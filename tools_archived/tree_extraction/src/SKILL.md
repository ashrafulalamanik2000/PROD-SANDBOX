---
name: tree_extraction
description: >-
  Extract tree TRUNKS (stems) and 2D CANOPY footprints (the vegetation outline)
  from a PointConv-classified AECON LiDAR cloud -- classical, deterministic, no
  training. Builds a min-Z ground DEM from class 2, segments crowns from a Canopy
  Height Model over class 5, fits a truncated-cone trunk per tree, and writes
  Tree_Stems.shp (PointZ at the trunk base + diameter/radius/circumference at 1 m,
  DBH at 1.3 m) and Tree_Canopy.shp (crown-footprint polygons). ONE run produces
  BOTH. Use for the AECON pipeline's tree-extraction step, after classify, when
  3_Classified_LAS/*_tf1_pointconv_combined_0p1m.la[sz] exists. Vendored, fully
  self-contained (no Greg_Sandbox / pixi / Docker dependency).
category: Vectorization_Skills
user-invocable: true
allowed-tools: [Bash, Read, Glob, Grep]
version: "1.0.0"
idempotent: true
---

# AECON tree-extraction (stems + canopy)

Vendored, self-contained port of the chain-orchestrator `stage4t_tree_trunk_canopy`
stage. The classical extractor runs **directly** under this machine's `gdal_env`
Python (numpy/scipy/laspy/lazrs/geopandas/pyogrio/shapely) — no pixi env, no
Docker image, no package runner, no Greg_Sandbox.

- **Wrapper:** `scripts/run_tree_extraction.py` — forwards to the worker with the
  current interpreter and the conda env's GDAL/PROJ data dirs set.
- **Worker:** `scripts/extract_tree_trunk_canopy.py` — the pure-CPU streaming
  extractor (5M-point chunks; RAM bounded by the class-5 canopy array + ground DEM).

## Inputs / outputs

- **In:** `3_Classified_LAS/*_tf1_pointconv_combined_0p1m.la[sz]` (from the
  classify step). Must carry class 2 (ground) and class 5 (high-veg, holds the
  stems) and a CRS in its LAS header (or pass `--epsg`).
- **Out:** into `4_Extracted_SHP/` (each `.shp` + a `.gpkg` twin):
  - `Tree_Stems.shp` — PointZ at the trunk base. Fields incl. `tree_id, ground_z,
    diam_1m, rad_1m, circ_1m, dbh_1p3m, trunk_ht, canopy_top, lean_deg, quality`.
  - `Tree_Canopy.shp` — PolygonZ crown footprint (= the vegetation outline).
    Fields `tree_id, crown_area, crown_diam, top_hag, n_pts`.

## Run

```bash
"C:\Users\AshrafulAnik\.conda\envs\gdal_env\python.exe" \
  scripts/run_tree_extraction.py \
  --input  <3_Classified_LAS>/<scene>_tf1_pointconv_combined_0p1m.laz \
  --out-dir <4_Extracted_SHP> \
  --epsg <working EPSG, e.g. 26917> \
  --canopy-class 5 --ground-class 2 --measure-height 1.0
```

Output CRS = `--epsg` if given, else the input header CRS — pass the dataset's
working EPSG (typically 26917; never assume — see the dataset memory) to be safe.
`--input` may be a single file or a directory (add `--pattern "*.laz"`).

**Exit codes:** `0` success, `3` benign-empty (tree-less tile — record and
continue), `1` error. On a tree-less corridor the worker prints one of
`no tree canopy` / `no canopy apexes` / `no trees survived` and exits 3.

## Downstream (shp-to-gdb)

`Tree_Stems` → TREES points (DIAMETER = `dbh_1p3m`, HEIGHT = `canopy_top`);
`Tree_Canopy` → TREES_BUSHES_HEDGES outlines (clipped out of BUILDINGS footprints
by the AECON GDB builder).

## Provenance

Ported 2026-07-21 from
`chain-orchestrator/.claude/skills/stage4t_tree_trunk_canopy` +
`scripts/extract_tree_trunk_canopy.py` (git_sha 6418794, readiness 8; golden diam
MAE 0.001 m, real 72_1 tile MAE 0.060 m). The heavy pixi/Docker/S3 packaging was
intentionally dropped — the worker is pure CPU Python and runs in `gdal_env`.
