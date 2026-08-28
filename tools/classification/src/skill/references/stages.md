# Topology-Aerial — stage graph, provenance, and known drifts

Reference companion to `SKILL.md`. This records where each stage came from, the
run-dir contract, and the doc-vs-code discrepancies that cost time when you hit
them cold.

## Where this came from

The skill was extracted from `launch_topology_aerial.bat`
(`Downloads/CLASSIFICATION MODEL/`), which is only a launcher: it checks Docker +
AWS creds, pulls `chain-full:v0.3.48` from ECR, picks a free host port, and
serves a web UI with `CHAIN_UI_DEFAULT_WORKFLOW=Topology_Aerial`. All stage logic
lives inside the image.

Topology-Aerial is the **unified** aerial workflow: it auto-detects the delivery
shape and runs the matching stage set —

- a **vehicle-trajectory corridor** delivery (folder with a `.lst` + `LAZ/`), or
- an **aerial customer pole-top** delivery (pole CSV / `*_POLES.shp` + cloud),

with an automatic feet↔metre round-trip when the input is in feet. That round-trip
is why `stratify_vegetation.py` takes unit handling seriously.

`v0.3.48` specifically fixed stage0u + stage0e OOMs (the corridor-crop polygon
and the outlier k-NN query are memory-bounded), proven on a 255M-point OAKVILLE2
delivery that SIGKILLed on v0.3.47.

Pinned model: **`PointCONV_model_6class_Mobile_v0.0.18_retune_c2`**, baked into
the image (the launcher's `MODELS_C1`). `CHAIN_MODELS_DIR` overrides with a host
folder. The host also has the older `PointCONV_model_6class_v0.0.10` at
`D:\LL\models`, which is what the standalone `classify.sh` path uses by default.

### Source of truth on disk

| Thing | Path |
|---|---|
| Orchestrator (all StageSpecs) | `source/agentic-workflows/Greg_Sandbox/agentic_development/Claude/projects/chain-orchestrator/chain_orchestrator.py` |
| Stage workers | `…/chain-orchestrator/scripts/` |
| Stage packages (manifests, readiness, launchers) | `…/chain-orchestrator/stage_packages/<stage>/` |
| Per-stage docs | `…/chain-orchestrator/docs/stages/` |
| Aecon stage order + run modes | `…/chain-orchestrator/stage_packages/AECON_STAGES.md` |
| Presets (canonical orders) | `…/chain-orchestrator/presets/` |
| Standalone skill distributions (the packaging pattern) | `source/agentic-workflows/Greg_Sandbox/standalone-skill-distribution/` |
| PointCONV standalone classifier | `source/agentic-workflows/Workflows/Classification_Project/` |

> The local `chain_orchestrator.py` checkout predates the Topology-Aerial
> workflow name (grep finds no `Topology_Aerial` in it) — that workflow was added
> in chain-full v0.3.45. The stage *set* it runs is the Aecon order below; only
> the auto-detect wrapper is newer than this checkout.

## Full chain order (canonical, from `presets/aecon_oakville2_55_full.yml`)

Classification-spine stages are **bold**; the rest are the vectorization half.

| # | Stage | Engine | Requires → Produces |
|---|---|---|---|
| 1 | `stage0t_trajectory` | cpu | trajectory source (`.lst`/`.csv`) → `trajectory.shp` |
| 2 | `stage0u_corridor_crop` | cpu | raw cloud + trajectory → corridor crops + manifest |
| 3 | `stage0e_outlier_removal` | cpu | crops → cleaned crops + class-7 noise sidecar |
| 4 | **`stage1_pointconv`** | **docker GPU** | cloud → classified 0.1 m |
| 5 | **`stage1b_fine_classification`** | cpu | raw + classified → 0.025 m fine-classified *(opt-in)* |
| 6 | `stage2_pole_crop` | cpu | classified → pole candidates |
| 7 | `stage3_pole_vec` | **docker GPU** | per-pole crops → pole body lines |
| 8 | `stage4_curbs` | cpu | classified → curblines + ground-classified |
| 9 | `stage4t_tree_trunk_canopy` | cpu | classified → tree stems + canopy |
| 10 | **`stage4w_building_walls`** | cpu | class-6 points → `Building_Walls.shp` |
| 11 | **`stage5_road_surface`** | cpu | ground-classified → `road_surface.shp` |
| 12 | **`stage6_final_classification`** | cpu | classified + road surface → `*_final_classified.laz` |
| — | **`stratify_vegetation` (6v)** | cpu | final classified → **veg split 3/4/5** ← added by this skill |
| 13 | `stage9_solv3d_project` | cpu | deliverables → SOLV3D project |

Optional / preset-dependent: `stage3_5_pole_network`, `stage0a_pole_csv`,
`stage0b_span_stats`, `stage0c_reproject_crops`, `stage4sg_sidewalks_geom`,
`stage7_corridor_merge`, `stage8_deliverables_to_ftus`.

A run's real order is whatever its preset's `stage_order:` lists.

## Run-dir layout

```
<run>/
├── 00_inputs/trajectory.shp            stage0t
├── 02_corridor/crops/                  stage0u
├── 02_corridor/crops_clean/            stage0e  (stage 1 reads THESE)
├── 01_pointconv/
│   ├── combined_outputs/               stage1   *_tf1_pointconv_combined_0p1m.las
│   └── combined_outputs_0p025m/        stage1b  *_tf1_pointconv_combined_0p025m.las
├── 02_pole_crop/                       stage2
├── 03_pole_vec_body/                   stage3
├── 04_curbs/                           stage4
├── 04t_tree_trunk_canopy/              stage4t
├── 04w_building_walls/                 stage4w  Building_Walls.shp
├── 05_road_surface/road_surface.shp    stage5
└── 06_final_classification/            stage6   *_final_classified.laz
                                        + veg_stratification_summary.json   (6v)
```

Stage 6 **prefers** `combined_outputs_0p025m/` when it exists and falls back to
`combined_outputs/`. Stage 6v inherits whichever Stage 6 used.

## Stage 6 override priority (highest wins)

From `final_classified_pointcloud.py`:

1. **Pole body → 19** — within `--pole-match-radius` (0.05 m) of any
   `pole_body_pts.las` point. Beats the road override.
2. **Building wall → 47** — a **class-6-only** override, XY proximity
   (`--wall-match-radius`, 0.4 m) to a densified `Building_Walls.shp` line.
3. **Road → 40** — a **class-2-only** override, point-in-polygon against
   `road_surface.shp`. Class-2-only is deliberate: a bare 2-D polygon test would
   otherwise relabel trees and vehicles overhead as road.
4. **PointCONV base** — everything else keeps `{0,2,5,6,14,15,18}`.

Stage 6v then refines **only** the veg class, and is deliberately last: it never
touches 19/40/47, so it composes with Stage 6 in either order — but running it
last means the ground set it uses for the DEM already includes the road points
(class 40), which is the better surface.

`original_class` (uint8 extra dim) preserves the pre-override label. Stage 6v
does **not** overwrite it when Stage 6 already wrote it.

### Overrides that are inert in an aerial/corridor run

- **Class 19** — KNOWN-INERT since 2026-06-10. The pole-body glob expects the
  full pole-vec layout (`PoleVec/Temp/PCseg/…/pole_body_pts.las`), but body-only
  pole-vec writes `PoleVec/EstimatePoleTops/P_XXX_thinned/` and no
  `pole_body_pts.las` at all (`n_pole_body_files=0` in every Oakville summary).
  PointCONV also folds all pole-like classes into 18, so 19 only ever meant
  "pole-vec-refined body points".
- **Class 40 / 47** — skipped silently when Stage 5 / Stage 4w did not run.

## Known doc-vs-code drifts

| Drift | Reality |
|---|---|
| Stage-6 doc says road → **class 11** | The worker has used **40** since 2026-06. Catalog 11 = "Tanks"; 11 mislabelled road for downstream SDAI tooling. `stage_package.yml` and the worker both say 40. |
| Stage-6 `epsg` default | Code default **26917** (UTM 17N, Ontario). Every Firmatek preset overrides to **26911**. Stage 8 reads this as the *source CRS* for its ftUS reproject and nothing cross-checks it — a wrong value silently produces wrong-coordinate deliverables. |
| `classify_las` SKILL.md paths | Documented as `D:\agentic-workflows\…`; the real checkout is `C:\Users\sdaiprod\source\agentic-workflows\…`. `D:\agentic-workflows` does not exist. |
| `veg_outline` SKILL.md interpreter | The documented `geotools/.venv` path does not exist. Use the `networkx` conda env. |
| Class scheme "6 classes" | Stage 1 emits **7** distinct values `{0,2,5,6,14,15,18}` — class 0 is not one of the six trained classes but is ~35 % of corridor output. |

## Class-0 provenance

~35 % of Aecon corridor outputs are class 0. Verified 2026: **99.5 % have a
labelled neighbour within 0.05 m** (median 0.000 m — tile-overlap duplicates
where only one copy received the label). They are densification points, not
coverage holes. The fix is Stage 1b's 1-NN back-projection/upsample (0 of 10.37M
Oakville fine points were out of range), planned to become the standard
final-cloud path alongside the 0.025 m-retrained curb model.

Stage 6v leaves class 0 alone. If those points matter for the veg product, run
Stage 1b before Stage 6/6v.

## Stage 6v design notes

**Why HAG and not the model.** Splitting low/med/high veg is a *geometric*
distinction (height above local ground), not a semantic one. Retraining PointCONV
to 8 classes would need annotated low/med veg at scale; a HAG threshold on an
already-correct veg mask gets the same product deterministically and reversibly
(the `hag` dimension is retained, so re-thresholding needs no recompute).

**Why min-Z grid + nearest-cell fill.** Same approach as the legacy
`geotools/module/Add_Hag/add_hag.py`, but:

- vectorized (`np.minimum.at` + one batched `cKDTree.query`) instead of a
  per-cell Python loop over the grid, which made the legacy module unusable at
  corridor scale;
- **non-destructive** — `add_hag.py` overwrites `Z` with HAG. Stage 6v writes HAG
  to an extra dimension and leaves geometry untouched;
- ground comes from the **classification** (2 + 40), not from every point's local
  minimum, so canopy over a slope doesn't define its own "ground".

**Known bias.** A min-Z DEM sits slightly *below* true ground (sensor noise +
intra-cell slope), so HAG is biased marginally high, and points within a few cm
of a threshold can land in the next band up. Measured on the synthetic fixture:
6 of 15,000 veg points (0.04 %) crossed a boundary. Lower `--dem-cell` over
strong relief to reduce it.

**Verified behaviour** (synthetic fixture, sloped ground + known HAG bands):

- exact band assignment for 15,000 veg points across 3 bands (±0.04 %);
- classes 2/14/18/40 provably unchanged;
- ftUS cloud with `--units auto` reproduces the metre result exactly;
- forcing the wrong unit collapses everything to class 5 (documented failure mode);
- streaming path byte-identical to in-memory (classification, `original_class`,
  `hag`, and all geometry/attribute dims);
- benign exit 3 with the source untouched on a real 473M-point unclassified cloud.

## Engine notes

- Every CPU stage runs `--engine native` (host conda env), `--engine pixi`
  (pinned `aecon-cpu`), or `--engine docker` (S3 image tarball, repo-free).
- **`stage1_pointconv` and `stage3_pole_vec` are host-orchestrated docker**: the
  worker runs on the host but spawns the `mmworkflow` GPU image as a sibling
  container. They **cannot** run `--engine native`.
- Stage 6 and Stage 6v are pure CPU — laspy / numpy / scipy (+ geopandas for
  Stage 6's road polygon). No GPU, no Docker.

## Class obtainability — the blockers, with evidence

Investigated 2026-08-19 on this machine.

| Class | Blocker | Evidence |
|---|---|---|
| **40 Road** | Stage 5 needs Stage 4 curb-skill `Run_*_classified.laz` + `Run_*_hag.npy`; Stage 4 needs a **pretrained curb model**, which is not present | `curb_skill_pipeline.py:178` → `"FAIL: no curb model found (pass --model, or bundle one at ...)"`; a filesystem search for a curb model found nothing |
| **19 Pole body** | Needs Stage 3 pole-vec FULL mode | `final_classified_pointcloud.py` header: KNOWN-INERT 2026-06-10, `n_pole_body_files=0` in every Oakville run summary |
| **51 Sidewalk** | No model emits it | `models.json` lists 2 models, both `{2,5,6,14,15,18}` |
| **9 Water** | Nothing in the chain produces it | no stage lists it in `produces` |

Note Stage 5's own road-band heuristic (ground with −0.05 < HAG < 0.30 m) is
**not** a curb-model substitute: it separates road from *raised* surfaces
(sidewalks sit 15–30 cm up), so on flat rural terrain it selects all ground.
The curb model supplies the EDGE_OF_PAVEMENT extent that actually bounds the road.

## Model inventory (`engine-cloud/geotools/lib/pointconv/models.json`)

Both selectable classifiers are 6-class — `{2: ground, 5: high_vegetation,
6: building, 14: wire, 15: transmission_tower, 18: utility_pole}`:

| Model | dim | Features | Notes |
|---|---|---|---|
| `PointCONV_model_6class_Mobile_v0.0.18_retune_c2` | 6 | x,y,z,hag,linearity,verticality | Chain-pinned (the launcher's `MODELS_C1`), baked into the image |
| `PointCONV_model_6class_Mobile_v0.0.15` | 3 | x,y,z | XYZ-only predecessor |

Both are **mobile**-trained — `models.json` warns to "expect a domain gap on
aerial data". The host also carries `PointCONV_model_6class_v0.0.10` at
`D:\LL\models`, which is what standalone `classify.sh` uses by default
(`Classification/inputconfig.yml: model_directory`), so a standalone run and a
chain run are **not** using the same weights.

### The c1_lv scheme decision (why class 3 is contested)

`MODEL_CARD_c1_lv.md` (promoted 2026-06-09) records: the desert "veg" deficit was
a labelling-scheme mismatch, entirely **low vegetation (code 3) confused with
ground**; medium/high veg were already at 96–99 % recall. Decision (Greg,
2026-06-08): *"low scrub == ground — code 3 folds to Ground (model idx 3 / LAS
class 2) globally; only codes 4+5 remain Vegetation."*

So the training scheme deliberately eliminates class 3, while `veg_outline.py`
expects to consume `3,4,5`. Stage 6v resolves this by making the split explicit
and reversible (`hag` + `original_class` retained) — and `--low-max 0` suppresses
class 3 entirely if a deliverable must follow the c1_lv convention.
