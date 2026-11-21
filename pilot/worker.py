import logging
import time
from sqlalchemy import select
from . import db, health
from .cluster import Cluster, Drift

RUNNABLE = ("QUEUED", "DEPLOYING", "EVALUATING", "ROLLING_BACK", "RESTORING", "VERIFYING_ROLLBACK")
log = logging.getLogger("releasepilot.worker")

def tick(cluster, measure=health.sample, clock=time.time):
    """Claim one due release. A short transaction spans one bounded external operation."""
    now = clock()
    with db.transaction(True) as conn:
        r = conn.execute(select(db.releases).where(db.releases.c.status.in_(RUNNABLE),
            db.releases.c.due <= now).order_by(db.releases.c.due, db.releases.c.id)
            .limit(1).with_for_update(skip_locked=True)).mappings().first()
        if not r:
            return False
        s = db.one(conn, db.services, r.service_id, True)
        artifact = db.one(conn, db.artifacts, r.artifact_id)
        rollback = r.status in ("ROLLING_BACK", "RESTORING", "VERIFYING_ROLLBACK")
        image = r.previous_image if rollback else artifact.image
        marker = r.previous_release if rollback else r.id
        age = now - r.since
        timeout = s.policy["rollout_timeout"]
        window = s.policy["window"]

        def move(state, reason, delay=0):
            stamp = clock()
            conn.execute(db.releases.update().where(db.releases.c.id==r.id)
                         .values(status=state, since=stamp, due=stamp+delay))
            db.audit(conn, "release.transition", release=r.id, status=state, reason=reason)
            if state in ("HEALTHY", "ROLLED_BACK"):
                conn.execute(db.services.update().where(db.services.c.id==s.id)
                    .values(active=None, healthy_image=image, healthy_release=marker))
            elif state in ("FAILED", "ROLLBACK_FAILED"):
                conn.execute(db.services.update().where(db.services.c.id==s.id).values(active=None))
                db.audit(conn, "incident.opened", release=r.id, reason=reason)

        def fail(reason):
            if rollback:
                move("ROLLBACK_FAILED", reason)
            elif r.previous_image:
                move("ROLLING_BACK", reason)
            else:
                move("FAILED", reason)

        def later():
            conn.execute(db.releases.update().where(db.releases.c.id==r.id).values(due=clock()+3))

        if s.active != r.id:
            raise RuntimeError("Inconsistent active release owner")
        try:
            if r.status in ("QUEUED", "ROLLING_BACK"):
                cluster.apply(s, image, marker, s.applied_image, s.applied_release)
                conn.execute(db.services.update().where(db.services.c.id==s.id)
                             .values(applied_image=image, applied_release=marker))
                move("RESTORING" if rollback else "DEPLOYING", "Image applied")
            elif not cluster.ready(s, image, marker):
                if age >= timeout:
                    fail("Rollout did not become ready before deadline")
                elif r.status in ("EVALUATING", "VERIFYING_ROLLBACK"):
                    move("RESTORING" if rollback else "DEPLOYING", "Readiness lost during health evaluation")
                else:
                    later()
            elif r.status in ("DEPLOYING", "RESTORING"):
                move("VERIFYING_ROLLBACK" if rollback else "EVALUATING", "Desired replicas ready", window)
            elif age < window:
                later()
            else:
                try:
                    data = measure(s, marker)
                    verdict, reason = health.evaluate(s.policy, data)
                except Exception as error:
                    data = {"error":type(error).__name__}
                    verdict, reason = "INCONCLUSIVE", "Telemetry query failed"
                db.audit(conn, "health.evaluated", release=r.id, image=image, rollback=rollback,
                         decision=verdict, reason=reason, metrics=data, policy=s.policy)
                if verdict == "PASS":
                    move("ROLLED_BACK" if rollback else "HEALTHY", reason)
                elif verdict == "FAIL" or rollback:
                    fail(reason)
                else:
                    move("REVIEW", reason)
        except Drift as error:
            move("ROLLBACK_FAILED" if rollback else "FAILED", str(error))
        except RuntimeError as error:
            fail(str(error))
        except Exception as error:
            if age >= timeout:
                fail("Cluster operation failed: " + type(error).__name__)
            else:
                log.warning("Release %s will retry %s", r.id, type(error).__name__)
                later()
        return True

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cluster = Cluster()
    while True:
        if not tick(cluster):
            time.sleep(1)
