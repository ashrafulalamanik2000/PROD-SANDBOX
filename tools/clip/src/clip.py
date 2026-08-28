#!/usr/bin/env python3
"""Clip LAS/LAZ files to an AOI polygon shapefile and merge.

Clipping stage extracted from tile_thin_clip.py (tools_archived). Takes the
union of the AOI's polygon features AS-IS (no buffering — buffer line layers
into a polygon AOI upstream), pre-filters clouds by header bbox, PDAL-crops
the survivors in parallel batches, and merges the results into one cloud.
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import shutil
import sys
import tempfile
import time
from pathlib import Path

import geopandas as gpd
import laspy
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union


def _bboxes_overlap(a: tuple, b: tuple) -> bool:
    """Check if two (minx, miny, maxx, maxy) bboxes overlap."""
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def build_clip_polygon(aoi_shp: Path) -> tuple:
    """Load the AOI shapefile, union its polygons, return (wkt, bbox)."""
    aoi_shp = Path(aoi_shp)
    gdf = gpd.read_file(aoi_shp)
    logging.info("[CLIP] Loaded %d AOI feature(s) from %s (CRS: %s)",
                 len(gdf), aoi_shp.name, gdf.crs)

    merged = unary_union(gdf.geometry)
    if isinstance(merged, Polygon):
        merged = MultiPolygon([merged])
    if not isinstance(merged, MultiPolygon) or merged.is_empty:
        raise RuntimeError(
            f"AOI {aoi_shp.name} does not union into polygons "
            f"(got {merged.geom_type}) — the AOI must be a polygon layer; "
            f"buffer line layers into polygons upstream")

    bbox = merged.bounds  # (minx, miny, maxx, maxy)
    logging.info("[CLIP] Clip polygon bbox: (%.1f, %.1f) -> (%.1f, %.1f)", *bbox)

    crop_wkt = merged.wkt
    logging.info("[CLIP] WKT length: %d chars", len(crop_wkt))

    return crop_wkt, bbox


def _prefilter_by_bbox(las_files: list, clip_bbox: tuple) -> tuple:
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
            logging.warning("[CLIP] Could not read header of %s: %s — skipping",
                            f.name, exc)
            skipped += 1
    return overlapping, skipped


def _init_worker_logging():
    # spawned pool workers start with unconfigured logging, which silently
    # swallows the per-batch INFO progress lines on Windows
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout, force=True)


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
            stages.append({"type": "readers.las", "filename": f, "tag": tag_r,
                           "nosrs": True})
            stages.append({
                "type": "filters.crop",
                "polygon": crop_wkt,
                "inputs": [tag_r],
                "tag": tag_c,
            })
            crop_tags.append(tag_c)

        if len(crop_tags) > 1:
            stages.append({"type": "filters.merge", "inputs": crop_tags,
                           "tag": "merged"})
            stages.append({"type": "writers.las", "filename": tmp_output,
                           "inputs": ["merged"], "forward": "all"})
        else:
            stages.append({"type": "writers.las", "filename": tmp_output,
                           "inputs": crop_tags, "forward": "all"})

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


def clip_to_aoi(
    las_folder: Path,
    aoi_shp: Path,
    output_path: Path,
    batch_size: int = 20,
    workers: int = 8,
) -> Path:
    """Clip all LAS/LAZ files to the AOI polygon union and merge into single output."""
    las_folder = Path(las_folder).resolve()
    aoi_shp = Path(aoi_shp).resolve()
    output_path = Path(output_path).resolve()

    las_files = sorted(las_folder.glob("*.las")) + sorted(las_folder.glob("*.laz"))
    if not las_files:
        raise RuntimeError(f"No .las or .laz files found in: {las_folder}")

    logging.info("[CLIP] LAS folder:       %s", las_folder)
    logging.info("[CLIP] Input files:      %d", len(las_files))
    logging.info("[CLIP] AOI:              %s", aoi_shp)
    logging.info("[CLIP] Output:           %s", output_path)
    logging.info("[CLIP] Batch size:       %d", batch_size)
    logging.info("[CLIP] Workers:          %d", workers)

    # Step 1: Build clip polygon -> WKT
    crop_wkt, clip_bbox = build_clip_polygon(aoi_shp)

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
    batches = [overlapping[i:i + batch_size]
               for i in range(0, len(overlapping), batch_size)]
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
        with multiprocessing.Pool(processes=workers,
                                  initializer=_init_worker_logging) as pool:
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
                writer = laspy.LasWriter(fh, laz_backend=None, header=header,
                                         closefd=False)
                writer.close()
            return output_path

        # Step 4: Merge batch outputs into final file
        if len(successful) == 1:
            logging.info("[CLIP] --- Single batch, moving to output ---")
            shutil.move(successful[0][0], str(output_path))
        else:
            logging.info("[CLIP] --- Merging %d batch outputs -> %s ---",
                         len(successful), output_path.name)
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clip LAS/LAZ files to an AOI polygon shapefile and merge",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-dir", required=True,
                        help="Directory with LAS/LAZ files to clip (top level)")
    parser.add_argument("--aoi-shp", required=True,
                        help="AOI polygon shapefile; clip to the union of its "
                             "features (used as-is, no buffering)")
    parser.add_argument("--output", required=True,
                        help="Output clipped+merged cloud path (.las or .laz)")
    parser.add_argument("--batch-size", type=int, default=20,
                        help="Files per PDAL batch")
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel worker processes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout, force=True)
    clip_to_aoi(
        las_folder=Path(args.input_dir),
        aoi_shp=Path(args.aoi_shp),
        output_path=Path(args.output),
        batch_size=args.batch_size,
        workers=args.workers,
    )
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
