"""Inspect a LAS/LAZ and say which classification stage to start from.

Prints the header facts that decide everything downstream — point count, point
format, CRS and its LINEAR UNITS, extra dimensions — plus a streamed class
histogram, then names the entry point:

    all class 0/1        -> unclassified, start at Stage 1 (PointCONV)
    has 2 + 5, no 3/4    -> classified, go to Stage 6v (vegetation split)
    has 3/4/5            -> already stratified

Streams the classification field in chunks, so it is safe on a 16 GB cloud
(bounded memory; it still has to read the file, which is I/O-bound).

Usage:
  python inspect_cloud.py <file.las|file.laz> [more files ...] [--header-only]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import laspy
import numpy as np


# SDAI/PTC catalog (SDAI_Classification_CLASSCODES_v4).
CLASS_NAMES = {
    0: "Never Classified", 1: "Unassigned", 2: "Ground",
    3: "Low Vegetation", 4: "Medium Vegetation", 5: "High Vegetation",
    6: "Building / manmade", 7: "Low point (noise)", 9: "Water",
    11: "Tanks (NOT road -- see SKILL.md)", 14: "Wire", 15: "Tower",
    18: "Pole", 19: "Pole - Street/traffic (pole-vec body)",
    40: "Road / Pavement", 47: "Building - Wall / Facade", 51: "Sidewalk",
}
STAGE1_CLASSES = {0, 2, 5, 6, 14, 15, 18}


def _laz_backend_for(path: Path):
    if not str(path).lower().endswith(".laz"):
        return None
    for cand in (
        getattr(laspy.LazBackend, "LazrsParallel", None),
        getattr(laspy.LazBackend, "Lazrs", None),
        getattr(laspy.LazBackend, "Laszip", None),
    ):
        if cand is not None and cand.is_available():
            return cand
    return None


def inspect(path: Path, header_only: bool = False,
            chunk_size: int = 20_000_000) -> int:
    print(f"\n=== {path} ===")
    if not path.exists():
        print("  MISSING")
        return 1
    size_gb = path.stat().st_size / 2**30
    with laspy.open(path, laz_backend=_laz_backend_for(path)) as r:
        h = r.header
        n = h.point_count
        print(f"  file size      : {size_gb:.2f} GiB")
        print(f"  points         : {n:,}")
        print(f"  LAS version    : {h.version}   point format: {h.point_format.id}"
              f"{'  (legacy: classification capped at 31)' if h.point_format.id <= 5 else '  (byte classification)'}")
        print(f"  scales         : {list(h.scales)}")
        print(f"  bbox           : X {h.mins[0]:.2f}..{h.maxs[0]:.2f}  "
              f"Y {h.mins[1]:.2f}..{h.maxs[1]:.2f}  "
              f"Z {h.mins[2]:.2f}..{h.maxs[2]:.2f}")
        print(f"  extent         : {h.maxs[0]-h.mins[0]:.1f} x "
              f"{h.maxs[1]-h.mins[1]:.1f} x {h.maxs[2]-h.mins[2]:.1f} units")
        area = (h.maxs[0]-h.mins[0]) * (h.maxs[1]-h.mins[1])
        if area > 0:
            print(f"  density        : ~{n/area:.1f} pts / sq unit")

        crs = None
        try:
            crs = h.parse_crs()
        except Exception as exc:                  # noqa: BLE001
            print(f"  CRS            : UNPARSABLE ({exc})")
        if crs is None:
            print("  CRS            : *** NONE *** -- Stage 6v will assume "
                  "METRES; pass --units ft if the data is imperial")
        else:
            ax = crs.axis_info[0]
            print(f"  CRS            : {crs.name}")
            print(f"  linear units   : {ax.unit_name} "
                  f"({ax.unit_conversion_factor} m/unit)"
                  f"{'  <-- FEET: Stage 6v converts thresholds automatically' if ax.unit_conversion_factor != 1.0 else ''}")
        ed = list(h.point_format.extra_dimension_names)
        print(f"  extra dims     : {ed if ed else '(none)'}")
        if "original_class" in ed:
            print("                   'original_class' present -> Stage 6 (or 6v) "
                  "has already run on this cloud")
        if "hag" in ed:
            print("                   'hag' present -> Stage 6v has already run")

        if header_only or n == 0:
            if n == 0:
                print("  EMPTY CLOUD")
            return 0

        print(f"  reading class histogram ({n:,} points, streamed)...")
        t0 = time.time()
        hist = np.zeros(256, dtype=np.int64)
        seen = 0
        for pts in r.chunk_iterator(chunk_size):
            c = np.asarray(pts.classification, dtype=np.uint8)
            hist += np.bincount(c, minlength=256)
            seen += len(c)
            if n > chunk_size:
                print(f"    {seen:,}/{n:,} ({100.0*seen/n:.0f}%)", flush=True)
        print(f"  ({time.time()-t0:.1f}s)")

    present = {int(i): int(v) for i, v in enumerate(hist) if v}
    print("  class histogram:")
    for k, v in sorted(present.items()):
        print(f"    {k:3d}  {CLASS_NAMES.get(k, '?'):<38} {v:>15,}  "
              f"({100.0*v/n:6.2f}%)")

    codes = set(present)
    veg = {3, 4, 5} & codes
    print("\n  -> ENTRY POINT:")
    if codes <= {0, 1}:
        print("     UNCLASSIFIED (all class 0/1). Start at STAGE 1 (PointCONV).")
        print("     Stage 6v has no veg and no ground to work with and will")
        print("     benign-skip (exit 3).")
    elif veg >= {3, 4} or (veg == {3, 4, 5}):
        print("     ALREADY STRATIFIED (classes 3/4 present). Re-run Stage 6v")
        print("     only to change thresholds, with --veg-classes 3,4,5.")
    elif 5 in codes and not ({3, 4} & codes):
        n_ground = present.get(2, 0) + present.get(40, 0)
        print("     CLASSIFIED, veg not split (class 5 only, no 3/4).")
        print("     -> GO TO STAGE 6v (stratify_vegetation.py). This is the")
        print("        normal entry point for the low/med/high veg split.")
        if n_ground < 100:
            print(f"     WARNING: only {n_ground:,} ground points (classes 2+40)"
                  " -- Stage 6v needs >=100 to build a DEM and will skip.")
    elif codes & {2, 6, 14, 15, 18}:
        print("     PARTIALLY CLASSIFIED but no vegetation class present.")
        print("     Stage 6v will benign-skip. Check the model / Stage 1 output.")
    else:
        print("     UNRECOGNISED class set -- inspect manually.")

    unexpected = codes - STAGE1_CLASSES - {3, 4, 7, 9, 19, 40, 47, 51, 1}
    if unexpected:
        print(f"  NOTE: non-catalog classes present: {sorted(unexpected)}")
    if 11 in codes:
        print("  WARNING: class 11 present. In the SDAI catalog 11 = 'Tanks',"
              " not road. Road is 40.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--header-only", action="store_true",
                   help="Skip the class histogram (no full read).")
    p.add_argument("--chunk-size", type=int, default=20_000_000)
    a = p.parse_args()
    rc = 0
    for f in a.files:
        rc |= inspect(f, a.header_only, a.chunk_size)
    return rc


if __name__ == "__main__":
    sys.exit(main())
