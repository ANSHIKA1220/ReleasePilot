import math
import os
import httpx

def sample(service, marker):
    window = int(service.policy["window"])
    labels = f'namespace="{service.namespace}",deployment="{service.deployment}",release_id="{int(marker)}"'
    queries = {
        "requests": f'sum(increase(demo_requests_total{{{labels}}}[{window}s]))',
        "errors": f'sum(increase(demo_requests_total{{{labels},code="500"}}[{window}s]))',
        "p95": f'histogram_quantile(0.95,sum by (le)(rate(demo_duration_seconds_bucket{{{labels}}}[{window}s])))',
    }
    values = {}
    with httpx.Client(base_url=os.getenv("PROMETHEUS_URL", "http://localhost:9090"), timeout=5) as client:
        for name, query in queries.items():
            response = client.get("/api/v1/query", params={"query":query})
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "success":
                raise ValueError("Prometheus query failed")
            items = payload["data"]["result"]
            value = float(items[0]["value"][1]) if len(items)==1 else math.nan
            values[name] = value if math.isfinite(value) else None
    return values

def evaluate(policy, data):
    calls, errors, latency = (data.get(k) for k in ("requests", "errors", "p95"))
    if any(v is None or not math.isfinite(v) or v < 0 for v in (calls, errors, latency)):
        return "INCONCLUSIVE", "Required telemetry unavailable"
    if calls < policy["minimum_requests"] or errors > calls:
        return "INCONCLUSIVE", "Insufficient or inconsistent request samples"
    if errors/calls >= policy["max_error_rate"]:
        return "FAIL", "HTTP error budget exceeded"
    if latency >= policy["max_p95_seconds"]:
        return "FAIL", "P95 latency budget exceeded"
    return "PASS", "All health gates passed"
