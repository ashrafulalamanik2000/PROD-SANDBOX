#!/usr/bin/env python3
"""
Example tool. This is the shape your existing stable scripts take:
plain argparse, plain prints, plus a few `emit` calls where you have a
number worth putting on the dashboard.

Nothing here knows about the API, run ids, or uploads.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sdtools.protocol import artifact, error, field, metric, progress  # noqa: E402

try:
    import laspy  # type: ignore
    HAVE_LASPY = True
except ImportError:
    HAVE_LASPY = False


def collect(inputs: list[str], recursive: bool) -> list[Path]:
    out: list[Path] = []
    pattern = "**/*" if recursive else "*"
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            out += [f for f in sorted(p.glob(pattern))
                    if f.suffix.lower() in (".las", ".laz")]
        elif p.exists():
            out.append(p)
        else:
            print(f"ERROR: input not found: {p}", file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", action="append", required=True)
    ap.add_argument("--recursive", action="store_true")
    args = ap.parse_args()

    files = collect(args.input, args.recursive)
    if not files:
        error("NoInput", "no LAS/LAZ files matched the given inputs")
        return 2

    print(f"Inspecting {len(files)} file(s)")
    if not HAVE_LASPY:
        print("WARNING: laspy not installed; reporting file sizes only", file=sys.stderr)

    total_points = 0
    total_bytes = 0
    crs_seen: set[str] = set()

    for i, f in enumerate(files, 1):
        size = f.stat().st_size
        total_bytes += size
        line = f"{f.name}  {size/1e6:.1f} MB"

        if HAVE_LASPY:
            try:
                with laspy.open(f) as fh:
                    hdr = fh.header
                    total_points += hdr.point_count
                    crs = fh.header.parse_crs()
                    if crs:
                        crs_seen.add(crs.to_string())
                    line += (f"  pts={hdr.point_count:,}"
                             f"  v{hdr.version}"
                             f"  x[{hdr.mins[0]:.1f},{hdr.maxs[0]:.1f}]")
            except Exception as exc:  # noqa: BLE001
                print(f"ERROR: cannot read {f.name}: {exc}", file=sys.stderr)

        print(line)
        progress(i / len(files), note=f"{i}/{len(files)}")
        artifact(str(f), kind=f.suffix.lower().lstrip("."), size_bytes=size)

    metric("file_count", len(files))
    metric("total_bytes", total_bytes, unit="bytes")
    if total_points:
        metric("total_points", total_points)
    if crs_seen:
        field("crs", sorted(crs_seen))
        if len(crs_seen) > 1:
            print(f"WARNING: mixed CRS across inputs: {sorted(crs_seen)}", file=sys.stderr)
    elif HAVE_LASPY:
        print("WARNING: no CRS found on any input", file=sys.stderr)

    print(f"Done. {len(files)} files, {total_bytes/1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
