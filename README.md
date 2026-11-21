# ReleasePilot

ReleasePilot deploys CI-verified container digests to a registered Kubernetes workload, evaluates release-specific Prometheus metrics, and restores the previous healthy digest when a release fails. Operators can see the reason for each decision in the dashboard and API.

## Run the local platform on Windows

Use Python 3.12, Docker Desktop with Linux containers, Kind, kubectl, PowerShell and a GitHub repository containing this source. The setup creates a cluster named `releasepilot`. Its PostgreSQL data lives on a persistent volume inside that cluster.

1. Put the source on the repository's `main` branch. Let **Verify and publish demo** finish with `demo/mode.txt` set to `healthy`. Record the numeric Actions run ID. Make the GHCR demo package public so the local cluster can pull it. The workflow grants write access to packages only in the publish job.
2. For local evidence imports, set `GITHUB_READ_TOKEN` in the PowerShell session to a token with Actions read access to that repository. The startup script copies it to the local cluster on first provisioning. Tokens are supplied by the operator.
3. Run the installer, create two operator accounts, and forward the API. Enter a separate password of at least twelve characters at each prompt.

```powershell
.\scripts\start-kind.ps1
kubectl --context kind-releasepilot -n releasepilot-demo exec -it deploy/pilot-api -- python -m pilot.manage admin@demo.local admin
kubectl --context kind-releasepilot -n releasepilot-demo exec -it deploy/pilot-api -- python -m pilot.manage dev@demo.local developer
kubectl --context kind-releasepilot -n releasepilot-demo port-forward svc/pilot-api 8000:8000
```

Open [the console](http://localhost:8000) or [the OpenAPI explorer](http://localhost:8000/docs). The API and browser use the same origin. Keep the local port forward running.

## Demonstrate a real rollback

1. Sign in as admin. Under **Services**, register service `demo-api`, repository `OWNER/REPO`, deployment `demo-api`, namespace `releasepilot-demo`, container `api`, and environment `development`.
2. Sign in as the developer. Import the healthy run ID and request a release. Sign in as admin, open the release, enter a reason and approve. Wait for **HEALTHY**. This creates the first eligible rollback target.
3. Change `demo/mode.txt` to `errors`, commit and publish to the same repository. Import that run and repeat the request and approval. The workload returns HTTP 500 for business requests while its readiness probe stays healthy. The traffic generator supplies real requests.
4. Open the release timeline. Expect a failed error-rate evaluation, a rollback to the first digest, then a passing health evaluation and **ROLLED_BACK**. Missing or insufficient telemetry on the forward path puts the release in **REVIEW**. An approver can retry the measurement or restore the previous healthy image. A failed recovery opens an incident in the audit history.
5. In **Services**, inspect the restored digest. For an independent check, use the command below.

```powershell
kubectl --context kind-releasepilot -n releasepilot-demo get deploy demo-api -o jsonpath='{.spec.template.spec.containers[0].image}'
```

The other fault modes are `slow` and `unready`. Publish each change as a separate build. For `slow`, sequential traffic may produce fewer than 100 requests per minute. Increase traffic replicas to 3 before the release to exercise the latency gate with sufficient samples. Missing data is inconclusive. To repeat the scripted success/rollback check on a fresh cluster, dispatch **Verify live rollback** with the two publish run IDs. That workflow installs the platform, drives its public API, and prints the actual timeline. Its success requires both recorded outcomes.

## How a release is controlled

| Component | Behavior |
| --- | --- |
| Service registry | Binds a service/environment to one namespace, deployment, container, repository, health policy and approval policy. Target names are unique. |
| CI import | Reads the completed successful main-branch push run and its build manifest from GitHub. Verifies the workflow path, source repository, commit and GHCR digest reference. CI scans and pushes the same image. |
| Authentication | Salted scrypt hashes and expiring server-side bearer sessions. Viewers read history. Developers request releases in granted environments. Approvers authorize deployment or recovery. Admins register targets and edit policies. Self-approval is blocked. |
| Queue and worker | A database transaction reserves the target. Replayed request keys return the existing release. PostgreSQL workers claim due rows with row locks and skip locked rows. |
| Kubernetes | Checks ownership, container name, expected image and release marker. Uses a resource-version precondition and a rolling update. Watches observed generation and all replica counts. |
| Health | Waits a full measurement window after rollout readiness. Queries request volume, HTTP errors and histogram P95 for pods carrying the selected release ID. Default gates are 100 requests, error rate below 5%, and P95 below 300 ms over 60 seconds. |
| Recovery | Restores the last verified digest, then checks rollout readiness and metrics again. A first deployment without a known healthy predecessor can fail without an automatic rollback. |
| Audit | Records actor, time, transitions, reasons, decisions, measured metrics and thresholds. Release history and audit events are append-only through the API. |

The first deployment requires an already-onboarded, labeled workload. The starter ships one. To onboard another workload, give it the ownership label from `infra/targets.yaml`, a matching pod `app` label for Prometheus, compatible metrics, and add its exact deployment name to the worker Role in `infra/control.yaml`. Configure a developer grant with `python -m pilot.manage EMAIL developer --environment staging`. Kubernetes resource changes outside ReleasePilot can trigger a drift incident. Inspect the recorded and live image/marker before reconciling the target.

## Observability

Forward Prometheus with `kubectl --context kind-releasepilot -n releasepilot-demo port-forward svc/prometheus 9090:9090`. Forward Grafana similarly with `svc/grafana 3000:3000`. Its provisioned **Release health** dashboard graphs the error ratio and P95 by release. The initial Grafana login is `admin`. Retrieve the generated password locally using the following PowerShell command.

```powershell
$encoded = kubectl --context kind-releasepilot -n releasepilot-demo get secret pilot-config -o jsonpath='{.data.GRAFANA_PASSWORD}'
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
```

## Develop without a cluster

The API, database, console and tests can run with SQLite. Deployment execution requires the Kubernetes worker and telemetry. No demo release is marked successful without measurements.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m pilot.manage admin@demo.local admin
.\.venv\Scripts\python -m uvicorn pilot.api:app --host 127.0.0.1 --port 8000
```

Start the host worker with `python -m pilot.worker` using the same virtual environment and database. For a host worker, set `KUBE_CONTEXT` and port-forward Prometheus. For PostgreSQL set `DATABASE_URL` to a `postgresql+psycopg://...` URL before migrating. The HTML/CSS/JavaScript dashboard is served directly by FastAPI and needs no Node build.

## Verification

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

The default suite isolates data in a temporary SQLite database. Set `PILOT_TEST_DATABASE_URL` only to a dedicated disposable PostgreSQL test database. Tests insert records there. The suite exercises FastAPI through HTTPX, the persisted state machine, concurrent API requests, idempotency, authorization, health gates, restore verification, replay after a worker interruption, Kubernetes patch preconditions, and GitHub build-evidence validation. Unit and API tests use controlled adapter responses. The separate live workflow exercises GitHub, Kind, PostgreSQL and Prometheus together.

The publish workflow also runs a Chromium login/registration/audit smoke test against the live API. Runtime evidence comes from a completed workflow or an observed local run. The repository focuses on the local rolling-update platform. AWS provisioning and Argo-based canary deployment are optional extensions.
