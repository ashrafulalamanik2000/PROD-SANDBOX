---
name: aecon-process
description: >
  End-to-end AECON/Burlington LiDAR + panoramic imagery pipeline. Parses .lst
  files into YAML, organizes project data, generates EOP metadata, stitches
  panoramas (pure-Python replacement for Solv3D engine.exe), then colorizes
  and clips LAS files. Runs fully Dockerized via the shared Firmatek
  `mmworkflow` image — no Solv3D install required. Runs multiple projects
  and LAS files in parallel with idempotent stages.
  Use when the user gives a data path containing AECON project folders, or
  mentions "AECON", "Burlington", "LiDAR + panorama", "cubemap", "Solv3D".
argument-hint: <data_path> [--stages all] [--crs EPSG:26917] [--buffer 45]
user-invocable: true
allowed-tools: Bash, Read, Glob, Grep
---

# AECON Processing Pipeline — Dockerized

Single-command pipeline for AECON mobile-LiDAR + panoramic imagery data.
All 5 stages (yaml → organize → metadata → pano → colorize) run inside the
shared Firmatek `mmworkflow` Docker image. The pano stage uses a pure-Python
replacement for Solv3D `engine.exe` that benchmarks ~40% faster at 16 workers.

## Quick Start

```bat
Run_AECON_Pipeline.bat <DATA_DIR>
```

Where `<DATA_DIR>` contains one or more AECON project folders. That's it.

The launcher validates every project before AWS/Docker. Validate only and emit
machine-readable JSON with:
```bat
Run_AECON_Pipeline.bat <DATA_DIR> --preflight-only --json
```
All pipeline flags may follow the data path and override launcher defaults.

The bat:
1. `aws ecr get-login-password ... | docker login` against the shared registry
2. `docker pull 750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:latest`
3. Mounts `scripts/` → `/app` and `<DATA_DIR>` → `/data`
4. Runs `aecon.py` through the container's `pdal` conda env
5. Stage 4 pano uses `pano_generator.py` (via `AECON_ENGINE` env) with 16 workers

## Benchmark (499 cubemap frames, 32-CPU / 131 GB host)

| Mode | Time | vs Solv3D |
|---|---:|---:|
| Docker + `pano_generator.py` @ 16 workers | **222s** | 🏆 1.40× faster |
| Host Python + `pano_generator.py` @ 16 workers | 224s | 1.38× |
| Host Python + `pano_generator.py` @ 8 workers (default) | 265s | 1.17× |
| Solv3D `engine.exe` | 310s | baseline |

16 workers is the sweet spot (24 and 32 workers tested — no further speedup).

## Required input layout

```
<DATA_DIR>/<project>/
├── <project>.iprj                (1 file, defines camera folder names)
├── <project>.lst                 (1+ files, each becomes Run_N)
├── Images/
│   ├── CubeMap.cal
│   ├── Forward/  Left/  Rear/  Right/  Top/  Bottom/
│   │   └── F_NNNNN.jpg           (raw cubemap frames)
└── Lidar/*.las                   (raw LAS files)
```

## Outputs (per project)

```
<project>/
├── InputConfig.yml                                       (stage: yaml)
├── Organized_Projects/
│   ├── Raw Project Data/
│   │   ├── InputConfig.yml                               (stage: organize)
│   │   ├── Image Project/Run N Camera 4 360/*.jpg        (renamed cubemaps)
│   │   └── LAS Files/*.las
│   └── Pano_output/
│       ├── Run_N_metadata.csv                            (EOPs, RPY)
│       └── *.jpg                                         (stage: pano, equirectangular)
└── Lidar/                                                (raw *.las stays loose)
    ├── Colorized/*_colorized.las                         (stage: colorize; transient
    │                                                      unless --keep-colorized)
    ├── Clipped/*_clipped.las                             (stage: colorize; classify input)
    └── Classified/                                       (reserved: pointconv output)
```

## Stages (all idempotent — re-running skips completed work)

| Stage | Does | Parallelism |
|---|---|---|
| `yaml` | `.lst` → `InputConfig.yml` (multi-run aware) | project-workers |
| `organize` | copy/rename 6-cam images + copy LAS | project-workers |
| `metadata` | per-run `Run_N_metadata.csv` with HRP→RPY via `Frot()` | project-workers |
| `pano` | cubemap → equirectangular via `pano_generator.py` | pano-workers (16) |
| `colorize` | KD-tree RGB sampling + camera-line clip | las-workers × threads |

## Workflow — when `/aecon-process <data_path>` is invoked

1. **Pass data path** as the first argument. The gate validates every discovered project.
2. **Set CRS explicitly when `EPSG:26917` is not correct.** Routine execution has no confirmation prompt.
3. **Run Docker**:
   ```bat
   "<repo>/Workflows/Aecon_Project/skills/aecon_process/Run_AECON_Pipeline.bat" "<data_path>"
   ```
   Use `run_in_background: true` for anything larger than a handful of projects.
4. **Report**: projects processed, per-stage timings, output paths, any per-project errors.

## Fallback — host-native (no Docker)

If Docker / ECR is unavailable, the skill can still run via the portable conda env
at `E:\MIGHTYMACHINE FILES\AECON_PORTABLE\myenv` pointing at `scripts/run.bat`.
Set `AECON_HOST_PY` to that environment's `python.exe` when it is not located at
`<skill>/myenv/python.exe`.
Solv3D's engine.exe is used in this fallback (if installed); otherwise
`pano_generator.py` can be used as the engine with:

```bat
python aecon.py "<data_root>" --engine "python pano_generator.py" \
    --stages yaml,organize,metadata,pano,colorize \
    --crs EPSG:26917 --project-workers 2 --las-workers 4 --threads 8
```

## Files in this skill

| File | Purpose |
|---|---|
| `Run_AECON_Pipeline.bat` | One-command Docker launcher — **preferred entry point** |
| `scripts/aecon.py` | CLI orchestrator |
| `scripts/stages.py` | All 5 stage implementations (idempotent) |
| `scripts/pano_generator.py` | Pure-Python cubemap→equirect (Solv3D drop-in) |
| `scripts/colorize.py` | LAS colorization — math identical to `BatchLASColorFromScratch_v8.py` |
| `scripts/camera_utils.py` | Buffer + PDAL clip |
| `scripts/rotation_utils.py` | `Frot()` HRP→RPY Euler conversion |
| `scripts/requirements.txt` | Python deps (informational — all already in `mmworkflow`) |
| `scripts/run.bat` | Host-native fallback launcher |
| `scripts/Dockerfile.v2` | Optional standalone aecon image (if ever needed) |
| `scripts/DOCKERIZE_PANO.md` | Background notes on the pano replacement |
| `scripts/preflight.py` | token-free all-project structure/stage validator with JSON output |

## Notes

- **`mmworkflow` image already has every Python dep** aecon needs (geopandas, laspy,
  pdal python bindings, opencv, Pillow, PyYAML, tqdm, etc.) in its `pdal` conda env at
  `/root/miniconda3/envs/pdal/bin/python`. No custom image build, no license gates.
- **Credentials**: the bat file mounts `%HOME%/.aws` into the container, so AWS SDK
  calls inside the pipeline (e.g., if any stage reads from S3) Just Work™.
- **Pano workers** tunable via `AECON_PANO_WORKERS` env var (default 16 is optimal
  on 32-CPU host — 24 and 32 workers tested, no further speedup).
- **Lock-remap** (Solv3D conservative default) is **off** for speed. Enable with
  `--lock-remap` on `pano_generator.py` if ever running on a low-RAM host.
- Partial panorama output resumes at missing frames. Failed engine/colorization
  work is no longer converted into a success marker or a zero exit code.
- Exit codes: `0` success, `1` processing/runtime failure, `2` data gate failure,
  `3` missing host runtime. Routine execution requires no model tokens.
