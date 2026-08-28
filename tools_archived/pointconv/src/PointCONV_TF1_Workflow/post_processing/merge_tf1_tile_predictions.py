from __future__ import annotations

import argparse
import copy
import csv
import json
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge legacy TF1 PointCONV tile predictions back to one thinned LAS per source file."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--tf1-output-root",
        type=Path,
        required=True,
        help="Output folder used as --out_folder for tf1/classification.py.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--match-tolerance", type=float, default=0.025)
    parser.add_argument("--default-class", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--write-core-only-output",
        action="store_true",
        help=(
            "Also write a *_core_only.las sidecar containing only rows that "
            "received at least one accepted core tile prediction."
        ),
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def xyz_array(las: "laspy.LasData") -> np.ndarray:
    require_numpy()
    return np.column_stack((np.asarray(las.x), np.asarray(las.y), np.asarray(las.z))).astype(np.float64)


def add_or_set_extra_dim(las: "laspy.LasData", name: str, values: np.ndarray, dtype: Any, description: str) -> None:
    import laspy

    require_numpy()
    existing_names = set(las.point_format.dimension_names)
    values = np.asarray(values, dtype=dtype)
    if name not in existing_names:
        las.add_extra_dim(laspy.ExtraBytesParams(name=name, type=np.dtype(dtype), description=description))
    setattr(las, name, values)


def find_raw_prediction(tf1_output_root: Path, tile_id: str) -> Path | None:
    tile_dir = tf1_output_root / tile_id
    expected = tile_dir / f"{tile_id}_t_raw.las"
    if expected.exists():
        return expected
    matches = sorted(tile_dir.glob("*_raw.las"))
    if matches:
        return matches[0]
    return None


def class_histogram(values: np.ndarray) -> dict[str, int]:
    require_numpy()
    if values.size == 0:
        return {}
    keys, counts = np.unique(values, return_counts=True)
    return {str(int(key)): int(count) for key, count in zip(keys, counts)}


def _prefer_vote(p_new, c_new, p_cur, c_cur, first_vote) -> bool:
    """Total-order preference for an overlap-halo vote, independent of tile
    iteration order: take the first vote, else the higher probability, else (on
    equal probability) the lower class id. Pure scalars (no numpy needed) so it
    is unit-testable. See merge_source()'s duplicate-ownership loop."""
    return bool(first_vote or p_new > p_cur or (p_new == p_cur and c_new < c_cur))


def merge_source(
    source_record: dict[str, Any],
    tf1_output_root: str,
    output_dir: str,
    match_tolerance: float,
    default_class: int,
    overwrite: bool,
    write_core_only_output: bool,
) -> dict[str, Any]:
    import laspy
    from scipy.spatial import cKDTree

    require_numpy()
    tf1_root = Path(tf1_output_root)
    output_root = Path(output_dir)

    source_stem = source_record["source_stem"]
    source_thinned_las = Path(source_record["source_thinned_las"])
    output_las_path = output_root / f"{source_stem}_tf1_pointconv_combined_0p1m.las"
    core_only_las_path = output_root / f"{source_stem}_tf1_pointconv_combined_0p1m_core_only.las"

    if output_las_path.exists() and not overwrite:
        return {
            "source_name": source_record["source_name"],
            "source_stem": source_stem,
            "output_las": str(output_las_path.resolve()),
            "status": "skipped_existing",
        }

    # Guard (2026-06-06): an empty tiles list means the manifest carries no
    # prediction tiles for this source (seen when stage-1 preprocessing was
    # RESUMED and the builder's skip path dropped tile records) — merging
    # would silently write an ALL-CLASS-0 output with status "merged".
    # Refuse loudly instead so the chain fails fast at the real cause.
    if not source_record.get("tiles"):
        print(f"[merge] ERROR {source_stem}: manifest has NO tiles for this "
              f"source — refusing to write an unclassified output "
              f"(rebuild the tile manifest; see the streaming builder's "
              f"resume path)", flush=True)
        return {
            "source_name": source_record["source_name"],
            "source_stem": source_stem,
            "output_las": str(output_las_path.resolve()),
            "status": "no_tiles_in_manifest",
        }

    source_las = laspy.read(source_thinned_las)
    point_count = len(source_las.points)

    source_classification = (
        np.asarray(source_las.classification, dtype=np.uint8)
        if "classification" in source_las.point_format.dimension_names
        else np.zeros((point_count,), dtype=np.uint8)
    )
    predicted_class = np.full((point_count,), int(default_class), dtype=np.uint8)
    predicted_prob = np.zeros((point_count,), dtype=np.float32)
    vote_count = np.zeros((point_count,), dtype=np.uint16)

    tile_summaries = []
    missing_tiles = 0
    rejected_noncore = 0
    rejected_tolerance = 0
    accepted_predictions = 0

    for tile in source_record.get("tiles", []):
        tile_id = tile["tile_id"]
        tile_las_path = Path(tile["tile_las"])
        raw_prediction_path = find_raw_prediction(tf1_root, tile_id)
        if raw_prediction_path is None:
            missing_tiles += 1
            tile_summaries.append(
                {
                    "tile_id": tile_id,
                    "status": "missing_raw_prediction",
                    "accepted_predictions": 0,
                }
            )
            continue

        tile_las = laspy.read(tile_las_path)
        raw_las = laspy.read(raw_prediction_path)
        tile_xyz = xyz_array(tile_las)
        raw_xyz = xyz_array(raw_las)
        tile_source_indices = np.load(tile["tile_indices_path"]).astype(np.int64)
        core_mask = np.load(tile["core_mask_path"]).astype(bool)

        if tile_source_indices.shape[0] != tile_xyz.shape[0]:
            raise ValueError(f"Tile index length mismatch for {tile_id}")
        if core_mask.shape[0] != tile_xyz.shape[0]:
            raise ValueError(f"Core mask length mismatch for {tile_id}")

        if raw_xyz.shape[0] == 0:
            tile_summaries.append(
                {
                    "tile_id": tile_id,
                    "status": "empty_raw_prediction",
                    "accepted_predictions": 0,
                }
            )
            continue

        tree = cKDTree(tile_xyz)
        distances, tile_positions = tree.query(raw_xyz, k=1)
        within_tolerance = distances <= float(match_tolerance)
        core_positions = np.zeros_like(within_tolerance, dtype=bool)
        core_positions[within_tolerance] = core_mask[tile_positions[within_tolerance]]

        accept_mask = within_tolerance & core_positions
        rejected_tolerance += int(np.count_nonzero(~within_tolerance))
        rejected_noncore += int(np.count_nonzero(within_tolerance & ~core_positions))

        if not np.any(accept_mask):
            tile_summaries.append(
                {
                    "tile_id": tile_id,
                    "status": "no_core_matches",
                    "accepted_predictions": 0,
                    "raw_prediction_count": int(raw_xyz.shape[0]),
                }
            )
            continue

        raw_classes = np.asarray(raw_las.classification, dtype=np.uint8)
        raw_prob = (
            np.asarray(raw_las.intensity, dtype=np.float32) / 65535.0
            if "intensity" in raw_las.point_format.dimension_names
            else np.ones((len(raw_las.points),), dtype=np.float32)
        )

        accepted_raw_indices = np.flatnonzero(accept_mask)
        accepted_tile_positions = tile_positions[accepted_raw_indices]
        source_indices = tile_source_indices[accepted_tile_positions]
        source_indices = source_indices.astype(np.int64)

        # Duplicate ownership can happen at numeric boundaries (a source point in
        # the overlap halo of >1 tile gets one vote per owning tile). Resolve with
        # a TOTAL order that does not depend on tile iteration order: highest
        # probability wins, and equal probabilities are broken by the lower class
        # id. The previous `>=` was last-writer-wins, so an equal-probability tie
        # resolved by whichever tile happened to be merged last — making boundary
        # labels depend on the (stable but incidental) manifest tile ordering.
        for raw_index, source_index in zip(accepted_raw_indices, source_indices):
            first_vote = vote_count[source_index] == 0
            vote_count[source_index] = np.uint16(min(int(vote_count[source_index]) + 1, 65535))
            p_new = raw_prob[raw_index]
            c_new = raw_classes[raw_index]
            if _prefer_vote(p_new, c_new, predicted_prob[source_index],
                            predicted_class[source_index], first_vote):
                predicted_prob[source_index] = p_new
                predicted_class[source_index] = c_new

        accepted_predictions += int(accepted_raw_indices.shape[0])
        tile_summaries.append(
            {
                "tile_id": tile_id,
                "status": "merged",
                "raw_prediction_count": int(raw_xyz.shape[0]),
                "accepted_predictions": int(accepted_raw_indices.shape[0]),
                "rejected_tolerance": int(np.count_nonzero(~within_tolerance)),
                "rejected_noncore": int(np.count_nonzero(within_tolerance & ~core_positions)),
                "raw_prediction_path": str(raw_prediction_path.resolve()),
            }
        )

    output_las = laspy.LasData(copy.deepcopy(source_las.header))
    output_las.points = source_las.points.copy()
    output_las.classification = predicted_class
    output_las.intensity = np.clip(predicted_prob * 65535.0, 0, 65535).astype(np.uint16)
    add_or_set_extra_dim(
        output_las,
        "source_class",
        source_classification,
        np.uint8,
        "Source class",
    )
    add_or_set_extra_dim(
        output_las,
        "pointconv_prob",
        predicted_prob,
        np.float32,
        "PointCONV prob",
    )
    add_or_set_extra_dim(
        output_las,
        "pointconv_votes",
        vote_count,
        np.uint16,
        "PointCONV votes",
    )

    output_las_path.parent.mkdir(parents=True, exist_ok=True)
    output_las.write(output_las_path)

    core_only_point_count = None
    if write_core_only_output:
        voted_mask = vote_count > 0
        core_only_las = laspy.LasData(copy.deepcopy(output_las.header))
        core_only_las.points = output_las.points[voted_mask].copy()
        core_only_las.write(core_only_las_path)
        core_only_point_count = int(np.count_nonzero(voted_mask))

    summary = {
        "source_name": source_record["source_name"],
        "source_stem": source_stem,
        "source_thinned_las": str(source_thinned_las.resolve()),
        "output_las": str(output_las_path.resolve()),
        "status": "merged",
        "point_count": int(point_count),
        "accepted_predictions": int(accepted_predictions),
        "points_with_predictions": int(np.count_nonzero(vote_count > 0)),
        "points_without_predictions": int(np.count_nonzero(vote_count == 0)),
        "core_only_output_las": (
            str(core_only_las_path.resolve()) if write_core_only_output else None
        ),
        "core_only_point_count": core_only_point_count,
        "dropped_no_vote_points": (
            int(np.count_nonzero(vote_count == 0)) if write_core_only_output else None
        ),
        "missing_tiles": int(missing_tiles),
        "rejected_noncore": int(rejected_noncore),
        "rejected_tolerance": int(rejected_tolerance),
        "predicted_class_histogram": class_histogram(predicted_class),
        "source_class_histogram": class_histogram(source_classification),
        "tile_summaries": tile_summaries,
    }
    save_json(output_root / f"{source_stem}_merge_summary.json", summary)
    return summary


def main() -> None:
    args = parse_args()
    manifest = load_json(args.manifest.resolve())
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    source_records = manifest.get("source_files", [])
    if not source_records:
        raise ValueError("Manifest does not contain any source_files entries")

    worker_count = max(1, int(args.workers))
    summaries: list[dict[str, Any]] = []

    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                merge_source,
                source,
                str(args.tf1_output_root.resolve()),
                str(output_dir),
                float(args.match_tolerance),
                int(args.default_class),
                bool(args.overwrite),
                bool(args.write_core_only_output),
            )
            for source in source_records
        ]
        for future in as_completed(futures):
            summaries.append(future.result())

    summaries.sort(key=lambda item: item["source_name"])
    run_summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "manifest": str(args.manifest.resolve()),
        "tf1_output_root": str(args.tf1_output_root.resolve()),
        "output_dir": str(output_dir),
        "match_tolerance": float(args.match_tolerance),
        "default_class": int(args.default_class),
        "write_core_only_output": bool(args.write_core_only_output),
        "source_count": len(summaries),
        "total_points": int(sum(item.get("point_count", 0) for item in summaries)),
        "total_points_with_predictions": int(
            sum(item.get("points_with_predictions", 0) for item in summaries)
        ),
        "total_points_without_predictions": int(
            sum(item.get("points_without_predictions", 0) for item in summaries)
        ),
        "sources": summaries,
    }
    save_json(output_dir / "merge_summary.json", run_summary)
    write_csv(
        output_dir / "merge_summary_sources.csv",
        [
            {
                key: value
                for key, value in item.items()
                if key not in {"tile_summaries", "predicted_class_histogram", "source_class_histogram"}
            }
            for item in summaries
        ],
    )
    print(f"Wrote {output_dir / 'merge_summary.json'}")

    # Fail-fast (2026-06-06): if NO source merged any predictions, the
    # combined outputs are unclassified — exit nonzero so the launcher /
    # orchestrator stops the chain at the cause instead of cascading
    # all-class-0 data through stages 1b/3/6/7/8.
    n_bad = sum(1 for s in summaries
                if s.get("status") == "no_tiles_in_manifest")
    n_voted = sum(int(s.get("points_with_predictions", 0)) for s in summaries)
    if summaries and n_voted == 0:
        print(f"[merge] FATAL: 0 points received predictions across "
              f"{len(summaries)} source(s) ({n_bad} had no tiles in the "
              f"manifest) — combined outputs would be unclassified",
              flush=True)
        import sys
        sys.exit(3)


if __name__ == "__main__":
    main()
