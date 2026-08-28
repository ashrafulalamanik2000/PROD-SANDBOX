# Setup — PointCONV Classification

One-time prerequisites per machine.

## 1. GPU
NVIDIA GPU, compute capability **7.0–12.0**, **≥16 GB VRAM**. Docker Desktop with
NVIDIA container support (`docker run --gpus all … nvidia-smi -L` must list the GPU).

## 2. mmworkflow GPU image (~60 GB)
```bash
aws ecr get-login-password --region us-west-2 \
  | docker login --username AWS --password-stdin 750433818015.dkr.ecr.us-west-2.amazonaws.com
docker pull 750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1
```
If you already have `mmworkflow:latest` (same digest), pass `--image …/mmworkflow:latest`
(the orchestrator runs with `--pull=never`, so the tag must exist locally).

## 3. PointCONV_TF1_Workflow + model — BUNDLED (git-LFS)
The `PointCONV_TF1_Workflow/` dir (tf1 code, `inputconfig_finetune_lowmem.yml`, and
the `PointCONV_model_6class_Mobile_v0.0.18_retune_c2` model) ships **inside this skill**.
The model checkpoint is stored via **git-LFS**, so after cloning:
```bash
git lfs install
git lfs pull            # fetches PointCONV_TF1_Workflow/models/** (~250 MB)
```
No `--pcv-dir` needed — it defaults to the bundled dir. Override only to use a
different workflow/model (`--pcv-dir`, `--model-name`, `--model-dir`).

## 4. Host Python (for the CPU presample step)
The presampler runs on the host with:
`laspy, numpy, scipy, scikit-learn, pyyaml, tqdm`.
Set `POINTCONV_PY` to that interpreter if it isn't `python` on PATH. On SDAI
machines the standard env is `gdal_env` (see the workspace `CLAUDE.md`):
```bash
export POINTCONV_PY="$USERPROFILE/.conda/envs/gdal_env/python.exe"
```

## Preflight
```bash
docker info >/dev/null && echo "docker ok"
docker run --rm --gpus all --entrypoint nvidia-smi <mmworkflow-ref> -L   # GPU visible?
"$POINTCONV_PY" -c "import laspy,numpy,scipy,sklearn,yaml,tqdm; print('host deps ok')"
```

## Gotchas
- **Input must have RGB** (colorized). Format-0/1 clouds fail in presample.
- `--inputconfig` for presample and inference must be identical (a config-hash check
  enforces it) — just don't override one side.
