"""TF1 fine-tune trainer for PointCONV_model_6class_v0.0.10.

Runs inside the workflow's Docker image (tensorflow.compat.v1, Python 3, GPU).

Key differences from the original training script:
  - Loads xyz, class AND per-point sample-weight (smpw) files. The smpw mask
    encodes which points are "ignored" (source class outside our 5-active set).
  - Loss is computed over only the 5 active class indices. Class 5
    (transmission tower) is therefore frozen from gradient flow. Its fc2
    weight column receives zero gradient and stays unchanged from the
    warm-start checkpoint.
  - Warm-starts from PointCONV_model_6class_v0.0.10/Best_Model/model.ckpt.
  - Saves a fine-tuned model directory with the original layout
    (Best_Model/, exp_def.p) so the tiled inference path can use it
    by just pointing at the new model_directory.

Run:
  python train_finetune.py --config /workspace/finetune/finetune_config.yml \
                           --data-root /exp/<run>/data \
                           --model-out /exp/<run>/model \
                           [--epochs N] [--smoke-test]
"""
from __future__ import annotations

import argparse
import copy
import logging
import os
import pickle
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml


# Inside the Docker image the workflow lives at /workspace.
# The legacy code expects two entries on sys.path:
#   - tf1/PointCONV/ so absolute imports like `model_code_PointCONV.utils.*` resolve
#   - tf1/PointCONV/model_code_PointCONV/ so relative-style imports like
#     `from utils import tf_util` and `from PointConv import ...` resolve.
WORKFLOW_ROOT = Path(__file__).resolve().parent.parent
POINTCONV_ROOT = WORKFLOW_ROOT / "tf1" / "PointCONV"
MODEL_CODE_DIR = POINTCONV_ROOT / "model_code_PointCONV"
sys.path.insert(0, str(POINTCONV_ROOT))
sys.path.insert(0, str(MODEL_CODE_DIR))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True,
                        help="Folder with train/, val/, test/ subdirs of lrn_*_*.npy files.")
    parser.add_argument("--model-out", type=Path, required=True,
                        help="Output folder (a new model directory will be created inside).")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--epochs", type=int, default=0,
                        help="Override training.full_training_epochs from config (0 = use config).")
    parser.add_argument("--smoke-test", action="store_true",
                        help="Run only training.smoke_test_epochs and skip best-model copy.")
    parser.add_argument("--max-train-regions", type=int, default=0)
    parser.add_argument("--max-val-regions", type=int, default=0)
    return parser.parse_args()


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as h:
        return yaml.safe_load(h)


def list_regions(split_dir: Path) -> list[tuple[Path, Path, Path]]:
    """Return list of (xyz_path, class_path, smpw_path) tuples for a split."""
    out: list[tuple[Path, Path, Path]] = []
    for xyz_path in sorted(split_dir.rglob("lrn_xyz_*.npy")):
        stem = xyz_path.stem.replace("lrn_xyz_", "")
        cls_path = xyz_path.parent / f"lrn_class_{stem}.npy"
        smpw_path = xyz_path.parent / f"lrn_smpw_{stem}.npy"
        if cls_path.exists() and smpw_path.exists():
            out.append((xyz_path, cls_path, smpw_path))
    return out


class FinetuneDataset:
    """Loads all regions for a split into memory."""

    def __init__(self, regions: list[tuple[Path, Path, Path]], num_classes: int):
        if not regions:
            raise ValueError("Empty region list")
        xyz_list, cls_list, smpw_list = [], [], []
        for xyz_p, cls_p, smpw_p in regions:
            xyz_list.append(np.load(xyz_p).astype(np.float32))
            cls_list.append(np.load(cls_p).astype(np.int32))
            smpw_list.append(np.load(smpw_p).astype(np.float32))
        self.xyz = np.stack(xyz_list, axis=0)        # [R, N, 3]
        self.cls = np.stack(cls_list, axis=0)        # [R, N]
        self.smpw_mask = np.stack(smpw_list, axis=0)  # [R, N], 0/1
        self.num_classes = int(num_classes)
        self.num_points = self.xyz.shape[1]
        self.dim = self.xyz.shape[2]

    def __len__(self):
        return self.xyz.shape[0]

    def label_histogram(self) -> np.ndarray:
        """Per-class point counts considering only valid (smpw>0) points."""
        hist = np.zeros((self.num_classes,), dtype=np.int64)
        valid = self.smpw_mask > 0
        for c in range(self.num_classes):
            hist[c] = int(np.count_nonzero((self.cls == c) & valid))
        return hist

    def get_batch(self, idxs: np.ndarray, class_weights: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        bsize = idxs.shape[0]
        xyz = self.xyz[idxs]
        cls = self.cls[idxs]
        mask = self.smpw_mask[idxs]
        # weight per point = mask * class_weights[label]
        cls_safe = np.clip(cls, 0, self.num_classes - 1)
        per_point_w = class_weights[cls_safe] * mask
        return xyz, cls, per_point_w.astype(np.float32)


def compute_class_weights(hist: np.ndarray, w_min: float, w_max: float, active: list[int]) -> np.ndarray:
    """Inverse-frequency style weights over active classes; non-active = 0."""
    weights = np.zeros_like(hist, dtype=np.float32)
    active_arr = np.asarray(active, dtype=np.int64)
    active_counts = hist[active_arr].astype(np.float64)
    if np.any(active_counts <= 0):
        # avoid div-by-zero; keep weight=1 for unobserved active classes
        active_counts = np.where(active_counts > 0, active_counts, 1.0)
    freq = active_counts / active_counts.sum()
    raw = freq.max() / freq
    raw = np.power(raw, 1.0 / 3.0)  # cube-root soft scaling, matches example trainer
    raw = np.clip(raw, w_min, w_max)
    for i, c in enumerate(active):
        weights[c] = float(raw[i])
    return weights


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", stream=sys.stdout)
    log = logging.getLogger("finetune")
    args = parse_args()

    # TensorFlow imports happen here so help/argparse don't pay the import cost.
    import tensorflow.compat.v1 as tf
    tf.disable_eager_execution()

    cfg = load_yaml(args.config)

    arch = cfg["architecture"]
    train_cfg = cfg["training"]
    num_classes = int(arch["num_classes"])
    bandwidth = float(arch["bandwidth"])
    radii = [float(x) for x in arch["radii"]]
    dim = int(arch["dim"])
    classes = list(arch["classes"])
    active = [int(x) for x in cfg["active_class_indices"]]
    n_points = int(cfg["sampling"]["n_points"])

    epochs = int(args.epochs) if args.epochs > 0 else int(train_cfg["full_training_epochs"])
    if args.smoke_test:
        epochs = int(train_cfg["smoke_test_epochs"])
    batch_size = int(train_cfg["batch_size"])
    base_lr = float(train_cfg["learning_rate"])
    decay_step = int(train_cfg["decay_step"])
    decay_rate = float(train_cfg["decay_rate"])
    bn_init_decay = float(train_cfg["bn_init_decay"])
    bn_decay_decay_rate = float(train_cfg["bn_decay_decay_rate"])
    bn_decay_clip = float(train_cfg["bn_decay_clip"])
    use_class_weight = bool(train_cfg["use_class_weight"])
    w_min = float(train_cfg["class_weight_min"])
    w_max = float(train_cfg["class_weight_max"])
    seed = int(train_cfg["random_seed"])
    gpu_index = int(train_cfg["gpu_index"])

    np.random.seed(seed)
    tf.set_random_seed(seed)

    # === data ===
    data_root = args.data_root.resolve()
    train_regions = list_regions(data_root / "train")
    val_regions = list_regions(data_root / "val")
    if args.max_train_regions:
        train_regions = train_regions[: args.max_train_regions]
    if args.max_val_regions:
        val_regions = val_regions[: args.max_val_regions]
    log.info(f"Train regions: {len(train_regions)}  Val regions: {len(val_regions)}")
    if not train_regions or not val_regions:
        raise SystemExit("Need at least one region in train and val splits")

    log.info("Loading train split into memory...")
    train_ds = FinetuneDataset(train_regions, num_classes)
    log.info(f"Train shape: {train_ds.xyz.shape}")

    log.info("Loading val split into memory...")
    val_ds = FinetuneDataset(val_regions, num_classes)
    log.info(f"Val shape: {val_ds.xyz.shape}")

    train_hist = train_ds.label_histogram()
    log.info(f"Train label histogram (valid): {train_hist.tolist()}")

    if use_class_weight:
        class_weights = compute_class_weights(train_hist, w_min, w_max, active)
    else:
        class_weights = np.zeros((num_classes,), dtype=np.float32)
        for c in active:
            class_weights[c] = 1.0
    log.info(f"Class weights (frozen indices stay 0): {class_weights.tolist()}")

    # === output dirs ===
    model_out_root = args.model_out.resolve()
    model_dir_name = cfg["model_dir_name"]
    model_path = model_out_root / model_dir_name
    best_model_dir = model_path / "Best_Model"
    log_dir = (args.log_dir or (model_out_root / "log")).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    best_model_dir.mkdir(parents=True, exist_ok=True)
    model_path.mkdir(parents=True, exist_ok=True)

    # Persist exp_def.p with arch settings (compatible with the inference path).
    # Dataset.py reads `scale_type` to choose mean (0) vs median (anything else)
    # XY centering. We use median, matching the warm-start model's exp_def.p.
    exp_def = {
        "BANDWIDTH": bandwidth,
        "NUM_CLASSES": num_classes,
        "BN_INIT_DECAY": bn_init_decay,
        "BN_DECAY_DECAY_RATE": bn_decay_decay_rate,
        "BN_DECAY_DECAY_STEP": float(decay_step),
        "BN_DECAY_CLIP": bn_decay_clip,
        "model": "PointConv_Seg",
        "std_min": 1e-5,
        "scale_type": 5,
        "classes": classes,
        "class2label": {c: i for i, c in enumerate(classes)},
        "seg_label_to_cat": {i: c for i, c in enumerate(classes)},
        "radii": radii,
        "Radius_NN": float(cfg["sampling"]["radius_nn"]),
        "acc_type": None,
        "dim": dim,
        "USE_RANDOM_ROTATE": bool(train_cfg["use_random_rotate"]),
        "USE_RANDOM_JITTER": bool(train_cfg["use_random_jitter"]),
        "SIGMA_JITTER": float(train_cfg["sigma_jitter"]),
        "CLIP_JITTER": float(train_cfg["clip_jitter"]),
        "NUM_POINT": n_points,
        "config": train_cfg,
        "fine_tune_source_checkpoint": cfg["warm_start_checkpoint"],
        "active_class_indices": active,
    }
    with (model_path / "exp_def.p").open("wb") as h:
        pickle.dump(exp_def, h)

    # === model graph ===
    import importlib
    MODEL = importlib.import_module("PointConv_Seg")

    with tf.Graph().as_default():
        with tf.device(f"/gpu:{gpu_index}"):
            pointclouds_pl, labels_pl, smpws_pl = MODEL.placeholder_inputs(batch_size, n_points, dim)
            is_training_pl = tf.placeholder(tf.bool, shape=())
            global_step = tf.Variable(0, trainable=False, name="global_step")

            bn_decay = tf.minimum(
                bn_decay_clip,
                1 - tf.train.exponential_decay(
                    bn_init_decay, global_step * batch_size,
                    float(decay_step), bn_decay_decay_rate, staircase=True,
                ),
            )

            log.info("Building model graph...")
            pred, end_points = MODEL.get_model(
                pointclouds_pl,
                is_training_pl,
                num_classes,
                bandwidth,
                bn_decay=bn_decay,
                radii=radii,
            )
            # pred is softmax output, shape [B, N, num_classes].

            # 5-class IoU-weighted loss: slice to active classes, freeze tower.
            #
            # `pred` is post-softmax over all `num_classes` columns. Slicing
            # alone leaves the loss dependent on the dropped logit because the
            # softmax denominator includes it -- in our case that produced
            # ~1-2% drift on the tower fc2 weights during training. Re-
            # normalizing the slice so the kept columns sum to 1 makes the
            # result identically `softmax(logits[active])` for those active
            # logits, and any gradient w.r.t. the dropped (tower) logit
            # cancels out. The tower head therefore gets *no* gradient signal
            # at all.
            active_idx = tf.constant(active, dtype=tf.int32)
            pred_active = tf.gather(pred, active_idx, axis=-1)        # [B, N, K_active]
            pred_active = pred_active / (
                tf.reduce_sum(pred_active, axis=-1, keepdims=True) + 1e-8
            )
            labels_safe = tf.clip_by_value(labels_pl, 0, num_classes - 1)
            one_hot_full = tf.one_hot(labels_safe, num_classes)
            one_hot_active = tf.gather(one_hot_full, active_idx, axis=-1)
            smpws_expanded = tf.expand_dims(smpws_pl, axis=-1)
            tiled_w = tf.tile(smpws_expanded, [1, 1, len(active)]) / float(len(active))
            intersection = one_hot_active * pred_active * tiled_w
            union = one_hot_active + pred_active - intersection
            i_sum = tf.reduce_sum(intersection, axis=-2)
            u_sum = tf.reduce_sum(union, axis=-2)
            iou_per_class = i_sum / (u_sum + 1e-6)
            mean_iou = tf.reduce_mean(iou_per_class)
            loss = 1.0 - mean_iou
            tf.summary.scalar("loss", loss)
            tf.summary.scalar("active_mean_iou", mean_iou)

            # Accuracy (over points where smpw>0).
            valid_mask_f = tf.cast(smpws_pl > 0, tf.float32)
            preds_argmax = tf.cast(tf.argmax(pred, axis=2), tf.int32)
            correct_int = tf.cast(tf.equal(preds_argmax, labels_pl), tf.float32)
            valid_sum = tf.reduce_sum(valid_mask_f) + 1e-6
            accuracy = tf.reduce_sum(correct_int * valid_mask_f) / valid_sum
            tf.summary.scalar("accuracy", accuracy)

            # Optimizer.
            learning_rate = tf.maximum(
                tf.train.exponential_decay(
                    base_lr, global_step * batch_size,
                    float(decay_step), decay_rate, staircase=True,
                ),
                1e-5,
            )
            tf.summary.scalar("learning_rate", learning_rate)
            optim_name = train_cfg.get("optimizer", "adam").lower()
            if optim_name == "momentum":
                optimizer = tf.train.MomentumOptimizer(learning_rate, momentum=float(train_cfg["momentum"]))
            else:
                optimizer = tf.train.AdamOptimizer(learning_rate)
            train_op = optimizer.minimize(loss, global_step=global_step)

            saver = tf.train.Saver(max_to_keep=4)

        sess_config = tf.ConfigProto()
        sess_config.gpu_options.allow_growth = True
        sess_config.allow_soft_placement = True
        sess = tf.Session(config=sess_config)

        merged = tf.summary.merge_all()
        train_writer = tf.summary.FileWriter(str(log_dir / "train"), sess.graph)
        val_writer = tf.summary.FileWriter(str(log_dir / "val"))

        # Warm-start.
        # The checkpoint may not contain every variable our graph needs (e.g. our
        # global_step, the Adam optimizer slots). We build a Saver whose var_list
        # is the intersection of the current graph's globals and the variables
        # actually present in the checkpoint, so the restore succeeds and the
        # missing ones keep their initial values.
        sess.run(tf.global_variables_initializer())
        warm = cfg["warm_start_checkpoint"]
        try:
            ckpt_var_names = {name for name, _ in tf.train.list_variables(warm)}
            graph_vars = tf.global_variables()
            restorable = [v for v in graph_vars if v.name.split(":")[0] in ckpt_var_names]
            skipped = [v.name for v in graph_vars if v.name.split(":")[0] not in ckpt_var_names]
            log.info(f"Restoring {len(restorable)}/{len(graph_vars)} variables from {warm}")
            if skipped:
                log.info(f"  Skipping (not in checkpoint, keeping init): {skipped[:8]}{' ...' if len(skipped) > 8 else ''}")
            if not restorable:
                raise RuntimeError("No variables in graph match checkpoint; refusing to fall back to random init")
            saver_restore = tf.train.Saver(var_list=restorable)
            saver_restore.restore(sess, warm)
            log.info(f"Restored warm-start checkpoint successfully.")
        except Exception as exc:
            log.error(f"Could not restore warm-start checkpoint ({warm}): {exc}")
            log.error("Aborting — fine-tune requires a valid warm start.")
            raise

        # Capture frozen FC2 class-5 weights so we can verify they don't drift.
        # Variable names in this checkpoint are slim-style: fc2/weights and fc2/biases.
        all_vars = tf.global_variables()
        fc2_kernel_var = None
        fc2_bias_var = None
        for v in all_vars:
            n = v.name
            if n.endswith("fc2/weights:0") or n.endswith("fc2/conv1d/kernel:0") or n.endswith("fc2/kernel:0"):
                fc2_kernel_var = v
            elif n.endswith("fc2/biases:0") or n.endswith("fc2/conv1d/bias:0") or n.endswith("fc2/bias:0"):
                fc2_bias_var = v
        frozen_check_pre = None
        frozen_bias_pre = None
        if fc2_kernel_var is not None:
            kernel_pre = sess.run(fc2_kernel_var)
            log.info(f"FC2 weights shape: {kernel_pre.shape} (last axis = num_classes; index 5 = tower)")
            frozen_check_pre = kernel_pre[..., 5].copy()
        if fc2_bias_var is not None:
            bias_pre = sess.run(fc2_bias_var)
            frozen_bias_pre = bias_pre[5].copy()

        def epoch_indices(num_regions: int) -> np.ndarray:
            idxs = np.arange(num_regions)
            np.random.shuffle(idxs)
            usable = (num_regions // batch_size) * batch_size
            return idxs[:usable]

        def run_one_epoch(ds: FinetuneDataset, training: bool, writer, epoch: int) -> dict:
            idxs = epoch_indices(len(ds))
            num_batches = idxs.shape[0] // batch_size
            total_correct, total_seen = 0, 0
            total_loss = 0.0
            tp = np.zeros((num_classes,), dtype=np.int64)
            fp = np.zeros((num_classes,), dtype=np.int64)
            fn = np.zeros((num_classes,), dtype=np.int64)

            for b in range(num_batches):
                batch_ids = idxs[b * batch_size:(b + 1) * batch_size]
                batch_xyz, batch_cls, batch_w = ds.get_batch(batch_ids, class_weights)
                feed = {
                    pointclouds_pl: batch_xyz,
                    labels_pl: batch_cls,
                    smpws_pl: batch_w,
                    is_training_pl: training,
                }
                if training:
                    summary, step, _, loss_val, pred_argmax, acc_val = sess.run(
                        [merged, global_step, train_op, loss, preds_argmax, accuracy],
                        feed_dict=feed,
                    )
                    if writer is not None:
                        writer.add_summary(summary, step)
                else:
                    summary, step, loss_val, pred_argmax, acc_val = sess.run(
                        [merged, global_step, loss, preds_argmax, accuracy],
                        feed_dict=feed,
                    )
                    if writer is not None:
                        writer.add_summary(summary, step)

                valid = batch_w > 0
                total_correct += int(np.count_nonzero((pred_argmax == batch_cls) & valid))
                total_seen += int(valid.sum())
                total_loss += float(loss_val)

                for c in range(num_classes):
                    pred_is_c = (pred_argmax == c) & valid
                    gt_is_c = (batch_cls == c) & valid
                    tp[c] += int(np.count_nonzero(pred_is_c & gt_is_c))
                    fp[c] += int(np.count_nonzero(pred_is_c & ~gt_is_c))
                    fn[c] += int(np.count_nonzero(~pred_is_c & gt_is_c))

            iou = np.zeros((num_classes,), dtype=np.float64)
            for c in range(num_classes):
                denom = tp[c] + fp[c] + fn[c]
                iou[c] = (tp[c] / denom) if denom > 0 else float("nan")
            active_iou = np.array([iou[c] for c in active])
            active_iou_valid = active_iou[~np.isnan(active_iou)]
            mean_iou_value = float(np.nanmean(active_iou_valid)) if active_iou_valid.size else float("nan")

            return {
                "epoch": epoch,
                "num_batches": int(num_batches),
                "loss_mean": total_loss / max(num_batches, 1),
                "accuracy": (total_correct / max(total_seen, 1)),
                "iou_per_class": iou.tolist(),
                "mean_iou_active": mean_iou_value,
            }

        history: list[dict[str, Any]] = []
        best_score = -1.0
        best_epoch = -1
        no_improve = 0
        patience = int(train_cfg.get("early_stop_patience", 0))

        # Class-name lookup for log output.
        for ep in range(epochs):
            t0 = time.time()
            train_res = run_one_epoch(train_ds, training=True, writer=train_writer, epoch=ep)
            val_res = run_one_epoch(val_ds, training=False, writer=val_writer, epoch=ep)
            t1 = time.time()
            log.info(
                f"epoch {ep:03d} | train loss={train_res['loss_mean']:.4f} acc={train_res['accuracy']:.4f} "
                f"miou={train_res['mean_iou_active']:.4f} | "
                f"val loss={val_res['loss_mean']:.4f} acc={val_res['accuracy']:.4f} "
                f"miou={val_res['mean_iou_active']:.4f} | "
                f"per-class val IoU={['%.3f' % v for v in val_res['iou_per_class']]} "
                f"({t1 - t0:.1f}s)"
            )
            history.append({"train": train_res, "val": val_res, "wall_seconds": t1 - t0})

            # Save best by val mean IoU on active classes.
            score = val_res["mean_iou_active"]
            improved = (not np.isnan(score)) and (score > best_score)
            if improved:
                best_score = score
                best_epoch = ep
                no_improve = 0
                if not args.smoke_test:
                    save_path = saver.save(sess, str(model_path / "model.ckpt"))
                    log.info(f"  best model saved to {save_path}")
                    # Mirror to Best_Model dir for downstream inference.
                    for f in os.listdir(model_path):
                        if f.startswith("model.ckpt"):
                            shutil.copy(model_path / f, best_model_dir / f)
            else:
                no_improve += 1
                if patience and no_improve >= patience:
                    log.info(f"Early stopping at epoch {ep} (no improvement for {patience} epochs)")
                    break

        # Verify FC2 class-5 weights stayed frozen.
        if fc2_kernel_var is not None and frozen_check_pre is not None:
            kernel_post = sess.run(fc2_kernel_var)
            diff = np.abs(kernel_post[..., 5] - frozen_check_pre).max()
            log.info(f"FC2 weights[..., 5] (tower) max abs change after training: {float(diff):.3e}")
        if fc2_bias_var is not None and frozen_bias_pre is not None:
            bias_post = sess.run(fc2_bias_var)
            diff_b = abs(float(bias_post[5]) - float(frozen_bias_pre))
            log.info(f"FC2 biases[5] (tower) abs change after training: {diff_b:.3e}")

        # Final test-set evaluation (using the best checkpoint when available).
        test_result: dict[str, Any] | None = None
        test_split_dir = data_root / "test"
        test_regions = list_regions(test_split_dir)
        if test_regions:
            log.info(f"Loading test split into memory ({len(test_regions)} regions)...")
            try:
                if best_epoch >= 0 and not args.smoke_test:
                    saver.restore(sess, str(model_path / "model.ckpt"))
                    log.info("Restored best checkpoint for test evaluation")
                test_ds = FinetuneDataset(test_regions, num_classes)
                test_result = run_one_epoch(test_ds, training=False, writer=None, epoch=-1)
                log.info(
                    f"TEST | loss={test_result['loss_mean']:.4f} acc={test_result['accuracy']:.4f} "
                    f"miou={test_result['mean_iou_active']:.4f} | "
                    f"per-class={['%.3f' % v for v in test_result['iou_per_class']]}"
                )
            except Exception as exc:
                log.warning(f"Test evaluation failed: {exc}")
        else:
            log.info("No test regions found; skipping test evaluation")

        # History dump.
        with (log_dir / "history.json").open("w", encoding="utf-8") as h:
            import json
            json.dump({
                "epochs_run": len(history),
                "best_epoch": int(best_epoch),
                "best_val_mean_iou_active": float(best_score),
                "class_names": classes,
                "active_class_indices": active,
                "class_weights": class_weights.tolist(),
                "history": history,
                "test_result": test_result,
                "started_at": datetime.now().isoformat(timespec="seconds"),
            }, h, indent=2)
        log.info(f"History written to {log_dir / 'history.json'}")
        log.info(f"Best epoch: {best_epoch}  best val mean IoU (active): {best_score:.4f}")
        if test_result:
            log.info(f"Test mean IoU (active): {test_result['mean_iou_active']:.4f}")
        log.info(f"Model saved at: {model_path}")


if __name__ == "__main__":
    main()
