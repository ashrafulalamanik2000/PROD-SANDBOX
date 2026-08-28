#!/usr/bin/env bash
# Run PointCONV classification on a folder of LAS/LAZ — GPU-optimized for the
# RTX 4090 (Ada) and RTX 5080 (Blackwell), tuned for large clouds (streaming
# tile builder). Wraps run_tf1_tiled_multi_source.sh; the .bat next to this file
# is the double-click entry point. See run_pointconv.bat for usage.
#
#   bash run_pointconv.sh <INPUT_DIR> <OUTPUT_DIR> [--check-models] [--model NAME]
#
# Output: <OUTPUT_DIR>/combined_outputs/<source>_tf1_pointconv_combined_0p1m.las
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"     # this script's folder
# Resolve the PointCONV_TF1_Workflow dir (holds the runner, models/, tf1/ configs):
#   1. this folder, if it IS the workflow (running from the repo); else
#   2. $POINTCONV_DIR (set this when running from the tester handoff); else error.
if [ -f "$HERE/run_tf1_tiled_multi_source.sh" ]; then
    WF="$HERE"
elif [ -n "${POINTCONV_DIR:-}" ] && [ -f "${POINTCONV_DIR%/}/run_tf1_tiled_multi_source.sh" ]; then
    WF="${POINTCONV_DIR%/}"
else
    echo "ERROR: PointCONV_TF1_Workflow not found. Run this from inside that folder," >&2
    echo "       or set POINTCONV_DIR to it, e.g.  set \"POINTCONV_DIR=C:\\path\\to\\PointCONV_TF1_Workflow\"" >&2
    exit 5
fi
S3MODELS="s3://sdai-model/lidar_ml"
ECR_HOST="750433818015.dkr.ecr.us-west-2.amazonaws.com"
IMG="${DOCKER_IMAGE:-$ECR_HOST/mmworkflow:v1.8.0.1}"
BASE_CFG="tf1/inputconfig_finetune.yml"                  # dim-6 config

# ---- args ------------------------------------------------------------------
INPUT_DIR=""; OUTPUT_DIR=""; CHECK_MODELS=0; MODEL_OVERRIDE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --check-models) CHECK_MODELS=1; shift;;
        --model)        MODEL_OVERRIDE="${2:-}"; shift 2;;
        -h|--help)
            echo "usage: run_pointconv.sh <INPUT_DIR> <OUTPUT_DIR> [--check-models] [--model NAME]"
            exit 0;;
        *) if   [ -z "$INPUT_DIR" ];  then INPUT_DIR="$1"
           elif [ -z "$OUTPUT_DIR" ]; then OUTPUT_DIR="$1"; fi; shift;;
    esac
done
[ -n "$INPUT_DIR" ]  || read -rp "Input folder (LAS/LAZ): " INPUT_DIR
[ -n "$OUTPUT_DIR" ] || read -rp "Output folder: " OUTPUT_DIR
INPUT_DIR="${INPUT_DIR//\\//}"; OUTPUT_DIR="${OUTPUT_DIR//\\//}"   # normalize \ -> /
[ -d "$INPUT_DIR" ] || { echo "ERROR: input folder not found: $INPUT_DIR" >&2; exit 2; }
mkdir -p "$OUTPUT_DIR"
N=$(find "$INPUT_DIR" -maxdepth 1 -iname '*.las' -o -maxdepth 1 -iname '*.laz' 2>/dev/null | wc -l)
[ "$N" -gt 0 ] || { echo "ERROR: no .las/.laz in $INPUT_DIR" >&2; exit 2; }

# ---- GPU detection -> per-GPU tuning ---------------------------------------
GNAME="$(nvidia-smi --query-gpu=name        --format=csv,noheader 2>/dev/null | head -1)"
GCC="$(  nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')"
GMEM="$( nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -dc '0-9')"
GMEM="${GMEM:-0}"
# VRAM drives batch size + tile concurrency; bigger = faster but more memory.
if   [ "$GMEM" -ge 22000 ]; then BATCH_SIZE=48; MAX_CONCURRENT_TILES=8   # 24 GB (4090)
elif [ "$GMEM" -ge 15000 ]; then BATCH_SIZE=32; MAX_CONCURRENT_TILES=6   # 16 GB (5080)
elif [ "$GMEM" -ge 10000 ]; then BATCH_SIZE=24; MAX_CONCURRENT_TILES=4
else                             BATCH_SIZE=16; MAX_CONCURRENT_TILES=3; fi
# Large-cloud tiling: ~400k-point tiles keep peak memory bounded while the
# streaming builder parallelizes; PREPROCESS_WORKERS scales with host cores.
TARGET_TILE_POINTS="${TARGET_TILE_POINTS:-400000}"
PREPROCESS_WORKERS="${PREPROCESS_WORKERS:-$(nproc 2>/dev/null || echo 4)}"
[ "$PREPROCESS_WORKERS" -gt 8 ] && PREPROCESS_WORKERS=8
echo "[pointconv] GPU: ${GNAME:-unknown} (cc ${GCC:-?}, ${GMEM} MiB)"
echo "[pointconv]   -> BATCH_SIZE=$BATCH_SIZE  MAX_CONCURRENT_TILES=$MAX_CONCURRENT_TILES  TARGET_TILE_POINTS=$TARGET_TILE_POINTS"
case "${GCC:-}" in
    12.*|13.*) echo "[pointconv]   Blackwell GPU: the image ships PTX (no sm_120 SASS), so the CUDA driver"
               echo "[pointconv]   JIT-compiles on first load (~a few minutes). The result is cached under"
               echo "[pointconv]   <OUTPUT_DIR>/.cuda_cache, so subsequent runs are fast.";;
esac

# ---- model selection (model_directory in the inputconfig is authoritative) --
DEFAULT_MODEL="$(grep -oE '^[[:space:]]*model_directory:[[:space:]]*\S+' "$WF/$BASE_CFG" | awk '{print $2}')"
MODEL="${MODEL_OVERRIDE:-$DEFAULT_MODEL}"
if [ "$CHECK_MODELS" = 1 ]; then
    echo "[pointconv] checking S3 ($S3MODELS) for the latest 6-class Mobile model..."
    LATEST="$(aws s3 ls "$S3MODELS/" 2>/dev/null | awk '{print $2}' \
              | grep -E '^PointCONV_model_6class_Mobile_v[0-9].*/$' | sed 's#/$##' \
              | sort -V | tail -1)"
    echo "[pointconv]   latest on S3  : ${LATEST:-<none found>}"
    echo "[pointconv]   default local : $DEFAULT_MODEL"
    if [ -n "$LATEST" ] && [ -z "$MODEL_OVERRIDE" ]; then
        MODEL="$LATEST"
        [ "$MODEL" != "$DEFAULT_MODEL" ] && echo "[pointconv]   -> using the newer S3 model: $MODEL"
    fi
fi
# fetch the chosen model if it is not present locally
if [ ! -d "$WF/models/$MODEL/Best_Model" ]; then
    echo "[pointconv] model '$MODEL' not local -> downloading from S3..."
    aws s3 cp "$S3MODELS/$MODEL" "$WF/models/$MODEL" --recursive --only-show-errors \
        || { echo "ERROR: could not download model '$MODEL' from S3." >&2; exit 4; }
fi
echo "[pointconv] using model: $MODEL"

# per-run inputconfig pointing classification.py at the chosen model
RUN_CFG_REL="tf1/_inputconfig_run.yml"
sed -E "s#^([[:space:]]*model_directory:[[:space:]]*).*#\1$MODEL#" \
    "$WF/$BASE_CFG" > "$WF/$RUN_CFG_REL"

# ---- ensure the GPU image (the tiled runner uses --pull=never) -------------
if ! docker image inspect "$IMG" >/dev/null 2>&1; then
    echo "[pointconv] $IMG not local -> pulling from ECR..."
    aws ecr get-login-password --region us-west-2 2>/dev/null \
        | docker login --username AWS --password-stdin "$ECR_HOST" >/dev/null 2>&1 || true
    docker pull "$IMG" || { echo "ERROR: image not local and ECR pull failed." >&2; exit 3; }
fi

# ---- run the streaming tiled PointCONV pipeline ----------------------------
echo "[pointconv] classifying $N file(s): $INPUT_DIR -> $OUTPUT_DIR"
BATCH_SIZE="$BATCH_SIZE" MAX_CONCURRENT_TILES="$MAX_CONCURRENT_TILES" \
TARGET_TILE_POINTS="$TARGET_TILE_POINTS" PREPROCESS_WORKERS="$PREPROCESS_WORKERS" \
POSTPROCESS_WORKERS="$PREPROCESS_WORKERS" PATTERN="${PATTERN:-*.la[sz]}" \
INPUT_CONFIG="$RUN_CFG_REL" MODEL_NAME="$MODEL" DOCKER_IMAGE="$IMG" \
    bash "$WF/run_tf1_tiled_multi_source.sh" "$INPUT_DIR" "$OUTPUT_DIR"

echo ""
echo "[pointconv] DONE. Classified clouds in: $OUTPUT_DIR/combined_outputs/"
