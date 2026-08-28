# TF1 PointCONV Classification Runbook

This document explains how to run the legacy TensorFlow 1 PointCONV classification path in [classification.py](tf1/classification.py).

The TF1 path is kept for legacy multiclass inference and comparison work. For current binary production-style workflows, prefer the TF2 layered inference stack documented in [RUN_LAYERED_INFERENCE.md](RUN_LAYERED_INFERENCE.md).

For large source LAS files, use the tiled workflow in [TF1_TILED_INFERENCE_WORKFLOW.md](TF1_TILED_INFERENCE_WORKFLOW.md). That workflow builds overlapping 0.1 m tiles first, runs this TF1 entrypoint on the tile folder, and then merges tile predictions back to the original source-file granularity.

## What The TF1 Pipeline Does

[classification.py](tf1/classification.py) runs three stages:

1. Read an input YAML config.
2. Preprocess each input LAS file with [preprocessing.py](tf1/preprocessing/preprocessing.py).
3. Run TF1 PointCONV segmentation and map predictions back to output LAS files through [PointCONV.py](tf1/PointCONV/PointCONV.py).

The preprocessing stage voxelizes input LAS files in parallel using `preprocessing.num_threads_file_voxelization`. The PointCONV stage then samples regions, runs the TF1 checkpoint, writes voxel-level predictions, and optionally maps predictions back to the thinned/original point set.

## Main Entrypoint

Run from the `tf1` folder:

```powershell
python classification.py `
  --input_inputconfig tf1\inputconfig.yml `
  --input_folder G:\path\to\las_folder `
  --out_folder G:\path\to\output_folder `
  --model_folder G:\path\to\model_root
```

All four arguments are required.

| Argument | Purpose |
| --- | --- |
| `--input_inputconfig` | YAML config containing `preprocessing:` and `PointConv:` sections. |
| `--input_folder` | Folder containing input `*.las` files. This path is not recursive. |
| `--out_folder` | Output folder for logs, preprocessing cache, voxel files, predictions, and mapped outputs. |
| `--model_folder` | Folder containing the model directory named by `PointConv.model_directory`. |

## Docker Example

The preferred runtime is the same Docker image used by the rest of this project:

```powershell
docker run --rm --pull=never --gpus all --shm-size=8gb `
  -v "E:\Point_Cloud_Classification\claude\PointCONV_TF1_Workflow:/workspace" `
  -w /workspace/tf1 `
  750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1 `
  python classification.py `
    --input_inputconfig /workspace/tf1/inputconfig.yml `
    --input_folder /workspace/Data/<input_las_folder> `
    --out_folder /workspace/Model_Development/tf1_classification_runs/<run_name> `
    --model_folder /workspace/Model_Development/safe_smoketest/model
```

## Docker Batch Wrapper

The legacy batch wrapper is [Docker_Run_Classification.bat](tf1/Docker_Run_Classification.bat).

Before using it, review and edit these variables:

| Variable | Meaning |
| --- | --- |
| `DATA_ROOT` | Host root folder mounted into the container. |
| `DATA_LAS` | LAS input folder relative to `DATA_ROOT`. |
| `CLASS_DIR` | Output folder relative to `DATA_ROOT`. |
| `MODEL_DIR` | Model root relative to `DATA_ROOT`. |
| `CLASSIFICATION` | Host folder containing this TF1 code, mounted as `/app/Classification`. |
| `INPUT_CONFIG` | Container path to the YAML config. |
| `CONTAINER` | Docker image name. |

Operational note:

- the current batch file stops and re-enables several Windows Update services around the Docker run
- review that behavior before using the batch file on a shared or managed workstation

## Config File

The default config is [inputconfig.yml](tf1/inputconfig.yml).

It has two required sections.

### `preprocessing`

Important fields:

| Field | Meaning |
| --- | --- |
| `thin_data` | Optional thinning before voxelization. Leave blank for no extra thinning. |
| `voxel_size` | Voxel size used to prepare the LAS for PointCONV. Current default is `0.1`. |
| `num_threads_file_voxelization` | Number of files to voxelize in parallel. |
| `convert_feet_to_meters` | Converts coordinates from feet to meters before processing when `true`. |
| `OutputLAS_FileFormat` | LAS file version for outputs. |
| `OutputLAS_PointFormat` | LAS point format for outputs. |
| `min_num_pts_voxel` | Minimum point count used by the voxelization/preparation path. |

### `PointConv`

Important fields:

| Field | Meaning |
| --- | --- |
| `model_directory` | Model folder name inside `--model_folder`. |
| `num_threads_PointCONV_sample` | Parallelism used by the sample/region builder. |
| `voxel_size_baseline` | Radius baseline used when mapping predictions back to points. |
| `map_to_original_points` | If `true`, maps voxel predictions back to the thinned/original points. |
| `GPU_INDEX` | `-1` selects the least-used GPU; otherwise set a specific GPU index. |
| `BATCH_SIZE` | TF1 inference batch size. |
| `random_seed_sample_points` | Seed used for deterministic sampling. |
| `num_classes` | Number of model output classes. |
| `noise_labels` | Classes excluded from prediction sampling. |
| `class_mapping_model` | Mapping between model output indices and LAS classification numbers. |

The current default `class_mapping_model` maps the 6-class base model to:

| Model index | LAS class | Label |
| --- | --- | --- |
| `0` | `14` | `Wire - General` |
| `1` | `18` | `Pole - electrical / utility` |
| `2` | `5` | `High Vegetation` |
| `3` | `2` | `Ground` |
| `4` | `6` | `Building` |
| `5` | `15` | `Pole - Transmission Tower` |

See [CLASS_CODE_REFERENCE_DTECH_2025.md](CLASS_CODE_REFERENCE_DTECH_2025.md) for the current class-code reference.

## Model Folder Layout

`--model_folder` should contain the model directory named by `PointConv.model_directory`.

For the default config:

```text
<model_folder>/
  PointCONV_model_6class_v0.0.10/
    exp_def.p
    Best_Model/
      model.ckpt*
```

The project copy includes the shared base model here:

- [PointCONV_model_6class_v0.0.10](Model_Development/safe_smoketest/model/PointCONV_model_6class_v0.0.10)

If the model directory is missing, the TF1 wrapper attempts to download it from S3 under `s3://sdai-model/lidar_ml/<model_directory>`.

## Outputs

The output folder will contain:

| Output | Description |
| --- | --- |
| `logs/` | Timestamped run log from `classification.py`. |
| `pre_pro_files.pklz` | Cached preprocessing manifest. Reused on later runs. |
| per-file preprocessing folders/files | Voxelized and thinned LAS artifacts generated by preprocessing. |
| `*_prob.npz` | Voxel-level class probability arrays. |
| `*_seg_out.las` | Voxel-level segmentation output. |
| `*_raw.las` | Prediction output mapped back to the thinned/original point set. |
| `*_prob_mapping.npz` | Mapping metadata and per-point probabilities for the mapped output. |

Caching note:

- if `pre_pro_files.pklz` exists, preprocessing is skipped and the cached file list is reused
- delete `pre_pro_files.pklz` and stale per-file outputs when changing input data, voxel size, or preprocessing options

## Performance Notes

- File-level voxelization is parallelized by `preprocessing.num_threads_file_voxelization`.
- Sample generation uses `PointConv.num_threads_PointCONV_sample`.
- PointCONV inference itself runs file by file.
- `BATCH_SIZE` controls TensorFlow inference batch size and may need to be lowered if GPU memory is tight.
- `--input_folder` only loads `*.las`; convert `.laz` files before using this TF1 path.

## Troubleshooting

### No LAS files found

Confirm `--input_folder` contains `.las` files directly. The TF1 preprocessing entrypoint does not search nested folders.

### Missing `PointConv` section

The YAML must include both `preprocessing:` and `PointConv:`. The shorter TF2 configs are not sufficient for this TF1 path.

### Model checkpoint not found

Confirm `--model_folder` contains the directory named by `PointConv.model_directory`, including `exp_def.p` and `Best_Model/model.ckpt*`.

### Output appears stale

Remove `pre_pro_files.pklz` and the corresponding per-file output folders before rerunning with changed preprocessing or model settings.

### GPU selection is surprising

Set `PointConv.GPU_INDEX` to a specific GPU index instead of `-1` if the least-used-GPU heuristic picks the wrong device.
