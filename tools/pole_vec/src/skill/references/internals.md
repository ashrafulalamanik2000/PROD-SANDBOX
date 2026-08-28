# Pole-network-catenary — internals, contracts, and drifts

Companion to `SKILL.md`. Verified 2026-08-23 against the host checkout at
`C:\Users\sdaiprod\source\agentic-workflows\Greg_Sandbox\agentic_development\Claude\projects\`
and the `sdai_chain_polevec` docker volume (md5-identical for
`PoleVec_workflow.py`, `pole_vectorization.py`, `estimate_pole_tops.py`, and
the shipped control/inputconfig YAMLs).

## Chain context

In `chain-full:v0.3.48` the pole half runs as: `stage2_pole_crop` (CPU) →
`stage3_5_pole_network` (CPU) → `stage3_pole_vec` (GPU docker, FULL mode needs
Stage 1b's 0.025 m fine cloud). Orchestrator entry points:
`chain_orchestrator.py` `run_stage2_pole_crop` (~L699), `run_stage3_pole_vec`
(L1418, with `_prepopulate_polevec_pole_tops` L1226 = "Path C" and
`_reclassify_crops_with_pointconv` L1336). The chain crops the RAW cloud and
reclassifies crops afterwards ("CT2": classified input lowers detection rate);
this skill's standalone path crops the classified cloud directly and accepts
that trade — revisit if detection recall looks poor.

Stage-2 pole-source modes in the chain: `loose_bridge` (default — DBSCAN
discovery via `reextract_poles_loose_bridge_multi.py`, then pipeline.py) and
`existing` (customer shapefile from stage0a). The discovery DBSCAN is
`eps=2.0 m, min_samples=10` over the combined class-18 XY of every matching
file; cluster gates `--min-pts 15`, `--min-z-range 1.5 m`. Run it on the
0.1 m-class cloud, never a dense 0.025 m one (94 GB OOM precedent).

## pipeline.py (Stage 2) — CLI notes beyond SKILL.md

- Defaults are ftUS: `--half-size 100.0`, `--search-radius 15.0`,
  `--voxel-size 0.082` (=0.025 m), `--col-size 0.5`, `--min-pole-height 9.0`,
  `--max-pole-height 50.0`. On metric data override all but `--col-size`.
- `--max-workers <=0` → auto (crop cap 8 / detect cap 16 / corridor cap 4).
  `--tile-parallel-files 1` is deliberate (LazrsParallel/Rayon deadlock risk).
- Cylinder stage exists (`--find-cylinders`, default off) — for thick
  utility poles; telco poles are too thin.
- Corridor stage default ON: `--corridor-max-span-distance 800.0` (units =
  CRS), `--corridor-voxel-size-meters 0.05` (this one IS metres),
  `corridor_spans/corridor_manifest.csv` + thinned merged corridor.
- Env: geopandas, laspy(+lazrs), pandas, numpy, shapely, pyproj; python-docx
  optional (text report always written). No PDAL/scipy/sklearn.
  The `networkx` conda env covers all of it (verified).
- `<stem>_processed.csv` lands **next to `--pole-shapefile`**, columns exactly
  `Pole, TOP_X, TOP_Y, TOP_Z, LAS_NAME` (no DENSITY — README is stale).
  Undetected-but-cropped poles get original XY + median detected `z_top`.
  Sibling `_processed.shp` adds `status`/`detected` + PointZ geometry.
- Crop filenames: `re.sub(r'[^a-zA-Z0-9_-]','_', pole_id)`.
- Detection is memory-budgeted (75 % avail RAM, psutil) with halved-concurrency
  retry on OOM-kill.

## estimate_pole_network.py (Stage 3.5)

- Naming is hard-coded twice: availability scan
  `glob("*_tf1_pointconv_combined_0p1m.la[sz]")` and per-pole lookup of
  exactly `{pole_id}_tf1_pointconv_combined_0p1m.laz|.las`. No-glob fallback →
  `link_crops_for_network.py`.
- Pole-ID column: first of `Pole, pole_id, PoleID, pole`; raw values not
  matching an available stem get `Pole_` prefixed.
- Needs only `x,y,z,classification` (18 → top/base Z within
  `--pole-z-radius-m 3`; 14 → stubs within `--stub-radius-m 20`, 8 sectors,
  `--min-stub-points 8`, `--min-stub-length-m 3`).
- Pass 1 directional match (`cos(angle_tol)` gate, score
  `d*(1+dev/tol)`), pass 2 mutual-stub component bridging (2× span, 1.5×
  angle, degree<2 endpoints only), near-dup QC at 15 m.
- Defaults betray Firmatek: `--waveb-crs EPSG:26911`, `--output-crs EPSG:6424`
  — always set both.
- `SystemExit` on: no CRS on pole shapefile, 0 seeded poles, empty graph.

## PoleVec_workflow.py (Stage 3)

Four required args; `--input_folder` = base_dir; `--input_inputcontrol` and
`--input_segmented_folder` are `os.path.join(base_dir, arg)` (an absolute
2nd arg wins, so `/app/...` also works). All output under
`<input_folder>/PoleVec/`; both YAMLs are archived into `PoleVec/logs/`.

### Stage-1 gate (why the seed CSV is respected)

```python
run_thinning = 'PoleTops' in ic and ic['PoleTops'].get('thin_data') is not None
if control['estimate_pole_tops'] is not None or run_thinning:
    estimate_pole_tops(...)          # discovery and/or thinning
pole_vectorization_wf(...)
```

- csv-seed inputconfig (`thin_data: null`) + `estimate_pole_tops:` blank →
  discovery never runs; the CSV at `pole_top_csv_file` is the sole seed.
- `inputconfig_FIRMATEK.yml` (`thin_data: 0.025`) → `estimate_pole_tops()`
  always runs; at L345 an existing non-empty CSV early-returns **only when
  thin_data is null** — otherwise it re-discovers and **overwrites the CSV**
  (LAS_NAME becomes `*_thinned.laz`). No backup is taken.
- `estimate_pole_tops.stop_after_pole_estimation` = early exit after
  discovery (distinct from `stop_after_pole_body_estimation`).

### Control YAML — key semantics (FULL vs body-only)

| Key | full | body-only | Notes |
|---|---|---|---|
| `stop_after_pole_body_estimation` | false | true | true → returns after body pass (still writes `Reference_Pole_Tops/Body_Lines_Initial_QC_Estimate.*`) |
| `process_wires/_crossarms/_transformer` | true | false | |
| `filter_wires` | true | false | true → `PoleVec/filter_wires.py` catenary/topology pass |
| `CRS` | 26911 (!) | 26917 | stamped, never validated — always re-template |
| `las_files_num_threads` | 8 (wave-scheduled) | 1 | outer per-crop pool |
| `pole_num_threads` | 1 | 8 | inner body search |
| `pole_top_csv_file` | `03_pole_vec_body/polevec_pole_tops.csv` | `poles_run55_processed.csv` | joined onto base_dir |

Required-at-startup keys (KeyError if absent; blank is fine):
`estimate_pole_tops`, `process_crossarms`, `process_transformer`,
`process_wires`, `processes_pole_body`, `OutputLAS_PointFormat`,
`OutputLAS_FileFormat`, `CRS`.

Dead / no-op keys (verified by grep): `convert_results_to_feet` (never read —
feet output rides `convert_feet_to_meters`), top-level `estimate_pole_top_z`
(code reads `inputconfig['pole_body']['estimate_pole_top_z']`),
`save_time_data`, blank `create_csv_file`. The component `classes:` lists in
the inputconfig are OUTPUT codes stamped on saved LAS, not input filters.

`parallel_processing` in the **inputconfig is ignored** — the control's block
is copied over it (`pole_vectorization.py:1673`).

### inputconfig_FIRMATEK_csv_seed.yml vs inputconfig_FIRMATEK.yml

1. `PoleTops.thin_data: 0.025 → null` (the whole point — see gate).
2. Adds `pole_body.body_search_*`: PointCONV-aware body search —
   `body_search_class_filter: [14,15,18]`,
   `body_search_class_filter_crops_dir: /data/02_pole_crop/output/crops_metric`
   (container path — matches this skill's layout when run dir is /data),
   `max_nn_m 1.0`, `refine_pole_top_z: true` (classes 15,18, radius 1.5 m).
   Missing crop file → **silent fallback to legacy RF path**.
3. Transformer ranges relaxed (r 0.16–0.44, h 0.5–1.2, density seeding).
4. Adds `wire.exclude_classes_filter` (classes 3,4,5 veg) — **NOT IMPLEMENTED
   anywhere in the code** (zero grep hits, host + volume). Wire filtering is
   actually the RF `wire_classifier` probability (threshold 0.5) or geometric
   crossarm/transformer removal. Do not rely on a veg filter.

### Model resolution (wire classifier)

Order: `1) <input_folder>/models/wire_classifier_rf_2026_03_15/estimator.joblib`
→ `2) $POLEVEC_MODELS_DIR or <dir of pole_vectorization.py>/models/...`
(= `/app/models/...` when the volume is mounted at /app — **present in the
volume**, 72 MB `estimator.joblib`) → `3) S3 download
s3://sdai-model/lidar_ml/...` into `<input_folder>/models/` (needs ~/.aws
mounted; leaves the dir behind). The HOST checkout's `models/` is empty —
mounting the host tree at /app forces path 3. `part_of_wires_poles_vs_not` and
`pole_body_classifier` follow the same pattern but are commented out (unused).

### Wire/catenary code path

Per-pole pass emits `PoleTop_<id>_{Linear,Parabola}_Wires` via the exhaustive
parabola search (`parabola_combine`): hdbscan tube clustering
(`max_tube_radius 0.25`, `min_cluster_size 5`), then survival gates —
**≥10 inliers, arc ≥2.0 m, ≥3 inliers/m of arc**, `max_gap_parabola 1.0`,
curvature `max_a 0.025`, slope −80..+45°, plus segment gates `min_pts_seg 10`,
`min_len_seg 1.5`, `max_gap 0.5`, `remove_points_near_pole_body 0.25`,
`wire_save_config.min_wire_length 1.5`.

With `filter_wires: true`, `PoleVec/filter_wires.py` then reads
`PoleVec/Temp/PCseg/*/*_pole_body.pkl`, builds the span topology from the
per-pole parabola stubs (`filter_control.topology`: `max_span 100.0`,
`plane_tol 1.0`, `line_tol 0.5`, `combined_inliers_min 0.8`) and fits a true
catenary `a*cosh((d-h)/a)+k` per matched stub pair (sampled at 0.5 m) →
`Combined/catenary.{gpkg,shp}` (attrs `poleA,poleB,wire_id,class=14,
type=cat_wire,length,sup_*`), `catenary_wire_connect.*`,
`topology/{lines,connect_points}.shp`. The catenary support filter is disabled
in shipped configs (all fits kept). Known latent bug: a legacy branch reads
`filter_control['topology']['angle_tolerance']` (absent → KeyError) but the
parabola-topology path in use does not hit it.

### Feet/metres heuristics

`convert_feet_to_meters: true` converts **only when x > 3,000,000** (CA State
Plane ftUS magnitude guard) using the international foot 0.3048 (not survey
foot — documented ~2 ppm drift), and additionally writes `Combined_feet/` +
`pole_tops_feet/`. The same x>3e6 guard protects the seed CSV from
double-conversion. Metric pipelines set it false and never touch this code.

### Output inventory

`PoleVec/logs/` (+YAML copies) · `PoleVec/Temp/PCseg/<las>/<id>_*.pkl` +
`pole_status.json` (restart marker) · `PoleVec/PCseg/<las>/PoleTop_<id>_*`
per-pole vectors+LAS · `PoleVec/Reference_Pole_Tops/Body_Lines*.{gpkg,shp,csv}`
· `PoleVec/Combined/Grp{N}_*` (N = pole index // 50) · catenary + topology
(full mode) · `Combined_feet/` (feet mode only) ·
`QC_Difficulty_Timings/timing_qc_difficulty.csv`.

Body-line consumer priority (chain report builder):
`03_pole_vec_body/Body_Lines.shp` → `PoleVec/Reference_Pole_Tops/Body_Lines.shp`
→ **`PoleVec/Combined/Grp0_Body_Lines.shp` (authoritative)**.

### Perf / memory

Measured (mobile 252-pole Firmatek, Docker/WSL2): ~65 s/pole, ~4.6 h; raising
`las_files_num_threads` above 1 there regressed 27–38 % (9P small-file I/O) —
but the shipped full control uses 8 with memory-wave scheduling against the
**container cgroup limit** (budget 0.72). Per-pole footprint 13–25 GiB on
dense crops; SIGKILL(-9) = OOM → lower threads. ~0.5–1 GB `PCseg/`
intermediates per pole. `pole_status.json` makes reruns skip finished poles.

## Related chain scripts (not part of this skill's main path)

- `merge_pole_bodies.py` — merge per-pole `peak_lines.shp`
  (`EstimatePoleTops` layout) into one `Body_Lines.shp`; `--require-wires`
  drops unattached poles.
- `filter_poles_by_wire_attachment.py` — annotate-only wire-attachment gate
  (class-14 count near pole top; radius 8 m, band +5/−2 m, min 20 pts →
  `wire_attachment.json`). Tuned for mobile density.
- `compute_span_statistics.py` — NN-span stats + recommended crop half-size
  (`max(40, ceil(p95/2)+5)` m); needs reportlab for the PDF (JSON always).
- `csv_pole_tops_to_shapefile.py` — customer pole CSV → shapefile.
- `build_pole_ingestion_report.py`, `stage3_polevec_infer.py` — chain-side
  reporting/orchestration, not needed standalone.
