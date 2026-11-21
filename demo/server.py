"""Instrumented workload. Change mode.txt in a new commit to publish a fault."""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODE = os.getenv("DEMO_MODE") or Path(__file__).with_name("mode.txt").read_text().strip()
if MODE not in {"healthy", "errors", "slow", "unready"}:
    raise ValueError("Unknown demo mode")
bounds = (.01,.05,.1,.2,.3,.5,1,2)
lock = threading.Lock()
count = {200:0,500:0}
buckets = [0]*len(bounds)
total = 0.0

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        global total
        status, body, kind = 200, b"ok\n", "text/plain"
        if self.path == "/work":
            start = time.monotonic()
            if MODE == "slow":
                time.sleep(.45)
            if MODE == "errors":
                status, body = 500, b"injected application error\n"
            duration = time.monotonic()-start
            with lock:
                count[status] += 1
                total += duration
                for i, bound in enumerate(bounds):
                    buckets[i] += int(duration<=bound)
        elif self.path == "/health/ready":
            if MODE=="unready":
                status,body = 503,b"unready\n"
        elif self.path == "/metrics":
            with lock:
                lines = ["# TYPE demo_requests_total counter"] + [
                    f'demo_requests_total{{code="{k}"}} {v}' for k,v in count.items()]
                lines += ["# TYPE demo_duration_seconds histogram"] + [
                    f'demo_duration_seconds_bucket{{le="{k}"}} {v}' for k,v in zip(bounds,buckets)]
                lines += [f'demo_duration_seconds_bucket{{le="+Inf"}} {sum(count.values())}',
                          f'demo_duration_seconds_count {sum(count.values())}', f'demo_duration_seconds_sum {total}']
            body, kind = ("\n".join(lines)+"\n").encode(), "text/plain; version=0.0.4"
        elif self.path != "/health/live":
            status, body = 404,b"unknown route\n"
        self.send_response(status)
        self.send_header("Content-Type",kind)
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__=="__main__":
    ThreadingHTTPServer(("0.0.0.0",8080),Handler).serve_forever()
