"""Serve the dashboard for local runs, live while a run is in progress.

    python orchestrator_py/serve.py            # then open http://localhost:8000
    python orchestrator_py/serve.py --port 8080

/            -> dashboard/ (static files)
/data/...    -> orchestrator_py/runs/... (run folders, read-only)
/data/runs.json is generated on each request from the run folders.
"""
import argparse
import http.server
import json
import os
import posixpath
import sys
from urllib.parse import unquote, urlparse

from runs import RUN_NAME
from worker_runner import RUNS_DIR

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DASHBOARD = os.path.join(ROOT, "dashboard")


def list_runs():
    runs = []
    if os.path.isdir(RUNS_DIR):
        for name in sorted(os.listdir(RUNS_DIR), reverse=True):
            path = os.path.join(RUNS_DIR, name, "history.json")
            if not RUN_NAME.match(name) or not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    h = json.load(f)
            except (OSError, ValueError):
                continue  # being written right now
            # Runs from before the ledger refactor have a different event format: skip them. A new
            # run has "started" from its first second; participants.json comes once miners joined
            if "started" not in h and not os.path.exists(os.path.join(RUNS_DIR, name, "network", "participants.json")):
                continue
            gens = h.get("generations", [])
            runs.append({"name": name, "finished": h.get("finished", False), "generations": len(gens),
                         "best_ld": gens[-1]["best"]["ld"] if gens else None})
    return runs


def safe_join(base, rel):
    """Map a URL path onto `base` without letting '..' escape it."""
    parts = [p for p in posixpath.normpath(rel).split("/") if p not in ("", ".", "..")]
    return os.path.join(base, *parts)


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = unquote(urlparse(path).path)
        if path.startswith("/data/"):
            return safe_join(RUNS_DIR, path[len("/data/"):])
        return safe_join(DASHBOARD, path)

    def do_GET(self):
        if urlparse(self.path).path == "/data/runs.json":
            body = json.dumps(list_runs()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")   # live polling must see fresh files
        super().end_headers()

    def log_message(self, *args):
        pass


class Server(http.server.ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server bind a port that is already in use, and the
    # browser then talks to whichever one answers; refuse instead
    allow_reuse_address = os.name != "nt"


def main():
    ap = argparse.ArgumentParser(description="Serve the DeCFD dashboard")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    try:
        server = Server(("127.0.0.1", args.port), Handler)
    except OSError:
        sys.exit(f"Port {args.port} is busy: the dashboard is probably already running at "
                 f"http://localhost:{args.port} (close the other serve.py, or pass --port)")
    print(f"Dashboard: http://localhost:{args.port}  (runs from {RUNS_DIR})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
