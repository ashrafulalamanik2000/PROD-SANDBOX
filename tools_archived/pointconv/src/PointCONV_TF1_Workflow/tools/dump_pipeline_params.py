"""Dump the effective + planned parameters of a pipeline run to a JSON file.

The chain has parameters scattered across:
  - Stage 1 PointCONV: tf1_tile_manifest.json (effective, after run started)
  - Stage 2 pole-cropping: pole-cropping CLI args + loose-bridge defaults
  - Stage 3 pole-vec body-only: PoleVec_control_body_only_pointconv.yml +
                                 inputconfig_FIRMATEK.yml
  - Stage 4 curb-skill: stage configs + the model NPZ's train_summary.json

This script consolidates all of them into one viz/pipeline_params.json that
the live dashboard reads. Where a stage hasn't started yet, the planned
defaults are written (marked with "source": "planned-default").

Usage:
    python dump_pipeline_params.py <RUN_DIR>
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


# Source of truth for Stage 2-4 "planned" parameters when those stages
# haven't run yet. Updated as we learn the right values for Mississauga-scale.
PLANNED = {
    "stage1b_fine_classification": {
        "source": "planned-default",
        "script": "projects/PointCONV/PointCONV_TF1_Workflow/tools/backproject_classification_to_fine.py",
        "enabled_default": False,
        "voxel_size_m": 0.025,
        "match_radius_m": 0.5,
        "notes": "Opt-in 0.025 m back-projection of 0.1 m predictions. "
                 "Required for full pole-vec (wires/crossarms/xfmrs). "
                 "~2 min/source on CPU, ~2 GB extra disk per source.",
    },
    "stage2_loose_bridge_extract": {
        "source": "planned-default",
        "script": "projects/pole-vectorization/scripts/reextract_poles_loose_bridge_multi.py",
        "dbscan_eps_m": 2.0,
        "dbscan_min_samples": 10,
        "min_pts": 15,
        "min_z_range_m": 1.5,
        "epsg": 26917,
        "notes": "Loose threshold per FINDING_dbscan_bridge_drops_poles. "
                 "Recovers ~3 missed poles on Oakville (no false positives).",
    },
    "stage2_pole_cropping": {
        "source": "planned-default",
        "script": "projects/pole-cropping/croping_around_poles/pipeline.py",
        "data_srs": "EPSG:26917",
        "half_size_m": 30.0,
        "search_radius_m": 5.0,
        "voxel_size_m": 0.025,
        "min_pole_height_m": 9.0,
        "max_pole_height_m": 100.0,
        "max_workers": 16,
        "corridors": True,
    },
    "stage3_pole_vec_body_only": {
        "source": "planned-default",
        "control_yml": "projects/pole-vectorization/PoleVec_Standalone/PoleVec_control_body_only_pointconv.yml",
        "input_yml": "projects/pole-vectorization/PoleVec_Standalone/inputconfig_FIRMATEK.yml",
        "stop_after_pole_body_estimation": True,
        "process_transformer": False,
        "process_wires": False,
        "process_crossarms": False,
        "filter_wires": False,
        "convert_results_to_feet": False,
        "docker_image": "750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:latest",
    },
    "stage4_curb_skill": {
        "source": "planned-default",
        "model_npz": "G:/runs/oakville_run55/20260519_102706/04_curbs/artifacts/runs/oakville_run55/04_training/curb_baseline_logreg.npz",
        "model_source_run": "oakville_run55 (S1+S2+S3 improvements; F=0.710)",
        "init_project_facade": True,
        "facade_script": "projects/curb-segmentation/scripts/build_project_facade.py",
        "make_splits_chunk_length_m": 75.0,
        "stages_to_run": ["init-project", "make-splits", "stage2-run",
                          "stage3-run", "stage5-run", "stage6d-run"],
        "stages_skipped": ["stage4-run (no truth to train on)",
                           "stage7-run (no truth to evaluate against)"],
        "stage6d": {
            "high_prob_threshold": 0.55,
            "window_length_m": 24.0,
            "min_support_points": 20,
        },
    },
}


def _load_json(p: Path) -> Any:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_parse_error": str(e)}


def _stage1_params(run_dir: Path) -> dict:
    manifest = _load_json(run_dir / "01_pointconv" / "manifests" / "tf1_tile_manifest.json")
    if not manifest:
        return {"source": "planned-default", "status": "manifest-not-yet-written"}
    params = dict(manifest.get("parameters", {}))
    return {
        "source": "effective (from manifest)",
        "model": "PointCONV_model_6class_Mobile_v0.0.13_al1",
        "input_dir": manifest.get("input_dir"),
        "output_root": manifest.get("output_root"),
        "source_file_count": manifest.get("source_file_count"),
        "tile_count": manifest.get("tile_count"),
        "voxel_size_m": params.get("voxel_size"),
        "target_tile_points": params.get("target_tile_points"),
        "min_tile_points": params.get("min_tile_points"),
        "min_radius_m": params.get("min_radius"),
        "overlap_m": params.get("overlap"),
        "chunk_size": params.get("chunk_size"),
        "max_concurrent_tiles": params.get("max_concurrent_tiles"),
        "workers": params.get("workers"),
        "builder": params.get("builder"),
        "tile_layout": params.get("tile_layout"),
        "created_at": manifest.get("created_at"),
    }


def _stage1b_params(run_dir: Path) -> dict:
    fine_dir = run_dir / "01_pointconv" / "combined_outputs_0p025m"
    if fine_dir.exists() and any(fine_dir.glob("*.las")):
        return {**PLANNED["stage1b_fine_classification"],
                "source": "effective (fine outputs exist)",
                "fine_output_dir": str(fine_dir),
                "fine_source_count": sum(1 for _ in fine_dir.glob("*.las"))}
    return PLANNED["stage1b_fine_classification"]


def _stage2_loose_params(run_dir: Path) -> dict:
    out_shp = run_dir / "02_pole_crop" / "poles_candidates_loose.shp"
    if out_shp.exists():
        return {**PLANNED["stage2_loose_bridge_extract"],
                "source": "effective (output exists)",
                "output_shp": str(out_shp)}
    return PLANNED["stage2_loose_bridge_extract"]


def _stage2_cropping_params(run_dir: Path) -> dict:
    out_dir = run_dir / "02_pole_crop" / "output"
    if out_dir.exists():
        return {**PLANNED["stage2_pole_cropping"],
                "source": "effective (output exists)",
                "output_dir": str(out_dir)}
    return PLANNED["stage2_pole_cropping"]


def _stage3_params(run_dir: Path) -> dict:
    out_dir = run_dir / "03_pole_vec_body"
    if out_dir.exists() and any(out_dir.rglob("*Body_Lines*")):
        return {**PLANNED["stage3_pole_vec_body_only"],
                "source": "effective (output exists)",
                "output_dir": str(out_dir)}
    return PLANNED["stage3_pole_vec_body_only"]


def _stage4_params(run_dir: Path) -> dict:
    out_dir = run_dir / "04_curbs"
    if (out_dir / "viz" / "curblines.shp").exists():
        return {**PLANNED["stage4_curb_skill"],
                "source": "effective (output exists)",
                "output_dir": str(out_dir)}
    return PLANNED["stage4_curb_skill"]


def _stage_states(run_dir: Path) -> dict:
    """Compute per-stage completion booleans for the progress bar."""
    pc_dir = run_dir / "01_pointconv"
    manifest = _load_json(pc_dir / "manifests" / "tf1_tile_manifest.json")
    s1_total = (manifest or {}).get("tile_count", 0)
    s1_done = 0
    if s1_total:
        tf1 = pc_dir / "tf1_outputs"
        if tf1.exists():
            for sub in tf1.iterdir():
                if sub.is_dir() and (sub / f"{sub.name}_v_seg_out.las").exists():
                    s1_done += 1
    combined = pc_dir / "combined_outputs"
    s1_merged = 0
    if combined.exists():
        s1_merged = sum(1 for _ in combined.glob("*_tf1_pointconv_combined_0p1m.las"))
    n_sources = (manifest or {}).get("source_file_count", 0)
    s1_state = ("done" if (s1_total and s1_merged == n_sources)
                else ("running" if s1_done < s1_total or s1_merged < n_sources else "running"))
    if s1_total == 0:
        s1_state = "pending"

    s2_shp = run_dir / "02_pole_crop" / "poles_candidates_loose.shp"
    s2_out = run_dir / "02_pole_crop" / "output"
    s2_state = "done" if s2_out.exists() and any(s2_out.rglob("*.shp")) else (
        "running" if s2_shp.exists() else "pending")

    s3_out = run_dir / "03_pole_vec_body"
    s3_state = "done" if s3_out.exists() and any(s3_out.rglob("*Body_Lines*")) else "pending"

    s4_curbs = run_dir / "04_curbs" / "viz" / "curblines.shp"
    s4_state = "done" if s4_curbs.exists() else "pending"

    return {
        "stage1": {"state": s1_state, "tiles_done": s1_done, "tiles_total": s1_total,
                   "sources_merged": s1_merged, "sources_total": n_sources},
        "stage2": {"state": s2_state},
        "stage3": {"state": s3_state},
        "stage4": {"state": s4_state},
    }


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--out", type=Path, default=None,
                   help="Output JSON path (default <run_dir>/viz/pipeline_params.json)")
    args = p.parse_args()
    out = args.out or args.run_dir / "viz" / "pipeline_params.json"

    payload = {
        "run_dir": str(args.run_dir),
        "stage_states": _stage_states(args.run_dir),
        "parameters": {
            "stage1_pointconv": _stage1_params(args.run_dir),
            "stage1b_fine_classification": _stage1b_params(args.run_dir),
            "stage2_loose_bridge_extract": _stage2_loose_params(args.run_dir),
            "stage2_pole_cropping": _stage2_cropping_params(args.run_dir),
            "stage3_pole_vec_body_only": _stage3_params(args.run_dir),
            "stage4_curb_skill": _stage4_params(args.run_dir),
        },
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
