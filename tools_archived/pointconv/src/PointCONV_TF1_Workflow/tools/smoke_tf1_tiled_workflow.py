from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import laspy
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a synthetic smoke test for the TF1 tiled workflow.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("_tmp_test_workspace") / "tf1_tiled_smoke",
        help="Temporary workspace for synthetic inputs and outputs.",
    )
    return parser.parse_args()


def create_synthetic_las(path: Path, point_count: int = 5000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = laspy.LasHeader(point_format=7, version="1.4")
    header.scales = np.array([0.001, 0.001, 0.001])
    header.offsets = np.array([0.0, 0.0, 0.0])

    las = laspy.LasData(header)
    grid_width = int(np.ceil(np.sqrt(point_count)))
    values = np.arange(point_count, dtype=np.float64)
    las.x = (values % grid_width) * 0.11
    las.y = (values // grid_width) * 0.11
    las.z = np.sin(values * 0.01) * 0.1
    las.intensity = np.full(point_count, 1000, dtype=np.uint16)
    las.classification = np.zeros(point_count, dtype=np.uint8)
    las.write(path)


def run_command(command: list[str]) -> None:
    print(" ".join(command), flush=True)
    subprocess.run(command, check=True)


def create_fake_tf1_outputs(manifest_path: Path, tf1_output_root: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for tile in manifest["tiles"]:
        tile_id = tile["tile_id"]
        tile_las = laspy.read(tile["tile_las"])
        tile_las.classification = np.full(len(tile_las.points), 14, dtype=np.uint8)
        tile_las.intensity = np.full(len(tile_las.points), 65535, dtype=np.uint16)
        tile_output_dir = tf1_output_root / tile_id
        tile_output_dir.mkdir(parents=True, exist_ok=True)
        tile_las.write(tile_output_dir / f"{tile_id}_t_raw.las")


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    if root.exists():
        shutil.rmtree(root)

    source_path = root / "input" / "synthetic.las"
    preprocess_root = root / "preprocessed"
    tf1_output_root = root / "tf1_outputs"
    combined_root = root / "combined_outputs"

    create_synthetic_las(source_path)
    run_command(
        [
            sys.executable,
            "pre_processing/build_tf1_inference_tiles.py",
            "--input-dir",
            str(source_path.parent),
            "--output-root",
            str(preprocess_root),
            "--voxel-size",
            "0.1",
            "--target-tile-points",
            "1000",
            "--min-tile-points",
            "100",
            "--min-radius",
            "1",
            "--overlap",
            "1",
            "--workers",
            "1",
            "--overwrite",
        ]
    )

    manifest_path = preprocess_root / "manifests" / "tf1_tile_manifest.json"
    create_fake_tf1_outputs(manifest_path, tf1_output_root)
    run_command(
        [
            sys.executable,
            "post_processing/merge_tf1_tile_predictions.py",
            "--manifest",
            str(manifest_path),
            "--tf1-output-root",
            str(tf1_output_root),
            "--output-dir",
            str(combined_root),
            "--workers",
            "1",
            "--overwrite",
        ]
    )

    summary = json.loads((combined_root / "merge_summary.json").read_text(encoding="utf-8"))
    if summary["total_points_with_predictions"] <= 0:
        raise RuntimeError("Smoke merge produced no predicted points")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
