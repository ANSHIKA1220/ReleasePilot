import os
import secrets
import time
import zipfile
from pathlib import Path
import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from . import auth, ci, db
from .models import ImportRun, Login, Policy, Reason, Release, Service

app = FastAPI(title="ReleasePilot")
api = APIRouter(prefix="/api/v1")
admin = Depends(auth.roles("admin"))
develop = Depends(auth.roles("developer", "admin"))
approve = Depends(auth.roles("approver", "admin"))
read = Depends(auth.identity)

def get(conn, table, ident, lock=False):
    row = db.one(conn, table, ident, lock)
    if not row:
        raise HTTPException(404, "Record not found")
    return row

def rows(stmt):
    with db.transaction() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]

@api.post("/auth/login")
def login(body: Login):
    with db.transaction(True) as conn:
        user = conn.execute(select(db.users).where(db.users.c.email == body.email.lower())).mappings().first()
        if not user or not auth.verify(body.password, user.password_hash):
            raise HTTPException(401, "Incorrect email or password")
        token = secrets.token_urlsafe(32)
        conn.execute(db.sessions.insert().values(token_hash=auth.digest(token), user_id=user.id,
                                                 expires=time.time()+8*3600))
        return {"token":token, "email":user.email, "role":user.role}

@api.post("/auth/logout")
def logout(user=read, credentials=Depends(auth.bearer)):
    with db.transaction(True) as conn:
        conn.execute(db.sessions.delete().where(db.sessions.c.token_hash == auth.digest(credentials.credentials)))
    return {"ok":True}

@api.get("/services")
def list_services(user=read):
    return rows(select(db.services).order_by(db.services.c.name))

@api.post("/services", status_code=201)
def register(body: Service, user=admin):
    if body.namespace != os.getenv("PILOT_NAMESPACE", "releasepilot-demo"):
        raise HTTPException(422, "Namespace is outside the configured target")
    try:
        with db.transaction(True) as conn:
            ident = conn.execute(db.services.insert().values(**body.model_dump())).inserted_primary_key[0]
            db.audit(conn, "service.registered", user["id"], service=ident)
            return dict(get(conn, db.services, ident))
    except IntegrityError:
        raise HTTPException(409, "Service or deployment already registered")

@api.get("/services/{sid}")
def service(sid:int, user=read):
    with db.transaction() as conn:
        return dict(get(conn, db.services, sid))

@api.get("/services/{sid}/artifacts")
def artifacts(sid:int, user=read):
    return rows(select(db.artifacts).where(db.artifacts.c.service_id==sid).order_by(db.artifacts.c.id.desc()))

@api.put("/services/{sid}/policy")
def update_policy(sid:int, body:Policy, user=admin):
    with db.transaction(True) as conn:
        service = get(conn, db.services, sid, True)
        if service.active is not None:
            raise HTTPException(409, "An active release is using this policy")
        conn.execute(db.services.update().where(db.services.c.id==sid).values(policy=body.model_dump()))
        db.audit(conn, "policy.updated", user["id"], service=sid, policy=body.model_dump())
        return dict(get(conn, db.services, sid))

@api.post("/services/{sid}/artifacts")
def import_artifact(sid:int, body:ImportRun, user=develop):
    with db.transaction() as conn:
        repository = get(conn, db.services, sid).repository
    try:
        manifest = ci.verify_run(repository, body.run_id)
    except (ValueError, KeyError, zipfile.BadZipFile, httpx.HTTPError):
        with db.transaction(True) as conn:
            db.audit(conn, "artifact.rejected", user["id"], service=sid, run_id=body.run_id)
        raise HTTPException(422, "Could not verify the GitHub workflow run and its image manifest")
    with db.transaction(True) as conn:
        get(conn, db.services, sid, True)
        found = conn.execute(select(db.artifacts).where(db.artifacts.c.service_id==sid,
                             db.artifacts.c.run_id==body.run_id)).mappings().first()
        if found:
            return dict(found)
        ident = conn.execute(db.artifacts.insert().values(service_id=sid, **manifest)).inserted_primary_key[0]
        db.audit(conn, "artifact.verified", user["id"], service=sid, **manifest)
        return dict(get(conn, db.artifacts, ident))

@api.post("/releases")
def request_release(body:Release, user=develop):
    with db.transaction(True) as conn:
        account = get(conn, db.users, user["id"], True)
        service = get(conn, db.services, body.service_id, True)
        if account.role == "developer" and service.environment not in account.environments:
            raise HTTPException(403, "Account has no release grant for this environment")
        prior = conn.execute(select(db.releases).where(db.releases.c.requester==user["id"],
                             db.releases.c.request_key==body.request_key)).mappings().first()
        if prior:
            if (prior.service_id, prior.artifact_id) != (body.service_id, body.artifact_id):
                raise HTTPException(409, "Request key was used with different inputs")
            return dict(prior)
        artifact = get(conn, db.artifacts, body.artifact_id)
        if artifact.service_id != service.id:
            raise HTTPException(422, "Artifact belongs to a different service")
        if service.active is not None:
            raise HTTPException(409, "A release is already active for this target")
        now = time.time()
        state = "PENDING_APPROVAL" if service.approval else "QUEUED"
        ident = conn.execute(db.releases.insert().values(**body.model_dump(), requester=user["id"],
            status=state, since=now, due=now, created=now, previous_image=service.healthy_image,
            previous_release=service.healthy_release)).inserted_primary_key[0]
        conn.execute(db.services.update().where(db.services.c.id==service.id).values(active=ident))
        db.audit(conn, "release.requested", user["id"], ident, status=state, image=artifact.image)
        return dict(get(conn, db.releases, ident))

@api.get("/releases")
def list_releases(service_id:int|None=None, status:str|None=None, limit:int=Query(100,ge=1,le=500), user=read):
    stmt = select(db.releases)
    if service_id is not None:
        stmt = stmt.where(db.releases.c.service_id==service_id)
    if status:
        stmt = stmt.where(db.releases.c.status==status)
    return rows(stmt.order_by(db.releases.c.id.desc()).limit(limit))

@api.get("/releases/{rid}")
def release(rid:int, user=read):
    with db.transaction() as conn:
        return dict(get(conn, db.releases, rid))

def decision(rid, action, reason, user):
    with db.transaction(True) as conn:
        release = get(conn, db.releases, rid, True)
        service = get(conn, db.services, release.service_id, True)
        if action == "rollback":
            review = release.status == "REVIEW" and service.active == rid
            current = (release.status == "HEALTHY" and service.healthy_release == rid
                       and service.applied_release == rid and service.active is None)
            if not (review or current) or not release.previous_image:
                raise HTTPException(409, "No eligible rollback target")
            state = "ROLLING_BACK"
        elif action == "retry":
            if release.status != "REVIEW" or service.active != rid:
                raise HTTPException(409, "Release is not awaiting health review")
            state = "EVALUATING"
        elif action == "reject" and release.status == "REVIEW" and service.active == rid and not release.previous_image:
            state = "FAILED"
            db.audit(conn, "incident.opened", user["id"], rid, reason=reason)
        else:
            if release.status != "PENDING_APPROVAL":
                raise HTTPException(409, "Release is not awaiting approval")
            if release.requester == user["id"] and action == "approve":
                raise HTTPException(403, "A different user must approve this release")
            state = "QUEUED" if action == "approve" else "REJECTED"
        now = time.time()
        conn.execute(db.releases.update().where(db.releases.c.id==rid).values(status=state, since=now, due=now))
        conn.execute(db.services.update().where(db.services.c.id==service.id)
                     .values(active=None if state in ("REJECTED","FAILED") else rid))
        db.audit(conn, "release."+action, user["id"], rid, status=state, reason=reason)
        return dict(get(conn, db.releases, rid))

@api.post("/releases/{rid}/approve")
def approve_release(rid:int, body:Reason, user=approve):
    return decision(rid, "approve", body.reason, user)

@api.post("/releases/{rid}/reject")
def reject_release(rid:int, body:Reason, user=approve):
    return decision(rid, "reject", body.reason, user)

@api.post("/releases/{rid}/rollback")
def rollback(rid:int, body:Reason, user=approve):
    return decision(rid, "rollback", body.reason, user)

@api.post("/releases/{rid}/retry")
def retry(rid:int, body:Reason, user=approve):
    return decision(rid, "retry", body.reason, user)

@api.get("/releases/{rid}/events")
def timeline(rid:int, user=read):
    return rows(select(db.events).where(db.events.c.release_id==rid).order_by(db.events.c.id))

@api.get("/releases/{rid}/evaluation")
def evaluations(rid:int, user=read):
    return rows(select(db.events).where(db.events.c.release_id==rid,
                db.events.c.action=="health.evaluated").order_by(db.events.c.id))

@api.get("/audit-events")
def audit_events(limit:int=Query(100,ge=1,le=500), action:str|None=None, actor:int|None=None, user=read):
    stmt = select(db.events)
    if action:
        stmt = stmt.where(db.events.c.action == action)
    if actor is not None:
        stmt = stmt.where(db.events.c.actor == actor)
    return rows(stmt.order_by(db.events.c.id.desc()).limit(limit))

@app.get("/health/live")
def live():
    return {"status":"up"}

@app.get("/health/ready")
def ready():
    try:
        with db.transaction() as conn:
            conn.execute(select(db.services.c.id).limit(1))
    except SQLAlchemyError:
        raise HTTPException(503, "Database schema unavailable")
    return {"status":"ready"}

app.include_router(api)
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1]/"web", html=True), name="dashboard")
