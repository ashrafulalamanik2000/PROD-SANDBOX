# How to Run

A single-page runbook for the four common operations on this workflow:

1. [Inference with the default 6-class model](#1-inference-with-the-default-6-class-model)
2. [Inference with the fine-tuned model](#2-inference-with-the-fine-tuned-model)
3. [Fine-tune the model on new labeled data](#3-fine-tune-the-model-on-new-labeled-data)
4. [Score predictions vs ground truth (IoU)](#4-score-predictions-vs-ground-truth-iou)

For deeper context see [TF1_TILED_INFERENCE_WORKFLOW.md](TF1_TILED_INFERENCE_WORKFLOW.md) and [FINETUNE_WORKFLOW.md](FINETUNE_WORKFLOW.md).

## Prerequisites

- Docker Desktop with the NVIDIA GPU runtime enabled.
- The image `750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1` already pulled (or AWS ECR login configured for `--pull=always`).
- AWS credentials at `%homedrive%%homepath%\.aws\` with read access to `s3://sdai-model/lidar_ml/` (only required if the model directory isn't already local — the inference wrapper auto-downloads).
- Source LAS files in meters, not feet.
- Python 3.9+ on the host with `laspy`, `numpy`, `scipy`, `joblib`, `tqdm`, `pyyaml` (for the data-prep + IoU helpers that run outside Docker).
- On Git Bash on Windows: prepend `MSYS_NO_PATHCONV=1` to every `docker run` invocation so paths like `/workspace` are not mangled to `C:/Program Files/Git/workspace`.

The examples below use three placeholders — substitute the paths for your
machine (drive letters vary per machine; use full paths):

```
<WORKFLOW>    = this PointCONV_TF1_Workflow folder (the bundled workflow dir)
<DATA>        = your data root (contains DTECH_2025\ClassifiedLAS)
<EXPERIMENTS> = where run outputs should be written
```

## 1. Inference with the default 6-class model

For small input folders (LAS files fit comfortably in a single sample sweep), use the legacy launcher:

```powershell
<WORKFLOW>\tf1\Docker_Run_Classification.bat
```

Edit `DATA_ROOT`, `DATA_LAS`, `CLASS_DIR`, `MODEL_DIR` near the top of the .bat. The wrapper will auto-download `s3://sdai-model/lidar_ml/PointCONV_model_6class_v0.0.10` into `<DATA_ROOT>\<MODEL_DIR>\PointCONV_model_6class_v0.0.10` on first use.

For large LAS files (>~50 M points), use the tiled launcher:

```powershell
<WORKFLOW>\run_tf1_tiled_ottercreek.ps1 `
  -RunName tf1_pointconv_0p1m_tiled_initial -Overwrite
```

Outputs land at `<DataRoot>\DTECH_2025_experiments\<RunName>\combined_outputs\<source_stem>_tf1_pointconv_combined_0p1m.las`. The `classification` field carries the predicted PointCONV class; `source_class`, `pointconv_prob`, `pointconv_votes` are extra dimensions.

### 1c. Multi-pass / left-right inputs of the same scene

When the input is **two or more lidar passes of the same area** (e.g. left + right sensor of a mobile run, or repeated coverage), combine + thin the raw inputs to 0.1 m **before** inference and run the model **once** on the unified cloud. This is faster than inferring each pass separately and reconciling at the end, and produces a cleaner output (no per-pass disagreements at boundaries).

```powershell
<WORKFLOW>\run_tf1_tiled_left_right.ps1 `
  -InputDir  "<DATA>\OAKVILLE2\LAS Files" `
  -RunRoot   "<EXPERIMENTS>" `
  -RunName   inference_oakville2_combined `
  -CombinedSourceName Run55_LR `
  -Overwrite
```

The launcher does, in one detached pipeline:

1. `tools/combine_thinned_las.py` over every `*.las` in `-InputDir` -> one voxelized union at the target voxel size, written to `<run>/combined_source/<CombinedSourceName>.las`. Per-voxel tie-break uses `intensity` for raw inputs (or `pointconv_prob` if the inputs are already classified PointCONV outputs — same script, auto-detected).
2. `pre_processing/build_tf1_inference_tiles.py` on that single combined LAS.
3. `tf1/classification.py` against the fine-tuned model (`PointCONV_model_6class_Mobile_v0.0.10`).
4. `post_processing/merge_tf1_tile_predictions.py` -> single `<run>/combined_outputs/<CombinedSourceName>_tf1_pointconv_combined_0p1m.las`.

`InputDir` must live anywhere under `DataRoot` (default `<DATA>`); the script checks this and fails fast if it's not.

For an **already-inferred** left + right pair (you already ran inference twice and want to merge results), use the smaller helper directly — it auto-detects the PointCONV extra dims and tie-breaks on `pointconv_prob`:

```bash
python tools/combine_thinned_las.py \
  --inputs A_tf1_pointconv_combined_0p1m.las B_tf1_pointconv_combined_0p1m.las \
  --output AB_combined_tf1_pointconv_combined_0p1m.las \
  --voxel-size 0.1
```

## 2. Inference with the fine-tuned model

Same two patterns, but pointing at `PointCONV_model_6class_Mobile_v0.0.10` (auto-downloaded from `s3://sdai-model/lidar_ml/PointCONV_model_6class_Mobile_v0.0.10`).

Single-folder:

```powershell
<WORKFLOW>\tf1\Docker_Run_Classification_Finetune.bat
```

Tiled:

```powershell
<WORKFLOW>\run_tf1_tiled_finetune.ps1 `
  -ModelFolder "<path\to\folder containing PointCONV_model_6class_Mobile_v0.0.10>" `
  -Overwrite
```

If the model folder doesn't yet contain `PointCONV_model_6class_Mobile_v0.0.10`, the wrapper logs a warning and the inference container downloads from S3 using the host's `~/.aws` credentials (mounted automatically).

## 3. Fine-tune the model on new labeled data

Three stages: prepare 16,384-point training samples on the host, train inside Docker, evaluate.

### 3a. Prepare data

```bash
python <WORKFLOW>/finetune/prepare_finetune_data.py \
  --config  <WORKFLOW>/finetune/finetune_config.yml \
  --mapping <WORKFLOW>/finetune/dtech_to_model_mapping.yml \
  --source-thinned-dir <path with *_thin_0p1m.las> \
  --output-dir <EXPERIMENTS>/<run-name>/data
```

Edit [finetune/dtech_to_model_mapping.yml](finetune/dtech_to_model_mapping.yml) before running if your DTECH source classes don't match the Otter Creek defaults.

The script writes `train/`, `val/`, `test/` subfolders of `lrn_xyz/class/smpw/scale_*.npy` files plus a `manifest.json`.

### 3b. Train

```bash
MSYS_NO_PATHCONV=1 docker run --rm --pull=never --gpus all --shm-size=8gb \
  -v "<WORKFLOW>:/workspace" \
  -v "<EXPERIMENTS>:/exp" \
  -v "<DATA>:/data" \
  -w /workspace \
  750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1 \
  python /workspace/finetune/train_finetune.py \
    --config /workspace/finetune/finetune_config.yml \
    --data-root /exp/<run-name>/data \
    --model-out /exp/<run-name>/model \
    --log-dir /exp/<run-name>/logs/train
```

For a quick verification first, add `--smoke-test --max-train-regions 30 --max-val-regions 12` (~2 min instead of ~50 min on RTX 4090).

The trainer warm-starts from `models/PointCONV_model_6class_v0.0.10/Best_Model/model.ckpt`, freezes the transmission-tower head, and writes the new model directory to `<run>/model/PointCONV_model_6class_Mobile_v0.0.10/` with the contract the inference path expects (`Best_Model/`, `model.ckpt.*`, `exp_def.p`).

For long runs (>10 min), use `docker run -d --name pointconv_finetune` and tail with `docker logs -f pointconv_finetune` to dodge any host-shell timeout.

### 3c. Evaluate the fine-tuned model

End-to-end against the same baseline tiles + manifest, with side-by-side IoU comparison:

```bash
python <WORKFLOW>/finetune/run_post_finetune_eval.py \
  --run-name <run-name> \
  --baseline-iou-mapping <EXPERIMENTS>/iou_eval_20260429_105436/class_mapping.yml \
  --baseline-iou-dir     <EXPERIMENTS>/iou_eval_20260429_105436
```

Outputs:
- `<run>/post_finetune_eval/combined_outputs/*_tf1_pointconv_combined_0p1m.las`
- `<run>/post_finetune_eval/iou/{iou_per_source.csv, iou_aggregate.csv, confusion_matrix.csv, iou_summary.json}`
- `<run>/post_finetune_eval/comparison.json` (per-class IoU baseline vs fine-tune)

### 3d. Publish the fine-tuned model to S3

So other machines can auto-download it:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --pull=never \
  -v "<WORKFLOW>:/workspace" \
  -v "<EXPERIMENTS>:/exp" \
  -v "$HOME/.aws:/root/.aws:ro" \
  -w /workspace \
  750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1 \
  python /workspace/tools/upload_finetune_model_to_s3.py \
    --local-dir /exp/<run-name>/model/<model-dir-name>
```

Add `--dry-run` to preview, `--overwrite` to replace existing keys.

## 4. Score predictions vs ground truth (IoU)

Once you have a `combined_outputs/` folder of merged LAS files (each carrying both the `classification` and `source_class` extra dim — true for any output of the tiled-inference path), you can compute IoU on the host:

```bash
python <WORKFLOW>/tools/compute_pointconv_iou.py \
  --combined-dir <path-to-combined_outputs> \
  --mapping <EXPERIMENTS>/iou_eval_20260429_105436/class_mapping.yml \
  --output-dir <path-for-results>
```

Outputs:
- `iou_per_source.csv` — IoU per (source LAS × class)
- `iou_aggregate.csv` — corpus-level IoU per class + mean
- `confusion_matrix.csv` — 6×6 confusion (rows = ground truth, cols = predicted)
- `iou_summary.json` — programmatic summary including ignored-source histogram

Edit the mapping yml to control which DTECH source classes feed each PointCONV class and which are ignored. The Otter Creek default mapping is documented in [FINETUNE_WORKFLOW.md](FINETUNE_WORKFLOW.md#dtech-source---model-index-mapping).

## Where outputs live

```
experiments/
  iou_eval_<ts>/                         # IoU run (any model)
    iou_*.csv, confusion_matrix.csv, iou_summary.json, class_mapping.yml
  finetune_<ts>/
    data/                                # 16K-point training samples
    logs/                                # training history.json + TB events
    model/<model-dir-name>/              # trained checkpoint dir
    post_finetune_eval/                  # inference + IoU + comparison.json
DTECH_<dataset>_experiments/
  tf1_pointconv_0p1m_tiled_<ts>/         # tiled-inference run (default model)
    preprocessed_tiles/, tf1_outputs/, combined_outputs/, manifests/
```

## Quick troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `docker: working directory '/workspace' is invalid` | Git Bash path mangling — prepend `MSYS_NO_PATHCONV=1` |
| `Could not restore warm-start checkpoint ... global_step not found` | Older trainer version; the current one filters the saver var-list to checkpoint variables only — pull the latest. |
| `KeyError: 'scale_type'` during inference | The model dir's `exp_def.p` is missing `scale_type`. Re-train with the current trainer (it writes `scale_type=5`) or patch the file. |
| Training works but tower predictions drift after fine-tune | Confirm `pred_active = pred_active / sum(...)` re-normalization is in `finetune/train_finetune.py`. The drift drops from ~0.017 to ~3 e-04 with the re-normalization in place. |
| `boto3.exceptions.NoCredentialsError` on first inference | Mount `~/.aws` into the container or bake credentials into the image. |
