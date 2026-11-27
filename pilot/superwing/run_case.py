"""Run one SuperWing case inside the pilot image: mesh once, then ADflow per condition.

This does what single-point.py does in the TU Munich repository, with two
differences: the number of MPI processes and the conditions to run are arguments.
The original hard-codes 8 processes and runs every condition.

The vendored scripts expect a fixed layout:
  - gen-mesh reads the tip template from '../../original_tip.xyz', so the case
    directory must sit two levels below that file (the image puts it at
    /sim/original_tip.xyz and mounts cases at /sim/cases/<name>);
  - run-adflow.py runs in one subfolder per condition and reads conds.json there,
    plus ../input.json and ../wing_vol.cgns.

Writes result.json in the case directory: per-stage wall time, peak memory of the
container, and CL, CD and convergence per condition.

Usage (inside the container, in the case directory):
  python /sim/run_case.py --np 6 --conditions 0
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")
ERR_CL_MAX = 0.002          # the cluster's convergence threshold (single-point.py)
REYNOLDS = 20.e6            # as in single-point.py and run-adflow.py
TEMPERATURE = 300           # K


def mpirun(n_proc, script_args, cwd, log_name, adflow_options=None):
    """Run a script under MPI; stdout and stderr go to a log in `cwd`.

    `adflow_options` (a dict) reaches every rank as $DECFD_ADFLOW_OPTIONS, which
    adflow_options.py merges into the options the vendored script passes to ADflow.
    """
    env = dict(os.environ, DECFD_ADFLOW_OPTIONS=json.dumps(adflow_options or {}))
    cmd = ["mpirun", "--oversubscribe", "-x", "DECFD_ADFLOW_OPTIONS", "-np", str(n_proc),
           sys.executable, *script_args]
    with open(os.path.join(cwd, log_name), "w") as log:
        return subprocess.run(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT).returncode


def memory_peak():
    """Peak memory of this container in bytes (cgroup v2), or None if unavailable."""
    try:
        with open("/sys/fs/cgroup/memory.peak") as f:
            return int(f.read())
    except (OSError, ValueError):
        return None


def run_mesh(case, n_proc):
    shutil.copy(os.path.join(VENDOR, "gen-mesh.superwing.py"), os.path.join(case, "gen-mesh.py"))
    t0 = time.perf_counter()
    code = mpirun(n_proc, ["gen-mesh.py"], case, "gen-mesh.out")
    seconds = time.perf_counter() - t0
    info_path = os.path.join(case, "mesh_info.json")
    if not os.path.exists(info_path):
        return {"state": "failed", "exit_code": code, "seconds": seconds}
    with open(info_path) as f:
        info = json.load(f)
    return {"state": "ok", "exit_code": code, "seconds": seconds, **info}


def run_condition(case, ic, m3, aa, n_proc, adflow_options=None):
    sub = os.path.join(case, str(ic))
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "conds.json"), "w") as f:
        json.dump({"M3": m3, "AA": aa, "re": REYNOLDS, "temp": TEMPERATURE}, f)
    shutil.copy(os.path.join(VENDOR, "run-adflow.py"), os.path.join(sub, "run-adflow.py"))
    for stale in ("output.json", "adflow.log"):   # never read a previous attempt's result
        if os.path.exists(os.path.join(sub, stale)):
            os.remove(os.path.join(sub, stale))
    t0 = time.perf_counter()
    code = mpirun(n_proc, [os.path.join(HERE, "adflow_options.py"), "run-adflow.py"], sub,
                  "run-adflow.out", adflow_options)
    seconds = time.perf_counter() - t0
    out_path = os.path.join(sub, "output.json")
    entry = {"ic": ic, "M3": m3, "AA": aa, "exit_code": code, "seconds": seconds}
    if not os.path.exists(out_path):
        return {**entry, "state": "failed"}
    with open(out_path) as f:
        res = json.load(f)["results"]
    converged = res.get("err_cl", float("inf")) < ERR_CL_MAX
    return {**entry, "state": "converged" if converged else "not converged",
            "CL": res.get("wing_cl"), "CD": res.get("wing_cd"), "CMz": res.get("wing_cmz"),
            "err_cl": res.get("err_cl"), "err_cd": res.get("err_cd"), "n_step": res.get("n_step")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", default=".", help="case directory with input.json (default: current)")
    ap.add_argument("--np", type=int, default=os.cpu_count(), help="MPI processes (default: all CPUs)")
    ap.add_argument("--conditions", default="all",
                    help="'all', 'none' (mesh only) or comma-separated indices, e.g. 0,3")
    ap.add_argument("--remesh", action="store_true", help="rebuild the mesh even if wing_vol.cgns exists")
    ap.add_argument("--adflow-options", default="{}",
                    help='JSON merged into the ADflow options, e.g. \'{"ANKSubspaceSize": 10}\'')
    args = ap.parse_args()
    adflow_options = json.loads(args.adflow_options)

    case = os.path.abspath(args.case)
    with open(os.path.join(case, "input.json")) as f:
        params = json.load(f)
    n_cond = len(params["M3"])
    if args.conditions == "all":
        wanted = range(n_cond)
    elif args.conditions == "none":   # mesh only
        wanted = []
    else:
        wanted = [int(x) for x in args.conditions.split(",")]

    result_path = os.path.join(case, "result.json")
    previous = {}
    if os.path.exists(result_path):   # a resumed run keeps earlier conditions and mesh info
        with open(result_path) as f:
            previous = json.load(f)
    result = {"np": args.np, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "adflow_options": adflow_options}
    if args.remesh or not os.path.exists(os.path.join(case, "wing_vol.cgns")):
        result["mesh"] = run_mesh(case, args.np)
        print(f"mesh: {result['mesh']['state']} in {result['mesh']['seconds']:.0f} s", flush=True)
        if result["mesh"]["state"] != "ok":
            result["memory_peak_bytes"] = memory_peak()
            with open(os.path.join(case, "result.json"), "w") as f:
                json.dump(result, f, indent=1)
            sys.exit(2)
        # gen-mesh writes the derived planform (half_span, surface_area) back into input.json
        with open(os.path.join(case, "input.json")) as f:
            params = json.load(f)
    else:
        result["mesh"] = {**previous.get("mesh", {}), "reused": True}

    done = {c["ic"]: c for c in previous.get("conditions", [])}
    for ic in wanted:
        entry = run_condition(case, ic, params["M3"][ic], params["AA"][ic], args.np, adflow_options)
        entry.update(np=args.np, adflow_options=adflow_options, memory_peak_bytes=memory_peak())
        done[ic] = entry
        result["conditions"] = [done[k] for k in sorted(done)]
        cl = f"CL {entry['CL']:.5f} CD {entry['CD']:.6f}" if "CL" in entry else ""
        print(f"condition {ic}: {entry['state']} in {entry['seconds']:.0f} s {cl}", flush=True)
        with open(result_path, "w") as f:   # keep partial results
            json.dump({**result, "memory_peak_bytes": memory_peak()}, f, indent=1)

    result["conditions"] = [done[k] for k in sorted(done)]
    result["memory_peak_bytes"] = memory_peak()
    with open(result_path, "w") as f:
        json.dump(result, f, indent=1)


if __name__ == "__main__":
    main()
