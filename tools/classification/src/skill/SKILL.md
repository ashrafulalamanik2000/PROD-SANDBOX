---
name: topology-aerial-classification
description: >-
  Run the CLASSIFICATION spine of the SDAI "Topology - Aerial" chain
  (chain-full v0.3.48) on a point cloud, standalone — without the pole/curb/
  vectorization stages. Covers Stage 1 PointCONV 6-class inference (GPU),
  optional Stage 1b fine back-projection, Stage 5 road surface, Stage 6 final
  classification (road 40 / pole-body 19 / building-wall 47 overrides), and
  Stage 6v vegetation stratification, which splits PointCONV's single veg class
  into LOW (3) / MEDIUM (4) / HIGH (5) vegetation by height above ground — the
  step the shipped chain does NOT have. Use when the user wants to "classify a
  LAS/LAZ", "run the classification model", "run PointCONV", "produce the final
  classified cloud", "get low/med/high vegetation", "stratify vegetation",
  "split the veg class", "add class 3 and 4", or asks about the Topology-Aerial
  stages. Do NOT use for pole vectorization / wire spans / catenaries
  (→ pole-network-catenary), tree stem+canopy polygons (→ tree_stems),
  vegetation OUTLINE polygons (→ veg_outline), or AOI clipping (→ clip_las).
user-invocable: true
allowed-tools: [Bash, Read, Glob, Grep, Write, Edit]
---

# Topology-Aerial — Classification workflow

The classification half of the unified aerial chain, extracted so it can run on
its own cloud without the pole-vectorization machinery. Everything the launcher
`launch_topology_aerial.bat` drives inside `chain-full:v0.3.48` is here as a
sequence of stages you can run one at a time.

**The headline addition:** the shipped chain's model is a **six-class** model —
it folds ALL vegetation into class 5 and never emits class 3 or 4. Stage 6v
(`scripts/stratify_vegetation.py`, in this skill) closes that gap and produces
**low / medium / high vegetation**.

## Class scheme — what comes out

Codes follow the SDAI/PTC catalog (`SDAI_Classification_CLASSCODES_v4`), which
agrees with ASPRS on 2/3/4/5.

| Class | Label | Produced by | Obtainable? |
|---:|---|---|---|
| 0 | Never Classified | Stage 1 (densification points — see [Class 0](#class-0-is-not-a-coverage-hole)) | ✅ |
| 2 | Ground | Stage 1 | ✅ |
| **3** | **Low Vegetation** | **Stage 6v** (HAG < 0.5 m) | ✅ |
| **4** | **Medium Vegetation** | **Stage 6v** (0.5 m ≤ HAG < 2.0 m) | ✅ |
| **5** | **High Vegetation** | Stage 1 (all veg) → **Stage 6v** narrows to HAG ≥ 2.0 m | ✅ |
| 6 | Building / manmade | Stage 1 (CONFLATED: facades + fences + vehicles) | ✅ |
| 7 | Low point / noise | `mark_noise.py` (`--mark-noise`) | ✅ opt-in |
| 14 | Wire | Stage 1 | ✅ |
| 15 | Tower | Stage 1 | ✅ |
| 18 | Pole | Stage 1 | ✅ |
| 47 | Building wall / facade | Stage 4w → Stage 6 override on class 6 | ⚠️ only where facades are sensed |
| 40 | Road / Pavement | Stage 6, from Stage 5's road polygon | ❌ **needs a pretrained curb model** |
| 19 | Pole body (refined) | Stage 6, from Stage 3 pole-vec | ❌ **KNOWN-INERT** for corridor runs |
| 51 | Sidewalk | — | ❌ no available model emits it |
| 9 | Water | — | ❌ nothing in the chain produces it |

Stage 1 emits **only** `{0, 2, 5, 6, 14, 15, 18}`. Every other code is a later
refinement of one of those.

### "All classes" means these 11, not the whole ASPRS table

Three codes are genuinely blocked, and it is worth knowing why before promising
them to a client:

- **40 Road** — Stage 6 can stamp it, but the road polygon comes from Stage 5,
  which reads Stage 4 curb-skill ground+HAG artifacts, which require a
  **pretrained curb model that is not on this machine**
  (`curb_skill_pipeline.py` exits "no curb model found"). If you obtain a road
  polygon from anywhere else, pass `--road-surface <shp>` and Stage 6 applies it.
  Note the HAG-band trick Stage 5 uses (ground with −0.05 < HAG < 0.30 m) is
  **not** a substitute — on flat rural terrain that band is *all* ground, not road.
- **19 Pole body** — needs Stage 3 pole-vec in **full** mode. Documented
  KNOWN-INERT since 2026-06-10: body-only pole-vec writes no `pole_body_pts.las`
  at all, so the override never fires. PointCONV also folds every pole-like class
  into 18 regardless.
- **51 Sidewalk / 9 Water** — no model in `models.json` emits them. Both
  available models are strictly 6-class `{2,5,6,14,15,18}`.

`scripts/class_totals.py` prints the achieved coverage **and** the reason for
every expected-but-absent code, so a delivery's class list is evidence rather
than assumption.

> **Never use class 11 for road.** In the SDAI catalog 11 = "Tanks". Older
> Stage-6 docs still say class 11; the worker has used **40** since 2026-06.

### There is no low/med-veg model — and the program folds low veg into ground

Worth knowing before anyone asks for a model-based split: `models.json` lists
only two classifiers, both 6-class, and the program's own model card
(`MODEL_CARD_c1_lv.md`, promoted 2026-06-09) records a deliberate decision —
*"low scrub == ground — code 3 folds to Ground globally; only codes 4+5 remain
Vegetation"* — taken because low veg was being confused with ground in desert
data. Medium/high veg were already at 96–99 % recall.

So class 3 is, by that convention, **trained away into ground**, while
`veg_outline.py` simultaneously expects to consume `3,4,5`. Stage 6v's HAG split
is the only route to 3/4 — and if your deliverable follows the c1_lv convention
instead, run with `--low-max 0` to suppress class 3 and emit only 4/5.

Also note both models were trained on **mobile** LiDAR; `models.json` warns to
"expect a domain gap on aerial data".

## The stages

| # | Stage | Engine | In → Out |
|---|---|---|---|
| 0u | `stage0u_corridor_crop` | CPU | raw cloud + trajectory → corridor crops. *Trajectory deliveries only; skip for an AOI clip.* |
| 0e | `stage0e_outlier_removal` | CPU | crops → cleaned crops + class-7 noise sidecar (statistical k-NN SOR) |
| **1** | **`stage1_pointconv`** | **GPU (docker)** | cloud → 6-class classified @ 0.1 m |
| 1b | `stage1b_fine_classification` | CPU | classified 0.1 m + raw → 0.025 m back-projected (~4× denser). *Opt-in, default off* |
| 5 | `stage5_road_surface` | CPU | ground-classified → `road_surface.shp` |
| 4w | `stage4w_building_walls` | CPU | class-6 points → `Building_Walls.shp` |
| **6** | **`stage6_final_classification`** | **CPU** | classified + road/wall/pole-body → `*_final_classified.laz` |
| **6v** | **`stratify_vegetation`** ← *this skill adds it* | **CPU** | final classified → **veg split into 3 / 4 / 5** |

Stages 2, 3, 3.5, 4, 4t, 7, 8, 9 are the pole/curb/tree/delivery half of the
chain and are **out of scope here**. Stage 6 tolerates their absence: each
override is skipped when its input file is missing.

**Minimum path to a low/med/high-veg cloud:** Stage 1 → Stage 6 → Stage 6v.
Stage 6 is not even strictly required — 6v runs directly on Stage 1 output (you
then get no road/pole refinement, and ground stays class 2).

## Prerequisites

| Need | Where | Check |
|---|---|---|
| GPU (Stage 1 only) | NVIDIA + container toolkit | `nvidia-smi` |
| `mmworkflow` image | `750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1` | `docker image ls \| grep mmworkflow` |
| PointCONV model | `D:\LL\models\PointCONV_model_6class_v0.0.10` — the **only** model the `classify.sh` path can run (see below) | `ls /d/LL/models` |
| AWS creds | `%USERPROFILE%\.aws` (ECR pull) | `aws sts get-caller-identity` |
| Python (CPU stages) | `C:/Users/sdaiprod/.conda/envs/networkx/python.exe` — laspy 2.7, numpy 1.26, scipy 1.15, geopandas, rasterio | verified 2026-08-19 |

Do **not** use the `myenv` conda env — its rasterio is built against numpy 1.x
against an installed numpy 2.x, so `import rasterio` dies.

## Step 1 — Inspect the input first (always)

Never assume a cloud is classified. Run:

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  "C:/Users/sdaiprod/.claude/skills/topology-aerial-classification/scripts/inspect_cloud.py" \
  "<input.las>"
```

It prints point count, point format, CRS + **linear units**, and a streamed
class histogram, then tells you which stage to start from. The decision it
encodes:

- **All class 0 / 1** → unclassified. **Start at Stage 1.** Stage 6v has nothing
  to work with and will benign-skip.
- **Has 2 and 5, no 3/4** → Stage-1 (or Stage-6) output. **Go to Stage 6v** —
  this is the normal entry point for the veg split.
- **Already has 3/4/5** → already stratified. Re-run 6v only to change
  thresholds, with `--veg-classes 3,4,5`.

## Which model — dim-3 vs dim-6 is NOT a swap

There are two runners on this host and they are **not** interchangeable, because
the chain-pinned model needs input channels the standalone runner cannot compute:

| Runner | Model | Why |
|---|---|---|
| `classify.sh` + `Classification/` + `mmworkflow:v1.8.0.1` | **`PointCONV_model_6class_v0.0.10` only** | `inputconfig.yml` hard-pins it; and **neither the source tree nor the image contains `geometry_features.py`**, so there is no way to compute the dim-6 channels |
| chain-full container, or `standalone-skill-distribution/point-conv-distribution/PointCONV_TF1_Workflow/` | `..._Mobile_v0.0.18_retune_c2` (chain-pinned) | dim-**6**: needs `x,y,z,hag,linearity,verticality`, i.e. `geometry_features.py` + the dim-gated changes in `SamplePoints_Parr_Deterministic.py`, `Dataset.py`, `PointCONV_Segment.py`, `PointConv_Seg.py`, `process_combined_seg_results.py` |

> **Copying the v0.0.18 weights out of the `sdai_chain_models` volume into
> `D:\LL\models` does NOT make `classify.sh` run it.** `exp_def.p` declares
> `dim=6`; a runner with no geometry-feature code cannot feed it. Use the
> chain-full container or the `point-conv-distribution` tree instead.

Consequence to state on any deliverable: a run through `classify.sh` is **not
chain-faithful** — it uses older, dim-3-era weights, not the ones
`launch_topology_aerial.bat` pins. Both are 6-class with the same code scheme, so
everything downstream works either way; only the label quality differs, and
`models.json` warns both models are mobile-trained with an aerial domain gap.

### Running the chain-pinned dim-6 model — setup + the tile-size window

Use `point-conv-distribution`'s `scripts/run_pointconv.py`. It computes the dim-6
geometry features **on the host** and uses the GPU image only for the TF1 forward
pass, which is why the image not having `geometry_features.py` does not matter.

Two one-time setup steps, both easy to miss:

```bash
DIST=".../standalone-skill-distribution/point-conv-distribution"
IMAGE="750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1"

# 1. compiled tf_ops .so — the /workspace mount SHADOWS the image's own ops, and
#    the vendored tree ships source only. Without this: "tf_*_so.so: cannot open
#    shared object file". Use WINDOWS-style host paths here; MSYS_NO_PATHCONV=1
#    applies to the container side and will mangle a /c/... destination.
cid=$(docker create "$IMAGE")
base=/app/Mobile_Data_Tools/preprocessing/PointCONV/model_code_PointCONV/utils/tf_ops
dst="C:/.../PointCONV_TF1_Workflow/tf1/PointCONV/model_code_PointCONV/utils/tf_ops"
for op in grouping/tf_grouping_so.so interpolation/tf_interpolate_so.so sampling/tf_sampling_so.so; do
  MSYS_NO_PATHCONV=1 docker cp "$cid:$base/$op" "$dst/$op"; done
docker rm "$cid"

# 2. model weights — the container runs --pull=never and does NOT mount ~/.aws,
#    so it cannot fetch them. Copy from the docker volume (no S3 needed):
docker run --rm -v sdai_chain_models:/m -v "C:/.../models:/out" alpine \
  cp -r /m/PointCONV_model_6class_Mobile_v0.0.18_retune_c2 /out/
```

Gate: three `.so` of ~57–102 KB, and `exp_def.p` loading with `dim == 6`. At run
time TF should log `Placeholder:0 shape=(batch, 16384, 6)` — a trailing **6** is
the proof you are really on the dim-6 path.

```bash
python scripts/run_pointconv.py --run-dir <RUN> \
  --param presample_engine=host --param epsg=26917 --param batch_size=24
```

`presample_engine=host` avoids needing pixi; the host interpreter needs
`laspy numpy scipy pyyaml tqdm joblib`. `batch_size=24` suits a 24 GB card
(12 is the 16 GB default).

> **Use `scripts/presample_per_tile.py` for the presample — do not call the
> vendored batch presampler directly on more than a couple of tiles.** It runs one
> subprocess per tile, validates each `.npz`, is resumable, and retries. Then let
> `pointconv_infer.py` do only the GPU pass (it skips presampling when patches
> exist):
>
> ```bash
> python scripts/presample_per_tile.py \
>   --source-dir <RUN>/01_pointconv/source --patches-dir <RUN>/01_pointconv/patches
> python <dist>/scripts/pointconv_infer.py --run-dir <RUN> --epsg 26917 --batch-size 12
> ```
>
> On Otter Creek that gave **144/154 tiles presampled (18.3 min)** and
> **144/144 classified (7.7 min on a 4090)**. Driving the batch presampler
> directly got 2/23.

> **THREE distinct failure modes on this path. Only the first is well-behaved.**
>
> **1. Undersized tiles — deterministic, clearly reported.**
> A tile under `min_pts_in_region` = **24,576** points fails with
> `FAIL … sampler returned None (likely too few points)`. Reproducible every time,
> recorded per-file in `patches/_manifest.json`. If *every* file fails this way the
> script raises `presample: ALL n crops failed`.
>
> **2. Dense tiles — a NONDETERMINISTIC native crash. No error message.**
> The presample dies silently: no Python traceback, no manifest, a leftover
> `<stem>_thin_tmp.las` in the output dir, exit 1. The only hint is a
> `geometry_features.py:69: RuntimeWarning: All-NaN slice encountered` just before.
> On Otter Creek an **18.9 M-point tile succeeded in one run and crashed in
> another** — so it is NOT a clean size threshold, and the last
> `Starting file: …` line printed is *not* reliably the file that died (with
> `--workers > 1` the output interleaves across workers).
>
> Do **not** try to derive a max-tile-size rule from it; that is a trap — I did,
> and it was wrong. Treat it as a flaky native fault in the vendored sampler,
> triggered by very dense data (Otter Creek reaches ~1,000–4,800 pts/m² with
> ~3.6 % exact-duplicate XYZ from overlapping passes).
>
> **Root cause: cumulative process state.** `s_0_0.las` **fails as the 4th file of
> a batch but succeeds alone** (114 patches, 14.2 s, exit 0), and a 53 M tile fails
> even alone — so the state accumulates across *regions within a tile* as well as
> across tiles. That one mechanism explains every observation: a 4.4 M tile
> (12 regions) passes, 6.3 M alone passes, 18.9 M alone fails, 53 M alone fails,
> and a 6.3 M tile fails once earlier tiles have spent the budget. `--workers 1`
> and `--sample-threads 1` do not help because neither resets the state.
> **The fix is a fresh process per tile** — that is what
> `presample_per_tile.py` does. Retries help because the fault is nondeterministic
> (one tile failed attempt 1, succeeded attempt 2).
>
> **3. Too sparse AFTER the 0.1 m thinning — deterministic.**
> A small-but-dense tile can clear the 24,576 raw-point check and still fall under
> it once thinned. Fails in under a second, and retries never help — which is
> exactly how to tell mode 3 from mode 2. On Otter Creek this hit 10 tiles of
> 28 k–280 k points (769,406 pts, 0.16 %). Not fixable at that tile size; merge
> into neighbours or recover via Stage 1b.
>
> **Also: the presample can write a SILENTLY CORRUPT `.npz`.** It exists at a
> plausible size but holds an object array, and the GPU step rejects it with
> `ValueError: Cannot load file containing pickled data when allow_pickle=False`
> — after classifying every other tile. **A written file is not proof of success**;
> validate with `np.load(..., allow_pickle=False)` and check the six expected keys
> (`xyz_orig`, `xyz_class`, `patch_indices`, `patch_count`, `mask_predict`,
> `config_hash`). `presample_per_tile.py` does this and regenerates on failure.
>
> Capture worker output to a **log file** and filter on read. Piping through
> `grep` at capture time discards the one `FAIL … <exception>` line you need and
> leaves a bare `Traceback`.
>
> Always reconcile point counts across a re-tiling. Splitting only the "oversized"
> tiles and dropping "small" ones silently lost **4.29 %** of one delivery when a
> 19.97 M-point tile landed in the exclusion bucket; separate *too-small* from
> *too-big* in your accounting and assert the total against the source.

### THIS PATH SILENTLY DROPS RGB AND INTENSITY — always check

The Wave-B writer emits point format 7, so `red`/`green`/`blue` **exist** and
nothing errors — but the source colour is never carried through, so they are all
**zero**. `intensity` is dropped identically. A viewer just shows a black cloud,
which is easy to ship by accident. Measured on Otter Creek: source 99.2 %
non-zero RGB (16-bit, max 65024) → every `*_combined_0p1m.laz` **0.0 %**.

The `classify.sh` + v0.0.10 path does **not** have this bug (its `_t_raw.las`
keeps RGB). It is specific to the dim-6 `point-conv-distribution` path.

Fix — `scripts/restore_rgb.py`, an **exact** per-point join (the classified points
*are* the original points, so classified→source KD distance is **0.0 for 100 %**):

```bash
python scripts/restore_rgb.py \
  --classified-dir <RUN>/06_final_classification \
  --source-dir     <RUN_v0018>/01_pointconv/source
```

It defaults to `--max-dist 0.0`, so a tile that does not match exactly is
**skipped and reported** rather than given approximate colour. Run it after the
CPU chain (it preserves `classification`, `original_class`, `hag`,
`source_class`, `pointconv_prob` bit-identically — verified). Use
`--strip _tf1_pointconv_combined_0p1m` to repair Stage-1 output directly instead.

Otter Creek: 144/144 tiles, 0 skipped, max distance 0.0, 1.8 min, RGB
0.0 % → 99.94 % non-zero.

Check it on every dim-6 run:

```bash
python scripts/inspect_cloud.py <tile>   # or the one-liner:
python -c "import laspy,numpy as np;l=laspy.read(r'<tile>');print((np.asarray(l.red).astype(int)+np.asarray(l.green)+np.asarray(l.blue)>0).mean())"
```

### The chain-faithful output is 0.1 m thinned

This path emits `*_combined_0p1m.laz` — a **0.1 m voxel** cloud, by design. On a
4.39 M-point tile the output was **779,641 points (−82 %)**. That is what the
chain's Stage 1 natively produces; **Stage 1b** is what back-projects the labels
onto a denser cloud. Decide which you want before quoting a point count:
0.1 m chain-faithful, or full density via Stage 1b.

## Step 2 — Stage 1: PointCONV inference (GPU)

Wraps the existing `classify_las` tooling. The script auto-tiles anything over
`--max-points` (PDAL `tile`), waits for a free GPU, and verifies one
`_t_raw.las` per input.

```bash
bash "C:/Users/sdaiprod/source/agentic-workflows/Workflows/Classification_Project/skills/classify_las/scripts/classify.sh" \
  --input   "<dir containing the LAS>" \
  --output  "<out_dir>" \
  --models  "D:/LL/models" \
  --src     "C:/Users/sdaiprod/source/agentic-workflows/Workflows/Classification_Project/Classification" \
  --max-points 40000000 --tile-size 500 --background
```

- Output: `<out_dir>/class_out/<stem>/<stem>_t_raw.las`
- `--input` takes a **directory**, not a file — isolate the target in its own
  folder or every LAS beside it is classified too.
- **Sizing:** the skill's 100M default `--max-points` OOMs in post-processing on
  dense aerial data. Use ~40M and a `--tile-size` that actually splits the
  extent (a 3.8 × 3.0 km AOI needs ~500 m tiles, not 2000 m).
- Run with `--background` for anything over ~50M points and tail the log; a
  full 473M-point clip is a multi-hour GPU job.
- Reruns skip inputs that already have `_t_raw.las`, so a crashed run resumes.

## Step 2b — One command for everything after Stage 1

`scripts/run_classification.py` runs the whole CPU half of the chain and emits
one final cloud per source carrying **every class this chain can produce**. Use
this instead of driving Stages 4w / 6 / 6v by hand.

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  "C:/Users/sdaiprod/.claude/skills/topology-aerial-classification/scripts/run_classification.py" \
  --stage1-dir "<out_dir>/class_out" \
  --run-dir    "<run_dir>" \
  --epsg 26917 \
  --mark-noise
```

What it does, in order:

1. **Assembles the run-dir** Stage 6 expects — hardlinks (not copies) the
   Stage-1 tiles into `01_pointconv/combined_outputs/`.
2. **Stage 4w** → `04w_building_walls/Building_Walls.shp` (class 47).
3. **Stage 6** → `06_final_classification/*_final_classified.laz` with the wall
   (and road, if you supplied one) overrides baked in + `original_class`.
4. **Stage 6v** → veg split into 3 / 4 / 5.
5. **`--mark-noise`** → class 7 on statistical outliers.
6. Prints the **aggregate class histogram** plus the reason for every
   expected-but-absent class, and writes `classification_chain_summary.json`.

Each step degrades gracefully: no walls found → Stage 6 skips the 47 override;
Stage 6 produces nothing → the veg split falls back to the Stage-1 clouds. Skip
steps with `--skip-walls` / `--skip-stage6` / `--skip-veg`.

`--epsg` matters: **Stage 1 does not carry the input CRS into `_t_raw.las`**, so
the wall extractor cannot infer one and 6v cannot auto-detect units. The driver
defaults to `26917` (UTM 17N) and passes `--units m` for the same reason.

### Class 7 is labelled, not removed

The chain's Stage 0e *removes* isolated points before inference (good for
classification quality, but then no class 7 reaches the deliverable).
`mark_noise.py` runs the same statistical k-NN isolation test (k=16,
std_ratio=6.0, 1.0 m floor — Stage 0e's defaults) on the **classified** cloud and
relabels outliers to 7, keeping the points. Ground and road are protected by
default (`--protect-classes 2,40`) — a sparse pavement edge is not noise.

Memory: the k-NN **tree** is in-memory (~30 B/point), but the query is chunked
(`--query-chunk`, 10 M) because the full `(N, k+1)` distance matrix is the real
wall — at 126 M points it is **24 GiB of float64 that only ever reduces to one
mean per point**. With chunking, a 126 M-point tile peaks around 12 GiB, so
`--max-points` defaults to 400 M. Lower it on a small host.

> A tile that exceeds `--max-points` is **skipped**, and its output then carries
> no class 7 at all. Check `noise_marking_summary.json` for `status:
> "too_large"` — a partial class-7 is worse than none, because the delivery looks
> noise-checked when part of it never was.

## Step 3 — Stage 6: final classification (optional, CPU)

Only worth running when you have Stage 5 road / Stage 4w wall / Stage 3 pole-vec
products to overlay. It reads a **chain run dir**, not loose files:

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  "C:/Users/sdaiprod/source/agentic-workflows/Greg_Sandbox/agentic_development/Claude/projects/chain-orchestrator/scripts/final_classified_pointcloud.py" \
  --run-dir "<run_dir>" --pole-match-radius 0.05 --chunk-size 5000000
```

Reads `01_pointconv/combined_outputs[_0p025m]/`, `05_road_surface/road_surface.shp`,
pole-body LAS; writes `06_final_classification/*_final_classified.laz` +
`final_classification_summary.json`. It preserves the pre-override label in an
`original_class` extra dimension.

Known-inert overrides in an aerial/corridor run: **class 19** (the pole-body
glob expects the full pole-vec layout, which body-only pole-vec does not
create) and **class 40 / 47** whenever Stage 5 / 4w did not run.

## Step 4 — Stage 6v: low / medium / high vegetation ← the deliverable

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  "C:/Users/sdaiprod/.claude/skills/topology-aerial-classification/scripts/stratify_vegetation.py" \
  --run-dir "<run_dir>"
```

or on loose files (a single LAS/LAZ or a directory):

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  ".../scripts/stratify_vegetation.py" \
  --input "<classified.las>" --out-dir "<out>" --suffix _veg
```

**Straight off a tiled Stage-1 run.** Stage 1 nests every tile in its own
subdirectory (`class_out/<tile>/<tile>_t_raw.las`), so point `--input` at
`class_out` and 6v finds them all — it tries top-level `*.las`/`*.laz` first,
then falls back to `*/*_t_raw.la[sz]`:

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  ".../scripts/stratify_vegetation.py" \
  --input "<out_dir>/class_out" --out-dir "<veg_out>" --suffix _veg --units m
```

Use `--input-pattern '**/*.laz'` for any other layout. Each tile gets its own
DEM, so HAG is very slightly discontinuous across tile seams; merge the tiles
first (Stage 7 style) if a seamless HAG matters.

**How it works.** It computes height above ground from the cloud's *own* ground
points — a minimum-Z DEM over classes `2,40` at `--dem-cell` (1.0 m), gap-filled
by nearest populated cell — then re-labels only the veg points:

```
hag < --low-max (0.5 m)  → 3  Low Vegetation
hag < --med-max (2.0 m)  → 4  Medium Vegetation
otherwise                → 5  High Vegetation
```

Class 40 is included in the ground set deliberately: road points *are* ground,
Stage 6 just refined their label.

**Guarantees** (asserted in the fixture tests):

- Only points already in `--veg-classes` change. Ground, road, wire, tower,
  pole, building and wall labels pass through untouched.
- `original_class` is **created only if absent** — when Stage 6 already made it,
  that dimension holds the true PointCONV base label and is left alone.
- A `hag` float32 extra dimension is written for every point (QC / re-thresholding
  without recomputing). `--no-hag` skips it.
- In-place writes are atomic (`.partial` + `os.replace`), and the streaming path
  refuses to publish an output whose point count does not match the source.

**Key flags**

| Flag | Default | Notes |
|---|---|---|
| `--low-max` / `--med-max` | 0.5 / 2.0 | **Always in METRES**, converted into data units automatically |
| `--dem-cell` | 1.0 m | Coarser smooths real relief; finer leaves more empty cells for the NN fallback |
| `--ground-classes` | `2,40` | |
| `--veg-classes` | `5` | Use `3,4,5` to re-stratify at new thresholds |
| `--units` | `auto` | Reads the header CRS; **falls back to metres with a WARN when there is no CRS** |
| `--streaming` | `auto` | Streams above `--stream-threshold` (40M) points |
| `--out-dir` / `--suffix` | in place | In place keeps Stage 7/8's `*_final_classified.laz` glob working |

Exit codes follow the stage-package contract: **0** ok, **3** benign
(no veg, or too little ground to build a DEM — the cloud is left untouched),
**1** error.

### Units are load-bearing

The aerial chain has a feet↔metre round-trip for ftUS deliveries, so a cloud can
legitimately be in survey feet. `--units auto` reads the CRS and scales the
thresholds. **A cloud with no CRS in its header silently assumes metres** — if
it is really in feet, every veg point lands in class 5 (2.0 m read as 2.0 ft
puts almost everything above the high cutoff). Check the WARN line, and pass
`--units ft` when the header has no CRS but the data is imperial.

### Scale

`--streaming auto` reads clouds under 40M points whole and switches to a
two-pass streaming path above that (pass 1 builds the DEM, pass 2 relabels and
writes), so RAM stays bounded by `--chunk-size` regardless of file size. The
streaming path is verified byte-identical to the in-memory path. It reads the
source twice and needs free disk equal to the source size for the temp output —
budget for that on a 16 GB cloud.

## Verify the result

```bash
"C:/Users/sdaiprod/.conda/envs/networkx/python.exe" \
  ".../scripts/inspect_cloud.py" "<out>/<stem>_veg.laz"
```

Sanity checks on a corridor/AOI cloud:

- Classes 3, 4 and 5 all present, and 3 + 4 are a **minority** of total veg —
  most sensed vegetation is canopy. A result that is ~100 % class 3 means the
  DEM sat above the data (or units are wrong); ~100 % class 5 means the
  thresholds were read in the wrong unit.
- Ground-point HAG should centre on ~0. `veg_stratification_summary.json`
  carries per-source counts, HAG stats and the before/after class histograms.
- Class 2/40/14/15/18 counts must be **unchanged** from the input histogram.
- Wire (14) HAG should land at plausible attachment heights (~6–8 m on a
  distribution corridor) — it is a free correctness check on the DEM.
- A strongly **negative** min HAG (e.g. −3.8 m) means the nearest-populated-cell
  fallback reached across a coverage gap and matched ground at a different
  elevation. Those points still land in class 3, which is the safe outcome, but a
  large negative tail plus a low "populated cells" percentage means the tile has
  patchy ground — coarsen `--dem-cell` or crop to the covered swath.

**Measured on Otter Creek** (250 m tile, 12.6 M pts, EPSG:26917): Stage 1 gave
84.5 % ground / 15.2 % class-5 veg; Stage 6v split 1.92 M veg points into
23.0 % low / 41.5 % medium / 35.5 % high in 6.7 s, ground HAG median 0.08 m,
wire HAG median 6.3 m, canopy to 20.2 m.

**Full delivery** (473 M pts, 3.8 × 3.0 km, 23 tiles at 400 m): Stage 1 25 min on
a 4090; the whole CPU chain 6.5 min. Ten classes, point count preserved exactly
(Stage 1 itself drops ~4 k points in voxelization; Stages 6/6v/7 drop none).

### Sanity-check Stage 1's labels before trusting the refinements

Everything after Stage 1 refines Stage 1's output, so a Stage-1 error propagates
silently. Both available models are **mobile**-trained and `models.json` warns of
an aerial domain gap, so on aerial data check the base histogram for plausibility
before shipping: on the Otter Creek delivery class 6 (building/manmade) came out
at **7.29 %** — high for the terrain, and class 6 is documented as CONFLATED
(facades + fences + vehicles). There is no truth layer here to score against, so
treat the class-6-derived products (47 walls) as provisional. `score_lines_vs_truth.py`
and `compute_pointconv_iou.py` in the chain scripts are the tools if you do get truth.

### Reading a class-47 count sanity-check

Class 47 came out at **5.19 % of 473 M points from only 2.3 km of wall lines** —
which looks impossible until you account for the geometry. The Stage-6 wall
override is an **XY-only** test (facades are vertical), so *every* class-6 point
in the facade's vertical column becomes 47, including roof points directly above
the wall line. A 6.5 m facade concentrates its whole vertical extent into a 0.8 m
XY ribbon, so the projected density there is orders of magnitude above the
cloud's ~40 pts/m² average.

Verified rather than assumed: every class-47 point measured ≤ 0.400 m from a wall
line (the exact `--wall-match-radius`) and 100 % had `original_class == 6`. The
override is behaving as designed — but **47 here means "class-6 point on a
building footprint", not strictly "facade"**. Treat it as a building refinement,
and tighten `--wall-match-radius` if you need facade-only.

## Class 0 is not a coverage hole

~35 % of Aecon corridor outputs are class 0. These are densification points, not
gaps: 99.5 % have a labelled neighbour within 0.05 m (median 0.000 m — tile-overlap
duplicates where only one copy got a label). The remedy is Stage 1b's 1-NN
back-projection, not a re-run of Stage 1. Stage 6v leaves class 0 alone; run
Stage 1b first if you want those points labelled and stratified.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Stage 1 hangs at `waiting for '<name>' to release the GPU` | `classify.sh` treats **any** container started with `--gpus all` as holding the GPU. A long-lived service (e.g. `orchestrator-api-1`, up for days at 0 % GPU) trips it forever | Confirm the GPU really is idle (`nvidia-smi`), then re-run with **`--no-gpu-wait`**. Do not stop unrelated services |
| Stage 1 output has no CRS | The classifier does not carry the input CRS into `_t_raw.las`, even when the input had one | Pass `--units m` / `--units ft` explicitly to 6v, or re-stamp the CRS (`subset_las_to_bbox.py --stamp-crs EPSG:26917`) |
| 6v reports `no_veg`, exit 3 | Cloud is unclassified (all class 0) or has no class 5 | Run Stage 1 first — see `inspect_cloud.py` |
| 6v reports `no_ground`, exit 3 | Fewer than 100 points in classes 2/40 | Cloud is veg-only, or ground was never classified. Nothing to do — the file is left untouched |
| Everything becomes class 5 | Data in feet, thresholds read as metres | Pass `--units ft` |
| Everything becomes class 3 | `--dem-cell` too coarse over strong relief, or a low outlier dragged the min-Z DEM down | Run Stage 0e outlier removal, or lower `--dem-cell` |
| `MemoryError` in 6v | `--streaming off` on a huge cloud | Drop the flag (auto) or use `--streaming on` |
| Stage 1 container exits rc=137 | Post-processing OOM, tile too big | Lower `--max-points` to ~40M, `--tile-size` to ~500 |
| `value 40 is greater than allowed (max: 31)` | Legacy point format ≤5 can't hold class 40 | Stage 6 auto-converts to pf 6/7. 6v alone never writes >31 so it keeps the source format |
| `unauthorized` on docker pull | ECR token expired | `aws ecr get-login-password --region us-west-2 \| docker login --username AWS --password-stdin 750433818015.dkr.ecr.us-west-2.amazonaws.com` |

## Downstream

The stratified cloud is what `veg_outline` expects — it rasterizes classes
`3,4,5` and skips absent ones, so before 6v it was silently working off class 5
alone. `tree_stems` still keys on class 5 (canopy), which 6v narrows to genuine
canopy (HAG ≥ 2 m) — an improvement to its input, not a break.

## References

- [`references/stages.md`](references/stages.md) — full stage graph, run-dir
  layout, upstream source paths, and the doc-vs-code drifts worth knowing.
- Launcher this was extracted from: `Downloads/CLASSIFICATION MODEL/launch_topology_aerial.bat`
  (`chain-full:v0.3.48`, `CHAIN_UI_DEFAULT_WORKFLOW=Topology_Aerial`).
