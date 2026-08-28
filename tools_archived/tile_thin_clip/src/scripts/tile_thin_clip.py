#!/usr/bin/env python3
"""
Three-stage pipeline:
  1. Tile   – Split large LAS/LAZ files into smaller tiles by point count
              (copied from pipeline.py tile_files)
  2. Thin   – Voxel downsample each tile, write compressed LAZ
              (copied from test_thin.py)
  3. Clip   – Clip to buffered NETWORK_LINES.shp polygon and merge
              (copied from clip_las skill: clip_las.py)

Usage:
    python tile_thin_clip.py \
        --input-dir /data/LAZ \
        --output-dir /data/pipeline_out \
        --network-shp /data/NETWORK_LINES.shp \
        --buffer 3.0

Outputs:
    <output-dir>/tiles/          Tiled LAS files
    <output-dir>/thinned/        Thinned LAZ files
    <output-dir>/clipped.las     Clipped & merged LAS file
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import List

import geopandas as gpd
import laspy
import numpy as np
from shapely.geometry import MultiPolygon
from shapely.ops import unary_union


# ─── Logging ─────────────────────────────────────────────────────────
def setup_logging(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "log"
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_dir / f"{ts}_tile_thin_clip.log"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


# ═══════════════════════════════════════════════════════════════════════
# STAGE 1: TILE  (copied from pipeline.py)
# ═══════════════════════════════════════════════════════════════════════

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
        fsize = f.stat().st_size
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
                        new_header.vlrs.append(vlr)
                    return laspy.open(tp, mode="w", laz_backend=None, header=new_header)

                try:
                    writer = _open_writer(tile_num)
                    for chunk in reader.chunk_iterator(chunk_size):
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


# ═══════════════════════════════════════════════════════════════════════
# STAGE 2: THIN  (copied from test_thin.py)
# ═══════════════════════════════════════════════════════════════════════

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

        # Create unique voxel keys
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
    with multiprocessing.Pool(num_workers) as pool:
        pool.map(_thin_one_file, work)

    thinned_count = len(list(output_dir.glob("*.laz")))
    logging.info(f"[THIN] Complete: {thinned_count} thinned file(s) in {output_dir}")
    return thinned_count


# ═══════════════════════════════════════════════════════════════════════
# STAGE 3: CLIP  (copied from clip_las.py skill)
# ═══════════════════════════════════════════════════════════════════════

# US Survey Feet to metres
US_FT_TO_M = 0.3048006096012192


def _detect_crs_units(crs) -> str:
    """Return 'metre', 'us-ft', or 'unknown' for a pyproj CRS."""
    if crs is None:
        return "unknown"
    try:
        from pyproj import CRS as ProjCRS
        c = ProjCRS(crs)
        unit = c.axis_info[0].unit_name.lower()
        if "foot" in unit or "feet" in unit or "ft" in unit:
            return "us-ft"
        if "metre" in unit or "meter" in unit:
            return "metre"
    except Exception:
        pass
    return "unknown"


def _buffer_metres_to_crs_units(buffer_m: float, crs_unit: str) -> float:
    """Convert a buffer in metres to CRS linear units."""
    if crs_unit == "us-ft":
        return buffer_m / US_FT_TO_M
    return buffer_m


def _bboxes_overlap(
    a: tuple,
    b: tuple,
) -> bool:
    """Check if two (minx, miny, maxx, maxy) bboxes overlap."""
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def build_clip_polygon(
    network_shp: Path,
    buffer_m: float = 3.0,
) -> tuple:
    """Load NETWORK_LINES.shp, buffer, and return (wkt, bbox, crs_unit)."""
    network_shp = Path(network_shp)
    gdf = gpd.read_file(network_shp)
    logging.info("[CLIP] Loaded %d network lines from %s (CRS: %s)", len(gdf), network_shp.name, gdf.crs)

    crs_unit = _detect_crs_units(gdf.crs)
    buffer_crs = _buffer_metres_to_crs_units(buffer_m, crs_unit)
    logging.info(
        "[CLIP] Buffer: %.1f m = %.4f %s",
        buffer_m,
        buffer_crs,
        crs_unit if crs_unit != "unknown" else "CRS units",
    )

    buffered = gdf.geometry.buffer(buffer_crs)
    merged = unary_union(buffered)
    if not isinstance(merged, MultiPolygon):
        merged = MultiPolygon([merged])

    bbox = merged.bounds  # (minx, miny, maxx, maxy)
    logging.info("[CLIP] Clip polygon bbox: (%.1f, %.1f) -> (%.1f, %.1f)", *bbox)

    crop_wkt = merged.wkt
    logging.info("[CLIP] WKT length: %d chars", len(crop_wkt))

    return crop_wkt, bbox, crs_unit


def _prefilter_by_bbox(
    las_files: list,
    clip_bbox: tuple,
) -> tuple:
    """Check each file's header bbox against clip bbox. Returns (overlapping, skipped_count)."""
    overlapping = []
    skipped = 0
    for f in las_files:
        try:
            with laspy.open(str(f)) as reader:
                h = reader.header
                file_bbox = (h.x_min, h.y_min, h.x_max, h.y_max)
            if _bboxes_overlap(file_bbox, clip_bbox):
                overlapping.append(f)
            else:
                skipped += 1
        except Exception as exc:
            logging.warning("[CLIP] Could not read header of %s: %s — skipping", f.name, exc)
            skipped += 1
    return overlapping, skipped


def _pdal_crop_batch(args: tuple) -> tuple:
    """Worker: run a PDAL crop-then-merge pipeline for a batch of LAS files."""
    import pdal as pdal_lib

    batch_files, crop_wkt, tmp_output, batch_idx = args
    t0 = time.time()

    try:
        stages = []
        crop_tags = []

        for i, f in enumerate(batch_files):
            tag_r = f"r{i}"
            tag_c = f"c{i}"
            stages.append({"type": "readers.las", "filename": f, "tag": tag_r, "nosrs": True})
            stages.append({
                "type": "filters.crop",
                "polygon": crop_wkt,
                "inputs": [tag_r],
                "tag": tag_c,
            })
            crop_tags.append(tag_c)

        if len(crop_tags) > 1:
            stages.append({"type": "filters.merge", "inputs": crop_tags, "tag": "merged"})
            stages.append({"type": "writers.las", "filename": tmp_output, "inputs": ["merged"], "forward": "all"})
        else:
            stages.append({"type": "writers.las", "filename": tmp_output, "inputs": crop_tags, "forward": "all"})

        pipeline_json = json.dumps({"pipeline": stages})
        p = pdal_lib.Pipeline(pipeline_json)
        count = p.execute()

        elapsed = time.time() - t0
        logging.info(
            "[CLIP]   [batch %d] %d files -> %s pts in %.0fs",
            batch_idx, len(batch_files), f"{count:,}", elapsed,
        )
        return (tmp_output, count)

    except Exception as exc:
        logging.error("[CLIP]   [batch %d] FAILED: %s", batch_idx, exc)
        return ("", 0)


def clip_to_network(
    las_folder: Path,
    network_shp: Path,
    output_path: Path,
    buffer_m: float = 3.0,
    batch_size: int = 20,
    workers: int = 8,
) -> Path:
    """Clip all LAS/LAZ files to buffered NETWORK_LINES.shp and merge into single output."""
    las_folder = Path(las_folder).resolve()
    network_shp = Path(network_shp).resolve()
    output_path = Path(output_path).resolve()

    # Collect input files
    las_files = sorted(las_folder.glob("*.las")) + sorted(las_folder.glob("*.laz"))
    if not las_files:
        raise RuntimeError(f"No .las or .laz files found in: {las_folder}")

    logging.info("[CLIP] LAS folder:       %s", las_folder)
    logging.info("[CLIP] Input files:      %d", len(las_files))
    logging.info("[CLIP] NETWORK_LINES:    %s", network_shp)
    logging.info("[CLIP] Output:           %s", output_path)
    logging.info("[CLIP] Buffer:           %.1f m", buffer_m)
    logging.info("[CLIP] Batch size:       %d", batch_size)
    logging.info("[CLIP] Workers:          %d", workers)

    # Step 1: Build clip polygon -> WKT
    crop_wkt, clip_bbox, crs_unit = build_clip_polygon(network_shp, buffer_m)

    # Step 2: Pre-filter files by header bbox
    logging.info("[CLIP] --- Pre-filtering %d files by header bbox ---", len(las_files))
    t_filter_start = time.time()
    overlapping, skipped = _prefilter_by_bbox(las_files, clip_bbox)
    t_filter_elapsed = time.time() - t_filter_start
    logging.info(
        "[CLIP] Pre-filter done in %.0fs: %d files overlap, %d skipped",
        t_filter_elapsed, len(overlapping), skipped,
    )

    if not overlapping:
        logging.warning("[CLIP] No files overlap the clip polygon — output will be empty")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with laspy.open(str(las_files[0])) as reader:
            header = reader.header
        with open(str(output_path), "wb") as fh:
            writer = laspy.LasWriter(fh, laz_backend=None, header=header, closefd=False)
            writer.close()
        return output_path

    # Step 3: Batch files and run PDAL crop in parallel
    batches = [overlapping[i:i + batch_size] for i in range(0, len(overlapping), batch_size)]
    logging.info("[CLIP] --- Clipping %d files in %d batches with %d workers ---",
                 len(overlapping), len(batches), workers)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        worker_args = [
            (
                [str(f) for f in batch],
                crop_wkt,
                str(Path(tmp_dir) / f"batch_{i}.las"),
                i,
            )
            for i, batch in enumerate(batches)
        ]

        t_clip_start = time.time()
        with multiprocessing.Pool(processes=workers) as pool:
            results = pool.map(_pdal_crop_batch, worker_args)
        t_clip_elapsed = time.time() - t_clip_start
        logging.info("[CLIP] --- Clipping done in %.0fs ---", t_clip_elapsed)

        # Filter out failed/empty batches
        successful = [(p, n) for p, n in results if p and n > 0]
        failed_count = len(results) - len([r for r in results if r[0]])
        empty_count = len([r for r in results if r[0] and r[1] == 0])

        if failed_count:
            logging.warning("[CLIP] %d batch(es) failed during clipping", failed_count)
        if empty_count:
            logging.info("[CLIP] %d batch(es) produced 0 points", empty_count)

        total_clipped = sum(n for _, n in successful)
        logging.info("[CLIP] Total points clipped: %s across %d batches",
                     f"{total_clipped:,}", len(successful))

        if not successful:
            logging.warning("[CLIP] No points survived clipping — writing empty output")
            with laspy.open(str(las_files[0])) as reader:
                header = reader.header
            with open(str(output_path), "wb") as fh:
                writer = laspy.LasWriter(fh, laz_backend=None, header=header, closefd=False)
                writer.close()
            return output_path

        # Step 4: Merge batch outputs into final file
        if len(successful) == 1:
            logging.info("[CLIP] --- Single batch, moving to output ---")
            shutil.move(successful[0][0], str(output_path))
        else:
            logging.info("[CLIP] --- Merging %d batch outputs -> %s ---", len(successful), output_path.name)
            t_merge_start = time.time()
            total_written = 0

            with laspy.open(successful[0][0]) as first_reader:
                merge_header = first_reader.header

            laz_backend = (
                laspy.LazBackend.LazrsParallel
                if output_path.suffix.lower() == ".laz"
                else None
            )
            out_fh = open(str(output_path), "wb")
            writer = laspy.LasWriter(
                out_fh, laz_backend=laz_backend, header=merge_header, closefd=True,
            )

            try:
                for batch_path, n_pts in successful:
                    with laspy.open(batch_path) as tmp_reader:
                        for chunk in tmp_reader.chunk_iterator(5_000_000):
                            writer.write_points(chunk)
                            total_written += len(chunk)
            finally:
                writer.close()

            t_merge_elapsed = time.time() - t_merge_start
            logging.info("[CLIP] --- Merge done in %.0fs (%s points) ---",
                         t_merge_elapsed, f"{total_written:,}")

    logging.info("[CLIP] Output: %s", output_path)
    return output_path


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Tile -> Thin -> Clip pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── Paths ──────────────────────────────────────────────────────
    parser.add_argument("--input-dir", required=True,
                        help="Directory with source LAS/LAZ files")
    parser.add_argument("--output-dir", required=True,
                        help="Pipeline output root directory")
    parser.add_argument("--network-shp", default=None,
                        help="NETWORK_LINES.shp for clip step")

    # ── Tiling ─────────────────────────────────────────────────────
    parser.add_argument("--max-points-per-tile", type=int, default=13_000_000,
                        help="Max points per tile (~0.5GB at 38 bytes/pt)")

    # ── Thinning ───────────────────────────────────────────────────
    parser.add_argument("--voxel-size", type=float, default=0.05,
                        help="Voxel size for thinning (map units)")
    parser.add_argument("--thin-workers", type=int, default=20,
                        help="Parallel workers for thinning")

    # ── Clipping ───────────────────────────────────────────────────
    parser.add_argument("--buffer", type=float, default=3.0,
                        help="Buffer distance in metres around network lines")
    parser.add_argument("--batch-size", type=int, default=20,
                        help="Files per PDAL batch for clipping")
    parser.add_argument("--clip-workers", type=int, default=8,
                        help="Parallel workers for clipping")
    parser.add_argument("--clip-output", default=None,
                        help="Output clipped LAS path (default: <output-dir>/clipped.las)")

    # ── Steps ──────────────────────────────────────────────────────
    parser.add_argument(
        "--steps", default="tile,thin,clip",
        help="Comma-separated stages to run: tile,thin,clip (default: all)",
    )

    args = parser.parse_args()

    steps = {s.strip().lower() for s in args.steps.split(",")}
    output_dir = Path(args.output_dir)
    input_dir = Path(args.input_dir)
    tile_dir = output_dir / "tiles"
    thin_dir = output_dir / "thinned"
    clip_output = Path(args.clip_output) if args.clip_output else output_dir / "clipped.las"

    setup_logging(output_dir)
    logging.info("=" * 60)
    logging.info("TILE -> THIN -> CLIP PIPELINE")
    logging.info("=" * 60)
    logging.info(f"Steps to run: {', '.join(sorted(steps))}")

    timings = {}

    # ── Stage 1: Tile ──────────────────────────────────────────────
    if "tile" in steps:
        logging.info("")
        logging.info("=" * 40)
        logging.info("STAGE 1: TILING")
        logging.info("=" * 40)
        t0 = time.time()
        existing_tiles = _list_las_files(tile_dir) if tile_dir.exists() else []
        if existing_tiles:
            tile_count = len(existing_tiles)
            logging.info(f"[TILE] Reusing {tile_count} existing tile(s) in {tile_dir}")
        else:
            tile_count = tile_files(
                input_dir=input_dir,
                tile_dir=tile_dir,
                max_points_per_tile=args.max_points_per_tile,
                recursive=True,
            )
        timings["tile"] = time.time() - t0
    else:
        logging.info("[TILE] Skipped")

    # ── Stage 2: Thin ─────────────────────────────────────────────
    if "thin" in steps:
        logging.info("")
        logging.info("=" * 40)
        logging.info("STAGE 2: THINNING")
        logging.info("=" * 40)
        t0 = time.time()

        # Thin from tiles dir (output of stage 1)
        thin_input = tile_dir
        if not tile_dir.exists() or not _list_las_files(tile_dir):
            # If no tiles, thin directly from input
            thin_input = input_dir
            logging.info(f"[THIN] No tiles found, thinning from input: {input_dir}")

        thin_files(
            input_dir=thin_input,
            output_dir=thin_dir,
            voxel_size=args.voxel_size,
            num_workers=args.thin_workers,
        )
        timings["thin"] = time.time() - t0
    else:
        logging.info("[THIN] Skipped")

    # ── Stage 3: Clip ─────────────────────────────────────────────
    if "clip" in steps:
        if not args.network_shp:
            logging.error("[CLIP] --network-shp is required for clip step")
            sys.exit(1)

        logging.info("")
        logging.info("=" * 40)
        logging.info("STAGE 3: CLIPPING (network line buffer)")
        logging.info("=" * 40)
        t0 = time.time()

        # Clip from thinned dir (output of stage 2)
        clip_input = thin_dir
        if not thin_dir.exists() or not (list(thin_dir.glob("*.las")) + list(thin_dir.glob("*.laz"))):
            # If no thinned files, clip from tiles
            clip_input = tile_dir
            if not tile_dir.exists() or not _list_las_files(tile_dir):
                # If no tiles either, clip from original input
                clip_input = input_dir
            logging.info(f"[CLIP] No thinned files found, clipping from: {clip_input}")

        clip_to_network(
            las_folder=clip_input,
            network_shp=Path(args.network_shp),
            output_path=clip_output,
            buffer_m=args.buffer,
            batch_size=args.batch_size,
            workers=args.clip_workers,
        )
        timings["clip"] = time.time() - t0
    else:
        logging.info("[CLIP] Skipped")

    # ── Summary ────────────────────────────────────────────────────
    total = sum(timings.values())
    logging.info("")
    logging.info("=" * 60)
    def _fmt(s): return f"{s / 60:.1f} min" if s >= 60 else f"{s:.1f} sec"
    for stage, secs in timings.items():
        logging.info(f"  {stage.upper():<10} {_fmt(secs)}")
    logging.info(f"  {'TOTAL':<10} {_fmt(total)}")
    logging.info("")
    logging.info("PIPELINE COMPLETE")
    logging.info(f"  Tiles:   {tile_dir}")
    logging.info(f"  Thinned: {thin_dir}")
    logging.info(f"  Clipped: {clip_output}")
    logging.info("=" * 60)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
