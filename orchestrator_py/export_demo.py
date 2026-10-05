"""Export the dashboard plus one finished run as a static site (for GitHub Pages).

    python orchestrator_py/export_demo.py              # latest run -> docs/demo/
    python orchestrator_py/export_demo.py <run_dir> --out docs/demo

Only what the dashboard reads is copied: history.json, the ledger event log, participants,
the per-generation frames and the report images. The run is stored under the stable name
data/run/, so the README can link to its report images across re-exports.
"""
import argparse
import glob
import json
import os
import shutil
import sys

from runs import latest_run_dir

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FILES = ["history.json", "network/ledger_tx.jsonl", "network/participants.json"]
GLOBS = ["frames/*.png", "report/*.png", "report/*.gif", "report/summary.json"]


def export(run_dir, out):
    name = os.path.basename(os.path.normpath(run_dir))
    with open(os.path.join(run_dir, "history.json"), encoding="utf-8") as f:
        history = json.load(f)
    if not history.get("finished"):
        sys.exit("The run has not finished yet.")

    if os.path.isdir(out):
        shutil.rmtree(out)
    shutil.copytree(os.path.join(ROOT, "dashboard"), out)
    data = os.path.join(out, "data", "run")
    for rel in FILES + [p for g in GLOBS for p in
                        (os.path.relpath(x, run_dir) for x in glob.glob(os.path.join(run_dir, g)))]:
        src = os.path.join(run_dir, rel)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(os.path.join(data, rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(data, rel))
    gens = history["generations"]
    with open(os.path.join(out, "data", "runs.json"), "w", encoding="utf-8") as f:
        json.dump([{"name": "run", "label": name, "finished": True, "generations": len(gens),
                    "best_ld": gens[-1]["best"]["ld"]}], f, indent=2)
    size = sum(os.path.getsize(os.path.join(d, x)) for d, _, fs in os.walk(out) for x in fs)
    print(f"Exported {name} -> {out} ({size / 1e6:.1f} MB)")


def main():
    ap = argparse.ArgumentParser(description="Export a static dashboard for one run")
    ap.add_argument("run_dir", nargs="?", help="run folder (default: the latest run)")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "demo"))
    args = ap.parse_args()
    run_dir = args.run_dir or latest_run_dir()
    if not run_dir:
        sys.exit("No run folder found.")
    export(run_dir, args.out)


if __name__ == "__main__":
    main()
