"""Compute "is the output reasonable" sanity counters per stage and write
them to <run_dir>/viz/output_sanity.json.

This is the analytical sibling of `viz/status.json` (which says "what
happened") and `viz/resources.json` (which says "what's the host doing").
Output sanity says "what do the actual deliverables look like."

Called by the dashboard watch loop on every render iteration. Cheap reads
only — no full-LAS-scan; LAS class histograms are sampled (every Nth
point) to keep wall time under a few seconds even for 1 GB files.

Output structure:
  {
    "computed_at": "...",
    "stage1": {
      "n_sources": 82,
      "total_classified_pts": 412_345_678,
      "sampled_pts": 2_061_728,           # sampling factor 200
      "class_histogram_sampled": {
        "0": ..., "2": ..., "5": ..., "6": ..., "14": ..., "18": ...
      },
      "class_histogram_estimated_full": {<same keys, scaled>},
      "class_distribution_pct": {...},
    },
    "stage2": {
      "n_pole_candidates": 2591,
      "n_crops_written": 5182,            # 2x because thinned variant
    },
    "stage3": {
      "n_poletops_dirs": 2590,
      "n_pole_bodies_estimated": ...,
    },
    "stage4": {
      "n_curblines": ...,
      "n_eop": ...,
      "total_curbline_meters": ...,
    }
  }
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(f"[output-sanity] {msg}", flush=True)


def sample_class_histogram(las_path: Path, every_nth: int = 200) -> dict:
    """Sample every Nth point's classification. Returns {class_id: count}."""
    try:
        import laspy
        las = laspy.read(las_path)
        import numpy as np
        cls = np.asarray(las.classification, dtype=np.uint8)
        sampled = cls[::every_nth]
        keys, counts = np.unique(sampled, return_counts=True)
        return {int(k): int(c) for k, c in zip(keys, counts)}, int(len(cls)), int(len(sampled))
    except Exception as e:
        return {"_error": str(e)}, 0, 0


def stage1_sanity(run_dir: Path) -> dict:
    s1_dir = run_dir / "01_pointconv" / "combined_outputs"
    out: dict = {}
    if not s1_dir.is_dir():
        return out
    files = sorted(s1_dir.glob("*_combined_0p1m.las"))
    out["n_sources"] = len(files)
    out["files_sampled"] = 0
    if not files:
        return out
    # Sample first 3 files to keep wall time bounded on big runs.
    sampled_files = files[:3]
    out["files_sampled"] = len(sampled_files)
    combined_hist: dict[int, int] = {}
    total_pts = 0
    sampled_pts = 0
    for f in sampled_files:
        hist, n, n_sampled = sample_class_histogram(f, every_nth=200)
        if "_error" in hist:
            out["sample_error"] = hist["_error"]
            continue
        total_pts += n
        sampled_pts += n_sampled
        for k, v in hist.items():
            combined_hist[k] = combined_hist.get(k, 0) + v
    out["total_classified_pts_first3"] = total_pts
    out["sampled_pts_first3"] = sampled_pts
    out["class_histogram_sampled_first3"] = {str(k): v for k, v in
                                             sorted(combined_hist.items())}
    total_sampled = sum(combined_hist.values())
    if total_sampled > 0:
        out["class_distribution_pct"] = {
            str(k): round(100 * v / total_sampled, 3)
            for k, v in sorted(combined_hist.items())
        }
    # Estimate FULL-corpus point counts by scaling first-3-files to all-files
    if total_pts > 0 and len(sampled_files) > 0:
        avg_pts_per_file = total_pts / len(sampled_files)
        out["estimated_total_pts_all_files"] = int(avg_pts_per_file * len(files))
    return out


def count_shapefile_records(shp_path: Path) -> int:
    """Cheap record count via DBF header (no geopandas)."""
    try:
        dbf = shp_path.with_suffix(".dbf")
        if not dbf.exists():
            return 0
        with dbf.open("rb") as f:
            f.seek(4)
            return int.from_bytes(f.read(4), "little")
    except Exception:
        return 0


def shapefile_total_length(shp_path: Path) -> float | None:
    """Sum of line/polyline geometry lengths in shapefile CRS units."""
    if not shp_path.is_file():
        return None
    try:
        import geopandas as gpd
        gdf = gpd.read_file(shp_path)
        if gdf.empty:
            return 0.0
        return float(gdf.geometry.length.sum())
    except Exception:
        return None


def stage2_sanity(run_dir: Path) -> dict:
    s2 = run_dir / "02_pole_crop"
    out: dict = {}
    candidates = s2 / "poles_candidates_loose.shp"
    if candidates.exists():
        out["n_pole_candidates"] = count_shapefile_records(candidates)
    crops_dir = s2 / "output" / "crops"
    if crops_dir.is_dir():
        # Both naming variants: P_NNN (CSV deliveries) + Pole_NN (SHP).
        out["n_crop_las_files"] = sum(
            1 for p in crops_dir.glob("*.la[sz]")
            if not p.stem.endswith("_thinned"))
    return out


def stage3_sanity(run_dir: Path) -> dict:
    out: dict = {}
    polevec_tops = run_dir / "PoleVec" / "EstimatePoleTops"
    if polevec_tops.is_dir():
        n_dirs = 0
        n_done = 0  # has peak_lines.gpkg
        for p in polevec_tops.iterdir():
            if not p.is_dir():
                continue
            n_dirs += 1
            if (p / "peak_lines.gpkg").exists():
                n_done += 1
        out["n_poletops_dirs"] = n_dirs
        out["n_poletops_with_peak_lines"] = n_done
        out["n_poletops_running_or_failed"] = n_dirs - n_done
    # Canonical full-mode location first (PoleVec/Combined/Grp0_*), then the
    # body-only legacy layout (03_pole_vec_body/).
    body_lines = (list((run_dir / "PoleVec" / "Combined")
                       .glob("Grp0_Body_Lines.shp"))
                  + list((run_dir / "03_pole_vec_body")
                         .rglob("Body_Lines.shp")))
    if body_lines:
        out["body_lines_shp"] = str(body_lines[0])
        out["n_body_lines"] = count_shapefile_records(body_lines[0])
    # Also check pole-vec combined outputs for wires/crossarms/transformers
    for name, full_glob in (("Wires", "Grp0_*_Wires.shp"),
                            ("Crossarms", "Grp0_Crossarm_Lines.shp"),
                            ("Transformers", "Grp0_Transformer_Lines.shp")):
        hits = (list((run_dir / "PoleVec" / "Combined").glob(full_glob))
                + list((run_dir / "03_pole_vec_body").rglob(f"{name}.shp")))
        if hits:
            out[f"n_{name.lower()}"] = sum(
                count_shapefile_records(p) for p in hits)
            break
    # Stage 3.5 wire-attachment manifest (produced by
    # filter_poles_by_wire_attachment.py when stage3_pole_vec.validation
    # is enabled). Surfaces orphan counts + the threshold used so the
    # dashboard can show e.g. "152/213 wire-attached (61 orphans)".
    wire_manifest = run_dir / "03_pole_vec_body" / "wire_attachment.json"
    if wire_manifest.exists():
        try:
            import json as _json
            m = _json.loads(wire_manifest.read_text(encoding="utf-8"))
            summary = m.get("summary", {}) or {}
            out["wire_validation_enabled"] = True
            out["n_wire_attached"] = int(summary.get("n_wire_attached", 0))
            out["n_wire_orphaned"] = int(summary.get("n_wire_orphaned", 0))
            out["wire_validation_params"] = m.get("params", {})
        except Exception:
            # Manifest exists but unreadable — record the failure
            # rather than silently dropping it.
            out["wire_validation_enabled"] = True
            out["wire_validation_error"] = "manifest unreadable"
    return out


def curb_skill_progress(run_dir: Path) -> dict:
    """Detect progress through curb-skill's internal sub-stages so the
    dashboard can show a Stage 4 checklist. Returns:
      {
        "found": bool,                   # does artifacts/ exist?
        "artifacts_root": str,
        "items": [
          {"key": "...", "label": "...", "state": "done"|"running"|"pending",
           "detail": "..."},
          ...
        ],
      }
    """
    s4 = run_dir / "04_curbs" / "artifacts"
    if not s4.is_dir():
        return {"found": False, "items": []}

    # Curb-skill writes either directly into artifacts/ OR into
    # artifacts/runs/<name>/ depending on layout. Detect.
    candidates = [s4] + list(s4.glob("runs/*"))
    root = next(
        (c for c in candidates
         if (c / "02_ground").is_dir() or (c / "01_corridor").is_dir()
            or (c / "dataset_manifest.json").exists()),
        s4,
    )

    def _exists(*parts: str) -> Path | None:
        for base in [root, root / "02_ground"]:
            p = base.joinpath(*parts)
            if p.exists():
                return p
        return None

    def _glob_count(rel: list[str], pattern: str) -> int:
        for base in [root, root / "02_ground"]:
            d = base.joinpath(*rel)
            if d.is_dir():
                return sum(1 for _ in d.glob(pattern))
        return 0

    items: list[dict] = []

    # init-project / make-splits artifacts
    dataset_manifest = root / "dataset_manifest.json"
    if not dataset_manifest.exists():
        dataset_manifest = s4 / "dataset_manifest.json"
    split_json = root / "split.json"
    if not split_json.exists():
        split_json = s4 / "split.json"
    items.append({
        "key": "init_project",
        "label": "init-project",
        "state": "done" if dataset_manifest.exists() else "pending",
        "detail": f"manifest: {dataset_manifest.name}" if dataset_manifest.exists() else "no manifest",
    })
    items.append({
        "key": "make_splits",
        "label": "make-splits",
        "state": "done" if split_json.exists() else "pending",
        "detail": f"split: {split_json.name}" if split_json.exists() else "no split",
    })

    # stage2 sub-pipeline: tile crop -> corridor crop -> per-run thin -> ground
    n_tiles = _glob_count(["01_corridor", "tiles"], "*.las") + \
              _glob_count(["01_corridor", "tiles"], "*.laz")
    items.append({
        "key": "cs2_tile_crop",
        "label": "curb stage 2 · tile crop",
        "state": "done" if n_tiles > 0 else "pending",
        "detail": f"{n_tiles} tiles",
    })

    # Corridor crop is only "done" once the downstream per-run-thinned
    # file exists. A .laz file in per_run/ may be in mid-write — laspy
    # keeps the file open and grows it incrementally, so file-exists is
    # a false-positive for the corridor-crop step. Use the next-stage
    # output as a tombstone instead.
    n_per_run = _glob_count(["01_corridor", "per_run"], "Run_*_corridor.la?")
    n_thin = _glob_count(["01_corridor", "per_run_thinned"], "Run_*_thin.la?")
    corridor_size_bytes = 0
    for base in [root, root / "02_ground"]:
        per_run_dir = base / "01_corridor" / "per_run"
        if per_run_dir.is_dir():
            for p in per_run_dir.glob("Run_*_corridor.la?"):
                corridor_size_bytes += p.stat().st_size
            break
    corridor_done = n_thin > 0  # any thinned output ⇒ corridor crop finished
    corridor_running = n_per_run > 0 and not corridor_done
    items.append({
        "key": "cs2_corridor_crop",
        "label": "curb stage 2 · corridor crop (per run)",
        "state": "done" if corridor_done
                  else "running" if corridor_running
                  else "pending",
        "detail": (f"{n_per_run} runs"
                   + (f" · {corridor_size_bytes / 1e9:.1f} GB so far"
                      if corridor_running else "")),
    })

    items.append({
        "key": "cs2_per_run_thin",
        "label": "curb stage 2 · per-run thin",
        "state": "done" if n_thin > 0 else "pending",
        "detail": f"{n_thin} thinned",
    })

    # Ground / HAG is one of:
    #   <root>/02_ground/ground_corridor.tif       (single-file layout)
    #   <root>/02_ground/per_run/Run_XX_ground.tif (per-run layout)
    ground_tif = _exists("02_ground", "ground_corridor.tif")
    n_ground_per_run = 0
    if not ground_tif:
        for base in [root, root / "02_ground"]:
            per_run = base / "02_ground" / "per_run"
            if per_run.is_dir():
                n_ground_per_run = sum(1 for _ in per_run.glob("Run_*_ground.tif"))
                if n_ground_per_run:
                    break
    items.append({
        "key": "cs2_ground",
        "label": "curb stage 2 · ground / HAG",
        "state": ("done" if (ground_tif or n_ground_per_run > 0)
                  else "pending"),
        "detail": ("ground_corridor.tif" if ground_tif
                   else f"{n_ground_per_run} per-run TIFs" if n_ground_per_run
                   else "—"),
    })

    stage2_summary = _exists("stage2_summary.json")
    items.append({
        "key": "cs2_summary",
        "label": "curb stage 2 · summary",
        "state": "done" if stage2_summary else "pending",
        "detail": stage2_summary.name if stage2_summary else "—",
    })

    # stage3 features (skipped in inference-only mode; truth_layers == []
    # in the manifest means no curbline truth → stage5 self-materializes).
    feat_dir = _exists("03_features")
    n_shards = 0
    if feat_dir:
        n_shards = sum(1 for _ in feat_dir.glob("*.parquet")) + \
                   sum(1 for _ in feat_dir.glob("*.npz"))
    inference_only = False
    try:
        import json as _json
        dm = root / "dataset_manifest.json"
        if not dm.exists():
            dm = s4 / "dataset_manifest.json"
        if dm.exists():
            mf = _json.loads(dm.read_text(encoding="utf-8"))
            inference_only = not mf.get("truth_layers")
    except Exception:
        pass
    if n_shards > 0:
        state = "done"
        detail = f"{n_shards} shards"
    elif inference_only:
        state = "done"  # not required for inference-only
        detail = "skipped (inference-only)"
    else:
        state = "pending"
        detail = "—"
    items.append({
        "key": "cs3_features",
        "label": "curb stage 3 · feature shards",
        "state": state,
        "detail": detail,
    })

    # stage5 inference
    inf_manifest = _exists("05_inference", "inference_manifest.json")
    items.append({
        "key": "cs5_inference",
        "label": "curb stage 5 · model inference",
        "state": "done" if inf_manifest else "pending",
        "detail": "inference_manifest.json" if inf_manifest else "—",
    })

    # stage6d decode
    vector_manifest = _exists("06d_ransac_curve", "vector_manifest.json")
    items.append({
        "key": "cs6d_decode",
        "label": "curb stage 6d · RANSAC decode",
        "state": "done" if vector_manifest else "pending",
        "detail": "vector_manifest.json" if vector_manifest else "—",
    })

    # Final shapefile deliverables. The orchestrator writes them in two
    # places depending on which step of the pipeline produces them:
    #   - stage 6d native:  artifacts/02_ground/06d_ransac_curve/{CURBLINE,EDGE_OF_PAVEMENT}.shp
    #   - manual copy:      04_curbs/viz/{curblines,eop}.shp
    # The native location always exists after stage 6d; the copy is a
    # convenience for QGIS/dashboard linking and may or may not happen.
    curb_candidates = [
        run_dir / "04_curbs" / "viz" / "curblines.shp",
        run_dir / "04_curbs" / "artifacts" / "02_ground"
                  / "06d_ransac_curve" / "CURBLINE.shp",
    ]
    eop_candidates = [
        run_dir / "04_curbs" / "viz" / "eop.shp",
        run_dir / "04_curbs" / "artifacts" / "02_ground"
                  / "06d_ransac_curve" / "EDGE_OF_PAVEMENT.shp",
    ]
    curblines_path = next((p for p in curb_candidates if p.exists()), None)
    eop_path = next((p for p in eop_candidates if p.exists()), None)
    state = "done" if (curblines_path and eop_path) else "pending"
    detail_bits = []
    if curblines_path:
        detail_bits.append(curblines_path.name)
    if eop_path:
        detail_bits.append(eop_path.name)
    items.append({
        "key": "curblines_shp",
        "label": "curblines.shp + EOP",
        "state": state,
        "detail": " + ".join(detail_bits) if detail_bits else "—",
    })

    # Promote the first pending step after the last done step to "running"
    # — curb-skill is single-threaded sequential, so if step N is done and
    # step N+1 is pending and step N+2 is pending, step N+1 is what the
    # process is actively working on (or where it failed and stopped).
    last_done_idx = -1
    for i, it in enumerate(items):
        if it["state"] == "done":
            last_done_idx = i
    if 0 <= last_done_idx < len(items) - 1:
        next_idx = last_done_idx + 1
        if items[next_idx]["state"] == "pending":
            items[next_idx]["state"] = "running"

    n_done = sum(1 for i in items if i["state"] == "done")
    n_running = sum(1 for i in items if i["state"] == "running")
    return {
        "found": True,
        "artifacts_root": str(root),
        "n_done": n_done,
        "n_running": n_running,
        "n_total": len(items),
        "items": items,
    }


def per_pole_status(run_dir: Path) -> dict:
    """Per-pole grid for the dashboard (G4). Returns:
       {
         "poles": [{"id": "P_000", "state": "done"|"running"|"pending"|"failed"}, ...],
         "summary": {"done": N, "running": N, "pending": N, "total": N},
       }
    Ordered by the candidates shapefile's row order (= P_000, P_001, ...).
    Done state = peak_lines.gpkg exists. Running = dir exists but no output.
    Pending = no dir for this pole_id."""
    out: dict = {"poles": [], "summary": {}}
    candidates = run_dir / "02_pole_crop" / "poles_candidates_loose.shp"
    if not candidates.exists():
        return out
    total = count_shapefile_records(candidates)
    if total == 0:
        return out
    def _pole_id(stem: str) -> str:
        return stem[: -len("_thinned")] if stem.endswith("_thinned") else stem

    polevec_tops = run_dir / "PoleVec" / "EstimatePoleTops"
    pcseg = run_dir / "PoleVec" / "PCseg"
    existing = set()
    done = set()
    if polevec_tops.is_dir():
        for p in polevec_tops.iterdir():
            if not p.is_dir():
                continue
            pole_id = _pole_id(p.name)
            existing.add(pole_id)
            if (p / "peak_lines.gpkg").exists():
                done.add(pole_id)
    # csv-seeded runs (Path C) skip estimate_pole_tops entirely, so
    # EstimatePoleTops/ never exists and the grid read all-pending even on
    # a finished full-mode chain (LaVerne 2026-06-11). Track those runs via
    # the per-pole PCseg working dirs: dir => running, body lines => done.
    if pcseg.is_dir():
        for p in pcseg.iterdir():
            if not p.is_dir():
                continue
            pole_id = _pole_id(p.name)
            existing.add(pole_id)
            if any(p.glob("*_Body_Lines.*")):
                done.add(pole_id)
    # Pole IDs: prefer the actual crop stems — SHP deliveries carry native
    # IDs (Pole_20), not the synthesized P_NNN, and stage 3 only processes
    # poles that HAVE crops (16 of LaVerne's 252 candidates are in-extent;
    # a 252-cell mostly-grey grid was misleading). Fall back to the legacy
    # P_{i:03d} synthesis over the candidates count when no crops exist yet.
    crops_dir = run_dir / "02_pole_crop" / "output" / "crops_metric"
    if not crops_dir.is_dir():
        crops_dir = run_dir / "02_pole_crop" / "output" / "crops"
    ids = []
    if crops_dir.is_dir():
        ids = sorted({_pole_id(p.stem)
                      for p in crops_dir.glob("*.la[sz]")})
    if not ids:
        # Pole-cropping convention: always 3-digit zero-pad below 1000,
        # natural digits above. f"P_{i:03d}" handles both correctly.
        ids = [f"P_{i:03d}" for i in range(total)]
    poles = []
    n_done = n_running = n_pending = 0
    for pole_id in ids:
        if pole_id in done:
            state = "done"
            n_done += 1
        elif pole_id in existing:
            state = "running"
            n_running += 1
        else:
            state = "pending"
            n_pending += 1
        poles.append({"id": pole_id, "state": state})
    out["poles"] = poles
    out["summary"] = {"done": n_done, "running": n_running,
                       "pending": n_pending, "total": len(ids)}
    return out


def stage4_sanity(run_dir: Path) -> dict:
    """Find curblines + EOP shapefile counts + lengths.

    Checks both the convenience-copy path (04_curbs/viz/{curblines,eop}.shp)
    and the native stage 6d output path
    (04_curbs/artifacts/02_ground/06d_ransac_curve/{CURBLINE,EDGE_OF_PAVEMENT}.shp).
    """
    out: dict = {}
    candidates = {
        "curblines": [
            run_dir / "04_curbs" / "viz" / "curblines.shp",
            run_dir / "04_curbs" / "artifacts" / "02_ground"
                      / "06d_ransac_curve" / "CURBLINE.shp",
        ],
        "eop": [
            run_dir / "04_curbs" / "viz" / "eop.shp",
            run_dir / "04_curbs" / "artifacts" / "02_ground"
                      / "06d_ransac_curve" / "EDGE_OF_PAVEMENT.shp",
        ],
    }
    for key, paths in candidates.items():
        for p in paths:
            if p.exists():
                out[f"n_{key}" if key == "eop" else "n_curblines"] = \
                    count_shapefile_records(p)
                total_len = shapefile_total_length(p)
                if total_len is not None:
                    out[f"total_{key}_units" if key == "eop"
                        else "total_curbline_units"] = round(total_len, 1)
                break
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    if not args.run_dir.is_dir():
        raise SystemExit(f"Run dir not found: {args.run_dir}")
    out_path = args.out or args.run_dir / "viz" / "output_sanity.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sanity = {
        "computed_at": dt.datetime.now().isoformat(timespec="seconds"),
        "stage1": stage1_sanity(args.run_dir),
        "stage2": stage2_sanity(args.run_dir),
        "stage3": stage3_sanity(args.run_dir),
        "stage4": stage4_sanity(args.run_dir),
        "per_pole_status": per_pole_status(args.run_dir),
        "curb_skill_progress": curb_skill_progress(args.run_dir),
    }
    out_path.write_text(json.dumps(sanity, indent=2), encoding="utf-8")
    log(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
