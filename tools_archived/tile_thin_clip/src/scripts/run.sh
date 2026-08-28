#!/usr/bin/env bash
# Docker wrapper for tile_thin_clip.py.
# Translates host paths to container paths and runs the pipeline inside the
# mmworkflow image. Auto-authenticates to ECR when needed.
#
# Usage:
#   bash run.sh --input-dir <host> --output-dir <host> [--network-shp <host>] [pipeline flags ...]

set -euo pipefail

IMAGE="${TILE_THIN_CLIP_IMAGE:-750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:latest}"
REGISTRY="${IMAGE%%/*}"
REGION="${TILE_THIN_CLIP_REGION:-us-west-2}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

INPUT_DIR=""
OUTPUT_DIR=""
NETWORK_SHP=""
PASSTHROUGH=()

while [ $# -gt 0 ]; do
  case "$1" in
    --input-dir)    INPUT_DIR="$2"; shift 2 ;;
    --output-dir)   OUTPUT_DIR="$2"; shift 2 ;;
    --network-shp)  NETWORK_SHP="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: run.sh --input-dir DIR --output-dir DIR [--network-shp PATH] [tile_thin_clip flags...]"
      echo "All other flags pass through to tile_thin_clip.py (run with --help inside container for full list)."
      exit 0
      ;;
    *) PASSTHROUGH+=("$1"); shift ;;
  esac
done

if [ -z "$INPUT_DIR" ] || [ -z "$OUTPUT_DIR" ]; then
  echo "ERROR: --input-dir and --output-dir are required" >&2
  exit 2
fi

if [ ! -d "$INPUT_DIR" ]; then
  echo "ERROR: input dir not found: $INPUT_DIR" >&2
  exit 2
fi

mkdir -p "$OUTPUT_DIR"

# Convert host paths to absolute. Works on Linux/Mac and Git Bash on Windows.
to_abs() {
  local p="$1"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -a "$p"
  elif [ -d "$p" ]; then
    (cd "$p" && pwd)
  else
    # File: cd to its dir then re-attach the filename
    local d=$(dirname "$p")
    local b=$(basename "$p")
    (cd "$d" && printf '%s/%s\n' "$(pwd)" "$b")
  fi
}

INPUT_ABS=$(to_abs "$INPUT_DIR")
OUTPUT_ABS=$(to_abs "$OUTPUT_DIR")
SCRIPT_ABS=$(to_abs "$SCRIPT_DIR")

# Try to pull the image; if auth has expired, re-login and retry once.
if ! docker pull "$IMAGE" >/dev/null 2>&1; then
  echo "[ecr] auth needed; logging in to $REGISTRY ($REGION)" >&2
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY" >&2
  docker pull "$IMAGE" >/dev/null
fi

DOCKER_ARGS=(
  --rm
  -v "${INPUT_ABS}:/data/input:ro"
  -v "${OUTPUT_ABS}:/data/output"
  -v "${SCRIPT_ABS}:/script:ro"
)

CONTAINER_NETWORK_FLAG=()
if [ -n "$NETWORK_SHP" ]; then
  if [ ! -f "$NETWORK_SHP" ]; then
    echo "ERROR: --network-shp not found: $NETWORK_SHP" >&2
    exit 2
  fi
  NET_DIR=$(to_abs "$(dirname "$NETWORK_SHP")")
  NET_NAME=$(basename "$NETWORK_SHP")
  DOCKER_ARGS+=( -v "${NET_DIR}:/data/network:ro" )
  CONTAINER_NETWORK_FLAG=( --network-shp "/data/network/${NET_NAME}" )
fi

# MSYS_NO_PATHCONV stops Git Bash on Windows from rewriting POSIX paths
# (/data/input, /root/..., /script) into Windows paths before docker sees them.
# Harmless no-op on Linux/Mac.
export MSYS_NO_PATHCONV=1

exec docker run "${DOCKER_ARGS[@]}" "$IMAGE" \
  /root/miniconda3/envs/pdal/bin/python /script/tile_thin_clip.py \
    --input-dir /data/input \
    --output-dir /data/output \
    "${CONTAINER_NETWORK_FLAG[@]}" \
    "${PASSTHROUGH[@]}"
