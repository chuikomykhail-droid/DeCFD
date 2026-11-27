#!/usr/bin/env bash
# One SuperWing condition as a DeCFD network task, computed in the pilot image.
#
#   net_task.sh <input.json> <work dir> <condition> <MPI processes> <ADflow options as base64 JSON>
#
# The work dir keeps the mesh between tasks of the same case and is rebuilt only when the
# case's input.json changes. Prints one JSON line: the condition's CL, CD, CMz, cycles, wall
# time and the sha256 of its output.json, or {"status": "error", ...}.
set -uo pipefail
INPUT="$1"; WORK="$2"; IC="$3"; NP="$4"
OPTS="$(printf '%s' "$5" | base64 -d)"
IMAGE="${IMAGE:-decfd-superwing}"

case "$WORK" in
  /srv/decfd/*) ;;
  *) echo '{"status": "error", "code": "work dir must be under /srv/decfd"}'; exit 2 ;;
esac
mkdir -p "$WORK"
if ! cmp -s "$INPUT" "$WORK/input.orig.json" || [ ! -f "$WORK/wing_vol.cgns" ]; then
  rm -rf "${WORK:?}"/*              # a different case: start from an empty directory
  cp "$INPUT" "$WORK/input.json"
  cp "$INPUT" "$WORK/input.orig.json"
fi
touch "$WORK/.task_start"
chown -R 1000:1000 "$WORK"           # the image runs as mdolabuser (uid 1000)
docker run --rm -v "$WORK":/sim/cases/case "$IMAGE" --np "$NP" --conditions "$IC" \
  --adflow-options "$OPTS" >> "$WORK/run.log" 2>&1

python3 - "$WORK" "$IC" <<'PY'
import hashlib, json, os, sys
work, ic = sys.argv[1], int(sys.argv[2])
out = {"status": "error", "code": "no result"}
try:
    out_path = os.path.join(work, str(ic), "output.json")
    fresh = os.path.exists(out_path) and os.path.getmtime(out_path) >= os.path.getmtime(os.path.join(work, ".task_start"))
    with open(os.path.join(work, "result.json")) as f:
        cond = {c["ic"]: c for c in json.load(f).get("conditions", [])}.get(ic)
    if fresh and cond and "CL" in cond:
        with open(out_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        with open(os.path.join(work, "input.json")) as f:
            area = json.load(f).get("surface_area")
        out = {"status": "ok", "state": cond["state"], "CL": cond["CL"], "CD": cond["CD"], "CMz": cond["CMz"],
               "n_step": cond["n_step"], "seconds": cond["seconds"], "output_sha256": digest, "surface_area": area}
    elif cond:
        out = {"status": "error", "code": cond.get("state")}
except Exception as e:   # report, never crash: the node decides what to do
    out = {"status": "error", "code": str(e)[:200]}
print(json.dumps(out))
PY
