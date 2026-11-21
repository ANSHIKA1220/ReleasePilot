"""Exercise two previously published builds on a fresh Kind installation."""
import argparse
import json
import os
import time
import uuid
import httpx

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("repository")
    parser.add_argument("healthy_run")
    parser.add_argument("faulty_run")
    parser.add_argument("--url",default="http://127.0.0.1:8000")
    args=parser.parse_args()
    with httpx.Client(base_url=args.url+"/api/v1/",timeout=45) as client:
        def post(path, body, headers=None):
            response=client.post(path,json=body,headers=headers)
            response.raise_for_status()
            return response.json()

        def session(role):
            result=post("auth/login",{"email":os.environ["PILOT_"+role+"_EMAIL"],
                                      "password":os.environ["PILOT_"+role+"_PASSWORD"]})
            return {"Authorization":"Bearer "+result["token"]}

        admin,developer=session("ADMIN"),session("DEVELOPER")
        target=post("services",{"name":"demo-api","deployment":"demo-api","repository":args.repository},admin)
        for run, expected in [(args.healthy_run,"HEALTHY"),(args.faulty_run,"ROLLED_BACK")]:
            artifact=post(f"services/{target['id']}/artifacts",{"run_id":run},developer)
            release=post("releases",{"service_id":target["id"],"artifact_id":artifact["id"],
                                     "request_key":str(uuid.uuid4())},developer)
            rid=release["id"]
            post(f"releases/{rid}/approve",{"reason":"Kind integration test"},admin)
            deadline=time.monotonic()+660
            while time.monotonic()<deadline:
                response=client.get(f"releases/{rid}",headers=admin)
                response.raise_for_status()
                state=response.json()["status"]
                if state in {"HEALTHY","ROLLED_BACK","REVIEW","ROLLBACK_FAILED","FAILED","REJECTED"}:
                    break
                time.sleep(3)
            response=client.get(f"releases/{rid}/events",headers=admin)
            response.raise_for_status()
            print(json.dumps(response.json(),indent=2))
            if state!=expected:
                raise RuntimeError(f"Release {rid}: expected {expected}, got {state}")
            if expected=="HEALTHY":
                baseline=artifact["image"]
            else:
                response=client.get(f"services/{target['id']}",headers=admin)
                response.raise_for_status()
                if response.json()["healthy_image"]!=baseline:
                    raise RuntimeError("Recovered digest differs from healthy baseline")
if __name__=="__main__":
    main()
