# Current Code State And Handoff

Date: `2026-04-30`

This note is the current handoff summary for this PointCONV TF1 workflow snapshot.

**Start here:** [HOW_TO_RUN.md](HOW_TO_RUN.md) is the single-page runbook. [FINETUNE_WORKFLOW.md](FINETUNE_WORKFLOW.md) covers the trainer in depth.

The workflow lives at `E:\Point_Cloud_Classification\claude\PointCONV_TF1_Workflow` on the working machine and is a self-contained subset focused on TF1 inference, the new TF1 fine-tune workflow, and the bundled `PointCONV_model_6class_v0.0.10` warm-start checkpoint. Some changelog entries below describe work done in a broader development workspace and link to files that are not in this snapshot.

## Current Status

### Ready

- TF2 binary training and inference for packaged PointCONV models
- layered inference for:
  - ground
  - vegetation
  - poles
  - wires
- packaged reference models and their runbooks
- Docker-based execution path for Windows host + GPU workflow
- operational documentation for:
  - layered inference
  - scaling
  - production checklist
  - troubleshooting
  - output interpretation
  - benchmark profiles
- TF1 tiled inference for large LAS files at `0.1 m`, including preprocessing, Docker inference, merge-back, smoke testing, and a reusable backup helper
- TF1 6-class fine-tune workflow (warm-started from `PointCONV_model_6class_v0.0.10`), see [FINETUNE_WORKFLOW.md](FINETUNE_WORKFLOW.md). First validated run on Otter Creek lifted mean IoU from `0.743` -> `0.869` (5 supported classes, 22.7M thinned points).
- Multi-pass / left-right inference workflow ([run_tf1_tiled_left_right.ps1](run_tf1_tiled_left_right.ps1) + [tools/combine_thinned_las.py](tools/combine_thinned_las.py)): combines N raw LAS files into one voxelized union, runs inference once on the unified cloud. Validated on Oakville left+right (~520 M raw -> 61 M unique 0.1 m voxels). See section 1c of [HOW_TO_RUN.md](HOW_TO_RUN.md).

### Experimental

- `ground -> thin ground to 0.3 m -> wire` acceleration workflow
- some layered-inference optimization ideas for very large corridor files
- Pointcept planning and scaffold documentation

### Not Yet Fully Operational

- new TF2 multiclass 6-class training workflow as a validated end-to-end production path
- zero-shot use of external frameworks as a replacement for local PointCONV models

## Workspace Roles

Use [PointCloud_Classification]() for:

- cleaner handoff
- routine training and inference
- packaged reference models
- mirrored benchmark documentation

Use [PointCONV]() for:

- source training datasets
- active development
- historical model-development artifacts
- benchmark scaffolding and experiment history

## Recommended Models

Current packaged binary reference models in this workspace:

- wire:
  [PointCONV_Wire_Amaret_v0.2.0](MODEL_VERSIONS/wire_model_versions/PointCONV_Wire_Amaret_v0.2.0)
- vegetation:
  [PointCONV_Vegetation_v0.1.0](MODEL_VERSIONS/vegetation_model_versions/PointCONV_Vegetation_v0.1.0)
- pole:
  [PointCONV_Pole_Amaret_v0.1.1](MODEL_VERSIONS/pole_model_versions/PointCONV_Pole_Amaret_v0.1.1)

Retained comparison package:

- pole candidate:
  [PointCONV_Pole_Amaret_v0.1.1_candidate](MODEL_VERSIONS/pole_model_versions/PointCONV_Pole_Amaret_v0.1.1_candidate)

Shared base model:

- [PointCONV_model_6class_v0.0.10](Model_Development/safe_smoketest/model/PointCONV_model_6class_v0.0.10)

## Layered Inference State

Current layered order:

1. ground
2. vegetation
3. pole
4. wire

Current merged precedence:

1. wire
2. pole
3. vegetation
4. ground
5. unclassified

Current default model-defined thinning:

- wire: `0.2 m`
- pole: `0.2 m`
- vegetation: `0.3 m`

Ground preprocessing:

- deterministic ground segmentation
- optional XY pre-voxel step at `0.3 m`

Core docs:

- [RUN_LAYERED_INFERENCE.md](RUN_LAYERED_INFERENCE.md)
- [LAYERED_INFERENCE_SCALING.md](LAYERED_INFERENCE_SCALING.md)
- [LAYERED_INFERENCE_PRODUCTION_CHECKLIST.md](LAYERED_INFERENCE_PRODUCTION_CHECKLIST.md)
- [LAYERED_INFERENCE_TROUBLESHOOTING.md](LAYERED_INFERENCE_TROUBLESHOOTING.md)

## Ground-Thin-Wire Experiment State

This optimization is documented here:

- [GROUND_THIN_WIRE_WORKFLOW.md](GROUND_THIN_WIRE_WORKFLOW.md)

Current conclusion:

- it produced a meaningful wire-runtime reduction on the tested span files
- it is promising enough to keep
- it should still be treated as experimental until its outputs are remapped back to the original full-density cloud for a fair quality comparison

Do not silently make it the default layered wire path yet.

## Training Data Locations

This workspace contains the source datasets used for the current packaged binary models.

- wire voxelized training data:
  [wire_training_data_Amaret_2026_03_23_voxel_0p2](Data/Wire_Training_Data/wire_training_data_Amaret_2026_03_23_voxel_0p2)
- wire source dataset:
  [wire_training_data_Amaret_2026_03_23](Data/Wire_Training_Data/wire_training_data_Amaret_2026_03_23)
- pole voxelized training data:
  [Amaret_pole_vs_the_rest_edited_voxel_0p2](Data/pole_infrastructure/Amaret_pole_vs_the_rest_edited_voxel_0p2)
- pole source dataset:
  [Amaret_pole_vs_the_rest_edited](Amaret_pole_vs_the_rest_edited)
- vegetation prepared training data:
  [prepared_data](Data/active_learning_rounds/training_data_2026_04_01A/round0_seed/prepared_data)
- vegetation source labeled LAS pool:
  [training_data_2026_04_01A](Data/training_data_2026_04_01A)

## Multiclass Status

Multiclass work is documented and scaffolded, but not yet fully validated as a TF2 production path.

Planning docs:

- [MULTICLASS_6CLASS_PLAN.md](MULTICLASS_6CLASS_PLAN.md)
- [MULTICLASS_6CLASS_RUNBOOK.md](MULTICLASS_6CLASS_RUNBOOK.md)

Current practical interpretation:

- enough planning exists to start the work
- a new owner should still expect implementation and validation work on the TF2 multiclass trainer/evaluator path

## External Framework Status

This workspace contains the benchmark planning/docs and the broader development history for external framework exploration.

Primary references:

- [Model_Development/external_benchmarks/EXTERNAL_FRAMEWORK_EVALUATIONS.md](Model_Development/external_benchmarks/EXTERNAL_FRAMEWORK_EVALUATIONS.md)
- [Model_Development/external_benchmarks/POINTCEPT_OUTDOOR_MULTICLASS_PLAN.md](Model_Development/external_benchmarks/POINTCEPT_OUTDOOR_MULTICLASS_PLAN.md)

Bottom line:

- external zero-shot models did not beat the local PointCONV binaries
- Pointcept remains interesting mainly as a trainable framework

## Environment And Execution Assumptions

The current operational path assumes:

- Windows host
- Docker available locally
- NVIDIA GPU available through Docker
- the `mmworkflow:v1.8.0.1` image for the standard inference path

Useful launchers:

- [Docker_Run_Classification.bat](Docker_Run_Classification.bat)
- [run_pole_inference.ps1](run_pole_inference.ps1)
- [run_tf1_tiled_ottercreek.ps1](run_tf1_tiled_ottercreek.ps1)

## TF1 Fine-tune State

The TF1 fine-tune workflow is operational for re-targeting the 6-class model on a labeled DTECH-style corpus. Core docs and helpers:

- [FINETUNE_WORKFLOW.md](FINETUNE_WORKFLOW.md)
- [finetune/prepare_finetune_data.py](finetune/prepare_finetune_data.py)
- [finetune/train_finetune.py](finetune/train_finetune.py)
- [finetune/run_post_finetune_eval.py](finetune/run_post_finetune_eval.py)
- [finetune/finetune_config.yml](finetune/finetune_config.yml)
- [finetune/dtech_to_model_mapping.yml](finetune/dtech_to_model_mapping.yml)

Validated Otter Creek fine-tune (2026-04-29):

- training data: 2,662 16K-point regions sampled from the two `OtterCreek_*_thin_0p1m.las` files; deterministic 70/15/15 spatial split (`train=1810`, `val=523`, `test=329`)
- training: 12 epochs, batch 6, learning rate `1e-4`, IoU-weighted loss over 5 active classes; transmission-tower head excluded from the loss (residual softmax-coupled drift on tower fc2 weights ~ 0.017)
- wall time: ~50 min on RTX 4090
- val mean IoU (best epoch): `0.865`
- test mean IoU (16K-point regions): `0.850`
- LAS-level IoU on the full 22,780,525 thinned-0.1m points: baseline `0.743` -> fine-tune `0.869` (mean over 5 supported classes)
- per-class deltas: utility pole `+0.231`, building `+0.173`, high vegetation `+0.119`, ground `+0.070`, wire `+0.040`, transmission tower n/a (no GT in this dataset)
- fine-tuned model (canonical): [models/PointCONV_model_6class_Mobile_v0.0.10](models/PointCONV_model_6class_Mobile_v0.0.10) (full layout: `Best_Model/`, `model.ckpt.*`, `exp_def.p` with `scale_type=5`, plus [`IoU_estimates.txt`](models/PointCONV_model_6class_Mobile_v0.0.10/IoU_estimates.txt)). Also published to `s3://sdai-model/lidar_ml/PointCONV_model_6class_Mobile_v0.0.10/` for auto-download.
- comparison artifacts: [experiments/finetune_20260429_125114/post_finetune_eval/comparison.json](../../experiments/finetune_20260429_125114/post_finetune_eval/comparison.json), [experiments/finetune_20260429_125114/post_finetune_eval/iou/](../../experiments/finetune_20260429_125114/post_finetune_eval/iou)

## TF1 Tiled Inference State

The TF1 tiled workflow is now operational for large LAS files that need a 0.1 m thinned output.

Core docs and helpers:

- [TF1_TILED_INFERENCE_WORKFLOW.md](TF1_TILED_INFERENCE_WORKFLOW.md)
- [TF1_WORKFLOW_BACKUP.md](TF1_WORKFLOW_BACKUP.md)
- [run_tf1_tiled_ottercreek.ps1](run_tf1_tiled_ottercreek.ps1)
- [build_tf1_inference_tiles.py](pre_processing/build_tf1_inference_tiles.py)
- [merge_tf1_tile_predictions.py](post_processing/merge_tf1_tile_predictions.py)
- [smoke_tf1_tiled_workflow.py](tools/smoke_tf1_tiled_workflow.py)
- [backup_tf1_workflow.ps1](tools/backup_tf1_workflow.ps1)

Validated DTECH/Otter Creek run:

- run folder:
  [tf1_pointconv_0p1m_tiled_20260429_1510](/E:/Image_PointCloud_Segmentation/DTECH_Otter_Creek/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_20260429_1510)
- final outputs:
  [combined_outputs](/E:/Image_PointCloud_Segmentation/DTECH_Otter_Creek/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_20260429_1510/combined_outputs)
- source files: `2`
- source points: `109,127,470`
- thinned points: `22,780,525`
- tiles: `58`
- points with predictions: `22,680,592`
- total wall time: about `25.1 min`
- TF1 classification time: `22:44`

## Version Control State

This workspace has been prepared for version control:

- local git repository initialized
- `.gitignore` present
- `.gitattributes` present
- Git LFS patterns configured

Important current state:

- a first commit has not yet been created
- `git status` still shows the repo as fully untracked because the initial add/commit has not happened yet

Version-control notes:

- [VERSION_CONTROL.md](VERSION_CONTROL.md)

## Recommended Handoff Steps

For the next owner:

1. Use [PointCloud_Classification]() for the clean operational handoff.
2. Read [CURRENT_CODE_STATE_AND_HANDOFF.md](CURRENT_CODE_STATE_AND_HANDOFF.md) there first.
3. Use this workspace only when you need the source datasets or full experiment history.
4. Use the stable packaged binary models first.
5. Treat [GROUND_THIN_WIRE_WORKFLOW.md](GROUND_THIN_WIRE_WORKFLOW.md) as experimental, not default.
6. If multiclass work is the next priority, begin from [MULTICLASS_6CLASS_PLAN.md](MULTICLASS_6CLASS_PLAN.md).
7. Before the first commit, review [VERSION_CONTROL.md](VERSION_CONTROL.md).

## Immediate Open Items

- integrate full-density remapping into the ground-thin-wire experiment so output quality can be compared fairly with the baseline
- decide whether the ground-thin-wire optimization should become an optional layered-inference mode
- finish and validate the TF2 multiclass trainer/evaluator path
- decide whether packaged models should remain in-repo before the first real git commit
