"""Wave B streaming inference: PointCONV from pre-sampled .npz patches.

Replaces the legacy 3-stage pipeline (tile-build → SamplePoints → infer →
merge) with a streaming 2-stage one when the Stage 0c pre-sample cache
exists:

    crops_metric/Pole_*.las   →  Stage 0c presample  →  patches_pointconv/<pole>_patches.npz
                                                                          │
                                                                          ▼
                                              [Wave B] classification_from_patches.py
                                                       (load → infer → vote → write)
                                                                          │
                                                                          ▼
                                       combined_outputs/<pole>_tf1_pointconv_combined_0p1m.las
                                       (BYTE-IDENTICAL schema to v0 path)

Architecture:
  - One pole at a time (sequential per source — matches v0).
  - For each pole's .npz:
      1. Load xyz_orig (N, 3), xyz_class (N,), patch_indices (P, 16384),
         patch_count (N,), mask_predict (N,), config_hash.
      2. Verify config_hash against the live inputconfig.yml — refuse
         mismatch (silent stale-cache prevention).
      3. Load the TF1 graph + model checkpoint once per pole
         (TODO: hoist across poles → task #86 A2 will fix this).
      4. For batches of B patches at a time:
            xyz_batch = xyz_orig[patch_indices[i:i+B]]    # (B, 16384, 3)
            xyz_batch -= median(xyz_batch, axis=1, keepdims=True)
            logits = sess.run(pred_op, {points_pl: xyz_batch})
            class_pred = argmax(logits, axis=-1)            # (B, 16384)
            prob_pred = softmax(logits).max(axis=-1)        # (B, 16384)
      5. Vote-aggregate per-original-point predictions using patch_count
         (the same algorithm merge_tf1_tile_predictions.py uses in v0).
      6. Write LAS with classification + extra dims (source_class,
         pointconv_prob, pointconv_votes) — schema-identical to v0.
      7. Update viz/pointconv_inference_progress.json after every Nth
         patch so the dashboard can show real-time progress.

Status: SCAFFOLDING — the TF1 model-feed step (the actual sess.run)
needs to be wired against the live PointCONV checkpoint. See the
explicit `# TODO Wave B model feed` markers below. Until that's done,
this script can be invoked with `--smoke-test` to verify the I/O
plumbing (load cache, batch tensors, write LAS) without actually
running the model.

The IoU comparison gate (scripts/compare_pointconv_outputs.py) must
pass at threshold 0.999 per class before this replaces the v0 path
as default. See docs/stages/1_pointconv_v0_original.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def _config_hash(point_conv_conf: dict, random_seed: int) -> str:
    """Recompute the patch config hash to validate cache freshness.

    Must match scripts/presample_pointconv_patches.py:_config_hash —
    if these diverge, cache invalidation breaks. Kept here as a
    standalone copy because the TF1 container can't easily import
    from chain-orchestrator/scripts/."""
    h = hashlib.sha256()
    for k in sorted(point_conv_conf.keys()):
        h.update(f"{k}={point_conv_conf[k]}".encode("utf-8"))
    h.update(f"seed={random_seed}".encode("utf-8"))
    return h.hexdigest()[:16]


def _load_pointconv_conf(inputconfig_path: Path,
                          model_dir: Path | None = None) -> dict:
    """Mirror presample_pointconv_patches.py:_load_pointconv_conf().

    MUST stay byte-identical to the Wave A side so the config_hash
    cache check works. Reads from:
      - inputconfig.yml: PointConv.* + preprocessing.*
      - model exp_def.p: Radius_NN (if model_dir provided)
    """
    import yaml
    with inputconfig_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    pc = cfg.get("PointConv", {}) or {}
    pp = cfg.get("preprocessing", {}) or {}

    radius_nn = 11.0
    dim = 3
    if model_dir is not None:
        exp_def_path = Path(model_dir) / "exp_def.p"
        if exp_def_path.is_file():
            try:
                from pickle import load as _pload
                with open(exp_def_path, "rb") as f:
                    _exp = _pload(f)
                if "data_definition" in _exp \
                        and "Radius_NN" in _exp["data_definition"]:
                    radius_nn = float(_exp["data_definition"]["Radius_NN"])
                elif "Radius_NN" in _exp:
                    radius_nn = float(_exp["Radius_NN"])
                dim = int(_exp.get("dim", 3))
            except Exception:
                pass

    return {
        "num_candidates": pc.get("num_candidates", 30),
        "max_points_per_region": pc.get("max_points_per_region", 311296),
        "min_points_in_region": pc.get("min_points_in_region", 24576),
        "num_threads_PointCONV_sample": pc.get(
            "num_threads_PointCONV_sample", 4),
        "min_samples_per_point": pc.get("min_samples_per_point", 1),
        "training_data_config": None,
        "class_mapping_model": None,
        "radius_nn": radius_nn,
        "n_points": pp.get("min_num_pts_voxel", 16384),
        "voxel_size": pp.get("voxel_size", 0.1),
        # Model input channels — part of the hash, see the Wave A side.
        "dim": dim,
    }


def _load_full_inputconfig(inputconfig_path: Path) -> dict:
    """Load the entire inputconfig.yml — including `PointConv.class_mapping_model`,
    which v0's process_combined_seg_results.py uses to map model output
    channels (0..NUM_CLASSES-1) to LAS class codes."""
    import yaml
    with inputconfig_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg


def _progress_writer(run_dir: Path, n_poles_total: int,
                       patches_per_pole: dict):
    """Returns a callable update(pole_id, n_patches_done, n_patches_total,
    rate, state) that writes viz/pointconv_inference_progress.json.

    Throttled to ~1 Hz to avoid hammering the disk."""
    progress_path = run_dir / "viz" / "pointconv_inference_progress.json"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "n_poles_total": int(n_poles_total),
        "n_poles_done": 0,
        "n_patches_total": int(sum(patches_per_pole.values())),
        "n_patches_done": 0,
        "current_pole": "",
        "patches_per_sec": 0.0,
        "eta_sec": None,
        "per_pole": {
            pid: {"n_patches": n, "n_done": 0, "state": "pending"}
            for pid, n in patches_per_pole.items()
        },
    }
    last_write = [0.0]

    def update(pole_id, n_done, n_total, rate=0.0, state_str="running",
                final_for_pole=False, force=False):
        state["current_pole"] = pole_id
        state["per_pole"].setdefault(
            pole_id, {"n_patches": n_total, "n_done": 0, "state": "running"}
        )
        state["per_pole"][pole_id]["n_done"] = int(n_done)
        state["per_pole"][pole_id]["n_patches"] = int(n_total)
        state["per_pole"][pole_id]["state"] = state_str
        if final_for_pole:
            state["n_poles_done"] += 1
            state["per_pole"][pole_id]["state"] = "done"
        # Recompute aggregates.
        state["n_patches_done"] = sum(
            p["n_done"] for p in state["per_pole"].values()
        )
        state["patches_per_sec"] = float(rate)
        remaining = state["n_patches_total"] - state["n_patches_done"]
        state["eta_sec"] = (remaining / rate) if rate > 0 else None

        now = time.time()
        if force or final_for_pole or (now - last_write[0]) > 1.0:
            last_write[0] = now
            tmp = progress_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, progress_path)

    return update, state


def _build_tf1_session(model_dir: Path, batch_size: int, fp16: bool = False):
    """Build the TF1 graph + restore the PointCONV checkpoint once.

    Returns a dict with everything _process_one_pole needs to run inference
    without rebuilding the graph per pole. Building the graph per pole would
    raise "Variable layer1/conv0/weights already exists" — TF1's variable
    scope is per-Graph and the default graph accumulates state across builds.

    The returned dict also carries exp_def metadata (NUM_CLASSES, NUM_POINT,
    etc.) so the per-pole loop doesn't have to re-load exp_def.p either."""
    import importlib
    import sys as _sys
    from pickle import load as _pickle_load

    # 1. Load exp_def.p metadata.
    exp_def_path = Path(model_dir) / "exp_def.p"
    with open(exp_def_path, "rb") as f:
        exp_def = _pickle_load(f)
    NUM_CLASSES = exp_def['NUM_CLASSES']
    BANDWIDTH = exp_def['BANDWIDTH']
    radii = exp_def['radii']
    NUM_POINT = exp_def['NUM_POINT']  # = 16384
    dim = exp_def['dim']              # = 3
    model_module_name = exp_def.get('model') or exp_def['FLAGS'].model
    LABELS_original = exp_def.get('LABELS_original')
    LABELS_Map = exp_def.get('LABELS_Map')
    # scale_type 0 → mean-center per patch (PointCONV_Segment Dataset:25)
    # scale_type 1 (or anything else) → median-center per patch.
    scale_type = int(exp_def.get('scale_type', 1))

    # 2. Make sure model_code_PointCONV is on sys.path (mirrors
    # PointCONV_Segment.py lines 67-69).
    _base_dir = str(Path(__file__).resolve().parent / "PointCONV")
    if _base_dir not in _sys.path:
        _sys.path.append(_base_dir)
    _model_code_dir = str(Path(_base_dir) / "model_code_PointCONV")
    if _model_code_dir not in _sys.path:
        _sys.path.append(_model_code_dir)
    try:
        import tensorflow.compat.v1 as tf
        tf.disable_v2_behavior()
    except ImportError:
        import tensorflow as tf  # pure-TF1 fallback
    MODEL = importlib.import_module(model_module_name)

    # 3. Build the graph + placeholders + restore checkpoint. ONCE.
    BATCH_SIZE = batch_size
    with tf.device('/gpu:0'):
        pl_points = tf.placeholder(
            tf.float32, shape=(BATCH_SIZE, NUM_POINT, dim))
        pl_labels = tf.placeholder(
            tf.int32, shape=(BATCH_SIZE, NUM_POINT))
        pl_smpws = tf.placeholder(
            tf.float32, shape=(BATCH_SIZE, NUM_POINT))
        pl_train = tf.placeholder(tf.bool, shape=())
        pred_op, _ = MODEL.get_model(
            pl_points, pl_train, NUM_CLASSES, BANDWIDTH, radii=radii)
        saver = tf.train.Saver()
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    config.allow_soft_placement = True
    if fp16:
        # TF1 grappler auto-mixed-precision (#91 fp16 lever): inserts fp16 casts
        # around the whitelisted matmul/conv ops (the fa_layer4 matmuls that OOM
        # at batch 48 are the heaviest) while keeping fp32 weights + non-whitelist
        # ops. No model-code change. On Ada (4090) Tensor Cores this can speed up
        # the heavy layers AND halve their activation memory (so a bigger batch
        # may then fit). It DOES change outputs numerically — validate per-class
        # IoU against the fp32 run before trusting it (see 1_pointconv_waveb_perf.md).
        config.graph_options.rewrite_options.auto_mixed_precision = 1  # ON
        print("  [fp16] WARNING auto_mixed_precision = ON — MEASURED to be a BAD "
              "trade on this model (#91): only ~4% faster but WRECKS the wire "
              "class (IoU 0.33 vs fp32) because wires are thin structures needing "
              "fp32 precision. Experimentation only; NEVER for production. See "
              "docs/stages/1_pointconv_waveb_perf.md.")
    sess = tf.Session(config=config)
    saver.restore(sess, str(
        Path(model_dir) / "Best_Model" / "model.ckpt"))
    return {
        "sess": sess,
        "pl_points": pl_points,
        "pl_labels": pl_labels,
        "pl_smpws": pl_smpws,
        "pl_train": pl_train,
        "pred_op": pred_op,
        "NUM_CLASSES": NUM_CLASSES,
        "NUM_POINT": NUM_POINT,
        "scale_type": scale_type,
        "BATCH_SIZE": BATCH_SIZE,
        "LABELS_original": LABELS_original,
        "LABELS_Map": LABELS_Map,
        "dim": dim,
    }


def _load_pole(npz_path: Path) -> dict:
    """Load one pole's cached arrays off disk (#91 prefetch unit).

    Pure I/O + numpy decompress — safe to run on a background thread while the
    GPU works on the previous pole. Returns the raw arrays + a t_load timer;
    the config_hash check stays in _process_one_pole so a mismatch is reported
    against that pole's result, not swallowed in the loader."""
    import numpy as np
    t = time.time()
    cache = np.load(npz_path)
    arrays = {
        "pole": npz_path.stem.replace("_patches", ""),
        "npz_path": npz_path,
        "xyz_orig": cache["xyz_orig"], "xyz_class": cache["xyz_class"],
        "patch_indices": cache["patch_indices"],
        "patch_count": cache["patch_count"], "mask_predict": cache["mask_predict"],
        "cache_hash": str(cache["config_hash"].item()),
    }
    arrays["t_load"] = time.time() - t
    return arrays


def _write_pole_las(payload: dict) -> float:
    """Write one pole's classified LAS (#91 async-write unit). Returns elapsed s.

    Runs on a background thread so the LAS serialization overlaps the GPU work
    on the NEXT pole. Byte-identical to the original inline write — same point
    format, same extra dims, same arrays; only the timing changes."""
    import numpy as np
    _t = time.time()
    if payload["smoke_test"]:
        payload["out_las"].with_suffix(".smoke.json").write_text(
            json.dumps({"pole": payload["pole_id"],
                        "n_patches": payload["n_patches"],
                        "n_points": payload["n_points"],
                        "n_votes_total": int(payload["n_votes_total"]),
                        "n_with_votes": int(payload["n_with_votes"]),
                        "msg": "smoke-test mode — no model run"}),
            encoding="utf-8")
    else:
        import laspy
        header = laspy.LasHeader(point_format=7, version="1.4")
        las_out = laspy.LasData(header=header)
        xyz_orig = payload["xyz_orig"]
        las_out.x = xyz_orig[:, 0]
        las_out.y = xyz_orig[:, 1]
        las_out.z = xyz_orig[:, 2]
        las_out.classification = payload["out_class"]
        las_out.add_extra_dim(laspy.ExtraBytesParams(
            name="source_class", type=np.uint8))
        las_out.add_extra_dim(laspy.ExtraBytesParams(
            name="pointconv_prob", type=np.float32))
        las_out.add_extra_dim(laspy.ExtraBytesParams(
            name="pointconv_votes", type=np.uint8))
        las_out["source_class"] = payload["xyz_class"].astype(np.uint8)
        las_out["pointconv_prob"] = payload["out_prob"]
        las_out["pointconv_votes"] = payload["out_votes"]
        las_out.write(str(payload["out_las"]))
    return time.time() - _t


def _process_one_pole(npz_path: Path, model_dir: Path,
                        inputconfig: dict, expected_hash: str,
                        output_dir: Path,
                        progress_update,
                        batch_size: int,
                        smoke_test: bool = False,
                        full_inputconfig: dict | None = None,
                        tf_handle: dict | None = None,
                        preloaded: dict | None = None,
                        defer_write: bool = False) -> dict:
    """Stream one pole's patches through PointCONV, write classified LAS.

    `tf_handle` is the dict returned by _build_tf1_session — built ONCE in
    main() and passed in per pole. Avoids the per-pole graph rebuild that
    triggered TF1's "variable already exists" error.

    Returns a small result dict (timings, error if any)."""
    import numpy as np
    result = {"pole": npz_path.stem.replace("_patches", ""),
              "ok": False, "error": None,
              "n_patches": 0, "n_points": 0, "wall_sec": 0.0,
              # Profiling sub-timers (#91): wall time per phase, so we can see
              # whether the loop is GPU-bound or stalled on npz-load / LAS-write.
              "t_load": 0.0, "t_gpu": 0.0, "t_infer": 0.0,
              "t_aggregate": 0.0, "t_write": 0.0}
    t0 = time.time()
    pole_id = result["pole"]
    try:
        # 1. Load cached patches — either inline (np.load here) or from a
        # preloaded dict produced by the #91 prefetch thread.
        if preloaded is not None:
            xyz_orig = preloaded["xyz_orig"]
            xyz_class = preloaded["xyz_class"]
            patch_indices = preloaded["patch_indices"]
            patch_count = preloaded["patch_count"]
            mask_predict = preloaded["mask_predict"]
            cache_hash = preloaded["cache_hash"]
            result["t_load"] = preloaded.get("t_load", 0.0)
        else:
            _t = time.time()
            cache = np.load(npz_path)
            xyz_orig = cache["xyz_orig"]                 # (N, 3) float64
            xyz_class = cache["xyz_class"]               # (N,) uint8
            patch_indices = cache["patch_indices"]       # (P, 16384) int32
            patch_count = cache["patch_count"]           # (N,) uint16
            mask_predict = cache["mask_predict"]         # (N,) bool
            cache_hash = str(cache["config_hash"].item())
            result["t_load"] = time.time() - _t
        if cache_hash != expected_hash:
            raise RuntimeError(
                f"config_hash mismatch: cache={cache_hash} vs "
                f"expected={expected_hash}. Re-run Stage 0c presample "
                f"OR delete the cache to fall back to live sampling."
            )
        # Belt-and-suspenders behind the hash: a cache sampled for a
        # different channel count would hit the TF1 placeholder as an
        # opaque shape error 200 lines later — say what's actually wrong.
        _model_dim = int(tf_handle["dim"]) if tf_handle else 3
        if not smoke_test and xyz_orig.shape[-1] != _model_dim:
            raise RuntimeError(
                f"cache dim mismatch: patches carry "
                f"{xyz_orig.shape[-1]} channels but the model expects "
                f"{_model_dim} (dim-6 models need Stage 0c presample with "
                f"use_geometry_features). Re-run Stage 0c presample.")
        n_patches = int(patch_indices.shape[0])
        n_points = int(xyz_orig.shape[0])
        result["n_patches"] = n_patches
        result["n_points"] = n_points

        progress_update(pole_id, 0, n_patches, 0.0, "running")

        # 2. Pull metadata + placeholders from the pre-built handle.
        if smoke_test:
            # Smoke-test path: synthesize defaults without loading exp_def.
            NUM_CLASSES = 6
            NUM_POINT = 16384
            scale_type = 1  # median
            BATCH_SIZE = batch_size
            sess = pred_op = None
            pl_points = pl_labels = pl_smpws = pl_train = None
            LABELS_original = LABELS_Map = None
        else:
            assert tf_handle is not None, "tf_handle required for real run"
            NUM_CLASSES = tf_handle["NUM_CLASSES"]
            NUM_POINT = tf_handle["NUM_POINT"]
            scale_type = tf_handle["scale_type"]
            BATCH_SIZE = tf_handle["BATCH_SIZE"]
            sess = tf_handle["sess"]
            pred_op = tf_handle["pred_op"]
            pl_points = tf_handle["pl_points"]
            pl_labels = tf_handle["pl_labels"]
            pl_smpws = tf_handle["pl_smpws"]
            pl_train = tf_handle["pl_train"]
            LABELS_original = tf_handle["LABELS_original"]
            LABELS_Map = tf_handle["LABELS_Map"]
        votes = np.zeros((n_points, NUM_CLASSES), dtype=np.float32)
        n_votes_per_pt = np.zeros((n_points,), dtype=np.uint16)

        # 4. Batched streaming inference loop. Pad final batch to
        # BATCH_SIZE since the TF1 graph has a fixed batch dim.
        rate_window = []  # (timestamp, n_patches_done) for rolling rate
        zeros_lbl = np.zeros((BATCH_SIZE, NUM_POINT), dtype=np.int32)
        ones_wts = np.ones((BATCH_SIZE, NUM_POINT), dtype=np.float32)
        _t_infer = time.time()
        _gpu = 0.0
        for batch_start in range(0, n_patches, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, n_patches)
            real_n = batch_end - batch_start
            idx = patch_indices[batch_start:batch_end]
            # Pad the batch to BATCH_SIZE so TF1's fixed-shape placeholder
            # is satisfied. We track real_n so we don't accumulate junk
            # padding into votes.
            if real_n < BATCH_SIZE:
                pad = np.repeat(
                    idx[-1:], BATCH_SIZE - real_n, axis=0)
                idx_padded = np.concatenate([idx, pad], axis=0)
            else:
                idx_padded = idx
            xyz_batch = xyz_orig[idx_padded]  # (BATCH_SIZE, 16384, dim)
            # Per-patch center: mean if exp_def['scale_type']==0 else median.
            # Mirrors Dataset.get_point_data() in PointCONV_Segment.py:23-27 —
            # which centers ONLY the XYZ channels; dim-6 feature channels
            # (hag/linearity/verticality) must stay absolute.
            xyz_batch = xyz_batch.astype(np.float32)
            _nc = min(3, xyz_batch.shape[-1])
            if scale_type == 0:
                xyz_batch[:, :, :_nc] -= np.mean(
                    xyz_batch[:, :, :_nc], axis=1, keepdims=True)
            else:
                xyz_batch[:, :, :_nc] -= np.median(
                    xyz_batch[:, :, :_nc], axis=1, keepdims=True)

            if smoke_test:
                # Uniform fake logits — exercises the aggregation path
                # without TF1.
                probs = np.full(
                    (BATCH_SIZE, NUM_POINT, NUM_CLASSES),
                    1.0 / NUM_CLASSES, dtype=np.float32)
            else:
                feed = {
                    pl_points: xyz_batch,
                    pl_labels: zeros_lbl,
                    pl_smpws: ones_wts,
                    pl_train: False,
                }
                # PointCONV's `pred` op already returns softmax probs
                # (NOT raw logits) — confirmed via process_combined_seg_results.py
                # which directly argmaxes pred_prob without re-softmaxing.
                _tg = time.time()
                probs = sess.run(pred_op, feed_dict=feed)
                _gpu += time.time() - _tg

            # 5. Per-original-point vote aggregation. Accumulate SOFTMAX
            # PROBABILITIES (not logits) per the legacy merge contract.
            # Only the real (non-padded) batch rows contribute.
            for b in range(real_n):
                pids = idx[b]
                # Vectorized accumulation — much faster than per-class loop.
                votes[pids] += probs[b]
                n_votes_per_pt[pids] += 1

            # 6. Progress + rate.
            now = time.time()
            rate_window.append((now, batch_end))
            rate_window = [(t, n) for t, n in rate_window
                            if now - t <= 10.0]
            if len(rate_window) >= 2:
                dt_ = rate_window[-1][0] - rate_window[0][0]
                dp_ = rate_window[-1][1] - rate_window[0][1]
                rate = (dp_ / dt_) if dt_ > 0 else 0.0
            else:
                rate = 0.0
            progress_update(pole_id, batch_end, n_patches, rate, "running")

        # NOTE: sess is OWNED by main()'s tf_handle and reused across poles.
        # Do NOT close it here.
        result["t_infer"] = time.time() - _t_infer
        result["t_gpu"] = _gpu
        _t_agg = time.time()

        # 7. Final per-point class + prob.
        # Where the original point was a sample target (mask_predict True
        # AND at least one vote): take argmax over normalized vote probs.
        # Else: class 0 (unclassified) with prob 0.
        out_class_model_idx = np.zeros((n_points,), dtype=np.uint8)
        out_prob = np.zeros((n_points,), dtype=np.float32)
        has_votes = n_votes_per_pt > 0
        if has_votes.any():
            # Normalize votes by count (mean softmax over patches that
            # voted on this point).
            normalized = votes[has_votes] / n_votes_per_pt[has_votes, None]
            out_class_model_idx[has_votes] = normalized.argmax(axis=1)
            out_prob[has_votes] = normalized.max(axis=1)

        # Map model index (0..NUM_CLASSES-1) → LAS class code.
        # Prefer inputconfig.yml's PointConv.class_mapping_model.class_label
        # because that's what v0 (process_combined_seg_results.py:317-324) uses,
        # so following the same source makes the byte-equivalence comparison
        # against v0 outputs apples-to-apples. Fall back to exp_def's
        # LABELS_original/LABELS_Map if the inputconfig doesn't carry one.
        out_class = np.zeros((n_points,), dtype=np.uint8)
        mapping = None
        if full_inputconfig is not None:
            pcc = (full_inputconfig.get("PointConv") or {})
            cmm = pcc.get("class_mapping_model") or {}
            class_label = cmm.get("class_label")
            if class_label is not None:
                # class_label[i] is the LAS code for model output channel i.
                mapping = np.array(list(class_label), dtype=np.uint8)
        if mapping is None and LABELS_original is not None:
            # `LABELS_original` is the list of source LAS class codes;
            # `LABELS_Map` is the mapping from those codes → model
            # indices. To invert: model_idx → LAS code via the inverse.
            import collections as _coll
            idx_to_code = _coll.defaultdict(lambda: 0)
            for code, model_idx in zip(LABELS_original, LABELS_Map):
                # If multiple codes collapse to one index, the last wins;
                # in practice this is rare on the Mobile_v0.0.15 model
                # which has 1:1 mapping.
                idx_to_code[int(model_idx)] = int(code)
            mapping = np.array(
                [idx_to_code[i] for i in range(max(idx_to_code.keys()) + 1)],
                dtype=np.uint8)
        if mapping is not None:
            out_class[has_votes] = mapping[out_class_model_idx[has_votes]]
        else:
            # No mapping in either source — assume the model emits LAS codes
            # directly (older checkpoints).
            out_class = out_class_model_idx

        # n_votes_per_pt -> uint8 capped for the pointconv_votes extra dim
        # (matches v0's range of values).
        out_votes = np.clip(n_votes_per_pt, 0, 255).astype(np.uint8)

        # 8. Build the classified-LAS write payload. With #91 the actual write
        # is DEFERRED to a background thread (async), so it overlaps the GPU
        # work on the next pole; without defer_write it's written inline (the
        # original behavior, byte-identical).
        result["t_aggregate"] = time.time() - _t_agg
        # Classified point cloud written as .laz (laz-throughout). The mmworkflow
        # container's laspy has lazrs/laszip backends, so laspy.write() to a .laz
        # path compresses automatically (~12x). Consumers glob *.la[sz].
        out_las = output_dir / f"{pole_id}_tf1_pointconv_combined_0p1m.laz"
        out_las.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "out_las": out_las, "pole_id": pole_id, "smoke_test": smoke_test,
            "n_patches": n_patches, "n_points": n_points,
            "n_votes_total": int(n_votes_per_pt.sum()),
            "n_with_votes": int(has_votes.sum()),
            "xyz_orig": xyz_orig, "xyz_class": xyz_class,
            "out_class": out_class, "out_prob": out_prob, "out_votes": out_votes,
        }
        if defer_write:
            result["_write_payload"] = payload
        else:
            result["t_write"] = _write_pole_las(payload)

        result["ok"] = True
        progress_update(pole_id, n_patches, n_patches, 0.0, "done",
                         final_for_pole=True, force=True)
    except NotImplementedError as e:
        result["error"] = f"NotImplemented: {e}"
        progress_update(pole_id, 0, result["n_patches"], 0.0, "failed",
                         final_for_pole=False, force=True)
    except Exception as e:
        import traceback
        result["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        progress_update(pole_id, 0, result["n_patches"], 0.0, "failed",
                         final_for_pole=False, force=True)
    result["wall_sec"] = time.time() - t0
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--patches-dir", type=Path, required=True,
                    help="Stage 0c presample cache "
                         "(<run>/02_pole_crop/output/patches_pointconv)")
    p.add_argument("--output-dir", type=Path, required=True,
                    help="Where to write classified LAS "
                         "(<run>/01_pointconv/combined_outputs)")
    p.add_argument("--model-dir", type=Path, required=True,
                    help="PointCONV checkpoint dir "
                         "(models/PointCONV_model_6class_Mobile_v0.0.15)")
    p.add_argument("--inputconfig", type=Path, required=True,
                    help="tf1/inputconfig.yml (for config_hash + n_points)")
    p.add_argument("--run-dir", type=Path, required=True,
                    help="Chain run dir (for the inference-progress JSON)")
    p.add_argument("--batch-size", type=int, default=24,
                    help="Patches per TF1 batch. Default 24 (#91 sweep: the GPU "
                         "is ~77%% of wall and already saturated per 16384-pt "
                         "patch, so bigger batches help only ~3-9%%; 48 OOMs the "
                         "fa_layer4 aggregation op on a 24 GB card). 24 is the "
                         "safe ceiling.")
    p.add_argument("--random-seed", type=int, default=42,
                    help="Sampler RNG seed (used for config_hash check). "
                         "Must match the value Stage 0c's presample used.")
    p.add_argument("--pipeline", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="#91: prefetch the next pole's npz + write the previous "
                         "pole's LAS on background threads so neither stalls the "
                         "GPU. --no-pipeline forces the serial path (identical "
                         "outputs; for A/B timing).")
    p.add_argument("--fp16", action="store_true",
                    help="#91 fp16 lever: enable TF1 grappler auto-mixed-precision "
                         "(fp16 casts on matmul/conv). Opt-in — changes outputs "
                         "numerically, so validate per-class IoU vs the fp32 run "
                         "first. May also free enough memory for a bigger batch.")
    p.add_argument("--smoke-test", action="store_true",
                    help="Verify I/O plumbing without running TF1 model. "
                         "Use to validate progress writer + cache "
                         "loading + LAS writer paths.")
    args = p.parse_args()

    npz_files = sorted(args.patches_dir.glob("*_patches.npz"))
    if not npz_files:
        print(f"ERROR: no *_patches.npz in {args.patches_dir}")
        return 2

    point_conv_conf = _load_pointconv_conf(args.inputconfig, args.model_dir)
    full_inputconfig = _load_full_inputconfig(args.inputconfig)
    expected_hash = _config_hash(point_conv_conf, args.random_seed)
    print(f"Wave B streaming inference")
    print(f"  patches-dir : {args.patches_dir}")
    print(f"  output-dir  : {args.output_dir}")
    print(f"  model-dir   : {args.model_dir}")
    print(f"  inputconfig : {args.inputconfig}")
    print(f"  config_hash : {expected_hash}")
    print(f"  batch_size  : {args.batch_size}")
    print(f"  poles       : {len(npz_files)}")
    if args.smoke_test:
        print(f"  MODE        : SMOKE TEST (no TF1 inference)")

    # Pre-scan to populate progress totals.
    import numpy as np
    patches_per_pole = {}
    for npz in npz_files:
        pid = npz.stem.replace("_patches", "")
        try:
            with np.load(npz) as c:
                patches_per_pole[pid] = int(c["patch_indices"].shape[0])
        except Exception:
            patches_per_pole[pid] = 0

    progress_update, _state = _progress_writer(
        args.run_dir, len(npz_files), patches_per_pole)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Build the TF1 graph + restore the checkpoint ONCE for the whole run.
    # Reused across all poles → saves ~10 s per pole AND avoids the
    # "Variable layer1/conv0/weights already exists" collision that hits
    # the second pole when the graph is built per pole. (PointCONV_Segment.py
    # works around this with `with tf.Graph().as_default(): evaluate()` —
    # we go the better route by just not rebuilding.)
    tf_handle = None
    if not args.smoke_test:
        print(f"  Building TF1 graph + restoring checkpoint (once)...")
        t_build = time.time()
        tf_handle = _build_tf1_session(args.model_dir, args.batch_size,
                                       fp16=args.fp16)
        print(f"  Graph + checkpoint ready in {time.time()-t_build:.1f}s")

    use_pipeline = args.pipeline and not args.smoke_test
    print(f"  pipeline    : {'on (#91 prefetch + async-write)' if use_pipeline else 'off (serial)'}")
    results = []
    _run0 = time.time()
    if use_pipeline:
        # #91: a single background loader prefetches the next pole's npz into a
        # small bounded queue while the GPU runs the current pole; finished
        # poles' LAS writes go to a 2-worker thread pool so serialization
        # overlaps the next pole's GPU work. The TF session is touched ONLY by
        # this main thread (sess.run), and progress_update is called ONLY here,
        # so there are no cross-thread races on either.
        import queue as _queue
        import threading as _threading
        from concurrent.futures import ThreadPoolExecutor
        load_q: _queue.Queue = _queue.Queue(maxsize=2)
        _SENT = object()

        def _loader():
            for _npz in npz_files:
                try:
                    load_q.put(_load_pole(_npz))
                except Exception as e:  # defer the failure to the main thread
                    load_q.put({"pole": _npz.stem.replace("_patches", ""),
                                "_load_error": repr(e)})
            load_q.put(_SENT)

        _threading.Thread(target=_loader, name="waveb-loader",
                          daemon=True).start()
        # ONE write worker on purpose: laspy.LasData.write() is not guaranteed
        # thread-safe, and two concurrent writes intermittently corrupted a
        # single pole's output (#91 validation). A single background writer
        # still fully overlaps the GPU — writes (~0.4 s/pole) run far behind
        # inference (~5 s/pole), so one worker never falls behind — without ever
        # running two laspy writes at once.
        write_pool = ThreadPoolExecutor(max_workers=1,
                                        thread_name_prefix="waveb-write")
        pending = []  # (result_dict, write_future)
        while True:
            item = load_q.get()
            if item is _SENT:
                break
            if "_load_error" in item:
                results.append({
                    "pole": item["pole"], "ok": False,
                    "error": item["_load_error"], "n_patches": 0,
                    "n_points": 0, "wall_sec": 0.0, "t_load": 0.0,
                    "t_gpu": 0.0, "t_infer": 0.0, "t_aggregate": 0.0,
                    "t_write": 0.0})
                progress_update(item["pole"], 0, 0, 0.0, "failed",
                                final_for_pole=False, force=True)
                continue
            r = _process_one_pole(
                item["npz_path"], args.model_dir, point_conv_conf,
                expected_hash, args.output_dir, progress_update,
                args.batch_size, smoke_test=False,
                full_inputconfig=full_inputconfig, tf_handle=tf_handle,
                preloaded=item, defer_write=True)
            payload = r.pop("_write_payload", None)
            if r["ok"] and payload is not None:
                pending.append((r, write_pool.submit(_write_pole_las, payload)))
            else:
                results.append(r)
        for r, fut in pending:  # drain async writes
            try:
                r["t_write"] = fut.result()
            except Exception as e:
                r["ok"], r["error"] = False, f"async write failed: {e!r}"
            results.append(r)
        write_pool.shutdown(wait=True)
    else:
        for npz in npz_files:
            results.append(_process_one_pole(
                npz, args.model_dir, point_conv_conf, expected_hash,
                args.output_dir, progress_update, args.batch_size,
                smoke_test=args.smoke_test,
                full_inputconfig=full_inputconfig, tf_handle=tf_handle))
    _run_wall = time.time() - _run0

    for r in sorted(results, key=lambda x: x["pole"]):
        if r["ok"]:
            print(f"  OK   {r['pole']}: {r['n_patches']} patches, "
                  f"{r['n_points']:,} pts "
                  f"[load {r['t_load']:.2f} | gpu {r['t_gpu']:.2f} | "
                  f"infer {r['t_infer']:.2f} | agg {r['t_aggregate']:.2f} | "
                  f"write {r['t_write']:.2f}]")
        else:
            print(f"  FAIL {r['pole']}: "
                  f"{r['error'].splitlines()[0] if r['error'] else '?'}")
    print(f"  total inference wall: {_run_wall:.1f}s "
          f"({'pipelined' if use_pipeline else 'serial'})")

    # --- Profiling summary (#91) -------------------------------------------
    # Where did the wall time go, and how much is overlappable I/O? The
    # serial loop pays load + infer + write back-to-back per pole; prefetch +
    # async-write would hide load (except the first) and write (except the
    # last) under the GPU. The "overlappable" figure is the upper bound on
    # what those two changes can save.
    ok = [r for r in results if r["ok"]]
    if ok:
        sload = sum(r["t_load"] for r in ok)
        sgpu = sum(r["t_gpu"] for r in ok)
        sinfer = sum(r["t_infer"] for r in ok)
        sagg = sum(r["t_aggregate"] for r in ok)
        swrite = sum(r["t_write"] for r in ok)
        swall = sum(r["t_load"] + r["t_infer"] + r["t_aggregate"] + r["t_write"]
                    for r in ok)
        # load+write that is NOT the first load / last write -> hideable.
        first_load = min((r["t_load"] for r in ok), default=0.0)
        last_write = ok[-1]["t_write"] if ok else 0.0
        overlappable = max(0.0, (sload - first_load) + (swrite - last_write))
        print("\n--- Wave B profile (#91) ------------------------------------")
        print(f"  poles ok           : {len(ok)}")
        print(f"  sum load (npz)     : {sload:8.1f}s")
        print(f"  sum gpu (sess.run) : {sgpu:8.1f}s   "
              f"({100*sgpu/sinfer:.0f}% of infer loop)" if sinfer else "")
        print(f"  sum infer loop     : {sinfer:8.1f}s   (gpu + vote-accumulate)")
        print(f"  sum aggregate      : {sagg:8.1f}s")
        print(f"  sum write (laspy)  : {swrite:8.1f}s")
        print(f"  sum per-pole wall  : {swall:8.1f}s")
        print(f"  -> overlappable I/O: {overlappable:8.1f}s "
              f"({100*overlappable/swall:.0f}% of wall) "
              f"hideable by prefetch+async-write")
        print("-------------------------------------------------------------")

    # Close the global session at end-of-run (the only place we should).
    if tf_handle is not None and tf_handle.get("sess") is not None:
        try:
            tf_handle["sess"].close()
        except Exception:
            pass

    n_ok = sum(1 for r in results if r["ok"])
    print(f"\nDONE: {n_ok}/{len(npz_files)} poles")
    return 0 if n_ok == len(npz_files) else 1


if __name__ == "__main__":
    sys.exit(main())
