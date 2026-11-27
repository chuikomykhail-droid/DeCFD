#!/usr/bin/env bash
# Run a pilot case in the decfd-superwing image (Linux or WSL, as a user that can run docker).
#
#   pilot/superwing/run.sh <case dir> [run_case.py arguments...]
#   pilot/superwing/run.sh pilot/superwing/cases/shape0000 --np 6 --conditions 0
#   RESUME=shape0000-20261126-231931 pilot/superwing/run.sh pilot/superwing/cases/shape0000 --conditions 0
#
# The case is copied to $RUNS/<case>-<timestamp> (default /srv/decfd/runs, on the Linux
# file system: the mesh and solution files are hundreds of MB), computed there, and
# result.json plus the logs are copied back to pilot/superwing/runs/ in the repository.
# RESUME=<run name> continues in an earlier run directory and reuses its mesh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CASE="$(cd "$1" && pwd)"
shift
RUNS="${RUNS:-/srv/decfd/runs}"
IMAGE="${IMAGE:-decfd-superwing}"

if [ -n "${RESUME:-}" ]; then
  NAME="$RESUME"
  WORK="$RUNS/$NAME"
  [ -f "$WORK/wing_vol.cgns" ] || { echo "no mesh in $WORK" >&2; exit 1; }
else
  NAME="$(basename "$CASE")-$(date +%Y%m%d-%H%M%S)"
  WORK="$RUNS/$NAME"
  mkdir -p "$WORK"
  cp "$CASE/input.json" "$WORK/"
  chown -R 1000:1000 "$WORK"   # the image runs as mdolabuser (uid 1000)
fi

echo "run $NAME in $WORK (image $(docker image inspect "$IMAGE" --format '{{.Id}}'))" | tee -a "$WORK/run.log"
status=0
docker run --rm -v "$WORK":/sim/cases/case "$IMAGE" "$@" 2>&1 | tee -a "$WORK/run.log" || status=$?

OUT="$HERE/runs/$NAME"
mkdir -p "$OUT"
cp "$CASE/reference.json" "$OUT/" 2>/dev/null || true
cd "$WORK"
for f in result.json input.json mesh_info.json run.log gen-mesh.out volume_meshing.log; do
  [ -f "$f" ] && cp "$f" "$OUT/"
done
for d in [0-9]*/; do
  [ -d "$d" ] || continue
  mkdir -p "$OUT/$d"
  for f in conds.json output.json adflow.log run-adflow.out; do
    [ -f "$d$f" ] && cp "$d$f" "$OUT/$d"
  done
done
echo "results in $OUT"
exit "$status"
