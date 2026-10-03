"""Thin wrapper around the C++ LBM worker binary.

Shared by the orchestrator, the mock miners and the verifier, so that every
party runs exactly the same command line for a given task.
"""
import hashlib
import json
import os
import subprocess

WORKER_PATH = os.environ.get(
    "WORKER_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "worker_cpp", "worker.exe")),
)
RUNS_DIR = os.path.join(os.path.dirname(__file__), "runs")

# Fields that define the "result" of a task. Only these go into the result hash,
# so diagnostic fields (spreads, timings) can change without breaking verification.
RESULT_KEYS = ("fx", "fy", "cd", "cl")


def shape_args(shape):
    """Canonical CLI arguments for a shape. The exact strings are the task definition."""
    return [str(shape.L)] + [str(t) for t in shape.t_pts] + [str(shape.alpha), str(shape.camber)]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_worker(args, run_dir=None, extra_args=(), timeout=60, threads=1):
    """Run the worker and return a dict: {"status": "ok", fx, fy, cd, cl, ...}
    or {"status": "error", "code": ...}. Never raises on worker failure."""
    run_dir = run_dir or RUNS_DIR
    os.makedirs(run_dir, exist_ok=True)
    env = dict(os.environ)
    if threads is not None:
        env["OMP_NUM_THREADS"] = str(threads)

    try:
        r = subprocess.run(
            [WORKER_PATH, *args, *extra_args],
            cwd=run_dir, capture_output=True, text=True, env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "code": "timeout"}
    except OSError as e:
        return {"status": "error", "code": "oserror", "stderr": str(e)}

    if r.returncode != 0:
        # Worker exit codes: 2 = bad args, 3 = empty body / body touches boundary, 4 = diverged
        return {"status": "error", "code": r.returncode, "stderr": r.stderr.strip()[-300:]}
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "error", "code": "bad_output", "stderr": r.stdout[-300:]}
    out["status"] = "ok"
    return out


def canonical_result(res):
    if res.get("status") == "ok":
        return {k: res[k] for k in RESULT_KEYS}
    return {"error": res.get("code")}


def result_hash(task_id, res):
    """Commitment to a task result. Deterministic for the same binary + same args."""
    payload = json.dumps({"task": task_id, "result": canonical_result(res)}, sort_keys=True)
    return sha256_hex(payload.encode())

