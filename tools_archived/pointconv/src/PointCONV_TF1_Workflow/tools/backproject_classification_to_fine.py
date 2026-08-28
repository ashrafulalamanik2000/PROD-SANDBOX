"""Back-project PointCONV 0.1 m classifications onto a 0.025 m thinning of
the raw source LAS.

Produces a high-resolution classified LAS suitable for pole-vec's wire /
crossarm / transformer extraction (which sees too few points per feature
at 0.1 m). Each fine point inherits classification + extra dims from its
1-nearest-neighbor in the 0.1 m classified cloud. Fine points farther
than `--match-radius` from any 0.1 m point get class=0 (unclassified).

Memory: streams the raw LAS via laspy.chunk_iterator so 100 M-point files
don't blow up. Loads the 0.1 m classified LAS fully (~5 M pts/source --
fits comfortably). Builds one cKDTree per source.

Usage:
    python backproject_classification_to_fine.py \\
        --raw-las         /path/to/<stem>.las \\
        --classified-las  /path/to/<stem>_tf1_pointconv_combined_0p1m.las \\
        --out-las         /path/to/<stem>_tf1_pointconv_combined_0p025m.las \\
        --fine-voxel-size 0.025 \\
        --match-radius    0.5 \\
        [--chunk-size 500000]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import laspy
import numpy as np
from scipy.spatial import cKDTree


PC_EXTRA_DIMS = ("source_class", "pointconv_prob", "pointconv_votes")


def log(msg: str) -> None:
    print(f"[backproject] {msg}", flush=True)


def stream_voxelize(raw_path: Path, voxel_size_m: float,
                    chunk_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Read raw LAS in chunks, voxelize to voxel_size_m (first-point-per-voxel
    within each chunk; cross-chunk duplicates accepted -- they're rare and
    only add a tiny redundancy at chunk boundaries, mirroring the existing
    streaming tile builder's behavior).

    Returns (Nx3 float64 XYZ, N uint16 intensity) in source CRS units.
    Intensity is the RAW sensor value of the kept first-point-per-voxel —
    downstream curb detection uses intensity features, and a zeroed-intensity
    back-projection silently produced 0 curb lines (2026-06-05 finding).
    """
    xs, ys, zs, ins = [], [], [], []
    t0 = time.time()
    total_raw = 0
    with laspy.open(raw_path) as reader:
        for chunk in reader.chunk_iterator(chunk_size):
            cx = np.asarray(chunk.x, dtype=np.float64)
            cy = np.asarray(chunk.y, dtype=np.float64)
            cz = np.asarray(chunk.z, dtype=np.float64)
            ci = np.asarray(chunk.intensity, dtype=np.uint16)
            total_raw += len(cx)
            kx = np.floor(cx / voxel_size_m).astype(np.int64)
            ky = np.floor(cy / voxel_size_m).astype(np.int64)
            kz = np.floor(cz / voxel_size_m).astype(np.int64)
            keys = np.column_stack((kx, ky, kz))
            _, first = np.unique(keys, axis=0, return_index=True)
            first.sort()
            xs.append(cx[first])
            ys.append(cy[first])
            zs.append(cz[first])
            ins.append(ci[first])
    x = np.concatenate(xs) if xs else np.empty(0, dtype=np.float64)
    y = np.concatenate(ys) if ys else np.empty(0, dtype=np.float64)
    z = np.concatenate(zs) if zs else np.empty(0, dtype=np.float64)
    i = np.concatenate(ins) if ins else np.empty(0, dtype=np.uint16)
    log(f"  voxelize {raw_path.name}: {total_raw:,} raw -> {len(x):,} "
        f"@ {voxel_size_m} m ({time.time()-t0:.1f}s)")
    return np.column_stack((x, y, z)), i


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw-las", required=True, type=Path)
    p.add_argument("--classified-las", required=True, type=Path,
                   help="0.1 m PointCONV-classified LAS (with extra dims)")
    p.add_argument("--out-las", required=True, type=Path)
    p.add_argument("--fine-voxel-size", type=float, default=0.025)
    p.add_argument("--match-radius", type=float, default=0.5,
                   help="Fine points farther than this from any classified "
                        "neighbor get class=0 (unclassified)")
    p.add_argument("--chunk-size", type=int, default=500_000)
    args = p.parse_args()

    if not args.raw_las.is_file():
        raise SystemExit(f"raw LAS not found: {args.raw_las}")
    if not args.classified_las.is_file():
        raise SystemExit(f"classified LAS not found: {args.classified_las}")
    args.out_las.parent.mkdir(parents=True, exist_ok=True)

    # 1. Voxelize raw to fine resolution (streaming). Keeps the raw sensor
    #    intensity of each surviving point.
    fine_xyz, fine_intensity = stream_voxelize(
        args.raw_las, args.fine_voxel_size, args.chunk_size)
    if len(fine_xyz) == 0:
        raise SystemExit("voxelization produced 0 points")

    # 2. Load the 0.1 m classified LAS fully.
    t0 = time.time()
    cls_las = laspy.read(args.classified_las)
    coarse_xyz = np.column_stack((
        np.asarray(cls_las.x, dtype=np.float64),
        np.asarray(cls_las.y, dtype=np.float64),
        np.asarray(cls_las.z, dtype=np.float64),
    ))
    coarse_cls = np.asarray(cls_las.classification, dtype=np.uint8)
    extras = {}
    for name in PC_EXTRA_DIMS:
        if name in cls_las.point_format.dimension_names:
            extras[name] = np.asarray(getattr(cls_las, name))
        else:
            extras[name] = None
            log(f"  WARN: classified LAS missing extra dim '{name}'")
    log(f"  loaded classified: {len(coarse_xyz):,} pts "
        f"({time.time()-t0:.1f}s)")

    # 3. KD-tree on classified XYZ, 1-NN query for every fine point.
    t0 = time.time()
    tree = cKDTree(coarse_xyz)
    dist, idx = tree.query(fine_xyz, k=1, workers=-1)
    in_range = dist <= args.match_radius
    n_in = int(in_range.sum())
    n_out = len(fine_xyz) - n_in
    log(f"  KD-tree + 1-NN: {len(fine_xyz):,} fine pts, "
        f"{n_in:,} in range, {n_out:,} out (>= {args.match_radius} m) "
        f"({time.time()-t0:.1f}s)")

    # 4. Build the output LAS: same header as the classified LAS (preserves
    #    point format + extra dims + scales/offsets).
    t0 = time.time()
    out = laspy.LasData(cls_las.header)
    # laspy expects each scalar dim to be assigned all at once after points
    # are allocated. Easiest path: allocate via .points.
    out_count = len(fine_xyz)
    out.points = laspy.ScaleAwarePointRecord.zeros(
        out_count,
        header=cls_las.header,
    )
    out.x = fine_xyz[:, 0]
    out.y = fine_xyz[:, 1]
    out.z = fine_xyz[:, 2]
    # Raw sensor intensity of the kept points (NOT the 0.1 m NN's value —
    # PointCONV's combined output rescales/drops intensity; the raw value is
    # what curb-skill's intensity features were trained on).
    out.intensity = fine_intensity

    # Classification: copy from 1-NN, class 0 for out-of-range.
    new_cls = coarse_cls[idx].copy()
    new_cls[~in_range] = 0
    out.classification = new_cls

    for name, arr in extras.items():
        if arr is None:
            continue
        copied = arr[idx].copy()
        if name == "pointconv_prob":
            copied[~in_range] = 0.0
        elif name == "pointconv_votes":
            copied[~in_range] = 0
        elif name == "source_class":
            copied[~in_range] = 0
        setattr(out, name, copied)

    out.write(args.out_las)
    sz_mb = args.out_las.stat().st_size / 1e6
    log(f"  wrote {args.out_las.name}  ({out_count:,} pts, {sz_mb:.1f} MB)"
        f" ({time.time()-t0:.1f}s)")
    log("done.")


if __name__ == "__main__":
    main()
