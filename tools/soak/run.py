#!/usr/bin/env python3
"""Sleeps with progress reporting. Exists to exercise the dispatcher."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sdtools.protocol import metric, progress  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--fail", action="store_true")
    args = ap.parse_args()

    ticks = max(int(args.seconds * 4), 1)
    print(f"soaking for {args.seconds}s")
    for i in range(1, ticks + 1):
        time.sleep(args.seconds / ticks)
        progress(i / ticks, note=f"{i * args.seconds / ticks:.1f}s")
    metric("soaked_s", args.seconds)
    if args.fail:
        print("ERROR: asked to fail", file=sys.stderr)
        return 1
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
