import io
import json
import unittest
import zipfile
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import httpx
from pilot import ci, health
from pilot.cluster import Cluster, Drift

class Integrations(unittest.TestCase):
    def test_imported_digest_is_bound_to_trusted_workflow(self):
        sha="b"*40
        image="ghcr.io/org/demo@sha256:"+"a"*64
        run={"head_sha":sha,"event":"push","status":"completed","conclusion":"success","head_branch":"main",
             "head_repository":{"full_name":"org/demo"},"path":".github/workflows/publish.yml"}
        manifest={"sha":sha,"image":image}
        def respond(req):
            if req.url.path.endswith("/artifacts"):
                data={"artifacts":[{"id":6,"name":"releasepilot-image","expired":False,"size_in_bytes":300}]}
            elif req.url.path.endswith("/zip"):
                raw=io.BytesIO()
                with zipfile.ZipFile(raw,"w") as f:
                    f.writestr("image.json",json.dumps(manifest))
                return httpx.Response(200,content=raw.getvalue())
            else:
                data=run
            return httpx.Response(200,json=data)
        factory=httpx.Client
        with patch.object(ci.httpx,"Client",side_effect=lambda **kw:factory(transport=httpx.MockTransport(respond),**kw)):
            self.assertEqual(ci.verify_run("org/demo","20")["image"],image)
            for field,value in [("event","pull_request"),("conclusion","failure"),("head_branch","feature"),
                                ("path",".github/workflows/untrusted.yml")]:
                with patch.dict(run,{field:value}),self.assertRaises(ValueError):
                    ci.verify_run("org/demo","20")
            with patch.dict(manifest,{"sha":"c"*40}),self.assertRaises(ValueError):
                ci.verify_run("org/demo","20")
            with patch.dict(manifest,{"image":"ghcr.io/org/demo:latest"}),self.assertRaises(ValueError):
                ci.verify_run("org/demo","20")

    def test_kube_patch_guards_and_rollout_readiness(self):
        adapter=Cluster.__new__(Cluster)
        adapter.api=Mock()
        meta=NS(labels={"app.kubernetes.io/managed-by":"releasepilot"},resource_version="7",generation=3)
        container=NS(name="api",image="old")
        pod=NS(metadata=NS(labels={"releasepilot-id":"1"}),spec=NS(containers=[container]))
        status=NS(observed_generation=3,conditions=[],updated_replicas=2,ready_replicas=2,available_replicas=2,replicas=2)
        adapter.api.read_namespaced_deployment.return_value=NS(metadata=meta,spec=NS(template=pod,replicas=2),status=status)
        service=NS(deployment="demo-api",namespace="releasepilot-demo",container="api")
        adapter.apply(service,"new",2,"old",1)
        patch_body=adapter.api.patch_namespaced_deployment.call_args.args[2]
        self.assertEqual(patch_body["metadata"]["resourceVersion"],"7")
        self.assertEqual(patch_body["spec"]["template"]["metadata"]["labels"],{"releasepilot-id":"2"})
        self.assertTrue(adapter.ready(service,"old",1))
        status.observed_generation=2
        self.assertFalse(adapter.ready(service,"old",1))
        with self.assertRaises(Drift):
            adapter.apply(service,"new",2,"untracked",1)
        meta.labels={}
        with self.assertRaises(Drift):
            adapter.apply(service,"new",2,"old",1)

    def test_metrics_queries_scoped_to_release(self):
        queries=[]
        def respond(req):
            queries.append(req.url.params["query"])
            return httpx.Response(200,json={"status":"success","data":{"result":[{"value":[1,"NaN"]}]}})
        factory=httpx.Client
        with patch.object(health.httpx,"Client",side_effect=lambda **kw:factory(transport=httpx.MockTransport(respond),**kw)):
            data=health.sample(NS(namespace="demo",deployment="svc",policy={"window":60}),42)
        self.assertEqual(data,{"errors":None,"requests":None,"p95":None})
        self.assertTrue(all('release_id="42"' in q and 'deployment="svc"' in q and '[60s]' in q for q in queries))
