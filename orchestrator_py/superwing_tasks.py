"""SuperWing cases as DeCFD network tasks: real RANS CFD (pyHyp mesh + ADflow) in a container.

The LBM tasks of the genetic algorithm are bit-exact, so an audit compares hashes. A RANS
solve on MPI is not: the mesh partition changes the last bits. So a SuperWing task is audited
with a tolerance: the verifier re-computes it and accepts the miner's CL and CD within
`CL_TOL` and `CD_TOL`. On chain it then confirms the miner's own commitment (match) or
submits its own (mismatch: the miner is slashed), with the same program instruction as
before. No change to the deployed program is needed.

What the chain holds for a SuperWing job and its tasks:
  job.binary_hash   `pipeline_hash()`: the container recipe (Dockerfile with the MDO Lab image
                    pinned by digest, the TU Munich scripts, our runner), as the LBM job pins worker.exe
  job.steps         the ADflow cycle cap of every task
  task.params       shape, condition, Mach, AoA, rootadj, cycle cap, solver (0 ANK, 1 DADI), 0
  task.params_hash  `task_spec()`: sha256 of the case's input.json, the condition and the solver options
  task.result       CMz, cycles, CD, CL (CD and CL in the slots the LBM tasks use for cd and cl)
  task.result_hash  `result_hash()`: commitment to CL, CD, CMz, cycles and the sha256 of output.json

The case files travel with the repository (pilot/superwing/cases/shapeNNNN/input.json); the
params hash lets a node check that it computes exactly the case the client posted.
"""
import base64
import hashlib
import json
import os
import random
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PILOT = os.path.join(ROOT, "pilot", "superwing")
CASES = os.path.join(PILOT, "cases")
NET_TASK = os.path.join(PILOT, "net_task.sh")
WORK_ROOT = "/srv/decfd/net"          # on the Linux side (WSL on Windows): meshes are hundreds of MB
WSL_DISTRO = os.environ.get("DECFD_WSL_DISTRO", "Ubuntu-24.04")

CL_TOL = 1e-3      # the pilot's comparison tolerances (pilot/superwing/compare.py)
CD_TOL = 1e-4      # 1 drag count

# Everything that goes into the image, in a fixed order. Dockerfile pins the base image by digest
# and cst-modeling3d by commit, so the recipe hash pins what a node computes with.
PIPELINE_FILES = ["Dockerfile", "run_case.py", "adflow_options.py", "vendor/gen-mesh.superwing.py",
                  "vendor/run-adflow.py", "vendor/original_tip.xyz", "vendor/LICENSE", "vendor/NOTICE.md"]


def _text_bytes(path):
    """File contents with CRLF folded to LF: a Windows checkout must hash like a Linux one."""
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def pipeline_hash(pilot_dir=PILOT):
    h = hashlib.sha256()
    for rel in PIPELINE_FILES:
        h.update(rel.encode() + b"\0" + hashlib.sha256(_text_bytes(os.path.join(pilot_dir, rel))).digest())
    return h.hexdigest()


def case_input(shape, cases_dir=CASES):
    return os.path.join(cases_dir, f"shape{int(shape):04d}", "input.json")


def canonical_options(adflow_options):
    """The solver options a task may set, normalised. A node rebuilds them from the task's params,
    so they are limited to what the params carry: the solver and the cycle cap."""
    extra = set(adflow_options) - {"useANKSolver", "nCycles"}
    if extra:
        raise ValueError(f"options a SuperWing task cannot carry: {sorted(extra)}")
    return {"useANKSolver": bool(adflow_options.get("useANKSolver", True)),
            "nCycles": int(adflow_options.get("nCycles", 3000))}


def options_from_params(params):
    return {"useANKSolver": params[6] == 0.0, "nCycles": int(params[5])}


def task_spec(input_path, ic, adflow_options):
    """(params, params_hash) of one condition of a case, as posted on chain."""
    adflow_options = canonical_options(adflow_options)
    with open(input_path) as f:
        case = json.load(f)
    spec = {"input_sha256": hashlib.sha256(_text_bytes(input_path)).hexdigest(), "ic": int(ic),
            "adflow_options": adflow_options}
    params_hash = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    shape = int(os.path.basename(os.path.dirname(input_path))[len("shape"):])
    params = [float(shape), float(ic), case["M3"][ic], case["AA"][ic], case["rootadj"],
              float(adflow_options["nCycles"]), 0.0 if adflow_options["useANKSolver"] else 1.0, 0.0]
    return params, params_hash


def result_values(res):
    """The four numbers published on chain: CMz, cycles, CD, CL. Zeros for a failed run."""
    if res.get("status") != "ok":
        return None
    return [res["CMz"], res["n_step"], res["CD"], res["CL"]]


def result_hash(task_id, res):
    if res.get("status") == "ok":
        body = {k: res[k] for k in ("CL", "CD", "CMz", "n_step", "output_sha256")}
    else:
        body = {"error": res.get("code")}
    return hashlib.sha256(json.dumps({"task": task_id, "result": body}, sort_keys=True).encode()).hexdigest()


def within_tolerance(ours, theirs, cl_tol=CL_TOL, cd_tol=CD_TOL):
    """The audit verdict: both runs succeeded and agree on CL and CD."""
    if ours.get("status") != "ok" or theirs.get("status") != "ok":
        return False
    return abs(ours["CL"] - theirs["CL"]) <= cl_tol and abs(ours["CD"] - theirs["CD"]) <= cd_tol


def _linux_path(path):
    path = os.path.abspath(path)
    if os.name == "nt" and len(path) > 1 and path[1] == ":":
        return "/mnt/" + path[0].lower() + path[2:].replace("\\", "/")
    return path


def run_superwing(input_path, ic, adflow_options, work_name, n_proc, timeout=6 * 3600):
    """Compute one condition in the pilot image (through WSL on Windows). Never raises."""
    opts = base64.b64encode(json.dumps(adflow_options).encode()).decode()
    cmd = ["bash", _linux_path(NET_TASK), _linux_path(input_path), f"{WORK_ROOT}/{work_name}", str(int(ic)),
           str(int(n_proc)), opts]
    if os.name == "nt":
        cmd = ["wsl.exe", "-d", WSL_DISTRO, "-u", "root", "--"] + cmd
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "error", "code": "timeout"}
    except OSError as e:
        return {"status": "error", "code": f"oserror: {e}"}
    lines = r.stdout.decode(errors="replace").replace("\0", "").strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (IndexError, ValueError):
        return {"status": "error", "code": f"exit {r.returncode}: {r.stderr.decode(errors='replace')[-200:]}"}


def fake_result(task_id, who):
    """What a lazy node submits without computing: plausible numbers, a made-up output hash."""
    r = random.Random(f"{task_id}:{who}")
    return {"status": "ok", "state": "converged", "CL": r.uniform(0.05, 0.5), "CD": r.uniform(0.010, 0.025),
            "CMz": r.uniform(0.0, 0.5), "n_step": 1500.0, "seconds": 1.0,
            "output_sha256": hashlib.sha256(f"{task_id}:{who}".encode()).hexdigest()}
