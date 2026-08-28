"""Compute per-class IoU between PointCONV predictions and DTECH source labels.

Reads merged 0.1 m thinned LAS files produced by
post_processing/merge_tf1_tile_predictions.py. Each LAS has:
  - classification: PointCONV predicted LAS class (0 if no vote)
  - source_class:   original DTECH classification at the thinned point

The mapping yml folds DTECH source classes into the 6 PointCONV classes for
ground truth, then we compute confusion matrices and per-class IoU.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import laspy
import numpy as np
import yaml


POINTCONV_LABEL_NAMES = {
    2: "Ground",
    5: "High Vegetation",
    6: "Building",
    14: "Wire",
    15: "Transmission Tower",
    18: "Utility Pole",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--combined-dir",
        type=Path,
        required=True,
        help="Folder containing *_tf1_pointconv_combined_0p1m.las files.",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        required=True,
        help="YAML mapping: DTECH source class -> PointCONV class.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Where to write IoU artifacts (csv + json).",
    )
    parser.add_argument(
        "--pattern",
        type=str,
        default="*_tf1_pointconv_combined_0p1m.las",
        help="Glob pattern for combined LAS files inside --combined-dir.",
    )
    return parser.parse_args()


def load_mapping(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    classes = cfg.get("pointconv_classes") or {}
    if not classes:
        raise ValueError("mapping yml has no pointconv_classes section")
    src_to_pc: dict[int, int] = {}
    pc_to_name: dict[int, str] = {}
    for pc_label, body in classes.items():
        pc_label_int = int(pc_label)
        pc_to_name[pc_label_int] = str(body.get("name", POINTCONV_LABEL_NAMES.get(pc_label_int, str(pc_label_int))))
        for src in body.get("sources", []) or []:
            src_to_pc[int(src)] = pc_label_int
    ignore_sources = {int(x) for x in (cfg.get("ignore_sources") or [])}
    return {
        "src_to_pc": src_to_pc,
        "pc_to_name": pc_to_name,
        "ignore_sources": ignore_sources,
        "ignore_no_prediction": bool(cfg.get("ignore_no_prediction", True)),
        "unmapped_source_policy": str(cfg.get("unmapped_source_policy", "ignore")),
    }


def confusion_matrix(true_labels: np.ndarray, pred_labels: np.ndarray, classes: list[int]) -> np.ndarray:
    """Rows = ground truth, cols = predicted. Entries outside `classes` are dropped."""
    class_to_idx = {c: i for i, c in enumerate(classes)}
    n = len(classes)
    matrix = np.zeros((n, n), dtype=np.int64)

    valid = np.isin(true_labels, classes) & np.isin(pred_labels, classes)
    t = true_labels[valid]
    p = pred_labels[valid]
    if t.size == 0:
        return matrix
    t_idx = np.fromiter((class_to_idx[int(v)] for v in t), dtype=np.int64, count=t.size)
    p_idx = np.fromiter((class_to_idx[int(v)] for v in p), dtype=np.int64, count=p.size)
    flat = t_idx * n + p_idx
    counts = np.bincount(flat, minlength=n * n)
    matrix += counts.reshape(n, n)
    return matrix


def per_class_iou(matrix: np.ndarray, classes: list[int]) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    diag = np.diag(matrix).astype(np.int64)
    row_sum = matrix.sum(axis=1).astype(np.int64)  # ground truth count
    col_sum = matrix.sum(axis=0).astype(np.int64)  # predicted count
    for i, cls in enumerate(classes):
        tp = int(diag[i])
        fn = int(row_sum[i] - diag[i])
        fp = int(col_sum[i] - diag[i])
        union = tp + fp + fn
        iou = float(tp) / union if union > 0 else float("nan")
        precision = float(tp) / (tp + fp) if (tp + fp) > 0 else float("nan")
        recall = float(tp) / (tp + fn) if (tp + fn) > 0 else float("nan")
        out[cls] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "support_gt": int(row_sum[i]),
            "support_pred": int(col_sum[i]),
            "iou": iou,
            "precision": precision,
            "recall": recall,
        }
    return out


def evaluate_file(las_path: Path, mapping: dict[str, Any]) -> dict[str, Any]:
    las = laspy.read(las_path)
    pred = np.asarray(las.classification, dtype=np.int32)
    if "source_class" not in las.point_format.dimension_names:
        raise ValueError(f"{las_path.name} has no source_class extra dim")
    src = np.asarray(las.source_class, dtype=np.int32)
    point_count = pred.shape[0]

    # Map source class -> PointCONV ground-truth class. -1 means ignore.
    src_to_pc = mapping["src_to_pc"]
    ignore_sources = mapping["ignore_sources"]
    pc_classes = sorted(mapping["pc_to_name"].keys())

    gt = np.full_like(src, fill_value=-1, dtype=np.int32)
    for s, pc in src_to_pc.items():
        gt[src == s] = pc
    # Anything explicitly ignored stays at -1; unmapped source classes also stay at -1
    # because we only set values where src_to_pc has a key.
    if mapping["unmapped_source_policy"] != "ignore":
        raise NotImplementedError("only unmapped_source_policy=ignore is implemented")

    # Drop unpredicted points if requested.
    keep = np.ones(point_count, dtype=bool)
    if mapping["ignore_no_prediction"]:
        keep &= pred != 0
    keep &= gt != -1
    keep &= np.isin(pred, pc_classes)
    # Note: predictions that fall outside pc_classes (shouldn't happen for the 6-class model
    # except the 0 sentinel) are dropped above.

    gt_kept = gt[keep]
    pred_kept = pred[keep]

    cm = confusion_matrix(gt_kept, pred_kept, pc_classes)
    per_class = per_class_iou(cm, pc_classes)

    # Distribution of ignored sources for diagnostics.
    ignored_mask = ~keep
    ignored_src_unique, ignored_src_counts = np.unique(src[ignored_mask], return_counts=True)
    ignored_src_hist = {int(k): int(v) for k, v in zip(ignored_src_unique, ignored_src_counts)}

    no_pred_count = int(np.count_nonzero(pred == 0))

    return {
        "file": las_path.name,
        "path": str(las_path.resolve()),
        "point_count": int(point_count),
        "evaluated_count": int(int(keep.sum())),
        "ignored_count": int(int((~keep).sum())),
        "no_prediction_count": no_pred_count,
        "pc_classes": pc_classes,
        "confusion_matrix": cm.tolist(),
        "per_class": per_class,
        "ignored_source_histogram": ignored_src_hist,
    }


def aggregate(file_results: list[dict[str, Any]], pc_classes: list[int]) -> dict[str, Any]:
    cm = np.zeros((len(pc_classes), len(pc_classes)), dtype=np.int64)
    for r in file_results:
        cm += np.asarray(r["confusion_matrix"], dtype=np.int64)
    per_class = per_class_iou(cm, pc_classes)
    # mean over every class (a class with zero GT contributes IoU=0 to this average)
    all_ious = [v["iou"] for v in per_class.values() if not np.isnan(v["iou"])]
    mean_iou_all = float(np.mean(all_ious)) if all_ious else float("nan")
    # mean only over classes that actually appear in the ground truth
    supported_ious = [v["iou"] for v in per_class.values() if v["support_gt"] > 0 and not np.isnan(v["iou"])]
    mean_iou_supported = float(np.mean(supported_ious)) if supported_ious else float("nan")
    return {
        "confusion_matrix": cm.tolist(),
        "per_class": per_class,
        "mean_iou_all": mean_iou_all,
        "mean_iou_supported": mean_iou_supported,
    }


def write_confusion_csv(path: Path, classes: list[int], names: dict[int, str], cm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["gt\\pred"] + [f"{c} {names.get(c, '')}" for c in classes]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for i, cls in enumerate(classes):
            row = [f"{cls} {names.get(cls, '')}"] + [int(cm[i, j]) for j in range(len(classes))]
            writer.writerow(row)


def write_per_class_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    combined_dir = args.combined_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    mapping = load_mapping(args.mapping.resolve())
    pc_classes = sorted(mapping["pc_to_name"].keys())

    las_files = sorted(combined_dir.glob(args.pattern))
    if not las_files:
        raise SystemExit(f"No LAS files matched {args.pattern} in {combined_dir}")

    print(f"Evaluating {len(las_files)} file(s) against {len(pc_classes)} PointCONV classes")
    file_results: list[dict[str, Any]] = []
    per_source_rows: list[dict[str, Any]] = []
    for las_path in las_files:
        print(f"  - {las_path.name}")
        res = evaluate_file(las_path, mapping)
        file_results.append(res)
        for cls in pc_classes:
            stats = res["per_class"][cls]
            per_source_rows.append({
                "source_file": res["file"],
                "pc_class": cls,
                "pc_name": mapping["pc_to_name"][cls],
                "tp": stats["tp"],
                "fp": stats["fp"],
                "fn": stats["fn"],
                "support_gt": stats["support_gt"],
                "support_pred": stats["support_pred"],
                "iou": stats["iou"],
                "precision": stats["precision"],
                "recall": stats["recall"],
            })

    agg = aggregate(file_results, pc_classes)

    # Per-class aggregate rows
    aggregate_rows: list[dict[str, Any]] = []
    for cls in pc_classes:
        stats = agg["per_class"][cls]
        aggregate_rows.append({
            "pc_class": cls,
            "pc_name": mapping["pc_to_name"][cls],
            "tp": stats["tp"],
            "fp": stats["fp"],
            "fn": stats["fn"],
            "support_gt": stats["support_gt"],
            "support_pred": stats["support_pred"],
            "iou": stats["iou"],
            "precision": stats["precision"],
            "recall": stats["recall"],
        })
    aggregate_rows.append({
        "pc_class": "MEAN_ALL",
        "pc_name": "Mean IoU (all 6 classes, unsupported counted as 0)",
        "tp": "",
        "fp": "",
        "fn": "",
        "support_gt": "",
        "support_pred": "",
        "iou": agg["mean_iou_all"],
        "precision": "",
        "recall": "",
    })
    aggregate_rows.append({
        "pc_class": "MEAN_SUPPORTED",
        "pc_name": "Mean IoU (classes with support_gt>0 only)",
        "tp": "",
        "fp": "",
        "fn": "",
        "support_gt": "",
        "support_pred": "",
        "iou": agg["mean_iou_supported"],
        "precision": "",
        "recall": "",
    })

    write_per_class_csv(output_dir / "iou_per_source.csv", per_source_rows)
    write_per_class_csv(output_dir / "iou_aggregate.csv", aggregate_rows)
    write_confusion_csv(
        output_dir / "confusion_matrix.csv",
        pc_classes,
        mapping["pc_to_name"],
        np.asarray(agg["confusion_matrix"], dtype=np.int64),
    )

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "combined_dir": str(combined_dir),
        "mapping_file": str(args.mapping.resolve()),
        "pattern": args.pattern,
        "pc_classes": pc_classes,
        "pc_names": {int(k): v for k, v in mapping["pc_to_name"].items()},
        "ignore_no_prediction": mapping["ignore_no_prediction"],
        "files": file_results,
        "aggregate": agg,
    }
    with (output_dir / "iou_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    print("")
    print("Aggregate IoU:")
    for cls in pc_classes:
        stats = agg["per_class"][cls]
        iou = stats["iou"]
        iou_str = f"{iou:.4f}" if not np.isnan(iou) else "n/a"
        rec = stats["recall"]
        rec_str = f"{rec:.4f}" if not np.isnan(rec) else "n/a"
        print(f"  {cls:>2} {mapping['pc_to_name'][cls]:<22}  IoU={iou_str}  P={stats['precision']:.4f}  R={rec_str}  support_gt={stats['support_gt']}")
    print(f"  Mean IoU (all 6 classes):     {agg['mean_iou_all']:.4f}")
    print(f"  Mean IoU (supported classes): {agg['mean_iou_supported']:.4f}")
    print("")
    print(f"Wrote: {output_dir}")


if __name__ == "__main__":
    main()
