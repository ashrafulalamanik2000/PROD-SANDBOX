# finetune/

TF1 fine-tune workflow for the bundled `PointCONV_model_6class_v0.0.10`. See
[../FINETUNE_WORKFLOW.md](../FINETUNE_WORKFLOW.md) for the full operational
runbook.

## Files

| File | Purpose |
| --- | --- |
| [finetune_config.yml](finetune_config.yml) | Single source of truth for training hyperparameters, splits, and warm-start path. |
| [dtech_to_model_mapping.yml](dtech_to_model_mapping.yml) | DTECH source classification -> 6-class model index map. Anything not listed becomes an ignored point. |
| [prepare_finetune_data.py](prepare_finetune_data.py) | Host-side data prep: reads 0.1 m thinned LAS, samples 16,384-point regions with the same `divide_and_conquer_sample_groups` used for inference, writes per-region `lrn_xyz/class/smpw/scale_*.npy` files into `train/val/test/` subfolders. |
| [train_finetune.py](train_finetune.py) | TF1 trainer. Restores the warm-start checkpoint into the matching graph variables, trains with IoU-weighted loss over the 5 active classes (transmission tower frozen by exclusion from the loss), and writes a model directory laid out for the existing tiled-inference path. |
| [run_post_finetune_eval.py](run_post_finetune_eval.py) | Orchestrates inference + merge + IoU comparison vs an existing baseline IoU run. Reuses the baseline's preprocessed tiles and tile manifest so the comparison is on identical input geometry. |
| [run_finetune.ps1](run_finetune.ps1) | PowerShell wrapper that launches the Docker training container with the standard set of mounts. |
| [run_post_finetune_eval.ps1](run_post_finetune_eval.ps1) | PowerShell wrapper for the post-train inference + IoU pipeline (Python orchestrator above is the canonical one). |

## Inference launchers (use the trained model)

These live alongside the existing baseline launchers. They consume
[../tf1/inputconfig_finetune.yml](../tf1/inputconfig_finetune.yml), which
points at `model_directory: PointCONV_model_6class_Mobile_v0.0.10` and
relies on the standard S3 auto-download in
[../tf1/PointCONV/PointCONV.py](../tf1/PointCONV/PointCONV.py) when the
local model directory is missing.

| Launcher | Purpose |
| --- | --- |
| [../tf1/Docker_Run_Classification_Finetune.bat](../tf1/Docker_Run_Classification_Finetune.bat) | Mirrors `Docker_Run_Classification.bat` for the fine-tuned model. |
| [../run_tf1_tiled_finetune.ps1](../run_tf1_tiled_finetune.ps1) | Mirrors `run_tf1_tiled_ottercreek.ps1` for large-file 0.1 m tiled inference with the fine-tuned model. |
| [../tools/upload_finetune_model_to_s3.py](../tools/upload_finetune_model_to_s3.py) | One-time helper to publish a fine-tuned model directory to `s3://sdai-model/lidar_ml/`. |
