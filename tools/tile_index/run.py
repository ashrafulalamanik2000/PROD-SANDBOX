#!/usr/bin/env python3
"""Demo tool: exercises progress, metrics, artifacts, warnings, and failure."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sdtools.protocol import artifact, error, field, metric, progress  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tile-size", type=int, default=500)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--fail-at", type=int, default=0)
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        error("BadInput", f"input directory does not exist: {src}")
        return 2

    files = [f for f in sorted(src.rglob("*")) if f.is_file()]
    field("tile_size", args.tile_size)
    field("workers", args.workers)
    print(f"Indexing {len(files)} file(s) from {src} at {args.tile_size} units, "
          f"{args.workers} workers")

    rng = random.Random(1234)
    entries = []
    skipped = 0

    for i, f in enumerate(files, 1):
        time.sleep(0.02)
        if args.fail_at and i > args.fail_at:
            error("TileReadFailure", f"unrecoverable read error on tile {f.name}")
            print(f"ERROR: failed reading {f.name}", file=sys.stderr)
            return 1
        if rng.random() < 0.15:
            skipped += 1
            print(f"WARNING: {f.name} has no spatial reference, skipping", file=sys.stderr)
            continue
        entries.append({"path": str(f), "size": f.stat().st_size})
        print(f"indexed {f.name}")
        progress(i / max(len(files), 1), note=f"tile {i}/{len(files)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"tile_size": args.tile_size, "entries": entries}, indent=2))

    metric("tiles_in", len(files))
    metric("tiles_indexed", len(entries))
    metric("tiles_skipped", skipped)
    artifact(str(out), kind="json", size_bytes=out.stat().st_size, rows=len(entries))
    print(f"Wrote {out} with {len(entries)} entries ({skipped} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
