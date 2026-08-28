---
name: pointconv-classification
description: >
  Classify a point cloud into the DTECH 6-class scheme (ground / low-med-high
  vegetation / building / wire / pole) with a PointCONV TF1 model. Host-
  orchestrated GPU stage: presample dim-6 patches on the host (CPU), then run
  TF1 inference inside the mmworkflow GPU image. This is chain stage 1 —
  everything downstream (veg-outline, tree-trunk, pole-vec) consumes its output.
  Use when asked to "classify a LAS/LAZ", "run PointCONV", "label ground/veg/
  building", or to produce a classified cloud from a colorized point cloud.
argument-hint: <las|laz|dir> [--run-dir <dir>] [--pcv-dir <PointCONV_TF1_Workflow>] [--model-name NAME] [--epsg N]
user-invocable: true
allowed-tools: Bash, Read, Glob, Grep
---

# PointCONV Classification (stage 1, GPU)

Classifies a **colorized** point cloud into the DTECH 6-class scheme using a
PointCONV TF1 model. Two steps, wired by `scripts/pointconv_classify.py`:

1. **Presample (host, CPU)** — `presample_pointconv_patches.py` voxelizes and
   samples 16K-point **dim-6 (XYZ + RGB)** patches → `<run>/01_pointconv/patches/*.npz`.
2. **Inference (GPU, sibling container)** — runs `tf1/classification_from_patches.py`
   inside the **mmworkflow** image; per-point labels merged back to
   `<run>/01_pointconv/combined_outputs/*_combined_0p1m.laz`.

## ⚠️ Input must be COLORIZED (RGB)
The model is **dim-6 = XYZ + RGB**; the presampler reads `red/green/blue`. A cloud
with no RGB (LAS point-format 0/1) **fails** (`LasData has no attribute 'red'`).
Colorize first (project imagery onto the cloud) — LAS point-format **2/3/7/8** with
RGB is required.

## Class codes (output `classification`)
`0` Unclassified · `2` Ground · `3` Low Veg · `4` Med Veg · `5` High Veg ·
`6` Building · `14` Wire · `15` Pole

## Run-dir contract
```
<run>/01_pointconv/source/*.la[sz]                                   # input (colorized)
<run>/01_pointconv/combined_outputs/*_tf1_pointconv_combined_0p1m.laz # classified (0.1 m)
```

## Self-contained
The **`PointCONV_TF1_Workflow/` (tf1 code, dim-6 config, and the 6-class model) is
bundled in this skill** (the model is git-LFS — run `git lfs pull` after cloning).
The only external dependency is the **mmworkflow GPU image** (pulled once) + a GPU.
No `--pcv-dir`/env needed unless you want a different workflow/model.

## Usage
Prereqs in [SETUP.md](SETUP.md) (GPU, mmworkflow image, host python deps, `git lfs pull`).

POSIX / Git Bash:
```bash
bash scripts/run.sh \
  /abs/colorized.laz --run-dir /abs/runs/myrun \
  --image 750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1 \
  --epsg 26917
```
Windows:
```cmd
scripts\run.cmd C:\abs\colorized.laz --run-dir C:\abs\runs\myrun --epsg 26917
```
If `--run-dir` is omitted, a file uses `<parent>/<stem>_pointconv_run` and a
directory uses `<directory>/_pointconv_run`. The launcher validates the input
headers, RGB point format, bundled workflow/model/config, host Python modules,
Docker daemon, and local image before staging or processing. Run the gate only:
```cmd
scripts\run.cmd C:\abs\colorized.laz --preflight-only --preflight-json
```

Defaults: bundled `--pcv-dir`, model `PointCONV_model_6class_Mobile_v0.0.18_retune_c2`,
config `tf1/inputconfig_finetune_lowmem.yml`. Override any as needed.

## Key flags (pass through to pointconv_classify.py)
| flag | default | meaning |
|------|---------|---------|
| `--pcv-dir` | `$POINTCONV_TF1_DIR` | path to `PointCONV_TF1_Workflow` (has `tf1/`, `models/`) |
| `--model-name` | `PointCONV_model_6class_Mobile_v0.0.18_retune_c2` | model subdir under `models/` (must exist there or be baked in image) |
| `--model-dir` | `<pcv>/models` | dir containing the model subdir |
| `--inputconfig` | `tf1/inputconfig_finetune_lowmem.yml` | dim-6 config (presample + inference MUST match — hash-checked) |
| `--image` | `…/mmworkflow:v1.8.0.1` | GPU image (use your local tag if different) |
| `--batch-size` | 24 | GPU batch |
| `--epsg` | none | CRS stamped on output when the source has none |
| `--rebuild-patches` | — | delete and regenerate patches only when the input/config manifest mismatches |
| `--dry-run` | — | print the commands only |

## Notes
- **Multiple files** in `source/` are all classified (one output each). Overlapping
  runs are fine as separate outputs; merge first (PDAL) only if you need one cloud.
- Output is voxel-downsampled to **0.1 m** (`_combined_0p1m`) — that's the stage's
  native output resolution, not the full input density.
- Existing patches are reused only when their manifest matches the staged inputs,
  config, and random seed. Stale staged LAS/LAZ files fail the gate instead of
  being silently processed.
- CRS: `classification_from_patches.py` writes from numpy patches (CRS-less); the
  orchestrator stamps the source CRS, or `--epsg` fallback, onto the output.
- Exit codes: `0` success, `1` processing failure, `2` input/configuration gate
  failure. Routine execution is script-driven and requires no model tokens.
