"""Turn one SuperWing shape into a case directory for the TU Munich pipeline.

SuperWing (https://huggingface.co/datasets/yunplus/SuperWing, CC BY-SA 4.0) stores:
  configs.dat     one row per shape: wing parameters and eight flight conditions
  running.jsonl   the cluster's run log per shape: mesh quality, CL, CD, convergence

A case directory gets:
  input.json      what gen-mesh.superwing.py and run-adflow.py read
  reference.json  the cluster's results for the same shape, to compare against

input.json always carries all eight conditions, even if only some will be run:
gen-mesh sizes the first cell from the mean Mach number of the list, so a shorter
list would give a different mesh from the one the cluster used.

Usage:
  python build_case.py --configs configs.dat --running running.jsonl --shape 0 --out cases/shape0000
"""
import argparse
import json
import os

SCALARS = ["SA", "DA", "DA_kink", "AR", "TR", "kink", "rootadj"]
VECTORS = [("rtcs", 3), ("cambers", 3), ("twists", 4), ("cst_u", 10), ("cst_l", 10)]
N_COND = 8
ROW_LEN = len(SCALARS) + sum(n for _, n in VECTORS) + 1 + 2 * N_COND   # + tcroot + M3 + AA = 54
COND_TOL = 1e-6   # configs.dat and running.jsonl must agree on every flight condition


def read_configs(path):
    """Rows of configs.dat as lists of floats, with the leading row index dropped.

    The header names the columns, but both CST vectors are labelled U0..U9, so rows
    are parsed by position (layout in `wing_params`).
    """
    with open(path) as f:
        header = f.readline()
        if not header.lstrip().startswith("VARIABLES"):
            raise ValueError(f"{path}: expected a 'VARIABLES =' header, got {header[:40]!r}")
        rows = []
        for line in f:
            parts = line.split()
            if parts:
                rows.append([float(x) for x in parts[1:]])
    return rows


def wing_params(row):
    """Split a configs.dat row into the wing parameters and the eight conditions.

    Layout: SA DA DA_kink AR TR kink rootadj | rtcs(3) cambers(3) twists(4) |
    cst_u(10) cst_l(10) | tcroot | M3(8) | AA(8).

    `rootadj` comes from configs.dat on purpose. running.jsonl repeats the shape
    vector with a different rootadj (the sampled design value). The published surface
    geometry matches the configs.dat value (see pilot/README.md).
    """
    if len(row) != ROW_LEN:
        raise ValueError(f"configs row has {len(row)} values, expected {ROW_LEN}")
    params, i = {}, 0
    for key in SCALARS:
        params[key] = row[i]
        i += 1
    for key, n in VECTORS:
        params[key] = row[i:i + n]
        i += n
    params["tcroot"] = row[i]
    i += 1
    params["M3"] = row[i:i + N_COND]
    params["AA"] = row[i + N_COND:i + 2 * N_COND]
    return params


def read_running(path, shape):
    """The run-log record of one shape, or None if the file does not have it.

    A truncated last line (a partial download) is ignored.
    """
    with open(path) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                break
            if rec.get("running_id") == shape:
                return rec
    return None


def reference_from_running(rec, params):
    """The cluster's results per condition, checked against the conditions in configs.dat."""
    conditions = []
    for ic in range(N_COND):
        info = rec.get(f"{ic}_running_info")
        if info is None:
            conditions.append({"ic": ic, "state": rec.get(f"{ic}_state", "missing")})
            continue
        wc, res = info["working_conditions"], info["results"]
        if abs(wc["M3"] - params["M3"][ic]) > COND_TOL or abs(wc["AA"] - params["AA"][ic]) > COND_TOL:
            raise ValueError(f"condition {ic}: running.jsonl has M={wc['M3']}, AA={wc['AA']}, "
                             f"configs.dat has M={params['M3'][ic]}, AA={params['AA'][ic]}")
        conditions.append({
            "ic": ic, "state": rec.get(f"{ic}_state"), "M3": wc["M3"], "AA": wc["AA"],
            "re": wc.get("re"), "CL": res["wing_cl"], "CD": res["wing_cd"], "CMz": res.get("wing_cmz"),
            "err_cl": res.get("err_cl"), "err_cd": res.get("err_cd"), "n_step": res.get("n_step"),
        })
    return {"shape": rec["running_id"], "mesh": rec.get("mesh"), "conditions": conditions}


def build_case(configs_path, shape, out_dir, running_path=None):
    rows = read_configs(configs_path)
    if not 0 <= shape < len(rows):
        raise IndexError(f"shape {shape} is not in {configs_path} ({len(rows)} rows)")
    params = wing_params(rows[shape])
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "input.json"), "w") as f:
        json.dump(params, f, indent=1)
    reference = None
    if running_path:
        rec = read_running(running_path, shape)
        if rec is None:
            raise KeyError(f"shape {shape} is not in {running_path}")
        reference = reference_from_running(rec, params)
        with open(os.path.join(out_dir, "reference.json"), "w") as f:
            json.dump(reference, f, indent=1)
    return params, reference


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", required=True, help="SuperWing configs.dat")
    ap.add_argument("--running", help="SuperWing running.jsonl, for reference.json")
    ap.add_argument("--shape", type=int, required=True, help="row index in configs.dat")
    ap.add_argument("--out", required=True, help="case directory to write")
    args = ap.parse_args()
    params, reference = build_case(args.configs, args.shape, args.out, args.running)
    print(f"shape {args.shape}: AR {params['AR']:.3f}, sweep {params['SA']:.2f} deg, "
          f"{N_COND} conditions -> {args.out}")
    if reference:
        ok = sum(1 for c in reference["conditions"] if c.get("state") == "Success")
        print(f"reference: {ok}/{N_COND} conditions converged on the cluster")


if __name__ == "__main__":
    main()
