# Version Control Notes

This active workspace is now initialized as a local git repository and prepared for Git LFS.

## What Should Be Versioned

Track these by default:
- source code under `tf1`, `tf2`, and `tools`
- root runbooks and planning docs
- curated model packages under `MODEL_VERSIONS`
- the shared 6-class base model under `Model_Development/safe_smoketest/model/PointCONV_model_6class_v0.0.10`
- selected external-benchmark source and planning docs under `Model_Development/external_benchmarks`

## What Should Not Be Versioned

These are ignored in `.gitignore`:
- `Data/`
- `logs/`
- local virtualenv and editor folders
- Python cache folders and local scratch work
- experiment output folders under `Model_Development`
- benchmark output folders such as `KPConv_runs`
- heavyweight local support folders such as `open3d_model_zoo`

## Git LFS

This workspace includes packaged model weights and TensorFlow checkpoints.

`.gitattributes` is configured so these large binary artifacts go to Git LFS:
- `*.h5`
- `*.pth`
- `*.pt`
- `*.ckpt`
- `*.pb`
- `*.meta`
- `*.index`
- `*.data-00000-of-00001`
- `*.docx`

Recommended setup for the first commit:

```powershell
git lfs install
git add .gitattributes .gitignore
git add .
git status
```

## Practical Notes

- This workspace contains active development history and more local scaffolding than `G:\codex\PointCloud_Classification`.
- If you want a cleaner repo focused on reusable training, inference, models, and docs, use `G:\codex\PointCloud_Classification`.
- If you later decide not to version packaged models, remove or relocate `MODEL_VERSIONS` and the shared 6-class base model before the first commit.

## Recommended First Review

Before the first commit, sanity-check:
- `README.md`
- `RUN_LAYERED_INFERENCE.md`
- `MODEL_ARCHITECTURE_OVERVIEW.md`
- `MULTICLASS_6CLASS_RUNBOOK.md`
- `MODEL_VERSIONS/`
- `Model_Development/external_benchmarks/`
