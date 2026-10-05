"""Per-run output folders: orchestrator_py/runs/<YYYYmmdd-HHMMSS>_seed<N>/

Every run writes into its own folder, so frames, fields and ledger logs of different
runs never mix (and make_gif.py can't pick up stale frames from an older, longer run).
"""
import json
import os
import re
import time

from worker_runner import RUNS_DIR

RUN_NAME = re.compile(r"^\d{8}-\d{6}_")


def new_run_dir(seed):
    name = f"{time.strftime('%Y%m%d-%H%M%S')}_seed{seed}"
    path, n = os.path.join(RUNS_DIR, name), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(RUNS_DIR, f"{name}-{n}")
    os.makedirs(path)
    return path


def latest_run_dir():
    if not os.path.isdir(RUNS_DIR):
        return None
    names = sorted(d for d in os.listdir(RUNS_DIR)
                   if RUN_NAME.match(d) and os.path.isdir(os.path.join(RUNS_DIR, d)))
    return os.path.join(RUNS_DIR, names[-1]) if names else None


def write_json(path, data):
    """Write via a temp file so a reader (e.g. a live dashboard) never sees a half-written file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
