---
name: shp-to-gdb
description: >
  Convert PAS/SDAI subcontractor shapefiles into populated SDAI-template File
  Geodatabases (SDAI_TEMPLATE_AERIAL.gdb + SDAI_TEMPLATE_TOPO.gdb) in EPSG:26914.
  Recreates empty copies of the template GDBs and loads the matching PAS_*
  shapefiles into each feature class with correct FEATURE_CODE values, 3D-Measured
  (ZM) geometry, boulder/pad point buffering, and street-furniture consolidation.
  Use when the user wants to build the gdb for a tile, put deliverable shapefiles
  into the geodatabase, or generate the same GDB format received from subs.
argument-hint: <shp_folder_path> [--out <output_dir>]
user-invocable: true
allowed-tools: Bash, Read, Glob, Grep
---

# SHP → SDAI Template GDB

Load a folder of PAS deliverable shapefiles into empty copies of the two SDAI
template geodatabases, matching each shapefile to its feature class and writing
3D-Measured (ZM) geometry in the template CRS (**EPSG:26914 / NAD83 UTM 14N**).

## Quick Reference

- **Script (PAS/Texas)**: `<skill dir>\scripts\build_gdb.py`
- **Script (AECON/Burlington)**: `<skill dir>\build_gdb_aecon.py`
- **Templates** (bundled, empty): `<skill dir>\templates\` (used by default)
- **Python**: `%USERPROFILE%\.conda\envs\gdal_env\python.exe` (standard per-machine
  `gdal_env` from `setup_machine.ps1`; resolve for the current user — never
  hardcode another user's home)

## AECON variant (FSA_* tiles)

For AECON/Burlington tile folders use `build_gdb_aecon.py --shp <tile folder>`
(one run per tile). It differs from the PAS flow:
- Output CRS **EPSG:26917** (UTM 17N); AECON source names (incl. trailing-space
  filenames), spec-conformant FEATURE_CO codes, SIGN_POST+STREET_SIGNS both →
  TRAFFIC_SIGN, BOULDER/CONCRETE_PAD copied as polygons/lines (no buffering).
- 2D TREE_BUSH_HEDGE outlines get per-vertex Z by IDW from TREES.shp ground points.
- **Building clip**: BUILDINGS footprints are subtracted from TREES_BUSHES_HEDGES
  outlines (rings re-close along walls; slivers <0.25 m² dropped; outlines fully
  inside a building removed; a building wholly inside a cluster becomes an inner
  ring). Z re-assigned from original vertices after reshaping.
- Downstream (AECON flow only): the workspace-level
  `Skills\final_delivery\make_final_delivery.py` turns the tile GDBs into the
  FinalDelivery 2D/3D shapefile package (EPSG:2958). Not part of the DFX
  pipeline — DFX packages via its own `client-delivery` gate.

---

## Workflow

When the user invokes `/shp-to-gdb`, follow these steps:

### Step 1: Resolve paths
1. **SHP folder**: read from `$ARGUMENTS` or ask the user for the folder of PAS
   shapefiles. Glob `<folder>\*.shp` and report the count.
2. **Output dir**: `--out` if given, else default to the SHP folder (the two
   `.gdb` are created there).

### Step 2: Check the pole prerequisite
The AERIAL gdb expects poles already split into `UTILITYPOLES_TOP.shp` and
`UTILITYPOLES_BASE.shp` (higher-Z point = top, lower-Z = base). If only a
combined `UTILITYPOLES.shp` exists, split it first (pair points within ~1.5 m,
classify by relative Z; ≥2 m gap = a real pole) before running this skill.

### Step 3: Run the script
```bash
"$USERPROFILE\.conda\envs\gdal_env\python.exe" "<skill dir>\scripts\build_gdb.py" --shp "<shp_folder>" 2>&1
```
Optional flags:
- `--out <dir>` — output directory (default: the `--shp` folder)
- `--templates <dir>` — override the bundled empty templates

### Step 4: Verify and report
Open the output GDBs and confirm: per-class feature counts, CRS = 26914,
`FEATURE_CODE`/`FEATURE_CO` values, and that BOULDER polygons are valid. Report
which classes were filled vs left empty, plus any "source not found" warnings the
script prints.

---

## Mapping (edit the dicts at the top of `build_gdb.py` if a sub uses other names)

**AERIAL** (`FEATURE_CODE`): UTILITYPOLES_TOP→POLE_TOPS (`POLE TOP`),
UTILITYPOLES_BASE→POLE_BASES (`POLE BASE`), OTHERPOLES→OTHER_POLES (`OTHER POLE`).
CONDUCTOR_*/GUY_WIRE_LINES/POLE_ATTACHMENT_POINTS have no PAS source → empty.

**TOPO** (geometry only unless noted): Back_of_Curb→BACK_OF_CURBS,
Final Building→BUILDINGS, Ditch_BOTTOM→DITCH_BOTTOMS, Ditch_TOP→DITCH_TOP_OF_SLOPE,
DRIVEWAY→DRIVEWAYS, EDGE_OF_PAVEMENT→EDGE_OF_PAVEMENT, EDGE_OF_GRAVELS→GRAVEL_ROADEDGES,
Fence→FENCES, FIREHYDRANTS→FIRE_HYDRANTS, Front_of_Curb→FRONT_OF_CURBS,
TRAFFICSIGN→TRAFFIC_SIGN, HYDROMETER→METER, TREE_BUSH_HEDGE→TREES_BUSHES_HEDGES (`TREES`).
- DRIVEWAY_MATERIALS_GRAVELS/BRICKS/CONCRETE → DRIVEWAY_MATERIAL (coded GRAVEL/BRICK/CONCRETE).
- BPAD, GLB, ONU, GAS_LINE_MARKER, STREETLIGHTS, STREET_FEATURE_UNKNOWN,
  TRAFFIC_CONTROL → STREET_FURNITURE (each with its own FEATURE_CO code).
- **BOULDER** (points) → buffered 0.5 m, dissolved into cluster polygons.
- **CONCRETE_PAD** (point) → buffered 1.5 m, boundary written as a line.

## Inputs

| File | Location | Description |
|------|----------|-------------|
| PAS `*.shp` | user-supplied folder | Deliverable shapefiles (poles, topo lines/points), EPSG:26914 |
| `*.gdb` templates | bundled `templates/` | Empty SDAI AERIAL + TOPO schema (schema only) |

## Output

| File | Location | Description |
|------|----------|-------------|
| `SDAI_TEMPLATE_AERIAL.gdb` | `--out` (default = SHP folder) | 7 pole/utility feature classes |
| `SDAI_TEMPLATE_TOPO.gdb` | `--out` | 39 topo feature classes |

## Gotchas (handled by the script)
- **Field name differs by GDB**: AERIAL uses `FEATURE_CODE`, TOPO uses the
  truncated `FEATURE_CO`; the script sets whichever exists. TRAFFIC_SIGN and
  BOULDER templates have no code field (geometry only).
- **M values**: Esri FGDB rejects NaN measures — geometry is written ZM with M=0.
- **File locks**: if an output GDB is open in ArcGIS/ArcCatalog/ArcGIS Pro, the
  overwrite fails and the script exits with a clear message — close it and re-run.
- Sources are reprojected to the template CRS only if they differ (PAS shapefiles
  are already 26914).
- Missing source shapefiles are reported as warnings; their classes stay empty.
