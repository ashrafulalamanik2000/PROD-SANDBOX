#!/usr/bin/env bash
# Run TF1 PointCONV inference on a directory of LAS files (multi-source mode).
#
# Unlike run_tf1_tiled_left_right.ps1, this does NOT call combine_thinned_las.py
# first -- that script loads all inputs into memory and is unsuitable for large
# multi-tile datasets (Mississauga: 82 LAS / 63 GB raw).
#
# Instead it uses build_tf1_inference_tiles_streaming.py directly with
# --input-dir, which streams each source LAS independently in parallel.
#
# Pipeline:
#   1. build_tf1_inference_tiles_streaming.py  (per-source thinning + tiling)
#   2. tf1/classification.py                   (GPU inference on tiles)
#   3. post_processing/merge_tf1_tile_predictions.py  (per-source merge)
#
# Result: <output_root>/combined_outputs/<source_stem>_tf1_pointconv_combined_0p1m.las
# for each input source (82 files for Mississauga).
#
# Usage:
#   bash run_tf1_tiled_multi_source.sh <INPUT_DIR> <OUTPUT_ROOT>
#
# Every tile-builder + inference + merge knob below is overridable via
# environment variable. To run with custom values from another workflow:
#
#   VOXEL_SIZE=0.05 TARGET_TILE_POINTS=200000 OVERLAP=30 \
#       bash run_tf1_tiled_multi_source.sh <INPUT_DIR> <OUTPUT_ROOT>
#
# Example (Mississauga, all defaults):
#   bash run_tf1_tiled_multi_source.sh \
#       /i/Research/Mississauga_P1_Central_3/Lidar \
#       /i/runs/mississauga_p1_central_3/20260519_214122/01_pointconv

set -euo pipefail

if [ $# -ne 2 ]; then
    echo "Usage: $0 <INPUT_DIR> <OUTPUT_ROOT>"
    echo ""
    echo "All tile-builder + inference knobs are env-var overridable:"
    echo "  VOXEL_SIZE, TARGET_TILE_POINTS, MIN_TILE_POINTS, MIN_RADIUS,"
    echo "  OVERLAP, CHUNK_SIZE, MAX_CONCURRENT_TILES, PREPROCESS_WORKERS,"
    echo "  POSTPROCESS_WORKERS, PATTERN, INPUT_CONFIG, MODEL_NAME,"
    echo "  DOCKER_IMAGE, WORKFLOW"
    exit 1
fi

INPUT_DIR="$1"
OUTPUT_ROOT="$2"

# --- Paths + image (env-overridable) ---------------------------------------
# Self-locating default: this script LIVES in the workflow dir, so any
# checkout resolves without env overrides — the old hardcoded G:/geotools
# default exit-125'd a fresh workstation (2026-06-05) AND died mid-run when
# that checkout was renamed under live mounts (2026-06-06, tile 258/622).
# pwd -W (Git Bash/MSYS) gives the Windows-style path docker -v prefers;
# plain pwd is the fallback elsewhere. Never point at another checkout.
WORKFLOW="${WORKFLOW:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && (pwd -W 2>/dev/null || pwd))}"
# Independently overridable so a preset's model_dir (e.g. a handoff bundle
# outside the repo) can be mounted as /model.
MODEL_DIR="${MODEL_DIR:-$WORKFLOW/models}"
# NOTE: MODEL_NAME is INFORMATIONAL only (echoed below; the whole models dir is
# mounted at /model regardless). The ACTUAL model is selected by `model_directory`
# in $INPUT_CONFIG (default tf1/inputconfig_finetune.yml). Keep the two in sync.
MODEL_NAME="${MODEL_NAME:-PointCONV_model_6class_Mobile_v0.0.15}"
DOCKER_IMAGE="${DOCKER_IMAGE:-750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1}"
AWS_DIR="${AWS_DIR:-$HOME/.aws}"
INPUT_CONFIG="${INPUT_CONFIG:-tf1/inputconfig_finetune.yml}"

# --- Tile builder parameters (env-overridable) -----------------------------
VOXEL_SIZE="${VOXEL_SIZE:-0.1}"
TARGET_TILE_POINTS="${TARGET_TILE_POINTS:-400000}"
MIN_TILE_POINTS="${MIN_TILE_POINTS:-25000}"
MIN_RADIUS="${MIN_RADIUS:-20.0}"
OVERLAP="${OVERLAP:-20.0}"
CHUNK_SIZE="${CHUNK_SIZE:-500000}"
PREPROCESS_WORKERS="${PREPROCESS_WORKERS:-4}"
MAX_CONCURRENT_TILES="${MAX_CONCURRENT_TILES:-8}"
POSTPROCESS_WORKERS="${POSTPROCESS_WORKERS:-4}"
PATTERN="${PATTERN:-*.las}"

mkdir -p "$OUTPUT_ROOT"

# Resolve the input dir's relative position under a sensible data root.
# Use the parent of INPUT_DIR as the data root.
DATA_ROOT="$(dirname "$INPUT_DIR")"
DATA_ROOT="$(dirname "$DATA_ROOT")"   # one more level up to be safe (e.g. I:/Research)
INPUT_REL="${INPUT_DIR#$DATA_ROOT/}"

# Output root anchor: parent of OUTPUT_ROOT.
EXP_ROOT="$(dirname "$OUTPUT_ROOT")"
EXP_ROOT="$(dirname "$EXP_ROOT")"  # e.g. I:/runs

# In-container paths.
C_WORKFLOW=/workspace
C_DATA=/data
C_EXP=/exp
C_INPUT="$C_DATA/$INPUT_REL"
C_OUTPUT_ROOT="$C_EXP/${OUTPUT_ROOT#$EXP_ROOT/}"

# Convert backslashes to forward slashes (Git Bash safety).
C_INPUT="${C_INPUT//\\//}"
C_OUTPUT_ROOT="${C_OUTPUT_ROOT//\\//}"
C_TILE_DIR="$C_OUTPUT_ROOT/preprocessed_tiles"
C_TF1_OUTPUT="$C_OUTPUT_ROOT/tf1_outputs"
C_COMBINED_OUTPUT="$C_OUTPUT_ROOT/combined_outputs"
C_MANIFEST="$C_OUTPUT_ROOT/manifests/tf1_tile_manifest.json"

echo "==========================================================================="
echo "INPUT_DIR     = $INPUT_DIR"
echo "DATA_ROOT     = $DATA_ROOT          (mounted at /data)"
echo "OUTPUT_ROOT   = $OUTPUT_ROOT"
echo "EXP_ROOT      = $EXP_ROOT           (mounted at /exp)"
echo "WORKFLOW      = $WORKFLOW           (mounted at /workspace)"
echo "MODEL         = $MODEL_DIR/$MODEL_NAME -> /model/$MODEL_NAME"
echo "AWS_DIR       = $AWS_DIR"
echo "Container input: $C_INPUT"
echo "Container output_root: $C_OUTPUT_ROOT"
echo "==========================================================================="
echo ""

# --- Sibling-mount path translation ----------------------------------------
# (chain-orchestrator/docker/UNIFIED_IMAGE_DESIGN.md, "The sibling-mount
# problem".) When this launcher runs INSIDE the chain-full container and
# starts mmworkflow as a SIBLING via the host docker socket, every -v SOURCE
# is resolved by the HOST daemon — it must be a host path or named volume.
# Mirror of _host_docker_path() in chain_orchestrator.py:
#   CHAIN_HOST_PATH_MAP="<container_prefix>=<host_prefix>[;...]"
#   1. map unset/empty -> identity (native runs byte-identical)
#   2. no '/' or '\' in SRC -> named volume, pass through
#   3. longest matching container prefix wins (forward-slash normalized)
#   4. map set + absolute SRC unmatched -> error on stderr, exit 4
_host_mount_src() {
    local src="$1"
    local map="${CHAIN_HOST_PATH_MAP:-}"
    if [ -z "$map" ]; then
        printf '%s\n' "$src"
        return 0
    fi
    case "$src" in
        */*|*\\*) ;;                          # contains a path separator
        *) printf '%s\n' "$src"; return 0 ;;  # named volume -> pass through
    esac
    local norm="${src//\\//}"
    local best_c="" best_h="" entry c h
    local -a entries
    IFS=';' read -r -a entries <<< "$map"
    for entry in "${entries[@]}"; do
        case "$entry" in *=*) ;; *) continue ;; esac
        c="${entry%%=*}"; h="${entry#*=}"
        c="${c//\\//}"; c="${c%/}"
        h="${h//\\//}"; h="${h%/}"
        [ -z "$c" ] && continue
        case "$norm" in
            "$c"|"$c"/*)
                if [ "${#c}" -gt "${#best_c}" ]; then
                    best_c="$c"; best_h="$h"
                fi
                ;;
        esac
    done
    if [ -n "$best_c" ]; then
        printf '%s\n' "${best_h}${norm:${#best_c}}"
        return 0
    fi
    case "$norm" in
        /*|[A-Za-z]:/*)
            echo "ERROR: CHAIN_HOST_PATH_MAP is set but mount source '$src' matches no container prefix in '$map' -- refusing to compose a silently-wrong sibling mount (UNIFIED_IMAGE_DESIGN.md rule 4)." >&2
            exit 4
            ;;
    esac
    printf '%s\n' "$norm"
}

# Translate every -v SOURCE for sibling launches (identity when
# CHAIN_HOST_PATH_MAP is unset). All THREE docker run phases below share
# DOCKER_MOUNTS, so this is the single composition site.
HOST_WORKFLOW="$(_host_mount_src "$WORKFLOW")"
HOST_DATA_ROOT="$(_host_mount_src "$DATA_ROOT")"
HOST_EXP_ROOT="$(_host_mount_src "$EXP_ROOT")"
HOST_MODEL_DIR="$(_host_mount_src "$MODEL_DIR")"
DOCKER_MOUNTS=(
    "-v" "$HOST_WORKFLOW:/workspace"
    "-v" "$HOST_DATA_ROOT:/data"
    "-v" "$HOST_EXP_ROOT:/exp"
    "-v" "$HOST_MODEL_DIR:/model"
)
if [ -d "$AWS_DIR" ]; then
    HOST_AWS_DIR="$(_host_mount_src "$AWS_DIR")"
    DOCKER_MOUNTS+=("-v" "$HOST_AWS_DIR:/root/.aws:ro")
fi

# 2026-05-24 (Verizon recovery): Phase 1 --overwrite is now opt-in
# via OVERWRITE_TILES=true. The streaming tile builder already has
# its own per-source skip-existing (checks thinned_path +
# thinned_indices_path), so by DROPPING --overwrite we get idempotent
# resume behavior: poles already preprocessed are skipped in seconds.
# Set OVERWRITE_TILES=true if you need a hard rebuild.
OVERWRITE_FLAG=""
[ "${OVERWRITE_TILES:-false}" = "true" ] && OVERWRITE_FLAG="--overwrite"

echo "[1/3] Streaming tile builder..."
echo "       OVERWRITE_TILES=${OVERWRITE_TILES:-false}  -> flag: '${OVERWRITE_FLAG}'"
t0=$(date +%s)
MSYS_NO_PATHCONV=1 docker run --rm --pull=never \
    "${DOCKER_MOUNTS[@]}" \
    -w "$C_WORKFLOW" \
    "$DOCKER_IMAGE" \
    python /workspace/pre_processing/build_tf1_inference_tiles_streaming.py \
        --input-dir "$C_INPUT" \
        --output-root "$C_OUTPUT_ROOT" \
        --pattern "$PATTERN" \
        --voxel-size "$VOXEL_SIZE" \
        --target-tile-points "$TARGET_TILE_POINTS" \
        --min-tile-points "$MIN_TILE_POINTS" \
        --min-radius "$MIN_RADIUS" \
        --overlap "$OVERLAP" \
        --chunk-size "$CHUNK_SIZE" \
        --workers "$PREPROCESS_WORKERS" \
        --max-concurrent-tiles "$MAX_CONCURRENT_TILES" \
        $OVERWRITE_FLAG
t1=$(date +%s)
echo "[1/3] tile builder done in $((t1-t0)) s"
echo ""

echo "[2/3] TF1 inference..."
t0=$(date +%s)
# CUDA_CACHE_PATH on the /exp mount (2026-06-06): GPUs newer than the
# image's SASS targets (e.g. CC 12.0 Blackwell) driver-JIT every kernel
# from PTX — "30 minutes or longer" per TF process, repeated per container
# without a persistent cache. /exp persists across phases AND runs, so the
# compile is paid once per machine. MAXSIZE raised: TF1's kernel set
# overflows the 256 MB default and silently re-compiles.
MSYS_NO_PATHCONV=1 docker run --rm --pull=never \
    --gpus all --shm-size=8gb \
    -e CUDA_CACHE_PATH=/exp/.cuda_cache \
    -e CUDA_CACHE_MAXSIZE=2147483648 \
    "${DOCKER_MOUNTS[@]}" \
    -w "$C_WORKFLOW/tf1" \
    "$DOCKER_IMAGE" \
    python classification.py \
        --input_inputconfig "$C_WORKFLOW/$INPUT_CONFIG" \
        --input_folder "$C_TILE_DIR" \
        --out_folder "$C_TF1_OUTPUT" \
        --model_folder "/model"
t1=$(date +%s)
echo "[2/3] inference done in $((t1-t0)) s"
echo ""

echo "[3/3] Merge per-source predictions..."
t0=$(date +%s)
MSYS_NO_PATHCONV=1 docker run --rm --pull=never \
    "${DOCKER_MOUNTS[@]}" \
    -w "$C_WORKFLOW" \
    "$DOCKER_IMAGE" \
    python /workspace/post_processing/merge_tf1_tile_predictions.py \
        --manifest "$C_MANIFEST" \
        --tf1-output-root "$C_TF1_OUTPUT" \
        --output-dir "$C_COMBINED_OUTPUT" \
        --workers "$POSTPROCESS_WORKERS" \
        --overwrite
t1=$(date +%s)
echo "[3/3] merge done in $((t1-t0)) s"
echo ""

echo "DONE. Per-source classified LAS at:"
echo "  $OUTPUT_ROOT/combined_outputs/"
# NOTE: `| sed -n '1,20p' || true`, NOT `| head -20`. Under `set -euo pipefail`
# (line 33) `head` closes the pipe after 20 lines -> `ls` gets SIGPIPE -> the
# script's LAST command exits 141 and aborts an already-successful multi-hour GPU
# stage (the live Mississauga crash, 2026-06-23). `sed` consumes all of ls's
# output (no SIGPIPE) and `|| true` keeps this diagnostic exit-neutral. Same fix
# as run_tf1_wave_b.sh:208 — do not "tidy" it back to `| head`.
ls -lh "$OUTPUT_ROOT/combined_outputs/" 2>&1 | sed -n '1,20p' || true
