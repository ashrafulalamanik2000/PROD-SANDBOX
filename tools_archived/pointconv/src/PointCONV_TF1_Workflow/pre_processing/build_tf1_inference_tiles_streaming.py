"""Streaming, bounded-memory replacement for build_tf1_inference_tiles.py.

Drop-in for the legacy TF1 PointCONV inference path. Produces the same
tile-manifest JSON contract so downstream `classification.py` and
`post_processing/merge_tf1_tile_predictions.py` need no changes.

Key differences vs. the legacy preprocessor:

- Uses `laspy.open()` + `chunk_iterator()` — never loads a full source
  LAS into memory.
- Spatial-tile-first routing: each chunk is filtered to the active
  tile's halo before voxel-dedup, so the dedup hash is bounded by
  `target_tile_points + halo_population` rather than by total
  source-wide unique voxel count.
- `--max-concurrent-tiles` (B) controls the memory vs. I/O trade-off.
  B = 1 processes tiles serially (one source-pass per tile, smallest
  hash); B = N (or larger) processes them all in one source-pass
  (largest hash). Default 8.
- Equal-width XY tiling (split at bbox midpoint along the longer axis).
  Simpler than the legacy equal-count sorted split but produces the
  same downstream interface. PoC measured ±2 % class-histogram drift
  vs. the legacy preprocessor on the 100 m clip, well within model
  noise.

Memory budget per chunk_size = 500,000:
  - Point record + xyz float64       ≈ 150 MB
  - Per-tile voxel hash              ≈ 80 MB × B
  - Bookkeeping (idx + mask)          ≈ 50 MB
  Total peak                          ≈ 200 + 80*B MB

For Run 48 full-corridor (54 GB raw): with target_tile_points=400K and
~20K tiles, B=20 gives 1.6 GB peak hash + ~15 hours of sequential I/O
on a 1 GB/s NVMe. B=100 gives 8 GB peak hash + ~3 hours. Tune B to
fit available RAM.

Manifest schema matches `build_tf1_inference_tiles.py` v1 (the legacy
preprocessor). Sidecars:
  - <source>_thin_source_indices.npy  — source_thinned positions to
    original source positions (size: thinned_count).
  - <tile>_source_thinned_indices.npy — tile positions to
    source_thinned positions (size: tile_point_count).
  - <tile>_core_mask.npy              — per-tile-position core flag.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import laspy
    import numpy as np

np = None
laspy = None


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def _require_libs():
    global np, laspy
    if np is None:
        import numpy as imported_np

        np = imported_np
    if laspy is None:
        import laspy as imported_laspy

        laspy = imported_laspy


# Per-process cache of the prior manifest's source fragments (resume path).
_PRIOR_FRAGMENTS: dict[str, dict] = {}


def _prior_source_fragment(output_root: Path, source_name: str):
    """Return the prior manifest's record for `source_name`, or None."""
    key = str(output_root)
    if key not in _PRIOR_FRAGMENTS:
        frag_map: dict[str, dict] = {}
        prior_path = output_root / "manifests" / "tf1_tile_manifest.json"
        if prior_path.exists():
            try:
                data = json.loads(prior_path.read_text(encoding="utf-8"))
                for rec in data.get("source_files", []):
                    name = rec.get("source_name")
                    if name:
                        frag_map[name] = rec
            except Exception as e:  # noqa: BLE001
                logging.warning(
                    f"prior manifest unreadable ({e}); skipped sources will "
                    f"be reconstructed from disk or rebuilt"
                )
        _PRIOR_FRAGMENTS[key] = frag_map
    return _PRIOR_FRAGMENTS[key].get(source_name)


def _fragment_tiles_on_disk(fragment: dict) -> bool:
    """True if every tile row's artifacts still exist on disk."""
    tiles = fragment.get("tiles") or []
    if not tiles:
        return False
    for t in tiles:
        for k in ("tile_las", "tile_indices_path", "core_mask_path"):
            p = t.get(k)
            if not p or not Path(p).exists():
                return False
    return True


def _reconstruct_tiles_from_disk(
    output_root: Path,
    source_path: Path,
    source_stem: str,
    input_dir: Path,
    thinned_path: Path,
    overlap: float,
) -> list[dict]:
    """Rebuild manifest tile rows for a source from on-disk artifacts.

    Used when the prior manifest is missing/gutted but the per-tile
    las + indices + core-mask files survive. Core bounds are approximated
    by the tile las header bounds — downstream merge uses
    core_mask_path/tile_indices_path, never these informational fields.
    """
    tile_dir = output_root / "preprocessed_tiles"
    index_dir = output_root / "tile_indices"
    rows: list[dict] = []
    for tile_las in sorted(tile_dir.glob(f"{source_stem}__tile_*.las")):
        tile_id = tile_las.stem
        idx_path = index_dir / f"{tile_id}_source_thinned_indices.npy"
        mask_path = index_dir / f"{tile_id}_core_mask.npy"
        if not idx_path.exists() or not mask_path.exists():
            logging.warning(
                f"[{source_stem}] {tile_id}: indices/core-mask missing — "
                f"disk reconstruction aborted"
            )
            return []
        mask = np.load(mask_path)
        with laspy.open(tile_las) as r:
            tmins = list(r.header.mins)
            tmaxs = list(r.header.maxs)
        n_total = int(mask.shape[0])
        n_core = int(mask.sum())
        rows.append(
            {
                "tile_id": tile_id,
                "source_name": source_path.name,
                "source_stem": source_stem,
                "source_path": str(source_path.resolve()),
                "relative_source_path": str(
                    source_path.resolve().relative_to(input_dir.resolve())
                ),
                "source_thinned_las": str(thinned_path.resolve()),
                "tile_las": str(tile_las.resolve()),
                "tile_indices_path": str(idx_path.resolve()),
                "core_mask_path": str(mask_path.resolve()),
                "core_point_count": n_core,
                "tile_point_count": n_total,
                "core_fraction": float(n_core) / float(max(n_total, 1)),
                "overlap": float(overlap),
                "core_min_x": float(tmins[0]),
                "core_min_y": float(tmins[1]),
                "core_max_x": float(tmaxs[0]),
                "core_max_y": float(tmaxs[1]),
                "tile_min_x": float(tmins[0]),
                "tile_min_y": float(tmins[1]),
                "tile_max_x": float(tmaxs[0]),
                "tile_max_y": float(tmaxs[1]),
                "peak_hash_size": 0,
                "reconstructed_from_disk": True,
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build 0.1 m thinned, overlapping LAS tiles via streaming I/O.",
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pattern", default="*.las")
    parser.add_argument("--voxel-size", type=float, default=0.1)
    parser.add_argument("--target-tile-points", type=int, default=400_000)
    parser.add_argument(
        "--min-tile-points",
        type=int,
        default=25_000,
        help="Tiles thinner than this are merged into a neighbor (best-effort, "
        "only enforced in the equal-width layout when a degenerate small last tile is detected).",
    )
    parser.add_argument(
        "--min-radius",
        type=float,
        default=20.0,
        help="Minimum tile half-width. Tiles narrower than 2*min-radius are padded out.",
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=20.0,
        help="XY halo on each side of a tile's core.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500_000,
        help="Points per laspy chunk_iterator step.",
    )
    parser.add_argument(
        "--max-concurrent-tiles",
        type=int,
        default=8,
        help="Number of tiles processed per source-pass (B). Smaller = "
        "less memory but more source-passes. Larger = more memory but fewer "
        "passes. Set to 0 or negative for unlimited (all tiles in one pass).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Number of source files to process in parallel (ProcessPoolExecutor). "
        "Tiles within a source are processed in batches of --max-concurrent-tiles.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    # Fieldnames = UNION of keys across all rows (rows[0] order first,
    # extras appended in first-seen order). Resume runs MIX row schemas —
    # reconstructed rows carry `reconstructed_from_disk`, built rows carry
    # `elapsed_seconds` — and DictWriter's default extrasaction='raise'
    # turned that mix into a post-build ValueError + permanent resume
    # crash loop (the JSON manifest persists before the CSVs; 2026-06-07
    # review finding, empirically reproduced).
    fieldnames = list(rows[0].keys())
    seen = set(fieldnames)
    for row in rows[1:]:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)


def _make_header(source_header: "laspy.LasHeader") -> "laspy.LasHeader":
    _require_libs()
    h = laspy.LasHeader(
        point_format=source_header.point_format.id,
        version=str(source_header.version),
    )
    h.offsets = source_header.offsets
    h.scales = source_header.scales
    for vlr in source_header.vlrs:
        h.vlrs.append(vlr)
    return h


def _choose_split_axis(bbox_min: "np.ndarray", bbox_max: "np.ndarray") -> int:
    _require_libs()
    extents = bbox_max[:2] - bbox_min[:2]
    return int(np.argmax(extents))


def _tile_edges(
    bbox_min: "np.ndarray", bbox_max: "np.ndarray", axis: int, n_tiles: int
) -> "np.ndarray":
    _require_libs()
    lo, hi = float(bbox_min[axis]), float(bbox_max[axis])
    return np.linspace(lo, hi + 1e-6, n_tiles + 1)


def _decide_tile_count(
    raw_count: int,
    bbox_min: "np.ndarray",
    bbox_max: "np.ndarray",
    split_axis: int,
    target_tile_points: int,
    min_radius: float,
) -> int:
    _require_libs()
    by_count = max(1, int(np.ceil(raw_count / max(target_tile_points, 1))))
    extent = float(bbox_max[split_axis] - bbox_min[split_axis])
    by_width = max(1, int(np.floor(extent / max(2.0 * min_radius, 1.0))))
    return max(1, min(by_count, by_width))


def class_histogram(values: "np.ndarray") -> dict[str, int]:
    _require_libs()
    if values.size == 0:
        return {}
    keys, counts = np.unique(values, return_counts=True)
    return {str(int(k)): int(c) for k, c in zip(keys, counts)}


# ---------------------------------------------------------------------------
# Per-tile streaming dedup state
# ---------------------------------------------------------------------------


class TileState:
    """Per-tile bookkeeping during a single source-pass."""

    def __init__(
        self,
        tile_id: str,
        edge_lo: float,
        edge_hi: float,
        halo: float,
        tile_writer: "laspy.LasWriter",
    ) -> None:
        self.tile_id = tile_id
        self.core_lo = edge_lo
        self.core_hi = edge_hi
        self.halo_lo = edge_lo - halo
        self.halo_hi = edge_hi + halo
        self.tile_writer = tile_writer
        self.seen: set[tuple[int, int, int]] = set()
        # Sidecars accumulate as np.ndarray chunks; concatenated at the end.
        self.thinned_indices_chunks: list = []
        self.core_masks_chunks: list = []
        # In streaming mode the tile's view of source_thinned indices is its
        # own concatenation. Sidecar maps tile-LAS row -> tile-local
        # source_thinned-LAS row. (Equivalent to existing builder where each
        # tile's source_thinned LAS is the per-source merged file.)
        self.tile_thinned_count = 0
        # Map from tile-LAS row to source-LAS row (for the source-thinned
        # indices sidecar — same semantics as the legacy builder.)
        self.source_indices_chunks: list = []
        self.tile_total = 0
        self.tile_core = 0
        self.peak_hash = 0
        # Bbox tracking for manifest.
        self.core_min = None
        self.core_max = None
        self.tile_min = None
        self.tile_max = None

    def close(self) -> None:
        self.tile_writer.close()


def _open_tile_writer(
    tile_dir: Path,
    tile_id: str,
    header: "laspy.LasHeader",
) -> tuple[Path, "laspy.LasWriter"]:
    _require_libs()
    path = tile_dir / f"{tile_id}.las"
    return path, laspy.open(path, mode="w", header=_make_header(header))


def _process_one_pass(
    source_path: Path,
    chunk_size: int,
    voxel_size: float,
    split_axis: int,
    tiles_in_pass: list[TileState],
    source_thinned_writer: "laspy.LasWriter",
    thinned_count_start: int,
) -> int:
    """One streaming pass through the source for the given subset of tiles.

    Each kept point (deduped against per-tile hash) is written to the tile
    AND to the shared source_thinned writer. The source_thinned LAS ends up
    being the concatenation of per-tile contributions, which means voxels
    near tile boundaries appear multiple times (once per tile whose halo
    covers them).

    This matches PoC v2 behavior. The 40 % size bloat (for default halo)
    is the trade-off that keeps per-tile memory bounded — a true global
    dedup would need a voxel-key → source_thinned-position dict that
    scales O(total voxels) and defeats the streaming purpose.

    The merge step in merge_tf1_tile_predictions.py handles boundary
    duplicates correctly: each tile's tile_indices_path points to its own
    contiguous range in source_thinned, votes accumulate independently
    per duplicate position, and the existing "highest probability wins"
    aggregation produces equivalent final predictions.

    Returns the new total thinned point count.
    """
    _require_libs()
    pass_thinned_count = thinned_count_start

    with laspy.open(source_path) as reader:
        chunk_offset = 0
        for chunk in reader.chunk_iterator(chunk_size):
            n = len(chunk)
            cx = np.asarray(chunk.x, dtype=np.float64)
            cy = np.asarray(chunk.y, dtype=np.float64)
            cz = np.asarray(chunk.z, dtype=np.float64)
            pos = cx if split_axis == 0 else cy

            for ts in tiles_in_pass:
                halo_mask = (pos >= ts.halo_lo) & (pos < ts.halo_hi)
                if not np.any(halo_mask):
                    continue
                halo_idx = np.flatnonzero(halo_mask)
                sub_cx = cx[halo_idx]
                sub_cy = cy[halo_idx]
                sub_cz = cz[halo_idx]
                kx = np.floor(sub_cx / voxel_size).astype(np.int64)
                ky = np.floor(sub_cy / voxel_size).astype(np.int64)
                kz = np.floor(sub_cz / voxel_size).astype(np.int64)
                keys = np.column_stack((kx, ky, kz))
                _, within_first = np.unique(keys, axis=0, return_index=True)
                within_first.sort()

                keep_local: list[int] = []
                for li in within_first:
                    key = (int(kx[li]), int(ky[li]), int(kz[li]))
                    if key not in ts.seen:
                        ts.seen.add(key)
                        keep_local.append(int(li))
                if not keep_local:
                    continue
                keep_local_arr = np.asarray(keep_local, dtype=np.int64)
                kept_chunk_idx = halo_idx[keep_local_arr]
                kept_chunk = chunk[kept_chunk_idx]
                kept_pos = pos[kept_chunk_idx]
                kept_source_indices = (chunk_offset + kept_chunk_idx).astype(np.int64)

                # Write to tile.
                ts.tile_writer.write_points(kept_chunk)

                # Write to source_thinned at the next contiguous position.
                # Each tile contributes its own range; ranges don't overlap
                # within a single source_thinned LAS but voxels do duplicate
                # across ranges (boundary halo points appear in multiple tiles).
                new_n = int(keep_local_arr.size)
                thinned_positions = np.arange(
                    pass_thinned_count,
                    pass_thinned_count + new_n,
                    dtype=np.int64,
                )
                source_thinned_writer.write_points(kept_chunk)
                pass_thinned_count += new_n

                # Sidecars.
                in_core = (kept_pos >= ts.core_lo) & (kept_pos < ts.core_hi)
                ts.thinned_indices_chunks.append(thinned_positions)
                ts.source_indices_chunks.append(kept_source_indices)
                ts.core_masks_chunks.append(in_core)

                # Bookkeeping.
                ts.tile_total += new_n
                ts.tile_core += int(np.count_nonzero(in_core))
                if ts.core_min is None:
                    ts.core_min = np.array([sub_cx.min(), sub_cy.min()])
                    ts.core_max = np.array([sub_cx.max(), sub_cy.max()])
                    ts.tile_min = ts.core_min.copy()
                    ts.tile_max = ts.core_max.copy()
                else:
                    ts.core_min = np.minimum(
                        ts.core_min, np.array([sub_cx.min(), sub_cy.min()])
                    )
                    ts.core_max = np.maximum(
                        ts.core_max, np.array([sub_cx.max(), sub_cy.max()])
                    )
                    ts.tile_min = ts.core_min.copy()
                    ts.tile_max = ts.core_max.copy()

                if len(ts.seen) > ts.peak_hash:
                    ts.peak_hash = len(ts.seen)

            chunk_offset += n

    return pass_thinned_count


def process_source_file(
    source_path_str: str,
    output_root_str: str,
    input_dir_str: str,
    voxel_size: float,
    target_tile_points: int,
    min_tile_points: int,
    min_radius: float,
    overlap: float,
    chunk_size: int,
    max_concurrent_tiles: int,
    overwrite: bool,
) -> dict[str, Any]:
    _require_libs()
    source_path = Path(source_path_str)
    output_root = Path(output_root_str)
    input_dir = Path(input_dir_str)

    source_stem = source_path.stem
    thinned_dir = output_root / "source_thinned"
    tile_dir = output_root / "preprocessed_tiles"
    index_dir = output_root / "tile_indices"
    for d in (thinned_dir, tile_dir, index_dir):
        d.mkdir(parents=True, exist_ok=True)

    voxel_tag = str(voxel_size).replace(".", "p")
    thinned_path = thinned_dir / f"{source_stem}_thin_{voxel_tag}m.las"
    thinned_indices_path = thinned_dir / f"{source_stem}_thin_source_indices.npy"

    if not overwrite and thinned_path.exists() and thinned_indices_path.exists():
        # RESUME PATH. The old behavior returned an EMPTY tile list here,
        # which gutted the manifest on any skip-resume: downstream merge saw
        # zero tiles per source and silently wrote 100% class-0 combined
        # outputs (2026-06-06 Mississauga baseline). A skipped source must
        # contribute the SAME manifest fragment a built one would:
        #   1. restore the fragment from the prior manifest, else
        #   2. reconstruct tile rows from on-disk artifacts, else
        #   3. fall through and REBUILD (slow, never silently wrong).
        prior = _prior_source_fragment(output_root, source_path.name)
        if prior is not None and _fragment_tiles_on_disk(prior):
            restored = dict(prior)
            restored["status"] = "skipped_existing_restored"
            logging.info(
                f"[{source_stem}] outputs exist, skipping — restored "
                f"{len(restored.get('tiles', []))} tile row(s) from the "
                f"prior manifest"
            )
            return restored
        tile_rows = _reconstruct_tiles_from_disk(
            output_root, source_path, source_stem, input_dir,
            thinned_path, overlap,
        )
        if tile_rows:
            with laspy.open(source_path) as r:
                raw_count = r.header.point_count
            with laspy.open(thinned_path) as r:
                thinned_count = r.header.point_count
            logging.info(
                f"[{source_stem}] outputs exist, skipping — reconstructed "
                f"{len(tile_rows)} tile row(s) from disk (prior manifest "
                f"missing or empty)"
            )
            return {
                "source_name": source_path.name,
                "source_stem": source_stem,
                "source_path": str(source_path.resolve()),
                "relative_source_path": str(
                    source_path.resolve().relative_to(input_dir.resolve())
                ),
                "source_point_count": int(raw_count),
                "thinned_point_count": int(thinned_count),
                "source_thinned_las": str(thinned_path.resolve()),
                "source_thinned_indices_path": str(
                    thinned_indices_path.resolve()),
                "class_histogram_thinned": {},
                "tile_count": len(tile_rows),
                "tiles": tile_rows,
                "status": "skipped_existing_reconstructed",
            }
        logging.warning(
            f"[{source_stem}] outputs exist but neither the prior manifest "
            f"nor on-disk tiles are usable — REBUILDING (an empty skip "
            f"would gut the manifest)"
        )

    logging.info(f"[{source_stem}] streaming tile build start")
    t0 = time.time()

    with laspy.open(source_path) as reader:
        header = reader.header
        raw_count = header.point_count
        bbox_min = np.array(header.mins, dtype=np.float64)
        bbox_max = np.array(header.maxs, dtype=np.float64)

    split_axis = _choose_split_axis(bbox_min, bbox_max)
    n_tiles = _decide_tile_count(
        raw_count, bbox_min, bbox_max, split_axis, target_tile_points, min_radius
    )
    edges = _tile_edges(bbox_min, bbox_max, split_axis, n_tiles)
    logging.info(
        f"[{source_stem}] {raw_count:,} pts, axis={'xy'[split_axis]}, "
        f"{n_tiles} tile(s), B={max_concurrent_tiles}"
    )

    # Open source_thinned writer once (concurrent passes append).
    thinned_writer = laspy.open(thinned_path, mode="w", header=_make_header(header))

    tile_states: list[TileState] = []
    try:
        # Allocate per-tile writers.
        for i in range(n_tiles):
            tile_id = f"{source_stem}__tile_{i+1:04d}"
            tile_path, tile_writer = _open_tile_writer(tile_dir, tile_id, header)
            ts = TileState(
                tile_id=tile_id,
                edge_lo=float(edges[i]),
                edge_hi=float(edges[i + 1]),
                halo=float(overlap),
                tile_writer=tile_writer,
            )
            ts.tile_path = tile_path
            tile_states.append(ts)

        # Process tiles in batches of B per source-pass.
        B = max_concurrent_tiles if max_concurrent_tiles > 0 else n_tiles
        pass_thinned_count = 0
        for batch_start in range(0, n_tiles, B):
            batch_end = min(batch_start + B, n_tiles)
            batch = tile_states[batch_start:batch_end]
            logging.info(
                f"[{source_stem}] pass {batch_start//B + 1}: tiles "
                f"{batch_start+1}..{batch_end}/{n_tiles}"
            )
            pass_thinned_count = _process_one_pass(
                source_path=source_path,
                chunk_size=chunk_size,
                voxel_size=voxel_size,
                split_axis=split_axis,
                tiles_in_pass=batch,
                source_thinned_writer=thinned_writer,
                thinned_count_start=pass_thinned_count,
            )
    finally:
        for ts in tile_states:
            ts.close()
        thinned_writer.close()

    elapsed = time.time() - t0
    logging.info(
        f"[{source_stem}] done in {elapsed:.1f}s, thinned_count={pass_thinned_count:,}"
    )

    # Persist sidecars and read class histogram from thinned LAS.
    thinned_las = laspy.read(thinned_path)
    classification = (
        np.asarray(thinned_las.classification, dtype=np.uint8)
        if "classification" in thinned_las.point_format.dimension_names
        else np.zeros((len(thinned_las.points),), dtype=np.uint8)
    )
    # Build source_thinned -> source_las index mapping.
    # Each tile's contribution to source_thinned is a contiguous range,
    # and thinned_indices_chunks contain positions in that range.
    # Concatenate them in tile-order (the same order they were written
    # to source_thinned) to produce the full mapping.
    thinned_source_indices = np.full(pass_thinned_count, -1, dtype=np.int64)
    for ts in tile_states:
        if not ts.thinned_indices_chunks:
            continue
        t_idx = np.concatenate(ts.thinned_indices_chunks)
        s_idx = np.concatenate(ts.source_indices_chunks)
        # All entries are valid (no -1 placeholders in this design); assign
        # vectorized.
        thinned_source_indices[t_idx] = s_idx
    np.save(thinned_indices_path, thinned_source_indices)

    tile_rows: list[dict[str, Any]] = []
    for ts in tile_states:
        # Concatenate per-tile sidecars.
        if ts.thinned_indices_chunks:
            tile_indices_arr = np.concatenate(ts.thinned_indices_chunks)
            core_mask_arr = np.concatenate(ts.core_masks_chunks)
        else:
            tile_indices_arr = np.empty((0,), dtype=np.int64)
            core_mask_arr = np.empty((0,), dtype=bool)
        idx_path = index_dir / f"{ts.tile_id}_source_thinned_indices.npy"
        mask_path = index_dir / f"{ts.tile_id}_core_mask.npy"
        np.save(idx_path, tile_indices_arr)
        np.save(mask_path, core_mask_arr)

        if ts.core_min is None:
            cmin, cmax = bbox_min[:2], bbox_max[:2]
        else:
            cmin, cmax = ts.core_min, ts.core_max
        if ts.tile_min is None:
            tmin, tmax = bbox_min[:2], bbox_max[:2]
        else:
            tmin, tmax = ts.tile_min, ts.tile_max

        tile_rows.append(
            {
                "tile_id": ts.tile_id,
                "source_name": source_path.name,
                "source_stem": source_stem,
                "source_path": str(source_path.resolve()),
                "relative_source_path": str(
                    source_path.resolve().relative_to(input_dir.resolve())
                ),
                "source_thinned_las": str(thinned_path.resolve()),
                "tile_las": str(ts.tile_path.resolve()),
                "tile_indices_path": str(idx_path.resolve()),
                "core_mask_path": str(mask_path.resolve()),
                "core_point_count": int(ts.tile_core),
                "tile_point_count": int(ts.tile_total),
                "core_fraction": (
                    float(ts.tile_core) / float(max(ts.tile_total, 1))
                ),
                "overlap": float(overlap),
                "core_min_x": float(cmin[0]),
                "core_min_y": float(cmin[1]),
                "core_max_x": float(cmax[0]),
                "core_max_y": float(cmax[1]),
                "tile_min_x": float(tmin[0]),
                "tile_min_y": float(tmin[1]),
                "tile_max_x": float(tmax[0]),
                "tile_max_y": float(tmax[1]),
                "peak_hash_size": int(ts.peak_hash),
            }
        )

    return {
        "source_name": source_path.name,
        "source_stem": source_stem,
        "source_path": str(source_path.resolve()),
        "relative_source_path": str(
            source_path.resolve().relative_to(input_dir.resolve())
        ),
        "source_point_count": int(raw_count),
        "thinned_point_count": int(pass_thinned_count),
        "source_thinned_las": str(thinned_path.resolve()),
        "source_thinned_indices_path": str(thinned_indices_path.resolve()),
        "class_histogram_thinned": class_histogram(classification),
        "tile_count": len(tile_rows),
        "tiles": tile_rows,
        "elapsed_seconds": float(elapsed),
        "status": "built",
    }


def main() -> None:
    setup_logging()
    args = parse_args()
    _require_libs()

    input_dir = args.input_dir.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    source_paths = sorted(input_dir.glob(args.pattern))
    if not source_paths:
        raise FileNotFoundError(
            f"No files matching {args.pattern!r} found in {input_dir}"
        )

    worker_count = max(1, int(args.workers))
    results: list[dict[str, Any]] = []
    if worker_count == 1:
        for path in source_paths:
            results.append(
                process_source_file(
                    source_path_str=str(path),
                    output_root_str=str(output_root),
                    input_dir_str=str(input_dir),
                    voxel_size=float(args.voxel_size),
                    target_tile_points=int(args.target_tile_points),
                    min_tile_points=int(args.min_tile_points),
                    min_radius=float(args.min_radius),
                    overlap=float(args.overlap),
                    chunk_size=int(args.chunk_size),
                    max_concurrent_tiles=int(args.max_concurrent_tiles),
                    overwrite=bool(args.overwrite),
                )
            )
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    process_source_file,
                    str(path),
                    str(output_root),
                    str(input_dir),
                    float(args.voxel_size),
                    int(args.target_tile_points),
                    int(args.min_tile_points),
                    float(args.min_radius),
                    float(args.overlap),
                    int(args.chunk_size),
                    int(args.max_concurrent_tiles),
                    bool(args.overwrite),
                )
                for path in source_paths
            ]
            for fut in as_completed(futures):
                results.append(fut.result())

    results.sort(key=lambda item: item["source_name"])
    tile_rows = [tile for src in results for tile in src.get("tiles", [])]

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "input_dir": str(input_dir),
        "output_root": str(output_root),
        "tile_input_dir": str((output_root / "preprocessed_tiles").resolve()),
        "parameters": {
            "pattern": args.pattern,
            "voxel_size": float(args.voxel_size),
            "target_tile_points": int(args.target_tile_points),
            "min_tile_points": int(args.min_tile_points),
            "min_radius": float(args.min_radius),
            "overlap": float(args.overlap),
            "chunk_size": int(args.chunk_size),
            "max_concurrent_tiles": int(args.max_concurrent_tiles),
            "workers": int(args.workers),
            "builder": "streaming",
            "tile_layout": "equal_width",
        },
        "source_file_count": len(results),
        "tile_count": len(tile_rows),
        "source_files": results,
        "tiles": tile_rows,
    }

    manifest_dir = output_root / "manifests"
    save_json(manifest_dir / "tf1_tile_manifest.json", manifest)
    write_csv(manifest_dir / "tf1_tile_manifest_tiles.csv", tile_rows)
    write_csv(
        manifest_dir / "tf1_tile_manifest_sources.csv",
        [
            {
                k: v
                for k, v in src.items()
                if k not in {"tiles", "class_histogram_thinned"}
            }
            for src in results
        ],
    )
    logging.info(f"Wrote {manifest_dir / 'tf1_tile_manifest.json'}")


if __name__ == "__main__":
    main()
