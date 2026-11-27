"""Data for the SuperWing page (docs/superwing/): the pilot's comparison with the TU Munich
cluster, convergence histories, and optionally a job that ran on the DeCFD network.

    python pilot/superwing/export_page.py pilot/superwing/runs/<pilot run> \\
        [--job orchestrator_py/network_logs/superwing-<timestamp>] [--out docs/superwing/data.json]

The pilot run directory is what run.sh copies back: result.json, input.json, reference.json
and one folder per condition with adflow.log. Coefficients are put on the cluster's reference
area, as compare.py does.
"""
import argparse
import json
import os
import re

from build_case import planform_area

ITER = re.compile(r"^\s+1\s+\d+\s+\d+\s+(DADI|ANK|RK|NK)\s")
EVERY = 10   # keep every 10th fine-grid cycle of the histories


def history(log_path, scale):
    """[[cycle, CL, CD], ...] of the fine grid, on the cluster's area."""
    rows = []
    with open(log_path, errors="replace") as f:
        for line in f:
            if ITER.match(line):
                p = line.split()
                rows.append((int(p[1]), float(p[9]) * scale, float(p[10]) * scale))
    if not rows:
        return []
    first = rows[0][0]
    keep = [r for r in rows if (r[0] - first) % EVERY == 0] + ([rows[-1]] if (rows[-1][0] - first) % EVERY else [])
    return [[r[0] - first, round(r[1], 6), round(r[2], 7)] for r in keep]


def pilot(run_dir):
    with open(os.path.join(run_dir, "result.json")) as f:
        result = json.load(f)
    with open(os.path.join(run_dir, "reference.json")) as f:
        reference = json.load(f)
    with open(os.path.join(run_dir, "input.json")) as f:
        case = json.load(f)
    area = case.get("surface_area") or planform_area(case, case["rootadj"])
    scale = area / reference["area_ref"]
    cluster = {c["ic"]: c for c in reference["conditions"]}
    conditions = []
    for c in result["conditions"]:
        ref = cluster[c["ic"]]
        log = os.path.join(run_dir, str(c["ic"]), "adflow.log")
        conditions.append({
            "ic": c["ic"], "M": c["M3"], "AoA": c["AA"], "cycles": c["n_step"], "minutes": round(c["seconds"] / 60, 1),
            "CL": c["CL"] * scale, "CD": c["CD"] * scale, "CL_raw": c["CL"], "CD_raw": c["CD"],
            "ref_CL": ref["CL"], "ref_CD": ref["CD"], "ref_steps": ref["n_step"],
            "dCL": c["CL"] * scale - ref["CL"], "dCD": c["CD"] * scale - ref["CD"],
            "history": history(log, scale) if os.path.exists(log) else []})
    mesh = result.get("mesh", {})
    return {"shape": reference["shape"], "area_ours": area, "area_cluster": reference["area_ref"],
            "rootadj_geometry": case["rootadj"], "rootadj_cluster_area": reference["area_ref_rootadj"],
            "rootadj_configs": case.get("rootadj_configs"), "scale": scale,
            "mesh_minutes": round(mesh.get("seconds", 0) / 60, 1), "cells": 3527680,
            "peak_memory_gib": round(result.get("memory_peak_bytes", 0) / 2**30, 1), "np": result.get("np"),
            "conditions": conditions}


def network_job(job_dir):
    with open(os.path.join(job_dir, "superwing_report.json")) as f:
        report = json.load(f)
    events = []
    with open(os.path.join(job_dir, "ledger_tx.jsonl")) as f:
        for line in f:
            e = json.loads(line)
            events.append({k: e.get(k) for k in ("sig", "ix", "signer", "task", "name", "t", "paid", "penalty",
                                                   "address", "config") if e.get(k) is not None})
    with open(os.path.join(job_dir, "participants.json")) as f:
        participants = json.load(f)
    return {"report": report, "events": events, "participants": participants["participants"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="pilot run directory (pilot/superwing/runs/<name>)")
    ap.add_argument("--job", help="network job log directory (orchestrator_py/network_logs/superwing-...)")
    ap.add_argument("--verifier-result", help="the verifier's result.json, for the audited tasks' numbers")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "..", "docs", "superwing",
                                                  "data.json"))
    args = ap.parse_args()
    data = {"pilot": pilot(args.run)}
    if args.job:
        data["network"] = network_job(args.job)
        if args.verifier_result:
            with open(args.verifier_result) as f:
                data["network"]["verifier"] = {str(c["ic"]): {"CL": c["CL"], "CD": c["CD"], "n_step": c["n_step"]}
                                               for c in json.load(f)["conditions"] if "CL" in c}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    n = sum(len(c["history"]) for c in data["pilot"]["conditions"])
    print(f"{args.out}: {len(data['pilot']['conditions'])} conditions, {n} history points"
          f"{', network job' if args.job else ''} ({os.path.getsize(args.out) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
