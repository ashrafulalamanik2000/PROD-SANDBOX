# Wire / Pole geometric post-processing (`wire_pole_postproc.py`)

Tier-1 **geometry-only** post-processing for PointCONV c2 combined clouds. Recovers
**Wire** and **Pole** points the model leaks to Vegetation/Man-made, with **no
retrain, no model change, and no color/RGB features**. Operates on the 0.1 m combined
cloud (`*_tf1_pointconv_combined_0p1m.la[sz]`) and rewrites `classification` in place.

**Status:** worker validated on the WARNER benchmark; chain integration is **drafted
but not yet applied** (see [Chain integration](#chain-integration)). The production
classifier and `chain_orchestrator.py` are untouched.

## What it does

Two passes, composed (A then B). The decision uses **no ground truth**.

- **Pass A — Wire.** RANSAC-fit 3D conductor lines from predicted-wire (class 14)
  seeds; model each span's catenary sag as a quadratic; reclaim non-wire points
  (pred ∈ {Veg 5, Man-made 6}) within `--wire-tol` of a conductor curve → **Wire**.
- **Pass B — Pole.** DBSCAN predicted-pole (class 18) seeds into poles; keep only
  those passing a **real-pole gate** (thin vertical shaft, vertical aspect ratio,
  ground-connected — rejects tree trunks); reclaim non-pole points inside a
  `--pole-radius` vertical cylinder around the axis that are locally vertical
  (PCA verticality ≥ `--pole-vmin`) → **Pole**.

### Locked operating point (WARNER-tuned, holdout-validated)
| param | value | meaning |
|---|---|---|
| `--wire-tol` | 0.25 m | perp distance to a conductor curve to reclaim |
| `--pole-radius` | 0.30 m | cylinder radius around a pole axis to reclaim |
| `--pole-vmin` | 0.50 | candidate vertical-linearity guard (Pass B) |

Tuning notes: the tight tube/cylinder supplies the precision on its own (a linearity
guard *hurt* Pass A recall and was dropped); the Pass B verticality guard at 0.50 was
chosen on the non-holdout cohort and proved **strictly better on the holdouts**
(higher IoU *and* precision on unseen data).

## Validated results — WARNER (17 files, 56.3 M scored voxels)

Scored with `compute_pointconv_iou.py` against the original DTECH labels
(`source_class`), folded via `class_mapping_warner.yml`.

| Class | Baseline c2 | + A/B post-proc | Δ |
|---|---|---|---|
| **Wire** | 0.821 | **0.847** | **+0.026** (recall 0.841 → 0.877) |
| **Pole** | 0.767 | **0.794** | **+0.027** (recall 0.842 → 0.879) |
| Ground | 0.954 | 0.954 | 0.000 |
| Vegetation | 0.941 | 0.941 | 0.000 |
| Man-made | 0.774 | 0.775 | +0.001 |
| **Mean (supported)** | 0.851 | **0.862** | **+0.011** |

Held-out generalization (3 sequestered files never used for tuning: 000020/000027/
000033) reproduced the gain in direction and magnitude with no class regressions.

## Usage

```bash
# Chain / per-file mode (correct one combined cloud):
python wire_pole_postproc.py --input  CLOUD_combined_0p1m.las \
                             --output CLOUD_combined_0p1m.las        # in place

# Batch mode (a directory of combined clouds):
python wire_pole_postproc.py --input-dir  IN_DIR --output-dir OUT_DIR \
                             --summary-json OUT_DIR/postproc_summary.json
```
Deps: numpy, scipy, laspy (self-contained; no other project modules). Adds two uint8
extra dims to the output — `wire_postproc_action` / `pole_postproc_action`
(1 = reclaimed) — for auditing. Files with too few wire/pole seeds (no conductors /
no gated poles) pass through unchanged.

## Chain integration

Intended slot: a new **`stage1a_wire_pole`** stage **after `stage1_pointconv`,
before `stage1b_fine_classification`**, correcting `01_pointconv/combined_outputs/`
in place (pristine originals backed up to `combined_outputs_precorrection/`, so the
stage is idempotent). Running before stage1b means the 0.025 m back-projection and
every downstream consumer (stage2/4/5/6) inherit the corrections automatically.
Recommended **opt-in** (`OPT_IN_STAGES`) first, so existing chains are unchanged
until a preset sets `stage1a_wire_pole.enabled: true`.

The copy-paste-ready `run_stage1a_wire_pole` runner, `_validate_inputs_*` validator,
`StageSpec` entry, `STAGE_ORDER`/`KNOWN_STAGES`/`OPT_IN_STAGES` edits, preset block,
and the remaining PR-gateway items (fixture + per-stage test in the `polecrop` env +
doc) are NOT applied here — they are the next, separate PR.

## Reproduction provenance

Tuning/validation were run in a scratch area (large WARNER combined LAS live outside
git, per the data-stays-at-source policy):
- Combined clouds: `…/classification_results/PointCONV_model_6class_Mobile_v0.0.18_retune_c2/WARNER/`
- Sweep / rollout / holdout / re-tune scripts + outputs:
  `G:/PointCONV_Model_Training/exp/pass_a/` (`sweep_pass_a.py`, `rollout_pass_a.py`,
  `holdout_check.py`, `sweep_pass_b.py`, `rollout_pass_b.py`, `retune_pass_b.py`,
  `pass_ab/` with the all-17 IoU).
- Eval mapping: `data/eval_mapping/class_mapping_warner.yml`.
- Method + full results: `finetune/handoff_doug/HANDOFF_warner_eval_2026_06_15.md`.

## What this does NOT fix (Pass C dropped — negative result)

A junction-topology pass (wire↔pole swap at pole tops) was attempted and **dropped**.
At 0.1 m voxel resolution wire and pole are spatially interleaved at junctions:
among predicted-pole points within 0.30 m of a conductor, the GT-**pole** points are
*more* linear/vertical (the shaft) than the GT-**wire** points, so local geometry
separates them backwards — no gate exceeds ~0.50 precision. The wire↔pole junction
swap (and total-miss files with no seeds, e.g. WARNER 000035) are **not recoverable
by post-processing**; they need finer resolution or better model features (Tier 2 c3
retrain: return-number + multi-scale eigenvalue features).
