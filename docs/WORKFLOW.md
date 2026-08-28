# Mobile-Data Preprocessing — the unified workflow, and why every step exists

`sdtools mobile-data-preprocessing` is the single front door for turning a raw
mobile-mapping delivery (AECON or DFX) into classification-ready data:
equirectangular panoramas + a colorized, clipped point cloud, with per-stage
LAS folders ready for the next steps.

Both clients need the SAME five things. They just start from different points,
so some stages are no-ops for one client:

| # | Stage      | Purpose (the *why*)                                                                | AECON            | DFX            |
|---|------------|------------------------------------------------------------------------------------|------------------|----------------|
| 1 | `organize` | Normalize the raw vendor delivery into the standard project layout                  | `yaml,organize`  | n/a (arrives organized) |
| 2 | `metadata` | Extract ONE camera pose table per run (frames + X/Y/Z + Roll/Pitch/Yaw)             | `metadata`       | `csv`          |
| 3 | `index`    | Persist camera-points + LAS-extent shapefiles (viewer/QC deliverable)               | n/a (temp files inside colorize) | `shp,lasindex` |
| 4 | `pano`     | Stitch 6 cubemap faces → equirectangular panoramas                                  | `pano`           | `pano`         |
| 5 | `colorize` | Paint LAS points with panorama RGB, clip to the camera-track buffer                 | `colorize`       | n/a (arrives colorized) |

Old stage names `csv` and `shp` still work as aliases of `metadata` and `index`.

After preprocessing, the mobile-data chain continues with two more console
tools (both wrap Topology-Aerial chain skills onto the same canonical layout):

| Tool | Purpose | In → Out | GPU? |
|---|---|---|---|
| `classification` | PointCONV 6-class inference + CPU refine (veg low/med/high split, optional walls/road overrides, class-7 noise) | `Lidar\Clipped` → `Lidar\Classified` | stage1 only (Docker, GPU box) |
| `pole-vec` | pole discovery → per-pole crops → span-graph nodes/edges → FULL pole-vec (body centerlines, parabola wires, catenary spans, DXF) | `Lidar\Classified` → `Vectors\Poles` | polevec stage only (Docker, GPU box) |

Every stage of both keeps ALL intermediates in a `_work\` folder under its
output dir, and `--stages clean` deletes it — that is the one-command
temp-file cleanup for each step.

---

## Why the two clients differ

**A DFX delivery arrives half-processed.** The vendor already organized images
into per-run `Run N Camera 4 360\` folders and already colorized the point
cloud. So DFX skips `organize` and `colorize`, and its whole metadata step is
one `.lst` → one CSV.

**An AECON delivery is raw Solv3D output.** Images arrive as SIX separate
per-camera folders (folder names defined in the `.iprj` project file), poses
live in `.lst` files as Solv3D **HRP** angles (Heading/Roll/Pitch), and the
LAS is neither colorized nor clipped. Getting AECON to the same state as a
fresh DFX delivery is exactly what stages 1–2 do; stage 5 does the coloring
the DFX vendor did upstream.

## The pose-table journey (the "YML, then CSV, then CSV again")

Each artifact is a *different answer*, not a redundant copy. In order:

| Artifact | Written by | What it is / why it must exist |
|---|---|---|
| `<project>\InputConfig.yml` | organize (yaml step) | Machine-readable parse of the vendor `.lst`: every frame's image path, camera number, Xyz, HRP. Organize needs this map to find and rename files. Multi-run aware (one `Run_N` block per `.lst`). |
| `Organized_Projects\Raw Project Data\InputConfig.yml` | organize | The SAME manifest rewritten to point at the organized copies — file names and folders changed during reorganization, so the map must be rewritten or every later stage would point at dead paths. |
| `...\Run N Camera 4 360\Run_N_metadata.csv` | metadata | The pose table in the shape the pano engine consumes: forward-camera (Camera 3) frames only, HRP angles converted to **Roll/Pitch/Yaw** via the `Frot()` rotation matrix. (DFX's `<mission>\<pname>_CSVs\<pname>.csv` is this same table, built straight from the `.lst`.) |
| `Organized_Projects\Pano_output\Run_N_metadata.csv` | pano | NOT a copy: the `Filename` column is rewritten from cubemap-face names (`..._3.jpg`) to the generated panorama names. This is the pose table *of the panoramas* — what colorize actually needs to project RGB onto points. Doubles as the pano stage's completeness check. |
| `Pano_output\All_Runs_metadata.csv` | colorize (multi-run projects only) | Union of the per-run pano pose tables, deduped by filename — colorize processes each LAS once against ALL runs, so it needs one table. |

Rule of thumb: **YML = file-organization manifests (stage 1), CSV = camera
pose tables (stages 2+)**. If we ever collapse artifacts, the candidates are
the two YMLs (a single manifest with both raw and organized paths) — the CSVs
each serve a distinct consumer.

## Where everything lands (per project)

```
<project>\
├── InputConfig.yml                                  organize: parsed .lst manifest
├── Organized_Projects\
│   ├── Raw Project Data\
│   │   ├── InputConfig.yml                          organize: manifest, organized paths
│   │   ├── Image Project\Run N Camera 4 360\*.jpg   organize: renamed cubemap faces
│   │   │   └── Run_N_metadata.csv                   metadata: image pose table
│   │   └── LAS Files\*.las                          organize: LAS copies
│   └── Pano_output\
│       ├── *.jpg                                    pano: equirectangular panoramas
│       ├── Run_N_metadata.csv                       pano: PANORAMA pose table
│       └── All_Runs_metadata.csv                    colorize: merged (multi-run only)
├── Lidar\                                           raw *.las stays loose (delivery format)
│   ├── Colorized\*_colorized.las                    colorize: full colorized LAS
│   │                                                (transient unless --keep-colorized)
│   ├── Clipped\*_clipped.las                        colorize: clipped — CLASSIFY INPUT
│   └── Classified\                                  classification: final classified LAZ
│       ├── *_final_classified.laz                   + summary JSONs
│       └── _work\                                   temp (stage1 tiles, chain run dir)
│                                                    → `classification --stages clean`
└── Vectors\
    └── Poles\                                       pole-vec deliverables
        ├── Network\                                 span-graph nodes/edges + QC
        ├── PoleVec\                                 body lines, wires, catenaries, DXF
        └── _work\                                   temp (per-pole crops, polevec run)
                                                     → `pole-vec --stages clean`
```

DFX missions keep their vendor-side layout (`Raw Project Data\...`) plus
`<pname>_CSVs\` (metadata + index outputs), `<pname>_LASINDEX.shp`, and
`Pano_output\`.

## Operational notes

- Every stage is **idempotent** — re-running skips work whose output exists,
  so resume by re-running with the remaining `--stages`.
- **Sharp edge:** once you delete the raw `Images\` tree and `.iprj` after
  organize (normal, to reclaim disk), preflight will reject any run that
  includes `organize`, and client auto-detect fails — resume with
  `--stages pano,colorize --client aecon`.
- Pano workers are embedded in the AECON engine string (`--workers N`);
  without the cap pano_generator uses ALL cores and OOMs 32 GB boxes.
- The `index` stage is currently DFX-only: AECON builds the same two
  shapefiles as temp files inside colorize and throws them away. Promoting
  them to a persisted AECON `index` stage is the natural next unification if
  the viewer/QC side ever wants them.

## Reference run

Cambridge_P3_60 (42 GB, 2603 panos, 4 LAS), 2026-08-26, run `017c55d8`:
full pipeline green end-to-end; pano 1290 s, colorize+clip 1985 s on a
32 GB laptop with `--pano-workers 4 --project-workers 2 --las-workers 2`.
