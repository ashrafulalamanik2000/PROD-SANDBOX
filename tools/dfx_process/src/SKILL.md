---
name: dfx-process
description: >
  End-to-end DFX (Solve3D) LiDAR + panoramic imagery pipeline. Parses
  Image Project.lst files into camera CSVs, creates georeferenced camera
  point shapefiles with viewer hyperlinks, indexes LAS file extents, and
  stitches cubemap images into equirectangular panoramas using
  pano_generator.py (pure-Python Solv3D engine.exe drop-in) via Docker.
  Input data must already be organized in Solve3D format with
  Raw Project Data/Image Project/ and Raw Project Data/LAS Files/ structure.
  All stages are idempotent. Use when the user mentions "DFX", "dfx process",
  "Solve3D", "CommTech", "lst to csv", or DFX camera point shapefiles.
argument-hint: <maindir> [--stages all] [--epsg 26914] [--pn <project_id>] [--platform viewer|cloud] [--pano-workers 16]
user-invocable: true
allowed-tools: Bash, Read, Glob, Grep
---

# DFX Processing Pipeline — Dockerized

Single-command pipeline for DFX mobile-LiDAR + panoramic imagery data.
Runs inside the shared Firmatek `mmworkflow` Docker image — no Solve3D install required.
Bundles its own `pano_generator.py` (pure-Python Solv3D `engine.exe` drop-in) for cubemap stitching.

## Quick Start

```bat
Run_DFX_Pipeline.bat <MAINDIR> --epsg 26914 --pn <project_id>
```

Where `<MAINDIR>` contains one or more DFX mission folders.

The launcher runs a deterministic input gate before AWS/Docker. Validate only,
including a JSON report suitable for schedulers:
```bat
Run_DFX_Pipeline.bat <MAINDIR> --preflight-only --json
```

## Required Input Layout

```
<MAINDIR>/<mission>/
└── Raw Project Data/
    ├── Image Project/
    │   ├── Image Project.lst
    │   └── Run N Camera 4 360/     (one dir per run, contains cubemap JPGs)
    └── LAS Files/
        └── *.las
```

## Outputs (per mission, all idempotent)

```
<mission>/
├── <pname>_Image Project.lst              (csv stage — copy of LST)
├── <pname>_CSVs/
│   ├── <pname>.csv                        (csv stage — camera positions)
│   └── <pname>_CameraPoints2.shp         (shp stage — with viewer hyperlinks)
├── <pname>_LASINDEX.shp                   (lasindex stage — LAS extents)
└── Pano_output/
    └── *.jpg                              (pano stage — equirectangular)
```

## Stages

| Stage | Does | Idempotent skip condition |
|---|---|---|
| `csv` | `.lst` → `<pname>.csv` via HRP→RPY rotation matrix | CSV exists |
| `shp` | CSV → camera points shapefile + viewer/GMap/StreetView hyperlinks | `_CameraPoints2.shp` exists |
| `lasindex` | LAS bounds → `<pname>_LASINDEX.shp` | LASINDEX shapefile exists |
| `pano` | cubemap runs → equirectangular JPGs via `pano_generator.py` | Every expected frame exists |

## Parameters

| Flag | Default | Notes |
|---|---|---|
| `--stages` | `all` | Comma-separated subset: `csv,shp,lasindex,pano` |
| `--epsg` | `26914` (UTM15N NAD83) | Match your project zone |
| `--pn` | _(empty)_ | Spatial Data AI viewer project ID for hyperlinks |
| `--platform` | `viewer` | `viewer` or `cloud` — controls hyperlink base URL |
| `--addhp` | `yes` | `yes` = add Lat/Long/Hyperlink/GMap/GStreet fields |
| `--pano-workers` | `16` | Parallel pano workers (16 is optimal on 32-CPU host) |

## Workflow — when `/dfx-process <maindir>` is invoked

1. **Pass maindir** as the first argument. The gate checks every discovered mission.
2. **Set EPSG/project ID explicitly when defaults are not correct.** Routine execution has no confirmation prompt.
3. **Run Docker**:
   ```bat
   "<repo>/Workflows/DFX_Project/skills/dfx_process/Run_DFX_Pipeline.bat" "<maindir>" --epsg <epsg> --pn <pn>
   ```
   Use `run_in_background: true` for more than one mission.
4. **Report**: missions processed, per-stage outputs, any errors.

## Files in this skill

| File | Purpose |
|---|---|
| `Run_DFX_Pipeline.bat` | One-command Docker launcher — **preferred entry point** |
| `scripts/dfx.py` | CLI orchestrator — all 4 stages |
| `scripts/dfx_csv.py` | LST → CSV (ported from Write_S3Dcsv_v10_effigis_asdef.py) |
| `scripts/dfx_shp.py` | CSV → camera points shapefile (ported from Solve3D_HP_v5_effigis_asdef.py) |
| `scripts/pano_generator.py` | cubemap → equirectangular stitcher (pure-Python Solv3D `engine.exe` drop-in) |
| `scripts/preflight.py` | token-free structure/stage validator with optional JSON output |

`pano_generator.py` is bundled here in `scripts/`. `Run_DFX_Pipeline.bat`
mounts this `scripts/` folder into the container at `/app`, and `dfx.py`
runs `/app/pano_generator.py` — so this skill is self-contained (no external
skill dependency). It is kept in sync with `Workflows/Aecon_Project/skills/aecon_process`
version; keep them in sync if either changes.

## Notes

- **EPSG** varies by project region: 26911 (UTM11N), 26912, 26914 (UTM15N), 26917 (UTM17N), 26919 (UTM19N)
- **`--pn`** is the short project hash from the Spatial Data AI viewer URL (e.g. `f1643a57`). Required for hyperlinks in the shp stage; can be left empty to skip hyperlink fields.
- **Pano workers**: 16 is the sweet spot on a 32-CPU host. Lower on smaller machines.
- **LAS only** (no pano): `--stages csv,shp,lasindex` to skip pano entirely.
- All paths in Docker mount the host `MAINDIR` at `/data` — no path conversion needed.
- Partial panorama folders resume at missing frames; a single existing JPG no
  longer marks the stage complete. Processing failures return nonzero.
- Exit codes: `0` success, `1` processing/runtime failure, `2` data gate failure,
  `3` missing host runtime. Routine execution requires no model tokens.
