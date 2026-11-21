import os
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine, select
from fastapi.testclient import TestClient
from pilot import auth, ci, db, health
from pilot.api import app
from pilot.cluster import Drift
from pilot.worker import tick

GOOD = {"requests":300,"errors":0,"p95":.08}
BAD = {"requests":300,"errors":30,"p95":.09}

class Workload:
    """Test double for a managed deployment, with an independently stored live image."""
    def __init__(self):
        self.image, self.marker, self.writes = None, None, 0
        self.available = True

    def apply(self, service, image, marker, expected_image, expected_marker):
        if (self.image,self.marker)==(image,marker):
            return
        if (self.image,self.marker)!=(expected_image,expected_marker):
            raise Drift("Unexpected deployment")
        self.image,self.marker = image,marker
        self.writes += 1

    def ready(self, service, image, marker):
        if (self.image,self.marker)!=(image,marker):
            raise Drift("Image changed")
        return self.available

class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = db.engine
        cls.tmp = tempfile.TemporaryDirectory()
        url = os.getenv("PILOT_TEST_DATABASE_URL")
        if url:
            cls.engine = create_engine(url)
        else:
            cls.engine = create_engine("sqlite:///"+str(Path(cls.tmp.name)/"test.db"))
        db.engine = cls.engine
        db.schema.create_all(db.engine)
        cls.client = TestClient(app)
        cls.tokens = {}
        for role in ["admin","developer","viewer","approver"]:
            email = role+"-"+uuid.uuid4().hex+"@test.local"
            password = "test operator password"
            with db.transaction(True) as conn:
                conn.execute(db.users.insert().values(email=email,role=role,password_hash=auth.password_hash(password)))
            result = cls.client.post("/api/v1/auth/login",json={"email":email,"password":password})
            cls.tokens[role] = {"Authorization":"Bearer "+result.json()["token"]}

    @classmethod
    def tearDownClass(cls):
        db.engine = cls.original
        cls.engine.dispose()
        cls.tmp.cleanup()

    def setUp(self):
        self.prefix="/api/v1/"
        self.service = self.post("services",{"name":"svc-"+uuid.uuid4().hex,"environment":"development",
            "repository":"test/demo","deployment":"app-"+uuid.uuid4().hex},"admin").json()["id"]
        self.cluster = Workload()
        self.now = time.time()

    def post(self, path, body, role="developer"):
        return self.client.post("/api/v1/"+path,headers=self.tokens[role],json=body)

    def artifact(self, run=1):
        build={"run_id":str(run),"image":"ghcr.io/test/demo@sha256:"+f"{run:064x}","sha":f"{run:040x}"}
        with patch.object(ci,"verify_run",return_value=build):
            response=self.post(f"services/{self.service}/artifacts",{"run_id":str(run)})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()["id"]

    def request(self, artifact, key=None):
        return self.post("releases",{"service_id":self.service,"artifact_id":artifact,
                                    "request_key":key or uuid.uuid4().hex})

    def approved(self, run):
        rid=self.request(self.artifact(run)).json()["id"]
        response=self.post(f"releases/{rid}/approve",{"reason":"Reviewed build"},"approver")
        self.assertEqual(response.status_code,200,response.text)
        return rid

    def state(self,rid):
        return self.client.get(self.prefix+f"releases/{rid}",headers=self.tokens["viewer"]).json()["status"]

    def step(self, data=GOOD):
        self.now += 70
        self.assertTrue(tick(self.cluster,lambda *args:data,lambda:self.now))

    def deploy(self,run,data):
        rid=self.approved(run)
        for _ in range(3):
            self.step(data)
        return rid

    def test_healthy_then_bad_then_verified_rollback(self):
        first=self.deploy(1,GOOD)
        self.assertEqual(self.state(first),"HEALTHY")
        bad=self.deploy(2,BAD)
        self.assertEqual(self.state(bad),"ROLLING_BACK")
        for _ in range(3):
            self.step()
        self.assertEqual(self.state(bad),"ROLLED_BACK")
        self.assertEqual(self.cluster.marker,first)
        evidence=self.client.get(self.prefix+f"releases/{bad}/evaluation",headers=self.tokens["viewer"]).json()
        self.assertEqual([e["details"]["decision"] for e in evidence],["FAIL","PASS"])
        self.assertEqual(self.post(f"releases/{bad}/rollback",{"reason":"stale"},"admin").status_code,409)

    def test_missing_metrics_hold_target_and_retry(self):
        rid=self.deploy(1,{})
        self.assertEqual(self.state(rid),"REVIEW")
        self.assertEqual(self.request(self.artifact(2)).status_code,409)
        self.assertEqual(self.post(f"releases/{rid}/retry",{"reason":"Traffic restored"},"approver").status_code,200)
        self.step()
        self.assertEqual(self.state(rid),"HEALTHY")

    def test_rollback_failure_opens_incident(self):
        self.deploy(1,GOOD)
        rid=self.deploy(2,BAD)
        for _ in range(3):
            self.step(BAD)
        self.assertEqual(self.state(rid),"ROLLBACK_FAILED")
        result=self.client.get(self.prefix+f"releases/{rid}/events",headers=self.tokens["viewer"])
        self.assertEqual(result.json()[-1]["action"],"incident.opened")

    def test_first_failed_image_can_be_replaced(self):
        first=self.deploy(1,BAD)
        self.assertEqual(self.state(first),"FAILED")
        self.assertEqual(self.state(self.deploy(2,GOOD)),"HEALTHY")

    def test_environment_grant_required(self):
        with db.transaction(True) as conn:
            conn.execute(db.services.update().where(db.services.c.id==self.service).values(environment="production"))
        self.assertEqual(self.request(self.artifact()).status_code,403)

    def test_replay_after_external_write(self):
        rid=self.approved(1)
        with db.transaction() as conn:
            release=db.one(conn,db.releases,rid)
            image=db.one(conn,db.artifacts,release.artifact_id).image
        self.cluster.apply(None,image,rid,None,None)
        for _ in range(3):
            self.step()
        self.assertEqual(self.state(rid),"HEALTHY")
        self.assertEqual(self.cluster.writes,1)

    def test_permissions_duplicates_and_reject(self):
        aid=self.artifact()
        data={"service_id":self.service,"artifact_id":aid,"request_key":"repeat-me"}
        self.assertEqual(self.post("releases",data,"viewer").status_code,403)
        self.assertEqual(self.client.post(self.prefix+"releases",json=data).status_code,401)
        with ThreadPoolExecutor(2) as pool:
            result=list(pool.map(lambda _:self.post("releases",data,"admin"),range(2)))
        self.assertEqual([r.status_code for r in result],[200,200])
        rid=result[0].json()["id"]
        self.assertEqual(result[1].json()["id"],rid)
        self.assertEqual(self.post(f"releases/{rid}/approve",{"reason":"self"},"admin").status_code,403)
        self.assertEqual(self.request(aid).status_code,409)
        self.assertEqual(self.post(f"releases/{rid}/reject",{"reason":"Hold"},"approver").json()["status"],"REJECTED")

    def test_readiness_timeout(self):
        rid=self.approved(1)
        self.step()
        self.cluster.available=False
        self.now+=200
        self.step()
        self.assertEqual(self.state(rid),"FAILED")

    def test_manual_rollback_rechecks_health(self):
        first=self.deploy(1,GOOD)
        second=self.deploy(2,GOOD)
        self.assertEqual(self.post(f"releases/{second}/rollback",{"reason":"Operator requested"},"admin").status_code,200)
        for _ in range(3):
            self.step()
        self.assertEqual(self.state(second),"ROLLED_BACK")
        self.assertEqual(self.cluster.marker,first)

    def test_concurrent_requests_for_one_target(self):
        aid=self.artifact()
        with ThreadPoolExecutor(2) as pool:
            codes=list(pool.map(lambda _:self.request(aid).status_code,range(2)))
        self.assertEqual(sorted(codes),[200,409])

    def test_unverified_artifact(self):
        with patch.object(ci,"verify_run",side_effect=ValueError("invalid")):
            self.assertEqual(self.post(f"services/{self.service}/artifacts",{"run_id":"12"}).status_code,422)
        self.assertEqual(self.request(9999999).status_code,404)

    def test_health_boundaries(self):
        policy={"minimum_requests":100,"max_error_rate":.05,"max_p95_seconds":.3}
        for data, expected in [(GOOD,"PASS"),(BAD,"FAIL"),(dict(GOOD,p95=.3),"FAIL"),
                               (dict(GOOD,requests=99),"INCONCLUSIVE"),({},"INCONCLUSIVE")]:
            self.assertEqual(health.evaluate(policy,data)[0],expected)
