"""Prepare 16,384-point fine-tune samples from DTECH thinned LAS files.

Pipeline:
  1. Load each 0.1 m thinned LAS file.
  2. Map each point's DTECH classification to a model index 0..5 (or -1 = ignore)
     using dtech_to_model_mapping.yml.
  3. Sample the full point cloud into 16,384-point regions with the same
     divide_and_conquer sampler the inference path uses.
  4. Compute each region's XY centroid; assign the region to train/val/test
     using a deterministic spatial-block hash so adjacent regions share splits.
  5. Save lrn_xyz_<i>.npy, lrn_class_<i>.npy, lrn_smpw_<i>.npy per region.
     - lrn_xyz_*.npy : (16384, 3) float32, mean-subtracted XYZ
     - lrn_class_*.npy : (16384,) uint8, model index in 0..5 (5 will be 0-count for this dataset)
     - lrn_smpw_*.npy : (16384,) float32, 0.0 for ignored points, 1.0 otherwise

The trainer multiplies smpw by per-class inverse-frequency weights at run time.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import laspy
import numpy as np
import yaml


# Ensure we can import the inference-time sampler.
WORKFLOW_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(WORKFLOW_ROOT / "tf1" / "PointCONV"))
from sample_pts import divide_and_conquer_sample_groups  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument(
        "--source-thinned-dir",
        type=Path,
        required=True,
        help="Folder containing 0.1 m thinned *_thin_0p1m.las files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Where to write {train,val,test} subfolders of npy files.",
    )
    parser.add_argument("--limit-files", type=int, default=0, help="0 = all")
    parser.add_argument("--max-regions-per-source", type=int, default=0, help="0 = no cap")
    return parser.parse_args()


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_mapping(mapping_yml: dict) -> tuple[np.ndarray, dict[int, int]]:
    """Return (lookup_array, source_to_idx) where lookup_array[c] = model_idx or -1."""
    raw = mapping_yml.get("source_to_model_idx") or {}
    src_to_idx: dict[int, int] = {int(k): int(v) for k, v in raw.items()}
    if not src_to_idx:
        raise ValueError("Empty source_to_model_idx in mapping yml")
    max_src = max(src_to_idx.keys())
    lut_size = max(max_src + 1, 256)
    lut = np.full((lut_size,), fill_value=-1, dtype=np.int8)
    for src, idx in src_to_idx.items():
        lut[src] = np.int8(idx)
    return lut, src_to_idx


def split_for_centroid(
    cx: float,
    cy: float,
    block_size_m: float,
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    seed: int,
) -> str:
    """Deterministic spatial-block-hash split. cx, cy are in meters."""
    bx = int(np.floor(cx / block_size_m))
    by = int(np.floor(cy / block_size_m))
    # 32-bit mix that depends on both coords and the seed.
    h = ((bx * 73856093) ^ (by * 19349663) ^ (seed * 83492791)) & 0xFFFFFFFF
    u = (h % 10000) / 10000.0  # in [0,1)
    if u < train_ratio:
        return "train"
    if u < train_ratio + val_ratio:
        return "val"
    return "test"


def process_one_source(
    las_path: Path,
    output_root: Path,
    lut: np.ndarray,
    cfg: dict,
    max_regions: int,
) -> dict[str, Any]:
    log = logging.getLogger("prep")
    log.info(f"Reading {las_path.name}")
    las = laspy.read(las_path)
    xyz = np.column_stack((np.asarray(las.x), np.asarray(las.y), np.asarray(las.z))).astype(np.float64)
    classification = np.asarray(las.classification, dtype=np.int32)

    n_total = xyz.shape[0]
    log.info(f"  {n_total:,} thinned points")

    # Per-point model index (-1 for ignore).
    safe_class = np.clip(classification, 0, lut.shape[0] - 1)
    model_idx = lut[safe_class].astype(np.int32)
    smpw_mask = (model_idx >= 0).astype(np.float32)

    # Sampler operates on XY.
    samp = cfg["sampling"]
    sample_ids, _coverage = divide_and_conquer_sample_groups(
        xyz[:, :2],
        r=float(samp["radius_nn"]),
        k=int(samp["n_points"]),
        q=int(samp["min_samples_per_point"]),
        max_points_per_region=int(samp["max_points_per_region"]),
        num_threads=int(samp["num_threads"]),
        num_candidates_in=int(samp["num_candidates"]),
        min_points_in_region=int(samp["min_points_in_region"]),
    )
    log.info(f"  sampled {len(sample_ids)} regions of {samp['n_points']} points")

    if max_regions and len(sample_ids) > max_regions:
        rng = np.random.default_rng(int(samp["random_seed"]))
        keep = rng.choice(len(sample_ids), size=max_regions, replace=False)
        sample_ids = sample_ids[keep]
        log.info(f"  capped to {len(sample_ids)} regions")

    split_cfg = cfg["spatial_split"]
    counts = {"train": 0, "val": 0, "test": 0}
    rare_kept = {0: 0, 1: 0}  # wire, pole counts
    source_stem = las_path.stem

    region_records: list[dict[str, Any]] = []

    for region_i, idx in enumerate(sample_ids):
        idx = np.asarray(idx, dtype=np.int64)
        region_xyz = xyz[idx]
        region_cls = model_idx[idx]
        region_smpw = smpw_mask[idx]

        cx, cy = float(np.median(region_xyz[:, 0])), float(np.median(region_xyz[:, 1]))
        split = split_for_centroid(
            cx, cy,
            block_size_m=float(split_cfg["block_size_m"]),
            train_ratio=float(split_cfg["train_ratio"]),
            val_ratio=float(split_cfg["val_ratio"]),
            test_ratio=float(split_cfg["test_ratio"]),
            seed=int(split_cfg["seed"]),
        )

        # Mean-subtract within the region (matches what the inference sampler does).
        scale_sub = np.median(region_xyz, axis=0).astype(np.float64)
        region_xyz_centered = (region_xyz - scale_sub).astype(np.float32)

        # Where the model index is invalid (ignore), park the label at 0 with smpw=0.
        region_cls_save = np.where(region_smpw > 0, region_cls, 0).astype(np.uint8)

        out_dir = output_root / split / source_stem
        out_dir.mkdir(parents=True, exist_ok=True)
        np.save(out_dir / f"lrn_xyz_{region_i:06d}.npy", region_xyz_centered)
        np.save(out_dir / f"lrn_class_{region_i:06d}.npy", region_cls_save)
        np.save(out_dir / f"lrn_smpw_{region_i:06d}.npy", region_smpw.astype(np.float32))
        np.save(out_dir / f"lrn_scale_{region_i:06d}.npy", scale_sub)

        counts[split] += 1
        valid = region_smpw > 0
        if np.any(valid):
            for k in (0, 1):
                rare_kept[k] += int(np.count_nonzero(region_cls[valid] == k))

        region_records.append({
            "region_index": region_i,
            "split": split,
            "centroid_xy": [cx, cy],
            "out_dir": str(out_dir),
        })

    return {
        "source_file": str(las_path),
        "source_stem": source_stem,
        "thinned_point_count": int(n_total),
        "regions": int(len(sample_ids)),
        "split_counts": counts,
        "rare_class_point_count": rare_kept,
        "region_records": region_records,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", stream=sys.stdout)
    args = parse_args()
    cfg = load_yaml(args.config)
    mapping_yml = load_yaml(args.mapping)
    lut, src_to_idx = build_mapping(mapping_yml)

    src_dir = args.source_thinned_dir.resolve()
    out_root = args.output_dir.resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val", "test"):
        (out_root / split).mkdir(parents=True, exist_ok=True)

    las_files = sorted(p for p in src_dir.glob("*.las") if p.is_file())
    if args.limit_files > 0:
        las_files = las_files[: args.limit_files]
    if not las_files:
        raise SystemExit(f"No LAS files in {src_dir}")

    log = logging.getLogger("prep")
    log.info(f"Found {len(las_files)} thinned LAS file(s)")
    log.info(f"Mapping covers {len(src_to_idx)} source classes; {(lut == -1).sum()} of LUT slots are ignore")

    summaries: list[dict[str, Any]] = []
    for las_path in las_files:
        summary = process_one_source(
            las_path=las_path,
            output_root=out_root,
            lut=lut,
            cfg=cfg,
            max_regions=int(args.max_regions_per_source),
        )
        summaries.append(summary)

    # Aggregate label histograms across the train split for class-weight planning.
    train_label_hist = {i: 0 for i in range(int(cfg["architecture"]["num_classes"]))}
    train_total_valid = 0
    for split in ("train",):
        for npy_path in sorted((out_root / split).rglob("lrn_class_*.npy")):
            stem = npy_path.with_suffix("").name
            i = stem.replace("lrn_class_", "")
            smpw_path = npy_path.parent / f"lrn_smpw_{i}.npy"
            cls = np.load(npy_path)
            sw = np.load(smpw_path)
            valid = sw > 0
            train_total_valid += int(valid.sum())
            uniq, cnt = np.unique(cls[valid], return_counts=True)
            for u, c in zip(uniq, cnt):
                train_label_hist[int(u)] = train_label_hist.get(int(u), 0) + int(c)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "config_path": str(args.config.resolve()),
        "mapping_path": str(args.mapping.resolve()),
        "source_thinned_dir": str(src_dir),
        "output_dir": str(out_root),
        "sources": summaries,
        "train_label_histogram_valid_only": train_label_hist,
        "train_total_valid_points": train_total_valid,
        "split_dir_counts": {
            split: sum(s["split_counts"].get(split, 0) for s in summaries) for split in ("train", "val", "test")
        },
    }

    with (out_root / "manifest.json").open("w", encoding="utf-8") as h:
        json.dump(manifest, h, indent=2)
    log.info(f"Manifest written to {out_root / 'manifest.json'}")
    log.info(f"Split region counts: {manifest['split_dir_counts']}")
    log.info(f"Train label histogram (valid only): {train_label_hist}")


if __name__ == "__main__":
    main()
