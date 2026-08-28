# Changelog

This changelog is a curated project history for the current workspace.

Notes:
- A local git repository is initialized for this workspace, but there is not yet a committed project history, so this file remains a curated change log rather than an exported commit log.
- Dates below reflect the implementation timeline in this workspace.
- Future updates should use the helper script at [append_changelog_entry.py](/G:/codex/PointCONV/tools/append_changelog_entry.py) for lightweight structured maintenance.


> Note: many entries below reference paths under `G:\codex\PointCONV` or `G:\codex\PointCloud_Classification`. Those are the broader development workspaces this TF1 workflow was carved out from. Files outside this PointCONV_TF1_Workflow snapshot are not present here, so the linked paths in older entries describe historical context, not local files.

## Changelog Workflow

Use the helper script to append a single dated entry:

```powershell
python G:\codex\PointCONV\tools\append_changelog_entry.py --section Changed --message "Describe the update"
```

Examples:

```powershell
python G:\codex\PointCONV\tools\append_changelog_entry.py --section Added --message "Added a new evaluation script"
python G:\codex\PointCONV\tools\append_changelog_entry.py --section Fixed --message "Fixed sparse window padding in TF2 sampling"
python G:\codex\PointCONV\tools\append_changelog_entry.py --date 2026-04-02 --section Notes --message "Round 1 acquisition policy tuned for Calgary tiles"
```

## 2026-04-27

### Changed
- Added [CURRENT_CODE_STATE_AND_HANDOFF.md](/G:/codex/PointCONV/CURRENT_CODE_STATE_AND_HANDOFF.md) to document the current ready / experimental / planned code status and the recommended handoff path.
- Added [GROUND_THIN_WIRE_WORKFLOW.md](/G:/codex/PointCONV/GROUND_THIN_WIRE_WORKFLOW.md) to document the current `ground -> thin ground to 0.3 m -> wire` experiment workflow and its caveats.
- Aligned the main layered-inference operational docs to the stable pole package [PointCONV_Pole_Amaret_v0.1.1](/G:/codex/PointCONV/MODEL_VERSIONS/pole_model_versions/PointCONV_Pole_Amaret_v0.1.1).

## 2026-04-30

### Added
- Added [tools/combine_thinned_las.py](tools/combine_thinned_las.py) for combining N LAS files into one and voxelizing the union at a target voxel size. Auto-detects mode: raw / pre-classified inputs (tie-break on `intensity`) vs PointCONV combined-output inputs carrying `source_class`/`pointconv_prob`/`pointconv_votes` (tie-break on `pointconv_prob`, extra dims propagate).
- Added [run_tf1_tiled_left_right.ps1](run_tf1_tiled_left_right.ps1), a PowerShell launcher for the multi-pass / left-right workflow: combines and thins all `*.las` files in an input dir to 0.1 m, builds tiles, runs inference once on the unified cloud against `PointCONV_model_6class_Mobile_v0.0.10`, and merges back. Recommended over running inference twice and combining outputs after.
- Documented the new workflow as section 1c "Multi-pass / left-right inputs of the same scene" in [HOW_TO_RUN.md](HOW_TO_RUN.md).

### Changed
- Renamed and packaged the fine-tuned model as `PointCONV_model_6class_Mobile_v0.0.10` and copied it to [models/PointCONV_model_6class_Mobile_v0.0.10](models/PointCONV_model_6class_Mobile_v0.0.10) (drop-in alongside the warm-start `PointCONV_model_6class_v0.0.10`). The directory is self-contained: relative `checkpoint` metadata, `Best_Model/`, `exp_def.p`, and a new [IoU_estimates.txt](models/PointCONV_model_6class_Mobile_v0.0.10/IoU_estimates.txt) recording per-class IoU vs the warm-start baseline.
- Republished the fine-tuned model under the new name to `s3://sdai-model/lidar_ml/PointCONV_model_6class_Mobile_v0.0.10/` so the auto-download path picks up the canonical name from any clean machine.
- Updated [tf1/inputconfig_finetune.yml](tf1/inputconfig_finetune.yml), [tf1/Docker_Run_Classification_Finetune.bat](tf1/Docker_Run_Classification_Finetune.bat), [run_tf1_tiled_finetune.ps1](run_tf1_tiled_finetune.ps1), [tools/upload_finetune_model_to_s3.py](tools/upload_finetune_model_to_s3.py), [finetune/finetune_config.yml](finetune/finetune_config.yml), and [finetune/run_post_finetune_eval.py](finetune/run_post_finetune_eval.py) to reference the new name.
- Ran inference on the 15 LAS tiles under `E:\Image_PointCloud_Segmentation\Mississauga\training_data_sidewalk_driveway` (3 subdirectories, 99.3 M thinned-0.1 m points) with `PointCONV_model_6class_Mobile_v0.0.10`. Outputs at `experiments/inference_mississauga_20260429_155803/combined_outputs/` as both `.las` and `.laz`.

## 2026-04-29

### Added
- Added the TF1 fine-tune workflow: [finetune/prepare_finetune_data.py](finetune/prepare_finetune_data.py) (data prep using the same 16,384-point sampler as inference), [finetune/train_finetune.py](finetune/train_finetune.py) (TF1 trainer with warm-start restore from `PointCONV_model_6class_v0.0.10`, 5-class IoU-weighted loss that excludes the transmission-tower head), [finetune/run_post_finetune_eval.py](finetune/run_post_finetune_eval.py) (orchestrates inference + merge + IoU comparison vs baseline), and [FINETUNE_WORKFLOW.md](FINETUNE_WORKFLOW.md) as the operational doc.
- Added [finetune/dtech_to_model_mapping.yml](finetune/dtech_to_model_mapping.yml) and [finetune/finetune_config.yml](finetune/finetune_config.yml) as single sources of truth for class mapping and training hyperparameters.
- Added [tools/compute_pointconv_iou.py](tools/compute_pointconv_iou.py) for per-class IoU on merged combined-output LAS files using a configurable DTECH-source-to-PointCONV-class mapping.
- Produced the first fine-tuned model on Otter Creek: [experiments/finetune_20260429_125114/model/PointCONV_model_6class_Mobile_v0.0.10](../../experiments/finetune_20260429_125114/model/PointCONV_model_6class_Mobile_v0.0.10). LAS-level mean IoU on 5 supported classes lifted from `0.743` (baseline) to `0.869` (fine-tune); the largest jumps were utility pole `+0.231`, building `+0.173`, and high vegetation `+0.119`.
- Added the fine-tune Docker launchers: [tf1/Docker_Run_Classification_Finetune.bat](tf1/Docker_Run_Classification_Finetune.bat) (single-folder LAS) and [run_tf1_tiled_finetune.ps1](run_tf1_tiled_finetune.ps1) (large-file 0.1 m tiled inference). Both consume [tf1/inputconfig_finetune.yml](tf1/inputconfig_finetune.yml), which extends `class_to_model` to cover the new pole/wire/ground/ignore mapping. The TF1 wrapper auto-downloads `s3://sdai-model/lidar_ml/PointCONV_model_6class_Mobile_v0.0.10` if the directory is missing.
- Added [tools/upload_finetune_model_to_s3.py](tools/upload_finetune_model_to_s3.py) to publish a fine-tuned model directory to `s3://sdai-model/lidar_ml/<basename>/` so the auto-download path can fetch it from any machine.

### Changed
- Added [CLASS_CODE_REFERENCE_DTECH_2025.md](/G:/codex/PointCONV/CLASS_CODE_REFERENCE_DTECH_2025.md) as the local reference for the DTECH 2025 LAS class-code workbook.
- Pre-populated the Pointcept utility6class class-map scaffold with a recommended source-to-target collapse based on the DTECH 2025 source labels.
- Updated multiclass, layered-inference, and packaged-model docs to show the standard label names behind the current key class ids such as `2`, `5`, `14`, `15`, and `18`.
- Replaced the TF1 placeholder classification notes with a full usage runbook at [tf1/classification.md](/G:/codex/PointCONV/tf1/classification.md) and added a TF1 landing page at [tf1/README.md](/G:/codex/PointCONV/tf1/README.md).
- Added the TF1 large-file tiled inference workflow: [build_tf1_inference_tiles.py](/G:/codex/PointCONV/pre_processing/build_tf1_inference_tiles.py), [merge_tf1_tile_predictions.py](/G:/codex/PointCONV/post_processing/merge_tf1_tile_predictions.py), [run_tf1_tiled_ottercreek.ps1](/G:/codex/PointCONV/run_tf1_tiled_ottercreek.ps1), and [TF1_TILED_INFERENCE_WORKFLOW.md](/G:/codex/PointCONV/TF1_TILED_INFERENCE_WORKFLOW.md).
- Added [smoke_tf1_tiled_workflow.py](/G:/codex/PointCONV/tools/smoke_tf1_tiled_workflow.py) to validate tiled preprocessing and merge-back inside the TF1 Docker image before large-file runs.
- Added [TF1_WORKFLOW_BACKUP.md](/G:/codex/PointCONV/TF1_WORKFLOW_BACKUP.md) and [backup_tf1_workflow.ps1](/G:/codex/PointCONV/tools/backup_tf1_workflow.ps1) for timestamped TF1 code/documentation backups with manifests and SHA-256 checksums.

## 2026-04-06

### Changed
- Packaged PointCONV_Wire_Amaret_v0.2.0 in MODEL_VERSIONS with the new 0.2-voxel Amaret wire run, held-out test metrics, split config, and default validation-tuned threshold.

## 2026-04-11

### Changed
- Packaged the round-2 Amaret pole follow-up as [PointCONV_Pole_Amaret_v0.1.1_candidate](/G:/codex/PointCONV_tf2_Models/PointCONV_Pole_Amaret_v0.1.1_candidate) and mirrored it into [MODEL_VERSIONS/pole_model_versions](/G:/codex/PointCONV/MODEL_VERSIONS/pole_model_versions/PointCONV_Pole_Amaret_v0.1.1_candidate).
- Documented `PointCONV_Pole_Amaret_v0.1.1_candidate` as the current internal pole-model lead while keeping [PointCONV_Pole_Amaret_v0.1.0](/G:/codex/PointCONV_tf2_Models/PointCONV_Pole_Amaret_v0.1.0) as the stable packaged baseline.

## 2026-04-12

### Changed
- Promoted the Amaret pole-infrastructure candidate to the stable packaged model [PointCONV_Pole_Amaret_v0.1.1](/G:/codex/PointCONV/MODEL_VERSIONS/pole_model_versions/PointCONV_Pole_Amaret_v0.1.1) while retaining the candidate package for traceability.
- Added the reusable pole-inference launchers [run_pole_inference.ps1](/G:/codex/PointCONV/run_pole_inference.ps1) and [infer_pole_model.py](/G:/codex/PointCONV/tf2/infer_pole_model.py).

## 2026-04-07

### Changed
- Updated [RUN_LAYERED_INFERENCE.md](/G:/codex/PointCONV/RUN_LAYERED_INFERENCE.md) to reflect the current recommended layered stack: deterministic ground, `PointCONV_Wire_Amaret_v0.2.0`, and `PointCONV_Vegetation_v0.1.0`.
- Documented the current model-defined thinning defaults for layered inference: wire at `0.2` and vegetation at `0.3`.

## 2026-04-04

### Changed
- Reorganized the workspace into Data and Model_Development, moved generated datasets and experiment outputs into the new top-level folders, and updated TF2 path defaults to match the new layout.

## 2026-04-03

### Added
- Trained the first class-14 wire PointConv baseline on the 0.20-voxel starter split and completed a separate held-out test evaluation.
- Added focal-loss support to TF2 binary training, compared class-14 wire experiments across 0.15 vs 0.20 voxel sizes and hybrid vs focal loss, and generated a threshold sweep for the best wire model.
- Added the Amaret wire-model workflow: created a spatial 0.15-voxel train/validation/test split, added binary-model warm-start support to tf2/train_binary_classifier.py, ran zero-shot transfer baselines, and trained the first warm-started Amaret wire model.
- Packaged the Amaret-specific wire model as PointCONV_Wire_Amaret_v0.1.0, added validation threshold sweep artifacts, and confirmed the legacy-init run outperformed the warm-start run on held-out Amaret test.
- Added target-domain planning and silver-label synthesis tooling for the La Verne and Ontario crops, including Combined-group split artifacts and train-split silver labels from PCseg outputs.
- Generated target-domain silver labels for train, validation, and test crops from La Verne and Ontario PCseg outputs, and created a prioritized gold review queue for the validation and test splits.
- Added a ground integration assessment for the La Verne and Ontario target workflow and generated a prioritized holdout review queue for validating the deterministic ground stage.
- Built a compact ground review pack for the highest-priority La Verne and Ontario validation and test crops, including crop LAS, matching PCseg LAS artifacts, a checklist, and a review-results template.

## 2026-04-02

### Added
- Added a root project changelog at [CHANGELOG.md](/G:/codex/PointCONV/CHANGELOG.md).
- Added active-learning round dataset preparation in [prepare_round_dataset.py](/G:/codex/PointCONV/tf2/active_learning/prepare_round_dataset.py).
- Added test-only model evaluation in [evaluate_binary_classifier.py](/G:/codex/PointCONV/tf2/evaluate_binary_classifier.py).
- Added a small split-config smoke fixture in [smoke_split_config.json](/G:/codex/PointCONV/tf2/smoke_split_config.json).
- Added the changelog helper script at [append_changelog_entry.py](/G:/codex/PointCONV/tools/append_changelog_entry.py) for lightweight structured updates.
- Added held-out threshold sweep analysis in [analyze_binary_thresholds.py](/G:/codex/PointCONV/tf2/analyze_binary_thresholds.py), including CSV, PNG plot, and cached point-level prediction outputs for binary classifier evaluation.
- Added standalone Docker launcher [run_binary_inference_model.py](/G:/codex/PointCONV/tools/run_binary_inference_model.py) to download named binary models from S3 and run TF2 binary inference by model name.
- Added model run instructions for [PointCONV_Vegetation_v0.1.0](/G:/codex/PointCONV/vegetation_model_versions/PointCONV_Vegetation_v0.1.0) in both markdown and docx formats.
- Added a reusable target-class prevalence audit for labeled LAS pools and generated class-14 audit artifacts for training_data_2026_04_01A.
- Ran a class-14 voxel-size bakeoff on the 4-file starter set and added a reusable voxel-bakeoff summarizer with comparison artifacts under wire_class14_bakeoff.

### Changed
- Updated [train_binary_classifier.py](/G:/codex/PointCONV/tf2/train_binary_classifier.py) to accept `--split-config` JSON input for train/validation/test file lists.
- Updated [train_binary_classifier.py](/G:/codex/PointCONV/tf2/train_binary_classifier.py) so `--skip-test-eval` keeps the held-out test set cold by not resolving or sampling test files during training.
- Updated [README.md](/G:/codex/PointCONV/tf2/active_learning/README.md) to document the cold-test workflow and round dataset preparation.
- Documented the changelog maintenance workflow and example commands in [CHANGELOG.md](/G:/codex/PointCONV/CHANGELOG.md).
- Set [PointCONV_Vegetation_v0.1.0](/G:/codex/PointCONV/vegetation_model_versions/PointCONV_Vegetation_v0.1.0) default inference threshold to 0.9 via model config and updated TF2 threshold resolution to honor model-level defaults.
- Generalized the TF2 binary training, evaluation, threshold-analysis, and inference scripts so trained models carry their target class metadata instead of relying on class-5 defaults.

### Active Learning
- Implemented executable active-learning dataset preparation:
  - [tile_las_units.py](/G:/codex/PointCONV/tf2/active_learning/tile_las_units.py)
  - [build_unit_manifest.py](/G:/codex/PointCONV/tf2/active_learning/build_unit_manifest.py)
  - [prepare_round_dataset.py](/G:/codex/PointCONV/tf2/active_learning/prepare_round_dataset.py)
- Ran pilot tiling for:
  - `StMarie_TILE_251_5160_UTM17_r1_mapped.las`
  - `5235565154883_mapped.las`
- Generated active-learning artifacts under [active_learning_artifacts/training_data_2026_04_01A](/G:/codex/PointCONV/active_learning_artifacts/training_data_2026_04_01A), including:
  - [dataset_manifest.json](/G:/codex/PointCONV/active_learning_artifacts/training_data_2026_04_01A/dataset_manifest.json)
  - [split_plan.json](/G:/codex/PointCONV/active_learning_artifacts/training_data_2026_04_01A/split_plan.json)
  - [tiling_plan.json](/G:/codex/PointCONV/active_learning_artifacts/training_data_2026_04_01A/tiling_plan.json)
  - [active_learning_bootstrap.json](/G:/codex/PointCONV/active_learning_artifacts/training_data_2026_04_01A/active_learning_bootstrap.json)
  - [active_learning_unit_manifest.json](/G:/codex/PointCONV/active_learning_artifacts/training_data_2026_04_01A/active_learning_unit_manifest.json)
- Materialized the round-0 prepared dataset under [round0_seed](/G:/codex/PointCONV/active_learning_rounds/training_data_2026_04_01A/round0_seed).

### Training Runs
- Completed the first active-learning seed-round training run:
  - [active_learning_round0_seed_hybrid_bs2_v1](/G:/codex/PointCONV/binary_class5_runs_active_learning/active_learning_round0_seed_hybrid_bs2_v1)
- Key round-0 validation result:
  - F1 `0.9463` and IoU `0.8981` at threshold `0.5`
  - tuned validation threshold `0.37`
- Added and verified the cold-test workflow with:
  - [cold_test_smoke](/G:/codex/PointCONV/binary_class5_runs_active_learning/cold_test_smoke)
  - [cold_test_eval_smoke](/G:/codex/PointCONV/binary_class5_runs_active_learning/evals/cold_test_eval_smoke)

## 2026-04-01

### Added
- Created the TensorFlow 2 codebase under [tf2](/G:/codex/PointCONV/tf2).
- Added native TF2/Keras model components:
  - [ops.py](/G:/codex/PointCONV/tf2/PointCONV/model/ops.py)
  - [layers.py](/G:/codex/PointCONV/tf2/PointCONV/model/layers.py)
  - [segmentation.py](/G:/codex/PointCONV/tf2/PointCONV/model/segmentation.py)
- Added the TF2 segmentation/inference entrypoint:
  - [PointCONV_Segment.py](/G:/codex/PointCONV/tf2/PointCONV/PointCONV_Segment.py)
- Added TF2 binary training and inference entrypoints:
  - [train_binary_classifier.py](/G:/codex/PointCONV/tf2/train_binary_classifier.py)
  - [infer_binary_classifier.py](/G:/codex/PointCONV/tf2/infer_binary_classifier.py)
- Added voxelized learning-data preparation:
  - [prepare_voxelized_learning_data.py](/G:/codex/PointCONV/tf2/prepare_voxelized_learning_data.py)

### Changed
- Fixed the small-batch padding issue in [Dataset.py](/G:/codex/PointCONV/tf2/PointCONV/Dataset.py) so small numbers of sampled windows do not fail during batch padding.
- Fixed sparse-sampling and metadata-alignment issues in [SamplePoints_Parr_Deterministic.py](/G:/codex/PointCONV/tf2/PointCONV/SamplePoints_Parr_Deterministic.py).
- Made binary preprocessing use config-driven thinning from [inputconfig.yml](/G:/codex/PointCONV/tf2/inputconfig.yml), with `voxel_size: 0.3` as the active binary-classifier preparation value.
- Updated binary inference to:
  - cache voxelized inputs
  - support faster non-deterministic inference options
  - map predictions back to the original-density point cloud in a TF1-style postprocess

### TF2 Validation
- Achieved TF2 smoke-test completion on the staged LAS data with outputs under [full_smoketest](/G:/codex/PointCONV/full_smoketest).
- Reintroduced TF2-compatible compiled point-cloud ops for near-TF1 inference speed and parity.
- Validated reduced-fixture parity between `tf1` and `tf2` under [parity_smoketest](/G:/codex/PointCONV/parity_smoketest).

### Binary Classification
- Trained multiple `class 5 vs rest` prototype models under [binary_class5_runs](/G:/codex/PointCONV/binary_class5_runs).
- Added hybrid cross-entropy plus soft-IoU loss support.
- Added per-epoch IoU logging to the TF2 binary trainer.
- Prepared explicit `0.3`-voxel learning data under [learning_data_validation_voxel_0p3](/G:/codex/PointCONV/learning_data_validation_voxel_0p3).
- Ran binary inference on La Verne test data under [binary_inference_runs](/G:/codex/PointCONV/binary_inference_runs), including mapped-back original-density outputs.

## Current Key Locations

### Source
- TF1 reference code: [tf1](/G:/codex/PointCONV/tf1)
- TF2 code: [tf2](/G:/codex/PointCONV/tf2)

### Training and Evaluation Artifacts
- Binary training runs: [binary_class5_runs](/G:/codex/PointCONV/binary_class5_runs)
- Active-learning training runs: [binary_class5_runs_active_learning](/G:/codex/PointCONV/binary_class5_runs_active_learning)
- Binary inference runs: [binary_inference_runs](/G:/codex/PointCONV/binary_inference_runs)

### Active-Learning Artifacts
- Planning and manifests: [active_learning_artifacts](/G:/codex/PointCONV/active_learning_artifacts/training_data_2026_04_01A)
- Prepared round datasets: [active_learning_rounds](/G:/codex/PointCONV/active_learning_rounds/training_data_2026_04_01A)
