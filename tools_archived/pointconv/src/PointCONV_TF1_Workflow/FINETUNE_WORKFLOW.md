# Fine-tune Workflow (TF1 PointCONV 6-class)

This workflow fine-tunes the legacy TF1 `PointCONV_model_6class_v0.0.10` on a
DTECH-style labeled LAS corpus. It reuses the same 0.1 m thinning, 16,384-point
sampler, and 6-class architecture used for inference, so the fine-tuned model
can be dropped into the existing tiled-inference path with no other changes.

Examples use placeholders — substitute paths for your machine (`<WORKFLOW>` =
this bundled workflow folder, `<DATA>` = your data root, `<EXPERIMENTS>` =
output root). The reference dataset was the two Otter Creek source LAS files at:

```
<DATA>\DTECH_2025\ClassifiedLAS
```

Outputs land under `<EXPERIMENTS>\<run-name>\`.

## Pipeline

| Stage | Script | Runs in |
| --- | --- | --- |
| 1. Data preparation | [prepare_finetune_data.py](finetune/prepare_finetune_data.py) | host Python (laspy + scipy + joblib) |
| 2. Fine-tune training | [train_finetune.py](finetune/train_finetune.py) | TF1 Docker (`mmworkflow:v1.8.0.1`) |
| 3. Tiled inference with fine-tuned model | [tf1/classification.py](tf1/classification.py) | TF1 Docker, GPU |
| 4. Tile merge | [post_processing/merge_tf1_tile_predictions.py](post_processing/merge_tf1_tile_predictions.py) | TF1 Docker |
| 5. IoU comparison | [tools/compute_pointconv_iou.py](tools/compute_pointconv_iou.py) | host Python (laspy + numpy + yaml) |

[run_post_finetune_eval.py](finetune/run_post_finetune_eval.py) orchestrates
stages 3–5 against an existing baseline run.

## Concepts

### Class layout

The model output is fixed: 6 classes in the order
`[wire, pole, veg, ground, man_made, transmission]`. Inference still maps
those indices back to LAS classifications `[14, 18, 5, 2, 6, 15]` via
`PointConv.class_mapping_model.model_to_class` in
[tf1/inputconfig.yml](tf1/inputconfig.yml).

### DTECH source -> model index mapping

The fine-tune data prep maps each DTECH source classification to a model
index using [finetune/dtech_to_model_mapping.yml](finetune/dtech_to_model_mapping.yml).
Defaults for this dataset:

| Model idx | Class | Source DTECH classes |
| --- | --- | --- |
| `0` | wire | `14, 30, 31` |
| `1` | pole | `18, 19, 20, 70, 71, 72, 73, 74, 75, 76, 77, 89, 90, 91` (utility + street/traffic + pole-mounted attachments) |
| `2` | veg | `3, 4, 5` |
| `3` | ground | `2, 40, 41, 42, 43, 50, 51, 52` |
| `4` | man_made | `6, 10` |
| `5` | transmission | `15` (frozen) |

Anything not listed is given sample weight 0 and excluded from the loss.

### Tower freeze

Index 5 (transmission tower) is excluded from the training loss. The trainer
gathers `pred[:, :, active_class_indices]` from the softmax output and then
**re-normalizes** the slice so the kept columns sum to 1. That makes the
sliced tensor identically `softmax(logits[active_class_indices])`, so any
gradient with respect to the dropped (tower) logit cancels out and the
tower fc2 row receives no gradient signal during fine-tuning. The first
fine-tune (2026-04-29 Otter Creek) used a slice-only formulation and the
tower fc2 weights drifted ~0.017; that's been fixed in
[finetune/train_finetune.py](finetune/train_finetune.py) for future runs.

## Running it

### 1. Prepare 16,384-point training samples

The data prep script expects the 0.1 m thinned LAS files from the existing
tiled-inference output (the same files we run IoU on). For the Otter Creek
defaults, point it at the `source_thinned/` folder of a finished run.

```bash
python finetune/prepare_finetune_data.py \
  --config finetune/finetune_config.yml \
  --mapping finetune/dtech_to_model_mapping.yml \
  --source-thinned-dir "<DATA>/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_20260429_1510/source_thinned" \
  --output-dir "<EXPERIMENTS>/<run-name>/data"
```

Output structure:

```
<run>/data/
  train/<source_stem>/lrn_xyz_NNNNNN.npy
                      lrn_class_NNNNNN.npy
                      lrn_smpw_NNNNNN.npy
                      lrn_scale_NNNNNN.npy
  val/<source_stem>/...
  test/<source_stem>/...
  manifest.json
```

The split is a deterministic XY-block hash (default 30 m blocks, 70/15/15) so
adjacent regions share a split and the test split never bleeds into train.

### 2. Fine-tune training

```powershell
docker run -d --name pointconv_finetune --pull=never --gpus all --shm-size=8gb `
  -v "<WORKFLOW>:/workspace" `
  -v "<EXPERIMENTS>:/exp" `
  -v "<DATA>:/data" `
  -w /workspace `
  750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1 `
  python /workspace/finetune/train_finetune.py `
    --config /workspace/finetune/finetune_config.yml `
    --data-root /exp/<run-name>/data `
    --model-out /exp/<run-name>/model `
    --log-dir /exp/<run-name>/logs/train
```

What the trainer does:

- Restores the warm-start checkpoint at
  [models/PointCONV_model_6class_v0.0.10/Best_Model/model.ckpt](models/PointCONV_model_6class_v0.0.10/Best_Model/model.ckpt).
  Variables not in the checkpoint (e.g. `global_step`, Adam slots) keep their
  initial values; this is by design.
- Trains with IoU-weighted loss over the 5 active classes only. Sample weights
  are `class_weight[label] * ignore_mask` (per-point smpw from the prep step
  multiplied by per-class inverse-frequency weights, capped at `100.0`).
- Saves the best-by-val-mean-IoU checkpoint to
  `<model-out>/<model_dir_name>/{model.ckpt.*, Best_Model/}` plus an
  `exp_def.p` matching the inference contract.
- After the loop, runs a final test-set evaluation and writes per-class IoU
  to `<log-dir>/history.json`.

Add `--smoke-test` for a 1-epoch dry run before committing to a full run.

For Windows + Git Bash, prepend `MSYS_NO_PATHCONV=1` to `docker run` so paths
like `/workspace` are not mangled to `C:/Program Files/Git/workspace`.

### 3-5. Run inference and IoU with the fine-tuned model

[finetune/run_post_finetune_eval.py](finetune/run_post_finetune_eval.py) runs
the entire post-training comparison and reuses the baseline run's
preprocessed tiles + manifest so the comparison is on identical input
geometry:

```bash
python finetune/run_post_finetune_eval.py \
  --run-name <run-name> \
  --baseline-iou-mapping <EXPERIMENTS>/iou_eval_20260429_105436/class_mapping.yml \
  --baseline-iou-dir     <EXPERIMENTS>/iou_eval_20260429_105436
```

It prints a side-by-side per-class IoU table and writes
`<run>/post_finetune_eval/comparison.json`.

## Config knobs

[finetune/finetune_config.yml](finetune/finetune_config.yml) is the single
source of truth.

| Field | Notes |
| --- | --- |
| `architecture.radii` | Must match the warm-start model. PointCONV_model_6class_v0.0.10: `[0.2, 0.4, 0.8, 1.6]`. |
| `architecture.bandwidth` | `0.5` for the bundled warm start. |
| `sampling.n_points` | `16384`. The model checkpoint is keyed to this. |
| `sampling.radius_nn` | `10.29` (the value baked into the warm-start model). |
| `spatial_split.{train_ratio,val_ratio,test_ratio}` | Defaults `0.70/0.15/0.15`. Block size `30 m`. |
| `training.learning_rate` | `1e-4` (10x smaller than original `1e-3`) for fine-tune. |
| `training.use_class_weight` | `true`. Inverse-frequency cube-root weights, capped at `class_weight_max`. |
| `training.full_training_epochs` | `12` is enough on this dataset; warm start does most of the work. |
| `active_class_indices` | `[0, 1, 2, 3, 4]`. Tower (index 5) is frozen by exclusion from the loss. |

## Otter Creek baseline numbers (2026-04-29)

LAS-level IoU on 22,780,525 thinned-0.1 m points:

| Class | Baseline | Fine-tune | Delta |
| --- | ---: | ---: | ---: |
| Ground | 0.860 | 0.930 | +0.070 |
| High Vegetation | 0.788 | 0.907 | +0.119 |
| Building | 0.669 | 0.842 | +0.173 |
| Wire | 0.853 | 0.892 | +0.040 |
| Transmission Tower | 0.000 | 0.000 | 0 (no GT) |
| Utility Pole | 0.544 | 0.775 | +0.231 |

Mean IoU (5 supported classes): **0.743 -> 0.869 (+0.127)** with 12 epochs of
fine-tuning (~50 min on an RTX 4090).

Fine-tuned model:
[experiments/finetune_20260429_125114/model/PointCONV_model_6class_Mobile_v0.0.10](../../../experiments/finetune_20260429_125114/model/PointCONV_model_6class_Mobile_v0.0.10).

## Running inference with the fine-tuned model

The fine-tuned model uses the same Docker image and the same
`tf1/classification.py` entrypoint as the warm-start model. The only
difference is the `model_directory` field in the input YAML.

### Launchers

| Launcher | Use when |
| --- | --- |
| [tf1/Docker_Run_Classification_Finetune.bat](tf1/Docker_Run_Classification_Finetune.bat) | Single-folder LAS classification on a single machine, mirrors `Docker_Run_Classification.bat`. |
| [run_tf1_tiled_finetune.ps1](run_tf1_tiled_finetune.ps1) | Large LAS files at 0.1 m via overlapping tiles + merge, mirrors `run_tf1_tiled_ottercreek.ps1`. |

Both launchers point at [tf1/inputconfig_finetune.yml](tf1/inputconfig_finetune.yml),
which sets `model_directory: PointCONV_model_6class_Mobile_v0.0.10`.

### Auto-download from S3

`tf1/classification.py` calls
[PointCONV/PointCONV.py](tf1/PointCONV/PointCONV.py), which checks for the
local model directory and, if absent, downloads it from
`s3://sdai-model/lidar_ml/<model_directory>` using the AWS credentials
mounted at `/root/.aws` (or the host's `~/.aws` via the launcher's mount).

For the fine-tuned model the S3 path is:

```
s3://sdai-model/lidar_ml/PointCONV_model_6class_Mobile_v0.0.10
```

### One-time S3 publication

After fine-tuning on a new corpus, push the model directory to S3 once so
other machines can auto-fetch it. Use
[tools/upload_finetune_model_to_s3.py](tools/upload_finetune_model_to_s3.py):

```powershell
python tools\upload_finetune_model_to_s3.py --dry-run
python tools\upload_finetune_model_to_s3.py
```

It uploads every file under
`experiments/finetune_20260429_125114/model/PointCONV_model_6class_Mobile_v0.0.10/`
to `s3://sdai-model/lidar_ml/PointCONV_model_6class_Mobile_v0.0.10/`,
skipping keys that already exist (override with `--overwrite`).

If you fine-tune again under a different name, pass `--local-dir` and the
helper will derive the target prefix from the directory's basename
(`lidar_ml/<basename>`), or override with `--prefix`.

## Known limitations

- **Tower freeze is soft.** The class-5 fc2 weights drifted by 0.017 (max abs)
  on the Otter Creek run. Tower predictions still happen at inference time
  using the slightly perturbed weights.
- **Two-file dataset.** The model is now specialized to this corridor. If you
  retrain on additional labeled tiles, expect more general performance.
- **No augmentation.** `USE_RANDOM_ROTATE` and `USE_RANDOM_JITTER` are off,
  matching the warm-start training. Turning them on may help in low-data
  regimes but should be paired with a larger train set.
