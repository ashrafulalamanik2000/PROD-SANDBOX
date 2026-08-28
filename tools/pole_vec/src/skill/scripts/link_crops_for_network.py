#!/usr/bin/env python
"""Hardlink per-pole crops to the naming estimate_pole_network.py requires.

estimate_pole_network.py (Stage 3.5) hard-codes the chain naming
``<pole_id>_tf1_pointconv_combined_0p1m.la[sz]`` in two places and silently
skips any pole whose file is missing.  Stage-2 crops are named ``<pole_id>.las``.
This links each crop under the expected name (hardlink when possible — same
volume, zero bytes — else copy).
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

SUFFIX = "_tf1_pointconv_combined_0p1m"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--crops-dir", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--include-thinned", action="store_true",
                    help="also link *_thinned crops (default: skipped, the "
                         "full-res crop wins)")
    args = ap.parse_args()

    if not args.crops_dir.is_dir():
        sys.exit(f"crops dir not found: {args.crops_dir}")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    crops = sorted(list(args.crops_dir.glob("*.las")) +
                   list(args.crops_dir.glob("*.laz")))
    linked = skipped = 0
    for crop in crops:
        stem = crop.stem
        if stem.endswith("_thinned"):
            if not args.include_thinned:
                skipped += 1
                continue
            stem = stem[: -len("_thinned")]
        dst = args.out_dir / f"{stem}{SUFFIX}{crop.suffix}"
        if dst.exists():
            continue
        try:
            os.link(crop, dst)
        except OSError:
            shutil.copy2(crop, dst)
        linked += 1

    print(f"linked {linked} crop(s) into {args.out_dir}"
          + (f" (skipped {skipped} _thinned)" if skipped else ""))
    if not linked:
        # a bare "nothing matched" hides whether the dir was empty, mid-write,
        # or full of already-linked crops -- show what was actually there
        entries = sorted(p.name for p in args.crops_dir.iterdir())
        already = len(list(args.out_dir.glob(f"*{SUFFIX}.la[sz]")))
        if already and already >= len(crops):
            print(f"all {already} crop link(s) already present in {args.out_dir}")
            return 0
        sys.exit(f"no crops linked - nothing matched *.las/*.laz in "
                 f"{args.crops_dir} ({len(entries)} entries"
                 + (f", first: {', '.join(entries[:8])}" if entries else ", EMPTY")
                 + f"; {already} pre-existing links in out-dir)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
