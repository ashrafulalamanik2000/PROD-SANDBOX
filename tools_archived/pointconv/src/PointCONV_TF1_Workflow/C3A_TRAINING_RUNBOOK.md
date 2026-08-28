# c3a training runbook — WARNER-augmented geometry fine-tune of c2

**Goal.** Train **c3a** = `PointCONV_model_6class_Mobile_v0.0.19_retune_c3`: a
**dim-6, geometry-only** fine-tune of c2 with the WARNER corpus folded in. Targets
the recall-limited Wire/Pole classes (WARNER eval: Wire 0.821, Pole 0.767).

**Locked decisions (confirmed 2026-06-18).**
1. **Holdout:** sequester {000020, 000027, 000033} (the Pass A/B holdout) **+ 000016**
   (small, both classes well-populated) — EXCLUDED from training, scored post-train.
2. **Labels:** reuse the DTECH fold `finetune/dtech_to_model_mapping.yml` unchanged
   (WARNER codes are 1:1 DTECH; no Tower GT).
3. **c3a ONLY** — dim-6 data-only fine-tune from c2. **No new features, no c3b, no
   intensity.** Features stay exactly XYZ + HAG + linearity + verticality.
4. **Hard-example mining:** add the 13 non-holdout WARNER files as an
   `--al-source-dir` so their wire/pole-rich regions are oversampled in train.
5. **Guard the home domain:** keep evaluating the original holdouts (oakville_part_3
   Pole, DTECH test) so c3a doesn't regress on the home domain.
6. **Validation:** track **Wire recall** and **Pole recall** (the binding metric);
   `use_class_weight` is already on (inverse-freq → rare classes weighted).

**Train vs holdout WARNER files (17 total).**
- Train (13): 000013, 000014, 000015, 000019, 000021, 000022, 000025, 000026,
  000028, 000029, 000032, 000034, 000035.
- Holdout (4, never trained): 000020, 000027, 000033, 000016.

## Everything is on-machine (training workspace)
`WS = G:\PointCONV_Model_Training\PointCONV_Handoff_2026_06_05\code\PointCONV_TF1_Workflow`
(maps to `/workspace` in the mmworkflow container; `/data` = `…\data`,
`/exp` = `G:\PointCONV_Model_Training\exp`).
- c3a config: `WS\finetune\handoff_doug\finetune_config_retune_c3.yml` (written).
- c2 weights to warm-start from: `WS\models\PointCONV_model_6class_Mobile_v0.0.18_retune_c2\Best_Model\model.ckpt`.
- Existing thinned corpus: `/exp/oakdoug_staging/thin_all/source_thinned`.
- Raw WARNER: `G:\WARNER\Warner_0000xx shape_extract (1).las`.

## Command sequence (run in the mmworkflow/pdal env, GPU host)
```bash
# 0. Thin the 13 non-holdout WARNER files to 0.1 m (-> *_thin_0p1m.las) into a
#    WARNER thinned dir, using the SAME 0.1 m voxel thinning that produced
#    source_thinned. Output: /exp/oakdoug_staging/thin_all/warner_thinned/
#    (EXCLUDE 000016/000020/000027/000033.)

# 1. Prepare c3 dataset (dim-6 features; WARNER oversampled as the AL source).
python finetune/prepare_finetune_data.py \
  --config  finetune/handoff_doug/finetune_config_retune_c3.yml \
  --mapping finetune/dtech_to_model_mapping.yml \
  --source-thinned-dir /exp/oakdoug_staging/thin_all/source_thinned \
  --al-source-dir      /exp/oakdoug_staging/thin_all/warner_thinned \
  --al-oversample 3 \
  --geometry-features \
  --output-dir /exp/oakdoug_staging/data_c3

# 2. Fine-tune from c2 (warm_start_checkpoint in the config points at c2's Best_Model).
python finetune/train_finetune.py --config finetune/handoff_doug/finetune_config_retune_c3.yml

# 3. Post-train eval: holdout WARNER (000020/027/033/016) + original holdouts.
python finetune/run_post_finetune_eval.py --config finetune/handoff_doug/finetune_config_retune_c3.yml
#    + the WARNER IoU pipeline (tile->infer->merge->compute_pointconv_iou.py) on the
#    4 holdout files, comparing Wire/Pole recall to the c2 baseline.
```

## Confirm / wire before the (multi-hour) run
- **WSL2 TF1 hang:** re-arm the mitigation from the c2 run (see
  `c1lv-next-retrain-spec` memory) before launching the 30-epoch train.
- **Thinning step (0):** point at the exact 0.1 m thinning tool the pipeline used to
  build `source_thinned` (voxel 0.1 m) and run it on the 13 WARNER files.
- **Verify** `…_retune_c2/Best_Model/model.ckpt` exists (the warm-start source).
- **`--al-oversample 3`** is the hard-example knob; tune up if Wire/Pole still lag,
  or switch to tile-level mining using the Pass A/B confusion maps.
- Smoke-test first (`smoke_test_epochs: 1`) to validate the data + warm-start load
  before the full 30-epoch run.

## What success looks like
Wire recall ↑ and Pole recall ↑ on the WARNER holdout vs c2, with Ground/Veg/
Man-made held and **no regression** on the original-domain holdouts.
