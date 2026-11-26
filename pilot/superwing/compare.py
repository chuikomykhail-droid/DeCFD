"""Compare a computed case with the cluster's published results.

Reads result.json (written by run_case.py) and reference.json (build_case.py) from
a case directory, prints one row per computed condition and exits with 1 if any
condition is outside tolerance.

A converged RANS solution should not depend on how the mesh was split across MPI
processes beyond the solver tolerance. A larger gap points at a different geometry,
mesh or solver version. Drag is reported in counts (1 count = 0.0001).

Usage:
  python compare.py cases/shape0000 [--cl-tol 1e-3] [--cd-tol 1e-4]
"""
import argparse
import json
import os
import sys


def compare(result, reference, cl_tol, cd_tol):
    """One row per condition computed by us; `ok` is None when there is nothing to compare."""
    ref = {c["ic"]: c for c in reference["conditions"]}
    rows = []
    for ours in result.get("conditions", []):
        theirs = ref.get(ours["ic"], {})
        row = {"ic": ours["ic"], "M3": ours["M3"], "AA": ours["AA"], "state": ours["state"],
               "ref_state": theirs.get("state"), "seconds": ours.get("seconds"),
               "n_step": ours.get("n_step"), "ref_n_step": theirs.get("n_step"), "ok": None}
        if "CL" in ours and "CL" in theirs:
            row.update(CL=ours["CL"], ref_CL=theirs["CL"], dCL=ours["CL"] - theirs["CL"],
                       CD=ours["CD"], ref_CD=theirs["CD"], dCD=ours["CD"] - theirs["CD"])
            row["ok"] = (ours["state"] == "converged" and abs(row["dCL"]) <= cl_tol
                         and abs(row["dCD"]) <= cd_tol)
        elif ours["state"] != "converged":
            row["ok"] = False
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case", help="case directory with result.json and reference.json")
    ap.add_argument("--cl-tol", type=float, default=1e-3, help="allowed |CL difference|")
    ap.add_argument("--cd-tol", type=float, default=1e-4, help="allowed |CD difference| (1e-4 = 1 count)")
    args = ap.parse_args()

    with open(os.path.join(args.case, "result.json")) as f:
        result = json.load(f)
    with open(os.path.join(args.case, "reference.json")) as f:
        reference = json.load(f)
    rows = compare(result, reference, args.cl_tol, args.cd_tol)

    mesh = result.get("mesh", {})
    if mesh.get("state") == "ok":
        ref_mesh = reference.get("mesh") or {}
        print(f"mesh: {mesh['seconds']:.0f} s, min quality {mesh.get('min_Quality')} "
              f"(cluster {ref_mesh.get('min_Quality')}), first cell {mesh.get('actual_ds')} "
              f"(cluster {ref_mesh.get('actual_ds')})")
    print(f"{'ic':>2} {'Mach':>6} {'AoA':>6}  {'CL':>9} {'cluster':>9} {'dCL':>9}  "
          f"{'CD':>9} {'cluster':>9} {'dCD,cnt':>8}  {'steps':>5} {'clus':>5}  {'min':>5}  verdict")
    for r in rows:
        line = f"{r['ic']:>2} {r['M3']:>6.4f} {r['AA']:>6.3f}  "
        if "CL" in r:
            line += (f"{r['CL']:>9.5f} {r['ref_CL']:>9.5f} {r['dCL']:>+9.5f}  "
                     f"{r['CD']:>9.6f} {r['ref_CD']:>9.6f} {r['dCD'] * 1e4:>+8.2f}  ")
        else:
            line += " " * 70
        steps = r["n_step"] if r["n_step"] is not None else float("nan")
        ref_steps = r["ref_n_step"] if r["ref_n_step"] is not None else float("nan")
        minutes = (r["seconds"] or 0) / 60
        verdict = {True: "match", False: "MISMATCH", None: "no reference"}[r["ok"]]
        line += f"{steps:>5.0f} {ref_steps:>5.0f}  {minutes:>5.1f}  {verdict} ({r['state']})"
        print(line)
    peak = result.get("memory_peak_bytes")
    if peak:
        print(f"peak memory: {peak / 2**30:.1f} GiB on {result.get('np')} MPI processes")
    sys.exit(1 if any(r["ok"] is False for r in rows) else 0)


if __name__ == "__main__":
    main()
