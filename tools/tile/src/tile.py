#!/usr/bin/env python3
"""Split large LAS/LAZ files into tiles by point count.

Tiling stage extracted from tile_thin_clip.py (tools_archived). Small LAZ
files are decompressed to LAS, small LAS files copied through, and anything
above --max-points-per-tile is streamed into numbered tiles.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import List

import laspy


def _list_las_files(input_dir: Path, recursive: bool = True) -> List[Path]:
    if recursive:
        files = list(input_dir.rglob("*"))
    else:
        files = list(input_dir.iterdir())
    return sorted(
        f for f in files
        if f.is_file()
        and f.suffix.lower() in (".las", ".laz")
        and not f.name.lower().endswith(".copc.laz")
    )


def tile_files(
    input_dir: Path,
    tile_dir: Path,
    max_points_per_tile: int = 13_000_000,
    chunk_size: int = 5_000_000,
    recursive: bool = True,
) -> int:
    """Split large LAS/LAZ files into tiles by point count."""
    tile_dir.mkdir(parents=True, exist_ok=True)

    las_files = _list_las_files(input_dir, recursive=recursive)
    if not las_files:
        raise RuntimeError(f"No LAS/LAZ files found in {input_dir}")

    logging.info(f"[TILE] Found {len(las_files)} file(s) in {input_dir}")

    total_tiles = 0
    for f in las_files:
        with laspy.open(f) as reader:
            pt_count = reader.header.point_count

        # Estimate uncompressed size
        rec_len = 38  # typical for format 8
        try:
            with laspy.open(f) as r:
                rec_len = r.header.point_format.size
        except Exception:
            pass
        uncompressed_est = pt_count * rec_len

        if uncompressed_est < max_points_per_tile * rec_len:
            # Small enough — just decompress if LAZ, or copy if LAS
            dst = tile_dir / f.with_suffix(".las").name
            if not dst.exists():
                if f.suffix.lower() == ".laz":
                    logging.info(f"[TILE] Decompressing: {f.name} -> {dst.name}")
                    with laspy.open(f) as reader:
                        las = reader.read()
                    with laspy.open(dst, mode="w", header=las.header) as writer:
                        writer.write_points(las.points)
                else:
                    logging.info(f"[TILE] Copying: {f.name}")
                    shutil.copy2(f, dst)
            total_tiles += 1
        else:
            logging.info(f"[TILE] Splitting: {f.name} ({pt_count:,} points)")
            stem = f.stem
            tile_num = 0
            tile_points = 0
            total_points = 0
            writer = None

            with laspy.open(f) as reader:
                header = reader.header

                def _open_writer(idx: int) -> laspy.LasWriter:
                    tp = tile_dir / f"{stem}_{idx:04d}.las"
                    new_header = laspy.LasHeader(
                        point_format=header.point_format,
                        version=header.version,
                    )
                    new_header.offsets = header.offsets
                    new_header.scales = header.scales
                    for vlr in header.vlrs:
                        # laspy regenerates the extra-bytes VLR from the point
                        # format; copying the original too doubles the extra-dim
                        # declaration and PDAL then rejects the file
                        if isinstance(vlr, laspy.vlrs.known.ExtraBytesVlr):
                            continue
                        new_header.vlrs.append(vlr)
                    return laspy.open(tp, mode="w", laz_backend=None, header=new_header)

                try:
                    writer = _open_writer(tile_num)
                    # chunks larger than the cap would overshoot it by up to a
                    # whole chunk (and files below chunk_size never split)
                    for chunk in reader.chunk_iterator(
                            min(chunk_size, max_points_per_tile)):
                        n = len(chunk)
                        total_points += n
                        if tile_points + n > max_points_per_tile and tile_points > 0:
                            writer.close()
                            tile_num += 1
                            tile_points = 0
                            writer = _open_writer(tile_num)
                        writer.write_points(chunk)
                        tile_points += n
                finally:
                    if writer is not None:
                        writer.close()

            n_tiles = tile_num + 1
            logging.info(f"[TILE] {total_points:,} points -> {n_tiles} tiles")
            total_tiles += n_tiles

    logging.info(f"[TILE] Complete: {total_tiles} tile(s) in {tile_dir}")
    return total_tiles


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Split large LAS/LAZ files into tiles by point count",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-dir", required=True,
                        help="Directory with source LAS/LAZ files (recursive)")
    parser.add_argument("--output-dir", required=True,
                        help="Directory the tiles are written into (flat)")
    parser.add_argument("--max-points-per-tile", type=int, default=13_000_000,
                        help="Max points per tile (~0.5GB at 38 bytes/pt)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout, force=True)
    tile_files(
        input_dir=Path(args.input_dir),
        tile_dir=Path(args.output_dir),
        max_points_per_tile=args.max_points_per_tile,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
