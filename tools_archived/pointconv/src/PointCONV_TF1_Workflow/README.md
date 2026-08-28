# PointCONV

This is the active PointCONV development workspace.

It contains:
- `tf1`, `tf2`, and `tools` for PointCONV training and inference
- the layered inference launcher and companion operations docs
- packaged reference models under `MODEL_VERSIONS`
- the shared 6-class base model under `Model_Development/safe_smoketest/model/PointCONV_model_6class_v0.0.10`
- external benchmark code, benchmark docs, and Pointcept planning under `Model_Development/external_benchmarks`
- model-development runs, search plans, and experiment artifacts under `Model_Development`

Recommended starting docs:
- `HOW_TO_RUN.md` — single-page runbook for inference, fine-tune, and IoU
- `CLASS_CODE_REFERENCE_DTECH_2025.md`
- `CURRENT_CODE_STATE_AND_HANDOFF.md`
- `RUN_LAYERED_INFERENCE.md`
- `tf1/classification.md`
- `TF1_TILED_INFERENCE_WORKFLOW.md`
- `TF1_WORKFLOW_BACKUP.md`
- `FINETUNE_WORKFLOW.md`
- `GROUND_THIN_WIRE_WORKFLOW.md`
- `LAYERED_INFERENCE_SCALING.md`
- `LAYERED_INFERENCE_PRODUCTION_CHECKLIST.md`
- `MODEL_ARCHITECTURE_OVERVIEW.md`
- `MULTICLASS_6CLASS_PLAN.md`
- `MULTICLASS_6CLASS_RUNBOOK.md`
- `Model_Development/external_benchmarks/EXTERNAL_FRAMEWORK_EVALUATIONS.md`
- `Model_Development/external_benchmarks/POINTCEPT_OUTDOOR_MULTICLASS_PLAN.md`

Version-control prep:
- see `VERSION_CONTROL.md` for the recommended git and Git LFS setup for this workspace

Recommended reference packages:
- `MODEL_VERSIONS\wire_model_versions\PointCONV_Wire_Amaret_v0.2.0`
- `MODEL_VERSIONS\vegetation_model_versions\PointCONV_Vegetation_v0.1.0`
- `MODEL_VERSIONS\pole_model_versions\PointCONV_Pole_Amaret_v0.1.1`
- `MODEL_VERSIONS\pole_model_versions\PointCONV_Pole_Amaret_v0.1.1_candidate`
