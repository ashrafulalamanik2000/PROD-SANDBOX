#!/usr/bin/env bash
# PointCONV classification - deterministic one-path POSIX entry point.
#   ./run.sh <las|laz|directory> [--run-dir <run>] [flags...]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${POINTCONV_PY:-}" ]; then
  PY="$POINTCONV_PY"
elif [ -x "${USERPROFILE:-}/.conda/envs/gdal_env/python.exe" ]; then
  PY="${USERPROFILE}/.conda/envs/gdal_env/python.exe"
else
  PY="python"
fi
[ "$#" -gt 0 ] || { echo "Usage: $0 <las|laz|directory> [--run-dir <run>] [flags...]" >&2; exit 2; }
exec "$PY" "$HERE/run_pipeline.py" "$@"
