---
name: high-veg-clusters
description: >
  Build smooth 3D TREE CLUSTER outline lines for HIGH vegetation (canopy
  height-above-ground >= 3 m) from classified LAS/LAZ point clouds. Per site:
  reclassify veg by HAG into Low/Med/High, build cluster outlines from the High
  band, DISSOLVE touching/overlapping footprints into single clusters, SMOOTH
  them (morphological close/open, no blocky raster staircase), drape Z to
  PolyLineZ, and copy the 3D (_Z) lines into a deliverable folder. Reproduces the
  DFX "TREESBUSHESHEDGES ... final" reference deliverable. Use when the user wants
  high-veg / tree-cluster / TREESBUSHESHEDGES outlines, or veg feature extraction
  from classified point clouds, and mentions HIGHVEG / tree clusters / smoothing.
argument-hint: --config sites.json [SITE ...] [--workroot D:\_work] [--final DIR]
user-invocable: true
allowed-tools: Bash, Read, Glob, Grep, Write, Edit
idempotent: true
version: "1.0.0"
---

# High-veg TREE CLUSTER outlines

**Goal:** turn classified LAS/LAZ into smooth 3D outline lines around HIGH
vegetation (tree canopy), one `TREE CLUSTER` feature per contiguous canopy blob,
each carrying its max canopy `HEIGHT`. This is the in-house alternative to
outsourcing tree-cluster feature extraction; output matches the DFX
`TREESBUSHESHEDGES_<site>final` reference (dense, rounded boundaries — **not** the
blocky raster staircase).

Everything is bundled in `scripts/`; no external skill or workspace dependency.

## Inputs / outputs

- **Input:** classified LAS/LAZ per site, with ground = class 2 and vegetation =
  class 5 (PointCONV output). Multiple tiles per site are fine (they're merged).
- **Output** (in `--final`, default `<workroot>\HIGHVEG_FINAL`): one 3D
  `TREESBUSHESHEDGES_<SITE>_Z.{shp,shx,dbf,prj,cpg,gpkg}` per site. Attributes:
  `FEATURE_CO="TREE CLUSTER"`, `HEIGHT` (max canopy m), `SHAPE_Leng`.
  Add `--keep-2d` to also emit the flat 2D lines.
- **Intermediates** kept under `<workroot>\<site>_hv\reclass\NN.laz` (the
  reclassified tiles — the slow part; cached so re-runs skip straight to
  build+drape). Delete `<workroot>\<site>_hv\TREESBUSHESHEDGES_*` to force a
  geometry rebuild after changing smoothing params.

## Run

```powershell
# python: gdal_env, the mmworkflow container, or any env with
# laspy + geopandas + shapely + scipy. WORKROOT must be a LOCAL drive (fast IO).
$py = "$env:USERPROFILE\.conda\envs\gdal_env\python.exe"
& $py "scripts\run_highveg_clusters.py" --config scripts\sites.json `
      --workroot D:\_sdai_work\highveg --final D:\_sdai_work\highveg\HIGHVEG_FINAL
# optional trailing SITE names run a subset: ... sites.json GIL_M2 WAB_M2
```

Copy `scripts\sites.example.json` to `sites.json` and fill in `src_root`, each
site's `epsg`, and its source tiles (`subdirs` under `src_root`, or explicit
`laz` globs). See the pipeline (`pipeline.json`) `classify` step for producing
the classified input; feed its `4_Classified_LAS\` output here.

## QA (always run before delivering)

```powershell
& $py "scripts\qa_check.py" D:\_sdai_work\highveg\HIGHVEG_FINAL
```
Targets: **% sharp corners <= ~0.5%** (blocky staircase is ~45-51%) and
**overlap pairs ~0**. Judge smoothness by sharp-corner %, NOT vertex count.

## How it works (and the tuning that matters)

Per tile: min-Z ground DEM (class 2) -> canopy-height model of class-5 points ->
threshold at MED (3 m) -> morphological close -> label -> pure-shapely
`_polygonize_labels` (rasterio-free). Then, across ALL tiles:

1. **Dissolve** every raw footprint with `unary_union` into single clusters
   (also heals tile-edge seams). Each merged cluster inherits the **max** canopy
   height of its members (via `gpd.sjoin` + groupby-max).
2. **Smooth** each merged cluster: buffer `+GAP`/`-GAP` (close, round joins) then
   `-OPEN`/`+OPEN` (open) then a **small** Douglas-Peucker simplify.
3. **Drape Z** onto the smoothed lines from the ground DEM -> PolyLineZ.

Tunables (CLI flags; defaults reproduce the reference):

| param | default | effect |
|---|---|---|
| `--med` | 3.0 | HAG (m) threshold for HIGH veg |
| `--cluster-gap` | 2.0 | raster close radius (m) — bridges canopy gaps |
| `--min-area` | 5.0 | drop clusters smaller than this (m²) |
| `--sm-gap` | 2.0 | vector close radius (m) |
| `--sm-open` | 0.75 | vector open radius (m) — rounds concavities, drops necks |
| `--sm-simp` | **0.1** | DP tolerance (m) — keep SMALL, see below |
| `--sm-qs` | 16 | arc segments per quadrant (arc smoothness) |

### Gotchas (learned the hard way — see also memory `veg-highveg-smoothing`)

- **`--sm-simp` must stay small (~0.1).** A large tolerance (e.g. 0.5) collapses
  the rounded arcs back into ~50° facets — output looks smoother than raw but is
  still visibly angular (~31% sharp). 0.1 + `--sm-qs 16` gives ~0.1% sharp.
- **Shapely version:** `buffer()`'s arc-resolution kwarg is `resolution=`
  (shapely 1.x, e.g. myenv 1.8.2) but `quad_segs=` (shapely 2.x, gdal_env/docker).
  The script's `_buffer()` shim handles both — don't hardcode either name.
- **Overlapping lines** come from smoothing clusters independently; the dissolve
  step (union before smoothing) fixes it. A few (~3 / 3500) near-touching pairs
  can survive where separate clusters sit 2–4 m apart; to force exactly 0, run
  the close on the GLOBAL union so anything <~4 m merges — that's what
  `scripts\cluster_smooth_reference.py` does (more aggressive merge, opt-in).
- **WORKROOT on a LOCAL drive**, never UNC — the reclass pass is IO-heavy.
- Deliverables ultimately belong under the dataset UNC tree
  (`\\SDAI-FS1\Production\Projects\DFX\Agentic_Workflow_Data\<Dataset>\5_Outsourced_Returns\`
  or `8_Final_Delivery\`), not the local workroot — stage them there when done.

## Files

- `scripts\run_highveg_clusters.py` — main builder (reclass → build → dissolve →
  smooth → drape → copy). Config/CLI driven, portable across shapely versions.
- `scripts\reclassify_veg_hag.py` — splits veg class 5 into Low(3)/Med(4)/High(5)
  by height-above-ground; chunked, handles 600M+ pt tiles. Called per tile.
- `scripts\qa_check.py` — smoothness / overlap / 3D report for a deliverable.
- `scripts\cluster_smooth_reference.py` — reference global-union close/open
  smoother (the zero-overlap, more-aggressive-merge variant; docker `/data` paths).
- `scripts\sites.example.json` — config template.
