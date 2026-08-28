#!/usr/bin/env python3
"""Aecon corridor deliverable processing (mock of the real stable script)."""
import argparse, json, sys
from pathlib import Path
import numpy as np

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True, help="Corridor tile directory")
    ap.add_argument("--out", type=Path, required=True, help="Output report path")
    ap.add_argument("--chainage-step", type=float, default=20.0, help="Station interval (m)")
    ap.add_argument("--crs", default="EPSG:2952", help="Target CRS")
    ap.add_argument("--strict", action="store_true", help="Fail on any tile warning")
    ap.add_argument("--tag", action="append", help="Report tags")
    args = ap.parse_args()

    tiles = sorted(args.input.glob("*.npy")) if args.input.exists() else []
    if not tiles:
        print(f"ERROR: no tiles in {args.input}", file=sys.stderr); return 2
    stats, warned = [], 0
    for i, t in enumerate(tiles, 1):
        z = np.load(t)
        lo, hi = float(z.min()), float(z.max())
        if hi - lo > 50:
            warned += 1
            print(f"WARNING: {t.name} elevation range {hi-lo:.1f} m looks wrong", file=sys.stderr)
        stats.append({"tile": t.name, "points": int(z.size), "z_min": lo, "z_max": hi})
        print(f"processed {t.name} ({z.size} pts)")
    if args.strict and warned:
        print(f"ERROR: strict mode, {warned} warnings", file=sys.stderr); return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"crs": args.crs, "chainage_step": args.chainage_step,
                                    "tiles": stats, "tags": args.tag or []}, indent=1))
    print(f"wrote {args.out} ({len(stats)} tiles, {warned} warnings)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
