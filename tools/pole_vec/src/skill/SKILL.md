---
name: pole-network-catenary
description: >-
  Run the POLE + WIRE half of the SDAI "Topology - Aerial" chain
  (chain-full v0.3.48) standalone: Stage 2 pole discovery/detection +
  per-pole cropping, Stage 3.5 pole-network topology (wire-stub graph →
  nodes/edges shapefiles), and Stage 3 pole vectorization in FULL mode
  (GPU docker) — pole-body centerlines, linear wires, parabola wire fits,
  inter-pole CATENARY spans, crossarms, transformers, connection points and
  a combined DXF. Input is a classified cloud carrying class 14 (wire) and
  18 (pole) — the output of topology-aerial-classification. Use when the
  user wants to "extract poles", "vectorize poles", "get wire spans",
  "fit catenaries", "pole network topology", "run pole-vec", "poles and
  wires from a classified LAS", or "utility network from LiDAR". Do NOT
  use for classification itself (→ topology-aerial-classification), tree
  polygons (→ tree_stems), or vegetation outlines (→ veg_outline).
user-invocable: true
allowed-tools: [Bash, Read, Glob, Grep, Write, Edit]
---

# Pole network + catenary — the vectorization half of Topology-Aerial

Extracted from `chain-full:v0.3.48` (the `launch_topology_aerial.bat` chain) so
the pole/wire stages run on their own, without the orchestrator. Companion to
`topology-aerial-classification`, which produces this skill's input.

## What comes out

| Product | Stage | File |
|---|---|---|
| Detected pole tops + per-pole LAS crops | 2 | `crops/<Pole_N>.las`, `detection/pole_detection_results.csv`, `<shp_dir>/<stem>_processed.{csv,shp}` |
| Pole network graph (first wire topology) | 3.5 | `<out>/<proj>_pole_network_{nodes,edges}.shp` + QC JSON + preview PNG |
| Pole-body 3D centerlines | 3 | `PoleVec/Combined/Grp0_Body_Lines.{shp,gpkg}` |
| Straight wire segments | 3 | `Grp0_Linear_Wires.shp` |
| Parabola (sag) wire fits per pole | 3 | `Grp0_Parabola_Wires.shp` |
| **Inter-pole catenary spans** | 3 | `PoleVec/Combined/catenary.{shp,gpkg}` + `catenary_wire_connect.*` + `PoleVec/topology/{lines,connect_points}.shp` |
| Crossarms / transformers / guy wires | 3 | `Grp0_Crossarm_Lines.shp`, `Grp0_Transformer_Lines.shp`, `Grp0_Guy_Lines.*` |
| Per-class extracted point clouds | 3 | `Grp0_{Poles,Wires,Crossarms,Transformers,Ground}.las` |
| CAD export | 3 | `Grp0_combined.dxf` |

`Grp0` = first group of ≤50 poles (`maximum_poles_in_group: 50`); a 51st pole
starts `Grp1_*`.

## Prerequisites

| Need | Where | Check |
|---|---|---|
| Classified cloud with classes **14 + 18** | `topology-aerial-classification` output, or a delivered classified LAZ | `inspect_cloud.py` histogram |
| **Metric CRS** | everything downstream assumes metres | header CRS |
| Python (CPU stages) | `C:/Users/sdaiprod/.conda/envs/networkx/python.exe` — laspy 2.7 (LazrsParallel), geopandas, scipy, sklearn, networkx — verified 2026-08-23 | import test |
| `mmworkflow` image (Stage 3 only) | `750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1` (`latest` = same id) | `docker image ls` |
| `sdai_chain_polevec` volume (Stage 3) | pole-vec code **+ baked wire classifier** `models/wire_classifier_rf_2026_03_15/estimator.joblib` (72 MB) — mounted at `/app`, no S3/AWS needed | `docker run --rm -v sdai_chain_polevec:/pv alpine ls /pv/models` |
| NVIDIA GPU (Stage 3 only) | `--gpus all` | `nvidia-smi` |

Source trees (host, all verified md5-identical to the volume where it matters):

- pole-cropping: `C:\Users\sdaiprod\source\agentic-workflows\Greg_Sandbox\agentic_development\Claude\projects\pole-cropping\croping_around_poles\pipeline.py`
- discovery: `...\projects\pole-vectorization\scripts\reextract_poles_loose_bridge_multi.py`
- network: `...\projects\chain-orchestrator\scripts\estimate_pole_network.py` (+ `scripts/pole_network/`)
- pole-vec: `...\projects\pole-vectorization\PoleVec_Standalone\` (host `models/` is EMPTY — use the volume)

## Units: read this first

`pipeline.py`'s flags are unit-agnostic (values are in the data's CRS units;
its *defaults* are ftUS). `estimate_pole_network.py` and every pole-vec
threshold are **hard metric**. On a metric cloud pass metric values everywhere;
on a ftUS delivery follow the chain's pattern (crop in feet, reproject crops to
metric before Stage 3) — this skill's tested path is **metric end-to-end**.

## Step 0 — inspect

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  "C:/Users/sdaiprod/.claude/skills/topology-aerial-classification/scripts/inspect_cloud.py" "<cloud>"
```

Need: class 18 present (discovery + body seeds) and class 14 present (network
stubs + wire fits). No 14/18 → run classification first. Also note the CRS —
every command below takes it explicitly.

## Step 1 — pole discovery (skip if the customer supplied pole tops)

DBSCAN clustering of class-18 points → one seed point per pole:

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  "C:/Users/sdaiprod/source/agentic-workflows/Greg_Sandbox/agentic_development/Claude/projects/pole-vectorization/scripts/reextract_poles_loose_bridge_multi.py" \
  "<dir with classified las/laz>" "<work>/poles/poles_candidates.shp" \
  --pattern "<name>.laz" --epsg <EPSG int> --min-pts 15 --min-z-range 1.5
```

- `--pattern` defaults to `*_tf1_pointconv_combined_0p1m.las` (chain tiles);
  falls back to `*.las` — pass it explicitly for a loose file. **`.laz` needs
  an explicit pattern** (the fallback only globs `.las`).
- DBSCAN is `eps=2.0 m, min_samples=10` on **combined XY across all files**.
  Fine at ~200k class-18 points; do NOT feed a dense 0.025 m cloud (a 94 GB
  OOM in production) — thin first if class-18 alone exceeds ~10M points.
- Put the shapefile in its own dir: Step 2 writes `<stem>_processed.{csv,shp}`
  **next to it**, and that CSV is the Stage-3 seed.
- Customer pole list instead: CSV → `csv_pole_tops_to_shapefile.py`
  (chain-orchestrator/scripts), SHP → use as-is (needs a CRS + a
  `Pole`/`pole_id` column).

## Step 2 — detect + crop (CPU, the Stage-2 pipeline)

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  "C:/Users/sdaiprod/source/agentic-workflows/Greg_Sandbox/agentic_development/Claude/projects/pole-cropping/croping_around_poles/pipeline.py" \
  --input-dir "<dir with the classified cloud>" \
  --pole-shapefile "<work>/poles/poles_candidates.shp" \
  --output-dir "<work>/02_pole_crop/output" \
  --data-srs EPSG:<epsg> \
  --half-size 50 --search-radius 5 --voxel-size 0.025 \
  --min-pole-height 4 --max-pole-height 30 \
  --max-points-per-tile 40000000 --max-workers 8
```

Metric knob cheat-sheet (defaults are ftUS — override ALL of these on metric
data): `--half-size` 40–70 m (must reach past mid-span so adjacent crops
overlap — for a defensible value run `compute_span_statistics.py` first:
`half = ceil(p95_nn/2)+5`), `--search-radius` 5, `--voxel-size` 0.025,
`--min-pole-height` 4–6, `--max-pole-height` 25–30, `--col-size 0.5` (default
is already metric-friendly).

- `--input-dir` is a **directory** (recursive); isolate the target cloud.
- Crops keep the source `classification` field, so cropping a *classified*
  cloud makes Stage 3's reclassify pre-step unnecessary. (The chain crops raw
  and reclassifies afterwards because classified input measurably lowered its
  detection rate — "CT2". If detection comes out poor, that is the first
  suspect: re-crop from the raw cloud and reclassify with
  `reclassify_crops_with_pointconv.py --crops-dir ... --classified-las ...
  --out-dir ... --max-nn-distance-m 0.2`.)
- Do **not** pass `--compress-intermediates`: pole-vec's `las_end_str: .las`
  makes `.laz` crops invisible (silent zero-match).
- Corridors are ON by default (`corridor_spans/` + a proximity-based
  `corridor_manifest.csv`). These spans are **first-estimate topology, not
  wire-derived** — Stages 3.5/3 supersede them.
- Outputs: `crops/<Pole_N>.las`, `crops/crop_manifest.csv` + `crop_boxes.shp`,
  `detection/pole_detection_results.csv` (+ `pole_tops_found.shp`), and —
  **next to the input pole shapefile** — `<stem>_processed.csv` with exactly
  `Pole, TOP_X, TOP_Y, TOP_Z, LAS_NAME`. That CSV is the seam to Stage 3.
- Undetected poles still get a CSV row (original XY, median-of-detected Z) as
  long as their crop exists; only crop-less poles are dropped.

## Step 3 — network topology (CPU, Stage 3.5) — cheap, run before pole-vec

`estimate_pole_network.py` extracts class-14 wire *stubs* around each pole top
and builds the span graph (directional match + component bridging + near-dup
QC). It hard-requires per-pole LAS named `<pole_id>_tf1_pointconv_combined_0p1m.la[sz]`
— hardlink the crops to that convention first:

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  "C:/Users/sdaiprod/.claude/skills/pole-network-catenary/scripts/link_crops_for_network.py" \
  --crops-dir "<work>/02_pole_crop/output/crops" --out-dir "<work>/network_in"

"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  "C:/Users/sdaiprod/source/agentic-workflows/Greg_Sandbox/agentic_development/Claude/projects/chain-orchestrator/scripts/estimate_pole_network.py" \
  --pole-tops "<work>/poles/poles_candidates_processed.shp" \
  --waveb-dir "<work>/network_in" \
  --waveb-crs EPSG:<epsg> --output-crs EPSG:<epsg> \
  --output-dir "<work>/utility_topology" --project-name <name>
```

- `--output-crs` defaults to **EPSG:6424 ftUS** (Firmatek convention) — set it
  to the delivery CRS explicitly or the nodes/edges come out reprojected.
- Only needs `x,y,z,classification` (no extra dims). Pole IDs come from the
  first of `Pole/pole_id/PoleID/pole` columns; a bare id gets `Pole_`
  prefixed, so IDs and crop names must agree.
- Tunables: `--stub-radius-m 20`, `--max-span-m 100` (raise for transmission
  spans), `--angle-tol-deg 20`, `--min-stub-points 8`, `--min-stub-length-m 3`,
  bridging `--bridge-max-span-mult 2 --bridge-angle-mult 1.5`.
- Exits nonzero if the pole shapefile has no CRS, zero poles match, or the
  graph is empty. Poles with no matching LAS are silently skipped — check the
  log line for skip counts.

## Step 4 — pole vectorization FULL mode (GPU docker, Stage 3)

`prepare_polevec_run.py` stages everything pole-vec expects and prints the
exact docker command:

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  "C:/Users/sdaiprod/.claude/skills/pole-network-catenary/scripts/prepare_polevec_run.py" \
  --crops-dir "<work>/02_pole_crop/output/crops" \
  --processed-csv "<work>/poles/poles_candidates_processed.csv" \
  --run-dir "<work>/polevec_run" --crs <epsg int>
```

It hardlinks crops into `<run>/02_pole_crop/output/crops_metric/`, writes the
seed CSV to `<run>/03_pole_vec_body/polevec_pole_tops.csv` (validating every
`LAS_NAME` resolves to a crop), and writes `<run>/PoleVec_control_runtime.yml`
— the firmatek FULL control with **`CRS:` set to your EPSG** (the shipped
control says 26911 and pole-vec never validates it against the crops; a wrong
value mislabels every output silently).

Then run (the model is baked in the volume — no AWS needed):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --gpus all --shm-size=8gb \
  -v sdai_chain_polevec:/app \
  -v "<work>/polevec_run":/data \
  750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1 \
  python /app/PoleVec_workflow.py \
    --input_inputconfig      /app/inputconfig_FIRMATEK_csv_seed.yml \
    --input_folder           /data \
    --input_inputcontrol     PoleVec_control_runtime.yml \
    --input_segmented_folder 02_pole_crop/output/crops_metric
```

- `--input_inputcontrol` is joined onto `--input_folder`, so the relative name
  picks up the runtime control from the run dir. The csv-seed inputconfig has
  `thin_data: null`, and with the seed CSV present pole-vec **skips its own
  pole-top discovery entirely** (the "Path C" behavior).
- Run it with `run_in_background` / a named container and poll — budget
  **~1 min/pole** (measured ~65 s/pole on mobile-density crops; aerial crops
  are lighter). Restartable: `PoleVec/Temp/PCseg/pole_status.json` marks done
  poles; delete `PoleVec/` for a clean rerun.
- Memory is wave-scheduled against the container cgroup limit; a SIGKILL(-9)
  means OOM — lower `las_files_num_threads` in the runtime control (8 → 2).

### Success gate — exit 0 LIES

pole-vec exits 0 even when it vectorized nothing. Verify:

```bash
ls "<run>/PoleVec/Combined/"Grp0_Body_Lines.shp \
   "<run>/PoleVec/Combined/"Grp0_Parabola_Wires.shp \
   "<run>/PoleVec/Combined/"catenary.shp
```

plus feature counts via geopandas. The catenary layer only exists because the
FULL control sets `filter_wires: true` (it fits `a*cosh((d-h)/a)+k` per
matched stub pair across the span graph, `max_span: 100 m`). Body-only runs
(`PoleVec_control_body_only_pointconv.yml`) produce none of the wire layers.

### Wire-fit density gates (why a span can be missing)

A parabola/catenary survives only with **≥10 inliers, ≥2.0 m arc, ≥3 points/m
of arc** (plus `min_pts_seg 10`, `max_gap 0.5–1.0 m`, curvature `max_a 0.025`,
slope −80°..+45°). Sparse aerial wire returns (~0.05 % of points) fit fewer
spans than mobile data — the chain runs Stage 1b (0.025 m fine
back-projection) before FULL pole-vec for exactly this reason. If spans are
missing, check class-14 counts per crop before blaming the fitter.

## Verify the result

- `Grp0_Body_Lines`: one centerline per detected pole, near-vertical, length ≈
  pole height from `pole_detection_results.csv`.
- `catenary.shp`: `poleA`/`poleB` attrs join back to seed CSV ids; span
  lengths should match `utility_topology` edge lengths (±). Wire attachment
  heights (Z of `catenary_wire_connect`) at plausible heights (6–12 m
  distribution, more for transmission).
- Cross-check Stage 3.5 vs Stage 3: 3.5's edges are stub-direction estimates;
  catenary spans are the wire-derived truth. Big disagreement → look at the
  class-14 recall in that area.
- `Grp0_combined.dxf` opens in CAD with all layers.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| pole-vec logs `Skipping <file> ... not associated with any pole top` / `[SEG_MATCH] Skipped n/N` | `LAS_NAME` in seed CSV ≠ crop filename (or `.laz` crops vs `las_end_str: .las`) | `prepare_polevec_run.py` validates this; re-check after any manual rename |
| Zero crops found by pole-vec | crops are `.laz` | re-crop without compression, or convert |
| `KeyError: 'estimate_pole_tops'` | key deleted from control YAML (empty value is required, absence is not) | keep the key, leave it blank |
| Everything ~3.28× wrong | feet data with `convert_feet_to_meters: false` — or the magnitude heuristic (`x > 3e6`) misfired | metric path: reproject crops to metric first |
| Wire pass finds nothing | class 14 sparse in crops; or crops carry class 0 (raw crop, no reclassify) | check crop histograms; reclassify or densify (Stage 1b) |
| `estimate_pole_network.py` skips all poles | naming convention | `link_crops_for_network.py` |
| Nodes/edges in wrong CRS | default `--output-crs EPSG:6424` | pass the delivery CRS |
| S3 download attempted / `NoCredentialsError` | host checkout mounted at `/app` (its `models/` is empty) | mount `sdai_chain_polevec:/app` |
| Detection finds far fewer poles than expected | classified-input detection penalty (CT2) | re-crop from raw + reclassify |

## Measured baseline — SITE_A01 (first validation, 2026-08-24)

169M-pt aerial AOI (1.2 × 1.0 km, 216 pts/m², EPSG:26917, class 14 = 0.05 %):
discovery 264 poles in ~10 s · Stage 2 (5 tiles, crop+detect+corridors)
**5.3 min**, 226/264 detected · Stage 3.5 **27 s**, 212 spans/86 components ·
pole-vec FULL **22.8 min** (≈5 s/pole on the 4090) → 222 body lines,
295 parabola wires, 40 linear wires, 248 crossarms, 3 transformers, 7 guys,
985 connection points, **17 catenary spans**, 5 DXFs. Note the catenary count
vs 212 stub-spans: the ≥3 pts/m arc gate is brutal on 0.05 % aerial wire
returns — densify first (Stage 1b) if the client needs span-complete
catenaries.

## Provenance & deeper internals

- Extracted 2026-08-23 from `chain-full:v0.3.48` / `launch_topology_aerial.bat`
  on SDAI-ML04; first validated on the SITE_A01 (BD_CAM_P3) delivery.
- [`references/internals.md`](references/internals.md) — control/inputconfig
  key-by-key semantics, model resolution order, catenary code path, chain
  stage contracts, and the doc-vs-code drifts (dead YAML keys, the
  unimplemented `exclude_classes_filter`, ftUS heuristics).
