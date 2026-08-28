"""Aggregate class histogram across a directory of LAS/LAZ files.

The coverage check for a finished classification run: it answers "which classes
did the chain actually produce, over the whole delivery" in one table, and calls
out the codes that are expected-but-absent with the reason.

Usage:
  python class_totals.py <dir-or-file> [more ...] [--pattern "*.la[sz]"]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import laspy
import numpy as np

CLASS_NAMES = {
    0: "Never Classified", 1: "Unassigned", 2: "Ground",
    3: "Low Vegetation", 4: "Medium Vegetation", 5: "High Vegetation",
    6: "Building / manmade", 7: "Low point (noise)", 9: "Water",
    11: "Tanks (NOT road)", 14: "Wire", 15: "Tower", 18: "Pole",
    19: "Pole body (pole-vec)", 40: "Road / Pavement",
    47: "Building wall / facade", 51: "Sidewalk",
}
# Why a class may legitimately be missing from a corridor/AOI delivery.
WHY_ABSENT = {
    0: "every point got a label -- this is GOOD (no densification gap)",
    3: "Stage 6v did not run (or no veg below 0.5 m)",
    4: "Stage 6v did not run (or no veg 0.5-2.0 m)",
    7: "run with --mark-noise to produce it",
    40: "needs a road polygon from Stage 5 (which needs a pretrained curb model)",
    47: "needs Stage 4w; empty is legitimate when no facades are sensed",
    19: "needs Stage 3 pole-vec FULL mode -- KNOWN-INERT for corridor runs",
    51: "no available model emits sidewalk (both models are 6-class)",
    9:  "nothing in this chain produces water",
}
EXPECTED = (0, 2, 3, 4, 5, 6, 7, 14, 15, 18, 19, 40, 47, 51)


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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("--pattern", default="*.la[sz]")
    p.add_argument("--chunk-size", type=int, default=20_000_000)
    a = p.parse_args()

    files: list[Path] = []
    for t in a.paths:
        if t.is_dir():
            files += sorted(t.glob(a.pattern))
        elif t.is_file():
            files.append(t)
    if not files:
        print(f"no LAS/LAZ found (pattern {a.pattern!r})")
        return 1

    total = np.zeros(256, dtype=np.int64)
    n_all = 0
    for f in files:
        try:
            with laspy.open(f, laz_backend=_laz_backend_for(f)) as r:
                for pts in r.chunk_iterator(a.chunk_size):
                    c = np.asarray(pts.classification, dtype=np.uint8)
                    total += np.bincount(c, minlength=256)
                    n_all += len(c)
        except Exception as exc:                  # noqa: BLE001
            print(f"  WARN {f.name}: {exc}")

    if n_all == 0:
        print("no points")
        return 1
    print(f"\n{len(files)} file(s), {n_all:,} points total\n")
    print(f"  {'code':>4}  {'label':<26} {'points':>16}  {'share':>7}")
    print(f"  {'-'*4}  {'-'*26} {'-'*16}  {'-'*7}")
    for k in range(256):
        if total[k]:
            print(f"  {k:>4}  {CLASS_NAMES.get(k, '?'):<26} "
                  f"{int(total[k]):>16,}  {100.0*total[k]/n_all:>6.2f}%")

    present = {k for k in range(256) if total[k]}
    missing = [k for k in EXPECTED if k not in present]
    if missing:
        print("\n  Expected-but-absent:")
        for k in missing:
            print(f"    {k:>3}  {CLASS_NAMES.get(k, '?'):<26} "
                  f"-- {WHY_ABSENT.get(k, 'not produced by this chain')}")
    print(f"\n  {len(present)} distinct classes present: {sorted(present)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
