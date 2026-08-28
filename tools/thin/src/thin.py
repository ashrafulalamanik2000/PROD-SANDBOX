#!/usr/bin/env python3
"""Voxel-downsample every LAS/LAZ file in a directory to compressed LAZ.

Thinning stage extracted from tile_thin_clip.py (tools_archived). Each point
is assigned to a voxel of --voxel-size; the first point per voxel survives.
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
from pathlib import Path

import laspy
import numpy as np


def _init_worker_logging():
    # spawned pool workers start with unconfigured logging, which silently
    # swallows the per-file INFO progress lines on Windows
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout, force=True)


def _thin_one_file(args):
    """Voxel-downsample a single LAS/LAZ file. Worker for multiprocessing."""
    idx, filepath, output_dir, voxel_size, total = args
    outpath = Path(output_dir) / (filepath.stem + ".laz")
    try:
        las = laspy.read(str(filepath))

        # Voxel downsize: assign each point to a voxel, keep first per voxel
        voxel_x = (las.x // voxel_size).astype(np.int64)
        voxel_y = (las.y // voxel_size).astype(np.int64)
        voxel_z = (las.z // voxel_size).astype(np.int64)

        _, unique_idx = np.unique(
            np.column_stack((voxel_x, voxel_y, voxel_z)),
            axis=0,
            return_index=True
        )

        thinned = las[np.sort(unique_idx)]
        thinned.write(str(outpath))

        pct = (1 - len(unique_idx) / len(las.points)) * 100
        logging.info(f"[THIN] [{idx+1}/{total}] {filepath.name} ({pct:.0f}% reduced)")

    except Exception as e:
        logging.error(f"[THIN] [{idx+1}/{total}] ERROR: {filepath.name} - {e}")


def thin_files(
    input_dir: Path,
    output_dir: Path,
    voxel_size: float = 0.05,
    num_workers: int = 20,
) -> int:
    """Voxel-downsample all LAS/LAZ files in input_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)

    files = list(Path(input_dir).glob("*.las")) + list(Path(input_dir).glob("*.laz"))
    total = len(files)
    if total == 0:
        raise RuntimeError(f"No LAS/LAZ files found in {input_dir}")

    logging.info(f"[THIN] {total} files, voxel size: {voxel_size}, workers: {num_workers}")

    work = [(idx, f, str(output_dir), voxel_size, total) for idx, f in enumerate(files)]
    with multiprocessing.Pool(num_workers,
                              initializer=_init_worker_logging) as pool:
        pool.map(_thin_one_file, work)

    thinned_count = len(list(output_dir.glob("*.laz")))
    logging.info(f"[THIN] Complete: {thinned_count} thinned file(s) in {output_dir}")
    return thinned_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Voxel-downsample LAS/LAZ files to compressed LAZ",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-dir", required=True,
                        help="Directory with LAS/LAZ files to thin (top level)")
    parser.add_argument("--output-dir", required=True,
                        help="Directory the thinned .laz files are written into")
    parser.add_argument("--voxel-size", type=float, default=0.05,
                        help="Voxel size for thinning (map units)")
    parser.add_argument("--workers", type=int, default=20,
                        help="Parallel worker processes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout, force=True)
    n = thin_files(
        input_dir=Path(args.input_dir),
        output_dir=Path(args.output_dir),
        voxel_size=args.voxel_size,
        num_workers=args.workers,
    )
    return 0 if n else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
