#!/usr/bin/env bash
# Wave B streaming PointCONV inference from pre-sampled patches.
#
# Drop-in alternative to run_tf1_tiled_multi_source.sh for chain runs that
# have Stage 0c's `presample_pointconv_patches: true` set (which writes
# <run>/02_pole_crop/output/patches_pointconv/<pole>_patches.npz).
#
# Unlike the v0 path (tile-build → SamplePoints → infer → merge), Wave B
# is a single streaming pass: load cached patches → infer in batches →
# vote-aggregate → write classified LAS. Eliminates the per-tile SamplePoints
# cost (the GPU-starvation cause in v0) by doing it once during the
# host-CPU-idle Stage 0c window.
#
# Result: <output_root>/combined_outputs/<source_stem>_tf1_pointconv_combined_0p1m.las
# Schema-identical to v0 (extra dims source_class, pointconv_prob, pointconv_votes).
#
# Usage:
#   bash run_tf1_wave_b.sh <PATCHES_DIR> <OUTPUT_ROOT> <RUN_DIR>
#
# Env-var overrides:
#   MODEL_NAME       — default PointCONV_model_6class_Mobile_v0.0.15
#   BATCH_SIZE       — patches per TF1 batch (default 12, matches v0)
#   INPUT_CONFIG     — default tf1/inputconfig.yml
#   DOCKER_IMAGE     — default mmworkflow:v1.8.0.1
#   RANDOM_SEED      — must match Stage 0c's presample (default 42)
#
# Example (chain orchestrator invocation):
#   PATCHES_DIR=/exp/<run>/02_pole_crop/output/patches_pointconv \
#   OUTPUT_ROOT=/exp/<run>/01_pointconv \
#   RUN_DIR=/exp/<run> \
#   bash run_tf1_wave_b.sh "$PATCHES_DIR" "$OUTPUT_ROOT" "$RUN_DIR"

set -euo pipefail

if [ $# -ne 3 ]; then
    echo "Usage: $0 <PATCHES_DIR> <OUTPUT_ROOT> <RUN_DIR>"
    echo ""
    echo "Wave B streaming inference. Requires Stage 0c's presample cache."
    exit 1
fi

PATCHES_DIR="$1"
OUTPUT_ROOT="$2"
RUN_DIR="$3"

# --- Paths + image (env-overridable) ---------------------------------------
# Self-locating default (see run_tf1_tiled_multi_source.sh): the hardcoded
# G:/geotools default broke fresh workstations (2026-06-05) and a live run
# when the checkout was renamed (2026-06-06). pwd -W -> Windows-style for
# docker -v on Git Bash; plain pwd elsewhere.
WORKFLOW="${WORKFLOW:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && (pwd -W 2>/dev/null || pwd))}"
MODEL_DIR="${MODEL_DIR:-$WORKFLOW/models}"
MODEL_NAME="${MODEL_NAME:-PointCONV_model_6class_Mobile_v0.0.15}"
DOCKER_IMAGE="${DOCKER_IMAGE:-750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1}"
AWS_DIR="${AWS_DIR:-$HOME/.aws}"
INPUT_CONFIG="${INPUT_CONFIG:-tf1/inputconfig.yml}"
# 24 is the safe ceiling on a 24 GB card (#91 sweep: batch 48 OOMs in the
# fa_layer4 aggregation op, which needs ~12 GiB at 48). 12->24 is ~9% faster.
BATCH_SIZE="${BATCH_SIZE:-24}"
RANDOM_SEED="${RANDOM_SEED:-42}"

# Output dir for classified LAS.
COMBINED_OUTPUT="$OUTPUT_ROOT/combined_outputs"
mkdir -p "$COMBINED_OUTPUT"

# Refuse to run if the cache doesn't exist or has no patches.
if [ ! -d "$PATCHES_DIR" ]; then
    echo "ERROR: patches_dir not found: $PATCHES_DIR"
    echo "Run Stage 0c with stage0c_reproject_crops.presample_pointconv_patches: true"
    exit 2
fi
N_NPZ=$(find "$PATCHES_DIR" -maxdepth 1 -name "*_patches.npz" | wc -l)
if [ "$N_NPZ" -eq 0 ]; then
    echo "ERROR: no *_patches.npz files in $PATCHES_DIR"
    exit 2
fi

# Resolve Docker mounts.
# Data root anchor: parent of PATCHES_DIR. We need /exp = <run>/... root.
# Walk up to find the experiment root (parent of the run dir).
EXP_ROOT="$(dirname "$RUN_DIR")"

# In-container paths.
C_WORKFLOW=/workspace
C_EXP=/exp
C_PATCHES="$C_EXP/${PATCHES_DIR#$EXP_ROOT/}"
C_OUTPUT_ROOT="$C_EXP/${OUTPUT_ROOT#$EXP_ROOT/}"
C_RUN_DIR="$C_EXP/${RUN_DIR#$EXP_ROOT/}"

# Convert backslashes to forward slashes (Git Bash safety).
C_PATCHES="${C_PATCHES//\\//}"
C_OUTPUT_ROOT="${C_OUTPUT_ROOT//\\//}"
C_RUN_DIR="${C_RUN_DIR//\\//}"
C_COMBINED_OUTPUT="$C_OUTPUT_ROOT/combined_outputs"

echo "==========================================================================="
echo "PATCHES_DIR   = $PATCHES_DIR"
echo "OUTPUT_ROOT   = $OUTPUT_ROOT"
echo "RUN_DIR       = $RUN_DIR"
echo "EXP_ROOT      = $EXP_ROOT           (mounted at /exp)"
echo "WORKFLOW      = $WORKFLOW           (mounted at /workspace)"
echo "MODEL         = $MODEL_DIR/$MODEL_NAME -> /model/$MODEL_NAME"
echo "Container patches: $C_PATCHES"
echo "Container output:  $C_COMBINED_OUTPUT"
echo "BATCH_SIZE    = $BATCH_SIZE"
echo "Cache files:  $N_NPZ npz files"
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
# CHAIN_HOST_PATH_MAP is unset).
HOST_WORKFLOW="$(_host_mount_src "$WORKFLOW")"
HOST_EXP_ROOT="$(_host_mount_src "$EXP_ROOT")"
HOST_MODEL_DIR="$(_host_mount_src "$MODEL_DIR")"
DOCKER_MOUNTS=(
    "-v" "$HOST_WORKFLOW:/workspace"
    "-v" "$HOST_EXP_ROOT:/exp"
    "-v" "$HOST_MODEL_DIR:/model"
)
if [ -d "$AWS_DIR" ]; then
    HOST_AWS_DIR="$(_host_mount_src "$AWS_DIR")"
    DOCKER_MOUNTS+=("-v" "$HOST_AWS_DIR:/root/.aws:ro")
fi

echo "[1/1] Wave B streaming inference..."
t0=$(date +%s)
# Persistent CUDA JIT cache — see run_tf1_tiled_multi_source.sh (2026-06-06).
MSYS_NO_PATHCONV=1 docker run --rm --pull=never \
    --gpus all --shm-size=8gb \
    -e CUDA_CACHE_PATH=/exp/.cuda_cache \
    -e CUDA_CACHE_MAXSIZE=2147483648 \
    "${DOCKER_MOUNTS[@]}" \
    -w "$C_WORKFLOW/tf1" \
    "$DOCKER_IMAGE" \
    python classification_from_patches.py \
        --patches-dir "$C_PATCHES" \
        --output-dir "$C_COMBINED_OUTPUT" \
        --model-dir "/model/$MODEL_NAME" \
        --inputconfig "$C_WORKFLOW/$INPUT_CONFIG" \
        --run-dir "$C_RUN_DIR" \
        --batch-size "$BATCH_SIZE" \
        --random-seed "$RANDOM_SEED" \
        ${EXTRA_ARGS:-}
t1=$(date +%s)
echo "[1/1] Wave B inference done in $((t1-t0)) s"
echo ""

echo "DONE. Per-source classified LAS at:"
echo "  $OUTPUT_ROOT/combined_outputs/"
# Diagnostic listing ONLY — must never affect the script's exit status. This is
# the last command, so under `set -o pipefail` a `| head` that closes the pipe
# early sent SIGPIPE to `ls` (exit 141) and the chain reported a fully-successful
# 50-min GPU stage as FAILED (only on runs with >19 output files). `sed` consumes
# all input (no SIGPIPE) and `|| true` makes the line exit-neutral regardless.
ls -lh "$OUTPUT_ROOT/combined_outputs/" 2>&1 | sed -n '1,20p' || true
