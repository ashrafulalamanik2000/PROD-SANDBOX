# TF1 Tiled Inference Workflow

This workflow prepares large LAS files for the legacy TF1 PointCONV model by building overlapping, 0.1 m thinned tiles, running TF1 inference on those tiles, and merging the predictions back to one 0.1 m thinned LAS per original source file.

The examples use two placeholders — substitute paths for your machine
(`<WORKFLOW>` = this bundled workflow folder, `<DATA>` = your data root):

| Purpose | Path |
| --- | --- |
| Source LAS files | `<DATA>\DTECH_2025\ClassifiedLAS` |
| Experiment outputs | `<DATA>\DTECH_2025_experiments` |
| Preprocessing code | [pre_processing](pre_processing) |
| Post-processing code | [post_processing](post_processing) |
| TF1 inference code | [tf1](tf1) |

## Pipeline

1. Thin each source LAS to one point per `0.1 m` voxel.
2. Split the thinned cloud into continuous overlapping tiles.
3. Keep about `400,000` core points per tile before overlap.
4. Guarantee each tile has at least a `20 m` radius support area, implemented as an XY tile envelope at least `40 m` wide/tall.
5. Increase overlap when a sparse tile has fewer than `25,000` points.
6. Run [classification.py](tf1/classification.py) on the tile folder.
7. Merge tile predictions back to one output LAS per original source file, using only each tile's core-owned points so overlap does not double-classify boundaries.

## One-Command Runner

Use [run_tf1_tiled_ottercreek.ps1](run_tf1_tiled_ottercreek.ps1) from PowerShell:

```powershell
run_tf1_tiled_ottercreek.ps1
```

This creates a timestamped run folder under:

```text
<DATA>\DTECH_2025_experiments
```

The runner uses Docker for all three stages so the host Python environment does not need `laspy`, `scipy`, or TensorFlow installed.

## Smoke Test

Before running large files on a new workstation, validate the Docker image and laspy compatibility with:

```powershell
docker run --rm --pull=never `
  -v "<WORKFLOW>:/workspace" `
  -w /workspace `
  750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1 `
  python /workspace/tools/smoke_tf1_tiled_workflow.py
```

This creates a synthetic LAS under `_tmp_test_workspace/tf1_tiled_smoke`, runs the tiler, writes fake TF1 raw outputs, and verifies that the merge step writes a combined LAS with predictions.

Recommended first production-style run:

```powershell
run_tf1_tiled_ottercreek.ps1 `
  -RunName tf1_pointconv_0p1m_tiled_initial `
  -PreprocessWorkers 2 `
  -PostprocessWorkers 2 `
  -Overwrite
```

Useful resume options:

```powershell
# Re-run TF1 inference and merge using existing tiles.
run_tf1_tiled_ottercreek.ps1 `
  -RunName tf1_pointconv_0p1m_tiled_initial `
  -SkipPreprocess `
  -Overwrite

# Re-run only the merge step after TF1 outputs already exist.
run_tf1_tiled_ottercreek.ps1 `
  -RunName tf1_pointconv_0p1m_tiled_initial `
  -SkipPreprocess `
  -SkipInference `
  -Overwrite
```

## Manual Commands

Preprocess:

```powershell
docker run --rm --pull=never `
  -v "<WORKFLOW>:/workspace" `
  -v "<DATA>:/data" `
  -w /workspace `
  750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1 `
  python /workspace/pre_processing/build_tf1_inference_tiles.py `
    --input-dir /data/DTECH_2025/ClassifiedLAS `
    --output-root /data/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_initial `
    --voxel-size 0.1 `
    --target-tile-points 400000 `
    --min-tile-points 25000 `
    --min-radius 20 `
    --overlap 20 `
    --workers 2 `
    --overwrite
```

Run TF1 inference:

```powershell
docker run --rm --pull=never --gpus all --shm-size=8gb `
  -v "<WORKFLOW>:/workspace" `
  -v "<DATA>:/data" `
  -w /workspace/tf1 `
  750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1 `
  python classification.py `
    --input_inputconfig /workspace/tf1/inputconfig.yml `
    --input_folder /data/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_initial/preprocessed_tiles `
    --out_folder /data/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_initial/tf1_outputs `
    --model_folder /workspace/Model_Development/safe_smoketest/model
```

Merge tile predictions:

```powershell
docker run --rm --pull=never `
  -v "<WORKFLOW>:/workspace" `
  -v "<DATA>:/data" `
  -w /workspace `
  750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1 `
  python /workspace/post_processing/merge_tf1_tile_predictions.py `
    --manifest /data/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_initial/manifests/tf1_tile_manifest.json `
    --tf1-output-root /data/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_initial/tf1_outputs `
    --output-dir /data/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_initial/combined_outputs `
    --workers 2 `
    --overwrite
```

## Output Layout

Each run folder contains:

| Folder/File | Description |
| --- | --- |
| `source_thinned/` | One 0.1 m thinned LAS per original source plus source-index sidecars. |
| `preprocessed_tiles/` | Overlapping tile LAS files passed directly to TF1. |
| `tile_indices/` | NumPy sidecars used to map tile points back to each thinned source LAS. |
| `manifests/tf1_tile_manifest.json` | Full preprocessing manifest used by the merge step. |
| `manifests/tf1_tile_manifest_tiles.csv` | Tile-level counts, extents, overlap, and source mapping. |
| `tf1_outputs/` | Legacy TF1 output folders, `*_prob.npz`, `*_seg_out.las`, and `*_raw.las`. |
| `combined_outputs/` | Final per-source merged LAS outputs and merge summaries. |

Final LAS files are written as:

```text
combined_outputs\<source_stem>_tf1_pointconv_combined_0p1m.las
```

The output LAS classification field contains the merged PointCONV prediction. Extra dimensions preserve:

| Extra dimension | Meaning |
| --- | --- |
| `source_class` | Original source classification after 0.1 m thinning. |
| `pointconv_prob` | Highest accepted PointCONV class probability for the point. |
| `pointconv_votes` | Number of accepted core tile predictions for the point. |

## Operational Notes

- The current [inputconfig.yml](tf1/inputconfig.yml) uses `preprocessing.voxel_size: 0.1` and `PointConv.voxel_size_baseline: 0.1`.
- TF1 only reads `*.las` files directly in the input folder. It does not recurse and does not process `.laz`.
- The source data should already be in meters. If data is in feet, convert to meters before this tiling workflow or use a separate conversion stage.
- The recommended starting worker count is `2` because the current input folder has two large LAS files and the workstation has 128 GB RAM.
- Increase preprocessing workers only after checking RAM pressure; each worker reads a full source file.
- If TF1 inference runs out of GPU memory, lower `PointConv.BATCH_SIZE` in [inputconfig.yml](tf1/inputconfig.yml).
