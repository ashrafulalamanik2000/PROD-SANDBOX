from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import laspy
    import numpy as np

np = None


def require_numpy():
    global np
    if np is None:
        import numpy as imported_numpy

        np = imported_numpy
    return np


def _horizontal_linear_unit(crs):
    """Return (unit_name_lower, factor_to_metre) for the CRS's horizontal axes,
    or (None, None) when it cannot be determined."""
    try:
        axes = list(crs.axis_info)
    except Exception:
        return None, None
    for axis in axes:
        direction = (getattr(axis, "direction", "") or "").lower()
        if direction in ("east", "west", "north", "south"):
            return (axis.unit_name or "").lower(), getattr(axis, "unit_conversion_factor", None)
    if axes:
        return (axes[0].unit_name or "").lower(), getattr(axes[0], "unit_conversion_factor", None)
    return None, None


def _warn_units(source_path: Path, reason: str) -> None:
    print(
        f"[build_tf1_inference_tiles] WARNING: {source_path.name}: {reason}. "
        f"Cannot confirm the cloud is in metres and proceeding anyway; PointConv "
        f"features (HAG/linearity/verticality) and the --voxel-size voxel are "
        f"metre-denominated, so if this is feet the tiles will be silently "
        f"mis-scaled. Reproject with tools/reproject_las_dir.py if unsure.",
        file=sys.stderr,
    )


def check_metric_units(header: "laspy.LasHeader", source_path: Path, allow_non_metric: bool) -> None:
    """Fail loud when a source LAS is not in projected metres.

    This tiler voxelizes raw XYZ at --voxel-size and the downstream PointConv
    features/grouping radii are all metre-denominated, with NO unit conversion
    anywhere on the tiled-inference path. A US-survey-foot (or geographic) cloud
    is therefore silently mis-scaled (a 0.1 m voxel becomes 0.1 ft, every
    neighborhood radius shrinks ~3.28x). We refuse such input early.

      * projected + metric horizontal unit       -> pass
      * projected + non-metric (feet) / geographic (degrees) -> raise
      * CRS missing / unparseable / pyproj absent -> loud warning, pass
        (some valid metric clouds carry no CRS VLR; don't hard-block them)
      * --allow-non-metric downgrades the hard failure to a warning
    """
    try:
        crs = header.parse_crs()
    except Exception as exc:  # pyproj absent or a malformed CRS VLR
        _warn_units(source_path, f"could not parse the CRS ({type(exc).__name__}: {exc})")
        return
    if crs is None:
        _warn_units(source_path, "the LAS header carries no CRS")
        return

    unit_name, factor = _horizontal_linear_unit(crs)
    if (not crs.is_geographic) and factor is not None and abs(factor - 1.0) < 1e-6:
        return  # confirmed projected metres

    if crs.is_geographic:
        detail = f"geographic (degrees, EPSG:{crs.to_epsg()})"
    elif unit_name:
        detail = f"horizontal unit {unit_name!r} (x{factor} to the metre, EPSG:{crs.to_epsg()})"
    else:
        detail = f"a non-metric / undetermined unit (EPSG:{crs.to_epsg()})"

    message = (
        f"{source_path.name}: source CRS is {detail}, not projected metres. The TF1 "
        f"tiled-inference path voxelizes raw XYZ at --voxel-size and uses metre-"
        f"denominated PointConv features/radii with NO unit conversion, so non-metric "
        f"input is silently mis-scaled. Reproject to a metric CRS first "
        f"(e.g. tools/reproject_las_dir.py), then re-run."
    )
    if allow_non_metric:
        _warn_units(source_path, message + "  [--allow-non-metric set: continuing]")
        return
    raise ValueError(message + "  Pass --allow-non-metric to override.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build 0.1 m thinned, overlapping LAS tiles for the legacy TF1 "
            "PointCONV inference path."
        )
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pattern", default="*.las")
    parser.add_argument("--voxel-size", type=float, default=0.1)
    parser.add_argument("--target-tile-points", type=int, default=400_000)
    parser.add_argument("--min-tile-points", type=int, default=25_000)
    parser.add_argument("--min-radius", type=float, default=20.0)
    parser.add_argument(
        "--overlap",
        type=float,
        default=20.0,
        help="Initial XY halo around each tile core, in source coordinate units.",
    )
    parser.add_argument(
        "--overlap-step",
        type=float,
        default=10.0,
        help="Extra halo added when a sparse tile needs more context points.",
    )
    parser.add_argument(
        "--max-overlap",
        type=float,
        default=100.0,
        help="Maximum halo used when trying to satisfy min-tile-points.",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--allow-non-metric",
        action="store_true",
        help=(
            "Downgrade the metric-CRS check from a hard failure to a warning. Use "
            "ONLY when the data is already in metres but the CRS tag is wrong/missing."
        ),
    )
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
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def class_histogram(classification: np.ndarray) -> dict[str, int]:
    if classification.size == 0:
        return {}
    values, counts = np.unique(classification, return_counts=True)
    return {str(int(value)): int(count) for value, count in zip(values, counts)}


def voxel_first_indices(xyz: np.ndarray, voxel_size: float) -> np.ndarray:
    require_numpy()
    if voxel_size <= 0:
        raise ValueError("voxel_size must be > 0")
    keys = np.floor(xyz / float(voxel_size)).astype(np.int64)
    _, first_indices = np.unique(keys, axis=0, return_index=True)
    return np.sort(first_indices.astype(np.int64))


def write_las_subset(source_las: "laspy.LasData", indices: np.ndarray, output_path: Path) -> None:
    import laspy

    require_numpy()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output = laspy.LasData(copy.deepcopy(source_las.header))
    output.points = source_las.points[indices]
    output.write(output_path)


def split_core_indices(
    xyz: np.ndarray,
    target_tile_points: int,
    min_tile_points: int,
) -> list[np.ndarray]:
    require_numpy()
    if xyz.shape[0] == 0:
        return []
    if xyz.shape[0] <= target_tile_points:
        return [np.arange(xyz.shape[0], dtype=np.int64)]

    ranges = np.ptp(xyz[:, :2], axis=0)
    axis = int(np.argmax(ranges))
    order = np.argsort(xyz[:, axis], kind="mergesort").astype(np.int64)

    chunks = [
        order[start : start + target_tile_points]
        for start in range(0, order.shape[0], target_tile_points)
    ]

    if len(chunks) > 1 and chunks[-1].shape[0] < min_tile_points:
        chunks[-2] = np.concatenate([chunks[-2], chunks[-1]])
        chunks.pop()

    return chunks


def expand_bounds_to_min_radius(bounds: np.ndarray, min_radius: float) -> np.ndarray:
    require_numpy()
    expanded = bounds.astype(np.float64).copy()
    min_width = 2.0 * float(min_radius)
    for axis in range(2):
        width = expanded[1, axis] - expanded[0, axis]
        if width < min_width:
            center = 0.5 * (expanded[0, axis] + expanded[1, axis])
            expanded[0, axis] = center - min_radius
            expanded[1, axis] = center + min_radius
    return expanded


def tile_mask_from_bounds(xy: np.ndarray, bounds: np.ndarray) -> np.ndarray:
    require_numpy()
    return (
        (xy[:, 0] >= bounds[0, 0])
        & (xy[:, 0] <= bounds[1, 0])
        & (xy[:, 1] >= bounds[0, 1])
        & (xy[:, 1] <= bounds[1, 1])
    )


def process_source_file(
    source_path: str,
    output_root: str,
    input_dir: str,
    voxel_size: float,
    target_tile_points: int,
    min_tile_points: int,
    min_radius: float,
    overlap: float,
    overlap_step: float,
    max_overlap: float,
    overwrite: bool,
) -> dict[str, Any]:
    import laspy

    require_numpy()
    source_path_obj = Path(source_path)
    output_root_obj = Path(output_root)
    input_dir_obj = Path(input_dir)

    source_stem = source_path_obj.stem
    thinned_dir = output_root_obj / "source_thinned"
    tile_dir = output_root_obj / "preprocessed_tiles"
    index_dir = output_root_obj / "tile_indices"

    thinned_las_path = thinned_dir / f"{source_stem}_thin_{str(voxel_size).replace('.', 'p')}m.las"
    thinned_indices_path = thinned_dir / f"{source_stem}_thin_source_indices.npy"

    if (
        thinned_las_path.exists()
        and thinned_indices_path.exists()
        and not overwrite
    ):
        source_las = laspy.read(source_path_obj)
        thinned_las = laspy.read(thinned_las_path)
        thinned_indices = np.load(thinned_indices_path)
    else:
        source_las = laspy.read(source_path_obj)
        xyz = np.column_stack(
            (
                np.asarray(source_las.x),
                np.asarray(source_las.y),
                np.asarray(source_las.z),
            )
        )
        thinned_indices = voxel_first_indices(xyz, voxel_size)
        thinned_dir.mkdir(parents=True, exist_ok=True)
        np.save(thinned_indices_path, thinned_indices)
        write_las_subset(source_las, thinned_indices, thinned_las_path)
        thinned_las = laspy.read(thinned_las_path)

    thinned_xyz = np.column_stack(
        (
            np.asarray(thinned_las.x),
            np.asarray(thinned_las.y),
            np.asarray(thinned_las.z),
        )
    )
    thinned_xy = thinned_xyz[:, :2]

    core_chunks = split_core_indices(thinned_xyz, target_tile_points, min_tile_points)

    tile_rows: list[dict[str, Any]] = []
    for tile_number, core_indices in enumerate(core_chunks, start=1):
        core_xy = thinned_xy[core_indices]
        core_bounds = np.array([core_xy.min(axis=0), core_xy.max(axis=0)], dtype=np.float64)
        base_bounds = expand_bounds_to_min_radius(core_bounds, min_radius)

        chosen_overlap = float(overlap)
        while True:
            tile_bounds = base_bounds.copy()
            tile_bounds[0, :] -= chosen_overlap
            tile_bounds[1, :] += chosen_overlap
            tile_mask = tile_mask_from_bounds(thinned_xy, tile_bounds)
            tile_indices = np.flatnonzero(tile_mask).astype(np.int64)
            if (
                tile_indices.shape[0] >= min_tile_points
                or chosen_overlap >= max_overlap
                or tile_indices.shape[0] == thinned_xyz.shape[0]
            ):
                break
            chosen_overlap += float(overlap_step)

        core_lookup = set(int(value) for value in core_indices.tolist())
        core_mask = np.array([int(value) in core_lookup for value in tile_indices], dtype=bool)

        tile_name = f"{source_stem}__tile_{tile_number:04d}"
        tile_las_path = tile_dir / f"{tile_name}.las"
        tile_indices_path = index_dir / f"{tile_name}_source_thinned_indices.npy"
        core_mask_path = index_dir / f"{tile_name}_core_mask.npy"

        if overwrite or not tile_las_path.exists():
            write_las_subset(thinned_las, tile_indices, tile_las_path)
        index_dir.mkdir(parents=True, exist_ok=True)
        np.save(tile_indices_path, tile_indices)
        np.save(core_mask_path, core_mask)

        tile_rows.append(
            {
                "tile_id": tile_name,
                "source_name": source_path_obj.name,
                "source_stem": source_stem,
                "source_path": str(source_path_obj.resolve()),
                "relative_source_path": str(source_path_obj.resolve().relative_to(input_dir_obj.resolve())),
                "source_thinned_las": str(thinned_las_path.resolve()),
                "tile_las": str(tile_las_path.resolve()),
                "tile_indices_path": str(tile_indices_path.resolve()),
                "core_mask_path": str(core_mask_path.resolve()),
                "core_point_count": int(core_indices.shape[0]),
                "tile_point_count": int(tile_indices.shape[0]),
                "core_fraction": float(core_indices.shape[0] / max(tile_indices.shape[0], 1)),
                "overlap": float(chosen_overlap),
                "core_min_x": float(core_bounds[0, 0]),
                "core_min_y": float(core_bounds[0, 1]),
                "core_max_x": float(core_bounds[1, 0]),
                "core_max_y": float(core_bounds[1, 1]),
                "tile_min_x": float(tile_bounds[0, 0]),
                "tile_min_y": float(tile_bounds[0, 1]),
                "tile_max_x": float(tile_bounds[1, 0]),
                "tile_max_y": float(tile_bounds[1, 1]),
            }
        )

    classification = (
        np.asarray(thinned_las.classification)
        if "classification" in thinned_las.point_format.dimension_names
        else np.zeros((len(thinned_las.points),), dtype=np.uint8)
    )

    return {
        "source_name": source_path_obj.name,
        "source_stem": source_stem,
        "source_path": str(source_path_obj.resolve()),
        "relative_source_path": str(source_path_obj.resolve().relative_to(input_dir_obj.resolve())),
        "source_point_count": int(len(source_las.points)),
        "thinned_point_count": int(len(thinned_las.points)),
        "source_thinned_las": str(thinned_las_path.resolve()),
        "source_thinned_indices_path": str(thinned_indices_path.resolve()),
        "class_histogram_thinned": class_histogram(classification),
        "tile_count": len(tile_rows),
        "tiles": tile_rows,
    }


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    source_paths = sorted(input_dir.glob(args.pattern))
    if not source_paths:
        raise FileNotFoundError(f"No files matching {args.pattern!r} found in {input_dir}")

    # Pre-flight: every source must be in projected metres. Reading just the LAS
    # header (no points) is cheap, and failing here avoids tiling a mis-scaled cloud.
    import laspy

    allow_non_metric = bool(args.allow_non_metric)
    for source_path in source_paths:
        with laspy.open(source_path) as reader:
            check_metric_units(reader.header, source_path, allow_non_metric)

    worker_count = max(1, int(args.workers))
    results: list[dict[str, Any]] = []

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
                float(args.overlap_step),
                float(args.max_overlap),
                bool(args.overwrite),
            )
            for path in source_paths
        ]
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: item["source_name"])
    tile_rows = [tile for source in results for tile in source["tiles"]]

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
            "overlap_step": float(args.overlap_step),
            "max_overlap": float(args.max_overlap),
            "workers": int(args.workers),
            "allow_non_metric": allow_non_metric,
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
                key: value
                for key, value in source.items()
                if key not in {"tiles", "class_histogram_thinned"}
            }
            for source in results
        ],
    )
    print(f"Wrote {manifest_dir / 'tf1_tile_manifest.json'}")


if __name__ == "__main__":
    main()
