---
name: tile-thin-clip
description: >
  Dockerized three-stage LAS/LAZ pipeline: (1) Tile large files into smaller
  tiles by point count, (2) Voxel-downsample (thin) each tile, (3) Clip to a
  buffered NETWORK_LINES.shp polygon and merge into a single output LAS.
  Runs entirely inside the SpatialData AI mmworkflow container, so no local
  Python or PDAL install is required — just Docker + AWS credentials.
argument-hint: <las_folder_path> [--steps tile,thin,clip] [--network-shp <path>] [--buffer 3.0] [--voxel-size 0.05]
user-invocable: true
allowed-tools: Bash, Read, Glob, Grep
---

# Tile-Thin-Clip (Docker)

Three-stage LAS/LAZ pipeline that tiles, thins (voxel downsample), and
optionally clips point cloud data to a buffered NETWORK_LINES.shp polygon.

Everything runs inside Firmatek's shared mmworkflow container — there is no
local Python or PDAL dependency.

## Quick Reference

- **Container image**: `750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:latest`
- **Script (inside container)**: `/script/tile_thin_clip.py`
- **Wrappers** (in this skill folder):
  - POSIX / Git Bash: `<skill dir>/scripts/run.sh`
  - Windows CMD: `<skill dir>\scripts\run.cmd`

One-time per machine: see [SETUP.md](SETUP.md).

---

## Workflow

When the user invokes `/tile-thin-clip` follow these steps:

### Step 1: Resolve Paths

1. **Input directory** — folder containing `.las` and/or `.laz`. Must be on
   local disk (Docker cannot mount network shares; stage data to a local
   drive first).
2. **Output directory** — local path where staging tiles, thinned LAZ, and
   final clipped LAS will be written. Created if missing.
3. **NETWORK_LINES.shp** — only required for the `clip` step. Local path.

### Step 2: Pre-flight Check

```bash
docker info >/dev/null 2>&1 && echo "docker OK"
aws sts get-caller-identity >/dev/null 2>&1 && echo "aws OK"
```

Authenticate to ECR (token valid 12 h). The wrapper script does this
automatically, but you can run it manually:

```bash
aws ecr get-login-password --region us-west-2 \
  | docker login --username AWS --password-stdin 750433818015.dkr.ecr.us-west-2.amazonaws.com
```

If any pre-flight check fails, see [SETUP.md](SETUP.md).

### Step 3: Run

POSIX / Git Bash / WSL:

```bash
bash "<skill dir>/scripts/run.sh" \
  --input-dir  "/abs/path/to/input" \
  --output-dir "/abs/path/to/output" \
  --steps tile,thin
```

Windows CMD:

```cmd
"<skill dir>\scripts\run.cmd" ^
  --input-dir  "C:\path\to\input" ^
  --output-dir "C:\path\to\output" ^
  --steps tile,thin
```

All flags supported by `tile_thin_clip.py` pass through:

| Flag | Default | Description |
|------|---------|-------------|
| `--steps` | `tile,thin,clip` | Comma-separated stages to run |
| `--max-points-per-tile` | `13000000` | Max points per tile (~0.5 GB) |
| `--voxel-size` | `0.05` | Voxel size for thinning (CRS units) |
| `--thin-workers` | `20` | Parallel workers for thinning |
| `--network-shp` | (required for clip) | Path to NETWORK_LINES.shp |
| `--buffer` | `3.0` | Buffer distance in metres |
| `--batch-size` | `20` | Files per PDAL batch |
| `--clip-workers` | `8` | Parallel workers for clipping |
| `--clip-output` | `<output>/clipped.las` | Custom output path |

### Step 4: Report Results

The script logs per-stage progress and writes its own log to
`<output>/log/<timestamp>_tile_thin_clip.log`. Report to the user:
- Per-stage timing
- Tile count
- Thinning reduction percentage
- Clipped point count (if clip ran)
- Output paths

---

## Inputs

| File | Description |
|------|-------------|
| `*.las` / `*.laz` | LAS/LAZ files (folder) |
| `NETWORK_LINES.shp` | Optional; required for clip step |

## Outputs

| File | Location | Description |
|------|----------|-------------|
| Tiled LAS | `<output>/tiles/` | After stage 1 |
| Thinned LAZ | `<output>/thinned/` | After stage 2 |
| Clipped LAS | `<output>/clipped.las` | After stage 3 |
| Log | `<output>/log/*.log` | Per-run pipeline log |

---

## Stages

### Stage 1: Tile
Splits large LAS/LAZ files into smaller tiles by point count. Small files are
decompressed (LAZ → LAS) or copied as-is. Large files are split into numbered
tiles.

### Stage 2: Thin
Voxel downsampling at configurable resolution (default 0.05 m / 5 cm cubes).
Keeps one point per voxel. Output is compressed LAZ. Runs in parallel.

### Stage 3: Clip
Loads NETWORK_LINES.shp, buffers each line by N metres (auto-converts to CRS
units for US Survey Feet), and clips all LAS/LAZ files to the buffered
polygon using PDAL. Pre-filters files by header bbox to skip non-overlapping
files. Runs PDAL crop in parallel batches, then merges into a single output
LAS.

## Running Individual Steps

You can run any subset of stages:
- `--steps tile` — tile only
- `--steps thin` — thin only (reads from tiles/ or input)
- `--steps clip` — clip only (reads from thinned/ or tiles/ or input)
- `--steps tile,thin` — skip clip
- `--steps thin,clip` — skip tile
