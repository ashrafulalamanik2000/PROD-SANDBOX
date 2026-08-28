"""Combine N LAS files into one and voxelize the union at a target voxel size.

Two supported input shapes:

1. Raw or pre-classified LAS files (no PointCONV extra dimensions)
   - Use case: combine left + right (or multi-pass) lidar runs of the same
     scene into a single thinned input BEFORE running inference.
   - Per-voxel tie-break: highest `intensity`.

2. PointCONV combined-output LAS files (carrying `source_class`,
   `pointconv_prob`, `pointconv_votes` extra dims, written by the
   tiled-inference merge step).
   - Use case: combine N already-classified per-source files into a single
     deliverable.
   - Per-voxel tie-break: highest `pointconv_prob`. Extra dims propagate.

The script auto-detects which mode it's in by looking at the first input.
All inputs must agree on whether they have the PointCONV extra dims.
"""
from __future__ import annotations

import argparse
import copy
import time
from pathlib import Path

import laspy
import numpy as np


PC_EXTRA_DIMS = ("source_class", "pointconv_prob", "pointconv_votes")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs", type=Path, nargs="+", required=True,
                   help="Two or more LAS files to combine.")
    p.add_argument("--output", type=Path, required=True,
                   help="Output LAS path. A matching .laz is also written next to it unless --no-laz is set.")
    p.add_argument("--voxel-size", type=float, default=0.1)
    p.add_argument("--no-laz", action="store_true", help="Skip the LAZ side-output.")
    return p.parse_args()


def has_pc_extra_dims(las: "laspy.LasData") -> bool:
    return all(name in las.point_format.dimension_names for name in PC_EXTRA_DIMS)


def main() -> None:
    args = parse_args()
    inputs = [p.resolve() for p in args.inputs]
    if len(inputs) < 2:
        raise SystemExit("Need at least 2 input LAS files")

    print(f"Voxel size: {args.voxel_size} m")
    print(f"Inputs ({len(inputs)}):")
    for p in inputs:
        sz_mb = p.stat().st_size / 1e6
        print(f"  {p}  ({sz_mb:.1f} MB)")

    print("\nReading inputs...")
    t0 = time.time()
    las_list = [laspy.read(str(p)) for p in inputs]
    template = las_list[0]
    n_total = sum(len(las.points) for las in las_list)
    print(f"  total raw points: {n_total:,}  ({time.time()-t0:.1f}s)")

    # Detect mode: PointCONV inference outputs vs raw / pre-classified.
    pc_modes = [has_pc_extra_dims(las) for las in las_list]
    if any(pc_modes) and not all(pc_modes):
        raise SystemExit(
            "Mixed inputs: some have PointCONV extra dims and some don't. "
            "Use one shape consistently."
        )
    pc_mode = bool(pc_modes[0])
    print(f"\nMode: {'PointCONV combined-output (preserve extra dims)' if pc_mode else 'raw / pre-classified'}")

    # Concatenate scalar dimensions we care about.
    def cat(name):
        return np.concatenate([np.asarray(getattr(las, name)) for las in las_list])

    x = cat("x")
    y = cat("y")
    z = cat("z")
    classification = cat("classification")
    intensity = cat("intensity")
    return_number = cat("return_number")
    number_of_returns = cat("number_of_returns")
    has_rgb = all("red" in las.point_format.dimension_names for las in las_list)
    if has_rgb:
        red = cat("red"); green = cat("green"); blue = cat("blue")
    has_gps = all("gps_time" in las.point_format.dimension_names for las in las_list)
    if has_gps:
        gps_time = cat("gps_time")

    if pc_mode:
        source_class = cat("source_class")
        pointconv_prob = cat("pointconv_prob")
        pointconv_votes = cat("pointconv_votes")
        # Higher pointconv_prob wins per voxel.
        priority = -pointconv_prob.astype(np.float64)
    else:
        # Higher intensity wins per voxel (proxy for return strength / data quality).
        priority = -intensity.astype(np.float64)

    # Voxelize: integer voxel indices, then a single int64 key.
    print(f"\nVoxelizing union at {args.voxel_size} m...")
    t0 = time.time()
    vx = np.floor(x / args.voxel_size).astype(np.int64)
    vy = np.floor(y / args.voxel_size).astype(np.int64)
    vz = np.floor(z / args.voxel_size).astype(np.int64)
    vx -= vx.min(); vy -= vy.min(); vz -= vz.min()
    My = int(vy.max()) + 1
    Mz = int(vz.max()) + 1
    if Mz * My > 2**40:
        raise SystemExit("Voxel grid too large for single-int64 hash; increase voxel size or split spatially")
    key = vx * (My * Mz) + vy * Mz + vz

    # Sort by (key ASC, priority ASC where lower=better) so first hit per unique key wins.
    order = np.lexsort((priority, key))
    key_sorted = key[order]
    _, first_per_voxel = np.unique(key_sorted, return_index=True)
    keep = order[first_per_voxel]
    n_kept = keep.shape[0]
    print(f"  unique voxels kept: {n_kept:,}  ({n_kept / n_total * 100:.1f}% of raw)  ({time.time()-t0:.1f}s)")

    # Build output LAS using template header.
    print("\nWriting output...")
    t0 = time.time()
    out = laspy.LasData(copy.deepcopy(template.header))
    out.x = x[keep]
    out.y = y[keep]
    out.z = z[keep]
    out.classification = classification[keep].astype(np.uint8)
    out.intensity = intensity[keep].astype(np.uint16)
    out.return_number = return_number[keep].astype(np.uint8)
    out.number_of_returns = number_of_returns[keep].astype(np.uint8)
    if has_rgb:
        out.red = red[keep].astype(np.uint16)
        out.green = green[keep].astype(np.uint16)
        out.blue = blue[keep].astype(np.uint16)
    if has_gps:
        out.gps_time = gps_time[keep].astype(np.float64)

    if pc_mode:
        extra_specs = [
            ("source_class", np.uint8, "Source class"),
            ("pointconv_prob", np.float32, "PointCONV prob"),
            ("pointconv_votes", np.uint16, "PointCONV votes"),
        ]
        for name, dtype, desc in extra_specs:
            if name not in out.point_format.dimension_names:
                out.add_extra_dim(laspy.ExtraBytesParams(name=name, type=np.dtype(dtype), description=desc))
        out.source_class = source_class[keep].astype(np.uint8)
        out.pointconv_prob = pointconv_prob[keep].astype(np.float32)
        out.pointconv_votes = pointconv_votes[keep].astype(np.uint16)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.write(str(args.output))
    print(f"  wrote {args.output}  ({args.output.stat().st_size/1e6:.1f} MB)")

    if not args.no_laz:
        laz_out = args.output.with_suffix(".laz")
        out.write(str(laz_out), laz_backend=laspy.LazBackend.LazrsParallel)
        print(f"  wrote {laz_out}  ({laz_out.stat().st_size/1e6:.1f} MB,  {laz_out.stat().st_size/args.output.stat().st_size*100:.1f}% of LAS)")
    print(f"  done ({time.time()-t0:.1f}s)")

    if pc_mode:
        u, c = np.unique(out.classification, return_counts=True)
        labels = {0:'(no pred)', 2:'Ground', 5:'High Veg', 6:'Building', 14:'Wire', 15:'Tower', 18:'Pole'}
        print("\nCombined predicted class histogram:")
        for k, v in zip(u, c):
            print(f"  {int(k):>3} {labels.get(int(k),'?'):<12}  {int(v):>11,}  ({v/n_kept*100:>5.1f}%)")


if __name__ == "__main__":
    main()
