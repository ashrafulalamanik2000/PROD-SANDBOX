"""Render a live workflow-progress map for a chained pipeline run.

Reads the on-disk state of a run directory and produces a single PNG that
shows where the pipeline is. Overlays:
  - Trajectory polyline (from 00_inputs/trajectory.csv).
  - All Stage-1 inference tiles colored by status:
        pending     gray
        inferring   amber  (tile dir exists but no _v_seg_out.las)
        done        green  (tile inference complete)
  - Per-source merged-output indicator (thick green border on the source
    bounding box) when combined_outputs/<stem>_tf1_*.las exists.
  - Stage-2 pole candidates (red dots) once
    02_pole_crop/poles_candidates_loose.shp exists.
  - Stage-3 pole bodies (purple lines) once 03_pole_vec_body output
    appears.
  - Stage-4 curblines (blue) and EOP (orange) once stage6d output appears.

Header text shows counters per stage and total elapsed time since
preprocessing started.

Usage:
    python render_pipeline_progress.py <RUN_DIR> [--once|--watch INTERVAL_S]

    With --watch, regenerates every INTERVAL_S seconds. With --once,
    renders a single PNG and exits.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import subprocess

# Hide cmd/PowerShell pop-up windows when this watch-loop spawns
# helper subprocesses every 60 s (compute_output_sanity, poll_resources,
# dump_pipeline_params). Windows-only flag; no-op elsewhere.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np


COLOR_PENDING = "#cccccc"
COLOR_INFERRING = "#ffb000"
COLOR_DONE = "#2e9b3a"
COLOR_MERGED_BORDER = "#0d5c1a"
COLOR_TRAJECTORY = "#444444"
COLOR_POLE_CANDIDATE = "#d8221f"
COLOR_POLE_BODY = "#8b3aa6"
# Stage 3.5 wire-validation: candidates that got a body but failed
# the wire-attachment check (PointCONV class-14 within radius/z-band).
# Distinct hue so testers can spot orphan poles at a glance.
COLOR_POLE_NO_WIRE = "#e8a017"
COLOR_CURBLINE = "#1f6fd8"
COLOR_EOP = "#e07a18"

# ---------------------------------------------------------------------------
# Canonical stage labels — the ONE source of truth for every stage-name
# string this dashboard renders (pills, status banner, map titles, output
# gallery, deliverables index). Titles mirror STAGE_SPECS in
# chain_orchestrator.py; punctuation is uniform: 'Stage <id> — <Title>'.
# Covers all stages of the four production workflows:
#   Firmatek full        : 0a 0b 2 0c 1 3.5 1b 3 6 7 8
#   Firmatek Validation  : 0a 0b 2
#   Aecon Corridor full  : 0t 0u 1 1b 2 3 4 5 6
#   Aecon Data Validation             : 0t 0u
STAGE_LABELS = {
    "stage0t_trajectory":          "Stage 0t — Trajectory ingestion",
    "stage0u_corridor_crop":       "Stage 0u — Trajectory-corridor crop",
    "stage0a_pole_csv":            "Stage 0a — Pole-tops ingestion",
    "stage0b_span_stats":          "Stage 0b — Span statistics",
    "stage0_reproject":            "Stage 0 — Reproject",
    "stage0c_reproject_crops":     "Stage 0c — Reproject crops to metric",
    "stage1_pointconv":            "Stage 1 — PointCONV inference",
    "stage1a_wire_pole":           "Stage 1a — Wire/Pole cleanup",
    "stage1b_fine_classification": "Stage 1b — Fine classification",
    "stage2_pole_crop":            "Stage 2 — Pole cropping & detection",
    "stage3_pole_vec":             "Stage 3 — Pole vectorization",
    "stage3_5_pole_network":       "Stage 3.5 — Pole-network topology",
    "stage4_curbs":                "Stage 4 — Curb & EOP vectorization",
    "stage4w_building_walls":      "Stage 4w — Building walls",
    "stage4t_tree_trunk_canopy":   "Stage 4t — Tree trunk & canopy",
    "stage5_road_surface":         "Stage 5 — Road surface",
    "stage4s_sidewalks":           "Stage 4s — Sidewalks",
    "stage4sg_sidewalks_geom":     "Stage 4sg — Sidewalks (geometric)",
    "stage6_final_classification": "Stage 6 — Final classification",
    "stage7_corridor_merge":       "Stage 7 — Corridor merge",
    "stage8_deliverables_to_ftus": "Stage 8 — Deliverables to ftUS",
    "stage9_solv3d_project":       "Stage 9 — SOLV3D viewer project",
}


def _chain_signals(run_dir: Path) -> tuple[list, str, str]:
    """Read chain.yml → (stage_order, stage0a csv_in, inputs.source_lst).

    Returns ([], "", "") when chain.yml is absent or still being written
    (launch-window race — see the 2026-05-27 note in _detect_workflow)."""
    so, csv_in, src_lst = [], "", ""
    try:
        cy = run_dir / "chain.yml"
        if cy.exists():
            import yaml as _yaml
            cfg = _yaml.safe_load(cy.read_text(encoding="utf-8")) or {}
            so = cfg.get("stage_order") or []
            csv_in = (cfg.get("stage0a_pole_csv") or {}).get("csv_in") or ""
            src_lst = (cfg.get("inputs") or {}).get("source_lst") or ""
    except Exception:
        pass
    return so, csv_in, src_lst


def _detect_workflow(run_dir: Path, params: dict | None = None) -> str:
    """Classify a run dir into one of the four production workflows.

    Returns 'Firmatek', 'Firmatek Validation', 'Aecon Corridor',
    'Aecon Data Validation', or 'Unknown'. Single source of truth for the HTML
    header, the matplotlib title, and the done-state banner.

    Signals, in priority order:
      1. chain.yml stage_order (falls back to params['stage_order'] and
         then viz/status.json stage names when chain.yml is mid-write).
      2. Positive-only side signals (stage0a csv_in / inputs.source_lst)
         — 2026-05-27: never DEFAULT to a workflow on an empty/partial
         stage_order; the launch-window race used to flash 'Aecon' for
         Firmatek runs. With no positive signal we stay 'Unknown'.

    Rules:
      trajectory stages (0t/0u) present, nothing else → 'Aecon Data Validation'
      trajectory stages + more (9-stage order)        → 'Aecon Corridor'
      0a present + stage 7/8                          → 'Firmatek'
      0a present + only 3 stages (0a/0b/2)            → 'Firmatek Validation'
    """
    so, csv_in, src_lst = _chain_signals(run_dir)
    if not so and params:
        so = params.get("stage_order") or []
    if not so:
        try:
            st = json.loads((run_dir / "viz" / "status.json")
                            .read_text(encoding="utf-8"))
            so = [s.get("name") for s in st.get("stages", []) if s.get("name")]
        except Exception:
            so = []
    s = set(so)
    traj = {"stage0t_trajectory", "stage0u_corridor_crop"}
    if s & traj:
        return "Aecon Data Validation" if s <= traj else "Aecon Corridor"
    if "stage0a_pole_csv" in s or csv_in:
        if {"stage7_corridor_merge", "stage8_deliverables_to_ftus"} & s:
            return "Firmatek"
        if s and len(s) <= 3:
            return "Firmatek Validation"
        return "Firmatek"
    if src_lst or s:
        # Legacy Aecon chain (no trajectory stages, no 0a) — e.g. the
        # original .lst-driven corridor profile.
        return "Aecon Corridor"
    return "Unknown"


def _load_manifest(run_dir: Path) -> dict | None:
    """Return the tile manifest, or None if Stage 1's tile builder
    hasn't written it yet (early-render race during chain startup).
    Callers must handle None gracefully — the rest of the renderer
    already does (empty tile list → no tile rectangles drawn, but the
    other layers still render)."""
    mp = run_dir / "01_pointconv" / "manifests" / "tf1_tile_manifest.json"
    if not mp.is_file():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _curb_road_metrics(run_dir: Path) -> dict:
    """Counts + sizes for the Aecon corridor's KEY deliverables — curblines,
    edge-of-pavement, the road-surface polygon, and building walls. Each field
    is None when the deliverable is absent. Lengths/areas assume the metric
    stage0c CRS the shapefiles are written in (EPSG:269xx), so length is metres
    and area is converted to hectares. geopandas-based; degrades to all-None."""
    out = {"curb_n": None, "curb_m": None, "eop_n": None, "eop_m": None,
           "road_n": None, "road_ha": None, "wall_n": None, "wall_m": None}
    try:
        import geopandas as gpd
    except Exception:
        return out

    def _lines(p: Path):
        try:
            if p.exists():
                g = gpd.read_file(p)
                return len(g), float(g.geometry.length.sum())
        except Exception:
            pass
        return None, None

    def _poly_ha(p: Path):
        try:
            if p.exists():
                g = gpd.read_file(p)
                return len(g), float(g.geometry.area.sum()) / 10000.0
        except Exception:
            pass
        return None, None

    out["curb_n"], out["curb_m"] = _lines(run_dir / "04_curbs" / "viz" / "curblines.shp")
    out["eop_n"], out["eop_m"] = _lines(run_dir / "04_curbs" / "viz" / "eop.shp")
    out["road_n"], out["road_ha"] = _poly_ha(run_dir / "05_road_surface" / "road_surface.shp")
    out["wall_n"], out["wall_m"] = _lines(run_dir / "04w_building_walls" / "Building_Walls.shp")
    return out


def _curb_road_summary_html(run_dir: Path) -> str:
    """The 'Key deliverables' headline line (curbs + roads first) for the
    Deliverables card. Returns '' when no curb/road deliverables exist."""
    m = _curb_road_metrics(run_dir)
    bits = []
    if m["curb_n"] is not None:
        _len = f' ({m["curb_m"]:.0f} m)' if m["curb_m"] else ""
        bits.append('<span style="color:#1456b8;font-weight:700;">'
                    f'Curb lines: {m["curb_n"]}{_len}</span>')
    if m["eop_n"] is not None:
        _len = f' ({m["eop_m"]:.0f} m)' if m["eop_m"] else ""
        bits.append('<span style="color:#b35a13;font-weight:700;">'
                    f'Edge of pavement: {m["eop_n"]}{_len}</span>')
    if m["road_n"] is not None:
        bits.append('<span style="color:#2a6e3a;font-weight:700;">'
                    f'Road surface: {m["road_ha"]:.2f} ha</span>')
    if m["wall_n"] is not None:
        _len = f' ({m["wall_m"]:.0f} m)' if m["wall_m"] else ""
        bits.append('<span style="color:#7a3aa6;font-weight:700;">'
                    f'Building walls: {m["wall_n"]}{_len}</span>')
    if not bits:
        return ""
    return ('<div style="font-size:13px;margin:6px 0 4px 0;">'
            '<span style="color:#333;font-weight:600;">Key deliverables &mdash; </span>'
            + ' &nbsp;&middot;&nbsp; '.join(bits) + '</div>')


def _load_trajectory(run_dir: Path) -> np.ndarray:
    """Try several known trajectory sources, in priority order:

      1. 00_inputs/trajectory.csv (legacy / per-source mode)
      2. 04_curbs/artifacts/dataset_manifest.json (curb-skill, after stage 4
         init-project has parsed the source .lst into trajectory_runs)

    Returns (N, 2) float64 array of XY trajectory stations, or empty
    array if no source is available yet.
    """
    tp = run_dir / "00_inputs" / "trajectory.csv"
    if tp.exists():
        rows = []
        with tp.open() as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append((float(r["x"]), float(r["y"])))
        if rows:
            return np.array(rows)

    # Fallback: curb-skill's dataset_manifest.json (available after stage 4
    # init-project, which is one of the first sub-steps of stage 4).
    manifest = run_dir / "04_curbs" / "artifacts" / "dataset_manifest.json"
    if manifest.exists():
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            rows = []
            for run_entry in m.get("trajectory_runs", []):
                for st in run_entry.get("stations", []):
                    rows.append((float(st["x"]), float(st["y"])))
            if rows:
                return np.array(rows)
        except Exception:
            pass

    return np.empty((0, 2))


def _tile_status(run_dir: Path, tile: dict) -> str:
    tile_id = tile["tile_id"]
    tile_out_dir = run_dir / "01_pointconv" / "tf1_outputs" / tile_id
    if not tile_out_dir.exists():
        return "pending"
    # The final per-tile classified LAS.
    seg = tile_out_dir / f"{tile_id}_v_seg_out.las"
    if seg.exists():
        return "done"
    return "inferring"


def _merged_sources(run_dir: Path) -> set[str]:
    co = run_dir / "01_pointconv" / "combined_outputs"
    if not co.exists():
        return set()
    out = set()
    for p in co.glob("*_tf1_pointconv_combined_0p1m.las"):
        # Strip the standard suffix to recover source_stem.
        stem = p.stem.replace("_tf1_pointconv_combined_0p1m", "")
        out.add(stem)
    return out


def _source_bbox(tiles_for_source: list[dict]) -> tuple[float, float, float, float]:
    xs0 = [t["core_min_x"] for t in tiles_for_source]
    ys0 = [t["core_min_y"] for t in tiles_for_source]
    xs1 = [t["core_max_x"] for t in tiles_for_source]
    ys1 = [t["core_max_y"] for t in tiles_for_source]
    return min(xs0), min(ys0), max(xs1), max(ys1)


def _shapefile_geoms(p: Path):
    """Load a shapefile lightly via fiona-free fallback to geopandas."""
    if not p.exists():
        return None
    try:
        import geopandas as gpd
        return gpd.read_file(p)
    except Exception as e:
        print(f"WARN reading {p}: {e}")
        return None


def _latest_activity_line(run_dir: Path) -> str:
    """Best-effort human-friendly 'what's happening right now' string.

    Parses the tail of the Stage 2 pole-cropping internal log + the
    orchestrator run log for the most recent meaningful phase marker.
    The corridor-merge + combined-thin phases (the longest part of Stage 2
    on big LAZ) emit no per-pole files, so without this the dashboard
    looks frozen — this surfaces lines like '[CORRIDOR] 14/14 spans merged'
    and '[TREE-MERGE] COMBINED_THIN level 2' as readable activity.

    Returns "" when nothing useful is found.
    """
    import re as _re
    logs = []
    ilog = run_dir / "02_pole_crop" / "output" / "log"
    if ilog.is_dir():
        logs.extend(sorted(ilog.glob("*_pipeline.log")))
    olog = run_dir / "_orchestrator_run.log"
    if olog.exists():
        logs.append(olog)
    # Stage 0t/0u run their own per-stage logs (Aecon corridor chain) — the
    # 0u crop is file-silent for minutes on multi-GB sources, so its
    # [traj-corridor] [k/n] lines are the only live signal.
    for _st in ("_stage0u_run.log", "_stage0t_run.log"):
        p = run_dir / _st
        if p.exists():
            logs.append(p)
    # Patterns → friendly phrasing. Checked newest-line-first.
    patterns = [
        (r"\[traj-corridor\]\s*\[(\d+)/(\d+)\]\s*([^:]+?)\s*:\s*[\d,]+/[\d,]+\s*\((\d+(?:\.\d+)?)%\)",
         lambda m: (f"corridor crop {m.group(1)}/{m.group(2)} — "
                    f"{m.group(3)} ({m.group(4)}% kept)")),
        (r"\[traj-corridor\]\s*(\d+)\s*source\(s\),\s*(\d+)\s*segment",
         lambda m: f"corridor crop starting — {m.group(1)} source(s)"),
        (r"\[corridor-density\]", lambda m: "rendering corridor density figure"),
        (r"COMBINED_THIN level (\d+)", lambda m: f"combining corridor cloud (merge level {m.group(1)})"),
        (r"\[CORRIDOR\]\s*(\d+)/(\d+)\s*spans merged", lambda m: f"merging corridor spans {m.group(1)}/{m.group(2)}"),
        (r"\[CORRIDOR\] Merging (\d+) spans", lambda m: f"merging {m.group(1)} corridor spans"),
        (r"\[CORRIDOR\] Thinning combined", lambda m: "thinning combined corridor"),
        (r"\[REPORT\]", lambda m: "writing detection report"),
        (r"cropping pole|\[CROP\]", lambda m: "cropping poles"),
        (r"\[TILE\][^\n]*->\s*(\d+)\s+tiles", lambda m: f"tiling source LAZ ({m.group(1)} tiles)"),
    ]
    try:
        # Read the last ~4 KB of the freshest log only.
        log_path = max(logs, key=lambda p: p.stat().st_mtime) if logs else None
        if not log_path:
            return ""
        text = log_path.read_text(encoding="utf-8", errors="replace")
        tail = text[-8000:]
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        for ln in reversed(lines):
            for pat, fmt in patterns:
                m = _re.search(pat, ln)
                if m:
                    return fmt(m)
        return ""
    except Exception:
        return ""


def _load_detection_status(run_dir: Path) -> dict:
    """Return {pole_id: status} from the Stage 2 detection results CSV.

    status is one of 'found' / 'not_found' / 'no_las_file'. Empty dict if
    the CSV doesn't exist yet (pre-Stage-2) or can't be parsed.

    Used to color pole markers by detection outcome on the validation
    chain, which has no Stage 3 pole-vec to drive the body-found coloring.
    For QC this is the single most important visual: which customer poles
    were actually detected in the LiDAR vs missed.
    """
    try:
        det_dirs = [p for p in run_dir.iterdir()
                    if p.is_dir() and p.name.endswith("_detection")]
        if not det_dirs:
            return {}
        det = sorted(det_dirs)[0]
        project = det.name.removesuffix("_detection")
        csv_p = det / f"{project}_detection_results.csv"
        if not csv_p.exists():
            return {}
        import pandas as _pd
        df = _pd.read_csv(csv_p)
        # Pole-id column varies: 'Pole' (Firmatek SHP), 'pole_id', or 'pole'.
        id_col = next((c for c in ("Pole", "pole_id", "pole", "PoleID")
                       if c in df.columns), None)
        if id_col is None or "status" not in df.columns:
            return {}
        return {str(r[id_col]): str(r["status"]) for _, r in df.iterrows()}
    except Exception as e:
        print(f"WARN _load_detection_status: {e}")
        return {}


def _valid_crop_bbox(b) -> bool:
    """True if b is a sane [xmin,ymin,xmax,ymax] crop bbox.

    Rejects the LAS placeholder bounds (x_min=+DBL_MAX, x_max=-DBL_MAX)
    that a half-written crop file reports while Stage 2 is still writing
    it. Caching/drawing those produces a giant rectangle spanning the
    whole map (the "green overlay over the entire LAZ" bug, 2026-05-28).
    """
    try:
        if not (isinstance(b, (list, tuple)) and len(b) >= 4):
            return False
        x0, y0, x1, y1 = (float(v) for v in b[:4])
        if not all(abs(v) < 1e12 for v in (x0, y0, x1, y1)):
            return False
        w, h = x1 - x0, y1 - y0
        return 0 < w < 50000 and 0 < h < 50000
    except Exception:
        return False


def _read_laz_bbox_facts(run_dir: Path) -> dict:
    """Return the most authoritative LAZ-bbox facts dict, or {}.

    Two sources, in priority order:
      1. viz/span_statistics.json    -- Stage 0b output. Full bbox + per-pole
         inside/outside counts + nearest-neighbor stats + recommended crop
         half-size. Available ~30 s into the run.
      2. 00_inputs/laz_extent.json   -- Stage 0a early-feedback. Just the
         bbox + pole_crs, read straight from LAZ headers (no point data).
         Available ~1 s into the run.

    Both files use the same `laz_bbox_in_pole_crs` + `pole_crs` schema, so
    every renderer call site that pulls `bb = sp.get("laz_bbox_in_pole_crs")
    or sp.get("laz_bbox")` works against either source unchanged.

    Returns {} if neither file exists (very-early Stage 0a, or non-Firmatek
    chain). Callers should treat missing as a normal "no data yet" state.
    """
    for p in (run_dir / "viz" / "span_statistics.json",
              run_dir / "00_inputs" / "laz_extent.json"):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {}


def _load_imported_pole_tops(run_dir: Path, axes_crs):
    """Load 00_inputs/pole_tops.shp (Stage 0a output) with an `inside_laz`
    boolean per pole, reprojected to axes_crs.

    Used as the early-feedback overlay when poles_candidates_loose.shp
    (Stage 2 output) doesn't exist yet — gives the dashboard pole markers
    immediately after Stage 0a finishes (~1 s) instead of making the
    operator wait through Stage 0b + Stage 2 (5+ min on huge LAZ).

    The inside_laz flag is computed in the SHP's native CRS (== pole_crs)
    BEFORE reprojection, using the laz_bbox from span_statistics.json
    (Stage 0b output). If Stage 0b hasn't run yet, all poles get
    inside_laz=True (we don't know which are out yet).

    Returns a GeoDataFrame or None if pole_tops.shp doesn't exist.
    """
    p = run_dir / "00_inputs" / "pole_tops.shp"
    if not p.exists():
        return None
    gdf = _shapefile_geoms(p)
    if gdf is None or len(gdf) == 0:
        return None
    # Default: assume inside (no LAZ extent known yet -- Stage 0a without a LAZ
    # dir, or pre-Stage-0b). laz_known stays False so the map labels these as
    # plain "imported pole tops" instead of asserting LAZ coverage we have not
    # actually computed.
    gdf = gdf.copy()
    gdf["inside_laz"] = True
    laz_known = False
    try:
        sp = _read_laz_bbox_facts(run_dir)
        bb = sp.get("laz_bbox_in_pole_crs") or sp.get("laz_bbox") or {}
        if bb and all(k in bb for k in ("xmin", "ymin", "xmax", "ymax")):
            gx = gdf.geometry.x.values
            gy = gdf.geometry.y.values
            inside = ((gx >= bb["xmin"]) & (gx <= bb["xmax"]) &
                      (gy >= bb["ymin"]) & (gy <= bb["ymax"]))
            gdf["inside_laz"] = inside
            laz_known = True
    except Exception as e:
        print(f"WARN _load_imported_pole_tops inside-LAZ check: {e}")
    out = _reproject_gdf(gdf, axes_crs)
    try:
        out.attrs["laz_known"] = laz_known
    except Exception:
        pass
    return out


def _reproject_gdf(gdf, target_crs):
    """Reproject a GeoDataFrame to target_crs if both CRSes are known
    and differ. No-op on missing inputs or matching CRS.

    Needed by the Firmatek "stay-in-feet" profile: vector layers come
    from multiple stages in different CRSes (pole_tops.shp in ftUS;
    Body_Lines.shp + crops_metric in EPSG:26911 after Stage 0c). The
    dashboard plots everything on a single map, so it picks one
    axes_crs (the post-stage0c metric CRS) and reprojects the rest to
    match. Without this, ftUS pole points plot at X ~6.5 M while the
    metric tile rectangles plot at X ~390 K — pole markers fall off
    the visible map entirely.
    """
    if gdf is None or target_crs is None:
        return gdf
    try:
        src_crs = gdf.crs
        if src_crs is None:
            return gdf
        # Cheap equality check first (avoids unnecessary CRS-object alloc).
        if str(src_crs) == str(target_crs):
            return gdf
        try:
            if src_crs.to_string() == str(target_crs):
                return gdf
        except Exception:
            pass
        return gdf.to_crs(target_crs)
    except Exception as e:
        print(f"WARN reproject -> {target_crs} failed: {e}")
        return gdf


def _detect_axes_crs(run_dir: Path, fallback_gdf=None) -> str | None:
    """Detect the CRS the dashboard map's axes are in.

    Priority chain (same as the basemap detection — kept in one place so
    geometry reprojection and basemap fetch agree on the CRS):
      1. chain.yml stage0c_reproject_crops.target_crs — Firmatek metric
         detour. PointCONV tile rectangles drawn from tile_manifest are
         in this CRS, so the axes are too.
      2. fallback_gdf.crs (typically pole_gdf) — Aethon single-CRS, or
         pre-Stage-0c Firmatek with no tiles plotted yet.
      3. span_statistics.json's pole_crs (set by Stage 0a).
    Returns None if no signal is available.
    """
    crs = None
    try:
        chain_yml = run_dir / "chain.yml"
        if chain_yml.exists():
            import yaml as _yaml
            cfg = _yaml.safe_load(chain_yml.read_text(encoding="utf-8")) or {}
            so = cfg.get("stage_order") or []
            if "stage0c_reproject_crops" in so:
                crs = (cfg.get("stage0c_reproject_crops") or {}).get("target_crs")
    except Exception:
        pass
    if crs is None and fallback_gdf is not None:
        try:
            if fallback_gdf.crs is not None:
                crs = fallback_gdf.crs.to_string()
        except Exception:
            pass
    if crs is None:
        try:
            crs = _read_laz_bbox_facts(run_dir).get("pole_crs")
        except Exception:
            pass
    return crs


def _read_log_tail(run_dir: Path, n_lines: int = 30) -> tuple[str, list[str]]:
    """Return (log-file-path, last N lines) for the currently-active stage's log."""
    candidates = [
        ("stage1", run_dir / "_stage1_run.log"),
        ("stage2", run_dir / "_stage2_run.log"),
        ("stage3", run_dir / "_stage3_run.log"),
        ("stage4", run_dir / "_stage4_run.log"),
    ]
    # Pick the most recently modified that exists.
    existing = [(name, p) for name, p in candidates if p.exists()]
    if not existing:
        return "", []
    existing.sort(key=lambda kv: kv[1].stat().st_mtime, reverse=True)
    name, path = existing[0]
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            # Read last ~64KB to find the tail without loading huge files.
            f.seek(0, 2)
            sz = f.tell()
            chunk = 65536
            f.seek(max(0, sz - chunk))
            data = f.read()
        lines = data.splitlines()
        return f"{name}: {path.name}", lines[-n_lines:]
    except Exception as e:
        return f"{name}: {path.name}", [f"<error reading log: {e}>"]


def _stage_pill(state: str, label: str, sub: str) -> str:
    color = {
        "done": "#2e9b3a",
        "running": "#ffb000",
        "pending": "#cccccc",
        "failed": "#d8221f",
        "disabled": "#999999",
    }.get(state, "#cccccc")
    text_color = "#ffffff" if state in ("done", "running", "failed") else "#444444"
    icon = {"done": "✓", "running": "⟳", "pending": "·",
            "failed": "✗", "disabled": "—"}.get(state, "·")
    return (
        f'<div class="stage-pill" style="background:{color};color:{text_color};">'
        f'<div class="stage-pill-title">{icon} {label}</div>'
        f'<div class="stage-pill-sub">{sub}</div>'
        f'</div>'
    )


def _params_panel_html(params_blob: dict) -> str:
    """Render the parameter sidebar from pipeline_params.json contents."""
    sections = []
    # NOTE: these keys are pipeline_params.json parameter-BLOCK names (a
    # different namespace from status.json stage keys); only stage1_pointconv
    # overlaps with STAGE_LABELS, so it is derived from the canonical dict.
    pretty_names = {
        "stage1_pointconv": STAGE_LABELS["stage1_pointconv"],
        "stage2_loose_bridge_extract": "Stage 2a — Loose DBSCAN bridge",
        "stage2_pole_cropping": "Stage 2b — pole-cropping",
        "stage3_pole_vec_body_only": "Stage 3 — pole-vec (body only)",
        "stage4_curb_skill": "Stage 4 — curb-skill",
    }
    parameters = params_blob.get("parameters", {})
    for key, label in pretty_names.items():
        block = parameters.get(key, {})
        if not block:
            continue
        src = block.get("source", "unknown")
        src_class = ("eff" if src.startswith("effective") else
                     ("plan" if src.startswith("planned") else "unk"))
        rows = []
        for k, v in block.items():
            if k == "source":
                continue
            if isinstance(v, (dict, list)):
                v_str = json.dumps(v, indent=2)
                rows.append(
                    f'<tr><td class="pk">{k}</td>'
                    f'<td class="pv"><pre>{v_str}</pre></td></tr>'
                )
            else:
                rows.append(
                    f'<tr><td class="pk">{k}</td>'
                    f'<td class="pv">{v}</td></tr>'
                )
        section = (
            f'<details open><summary>'
            f'<span class="stage-name">{label}</span>'
            f'<span class="src-badge src-{src_class}">{src}</span>'
            f'</summary>'
            f'<table class="params-table">{"".join(rows)}</table>'
            f'</details>'
        )
        sections.append(section)
    return "\n".join(sections)


def _render_html(run_dir: Path, out_html: Path, png_name: str,
                 stage_counts: dict, progress_counts: dict | None = None,
                 rates: dict | None = None) -> None:
    """Render the auto-refreshing HTML dashboard."""
    progress_counts = progress_counts or {}
    rates = rates or {}
    # Load params JSON if dump_pipeline_params has been called.
    params_path = out_html.parent / "pipeline_params.json"
    params_blob: dict = {}
    if params_path.exists():
        try:
            params_blob = json.loads(params_path.read_text(encoding="utf-8"))
        except Exception as e:
            params_blob = {"_error": str(e)}

    states = params_blob.get("stage_states", {})

    # The orchestrator's status.json is authoritative when present -- it
    # captures running/failed/elapsed which the filesystem heuristic can't.
    status_path = out_html.parent / "status.json"
    orch_status: dict = {}
    if status_path.exists():
        try:
            orch_status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    orch_stages = {s["name"]: s for s in orch_status.get("stages", [])}

    def _merge_state(stage_key: str, orch_key: str) -> tuple[str, str]:
        """Return (state, optional-extra-sub-text).

        Orchestrator state wins over the filesystem heuristic for
        running/failed. For "done" we trust either source.
        """
        fs_state = states.get(stage_key, {}).get("state", "pending")
        o = orch_stages.get(orch_key, {})
        o_state = o.get("state")
        extra = ""
        if o_state in ("running", "failed"):
            state = o_state
            if "started_at" in o:
                extra = f"started {o['started_at'].split('T')[-1]}"
        elif o_state == "done":
            state = "done"
            if "elapsed_s" in o and o["elapsed_s"] > 0:
                mins = o["elapsed_s"] / 60.0
                extra = f"{mins:.1f} min"
        else:
            state = fs_state
        return state, extra

    def _rate_str(stage_key: str) -> str:
        """Format 'X.X /min · ETA Y min' from the rates dict (G1)."""
        r = rates.get(stage_key)
        if not r or r.get("complete"):
            return ""
        rpm = r.get("rate_per_min", 0)
        eta = _format_eta(r.get("eta_sec"))
        if rpm <= 0 and not eta:
            return ""
        bits = []
        if rpm > 0:
            unit = (progress_counts.get(stage_key, {}).get("unit") or "items")
            bits.append(f"{rpm:.1f} {unit}/min")
        if eta:
            bits.append(eta)
        return " · ".join(bits)

    # Compute stage pills.
    s1 = states.get("stage1", {})
    s1_state, s1_extra = _merge_state("stage1", "stage1_pointconv")
    s1_sub = (f'{s1.get("tiles_done", 0)}/{s1.get("tiles_total", 0)} tiles · '
              f'{s1.get("sources_merged", 0)}/{s1.get("sources_total", 0)} merged')
    s1_rate = _rate_str("stage1_pointconv")
    if s1_rate:
        s1_sub = f"{s1_sub} · {s1_rate}"
    if s1_extra:
        s1_sub = f"{s1_sub} · {s1_extra}"

    s2_state, s2_extra = _merge_state("stage2", "stage2_pole_crop")
    s2_sub = (f"{stage_counts.get('stage2_candidates', 0)} candidates"
              if stage_counts.get("stage2_candidates") else "")
    s2_p = progress_counts.get("stage2_pole_crop", {})
    if s2_p and s2_p.get("total"):
        s2_sub = (s2_sub + " · " if s2_sub else "") + \
                 f"{s2_p['done']}/{s2_p['total']} crops"
    # Pole-cropping native tile progress: count the <stem>_NNNN.las
    # files written under 02_pole_crop/output/tiles/ vs the total tile
    # count parsed from the stage 2 log. Lets the user see "142/476
    # tiles" as Stage 2 chews through a huge raw LAZ (Verizon profile).
    try:
        tile_dir = run_dir / "02_pole_crop" / "output" / "tiles"
        if tile_dir.is_dir():
            # Tiles are named <source_stem>_NNNN.las by pole-cropping's
            # tile_files() — count anything matching *_NNNN.las.
            n_tiles_written = sum(1 for _ in tile_dir.glob("*_[0-9][0-9][0-9][0-9].la[sz]"))
            # Total tile count comes from the stage 2 log:
            # "[TILE] N points -> M tiles (from <stem>)"
            # OR estimate it from the LAZ point count / max_points_per_tile
            # if the log line hasn't been written yet (it appears at end).
            n_tiles_total = None
            tiling_active = True
            cur_src = None
            # Pole-cropping writes its [TILE] log to its OWN log dir at
            # <run>/02_pole_crop/output/log/<timestamp>_pipeline.log,
            # not to the orchestrator's _stage2_run.log (which only
            # gets the command-line echo + the candidates filter step).
            # Use the MOST RECENT internal log; fall back to the orch log.
            log_candidates = []
            internal_log_dir = run_dir / "02_pole_crop" / "output" / "log"
            if internal_log_dir.is_dir():
                # reverse=True -> newest (timestamp-prefixed) first, so a
                # re-run's log wins and we don't double-count old runs.
                log_candidates.extend(
                    sorted(internal_log_dir.glob("*_pipeline.log"), reverse=True))
            orch_log = run_dir / "_stage2_run.log"
            if orch_log.exists():
                log_candidates.append(orch_log)
            import re as _re
            for log_path in log_candidates:
                try:
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                    # The tiler logs, per source:
                    #   "[TILE] Splitting: <name> (<N> points)"         start
                    #   "[TILE] <N> points -> <M> tiles (from <name>)"  done
                    # single-tile sources log "[TILE] Writing tile: <name> -> ...".
                    # The big source can split for many minutes; summing only the
                    # finished "-> M tiles" lines lets the total lag the written
                    # count, so the bar showed a permanent 100%. Count finished
                    # sources for real + ESTIMATE in-flight ones (points / the
                    # observed points-per-tile) so the percent is honest.
                    done = {m.group(3).strip(): (int(m.group(1).replace(",", "")),
                                                 int(m.group(2)))
                            for m in _re.finditer(
                                r"\[TILE\]\s+([\d,]+)\s+points\s*->\s*(\d+)\s+tiles\s*\(from\s+(.+?)\)",
                                text)}
                    started = {m.group(1).strip(): int(m.group(2).replace(",", ""))
                               for m in _re.finditer(
                                   r"\[TILE\] Splitting:\s*(.+?)\s*\(([\d,]+)\s+points\)",
                                   text)}
                    single = {m.group(1).strip() for m in _re.finditer(
                        r"\[TILE\] Writing tile:\s*(.+?)\s*->", text)}
                    if not (done or started or single):
                        continue
                    m_src = _re.search(
                        r"\[TILE\] (?:Found|Tiling)\s+(\d+)\s+file", text)
                    n_src = int(m_src.group(1)) if m_src else len(started) + len(single)
                    _dp = sum(p for p, _ in done.values())
                    _dt = sum(t for _, t in done.values())
                    ppt = (_dp / _dt) if _dt else 13_000_000.0
                    total = sum(
                        done[n][1] if n in done
                        else max(1, int((p + ppt - 1) // ppt))
                        for n, p in started.items())
                    total += len(single - set(started) - set(done))
                    n_tiles_total = total or None
                    finished = len(set(done) | (single - set(started)))
                    tiling_active = finished < n_src
                    pend = [n for n in started if n not in done]
                    cur_src = pend[-1] if pend else None
                    break
                except Exception:
                    continue
            if n_tiles_written or n_tiles_total:
                if n_tiles_total and not tiling_active:
                    tile_bit = f"{n_tiles_total} tiles tiled"
                elif n_tiles_total:
                    # Mid-tiling: honest estimate, never a confident 100%.
                    disp_total = max(n_tiles_total, n_tiles_written)
                    pct = min(99.0, 100.0 * n_tiles_written / max(disp_total, 1))
                    _src = f" · splitting {cur_src}" if cur_src else ""
                    tile_bit = (f"{n_tiles_written}/~{disp_total} tiles "
                                f"({pct:.0f}%, tiling{_src})")
                else:
                    tile_bit = f"{n_tiles_written} tiles written"
                s2_sub = f"{s2_sub} · {tile_bit}" if s2_sub else tile_bit
    except Exception:
        pass
    s2_rate = _rate_str("stage2_pole_crop")
    if s2_rate:
        s2_sub = f"{s2_sub} · {s2_rate}" if s2_sub else s2_rate
    if s2_extra:
        s2_sub = f"{s2_sub} · {s2_extra}" if s2_sub else s2_extra

    s3_state, s3_extra = _merge_state("stage3", "stage3_pole_vec")
    s3_sub = (f"{stage_counts.get('stage3_bodies', 0)} bodies"
              if stage_counts.get("stage3_bodies") else "")
    s3_p = progress_counts.get("stage3_pole_vec", {})
    if s3_p and s3_p.get("total"):
        s3_sub = (s3_sub + " · " if s3_sub else "") + \
                 f"{s3_p['done']}/{s3_p['total']} poles"
    s3_rate = _rate_str("stage3_pole_vec")
    if s3_rate:
        s3_sub = f"{s3_sub} · {s3_rate}" if s3_sub else s3_rate
    if s3_extra:
        s3_sub = f"{s3_sub} · {s3_extra}" if s3_sub else s3_extra

    # Stage 3.5 utility-pole network — derive sub from utility_topology summary.
    s3_5_state, s3_5_extra = _merge_state("stage3_5", "stage3_5_pole_network")
    s3_5_sub = ""
    try:
        detection_dirs = [p for p in run_dir.iterdir()
                          if p.is_dir() and p.name.endswith("_detection")]
        if detection_dirs:
            _topo_dir = sorted(detection_dirs)[0] / "utility_topology"
            _sums = list(_topo_dir.glob("*_pole_network_summary.json"))
            if _sums:
                _ts = json.loads(_sums[0].read_text(encoding="utf-8"))
                _t = _ts.get("topology", {})
                _q = _ts.get("qc", {})
                _n_dup = int(_q.get("n_near_duplicate_pairs", 0))
                s3_5_sub = (f"{int(_t.get('n_nodes', 0))} poles · "
                             f"{int(_t.get('n_edges', 0))} spans · "
                             f"{int(_t.get('n_components', 0))} comp")
                if _n_dup:
                    s3_5_sub = f"{s3_5_sub} · {_n_dup} QC"
                # If the orchestrator hasn't started Stage 3.5 yet but the
                # JSON exists (e.g. from a manual POC run earlier in the
                # session), promote the pill from pending → done so the
                # operator sees the output.
                if s3_5_state == "pending":
                    s3_5_state = "done"
    except Exception:
        pass
    if s3_5_extra:
        s3_5_sub = f"{s3_5_sub} · {s3_5_extra}" if s3_5_sub else s3_5_extra

    s4_state, s4_extra = _merge_state("stage4", "stage4_curbs")
    n_curbs = stage_counts.get("stage4_curbs", 0)
    n_eops = stage_counts.get("stage4_eops", 0)
    s4_sub = (f"{n_curbs} curblines · {n_eops} EOP" if (n_curbs or n_eops) else "")
    # G14: curb-skill sub-stage progress in the pill subtitle.
    s4_p = progress_counts.get("stage4_curbs", {})
    if s4_p and s4_p.get("total"):
        s4_sub = (s4_sub + " · " if s4_sub else "") + \
                 f"{s4_p['done']}/{s4_p['total']} sub-stages"
    s4_rate = _rate_str("stage4_curbs")
    if s4_rate:
        s4_sub = f"{s4_sub} · {s4_rate}" if s4_sub else s4_rate
    if s4_extra:
        s4_sub = f"{s4_sub} · {s4_extra}" if s4_sub else s4_extra

    # Stage 4t — tree trunk & canopy (Aecon corridor specialty stage). Counts
    # are read cheaply from each .dbf header (record count at byte 4), the same
    # trick stage 5 uses below, so no geopandas import is needed for the pill.
    s4t_state, s4t_extra = _merge_state("stage4t_tree_trunk_canopy",
                                        "stage4t_tree_trunk_canopy")
    s4t_sub = ""
    try:
        def _dbf_count(shp):
            dbf = shp.with_suffix(".dbf")
            if not dbf.exists():
                return 0
            with dbf.open("rb") as f:
                f.seek(4)
                return int.from_bytes(f.read(4), "little")
        t_dir = run_dir / "04t_tree_trunk_canopy"
        n_stems = _dbf_count(t_dir / "Tree_Stems.shp")
        n_canopy = _dbf_count(t_dir / "Tree_Canopy.shp")
        if n_stems or n_canopy:
            s4t_sub = f"{n_stems} trunks · {n_canopy} canopy"
    except Exception:
        pass
    if s4t_extra:
        s4t_sub = f"{s4t_sub} · {s4t_extra}" if s4t_sub else s4t_extra

    # Stage 5 — road surface + classified points. No
    # filesystem-counts dict yet, so the subtitle just reflects
    # presence of the road_surface.shp deliverable.
    s5_state, s5_extra = _merge_state("stage5_road_surface", "stage5_road_surface")
    s5_sub = ""
    try:
        road_shp = run_dir / "05_road_surface" / "road_surface.shp"
        if road_shp.exists():
            # Cheap record-count from the .dbf header.
            dbf = road_shp.with_suffix(".dbf")
            if dbf.exists():
                with dbf.open("rb") as f:
                    f.seek(4)
                    n_poly = int.from_bytes(f.read(4), "little")
                s5_sub = f"{n_poly} polygon" + ("s" if n_poly != 1 else "")
        rc_dir = run_dir / "05_road_surface" / "road_classified"
        if rc_dir.is_dir():
            n_rc = sum(1 for _ in rc_dir.glob("*_road_classified.laz"))
            if n_rc:
                s5_sub = (s5_sub + " · " if s5_sub else "") + f"{n_rc} road LAZ"
    except Exception:
        pass
    if s5_extra:
        s5_sub = f"{s5_sub} · {s5_extra}" if s5_sub else s5_extra

    # Stage 6 — final classified LAZ + GPKG + QGIS project.
    s6_state, s6_extra = _merge_state("stage6_final_classification",
                                       "stage6_final_classification")
    s6_sub = ""
    try:
        fc_dir = run_dir / "06_final_classification"
        if fc_dir.is_dir():
            n_fc = sum(1 for _ in fc_dir.glob("*_final_classified.laz"))
            if n_fc:
                s6_sub = f"{n_fc} final LAZ"
        if (run_dir / "tester_deliverables.gpkg").exists():
            s6_sub = (s6_sub + " · " if s6_sub else "") + "GPKG ✓"
        if (run_dir / "TESTER_QGIS_PROJECT.qgz").exists():
            s6_sub = (s6_sub + " · " if s6_sub else "") + "QGIS ✓"
    except Exception:
        pass
    if s6_extra:
        s6_sub = f"{s6_sub} · {s6_extra}" if s6_sub else s6_extra

    # Stage 0a — CSV -> pole_tops.shp (Verizon profile).
    s0a_state, s0a_extra = _merge_state("stage0a_pole_csv", "stage0a_pole_csv")
    s0a_sub = ""
    try:
        s0a_shp = run_dir / "00_inputs" / "pole_tops.shp"
        if s0a_shp.exists():
            dbf = s0a_shp.with_suffix(".dbf")
            if dbf.exists():
                with dbf.open("rb") as f:
                    f.seek(4)
                    n = int.from_bytes(f.read(4), "little")
                s0a_sub = f"{n} pole tops"
    except Exception:
        pass
    if s0a_extra:
        s0a_sub = f"{s0a_sub} · {s0a_extra}" if s0a_sub else s0a_extra

    # Stage 0b — span statistics + coverage preview.
    s0b_state, s0b_extra = _merge_state("stage0b_span_stats", "stage0b_span_stats")
    s0b_sub = ""
    try:
        s0b_json = run_dir / "viz" / "span_statistics.json"
        if s0b_json.exists():
            import json as _json
            d = _json.loads(s0b_json.read_text(encoding="utf-8"))
            n_in = d.get("n_inside_laz_extent", 0)
            n_out = d.get("n_outside_laz_extent", 0)
            rec = d.get("recommended_crop_half_size_m")
            s0b_sub = f"{n_in} in / {n_out} out"
            if rec:
                s0b_sub += f" · crop {rec:.0f}m"
    except Exception:
        pass
    if s0b_extra:
        s0b_sub = f"{s0b_sub} · {s0b_extra}" if s0b_sub else s0b_extra

    # Stage 0 — LAS reproject.
    s0_state, s0_extra = _merge_state("stage0_reproject", "stage0_reproject")
    s0_sub = ""
    try:
        s0_dir = run_dir / "00_reprojected" / "las"
        if s0_dir.is_dir():
            n = sum(1 for _ in s0_dir.glob("*.la[sz]"))
            if n:
                s0_sub = f"{n} LAS reprojected"
    except Exception:
        pass
    if s0_extra:
        s0_sub = f"{s0_sub} · {s0_extra}" if s0_sub else s0_extra

    # Stage 1b — fine-resolution back-projection.
    s1b_state, s1b_extra = _merge_state("stage1b_fine_classification",
                                         "stage1b_fine_classification")
    s1b_sub = ""
    try:
        s1b_dir = run_dir / "01_pointconv" / "combined_outputs_0p025m"
        if s1b_dir.is_dir():
            n = sum(1 for _ in s1b_dir.glob("*_0p025m.las"))
            if n:
                s1b_sub = f"{n} fine LAS"
    except Exception:
        pass
    if s1b_extra:
        s1b_sub = f"{s1b_sub} · {s1b_extra}" if s1b_sub else s1b_extra

    # Stage 7 — corridor LAZ merge.
    s7_state, s7_extra = _merge_state("stage7_corridor_merge",
                                       "stage7_corridor_merge")
    s7_sub = ""
    try:
        s7_laz = run_dir / "07_corridor" / "corridor_classified.laz"
        if s7_laz.exists():
            sz_gib = s7_laz.stat().st_size / (1024**3)
            s7_sub = f"{sz_gib:.1f} GiB"
    except Exception:
        pass
    if s7_extra:
        s7_sub = f"{s7_sub} · {s7_extra}" if s7_sub else s7_extra

    # Stage 0c — per-pole crops reproject (Verizon stay-in-feet profile).
    s0c_state, s0c_extra = _merge_state("stage0c_reproject_crops",
                                         "stage0c_reproject_crops")
    s0c_sub = ""
    try:
        s0c_dir = run_dir / "02_pole_crop" / "output" / "crops_metric"
        if s0c_dir.is_dir():
            n = len(_pole_files(s0c_dir, "las"))
            if n:
                s0c_sub = f"{n} crops reprojected"
    except Exception:
        pass
    if s0c_extra:
        s0c_sub = f"{s0c_sub} · {s0c_extra}" if s0c_sub else s0c_extra

    # Stage 8 — deliverables back to ftUS (Verizon final step).
    s8_state, s8_extra = _merge_state("stage8_deliverables_to_ftus",
                                       "stage8_deliverables_to_ftus")
    s8_sub = ""
    try:
        s8_per_pole = run_dir / "06_final_classification_ftus"
        s8_corridor = run_dir / "07_corridor_ftus" / "corridor_classified_ftus.laz"
        bits = []
        if s8_per_pole.is_dir():
            n = sum(1 for _ in s8_per_pole.glob("*_final_classified.laz"))
            if n:
                bits.append(f"{n} per-pole")
        if s8_corridor.exists():
            sz_gib = s8_corridor.stat().st_size / (1024**3)
            bits.append(f"corridor {sz_gib:.1f} GiB")
        s8_sub = " · ".join(bits)
    except Exception:
        pass
    if s8_extra:
        s8_sub = f"{s8_sub} · {s8_extra}" if s8_sub else s8_extra

    # Stage 0t — trajectory ingestion (Aecon corridor profile).
    s0t_state, s0t_extra = _merge_state("stage0t_trajectory", "stage0t_trajectory")
    s0t_sub = ""
    try:
        _traj = run_dir / "00_inputs" / "trajectory.shp"
        if _traj.exists():
            _dbf = _traj.with_suffix(".dbf")
            if _dbf.exists():
                with _dbf.open("rb") as f:
                    f.seek(4)
                    _n = int.from_bytes(f.read(4), "little")
                s0t_sub = f"{_n} run line(s)"
    except Exception:
        pass
    if s0t_extra:
        s0t_sub = f"{s0t_sub} · {s0t_extra}" if s0t_sub else s0t_extra

    # Stage 0u — trajectory-corridor crop (Aecon corridor profile).
    s0u_state, s0u_extra = _merge_state("stage0u_corridor_crop", "stage0u_corridor_crop")
    s0u_sub = ""
    try:
        _mani = run_dir / "02_corridor" / "crops" / "_corridor_manifest.json"
        if _mani.exists():
            import json as _json
            _d = _json.loads(_mani.read_text(encoding="utf-8"))
            _no = _d.get("n_outputs", 0)
            _gt = _d.get("grand_total_points", 0) or 0
            _gk = _d.get("grand_kept_points", 0) or 0
            _pct = (100.0 * _gk / _gt) if _gt else 0.0
            s0u_sub = f"{_no} corridor file(s) · {_pct:.0f}% kept"
    except Exception:
        pass
    if s0u_extra:
        s0u_sub = f"{s0u_sub} · {s0u_extra}" if s0u_sub else s0u_extra

    # Build pills list by reading the ACTUAL stages from status.json, so
    # per-preset stage_order (e.g. Verizon's 9-stage chain) renders the
    # right pill bar. Mississauga's 8-stage chain keeps working unchanged.
    # Pill labels derive from the canonical module-level STAGE_LABELS so the
    # pill bar, banner, gallery and deliverables index all say the same thing.
    _pill_states = {
        "stage0t_trajectory":           (s0t_state, s0t_sub),
        "stage0u_corridor_crop":        (s0u_state, s0u_sub),
        "stage0a_pole_csv":             (s0a_state, s0a_sub),
        "stage0b_span_stats":           (s0b_state, s0b_sub),
        "stage0_reproject":             (s0_state,  s0_sub),
        "stage0c_reproject_crops":      (s0c_state, s0c_sub),
        "stage1_pointconv":             (s1_state,  s1_sub),
        "stage1b_fine_classification":  (s1b_state, s1b_sub),
        "stage2_pole_crop":             (s2_state,  s2_sub),
        "stage3_pole_vec":              (s3_state,  s3_sub),
        "stage3_5_pole_network":        (s3_5_state, s3_5_sub),
        "stage4_curbs":                 (s4_state,  s4_sub),
        "stage4t_tree_trunk_canopy":    (s4t_state, s4t_sub),
        "stage5_road_surface":          (s5_state,  s5_sub),
        "stage6_final_classification":  (s6_state,  s6_sub),
        "stage7_corridor_merge":        (s7_state,  s7_sub),
        "stage8_deliverables_to_ftus":  (s8_state,  s8_sub),
    }
    STAGE_DISPLAY = {k: (STAGE_LABELS[k], _st, _sub)
                     for k, (_st, _sub) in _pill_states.items()}
    # status.json's stages list is authored by chain_orchestrator's
    # _load_status(cfg) which already respects per-preset stage_order.
    pill_stages = [s.get("name") for s in orch_status.get("stages", [])
                   if s.get("name") in STAGE_DISPLAY]
    if not pill_stages:
        # Fallback to the default 6-pill Mississauga layout.
        pill_stages = ["stage1_pointconv", "stage2_pole_crop",
                       "stage3_pole_vec", "stage4_curbs",
                       "stage5_road_surface", "stage6_final_classification"]
    # Inject Stage 3.5 if the deliverable exists but the status.json
    # predates the stage (e.g. a manual estimate_pole_network.py run on
    # an already-completed chain). Place it right after stage1_pointconv
    # — Stage 3.5 only needs class==14 (wire) + class==18 (pole) from
    # Stage 1 and pole_tops.shp from Stage 2, so it runs immediately
    # after PointCONV finishes, BEFORE the heavier Stage 1b / Stage 3
    # pole-vec work.
    if ("stage3_5_pole_network" not in pill_stages
            and s3_5_state == "done"):
        if "stage1_pointconv" in pill_stages:
            pill_stages.insert(
                pill_stages.index("stage1_pointconv") + 1,
                "stage3_5_pole_network")
        elif "stage3_pole_vec" in pill_stages:
            pill_stages.insert(
                pill_stages.index("stage3_pole_vec") + 1,
                "stage3_5_pole_network")
        else:
            pill_stages.append("stage3_5_pole_network")
    pills = "\n".join(
        _stage_pill(STAGE_DISPLAY[name][1],
                    STAGE_DISPLAY[name][0],
                    STAGE_DISPLAY[name][2])
        for name in pill_stages
    )
    n_pills = len(pill_stages)

    params_html = _params_panel_html(params_blob) if params_blob else (
        '<p class="hint">Run dump_pipeline_params.py to populate this panel.</p>'
    )

    log_label, log_lines = _read_log_tail(run_dir, n_lines=40)
    log_html = "<br>".join(line.replace("<", "&lt;").replace(">", "&gt;")
                           for line in log_lines)
    # Hide the Log-tail card entirely when there is nothing to show (a fast CPU
    # stage that logged nothing) instead of rendering an empty box.
    log_card = ("" if not log_html.strip() else
                '<div class="log-card">'
                '<h3 style="margin:0 0 8px 0; font-size: 14px;">'
                f'Log tail — {log_label}</h3>'
                f'<div class="log-pre">{log_html}</div></div>')

    # G4: per-pole status grid (rendered into per_pole_html, used below).
    per_pole_html = ""
    sanity_path = out_html.parent / "output_sanity.json"
    if sanity_path.exists():
        try:
            _s = json.loads(sanity_path.read_text(encoding="utf-8"))
            pp = _s.get("per_pole_status", {}) or {}
            poles = pp.get("poles", []) or []
            summary = pp.get("summary", {}) or {}
            # Only show the Stage 3 per-pole grid once Stage 3 (pole-vec) has
            # actually started (done + running > 0). Validation runs never run
            # Stage 3, so an all-"pending" 575-pole grid was misleading.
            if poles and (summary.get("done", 0)
                          + summary.get("running", 0)) > 0:
                cells = []
                color_map = {"done": "#2e9b3a", "running": "#ffb000",
                             "pending": "#cccccc", "failed": "#d8221f"}
                for p in poles:
                    c = color_map.get(p["state"], "#cccccc")
                    title = f"{p['id']}: {p['state']}"
                    cells.append(
                        f'<div title="{title}" style="background:{c}; '
                        f'width:8px; height:8px; border-radius:1px;"></div>')
                per_pole_html = (
                    f'<div><b>Stage 3 per-pole:</b> '
                    f'<span class="state-pill state-done">'
                    f'{summary.get("done", 0)} done</span> · '
                    f'<span class="state-pill state-running">'
                    f'{summary.get("running", 0)} running</span> · '
                    f'<span class="state-pill state-pending">'
                    f'{summary.get("pending", 0)} pending</span> · '
                    f'<span class="hint">{summary.get("total", 0)} total</span>'
                    f'</div>'
                    f'<div style="display:grid; gap:1px; margin-top:6px; '
                    f'grid-template-columns: repeat(60, 8px);">'
                    f'{"".join(cells)}</div>'
                )
        except Exception as e:
            per_pole_html = f'<p class="hint">per-pole status parse error: {e}</p>'

    # G3: output sanity card.
    sanity_path = out_html.parent / "output_sanity.json"
    sanity_html = '<p class="hint">No output_sanity.json yet.</p>'
    if sanity_path.exists():
        try:
            sanity = json.loads(sanity_path.read_text(encoding="utf-8"))
            parts = []
            s1 = sanity.get("stage1", {})
            if s1.get("n_sources"):
                # Class distribution as inline bars
                dist = s1.get("class_distribution_pct", {})
                if dist:
                    CLASS_LABELS = {"0": "unclass", "2": "ground", "5": "veg",
                                    "6": "manmade", "14": "wire",
                                    "15": "tx-tower", "18": "pole"}
                    bars = []
                    for cls_id, pct in dist.items():
                        label = CLASS_LABELS.get(cls_id, f"cls{cls_id}")
                        bars.append(
                            f'<span style="display:inline-block; '
                            f'background:#e2e2e6; padding:1px 6px; margin:1px; '
                            f'border-radius:3px; font-size:11px;">'
                            f'{label} <b>{pct}%</b></span>')
                    parts.append(
                        f'<div><b>Stage 1 class mix</b> '
                        f'(sampled {s1.get("sampled_pts_first3", 0):,} pts '
                        f'from {s1.get("files_sampled", 0)} of '
                        f'{s1.get("n_sources", 0)} sources):</div>'
                        f'<div style="margin-top:4px;">{" ".join(bars)}</div>')
                if s1.get("estimated_total_pts_all_files"):
                    parts.append(
                        f'<div class="hint">Estimated total classified points: '
                        f'~{s1["estimated_total_pts_all_files"]:,}</div>')
            s2 = sanity.get("stage2", {})
            if s2:
                parts.append(
                    f'<div><b>Stage 2</b>: '
                    f'{s2.get("n_pole_candidates", 0):,} candidates, '
                    f'{s2.get("n_crop_las_files", 0):,} crop files</div>')
            s3 = sanity.get("stage3", {})
            if s3:
                s3_bits = [f'{s3.get("n_poletops_dirs", 0):,} pole-top dirs']
                if "n_body_lines" in s3:
                    s3_bits.append(f'{s3["n_body_lines"]:,} body lines')
                for k in ("n_wires", "n_crossarms", "n_transformers"):
                    if k in s3:
                        s3_bits.append(f'{s3[k]:,} {k[2:]}')
                parts.append(f'<div><b>Stage 3</b>: {" · ".join(s3_bits)}</div>')
            s4 = sanity.get("stage4", {})
            if s4:
                bits = []
                if "n_curblines" in s4:
                    bits.append(f'{s4["n_curblines"]:,} curblines '
                                f'({s4.get("total_curbline_units", 0)} units)')
                if "n_eop" in s4:
                    bits.append(f'{s4["n_eop"]:,} EOP')
                parts.append(f'<div><b>Stage 4</b>: {" · ".join(bits)}</div>')
            parts.append(f'<div class="hint">Computed at {sanity.get("computed_at","?")}</div>')
            sanity_html = "\n".join(parts) if parts else sanity_html
        except Exception as e:
            sanity_html = f'<p class="hint">output_sanity.json parse error: {e}</p>'

    # Hide the Output-sanity card unless it carries real per-stage data (any
    # "<b>Stage N</b>" block). Early stages (0a) only have a timestamp -> skip.
    sanity_card = ("" if "<b>" not in sanity_html else
                   '<div class="log-card">'
                   '<h3 style="margin:0 0 8px 0; font-size: 14px;">'
                   'Output sanity</h3>'
                   f'{sanity_html}</div>')

    # G2: host resources card.
    res_path = out_html.parent / "resources.json"
    resources_html = '<p class="hint">No resources.json yet — run poll_resources.py.</p>'
    if res_path.exists():
        try:
            res = json.loads(res_path.read_text(encoding="utf-8"))
            parts = []
            for g in res.get("gpus", []):
                parts.append(
                    f'<div><b>GPU {g.get("index")}</b> {g.get("name", "?")}: '
                    f'<span style="color:#1c6dd0;">{g.get("util_pct", 0)}% util</span>, '
                    f'mem {g.get("mem_used_mib", 0)}/{g.get("mem_total_mib", 0)} MiB '
                    f'({g.get("mem_pct", 0)}%), '
                    f'{g.get("temperature_c", 0)} °C, '
                    f'{g.get("power_w") or "—"} W</div>')
            if not res.get("gpus"):
                parts.append('<div><b>GPU</b>: nvidia-smi not available</div>')
            d = res.get("disk", {})
            disk_color = "#d8221f" if d.get("free_pct", 100) < 10 else "#1c6dd0"
            parts.append(
                f'<div><b>Disk</b> ({d.get("path","?")}): '
                f'<span style="color:{disk_color};">'
                f'{d.get("free_gb",0)} GB free</span> of {d.get("total_gb",0)} GB '
                f'({d.get("free_pct",0)}% free)</div>')
            r = res.get("ram", {})
            if "error" not in r:
                parts.append(
                    f'<div><b>RAM</b>: {r.get("available_gb",0)} GB available of '
                    f'{r.get("total_gb",0)} GB ({r.get("used_pct",0)}% used)</div>')
            dock = res.get("docker", {})
            if dock.get("available"):
                containers = dock.get("active_containers", [])
                if containers:
                    inner = "<ul style='margin:4px 0 0 18px;'>"
                    for c in containers:
                        inner += (f'<li><code>{c.get("name","?")}</code> · '
                                  f'{c.get("status","?")} · '
                                  f'{c.get("image","?").split("/")[-1]}</li>')
                    inner += "</ul>"
                    parts.append(f'<div><b>Docker</b>: {len(containers)} active'
                                 f'{inner}</div>')
                else:
                    parts.append('<div><b>Docker</b>: no active containers</div>')
            parts.append(f'<div class="hint">Polled at {res.get("polled_at","?")}</div>')
            resources_html = "\n".join(parts)
        except Exception as e:
            resources_html = f'<p class="hint">resources.json parse error: {e}</p>'

    # Wrap per-pole grid in a card div (only if non-empty so we don't
    # show an empty card on Stage 1-only or stage-3-not-started runs).
    per_pole_card = ""
    if per_pole_html:
        per_pole_card = (
            '<div class="log-card">'
            '<h3 style="margin:0 0 8px 0; font-size: 14px;">Per-pole status (Stage 3)</h3>'
            f'{per_pole_html}'
            '</div>'
        )

    # G14: curb-skill internal progress card.
    # Curb-skill has ~11 sub-stages (init/splits/tile-crop/corridor/thin/
    # ground/summary/features/inference/decode/curblines). We want one
    # checklist row per sub-stage with state badge + tiny detail string.
    curb_skill_card = ""
    if sanity_path.exists():
        try:
            _s = json.loads(sanity_path.read_text(encoding="utf-8"))
            csp = _s.get("curb_skill_progress", {}) or {}
            if csp.get("found"):
                items = csp.get("items", []) or []
                n_done = csp.get("n_done", 0)
                n_total = csp.get("n_total", len(items))
                # Color-coded badges matching the per-pole legend.
                rows = []
                for it in items:
                    state = it.get("state", "pending")
                    if state == "done":
                        badge_class, badge_text = "state-done", "done"
                    elif state == "running":
                        badge_class, badge_text = "state-running", "running"
                    elif state == "failed":
                        badge_class, badge_text = "state-failed", "failed"
                    else:
                        badge_class, badge_text = "state-pending", "pending"
                    detail = (it.get("detail") or "").replace("<", "&lt;")
                    label = (it.get("label") or it.get("key", "?")).replace("<", "&lt;")
                    rows.append(
                        f'<tr>'
                        f'<td style="padding:3px 8px 3px 0;">'
                        f'<span class="state-pill {badge_class}">{badge_text}</span>'
                        f'</td>'
                        f'<td style="padding:3px 0; font-weight:500;">{label}</td>'
                        f'<td style="padding:3px 0 3px 12px; color:#666; '
                        f'font-family: Consolas, monospace; font-size: 11px;">'
                        f'{detail}</td>'
                        f'</tr>'
                    )
                curb_skill_card = (
                    '<div class="log-card">'
                    '<h3 style="margin:0 0 8px 0; font-size: 14px;">'
                    f'Curb-skill progress '
                    f'<span class="hint">({n_done}/{n_total} done)</span>'
                    '</h3>'
                    '<table style="width:100%; border-collapse:collapse; font-size:12px;">'
                    f'{"".join(rows)}'
                    '</table>'
                    '<div class="hint" style="margin-top:6px;">'
                    f'artifacts root: <code>{csp.get("artifacts_root","?")}</code></div>'
                    '</div>'
                )
        except Exception as e:
            curb_skill_card = (
                '<div class="log-card">'
                '<h3 style="margin:0 0 8px 0; font-size: 14px;">Curb-skill progress</h3>'
                f'<p class="hint">curb-skill progress parse error: {e}</p>'
                '</div>'
            )

    cache_bust = int(time.time())
    updated = time.strftime("%Y-%m-%d %H:%M:%S")
    # Workflow-type label (Firmatek / Aecon / Unknown) + source data
    # directory name, both read from chain.yml. Header format is
    #   "{workflow_type} · {data_dir_name} · {run_id} - pipeline progress"
    # The data-dir name is the SOURCE directory the customer handed us
    # (e.g. "Verizon 20K LA 10" / "Mississauga_P1_Central_6_VERY_SMALL"),
    # NOT the preset name (which is an SDAI-internal slug like
    # "verizon_la_zone10"). Falls back to the preset name when neither
    # source path is set in chain.yml.
    # Workflow type from the single shared classifier (also used by the
    # matplotlib title + the done-state banner). The positive-signal /
    # launch-window-race handling lives inside _detect_workflow.
    workflow_type = _detect_workflow(run_dir, params_blob)
    data_dir_name = run_dir.parent.name  # fallback = preset name
    _so_hdr, _csv_in_hdr, _src_lst_hdr = _chain_signals(run_dir)
    if _csv_in_hdr:
        data_dir_name = Path(_csv_in_hdr).parent.name
    elif _src_lst_hdr:
        data_dir_name = Path(_src_lst_hdr).parent.name
    run_name = f"{workflow_type} · {data_dir_name} · {run_dir.name}"
    run_id_disp = run_dir.name
    # Customer project folder = the dir that contains _runs/<ts> or
    # _stage_runs/<id> (e.g. "La Verne + Ontario Small"); shown in the title so
    # an operator can tell which project a dashboard tab belongs to. Falls back
    # to the immediate parent for flat <project>/<ts> layouts.
    project_folder = (run_dir.parent.parent.name
                      if run_dir.parent.name in ("_runs", "_stage_runs")
                      else run_dir.parent.name)
    project_folder = (project_folder.replace("&", "&amp;")
                      .replace("<", "&lt;").replace(">", "&gt;"))
    # Page title: "<workflow type>: <project directory name>".
    header_title = f"{workflow_type}: {project_folder}"

    # Sidecar pole-crops PNG (Stage 2 phase 2). When present, embed
    # below the main map. Sized full-width so the per-pole granularity
    # is visible.
    pole_crops_png = out_html.parent / "pole_crops.png"
    if pole_crops_png.exists():
        pole_crops_section = (
            '<h3 style="margin:0 0 6px 0; font-size: 14px;">'
            'Per-pole crops — point density</h3>'
            f'<img src="pole_crops.png?t={cache_bust}" alt="pole crops">'
            '<div class="legend-note">'
            'One square per pole crop, filled green with opacity scaled to '
            'the LiDAR point density inside it (denser = more solid green). '
            'Red = no crop written.'
            '</div>'
        )
    else:
        pole_crops_section = ""

    # Stage 0b span-statistics thumbnail card (added 2026-05-27). Sits
    # below the main map; renders only when viz/span_statistics.png
    # exists (Stage 0b done). Shows the existing 2-panel PNG (nearest-
    # neighbor histogram + pole coverage vs LAZ extent map) at small
    # size with a one-line summary pulled from span_statistics.json
    # and links to the PDF/HTML versions for the full report.
    span_stats_card = ""
    try:
        sp_json_path = out_html.parent / "span_statistics.json"
        sp_png_path = out_html.parent / "span_statistics.png"
        if sp_png_path.exists():
            summary_line = ""
            if sp_json_path.exists():
                try:
                    sp = json.loads(sp_json_path.read_text(encoding="utf-8"))
                    nin = sp.get("n_inside_laz_extent", "?")
                    nout = sp.get("n_outside_laz_extent", "?")
                    rec = sp.get("recommended_crop_half_size_m")
                    nn = sp.get("nearest_neighbor_m", {})
                    p95 = nn.get("p95")
                    parts = [f"{nin} inside / {nout} outside LAZ extent"]
                    if p95 is not None:
                        parts.append(f"p95 nearest-neighbor {p95:.1f}")
                    if rec is not None:
                        parts.append(f"recommended crop {rec:.0f}")
                    summary_line = (
                        f'<div class="span-summary">'
                        f'<small>{" · ".join(parts)}</small></div>'
                    )
                except Exception as e:
                    summary_line = (
                        f'<div class="span-summary">'
                        f'<small>span_statistics.json parse error: {e}</small></div>'
                    )
            # Optional links to full report formats (only if the files exist).
            link_bits = []
            if (out_html.parent / "span_statistics.pdf").exists():
                link_bits.append('<a href="span_statistics.pdf" target="_blank">PDF</a>')
            if (out_html.parent / "span_statistics.html").exists():
                link_bits.append('<a href="span_statistics.html" target="_blank">HTML</a>')
            if link_bits:
                summary_line += (
                    f'<div class="span-links"><small>full report: '
                    f'{" &middot; ".join(link_bits)}</small></div>'
                )
            span_stats_card = (
                '<div class="log-card span-card">'
                '<h3 style="margin:0 0 8px 0; font-size: 14px;">'
                f'{STAGE_LABELS["stage0b_span_stats"]}</h3>'
                f'<img src="span_statistics.png?t={cache_bust}" '
                f'alt="span statistics" class="span-img">'
                f'{summary_line}</div>'
            )
    except Exception as e:
        print(f"WARN building span-stats card: {e}")

    # Stage 1 PointCONV inference-progress card (added 2026-05-27 for
    # Wave B, task #138). Renders when viz/pointconv_inference_progress.json
    # exists — that file is written by the streaming inference script
    # (Wave B's classification_from_patches.py) on every Nth patch.
    # The card shows per-pole progress + aggregate rate + ETA. Sits
    # alongside the Stage 0b span-statistics card (also a sidecar
    # visualization below the main map).
    pointconv_card = ""
    try:
        pc_progress_json = run_dir / "viz" / "pointconv_inference_progress.json"
        if pc_progress_json.exists():
            pc_data = json.loads(pc_progress_json.read_text(encoding="utf-8"))
            n_poles_total = int(pc_data.get("n_poles_total", 0))
            n_poles_done = int(pc_data.get("n_poles_done", 0))
            n_patches_total = int(pc_data.get("n_patches_total", 0))
            n_patches_done = int(pc_data.get("n_patches_done", 0))
            current_pole = pc_data.get("current_pole", "")
            rate = float(pc_data.get("patches_per_sec", 0.0))
            eta_sec = pc_data.get("eta_sec")
            pct = (100.0 * n_patches_done / n_patches_total
                   if n_patches_total else 0.0)
            eta_str = (f"{int(eta_sec // 60)}m {int(eta_sec % 60)}s"
                       if eta_sec else "—")
            # Per-pole bar list: pending (gray) + done (green) + current (amber)
            per_pole = pc_data.get("per_pole", {})
            bars_html = ""
            for pole_id in sorted(per_pole.keys()):
                p = per_pole[pole_id]
                ntot = int(p.get("n_patches", 0)) or 1
                ndone = int(p.get("n_done", 0))
                ppct = 100.0 * ndone / ntot
                state = p.get("state", "pending")
                bar_color = ("#2e9b3a" if state == "done"
                             else "#ffb000" if state == "running"
                             else "#bbbbbb")
                bars_html += (
                    f'<div class="ppc-row">'
                    f'<div class="ppc-label">{pole_id}</div>'
                    f'<div class="ppc-bar-bg">'
                    f'<div class="ppc-bar" style="width:{ppct:.0f}%;'
                    f'background:{bar_color};"></div>'
                    f'</div>'
                    f'<div class="ppc-val">{ndone}/{ntot}</div></div>'
                )
            pointconv_card = (
                f'<div class="log-card span-card">'
                f'<h3 style="margin:0 0 8px 0; font-size: 14px;">'
                f'{STAGE_LABELS["stage1_pointconv"]} (Wave B streaming)</h3>'
                f'<div class="stat-grid" style="display:grid;'
                f'grid-template-columns:repeat(4,1fr);gap:8px;'
                f'margin-bottom: 10px;">'
                f'<div class="stat-card">'
                f'<div style="font-size:18px;font-weight:600;">'
                f'{pct:.0f}%</div>'
                f'<div style="font-size:10px;color:#666;">'
                f'overall progress</div></div>'
                f'<div class="stat-card">'
                f'<div style="font-size:18px;font-weight:600;color:#1a6e2a;">'
                f'{n_poles_done}/{n_poles_total}</div>'
                f'<div style="font-size:10px;color:#666;">poles done</div>'
                f'</div>'
                f'<div class="stat-card">'
                f'<div style="font-size:18px;font-weight:600;">'
                f'{rate:.1f}</div>'
                f'<div style="font-size:10px;color:#666;">patches/sec</div>'
                f'</div>'
                f'<div class="stat-card">'
                f'<div style="font-size:18px;font-weight:600;color:#1c6dd0;">'
                f'{eta_str}</div>'
                f'<div style="font-size:10px;color:#666;">ETA</div>'
                f'</div>'
                f'</div>'
                f'<div style="font-size:11px;color:#555;margin-bottom:6px;">'
                f'current pole: <code>{current_pole or "(idle)"}</code> &middot; '
                f'{n_patches_done:,} / {n_patches_total:,} patches</div>'
                f'<div class="ppc-list">{bars_html}</div>'
                f'</div>'
            )
    except Exception as e:
        print(f"WARN building pointconv inference card: {e}")

    # Stage 2 detection-report card (added 2026-05-27). Renders once
    # <run_dir>/<project>_detection/ exists (i.e. build_detection_report.py
    # has run as part of Stage 2). Shows a small stats summary + direct
    # links to the HTML / PDF / CSV deliverables. Located between the
    # Stage 0b card and the Output sanity log card.
    detection_card = ""
    try:
        # Find the report folder via filesystem glob (the exact prefix
        # depends on the project name, which we don't have at render time).
        detection_dirs = [p for p in run_dir.iterdir()
                          if p.is_dir() and p.name.endswith("_detection")]
        if detection_dirs:
            detection_dir = sorted(detection_dirs)[0]
            project = detection_dir.name.removesuffix("_detection")
            # Pull summary counts from <project>_detection_results.csv.
            results_csv = detection_dir / f"{project}_detection_results.csv"
            n_total = n_found = n_not_found = n_no_las = 0
            if results_csv.exists():
                try:
                    import pandas as _pd
                    _df = _pd.read_csv(results_csv)
                    n_total = len(_df)
                    sc = _df["status"].value_counts().to_dict() \
                        if "status" in _df.columns else {}
                    n_found = int(sc.get("found", 0))
                    n_not_found = int(sc.get("not_found", 0))
                    n_no_las = int(sc.get("no_las_file", 0))
                except Exception:
                    pass
            # Extra coverage stats: per-pole crop boxes + corridor spans
            # come from shapefiles in the detection folder (Stage 2 wrote
            # them explicitly via build_detection_report). Counts here
            # mirror the "[report] crop_boxes -> ..." log line.
            n_crops = 0
            n_spans = 0
            total_span_m = 0.0
            try:
                import geopandas as _gpd
                # crop_boxes + corridor_spans shapefiles live in the
                # pole_crops_and_corridors/ subdir (Stage 2 deliverables
                # layout). Older runs put them at the detection-folder top
                # level — check both so the card works either way.
                def _find_shp(stem):
                    for cand in (
                        detection_dir / "pole_crops_and_corridors" / f"{stem}.shp",
                        detection_dir / f"{stem}.shp",
                    ):
                        if cand.exists():
                            return cand
                    return None
                cb_shp = _find_shp(f"{project}_crop_boxes")
                if cb_shp:
                    n_crops = len(_gpd.read_file(cb_shp))
                cs_shp = _find_shp(f"{project}_corridor_spans")
                if cs_shp:
                    cs_gdf = _gpd.read_file(cs_shp)
                    n_spans = len(cs_gdf)
                    # Sum line lengths in the SHP's native units. Most
                    # Firmatek SHPs are in EPSG:6424 ftUS, so total_span_m
                    # below is actually feet — convert if so.
                    try:
                        total_native = float(cs_gdf.length.sum())
                        crs = cs_gdf.crs.to_string() if cs_gdf.crs else ""
                        # Cheap unit guess: ftUS EPSG codes for California
                        # State Plane (Firmatek). Anything else: assume meters.
                        if any(c in crs for c in ("6424", "6425", "2229", "ftUS")):
                            total_span_m = total_native * 0.3048006096
                        else:
                            total_span_m = total_native
                    except Exception:
                        total_span_m = 0.0
            except Exception as e:
                print(f"WARN detection-card crop/span counts: {e}")

            # Links to deliverables (only emit for files that exist).
            # Paths are RELATIVE to the dashboard's <base href="/view/<run_id>/">
            # so "<detection_dir>/<file>" resolves to the asset route, which
            # now serves files from the run-dir root (not just viz/). No "../"
            # — that would escape /view/<run_id>/ and 404.
            link_bits = []
            html_rel = f"{detection_dir.name}/{project}_detection_summary.html"
            pdf_rel = f"{detection_dir.name}/{project}_detection_summary.pdf"
            results_rel = f"{detection_dir.name}/{project}_detection_results.csv"
            if (detection_dir / f"{project}_detection_summary.pdf").exists():
                link_bits.append(f'<a href="{pdf_rel}" target="_blank">PDF</a>')
            if (detection_dir / f"{project}_detection_summary.html").exists():
                link_bits.append(f'<a href="{html_rel}" target="_blank">HTML</a>')
            if (detection_dir / f"{project}_detection_results.csv").exists():
                link_bits.append(f'<a href="{results_rel}" target="_blank">CSV</a>')
            links_html = ""
            if link_bits:
                links_html = (
                    f'<div class="span-links"><small>'
                    f'full report: {" &middot; ".join(link_bits)}</small></div>'
                )
            # Format span length for display (km if >= 1000 m).
            span_label = (f"{total_span_m/1000.0:.2f} km"
                          if total_span_m >= 1000.0 else
                          (f"{total_span_m:.0f} m" if total_span_m > 0 else "—"))
            detection_card = (
                f'<div class="log-card span-card">'
                f'<h3 style="margin:0 0 8px 0; font-size: 14px;">'
                f'{STAGE_LABELS["stage2_pole_crop"].replace("&", "&amp;")}</h3>'
                f'<div class="stat-grid" style="display:grid;'
                f'grid-template-columns:repeat(4,1fr);gap:8px;">'
                f'<div class="stat-card">'
                f'<div style="font-size:18px;font-weight:600;">{n_total}</div>'
                f'<div style="font-size:10px;color:#666;">total poles</div></div>'
                f'<div class="stat-card">'
                f'<div style="font-size:18px;font-weight:600;color:#1a6e2a;">{n_found}</div>'
                f'<div style="font-size:10px;color:#666;">detected</div></div>'
                f'<div class="stat-card">'
                f'<div style="font-size:18px;font-weight:600;color:#934700;">{n_not_found}</div>'
                f'<div style="font-size:10px;color:#666;">not found</div></div>'
                f'<div class="stat-card">'
                f'<div style="font-size:18px;font-weight:600;color:#888;">{n_no_las}</div>'
                f'<div style="font-size:10px;color:#666;">outside LAZ</div></div>'
                f'</div>'
                f'<div class="stat-grid" style="display:grid;'
                f'grid-template-columns:repeat(2,1fr);gap:8px;margin-top:8px;">'
                f'<div class="stat-card">'
                f'<div style="font-size:18px;font-weight:600;color:#4a7a4c;">{n_crops}</div>'
                f'<div style="font-size:10px;color:#666;">per-pole crop boxes</div></div>'
                f'<div class="stat-card">'
                f'<div style="font-size:18px;font-weight:600;color:#1c6dd0;">{n_spans}'
                f'<span style="font-size:11px;color:#666;font-weight:400;"> &middot; {span_label}</span>'
                f'</div>'
                f'<div style="font-size:10px;color:#666;">corridor spans (total length)</div></div>'
                f'</div>'
                f'<div class="span-summary" style="margin-top:8px;">'
                f'<small>report folder: <code>{detection_dir.name}/</code></small></div>'
                f'{links_html}</div>'
            )
    except Exception as e:
        print(f"WARN building detection card: {e}")

    # Searched-but-not-found crop previews card (added 2026-05-30): the per-pole
    # preview PNGs from <project>_detection/Searched_But_Not_Found_Poles/.
    not_found_previews_card = ""
    try:
        detection_dirs = [p for p in run_dir.iterdir()
                          if p.is_dir() and p.name.endswith("_detection")]
        if detection_dirs:
            detection_dir = sorted(detection_dirs)[0]
            nf_dir = detection_dir / "Searched_But_Not_Found_Poles"
            nf_pngs = _pole_files(nf_dir, "png")
            if nf_pngs:
                # Copy each preview PNG VIZ-LOCAL (prefixed nf_) + link the bare
                # filename, so the images resolve both under file:// (relative
                # to viz/) and the Flask UI (its asset route serves viz/). The
                # previous src was run-dir-relative -> broke under file://.
                import shutil as _sh_nf
                _viz_nf = run_dir / "viz"
                _tiles = []
                for p in nf_pngs:
                    _vn = f"nf_{p.name}"
                    try:
                        _sh_nf.copy2(p, _viz_nf / _vn)
                    except Exception:
                        pass
                    _tiles.append(
                        f'<figure style="margin:0;flex:1 1 320px;max-width:49%;">'
                        f'<img src="{_vn}?t={cache_bust}" alt="{p.stem}" '
                        f'style="width:100%;border:1px solid #d8dce2;'
                        f'border-radius:6px;">'
                        f'<figcaption style="font-size:11px;color:#666;">'
                        f'{p.stem}</figcaption></figure>')
                tiles = "".join(_tiles)
                not_found_previews_card = (
                    '<div class="log-card">'
                    '<h3 style="margin:0 0 6px 0; font-size:14px;">'
                    'Searched but not found &mdash; crop previews</h3>'
                    '<div style="font-size:12px;color:#666;margin-bottom:8px;">'
                    'Plan view + elevation profile for each surveyed pole '
                    'inside coverage that was not detected (red + = surveyed '
                    'location). Point clouds with a red survey marker: '
                    f'<code>{detection_dir.name}/Searched_But_Not_Found_Poles/'
                    '</code>.</div>'
                    '<div style="display:flex;flex-wrap:wrap;gap:12px;">'
                    f'{tiles}</div></div>')
    except Exception as e:
        print(f"WARN building not-found previews card: {e}")

    # Stage 2 internal-phase checklist (Tile/Crop/Detect/Corridor/Report).
    try:
        stage2_phase_card = _stage2_phase_card(run_dir)
    except Exception as e:
        print(f"WARN building stage2 phase card: {e}")
        stage2_phase_card = ""

    # run_manifest.json pieces for the (single) Deliverables card built
    # further below: item links + found/missed summary + peak-RAM line.
    # 2026-06-07: this used to build a standalone card here that was then
    # silently OVERWRITTEN by the per-stage _deliv_rows card — the two are
    # now merged into ONE card (see "Single Deliverables card" below). All
    # manifest paths are run-dir-relative posix (no '../'), which is exactly
    # what the Flask asset route under <base href="/view/<run_id>/"> serves.
    _man_items_html = ""
    _man_summ_html = ""
    _man_res_html = ""
    try:
        import json as _json
        mf = run_dir / "run_manifest.json"
        if mf.is_file():
            man = _json.loads(mf.read_text(encoding="utf-8"))
            s = man.get("summary", {}) or {}
            deliv = man.get("deliverables", []) or []

            def _dl_item(dd):
                lbl, pth = dd.get("label", ""), dd.get("path", "")
                if dd.get("kind") == "folder":
                    return f'<li>{lbl}: <code>{pth}/</code></li>'
                return (f'<li><a href="{pth}?t={cache_bust}" target="_blank" '
                        f'rel="noopener">{lbl}</a> '
                        f'<span style="color:#999;font-size:11px;">{pth}</span></li>')

            ordered = [d for d in deliv if d.get("primary")] + \
                      [d for d in deliv if not d.get("primary")]
            if s:
                _man_summ_html = (
                    '<div style="font-size:13px;margin:6px 0 2px 0;">'
                    f'<span style="color:#1a6e2a;font-weight:600;">Poles found: {s.get("found",0)}</span>'
                    ' &nbsp;&middot;&nbsp; '
                    f'<span style="color:#934700;font-weight:700;">Poles missed (searched but not found): {s.get("missed",0)}</span>'
                    ' &nbsp;&middot;&nbsp; '
                    f'<span style="color:#888;">Outside LAZ coverage: {s.get("outside_coverage",0)}</span>'
                    f' &nbsp;&middot;&nbsp; <span style="color:#555;">total {s.get("poles_total",0)}</span>'
                    '</div>')
            # Lead with curbs + roads — the corridor workflow's key
            # deliverables — above the (secondary) pole-detection summary.
            _key_html = _curb_road_summary_html(run_dir)
            if _key_html:
                _man_summ_html = _key_html + _man_summ_html
            # Peak RAM the run used (container cgroup high-water, from manifest).
            res = man.get("resources", {}) or {}
            if res.get("peak_ram_gb"):
                lim = res.get("container_limit_gb")
                _man_res_html = (
                    '<div style="font-size:12px;color:#555;margin-bottom:6px;">'
                    f'Peak RAM this run: <b>{res["peak_ram_gb"]:.1f} GB</b>'
                    + (f' &middot; container limit {lim:.0f} GB' if lim
                       else ' &middot; no container cap')
                    + '</div>')
            _man_items_html = "".join(_dl_item(d) for d in ordered)
    except Exception as e:
        print(f"WARN building deliverables manifest section: {e}")

    # Stage 3.5 utility-pole-network card (added 2026-05-28). Renders when
    # <run_dir>/<project>_detection/utility_topology/*_pole_network_summary.json
    # exists — i.e. after estimate_pole_network.py runs. Shows: poles, spans,
    # components, isolated count, bridge count, QC near-duplicates, plus a
    # small preview thumbnail and links to the shapefiles.
    topology_card = ""
    try:
        detection_dirs = [p for p in run_dir.iterdir()
                          if p.is_dir() and p.name.endswith("_detection")]
        if detection_dirs:
            detection_dir = sorted(detection_dirs)[0]
            project = detection_dir.name.removesuffix("_detection")
            topo_dir = detection_dir / "utility_topology"
            summary_json = topo_dir / f"{project}_pole_network_summary.json"
            if summary_json.exists():
                ts = json.loads(summary_json.read_text(encoding="utf-8"))
                t = ts.get("topology", {})
                q = ts.get("qc", {})
                n_poles = int(t.get("n_nodes", 0))
                n_spans = int(t.get("n_edges", 0))
                n_comp = int(t.get("n_components", 0))
                n_iso = len(t.get("isolated_poles", []))
                n_bridges = int(t.get("n_bridges_added", 0))
                n_dups = int(q.get("n_near_duplicate_pairs", 0))
                span = t.get("span_length_m", {})
                avg_span = float(span.get("mean", 0.0))
                # Links to deliverables (relative URLs from viz/progress.html
                # to the detection folder).
                nodes_shp = topo_dir / \
                    f"{project}_pole_network_nodes.shp"
                edges_shp = topo_dir / \
                    f"{project}_pole_network_edges.shp"
                preview_png = topo_dir / \
                    f"{project}_pole_network_preview.png"
                readme_md = topo_dir / "README.md"
                # Run-dir-relative hrefs, NO '../' (same idiom as the
                # detection card): the Flask asset route serves run-dir
                # root paths under <base href="/view/<run_id>/">, and a
                # '../' would escape the base tag and 404.
                link_bits = []
                if nodes_shp.exists():
                    link_bits.append(
                        f'<a href="{detection_dir.name}/utility_topology/'
                        f'{nodes_shp.name}" target="_blank">nodes.shp</a>')
                if edges_shp.exists():
                    link_bits.append(
                        f'<a href="{detection_dir.name}/utility_topology/'
                        f'{edges_shp.name}" target="_blank">edges.shp</a>')
                if readme_md.exists():
                    link_bits.append(
                        f'<a href="{detection_dir.name}/utility_topology/'
                        f'README.md" target="_blank">README</a>')
                links_html = ""
                if link_bits:
                    links_html = (
                        f'<div class="span-links"><small>'
                        f'deliverables: {" &middot; ".join(link_bits)}'
                        f'</small></div>')
                # Optional thumbnail. Copy the PNG into viz/ so Flask can
                # serve it via the asset route (relative ../ paths don't
                # resolve through Flask's viz/-scoped /view/<id>/<asset>).
                preview_html = ""
                if preview_png.exists():
                    try:
                        import shutil as _shutil
                        viz_preview = run_dir / "viz" / "topology_preview.png"
                        _shutil.copyfile(preview_png, viz_preview)
                        preview_html = (
                            f'<div style="margin-top:8px;">'
                            f'<a href="topology_preview.png" target="_blank">'
                            f'<img src="topology_preview.png" '
                            f'style="max-width:100%;height:auto;'
                            f'border:1px solid #ddd;border-radius:4px;">'
                            f'</a></div>'
                        )
                    except Exception:
                        # Fallback: run-dir-relative path (no '../' — served
                        # by the Flask asset route under the /view base tag).
                        preview_rel = (
                            f"{detection_dir.name}/utility_topology/"
                            f"{preview_png.name}")
                        preview_html = (
                            f'<div style="margin-top:8px;">'
                            f'<a href="{preview_rel}" target="_blank">'
                            f'<img src="{preview_rel}" style="max-width:100%;'
                            f'height:auto;border:1px solid #ddd;'
                            f'border-radius:4px;"></a></div>'
                        )
                topology_card = (
                    f'<div class="log-card span-card">'
                    f'<h3 style="margin:0 0 8px 0; font-size: 14px;">'
                    f'{STAGE_LABELS["stage3_5_pole_network"]}</h3>'
                    f'<div class="stat-grid" style="display:grid;'
                    f'grid-template-columns:repeat(4,1fr);gap:8px;">'
                    f'<div class="stat-card">'
                    f'<div style="font-size:18px;font-weight:600;">'
                    f'{n_poles}</div>'
                    f'<div style="font-size:10px;color:#666;">poles</div>'
                    f'</div>'
                    f'<div class="stat-card">'
                    f'<div style="font-size:18px;font-weight:600;'
                    f'color:#1c6dd0;">{n_spans}</div>'
                    f'<div style="font-size:10px;color:#666;">spans</div>'
                    f'</div>'
                    f'<div class="stat-card">'
                    f'<div style="font-size:18px;font-weight:600;">'
                    f'{n_comp}</div>'
                    f'<div style="font-size:10px;color:#666;">components</div>'
                    f'</div>'
                    f'<div class="stat-card">'
                    f'<div style="font-size:18px;font-weight:600;color:'
                    f'{("#1a6e2a" if n_iso == 0 else "#934700")};">{n_iso}</div>'
                    f'<div style="font-size:10px;color:#666;">isolated</div>'
                    f'</div>'
                    f'</div>'
                    f'<div class="span-summary" style="margin-top:8px;">'
                    f'<small>avg span <strong>{avg_span:.1f} m</strong>'
                    f' &middot; {n_bridges} bridge edge(s) added'
                    f' &middot; {n_dups} QC near-duplicate pair(s)</small>'
                    f'</div>'
                    f'{links_html}'
                    f'{preview_html}'
                    f'</div>'
                )
    except Exception as e:
        print(f"WARN building topology card: {e}")

    # Optional "current stage" subtitle from orchestrator status.
    current_stage = orch_status.get("current_stage")
    overall_state = orch_status.get("overall_state")
    sub_extra = ""
    if current_stage:
        sub_extra = f' &middot; Orchestrator: <strong>{overall_state or "?"}</strong> on <code>{current_stage}</code>'
    elif overall_state == "done":
        sub_extra = ' &middot; Orchestrator: <strong>done</strong> ✓'
    elif overall_state == "failed":
        sub_extra = ' &middot; Orchestrator: <strong style="color:#d8221f;">FAILED</strong>'

    # Per-stage deliverables to link (mirrors STAGE_SPECS[stage].deliverables in
    # chain_orchestrator.py; this renderer lives in another project so the map
    # is kept here -- add a stage's human-facing reports below). Each is made
    # VIZ-LOCAL by _viz_local_link(): if it isn't already under viz/, copy it
    # from 00_inputs/, then link the bare filename so it resolves both file://
    # (relative to viz/) AND the Flask UI (which injects <base href>). Defined
    # here (above the banner) so both the done-state banner's Deliverables
    # section and the per-stage gallery below share one source of truth.
    import shutil as _shutil
    _viz = run_dir / "viz"
    # Detection folder (project-prefixed) for stage-3.5 topology deliverables.
    _det0 = next((p for p in sorted(run_dir.iterdir())
                  if p.is_dir() and p.name.endswith("_detection")), None)
    _proj0 = _det0.name.removesuffix("_detection") if _det0 else ""
    # One "Interactive Map" (the stage-0t map carries EVERY vector layer with
    # a top-right layer control; re-rendered at stages 0u/4/6). When it exists
    # the per-stage "view on map" duplicates are suppressed. Firmatek chains
    # have no trajectory map — their stage-2 detection map (also a top-right
    # layer control: poles by status, crop boxes, density) gets the SAME
    # button (tester request 2026-06-10: match Aecon Data Validation).
    _has_imap = (_viz / "stage0t_trajectory_map.html").is_file()
    _imap_href = ("stage0t_trajectory_map.html" if _has_imap
                  else "stage2_detection_map.html"
                  if (_viz / "stage2_detection_map.html").is_file() else None)
    _stage_deliverables = {
        "stage0t_trajectory": [("stage0t_report.html", "QC report (HTML)"),
                               ("stage0t_report.pdf", "QC report (PDF)"),
                               ("stage0t_trajectory_map.html", "Interactive Map (all layers)"),
                               ("trajectory.gpkg", "trajectory (GeoPackage)")],
        "stage0a_pole_csv": [("pole_ingestion_report.pdf", "pole-ingestion report (PDF)"),
                             ("pole_ingestion_report.csv", "report (CSV)")],
        "stage0b_span_stats": [("span_statistics.pdf", "span report (PDF)"),
                               ("span_statistics.html", "span report (HTML)")],
        "stage1a_wire_pole": [("stage1a_wire_pole_summary.json",
                               "wire/pole cleanup summary (JSON)")],
        "stage0u_corridor_crop": [("stage0u_corridor_density.png",
                                   "corridor density (PNG)")],
        "stage2_pole_crop": [("stage2_crop_density.png",
                              "crop density (PNG)")],
        # Late stages (2026-06-07): run-dir-relative paths, NO '../' — the
        # Flask asset route serves <run_dir>/<path> under the /view base
        # tag. Filenames verified against chain_orchestrator.py outputs.
        "stage3_5_pole_network": ([] if not _det0 else [
            (f"{_det0.name}/utility_topology/{_proj0}_pole_network_nodes.shp",
             "pole-network nodes (SHP)"),
            (f"{_det0.name}/utility_topology/{_proj0}_pole_network_edges.shp",
             "pole-network edges (SHP)")]),
        # Full-mode pole-vec deliverables (Firmatek full chain). Absent files
        # self-suppress via _viz_local_link, so Aecon body-only runs (which
        # write no PoleVec/Combined/) show nothing extra. Added 2026-06-11:
        # wires/crossarms/transformers were filesystem-only — the Firmatek
        # full chain's headline outputs never reached the dashboard.
        "stage3_pole_vec": [
            ("PoleVec/Combined/Grp0_Body_Lines.shp", "pole body lines (SHP)"),
            ("PoleVec/Combined/Grp0_Span_Wires.shp",
             "wires — span-stitched catenaries (SHP)"),
            ("PoleVec/Combined/Grp0_Parabola_Wires.shp",
             "wires — catenary (SHP)"),
            ("PoleVec/Combined/Grp0_Linear_Wires.shp",
             "wires — straight (SHP)"),
            ("PoleVec/Combined/Grp0_Guy_Lines.shp", "guy wires (SHP)"),
            ("PoleVec/Combined/Grp0_Crossarm_Lines.shp", "crossarms (SHP)"),
            ("PoleVec/Combined/Grp0_Transformer_Lines.shp",
             "transformers (SHP)"),
            ("PoleVec/Combined/Grp0_combined.dxf", "combined CAD (DXF)")],
        "stage4_curbs": [("04_curbs/viz/curblines.shp", "curb lines (SHP)"),
                         ("04_curbs/viz/eop.shp", "edge of pavement (SHP)")],
        "stage4w_building_walls": [
            ("04w_building_walls/Building_Walls.shp", "building walls (SHP)")],
        "stage4t_tree_trunk_canopy": [
            ("04t_tree_trunk_canopy/Tree_Stems.shp", "tree trunks (SHP)"),
            ("04t_tree_trunk_canopy/Tree_Canopy.shp", "tree canopy (SHP)")],
        "stage4sg_sidewalks_geom": [
            ("04sg_sidewalks_geom/Sidewalk_Geom_Edges.shp",
             "geometric sidewalks (SHP)")],
        "stage5_road_surface": [("05_road_surface/road_surface.shp",
                                 "road surface (SHP)")],
        "stage6_final_classification": [
            ("stage6_final_density.png", "final density (PNG)"),
            ("tester_deliverables.gpkg", "tester deliverables (GPKG)"),
            ("TESTER_QGIS_PROJECT.qgz", "tester QGIS project (QGZ)")],
        "stage7_corridor_merge": [("07_corridor/corridor_classified.laz",
                                   "corridor LAZ (metric)")],
        "stage8_deliverables_to_ftus": [
            ("07_corridor_ftus/corridor_classified_ftus.laz",
             "corridor LAZ (ftUS)")],
        "stage9_solv3d_project": [
            ("TESTER_SOLV3D_PROJECT.json", "SOLV3D viewer project (JSON)"),
            ("arcgis/README_ARCGIS.md", "ArcGIS instructions (lyrx + builder)"),
            ("arcgis/MAKE_ARCGIS_PROJECT.py",
             "ArcGIS project builder (run in Pro)")],
    }

    def _viz_local_link(_fname, _label):
        """Return an <a> for a deliverable, or None when the file is absent.

        Two href flavors, both WITHOUT '../':
          * bare filename — viz-local file (copied from 00_inputs/ if
            needed); resolves under file:// AND the Flask UI.
          * run-dir-relative path (contains '/', or a run-dir-root file
            like tester_deliverables.gpkg) — linked as-is; the Flask
            asset route serves both viz/<asset> and <run_dir>/<asset>,
            while '../' would escape <base href="/view/<run_id>/"> → 404.
        """
        if "/" not in _fname:
            _dst = _viz / _fname
            if not _dst.is_file():
                _alt = run_dir / "00_inputs" / _fname
                if _alt.is_file():
                    try:
                        _shutil.copy2(_alt, _dst)
                    except Exception:
                        pass
            if _dst.is_file():
                return f'<a href="{_fname}" target="_blank">{_label}</a>'
        if (run_dir / _fname).is_file():
            return f'<a href="{_fname}" target="_blank">{_label}</a>'
        return None

    # --- Prominent status banner (D1 live activity + D5 post-run recap) ----
    # Always-visible bar above the map. While running it answers "what's
    # happening right now" (critical during Stage 2's long, file-silent
    # corridor-merge phase). When done it flips to a one-line recap: the
    # headline detection numbers for pole chains, or a Deliverables list for
    # chains (e.g. Aecon / a 0t-only run) that produce no detection results.
    status_banner = ""
    try:
        if overall_state == "failed":
            _fs = current_stage or "a stage"
            status_banner = (
                '<div style="margin:0 0 14px 0;padding:12px 16px;border-radius:8px;'
                'background:#fcebea;border:1px solid #f3b6b3;color:#831a17;'
                'font-size:14px;font-weight:600;">'
                f'&#10007; Chain FAILED on <code>{_fs}</code> — '
                'see the log tail below for the error.</div>'
            )
        elif overall_state == "done":
            # D5 recap — workflow-aware (2026-06-07): the old banner said
            # "Validation complete" for ANY chain with a detection CSV,
            # which was wrong for the Firmatek FULL and Aecon corridor
            # chains. workflow_type comes from _detect_workflow (shared
            # with the page header).
            ds = _load_detection_status(run_dir)
            n_tot = len(ds)
            n_fnd = sum(1 for v in ds.values() if v == "found")
            _green = ('margin:0 0 14px 0;padding:12px 16px;border-radius:8px;'
                      'background:#e7f7ea;border:1px solid #b6e3bf;color:#1a6e2a;'
                      'font-size:14px;font-weight:600;')

            def _deliv_links_done():
                _dl = []
                for _sk2 in [s.get("name") for s in
                             (orch_status.get("stages") or [])
                             if s.get("state") == "done" and s.get("name")]:
                    for _fn, _lab in _stage_deliverables.get(_sk2, []):
                        _a = _viz_local_link(_fn, _lab)
                        if _a:
                            _dl.append(_a)
                return _dl

            if workflow_type == "Firmatek" and n_tot:
                pct = 100.0 * n_fnd / max(n_tot, 1)
                status_banner = (
                    f'<div style="{_green}">'
                    f'&#10003; Full chain complete — '
                    f'<strong>{n_fnd}/{n_tot}</strong> poles detected '
                    f'({pct:.0f}%), deliverables below.</div>'
                )
            elif workflow_type == "Aecon Corridor":
                # Lead with the corridor's KEY deliverables — curbs + road
                # surface — then poles as a secondary line item.
                _m = _curb_road_metrics(run_dir)
                _bits = []
                if _m["curb_n"] is not None:
                    _l = f' ({_m["curb_m"]:.0f} m)' if _m["curb_m"] else ""
                    _bits.append(f'<strong>{_m["curb_n"]}</strong> curb line(s){_l}')
                if _m["eop_n"] is not None:
                    _l = f' ({_m["eop_m"]:.0f} m)' if _m["eop_m"] else ""
                    _bits.append(f'<strong>{_m["eop_n"]}</strong> EOP line(s){_l}')
                if _m["road_n"] is not None:
                    _bits.append(f'road surface <strong>{_m["road_ha"]:.2f} ha</strong>')
                if _m["wall_n"] is not None:
                    _l = f' ({_m["wall_m"]:.0f} m)' if _m["wall_m"] else ""
                    _bits.append(f'<strong>{_m["wall_n"]}</strong> building wall(s){_l}')
                if n_tot:
                    _bits.append(f'{n_fnd}/{n_tot} poles')
                _extra = (" — " + " &middot; ".join(_bits)) if _bits else ""
                status_banner = (
                    f'<div style="{_green}">'
                    f'&#10003; Full chain complete{_extra}. '
                    '<span style="font-weight:400;color:#3a7a46;">'
                    'See the Deliverables section below.</span></div>'
                )
            elif workflow_type == "Aecon Data Validation":
                _dl = _deliv_links_done()
                _body = ((' <span style="font-weight:400;color:#3a7a46;">'
                          '&middot; ' + " &middot; ".join(_dl) + "</span>")
                         if _dl else "")
                status_banner = (
                    f'<div style="{_green}">'
                    f'&#10003; QC complete — trajectory + corridor ready.'
                    f'{_body}</div>'
                )
            elif n_tot:
                # Firmatek Validation (and any unclassified chain with a
                # detection CSV): the original validation recap.
                pct = 100.0 * n_fnd / max(n_tot, 1)
                extra = (f" — <strong>{n_fnd}/{n_tot}</strong> poles detected "
                         f"({pct:.0f}%)")
                status_banner = (
                    f'<div style="{_green}">'
                    f'&#10003; Validation complete{extra}. '
                    '<span style="font-weight:400;color:#3a7a46;">Scroll down for '
                    'the detection report + per-pole results.</span></div>'
                )
            else:
                # No detection results (e.g. a 0t-only / unknown run): show
                # the actual deliverables in place of the detection pointer.
                _dl = _deliv_links_done()
                if _dl:
                    _body = ('<span style="font-weight:400;color:#3a7a46;"> &middot; '
                             + " &middot; ".join(_dl) + "</span>")
                else:
                    _body = ('<span style="font-weight:400;color:#3a7a46;"> '
                             'See the Deliverables section below.</span>')
                status_banner = (
                    f'<div style="{_green}">'
                    f'&#10003; Run complete &mdash; Deliverables:{_body}</div>'
                )
        else:
            # D1 live: current stage + parsed activity line.
            label = STAGE_LABELS.get(current_stage, current_stage or "starting…")
            activity = _latest_activity_line(run_dir)
            act_html = (f' <span style="font-weight:400;color:#1f4e7a;">&middot; '
                        f'{activity}</span>') if activity else ""
            status_banner = (
                '<div style="margin:0 0 14px 0;padding:12px 16px;border-radius:8px;'
                'background:#e8f1fb;border:1px solid #b6d3f0;color:#1c5aa0;'
                'font-size:14px;font-weight:600;">'
                f'&#9654; Running &middot; <strong>{label}</strong>{act_html}</div>'
            )
    except Exception as e:
        print(f"WARN building status banner: {e}")

    # Refresh strategy (2026-05-28): while the chain is live, the MAP image
    # is hot-swapped in place by JS every 5 s (preload-then-swap → no flash,
    # no scroll reset), and the whole page does a slower 30 s meta-refresh to
    # pick up the text cards / banner / pills. Once done/failed the page is
    # static (no meta-refresh, no JS) — nothing changes, so don't reload.
    _is_live = overall_state not in ("done", "failed")
    _meta_refresh = ('<meta http-equiv="refresh" content="30">'
                     if _is_live else "")
    if _is_live:
        _img_src_js = json.dumps(f"{png_name}")
        _live_js = (
            "<script>\n"
            "(function(){\n"
            f"  var IMG = {_img_src_js};\n"
            "  // Preload the new map into a detached Image, then swap the\n"
            "  // visible <img> src only after it has decoded — avoids the\n"
            "  // blank flash a direct src reset would cause.\n"
            "  function swapMap(){\n"
            "    var pre = new Image();\n"
            "    pre.onload = function(){\n"
            "      var el = document.getElementById('progress-img');\n"
            "      if (el) el.src = pre.src;\n"
            "    };\n"
            "    pre.src = IMG + '?t=' + Date.now();\n"
            "  }\n"
            "  setInterval(swapMap, 5000);\n"
            "})();\n"
            "</script>"
        )
        _sub_refresh_note = ("map auto-updates every 5 s &middot; "
                             "cards every 30 s")
    else:
        _live_js = ""
        _sub_refresh_note = "run complete &middot; static view"

    # --- Per-stage output: title the map as the output of the last-done (or
    # running) stage, and link this stage's report deliverables + the map PNG.
    # The orchestrator snapshots progress.png to stage<id>.png after each stage
    # so the map is preserved through the whole workflow.
    # Canonical labels (2026-06-07): derive from module-level STAGE_LABELS —
    # the old local copy was missing stage4_curbs / stage5_road_surface, so
    # completed Aecon stage-4/5 gallery cards rendered raw stage keys.
    _stage_label_full = STAGE_LABELS
    _last_stage = current_stage
    if not _last_stage:
        for _st in reversed(orch_status.get("stages", []) or []):
            if _st.get("state") == "done":
                _last_stage = _st.get("name")
                break
    _map_title = ("Output of " + _stage_label_full.get(_last_stage, _last_stage)
                  if _last_stage else "Stage output map")
    # Link this stage's deliverables (_stage_deliverables + _viz_local_link are
    # defined above the status banner). Each is made VIZ-LOCAL so the link
    # resolves both file:// (relative to viz/) AND the Flask UI (<base href>).
    _out_link_bits = []
    for _fname, _label in _stage_deliverables.get(_last_stage, []):
        _a = _viz_local_link(_fname, _label)
        if _a:
            _out_link_bits.append(_a)
    _out_link_bits.append(f'<a href="{png_name}" target="_blank">map PNG</a>')
    _stage_output_links = ('<div class="legend-note" style="margin-top:6px;">'
                           'Outputs: ' + " &middot; ".join(_out_link_bits)
                           + "</div>")

    # Stage-aware map caption: early stages only have imported pole tops (+ the
    # LAZ extent box). The tile/trajectory/body/curb legend only applies once
    # those stages have actually run.
    if _last_stage in ("stage0a_pole_csv", "stage0b_span_stats"):
        map_legend_note = ("Imported pole tops as cyan dots (gray = outside the "
                           "LAZ extent, when known); dashed box = LAZ coverage "
                           "extent. Satellite basemap for context.")
    else:
        map_legend_note = ("Tiles colored by status (gray=pending, "
                           "amber=inferring, green=done). Trajectory in dark "
                           "blue. Pole candidates/bodies and curblines are "
                           "labeled in the map legend.")

    # ---- Per-stage output gallery -------------------------------------------
    # Keep one "Output of Stage NN" card per COMPLETED stage (in order), each
    # with that stage's distinctive image + its own deliverable links, so every
    # stage's result is PRESERVED in the dashboard instead of only the last one.
    # 0b's distinctive output is the span figure (its dedicated span_stats_card
    # below), so it is skipped as an image card here but still contributes its
    # deliverables to the top index. A currently-running stage shows the live
    # auto-refreshing map.
    import re as _re

    _stage_output_image = {
        "stage0t_trajectory": "stage0t_trajectory.png",
        "stage0u_corridor_crop": "stage0u_corridor_density.png",
        "stage1_pointconv": "stage1_classified_footprint.png",
        "stage0a_pole_csv": "stage0a.png",
        "stage2_pole_crop": "stage2.png",
        # Curb + EOP + back-of-curb vectors over satellite imagery
        # (render_curbline_figure.py; renders without the basemap offline).
        # Absent when zero vectors were extracted — then the stage4.png
        # snapshot fallback applies as usual.
        "stage4_curbs": "stage4_curblines.png",
        # Final-classification footprint with the SDAI/PTC class-color legend
        # (render_trajectory_corridor.py --classified-dir 06_final_…). Aecon
        # corridor chains only — snapshot fallback elsewhere.
        "stage6_final_classification": "stage6_final_classified_footprint.png",
    }
    _stage_caption = {
        "stage4_curbs": ("Curb lines (red), edge of pavement (orange), and "
                         "back of curb (purple) over satellite imagery — "
                         "counts + lengths in the legend. Same layers are "
                         "toggleable on the interactive map."),
        "stage6_final_classification": (
            "Final classified cloud (subsampled) colored by the SDAI/PTC "
            "class catalog — the legend states what each color means. "
            "Road = class 40 (Road / Pavement)."),
        "stage4w_building_walls": ("Building wall lines (LineString Z) from "
                                   "class-6 points — classical cell/RANSAC "
                                   "extractor, no training."),
        "stage4t_tree_trunk_canopy": ("Tree trunks (truncated-cone; diameter / "
                                      "radius / circumference at 1 m above "
                                      "ground) + 2D canopy footprints from "
                                      "class-5 high-veg over class-2 ground — "
                                      "CHM crown split + woody-core Taubin "
                                      "circle fits, no training."),
        "stage4sg_sidewalks_geom": ("Sidewalk edge lines (LineString Z) recovered "
                                    "GEOMETRICALLY from 6-class ground — no "
                                    "sidewalk class: roughness + brightness + an "
                                    "adaptive road-relative offset + parallelism. "
                                    "Needs a colorized cloud."),
        "stage0t_trajectory": ("Vehicle trajectory (per run) + the corridor band "
                               "Stage 0u will crop (± half-width), over the "
                               "LAZ/AOI coverage extent."),
        "stage1_pointconv": ("PointCONV-classified footprint (colored by class) "
                             "over the corridor band — gaps inside the band are "
                             "road the chain didn't classify."),
        "stage1a_wire_pole": ("Geometry-only wire/pole cleanup of the 0.1 m "
                              "cloud (in place): RANSAC conductor + catenary "
                              "reclaim and a DBSCAN pole-shaft gate recover Wire/"
                              "Pole points leaked to veg/man-made. Pristine "
                              "originals kept in combined_outputs_precorrection/."),
        "stage0u_corridor_crop": ("Corridor crops (Stage 1's input) shaded by "
                                  "relative point density (points/m², log "
                                  "scale) — bright = dense returns near the "
                                  "scanner, gray = nothing survived the crop."),
        "stage0a_pole_csv": ("Imported pole tops on the LAZ coverage map "
                             "(gray = outside the LAZ extent)."),
        "stage2_pole_crop": "Per-pole crop boxes + detection over the LAZ tiles.",
    }

    def _snap_png(_sk):
        _m = _re.search(r"stage([0-9]+[a-z]*(?:_[0-9]+)?)", _sk)
        return f"stage{_m.group(1)}.png" if _m else None

    def _deliv_links_for(_sk):
        """Anchor list for a stage's deliverables — shares _viz_local_link
        (viz-local copy + run-dir-relative href rules) with the banner."""
        _bits = []
        for _fn, _lab in _stage_deliverables.get(_sk, []):
            _a = _viz_local_link(_fn, _lab)
            if _a:
                _bits.append(_a)
        return _bits

    _done_stages = [s.get("name") for s in (orch_status.get("stages") or [])
                    if s.get("state") == "done" and s.get("name")]
    _gallery, _deliv_rows = [], []
    for _sk in _done_stages:
        _links = _deliv_links_for(_sk)
        if _links:
            _deliv_rows.append(
                f'<li><b>{_stage_label_full.get(_sk, _sk)}</b>: '
                + " &middot; ".join(_links) + "</li>")
        if _sk == "stage0b_span_stats":
            continue  # 0b's output is the dedicated span_stats_card below
        # Prefer the stage's dedicated figure; fall back to the map snapshot if
        # that figure wasn't produced (e.g. the Stage 1 classified-footprint
        # figure only exists for the Aecon corridor chain).
        _img = _stage_output_image.get(_sk)
        if _img and not (_viz / _img).is_file():
            _img = None
        _img = _img or _snap_png(_sk)
        if not _img or not (_viz / _img).is_file():
            continue
        _lbl = _stage_label_full.get(_sk, _sk)
        _cap = _stage_caption.get(_sk, "")
        _card_links = list(_links) + [
            f'<a href="{_img}" target="_blank">map PNG</a>']
        # Stage 0t also ships an interactive Leaflet map (trajectory + corridor
        # on real basemap tiles, with the QC info box). Relative href resolves
        # under file:// (viz-sibling) AND the Flask /view/<run_id>/ base tag.
        # Per-card "view on map" links removed 2026-06-10 — the dashboard now
        # has ONE "Interactive Map" button (top of the gallery) opening the
        # all-layers map; per-card duplicates only confused testers.
        _gallery.append(
            '<div class="map-card" style="margin-bottom:16px;">'
            f'<h3 style="margin:0 0 6px 0; font-size:14px;">Output of {_lbl}</h3>'
            f'<img src="{_img}?t={cache_bust}" alt="{_lbl}">'
            + (f'<div class="legend-note">{_cap}</div>' if _cap else "")
            + '<div class="legend-note" style="margin-top:6px;">Deliverables: '
            + " &middot; ".join(_card_links) + "</div></div>")
    if pole_crops_section:
        _gallery.append('<div class="map-card" style="margin-bottom:16px;">'
                        + pole_crops_section + "</div>")
    if current_stage and current_stage not in _done_stages:
        _lbl = _stage_label_full.get(current_stage, current_stage)
        _gallery.append(
            '<div class="map-card" style="margin-bottom:16px;">'
            f'<h3 style="margin:0 0 6px 0; font-size:14px;">Running: {_lbl}</h3>'
            f'<img id="progress-img" src="{png_name}?t={cache_bust}" alt="live map">'
            f'<div class="legend-note">{map_legend_note}</div></div>')
    # THE single "Interactive Map" button (replaces the per-card map links):
    # opens the all-layers stage-0t map, or — Firmatek chains, which have no
    # trajectory map — the stage-2 detection map. Both carry a top-right
    # layer control.
    if _imap_href:
        _imap_note = (
            'All layers on satellite imagery &mdash; use the selector (top '
            'right of the map) to toggle trajectory, corridor, density, '
            'curbs, edge of pavement, road surface, walls, poles and crop '
            'boxes.' if _has_imap else
            'All detections on satellite imagery &mdash; use the selector '
            '(top right of the map) to toggle poles by status, crop boxes '
            'and density.')
        _gallery.insert(0, (
            '<div class="map-card" style="margin-bottom:16px; text-align:center;">'
            f'<a href="{_imap_href}" target="_blank" '
            'style="display:inline-block; padding:10px 26px; background:#1c5aa0; '
            'color:#fff; font-size:15px; font-weight:600; border-radius:6px; '
            'text-decoration:none;">'
            '&#128506; Interactive Map &#8599;</a>'
            '<div class="legend-note" style="margin-top:6px;">'
            f'{_imap_note}</div></div>'))
    if _gallery:
        stage_gallery = "\n".join(_gallery)
    else:
        stage_gallery = (
            '<div class="map-card">'
            f'<h3 style="margin:16px 0 6px 0; font-size: 14px;">{_map_title}</h3>'
            f'<img id="progress-img" src="{png_name}?t={cache_bust}" '
            'alt="progress map">'
            f'<div class="legend-note">{map_legend_note}</div>'
            f'{_stage_output_links}</div>')

    # Top Deliverables index: per-stage report links (0a/0b, viz-local) + Stage
    # 2's detection reports (copied viz-local) + a link to the full deliverables
    # folder. Bare links resolve under file:// AND the Flask UI; the bulky layers
    # (shapefiles, GeoPackage, missed-pole LAZ) live behind the folder link.
    _det_dir = next((p for p in run_dir.iterdir()
                     if p.is_dir() and p.name.endswith("_detection")), None)
    if _det_dir:
        _s2 = []
        for _pat, _lab in (("*_detection_summary.pdf", "detection report (PDF)"),
                           ("*_detection_summary.html", "detection report (HTML)"),
                           ("*_detection_results.csv", "per-pole results (CSV)")):
            _hit = next(iter(sorted(_det_dir.glob(_pat))), None)
            if _hit and _hit.is_file():
                try:
                    _shutil.copy2(_hit, _viz / _hit.name)
                except Exception:
                    pass
                if (_viz / _hit.name).is_file():
                    _s2.append(f'<a href="{_hit.name}" target="_blank">{_lab}</a>')
        # Interactive detection map (viz-local; rendered by
        # render_detection_map.py). Plain relative href resolves under both
        # file:// (same dir as progress.html) and the Flask asset route.
        # On Firmatek chains this is the map behind the big button; keep one
        # Deliverables-index row for it (the Aecon analog is the stage-0t
        # row below). Suppressed when the trajectory map exists.
        if (_viz / "stage2_detection_map.html").is_file() and not _has_imap:
            _s2.append('<a href="stage2_detection_map.html" target="_blank">'
                       'Interactive Map &#8599;</a>')
        if _s2:
            _deliv_rows.append(
                f'<li><b>{STAGE_LABELS["stage2_pole_crop"].replace("&", "&amp;")}</b>: '
                + " &middot; ".join(_s2) + "</li>")
        # Folder link is context-dependent. Static href targets the Flask
        # /view/<run_id>/dir/<det>/ listing route (no '../' — that would
        # escape the <base href="/view/<run_id>/"> tag and 404, QC feedback
        # 2026-06-05). When the page is opened directly off disk (file://)
        # a tiny script swaps in the relative OS directory path instead.
        _deliv_rows.append(
            f'<li>&#128193; <a id="deliv-folder-link" '
            f'data-dir="{_det_dir.name}" href="dir/{_det_dir.name}/" '
            'target="_blank"><b>All deliverables (open folder)</b></a> '
            f'<span style="color:#999;font-size:11px;">{_det_dir.name}/</span>'
            '</li>'
            '<script>(function(){if(location.protocol==="file:"){'
            'var a=document.getElementById("deliv-folder-link");'
            'if(a){a.setAttribute("href",'
            '["..",a.getAttribute("data-dir"),""].join("/"));}}})();'
            '</script>')
    # Aecon Data Validation / corridor: surface the Stage 0t deliverables (interactive
    # trajectory map + QC report + corridor density) in the Deliverables index —
    # the analog of the Firmatek stage-2 detection row above. _det_dir is None
    # for these chains, so without this Aecon Data Validation has no Deliverables map link.
    # Files are viz-local and existence-gated, so this no-ops on pole chains.
    _s0t_deliv = []
    for _fn, _lab in (("stage0t_trajectory_map.html",
                       "Interactive Map (all layers) &#8599;"),
                      ("stage0t_report.html", "QC report (HTML)"),
                      ("stage0t_report.pdf", "QC report (PDF)"),
                      ("stage0u_corridor_density.png", "corridor density (PNG)")):
        if (_viz / _fn).is_file():
            _s0t_deliv.append(f'<a href="{_fn}" target="_blank">{_lab}</a>')
    if _s0t_deliv:
        _deliv_rows.append(
            f'<li><b>{STAGE_LABELS["stage0t_trajectory"].replace("&", "&amp;")}</b>: '
            + " &middot; ".join(_s0t_deliv) + "</li>")
    # ---- Single Deliverables card (2026-06-07) -----------------------------
    # ONE card merging both former sources: the per-stage _deliv_rows section
    # (stage-by-stage report links) and the run_manifest.json section (item
    # links + found/missed summary + peak-RAM line, built further up). The
    # manifest-based card used to be silently overwritten here whenever any
    # mapped stage had completed. All hrefs are run-dir-relative (no '../').
    deliverables_card = ""
    if _deliv_rows or _man_items_html:
        _rows_html = (
            '<ul style="margin:4px 0 0 0; padding-left:18px; font-size:13px; '
            'line-height:1.7;">' + "".join(_deliv_rows) + "</ul>"
            if _deliv_rows else "")
        _man_html = ""
        if _man_items_html:
            _man_html = (
                '<div style="font-size:12px;color:#666;margin-top:8px;">'
                'From <code>run_manifest.json</code>:</div>'
                '<ul style="margin:4px 0 0 0; padding-left:18px; '
                'font-size:13px; line-height:1.7;">' + _man_items_html
                + "</ul>")
        deliverables_card = (
            '<div class="log-card" style="border-left:4px solid #1c6dd0;">'
            '<h3 style="margin:0 0 6px 0; font-size:14px;">Deliverables</h3>'
            + _rows_html + _man_html + _man_summ_html + _man_res_html
            + '<div style="font-size:11px;color:#999;margin-top:6px;">'
            'Full index: <code>run_manifest.json</code></div></div>')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
{_meta_refresh}
<title>{header_title} — pipeline progress</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    margin: 0; padding: 16px; background: #f5f5f7; color: #222;
  }}
  h1 {{ font-size: 18px; margin: 0 0 4px 0; }}
  .sub {{ color: #666; font-size: 12px; margin-bottom: 12px; }}
  .stage-bar {{
    display: grid; grid-template-columns: repeat({n_pills}, 1fr); gap: 6px; margin-bottom: 16px;
  }}
  @media (max-width: 1200px) {{
    .stage-bar {{ grid-template-columns: repeat({max(1, n_pills // 2)}, 1fr); }}
  }}
  .stage-pill {{
    border-radius: 8px; padding: 10px 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.08);
  }}
  .stage-pill-title {{ font-weight: 600; font-size: 13px; }}
  .stage-pill-sub {{ font-size: 11px; opacity: 0.9; margin-top: 2px; min-height: 1em; }}
  .main {{
    /* Single-column layout (2026-05-27): the right-side Parameters
       panel was moved exclusively to the bundle viewer per user
       request, so the map card now spans the full width. */
    display: grid; grid-template-columns: 1fr; gap: 16px;
  }}
  .map-card, .params-card, .log-card {{
    background: white; border-radius: 8px; padding: 12px; box-shadow: 0 1px 2px rgba(0,0,0,0.06);
  }}
  .map-card img {{ width: 100%; height: auto; display: block; border-radius: 4px; }}
  .log-card {{ margin-top: 16px; }}
  /* Span-statistics thumbnail card (Stage 0b output, added 2026-05-27).
     Sized as a "small window" — capped at 800 px tall and 60% of the
     map-card width, sitting between the main map and the log sections. */
  .span-card {{ max-width: 720px; }}
  .span-img {{ width: 100%; height: auto; max-height: 280px;
               object-fit: contain; display: block;
               border-radius: 4px; background: white; }}
  .span-summary {{ margin-top: 6px; color: #444; }}
  .span-links {{ margin-top: 2px; color: #555; }}
  .span-links a {{ color: #1c6dd0; text-decoration: none; }}
  .span-links a:hover {{ text-decoration: underline; }}
  /* Stage 2 detection-card stat tiles (added 2026-05-27). */
  .stat-card {{ background: #fafafc; padding: 8px 10px; border-radius: 6px;
                border: 1px solid #efeff2; text-align: center; }}
  /* Stage 1 Wave B inference-progress card per-pole bars. */
  .ppc-list {{ display: grid;
               grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
               gap: 4px 8px; }}
  .ppc-row {{ display: grid;
              grid-template-columns: 70px 1fr 50px;
              gap: 6px; align-items: center; font-size: 11px; }}
  .ppc-label {{ font-family: Consolas, monospace; color: #333; }}
  .ppc-bar-bg {{ background: #f0f0f3; height: 8px; border-radius: 4px;
                 overflow: hidden; }}
  .ppc-bar {{ height: 100%; transition: width 0.2s linear; }}
  .ppc-val {{ font-family: Consolas, monospace; color: #555;
              text-align: right; font-size: 10px; }}
  .log-pre {{
    font-family: "Cascadia Mono", Consolas, monospace; font-size: 11px;
    background: #1d1f21; color: #c5c8c6; padding: 10px; border-radius: 4px;
    overflow-x: auto; max-height: 280px; overflow-y: auto;
  }}
  details {{ margin-bottom: 8px; border: 1px solid #e5e5e7; border-radius: 6px; padding: 8px 10px; }}
  summary {{ cursor: pointer; font-weight: 600; display: flex; align-items: center; gap: 8px; }}
  summary::-webkit-details-marker {{ display: none; }}
  .stage-name {{ flex: 1; font-size: 13px; }}
  .src-badge {{
    font-size: 10px; padding: 2px 8px; border-radius: 999px;
    font-weight: 500; text-transform: lowercase;
  }}
  .src-eff {{ background: #d6f4d8; color: #1a6e2a; }}
  .src-plan {{ background: #fde9d0; color: #934700; }}
  .src-unk {{ background: #e2e2e2; color: #555; }}
  /* G4/G14 status badges (shared by per-pole grid + curb-skill checklist) */
  .state-pill {{
    display: inline-block; font-size: 10px; padding: 2px 8px;
    border-radius: 999px; font-weight: 500; text-transform: lowercase;
    line-height: 1.4;
  }}
  .state-done    {{ background: #d6f4d8; color: #1a6e2a; }}
  .state-running {{ background: #fde9d0; color: #934700; }}
  .state-pending {{ background: #e2e2e2; color: #555; }}
  .state-failed  {{ background: #fbd0d0; color: #8a1d1c; }}
  .params-table {{ width: 100%; margin-top: 8px; border-collapse: collapse; font-size: 11px; }}
  .params-table td {{ padding: 3px 6px; vertical-align: top; }}
  .params-table .pk {{ color: #555; white-space: nowrap; font-family: "Cascadia Mono", Consolas, monospace; }}
  .params-table .pv {{ font-family: "Cascadia Mono", Consolas, monospace; word-break: break-word; }}
  .params-table pre {{ margin: 0; font-size: 10px; white-space: pre-wrap; }}
  .hint {{ color: #888; font-size: 12px; }}
  .legend-note {{ color: #666; font-size: 11px; margin-top: 8px; }}
</style>
</head>
<body>
  <h1>{workflow_type}: <span style="color:#1c6dd0;">{project_folder}</span></h1>
  <div class="sub">pipeline progress &middot; run {run_id_disp} &middot; Updated {updated} ({_sub_refresh_note}){sub_extra}</div>
  <div class="stage-bar">{pills}</div>
  {status_banner}
  {deliverables_card}
  <div class="main">{stage_gallery}</div>
  {span_stats_card}
  {pointconv_card}
  {stage2_phase_card}
  {detection_card}
  {not_found_previews_card}
  {topology_card}
  {sanity_card}
  {per_pole_card}
  {curb_skill_card}
  <div class="log-card">
    <h3 style="margin:0 0 8px 0; font-size: 14px;">Host resources</h3>
    {resources_html}
  </div>
  {log_card}
  {_live_js}
</body>
</html>
"""
    out_html.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# G1 — ETA + rate per stage
# ---------------------------------------------------------------------------

def _count_stage_progress(run_dir: Path, manifest: dict | None,
                          tiles: list, sources: list) -> dict[str, dict]:
    """Per-stage (done, total, unit). Filesystem-truth, no orchestrator state."""
    counts: dict[str, dict] = {}

    # Stage 1 — done = tiles with seg_out, total = tile_count
    tf1 = run_dir / "01_pointconv" / "tf1_outputs"
    if manifest and tiles:
        done = 0
        if tf1.exists():
            for sub in tf1.iterdir():
                if sub.is_dir() and (sub / f"{sub.name}_v_seg_out.las").exists():
                    done += 1
        counts["stage1_pointconv"] = {
            "done": done, "total": len(tiles), "unit": "tiles"}

    # Stage 1b — done = back-projected sources, total = source count
    fine = run_dir / "01_pointconv" / "combined_outputs_0p025m"
    if fine.exists() and sources:
        done = sum(1 for _ in fine.glob("*_combined_0p025m.las"))
        counts["stage1b_fine_classification"] = {
            "done": done, "total": len(sources), "unit": "sources"}

    # Stage 2 — done = per-pole crops written, total = candidates
    crops_dir = run_dir / "02_pole_crop" / "output" / "crops"
    candidates_shp = run_dir / "02_pole_crop" / "poles_candidates_loose.shp"
    if crops_dir.exists() and candidates_shp.exists():
        # Crops are named Pole_*.las (SHP input — Firmatek/validation) or
        # P_NNN.las (CSV input). Count whichever naming this run used.
        done = sum(1 for _ in crops_dir.glob("Pole_*.la[sz]"))
        if done == 0:
            done = sum(1 for _ in crops_dir.glob("P_*.la[sz]"))
        # Cheap candidate count: file count + 1 record per feature in DBF
        # — read DBF header bytes 4-7 (uint32 LE) for record count
        try:
            dbf = candidates_shp.with_suffix(".dbf")
            if dbf.exists():
                with dbf.open("rb") as f:
                    f.seek(4)
                    total = int.from_bytes(f.read(4), "little")
            else:
                total = 0
        except Exception:
            total = 0
        counts["stage2_pole_crop"] = {
            "done": done, "total": total, "unit": "crops"}

    # Stage 3 — done = poletops dirs in PoleVec/EstimatePoleTops
    polevec_tops = run_dir / "PoleVec" / "EstimatePoleTops"
    if polevec_tops.exists():
        done = sum(1 for p in polevec_tops.iterdir() if p.is_dir())
        # Total = stage2's candidate count if known
        s2 = counts.get("stage2_pole_crop", {})
        total = s2.get("total", 0)
        counts["stage3_pole_vec"] = {
            "done": done, "total": total, "unit": "poles"}

    # Stage 4 — curb-skill 11 sub-stages, n_done/n_total from
    # curb_skill_progress() if available.
    sanity_path = run_dir / "viz" / "output_sanity.json"
    if sanity_path.exists():
        try:
            _s = json.loads(sanity_path.read_text(encoding="utf-8"))
            csp = _s.get("curb_skill_progress", {}) or {}
            if csp.get("found"):
                counts["stage4_curbs"] = {
                    "done": csp.get("n_done", 0),
                    "total": csp.get("n_total", 0),
                    "unit": "sub-stages",
                }
        except Exception:
            pass
    return counts


def _load_rate_history(run_dir: Path) -> dict:
    p = run_dir / "viz" / "rate_history.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_rate_history(run_dir: Path, hist: dict) -> None:
    p = run_dir / "viz" / "rate_history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(hist, indent=2), encoding="utf-8")


def _update_rate_history(run_dir: Path, counts: dict[str, dict],
                         max_samples: int = 30) -> dict:
    """Append a snapshot to viz/rate_history.json; trim to last N samples."""
    hist = _load_rate_history(run_dir)
    now = dt.datetime.now().isoformat(timespec="seconds")
    for stage, c in counts.items():
        if stage not in hist:
            hist[stage] = []
        # De-dup: skip if last sample has identical done count
        if hist[stage] and hist[stage][-1]["done"] == c["done"]:
            # Update timestamp but don't add a new sample
            continue
        hist[stage].append({"t": now, "done": int(c["done"]),
                            "total": int(c["total"])})
        if len(hist[stage]) > max_samples:
            hist[stage] = hist[stage][-max_samples:]
    _save_rate_history(run_dir, hist)
    return hist


def _compute_rates(hist: dict) -> dict[str, dict]:
    """For each stage with >=2 samples, return rate + ETA."""
    rates: dict[str, dict] = {}
    for stage, samples in hist.items():
        if len(samples) < 2:
            continue
        first = samples[0]
        last = samples[-1]
        try:
            t0 = dt.datetime.fromisoformat(first["t"])
            t1 = dt.datetime.fromisoformat(last["t"])
        except Exception:
            continue
        elapsed = (t1 - t0).total_seconds()
        delta = last["done"] - first["done"]
        total = last["total"] or 0
        remaining = max(0, total - last["done"])
        if elapsed <= 0 or delta <= 0:
            rates[stage] = {
                "rate_per_min": 0.0, "eta_sec": None,
                "sample_count": len(samples), "window_sec": elapsed,
                "complete": last["done"] >= total and total > 0,
            }
            continue
        rate_per_sec = delta / elapsed
        rates[stage] = {
            "rate_per_min": round(rate_per_sec * 60, 2),
            "rate_per_sec": round(rate_per_sec, 4),
            "sec_per_unit": round(1.0 / rate_per_sec, 1) if rate_per_sec else None,
            "eta_sec": int(remaining / rate_per_sec) if rate_per_sec > 0 else None,
            "sample_count": len(samples),
            "window_sec": int(elapsed),
            "complete": last["done"] >= total and total > 0,
        }
    return rates


def _format_eta(eta_sec: int | None) -> str:
    if eta_sec is None:
        return ""
    if eta_sec < 60:
        return f"ETA {eta_sec}s"
    if eta_sec < 3600:
        return f"ETA {eta_sec // 60} min"
    return f"ETA {eta_sec / 3600:.1f} h"


def _refresh_params(run_dir: Path) -> None:
    """Run dump_pipeline_params.py for the current state. Silent on failure;
    the HTML degrades gracefully if pipeline_params.json is stale."""
    dumper = Path(__file__).parent / "dump_pipeline_params.py"
    if not dumper.exists():
        return
    try:
        subprocess.run([sys.executable, str(dumper), str(run_dir)],
                       check=False, capture_output=True, timeout=30,
                       creationflags=_NO_WINDOW)
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] params refresh error: {e}")


def _refresh_resources(run_dir: Path) -> None:
    """Poll host resources into viz/resources.json (G2). Silent on failure."""
    poller = Path(__file__).parent / "poll_resources.py"
    if not poller.exists():
        return
    try:
        subprocess.run([sys.executable, str(poller), str(run_dir)],
                       check=False, capture_output=True, timeout=15,
                       creationflags=_NO_WINDOW)
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] resource poll error: {e}")


def _refresh_output_sanity(run_dir: Path) -> None:
    """Compute output sanity counters into viz/output_sanity.json (G3)."""
    tool = Path(__file__).parent / "compute_output_sanity.py"
    if not tool.exists():
        return
    try:
        subprocess.run([sys.executable, str(tool), str(run_dir)],
                       check=False, capture_output=True, timeout=60,
                       creationflags=_NO_WINDOW)
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] output sanity error: {e}")


def render(run_dir: Path, out_png: Path):
    _refresh_params(run_dir)
    _refresh_resources(run_dir)
    _refresh_output_sanity(run_dir)
    manifest = _load_manifest(run_dir)
    # Manifest is None during the brief window between orchestrator
    # launch and stage 1's tile-builder writing tf1_tile_manifest.json.
    # Render anyway with empty tile/source lists so the dashboard at
    # least shows the stage pills + log tail + resources.
    tiles = manifest.get("tiles", []) if manifest else []
    sources = manifest.get("source_files", []) if manifest else []
    traj = _load_trajectory(run_dir)
    merged = _merged_sources(run_dir)

    by_source: dict[str, list[dict]] = {}
    for t in tiles:
        by_source.setdefault(t["source_stem"], []).append(t)

    # Tile statuses.
    status_counts = {"pending": 0, "inferring": 0, "done": 0}
    tile_states = []
    for t in tiles:
        s = _tile_status(run_dir, t)
        tile_states.append((t, s))
        status_counts[s] += 1

    # Subsequent-stage overlays. Layers come from multiple stages and
    # may be in different CRSes (e.g. Firmatek profile: pole_tops in
    # ftUS but Body_Lines + curblines in the metric stage0c target CRS).
    # Detect the axes CRS once here and reproject every gdf to it so
    # they all plot on the same map without falling off-screen.
    pole_shp = run_dir / "02_pole_crop" / "poles_candidates_loose.shp"
    pole_gdf_raw = _shapefile_geoms(pole_shp)
    # Stage 0a early-feedback fallback (added 2026-05-27): if Stage 2 hasn't
    # produced the detection candidates shapefile yet, show the imported
    # pole_tops.shp (Stage 0a output) on the main map so the operator gets
    # visual confirmation of pole positions immediately after Stage 0a (~1 s)
    # instead of waiting through Stage 0b + Stage 2 (5+ min). Markers are
    # drawn as small cyan/gray dots (inside/outside LAZ extent) and get
    # replaced by the star/diamond/X status markers once Stage 2 finishes.
    pole_source_imported = False
    if pole_gdf_raw is None:
        imported_gdf = _shapefile_geoms(run_dir / "00_inputs" / "pole_tops.shp")
        if imported_gdf is not None:
            pole_gdf_raw = imported_gdf
            pole_source_imported = True
    axes_crs = _detect_axes_crs(run_dir, fallback_gdf=pole_gdf_raw)
    if pole_source_imported:
        # Re-load via the inside-LAZ helper so we get the coverage flag.
        pole_gdf = _load_imported_pole_tops(run_dir, axes_crs)
    else:
        pole_gdf = _reproject_gdf(pole_gdf_raw, axes_crs)
    # Body_Lines.shp location depends on the pole-vec workflow + mode:
    #   * Aethon body-only-pointconv : 03_pole_vec_body/[Combined/]Body_Lines.shp
    #   * Firmatek metric full mode  : PoleVec/Reference_Pole_Tops/Body_Lines.shp
    #     (with PoleVec/Combined/Grp0_Body_Lines.shp as group-0 fallback)
    # Try each in order; the first that exists wins. Without this list,
    # Firmatek runs silently lose their body markers because the renderer
    # looks in the wrong directory.
    body_shp = None
    for cand in (
        run_dir / "03_pole_vec_body" / "Combined" / "Body_Lines.shp",
        run_dir / "03_pole_vec_body" / "Body_Lines.shp",
        run_dir / "PoleVec" / "Reference_Pole_Tops" / "Body_Lines.shp",
        run_dir / "PoleVec" / "Combined" / "Grp0_Body_Lines.shp",
    ):
        if cand.exists():
            body_shp = cand
            break
    body_gdf = _reproject_gdf(_shapefile_geoms(body_shp) if body_shp else None,
                              axes_crs)
    # Stage 3.5 wire-attachment manifest (optional). Lets us split the
    # "body found" group into wire-attached (green) vs wire-orphaned
    # (orange). When the manifest is absent we render the historic
    # 2-color scheme (green / red x).
    wire_manifest_path = run_dir / "03_pole_vec_body" / "wire_attachment.json"
    wire_orphan_ids: set[str] = set()
    if wire_manifest_path.exists():
        try:
            import json as _json
            _m = _json.loads(wire_manifest_path.read_text(encoding="utf-8"))
            wire_orphan_ids = {pid for pid, info in
                               (_m.get("poles", {}) or {}).items()
                               if not info.get("wire_attached", False)
                               and info.get("reason") is None}  # exclude no-body
        except Exception:
            pass
    # Curblines + EOP: try convenience-copy path first (04_curbs/viz/), then
    # native stage 6d output path (04_curbs/artifacts/02_ground/06d_ransac_curve/).
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
    curb_gdf = _reproject_gdf(
        next((_shapefile_geoms(p) for p in curb_candidates if p.exists()), None),
        axes_crs)
    eop_gdf = _reproject_gdf(
        next((_shapefile_geoms(p) for p in eop_candidates if p.exists()), None),
        axes_crs)

    # stage4t tree-trunk segmentation (Aecon corridor): canopy footprints
    # (polygons) + trunk locations (points). Same read/reproject idiom as the
    # curb/EOP overlays; best-effort (None when the stage didn't run).
    _tree_canopy_p = run_dir / "04t_tree_trunk_canopy" / "Tree_Canopy.shp"
    _tree_stems_p = run_dir / "04t_tree_trunk_canopy" / "Tree_Stems.shp"
    tree_canopy_gdf = _reproject_gdf(
        _shapefile_geoms(_tree_canopy_p) if _tree_canopy_p.exists() else None,
        axes_crs)
    tree_stems_gdf = _reproject_gdf(
        _shapefile_geoms(_tree_stems_p) if _tree_stems_p.exists() else None,
        axes_crs)

    # --- Plot ---
    # Figure size auto-adjusts to data aspect so wide corridors
    # (Verizon: ~20 km × 4.6 km) get a wide-thin figure instead of
    # being squeezed into a square frame. Width is fixed at 18 in;
    # height clamped to [4, 14] in.
    _data_aspect = 1.0
    try:
        _sp = _read_laz_bbox_facts(run_dir)
        _bb = _sp.get("laz_bbox_in_pole_crs") or _sp.get("laz_bbox") or {}
        if _bb:
            _dx = _bb["xmax"] - _bb["xmin"]
            _dy = _bb["ymax"] - _bb["ymin"]
            _data_aspect = _dx / max(_dy, 1e-6)
    except Exception:
        pass
    fig_w = 18.0
    fig_h = max(4.0, min(14.0, fig_w / max(_data_aspect, 0.5)))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=110)

    # Pole-cropping native tiles (Firmatek profile, Stage 2 on raw LAZ).
    # Each *_NNNN.las under 02_pole_crop/output/tiles/ has a header
    # bbox; drawing them as green rectangles gives a live "tiling
    # progress over the LAZ extent" view as Stage 2 chews through the
    # 6.2 B-point file. Cached in tile_bboxes.json so each render only
    # reads NEW files (header-only, ~5 ms each).
    n_pc_tiles = 0
    try:
        pc_tile_dir = run_dir / "02_pole_crop" / "output" / "tiles"
        if pc_tile_dir.is_dir():
            cache_path = run_dir / "viz" / "tile_bboxes.json"
            cache = {}
            if cache_path.exists():
                try:
                    cache = json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:
                    cache = {}
            n_new = 0
            for las_path in sorted(pc_tile_dir.glob("*_[0-9][0-9][0-9][0-9].la[sz]")):
                if las_path.name in cache:
                    continue
                try:
                    import laspy as _laspy
                    h = _laspy.open(las_path).header
                    cache[las_path.name] = [float(h.x_min), float(h.y_min),
                                             float(h.x_max), float(h.y_max)]
                    n_new += 1
                except Exception:
                    continue
            if n_new:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(cache, indent=1),
                                          encoding="utf-8")
                except Exception:
                    pass
            # Draw all cached tile bboxes (most recent on top).
            # Alpha lowered from 0.45 -> 0.22 so the satellite imagery
            # underneath shows through clearly — the user can verify
            # the tiles cover real ground, not just abstract numbers.
            for name, bbox in cache.items():
                xmin, ymin, xmax, ymax = bbox
                ax.add_patch(mpatches.Rectangle(
                    (xmin, ymin), xmax - xmin, ymax - ymin,
                    facecolor=COLOR_DONE, edgecolor="#0d5c1a",
                    linewidth=0.4, alpha=0.22, zorder=2))
            n_pc_tiles = len(cache)
    except Exception:
        pass

    # LAZ extent rectangle from stage 0b's span_statistics.json. Gives
    # the user the full footprint context even when only a few tiles
    # have been written so far.
    try:
        sp = _read_laz_bbox_facts(run_dir)
        bb = sp.get("laz_bbox_in_pole_crs") or sp.get("laz_bbox") or {}
        if bb:
            ax.add_patch(mpatches.Rectangle(
                (bb["xmin"], bb["ymin"]),
                bb["xmax"] - bb["xmin"], bb["ymax"] - bb["ymin"],
                facecolor="none", edgecolor="#1c6dd0",
                linewidth=1.5, linestyle="--", alpha=0.85, zorder=1))
    except Exception:
        pass

    # Compute pole→body status mapping early so we can use it for both
    # the per-pole crop rectangles (when Stage 1 is complete) and the
    # pole markers (always). pole_id formats vary between pole-vec modes
    # (PoleTop_P_NNN in full mode vs P_NNN in body-only-pointconv).
    body_pole_ids: set = set()
    if body_gdf is not None and "pole_id" in body_gdf.columns:
        body_pole_ids = {
            str(pid).replace("PoleTop_", "")
            for pid in body_gdf["pole_id"].tolist()
        }
    if pole_gdf is not None and len(pole_gdf) and "Pole" in pole_gdf.columns:
        row_pole_ids = list(pole_gdf["Pole"].astype(str))
    elif pole_gdf is not None:
        row_pole_ids = [f"P_{i:03d}" for i in range(len(pole_gdf))]
    else:
        row_pole_ids = []

    # Decide overlay mode:
    #   Stage 1 IN PROGRESS or INCOMPLETE  -> PointCONV tile rectangles
    #                                          colored by inference status
    #                                          (pending / inferring / done).
    #   Stage 1 COMPLETE and crop bboxes
    #   cached                             -> per-pole CROP rectangles
    #                                          colored by Stage 3 body
    #                                          status (found / orphan /
    #                                          missed). Tile rectangles
    #                                          are no longer informative
    #                                          once 100% inferred.
    total_t = len(tile_states)
    done_t = sum(1 for _, st in tile_states if st == "done")
    stage1_complete = total_t > 0 and done_t == total_t

    crop_cache: dict = {}
    crop_bboxes_path = run_dir / "viz" / "crop_bboxes.json"
    if crop_bboxes_path.exists():
        try:
            crop_cache = json.loads(
                crop_bboxes_path.read_text(encoding="utf-8")) or {}
        except Exception:
            crop_cache = {}

    show_crop_mode = stage1_complete and bool(crop_cache) and bool(row_pole_ids)
    # 2026-05-27: suppress the legacy red-filled "crop-area mode"
    # rectangles when the newer green-outline crop boxes will be drawn
    # below (the crop_bboxes cache has entries → drawing will happen).
    # Per-user feedback those red fills looked like opaque shading
    # inside each crop box; the green outlines are now the sole
    # crop-area indicator. Once Stage 3 produces body lines AND we
    # want to color by status, we'll re-enable a non-red version
    # (green=body, gray=no-body placeholder, no red).
    crop_bboxes_cache_path = run_dir / "viz" / "crop_bboxes.json"
    if crop_bboxes_cache_path.exists():
        try:
            _cb = json.loads(crop_bboxes_cache_path.read_text(encoding="utf-8"))
            if _cb:  # has entries
                show_crop_mode = False
        except Exception:
            pass

    if show_crop_mode and pole_gdf is not None and len(pole_gdf):
        # Per-pole crop rectangles colored by body-status. Same colour
        # codes used for the pole-marker scatter further down so the
        # rectangle + dot are visually paired.
        gx_iter = pole_gdf.geometry.x.values
        gy_iter = pole_gdf.geometry.y.values
        for i, pid in enumerate(row_pole_ids):
            bbox = crop_cache.get(f"{pid}.las")
            if bbox is None:
                # Pending / unknown crop — draw a placeholder using the
                # pole position and an estimated half-size from another
                # cached crop, if any.
                if i >= len(gx_iter):
                    continue
                # Try estimating half-size from any cached bbox.
                hs = None
                for _bb in crop_cache.values():
                    hs = (_bb[2] - _bb[0]) / 2.0
                    break
                if hs is None:
                    continue
                xmin = gx_iter[i] - hs
                ymin = gy_iter[i] - hs
                xmax = gx_iter[i] + hs
                ymax = gy_iter[i] + hs
                fill, edge = "#cccccc", "#888888"
            else:
                xmin, ymin, xmax, ymax = bbox
                if pid in body_pole_ids:
                    if pid in wire_orphan_ids:
                        fill, edge = COLOR_POLE_NO_WIRE, "#7a4f00"   # orphan
                    else:
                        fill, edge = "#1a8e3a", "#0d5c1a"            # found
                else:
                    fill, edge = "#d8221f", "#7a1611"                # missed
            ax.add_patch(mpatches.Rectangle(
                (xmin, ymin), xmax - xmin, ymax - ymin,
                facecolor=fill, edgecolor=edge,
                linewidth=0.6, alpha=0.28, zorder=3))
    else:
        # Stage 1 not yet 100% done (or no crop cache): draw the
        # PointCONV tile rectangles colored by inference status. Drawn
        # ABOVE pole-crop tiles so the later, finer-grain tiling
        # visually dominates.
        for t, st in tile_states:
            x0, y0 = t["core_min_x"], t["core_min_y"]
            w = t["core_max_x"] - x0
            h = t["core_max_y"] - y0
            c = COLOR_DONE if st == "done" else (
                COLOR_INFERRING if st == "inferring" else COLOR_PENDING)
            # alpha lowered 0.55 -> 0.25 so the satellite imagery
            # underneath stays readable.
            ax.add_patch(mpatches.Rectangle((x0, y0), w, h, facecolor=c,
                                            edgecolor="#0d5c1a",
                                            linewidth=0.4,
                                            alpha=0.25, zorder=3))

    # Source-level merged borders intentionally NOT drawn — the user
    # found them visually noisy. Still count merged sources for the
    # status JSON downstream.
    n_merged = sum(1 for stem in by_source if stem in merged)

    # Trajectory polyline.
    if len(traj):
        ax.plot(traj[:, 0], traj[:, 1], color=COLOR_TRAJECTORY,
                linewidth=0.6, alpha=0.7, zorder=3)

    # Corridor spans (Stage 2 corridor extraction, added 2026-05-27).
    # Blue line segments connecting pole pairs that have a span LAS
    # file written. Reads corridor_manifest.csv (the cheap-to-parse
    # listing) and looks up pole positions from poles_candidates_loose.shp.
    # Drawn early (zorder=4) so crop boxes + pole markers stay on top.
    n_corridor_spans = 0
    try:
        spans_csv = run_dir / "02_pole_crop" / "output" / \
            "corridor_spans" / "corridor_manifest.csv"
        if spans_csv.exists() and pole_gdf is not None and len(pole_gdf) \
                and "Pole" in pole_gdf.columns:
            import csv as _csv
            # Build pole_id → (x, y) lookup from the reprojected pole_gdf
            # (already in axes_crs, so spans plot at the right place).
            pole_xy = {
                str(r["Pole"]): (r.geometry.x, r.geometry.y)
                for _, r in pole_gdf.iterrows()
            }
            with spans_csv.open("r", encoding="utf-8") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    a, b = row.get("pole_a"), row.get("pole_b")
                    if a in pole_xy and b in pole_xy:
                        ax_pt, ay_pt = pole_xy[a]
                        bx_pt, by_pt = pole_xy[b]
                        # Bright amber, thicker (was #1c6dd0/1.2 — the same
                        # blue as the LAZ rectangle, so spans blended into
                        # it). Amber reads clearly on satellite and is
                        # distinct from the green crop boxes + green/red
                        # pole markers.
                        ax.plot([ax_pt, bx_pt], [ay_pt, by_pt],
                                color="#ff9500", linewidth=2.2,
                                alpha=0.9, zorder=4,
                                solid_capstyle="round")
                        n_corridor_spans += 1
    except Exception as e:
        print(f"WARN drawing corridor spans: {e}")

    # Stage 3.5 utility pole network overlay (added 2026-05-28). Reads
    # <run>/<project>_detection/utility_topology/*_pole_network_edges.shp
    # (in EPSG:6424 ftUS) and *_nodes.shp, reprojects to axes_crs, draws
    # the spans as solid blue lines + nodes as small dots colored by
    # degree. Bridge edges drawn dashed orange to distinguish them from
    # pass-1 direct matches.
    n_network_spans = 0
    n_network_nodes = 0
    try:
        _det_dirs = [p for p in run_dir.iterdir()
                     if p.is_dir() and p.name.endswith("_detection")]
        if _det_dirs:
            _topo_dir = sorted(_det_dirs)[0] / "utility_topology"
            _edges_files = list(_topo_dir.glob(
                "*_pole_network_edges.shp"))
            _nodes_files = list(_topo_dir.glob(
                "*_pole_network_nodes.shp"))
            if _edges_files:
                import geopandas as _gpd
                _e = _gpd.read_file(_edges_files[0])
                if axes_crs and _e.crs is not None \
                        and str(_e.crs) != str(axes_crs):
                    _e = _e.to_crs(axes_crs)
                if "is_bridge" in _e.columns:
                    _normal = _e[~_e["is_bridge"].astype(bool)]
                    _bridge = _e[_e["is_bridge"].astype(bool)]
                else:
                    _normal = _e
                    _bridge = _e.iloc[0:0]
                if len(_normal) > 0:
                    _normal.plot(ax=ax, color="#0b4f9c",
                                  linewidth=1.4, alpha=0.9, zorder=5)
                if len(_bridge) > 0:
                    _bridge.plot(ax=ax, color="#e08000",
                                  linewidth=1.3, alpha=0.95,
                                  linestyle="--", zorder=5)
                n_network_spans = len(_e)
            if _nodes_files:
                import geopandas as _gpd
                _n = _gpd.read_file(_nodes_files[0])
                if axes_crs and _n.crs is not None \
                        and str(_n.crs) != str(axes_crs):
                    _n = _n.to_crs(axes_crs)
                # Color by degree — muted palette (less bright than the
                # below-the-map preview PNG, which uses #1a6e2a / #00d24a).
                _deg_color = {0: "#a83838", 1: "#c87830",
                              2: "#3d6b3f", 3: "#225999"}
                for _d, _grp in _n.groupby(
                        _n.get("degree", 0).astype(int) if "degree"
                        in _n.columns else [0] * len(_n)):
                    _c = _deg_color.get(int(_d), "#225999")
                    ax.scatter(_grp.geometry.x, _grp.geometry.y,
                               c=_c, s=34, marker="o",
                               edgecolors="white", linewidths=0.6,
                               zorder=7)
                n_network_nodes = len(_n)
    except Exception as e:
        print(f"WARN drawing utility pole network overlay: {e}")

    # Crop boxes (Stage 2 per-pole crops, added 2026-05-27). Green
    # outlined rectangles from header bboxes of crops/Pole_*.las (or
    # P_???.las for CSV-input runs). Cached in viz/crop_bboxes.json so
    # only NEW files trigger a header read (~5 ms each). Now reads
    # incrementally during Stage 2 instead of waiting for Stage 1 to
    # finish (the prior "crop-area mode" gating).
    n_crop_boxes = 0
    try:
        # Prefer crops_metric/ (post-Stage-0c) over crops/ (raw ftUS),
        # since Firmatek deletes the latter after Stage 0c.
        for sub in ("crops_metric", "crops"):
            crops_dir = run_dir / "02_pole_crop" / "output" / sub
            if not crops_dir.is_dir():
                continue
            # Auto-detect crop naming. Match orchestrator's
            # _detect_crop_pattern() logic (which lives in chain_orchestrator.py
             # and isn't importable here, so inlined). SHP-input runs use
            # Pole_<id>.las; CSV-input runs use P_NNN.las.
            if list(crops_dir.glob("Pole_*.la[sz]")):
                crop_pat = "Pole_*.la[sz]"
            else:
                crop_pat = "P_???.la[sz]"
            crop_files = sorted(crops_dir.glob(crop_pat))
            if not crop_files:
                continue
            # Cache header bboxes — keyed by filename so we only read
            # NEW crops on each render pass.
            cache_path = run_dir / "viz" / "crop_bboxes.json"
            cache = {}
            if cache_path.exists():
                try:
                    cache = json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:
                    cache = {}
            # A crop bbox is only valid once Stage 2 has finished writing
            # the LAS file. A half-written file's header still carries the
            # LAS spec's placeholder bounds (x_min=+DBL_MAX, x_max=-DBL_MAX),
            # which would otherwise be cached as garbage and drawn as a
            # giant rectangle spanning the whole map. Reject non-finite /
            # inverted / absurdly large boxes; such crops get re-read on a
            # later pass once the file is complete (self-healing cache).
            _crop_ok = _valid_crop_bbox  # module-level shared validator

            n_new = 0
            crop_native_crs = None
            for las_path in crop_files:
                # Skip only if already cached, valid, AND carries a point
                # count (5th element). Entries missing npts are re-read so
                # the density overlay can be computed — a cheap header read.
                cached = cache.get(las_path.name)
                if cached is not None and _crop_ok(cached) and len(cached) >= 5:
                    continue
                try:
                    import laspy as _laspy
                    h = _laspy.open(las_path).header
                    bb = [float(h.x_min), float(h.y_min),
                          float(h.x_max), float(h.y_max)]
                    if not _crop_ok(bb):
                        # Half-written / placeholder header — leave it out of
                        # the cache so the next render pass tries again.
                        cache.pop(las_path.name, None)
                        continue
                    try:
                        bb.append(int(h.point_count))  # 5th: density input
                    except Exception:
                        bb.append(0)
                    cache[las_path.name] = bb
                    if crop_native_crs is None:
                        try:
                            crop_native_crs = h.parse_crs()
                        except Exception:
                            pass
                    n_new += 1
                except Exception:
                    continue
            if n_new:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    # Atomic write (temp + replace) so a render that overlaps
                    # another never leaves a half-written / interleaved cache.
                    tmp = cache_path.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(cache, indent=1), encoding="utf-8")
                    tmp.replace(cache_path)
                except Exception:
                    pass

            # Pre-compute per-crop point density so the green fill alpha can
            # be normalized across this run's crops. density = points / area
            # (native CRS units squared). Edge-clipped crops have smaller
            # area so density stays comparable.
            valid_crops = []
            max_density = 0.0
            for fname, bbox in cache.items():
                if not _crop_ok(bbox):
                    continue  # defensive: never draw a garbage box
                x0, y0, x1, y1 = (float(v) for v in bbox[:4])
                npts = float(bbox[4]) if len(bbox) >= 5 else 0.0
                dens = npts / max((x1 - x0) * (y1 - y0), 1.0) if npts else 0.0
                if dens > max_density:
                    max_density = dens
                valid_crops.append((x0, y0, x1, y1, dens))

            # Shared native->axes reprojection so the fill + outline align.
            _tx = None
            if crop_native_crs is not None and axes_crs and \
                    str(crop_native_crs) != str(axes_crs):
                try:
                    from pyproj import Transformer as _Tx
                    _tx = _Tx.from_crs(crop_native_crs, axes_crs,
                                       always_xy=True)
                except Exception:
                    _tx = None

            for (x0, y0, x1, y1, dens) in valid_crops:
                ax0, ay0, ax1, ay1 = x0, y0, x1, y1
                if _tx is not None:
                    try:
                        ax0, ay0 = _tx.transform(x0, y0)
                        ax1, ay1 = _tx.transform(x1, y1)
                    except Exception:
                        pass
                w, hh = ax1 - ax0, ay1 - ay0
                # Density fill (below the outline). Alpha scales 0.04
                # (sparse) -> 0.20 (densest crop in this run). Capped low on
                # purpose: corridor crops overlap ~44%, so up to 3 can stack
                # at a point; 1-(1-0.20)^3 = 0.49, keeping the compounded
                # green at/under 50% so the satellite basemap always shows
                # through. Green only ever appears INSIDE a per-pole crop —
                # never across the whole LAZ extent (per user request).
                if max_density > 0 and dens > 0:
                    a = 0.04 + 0.16 * (dens / max_density)
                    ax.add_patch(mpatches.Rectangle(
                        (ax0, ay0), w, hh, facecolor="#1a8e3a",
                        edgecolor="none", alpha=a, zorder=4))
                # Bright yellow-green outline on top (the per-pole "window").
                ax.add_patch(mpatches.Rectangle(
                    (ax0, ay0), w, hh, linewidth=1.1, edgecolor="#b6ff3a",
                    facecolor="none", alpha=0.6, zorder=5))
                n_crop_boxes += 1
            break  # Only render one sub-dir's worth
    except Exception as e:
        print(f"WARN drawing crop boxes: {e}")

    # Pole candidates.
    n_poles = 0
    # Track what pole markers actually got drawn so the legend matches the
    # map exactly (the legend uses explicit handles=, so scatter label=
    # kwargs are ignored — we must add matching legend entries by hand).
    _drawn_found = _drawn_notfound = _drawn_outside = _drawn_candidate = 0
    if pole_gdf is not None and len(pole_gdf):
        gx = pole_gdf.geometry.x.values
        gy = pole_gdf.geometry.y.values
        n_poles = len(pole_gdf)
        # Stage 0a early-feedback branch (added 2026-05-27): when pole_gdf
        # came from 00_inputs/pole_tops.shp (because Stage 2's detection
        # output isn't there yet), draw distinct cyan/gray small dots that
        # only convey "imported, not yet validated against LAZ" + inside-
        # LAZ coverage status. These get replaced naturally by the bigger
        # star/diamond/X status markers once Stage 2 produces
        # poles_candidates_loose.shp.
        if pole_source_imported:
            # Stage 0a early-feedback: small dots only (no Stage 2/3 body
            # coloring yet). Only split inside/outside when the LAZ extent is
            # actually known (Stage 0a laz_extent.json or Stage 0b span stats);
            # otherwise label them plainly so the map does not assert LAZ
            # coverage that has not been computed.
            laz_known = bool(getattr(pole_gdf, "attrs", {}).get("laz_known"))
            if laz_known and "inside_laz" in pole_gdf.columns:
                inside = pole_gdf["inside_laz"].astype(bool).values
                if inside.any():
                    ax.scatter(gx[inside], gy[inside], s=40, c="#19c4d8",
                               marker="o", edgecolors="white", linewidths=0.6,
                               zorder=6,
                               label=f"imported, inside LAZ ({int(inside.sum())})")
                if (~inside).any():
                    ax.scatter(gx[~inside], gy[~inside], s=40, c="#a0a0a0",
                               marker="o", edgecolors="white", linewidths=0.6,
                               zorder=6,
                               label=f"imported, outside LAZ ({int((~inside).sum())})")
            else:
                ax.scatter(gx, gy, s=40, c="#19c4d8", marker="o",
                           edgecolors="white", linewidths=0.6, zorder=6,
                           label=f"imported pole tops ({n_poles})")
        else:
            # Color candidates by whether stage 3 pole-vec found a body for
            # this candidate. Body_Lines.shp pole_id "P_NNN" maps to
            # candidate FID = N. Green = body found; red = candidate but
            # no body (Stage 3 failed for this pole).
            body_pole_ids: set = set()
            if body_gdf is not None and "pole_id" in body_gdf.columns:
                # PoleVec full mode writes pole_id as "PoleTop_P_NNN"; the
                # body-only-pointconv variant writes plain "P_NNN". Strip the
                # PoleTop_ prefix so both forms match the "Pole" column in
                # poles_candidates_loose.shp ("P_NNN").
                body_pole_ids = {
                    str(pid).replace("PoleTop_", "")
                    for pid in body_gdf["pole_id"].tolist()
                }
            # Map each row of pole_gdf to its canonical "P_NNN" id. Prefer
            # the "Pole" column (written by csv_pole_tops_to_shapefile.py for
            # Firmatek runs) over the gdf row index — the row index would
            # only match when the CSV had no extent-filtering, which is
            # almost never the case in production.
            if "Pole" in pole_gdf.columns:
                row_pole_ids = list(pole_gdf["Pole"].astype(str))
            else:
                row_pole_ids = [f"P_{i:03d}" for i in range(len(pole_gdf))]
            if body_pole_ids:
                found = []        # body found AND wires attached (or no wire check)
                orphan = []       # body found BUT no wires attached
                missed = []       # no body produced (Stage 3 failed)
                for i, (cx, cy) in enumerate(zip(gx, gy)):
                    pid = row_pole_ids[i] if i < len(row_pole_ids) else f"P_{i:03d}"
                    if pid in body_pole_ids:
                        if pid in wire_orphan_ids:
                            orphan.append((cx, cy))
                        else:
                            found.append((cx, cy))
                    else:
                        missed.append((cx, cy))
                # Marker sizes + distinctive shapes (2026-05-27): the prior
                # plain green circles got lost inside the green crop-area
                # rectangles. Use a 5-point star for "body found", a thick
                # diamond for "orphan (no wires)", and a bold X for "missed".
                # White edges + zorder=6 keep them readable against the
                # satellite imagery underneath.
                if found:
                    fx, fy = zip(*found)
                    ax.scatter(fx, fy, s=180, c="#00d24a", marker="*",
                               edgecolors="white", linewidths=1.5, zorder=6)
                if orphan:
                    ox_, oy_ = zip(*orphan)
                    ax.scatter(ox_, oy_, s=140, c=COLOR_POLE_NO_WIRE,
                               marker="D", edgecolors="white",
                               linewidths=1.5, zorder=6)
                if missed:
                    mx, my = zip(*missed)
                    ax.scatter(mx, my, s=160, c="#ff1f1c", marker="X",
                               edgecolors="white", linewidths=1.5, zorder=6)
            else:
                # Stage 2 done but stage 3 hasn't produced bodies yet.
                # On the validation chain (no Stage 3 at all) this is the
                # terminal state — so color markers by DETECTION status
                # (found / not_found) which is the key QC signal: did we
                # actually find each customer pole in the LiDAR?
                detect_status = _load_detection_status(run_dir)
                if detect_status:
                    # Differentiate the two miss types so the operator can
                    # tell a coverage gap (red X, outside LAZ) from a genuine
                    # detection miss (orange X, searched but not found).
                    found_xy, notfound_xy, outside_xy = [], [], []
                    for i, (cx, cy) in enumerate(zip(gx, gy)):
                        pid = (row_pole_ids[i] if i < len(row_pole_ids)
                               else f"P_{i:03d}")
                        st = detect_status.get(pid, "")
                        if st == "found":
                            found_xy.append((cx, cy))
                        elif st == "no_las_file":
                            outside_xy.append((cx, cy))   # outside LAZ coverage
                        else:
                            # not_found / unknown → searched but not found
                            notfound_xy.append((cx, cy))
                    if found_xy:
                        fx, fy = zip(*found_xy)
                        ax.scatter(fx, fy, s=150, c="#00d24a", marker="*",
                                   edgecolors="white", linewidths=1.4,
                                   zorder=7)
                        _drawn_found = len(found_xy)
                    if notfound_xy:
                        mx, my = zip(*notfound_xy)
                        ax.scatter(mx, my, s=130, c="#ff8c1a", marker="X",
                                   edgecolors="white", linewidths=1.4,
                                   zorder=7)
                        _drawn_notfound = len(notfound_xy)
                    if outside_xy:
                        ox2, oy2 = zip(*outside_xy)
                        ax.scatter(ox2, oy2, s=130, c="#ff1f1c", marker="X",
                                   edgecolors="white", linewidths=1.4,
                                   zorder=7)
                        _drawn_outside = len(outside_xy)
                else:
                    # No detection CSV yet (mid-Stage-2): plain cyan dots
                    # so the operator can still count + locate poles. Keep
                    # small so they don't block the satellite view.
                    ax.scatter(gx, gy, s=22, marker="o",
                               c="#19c4d8", edgecolors="white",
                               linewidths=0.5, alpha=0.95, zorder=6)
                    _drawn_candidate = n_poles

    # Pole bodies (lines).
    n_bodies = 0
    if body_gdf is not None and len(body_gdf):
        body_gdf.plot(ax=ax, color=COLOR_POLE_BODY, linewidth=1.0, zorder=6)
        n_bodies = len(body_gdf)

    # Curblines and EOP.
    n_curbs = 0
    if curb_gdf is not None and len(curb_gdf):
        curb_gdf.plot(ax=ax, color=COLOR_CURBLINE, linewidth=0.8, zorder=7)
        n_curbs = len(curb_gdf)
    n_eops = 0
    if eop_gdf is not None and len(eop_gdf):
        eop_gdf.plot(ax=ax, color=COLOR_EOP, linewidth=0.8, zorder=7)
        n_eops = len(eop_gdf)

    # Tree canopy (filled green, low z) + trunks (brown triangles, on top).
    n_canopy = 0
    if tree_canopy_gdf is not None and len(tree_canopy_gdf):
        try:
            tree_canopy_gdf.plot(ax=ax, color="#228B22", alpha=0.28,
                                 edgecolor="#1b6b1b", linewidth=0.4, zorder=5)
            n_canopy = len(tree_canopy_gdf)
        except Exception as e:
            print(f"WARN drawing tree canopy overlay: {e}")
    n_trunks = 0
    if tree_stems_gdf is not None and len(tree_stems_gdf):
        try:
            tree_stems_gdf.plot(ax=ax, color="#8B4513", marker="^",
                                markersize=20, edgecolor="white",
                                linewidth=0.4, zorder=8)
            n_trunks = len(tree_stems_gdf)
        except Exception as e:
            print(f"WARN drawing tree trunk overlay: {e}")

    # LAZ coverage extent rectangle (Stage 0b output, added 2026-05-27).
    # A thin dashed gray rectangle so the operator can SEE coverage
    # boundaries vs imported pole positions right after Stage 0b finishes
    # (~30 s) — well before Stage 2 produces detection markers (5+ min).
    # Drawn from span_statistics.json's laz_bbox_in_pole_crs (Stage 0b) or
    # 00_inputs/laz_extent.json (Stage 0a early-feedback); reprojected
    # to axes_crs when the dashboard is in metric mode (Firmatek post-0c).
    try:
        sp = _read_laz_bbox_facts(run_dir)
        bb = sp.get("laz_bbox_in_pole_crs") or sp.get("laz_bbox") or {}
        if bb and all(k in bb for k in ("xmin", "ymin", "xmax", "ymax")):
            sp_crs = sp.get("pole_crs")
            xmin, ymin = bb["xmin"], bb["ymin"]
            xmax, ymax = bb["xmax"], bb["ymax"]
            if sp_crs and axes_crs and str(sp_crs) != str(axes_crs):
                try:
                    from pyproj import Transformer as _Tx
                    tx = _Tx.from_crs(sp_crs, axes_crs, always_xy=True)
                    xmin, ymin = tx.transform(xmin, ymin)
                    xmax, ymax = tx.transform(xmax, ymax)
                except Exception as e:
                    print(f"WARN reproject laz_bbox -> axes_crs: {e}")
            # Color #1c6dd0 (blue) + dashed lw=1.5 matches the existing
            # legend entry at this file ~line 1973 — keep them in sync.
            laz_rect = mpatches.Rectangle(
                (xmin, ymin), xmax - xmin, ymax - ymin,
                linewidth=1.5, linestyle="--",
                edgecolor="#1c6dd0", facecolor="none",
                zorder=4, label="LAZ coverage extent",
            )
            ax.add_patch(laz_rect)
    except Exception as e:
        print(f"WARN drawing LAZ extent rectangle: {e}")

    # Extents. Priority chain:
    #   1. PointCONV tiles (Aecon — always preferred when present)
    #   2. LAZ extent from span_statistics.json (Firmatek pre-PointCONV)
    #   3. Pole-cropping tile cache (Firmatek mid-Stage 2)
    #   4. Pole candidates bounding box (last-resort fallback)
    extents_xs: list[float] = []
    extents_ys: list[float] = []
    if tiles:
        extents_xs += [t["core_min_x"] for t in tiles]
        extents_xs += [t["core_max_x"] for t in tiles]
        extents_ys += [t["core_min_y"] for t in tiles]
        extents_ys += [t["core_max_y"] for t in tiles]
    else:
        try:
            sp = _read_laz_bbox_facts(run_dir)
            bb = sp.get("laz_bbox_in_pole_crs") or sp.get("laz_bbox") or {}
            if bb:
                # CRS-safety (fixed 2026-05-27): the JSON's bbox is in
                # span_statistics.json's pole_crs (typically EPSG:6424
                # for Firmatek). The axes are in axes_crs (EPSG:26911
                # for Firmatek post-Stage 0c). Without this reproject,
                # the axis limits get set in ftUS coordinates while
                # the reprojected pole markers + tile rectangles are
                # in meters — markers fall off the visible map entirely.
                sp_crs = sp.get("pole_crs")
                xmin, ymin = bb["xmin"], bb["ymin"]
                xmax, ymax = bb["xmax"], bb["ymax"]
                if sp_crs and axes_crs and str(sp_crs) != str(axes_crs):
                    try:
                        from pyproj import Transformer as _Tx
                        tx = _Tx.from_crs(sp_crs, axes_crs, always_xy=True)
                        xmin, ymin = tx.transform(xmin, ymin)
                        xmax, ymax = tx.transform(xmax, ymax)
                    except Exception as e:
                        print(f"WARN reproject laz_bbox extents -> "
                              f"axes_crs: {e}")
                extents_xs += [xmin, xmax]
                extents_ys += [ymin, ymax]
        except Exception:
            pass
        if not extents_xs and n_pc_tiles:
            # Use the pole-crop tile cache bounds.
            try:
                cache = json.loads((run_dir / "viz" / "tile_bboxes.json")
                                    .read_text(encoding="utf-8"))
                for bbox in cache.values():
                    extents_xs += [bbox[0], bbox[2]]
                    extents_ys += [bbox[1], bbox[3]]
            except Exception:
                pass
        # Always include imported pole positions so poles OUTSIDE the LAZ
        # coverage box stay visible (the LAZ bbox alone would crop them out --
        # e.g. La Verne, where only 14 of 252 poles fall inside the LAZ).
        if pole_gdf is not None and len(pole_gdf):
            extents_xs += [float(pole_gdf.geometry.x.min()),
                           float(pole_gdf.geometry.x.max())]
            extents_ys += [float(pole_gdf.geometry.y.min()),
                           float(pole_gdf.geometry.y.max())]
    if extents_xs and extents_ys:
        # Pad as a fraction of extent so the map scales sensibly across
        # both ~100m clip_100m runs and ~20km Verizon corridors.
        dx = max(extents_xs) - min(extents_xs)
        dy = max(extents_ys) - min(extents_ys)
        pad = max(50.0, 0.02 * max(dx, dy))
        ax.set_xlim(min(extents_xs) - pad, max(extents_xs) + pad)
        ax.set_ylim(min(extents_ys) - pad, max(extents_ys) + pad)

    # Public basemap (Esri WorldImagery satellite by default).
    # Opt-out via env var DASHBOARD_BASEMAP=none. Contextily fetches
    # XYZ tiles in WebMercator and reprojects them to the axes CRS;
    # disk-cached at ~/.cache/contextily/ so subsequent renders are
    # free. Falls back silently when contextily isn't installed,
    # network is down, or the axes CRS is missing.
    basemap_mode = os.environ.get("DASHBOARD_BASEMAP", "satellite").lower()
    if basemap_mode != "none" and extents_xs:
        try:
            import contextily as cx
            providers = {
                "satellite":   cx.providers.Esri.WorldImagery,
                "streets":     cx.providers.OpenStreetMap.Mapnik,
                "light":       cx.providers.CartoDB.Positron,
                "dark":        cx.providers.CartoDB.DarkMatter,
                "voyager":     cx.providers.CartoDB.Voyager,
            }
            provider = providers.get(basemap_mode,
                                      cx.providers.Esri.WorldImagery)
            # axes_crs was detected earlier in render() (before the
            # geometry loads, via _detect_axes_crs) and is reused here so
            # the basemap fetch agrees with the plotted geometry CRS.
            if axes_crs:
                # 2026-05-25: zoom="auto" picks ~22 for tiny extents
                # (Firmatek small-data runs), past Esri WorldImagery's
                # max-coverage zoom (~19), so tiles come back as
                # "Map data not yet available" placeholders. Pick zoom
                # from extent size — assumes axes_crs is feet (ftUS) or
                # meters (UTM) and the LiDAR coverage area dictates how
                # much detail makes sense. Caps at 19 (Esri's max).
                ext_w = max(extents_xs) - min(extents_xs)
                ext_h = max(extents_ys) - min(extents_ys)
                ext_max = max(ext_w, ext_h)
                # Convert ftUS to meters for the threshold check
                # (Firmatek EPSG:6424 axes are in feet).
                if axes_crs and "6424" in str(axes_crs):
                    ext_max_m = ext_max * 0.3048
                else:
                    ext_max_m = ext_max  # already metric
                if ext_max_m < 200:
                    bm_zoom = 19
                elif ext_max_m < 2000:
                    bm_zoom = 17
                elif ext_max_m < 20000:
                    bm_zoom = 15
                elif ext_max_m < 200000:
                    bm_zoom = 13
                else:
                    bm_zoom = 11
                cx.add_basemap(ax, crs=axes_crs, source=provider,
                               zoom=bm_zoom, attribution_size=6,
                               zorder=0)
        except Exception as e:
            # Log + continue. Most common: no network, contextily
            # missing, or unsupported CRS. The geometry layers
            # already drew so the map is still useful.
            print(f"[basemap] skipped: {e}")
    ax.set_aspect("equal")
    # Pull CRS from the candidate shapefile if available; otherwise
    # leave it blank rather than hard-code Aecon's EPSG:26917.
    crs_label = ""
    try:
        if pole_gdf is not None and pole_gdf.crs is not None:
            crs_label = f" ({pole_gdf.crs.to_string()})"
    except Exception:
        pass
    ax.set_xlabel(f"X{crs_label}")
    ax.set_ylabel(f"Y{crs_label}")
    ax.grid(True, linestyle=":", linewidth=0.4, alpha=0.6)

    # Header text.
    total = len(tiles)
    p, i, d = status_counts["pending"], status_counts["inferring"], status_counts["done"]
    pct = 100.0 * d / max(total, 1)
    # Workflow type in the matplotlib title (mirrors the HTML h1 set
    # further up) — both now come from the single shared _detect_workflow
    # classifier (this used to be a second, drifted copy of the logic).
    _wt = _detect_workflow(run_dir)
    # stage_order — used below to gate the PointCONV title line.
    _so = _chain_signals(run_dir)[0]
    _proj = (run_dir.parent.parent.name
             if run_dir.parent.name in ("_runs", "_stage_runs")
             else run_dir.parent.name)
    title_lines = [
        f"{_wt}: {_proj} — pipeline progress",
    ]
    # The PointCONV line only belongs on chains that actually run Stage 1.
    # The validation chain (Stages 0a/0b/2) has no PointCONV, so the old
    # unconditional "preprocessed 0/0 | inference 0/0 tiles (0.0%)" line was
    # just noise — drop it when Stage 1 isn't in the chain / has no tiles.
    if "stage1_pointconv" in _so or total or sources:
        title_lines.append(
            f"Stage 1 PointCONV:  preprocessed {len(sources)}/{len(sources)} | "
            f"inference {d}/{total} tiles done ({pct:.1f}%)")
    extras = []
    if n_poles:
        if pole_source_imported:
            # Stage 0a fallback: dashboard pole markers come from
            # 00_inputs/pole_tops.shp instead of Stage 2's detection
            # output. Label accordingly + show inside/outside-LAZ split
            # if Stage 0b already supplied coverage info.
            try:
                n_inside = int(pole_gdf["inside_laz"].astype(bool).sum())
                n_outside = n_poles - n_inside
                if n_outside:
                    extras.append(
                        f"Stage 0a: {n_poles} imported "
                        f"({n_inside} in LAZ, {n_outside} outside)"
                    )
                else:
                    extras.append(f"Stage 0a: {n_poles} pole tops imported")
            except Exception:
                extras.append(f"Stage 0a: {n_poles} pole tops imported")
        else:
            extras.append(f"Stage 2: {n_poles} pole candidates")
    if n_bodies:
        extras.append(f"Stage 3: {n_bodies} pole bodies")
    if n_curbs or n_eops:
        extras.append(f"Stage 4: {n_curbs} curblines, {n_eops} EOP")
    if extras:
        title_lines.append(" | ".join(extras))
    title_lines.append(f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    ax.set_title("\n".join(title_lines), fontsize=10, loc="left")

    # Legend — only include items that are actually drawn.
    legend_items = []
    if n_pc_tiles:
        legend_items.append(mpatches.Patch(
            facecolor=COLOR_DONE, edgecolor="#0d5c1a", alpha=0.45,
            label=f"pole-crop tile written ({n_pc_tiles})"))
    # LAZ extent dashed outline — present iff span_statistics.json has bbox.
    try:
        if (run_dir / "viz" / "span_statistics.json").exists():
            legend_items.append(plt.Line2D([0], [0], color="#1c6dd0",
                                            linestyle="--", lw=1.5,
                                            label="source LAZ extent"))
    except Exception:
        pass
    # Corridor span lines (added 2026-05-27). The "first-estimate"
    # qualifier is important — these are PROXIMITY-based pole-pair
    # connections from pole-cropping's nearest-neighbor scan, NOT
    # results of any wire-detection / wire-extraction step. Real wire
    # topology comes from Stage 3 (PointCONV class-14 wire points +
    # pole-vec catenary fits). See docs/stages/2_pole_cropping.md.
    if n_corridor_spans:
        legend_items.append(plt.Line2D([0], [0], color="#ff9500",
                                        lw=2.2,
                                        label=f"corridor span "
                                              f"({n_corridor_spans}, "
                                              f"first-est. Utility Network "
                                              f"Topology, pre-wire)"))
    # Crop box outlines (brightened to #b6ff3a 2026-05-28 for visibility
    # on satellite imagery — keep in sync with the rectangle draw above).
    if n_crop_boxes:
        legend_items.append(mpatches.Patch(
            facecolor="none", edgecolor="#b6ff3a", linewidth=1.1,
            label=f"per-pole crop box ({n_crop_boxes})"))
    # Stage 3.5 utility pole network (added 2026-05-28).
    if n_network_spans:
        legend_items.append(plt.Line2D([0], [0], color="#0b4f9c",
                                        lw=1.4,
                                        label=f"utility span "
                                              f"({n_network_spans}, "
                                              f"Stage 3.5)"))
        legend_items.append(plt.Line2D(
            [0], [0], color="#e08000", lw=1.3, linestyle="--",
            label="utility span (bridge edge)"))
        legend_items.append(plt.Line2D(
            [0], [0], marker="o", color="w", markerfacecolor="#3d6b3f",
            markersize=8, linewidth=0,
            label=f"pole network node ({n_network_nodes})"))
    if tiles:
        legend_items += [
            # Tile-status legend entries removed 2026-05-27 per user
            # request. The visual representation of PointCONV tile
            # progress moved to a dedicated inference-progress card
            # below the main map (see task #138 / FEEDBACK_PLAN §
            # "Wave B inference card"). The tiles themselves are
            # still drawn as colored rectangles on the map; just the
            # legend chips were removed to declutter.
        ]
    if len(traj):
        legend_items.append(plt.Line2D([0], [0], color=COLOR_TRAJECTORY, lw=1.0,
                                        label="trajectory"))
    if n_poles:
        # When bodies have been computed, candidates are colored by
        # body-found status (green) vs body-missing (red X).
        if n_bodies:
            legend_items.append(plt.Line2D([0], [0], marker="*", color="white",
                                            markerfacecolor="#00d24a",
                                            markeredgecolor="white",
                                            markersize=12,
                                            label="pole (body + wires)"
                                                if wire_orphan_ids
                                                else "pole (body found)",
                                            linestyle=""))
            if wire_orphan_ids:
                legend_items.append(plt.Line2D([0], [0], marker="D",
                                                color="white",
                                                markerfacecolor=COLOR_POLE_NO_WIRE,
                                                markeredgecolor="white",
                                                markersize=9,
                                                label="pole (body, no wires)",
                                                linestyle=""))
            legend_items.append(plt.Line2D([0], [0], marker="X",
                                            color="white",
                                            markerfacecolor="#ff1f1c",
                                            markeredgecolor="white",
                                            markersize=10,
                                            label="pole (no body)",
                                            linestyle=""))
            legend_items.append(plt.Line2D([0], [0], color=COLOR_POLE_BODY,
                                            lw=1.0, label="pole body line"))
        elif pole_source_imported:
            # Stage 0a early-feedback markers: cyan (inside LAZ) + gray
            # (outside, when known from Stage 0b's coverage check).
            try:
                _n_in = int(pole_gdf["inside_laz"].astype(bool).sum())
            except Exception:
                _n_in = n_poles
            legend_items.append(plt.Line2D([0], [0], marker="o", color="white",
                                            markerfacecolor="#19c4d8",
                                            markeredgecolor="white",
                                            markersize=6,
                                            label=f"imported, inside LAZ ({_n_in})",
                                            linestyle=""))
            if _n_in < n_poles:
                legend_items.append(plt.Line2D([0], [0], marker="o", color="white",
                                                markerfacecolor="#a0a0a0",
                                                markeredgecolor="white",
                                                markersize=6,
                                                label=f"imported, outside LAZ ({n_poles - _n_in})",
                                                linestyle=""))
        else:
            # Validation chain (Stage 2 done, no Stage 3). Mirror EXACTLY
            # what the pole-marker drawing produced above so the legend
            # symbols match the map: green star = detected, orange X =
            # searched but not found, red X = outside LAZ coverage (all from
            # the detection CSV), or a blue circle = candidate when the CSV
            # isn't available yet.
            if _drawn_found:
                legend_items.append(plt.Line2D([0], [0], marker="*",
                                                color="white",
                                                markerfacecolor="#00d24a",
                                                markeredgecolor="white",
                                                markersize=12, linestyle="",
                                                label=f"pole detected ({_drawn_found})"))
            if _drawn_notfound:
                legend_items.append(plt.Line2D([0], [0], marker="X",
                                                color="white",
                                                markerfacecolor="#ff8c1a",
                                                markeredgecolor="white",
                                                markersize=10, linestyle="",
                                                label=f"searched but not found ({_drawn_notfound})"))
            if _drawn_outside:
                legend_items.append(plt.Line2D([0], [0], marker="X",
                                                color="white",
                                                markerfacecolor="#ff1f1c",
                                                markeredgecolor="white",
                                                markersize=10, linestyle="",
                                                label=f"outside LAZ coverage ({_drawn_outside})"))
            if _drawn_candidate:
                legend_items.append(plt.Line2D([0], [0], marker="o",
                                                color="white",
                                                markerfacecolor="#19c4d8",
                                                markeredgecolor="white",
                                                markersize=7, linestyle="",
                                                label=f"pole candidate ({_drawn_candidate})"))
    if n_curbs:
        legend_items.append(plt.Line2D([0], [0], color=COLOR_CURBLINE, lw=1.0,
                                       label="curbline"))
    if n_eops:
        legend_items.append(plt.Line2D([0], [0], color=COLOR_EOP, lw=1.0, label="EOP"))
    if n_trunks:
        legend_items.append(plt.Line2D([0], [0], marker="^", color="#8B4513",
                                       markeredgecolor="white", markersize=8,
                                       linestyle="",
                                       label=f"tree trunk ({n_trunks})"))
    if n_canopy:
        legend_items.append(mpatches.Patch(facecolor="#228B22", alpha=0.45,
                                           edgecolor="#1b6b1b",
                                           label=f"tree canopy ({n_canopy})"))
    ax.legend(handles=legend_items, loc="lower left", fontsize=8, framealpha=0.9)

    # Disclaimer caption: corridor spans are proximity-based topology
    # ESTIMATES, not the final wire topology (which comes from Stage 3
    # PointCONV class-14 + pole-vec catenary fits). Note this on the
    # graphic itself so a viewer doesn't mistake it for a wired-network
    # diagram. Only render the caption when spans are actually drawn.
    if n_corridor_spans:
        ax.text(0.99, 0.01,
                "Corridor spans = first-estimate topology (proximity-based, "
                "pre-wire-extraction). Stage 3 produces final wire topology.",
                transform=ax.transAxes,
                ha="right", va="bottom",
                fontsize=7, color="#555555", alpha=0.85,
                bbox=dict(boxstyle="round,pad=0.3",
                          facecolor="white", edgecolor="#cccccc",
                          alpha=0.85, linewidth=0.5))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"[{time.strftime('%H:%M:%S')}] wrote {out_png}  "
          f"(tiles done {d}/{total}, merged {n_merged}/{len(sources)})")

    # G1 — update rate history + compute ETAs for stages with progress counters.
    try:
        progress = _count_stage_progress(run_dir, manifest, tiles, sources)
        hist = _update_rate_history(run_dir, progress)
        rates = _compute_rates(hist)
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] rate-history error: {e}")
        progress, rates = {}, {}

    # Also write the HTML dashboard next to the PNG.
    out_html = out_png.parent / "progress.html"
    try:
        _render_html(run_dir, out_html, out_png.name,
                     stage_counts={
                         "stage1": {"done": d, "total": total,
                                    "merged": n_merged, "sources_total": len(sources)},
                         "stage2_candidates": n_poles,
                         "stage3_bodies": n_bodies,
                         "stage4_curbs": n_curbs,
                         "stage4_eops": n_eops,
                     },
                     progress_counts=progress, rates=rates)
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] html render error: {e}")


def _pole_files(dirpath: Path, suffix: str = "la[sz]") -> list:
    """Per-pole files matching EITHER id convention, in ONE place so no caller
    has to remember both: P_NNN (CSV/lat-lon input) OR Pole_NNN (SHP /
    *_POLES.shp input, e.g. La Verne). `suffix` is a glob suffix, e.g. 'png',
    'las', 'la[sz]'. Centralizing this prevents single-variant globs from
    silently hiding one input type's files (the recurring layout bug:
    a 'Pole_*'-only glob hid the card on CSV runs; a 'P_*'-only glob hid it
    on SHP runs)."""
    if not dirpath.is_dir():
        return []
    return sorted(list(dirpath.glob(f"P_*.{suffix}"))
                  + list(dirpath.glob(f"Pole_*.{suffix}")))


def _crop_files(crops_dir: Path) -> list:
    """Per-pole crop LAS/LAZ for either variant (see _pole_files)."""
    return _pole_files(crops_dir, "la[sz]")


def _stage2_phase_card(run_dir: Path) -> str:
    """Stage 2's five internal phases as a checklist (Option-1 display/status
    split — pole-cropping still runs as ONE process). Each phase's state comes
    from its DURABLE canonical output; a downstream output implies the upstream
    phase finished. Returns '' before Stage 2 starts."""
    out = run_dir / "02_pole_crop" / "output"
    if not out.is_dir():
        return ""
    dets = [p for p in run_dir.iterdir()
            if p.is_dir() and p.name.endswith("_detection")]
    det = sorted(dets)[0] if dets else None
    proj = det.name[:-len("_detection")] if det else ""
    summary = (det / f"{proj}_detection_summary.html") if det else None
    detcsv = out / "detection" / "pole_detection_results.csv"
    corr = out / "corridor_spans" / "corridor_manifest.csv"
    spans_dir = out / "corridor_spans" / "spans"
    n_crops = len(_pole_files(out / "crops", "la[sz]"))
    n_spans = len(list(spans_dir.glob("*.la[sz]"))) if spans_dir.is_dir() else 0
    # Cumulative done flags: a downstream output implies upstream finished.
    report_done = bool(summary and summary.exists())
    corr_done = corr.exists() or report_done
    detect_done = detcsv.exists() or corr_done
    crop_done = detect_done
    tile_done = crop_done or n_crops > 0
    dcount = ""
    if detcsv.exists():
        try:
            import csv as _csv
            sc = {}
            with open(detcsv, encoding="utf-8") as f:
                for r in _csv.DictReader(f):
                    sc[r.get("status", "")] = sc.get(r.get("status", ""), 0) + 1
            dcount = (f"{sc.get('found',0)} found &middot; "
                      f"{sc.get('not_found',0)} missed &middot; "
                      f"{sc.get('no_las_file',0)} outside")
        except Exception:  # noqa: BLE001
            dcount = ""

    def _st(done, prior):
        return "done" if done else ("run" if prior else "pend")

    rows = [
        ("Tile", "source LAZ &rarr; tiles/", _st(tile_done, True), ""),
        ("Crop", "tiles + pole_tops &rarr; crops/", _st(crop_done, tile_done),
         f"{n_crops} crops" if n_crops else ""),
        ("Detect", "crops &rarr; detection/", _st(detect_done, crop_done), dcount),
        ("Corridor", "poles &rarr; corridor_spans/", _st(corr_done, detect_done),
         f"{n_spans} spans" if n_spans else ""),
        ("Report", "&rarr; &lt;project&gt;_detection/", _st(report_done, corr_done),
         "PDF &middot; HTML" if report_done else ""),
    ]
    badge = {"done": ("&#10003;", "#2e9b3a"), "run": ("&#10227;", "#ffb000"),
             "pend": ("&bull;", "#c0c0c0")}
    li = ""
    for name, io, state, detail in rows:
        sym, col = badge[state]
        d = f' <span style="color:#888;">&mdash; {detail}</span>' if detail else ""
        li += (f'<li style="margin:3px 0;">'
               f'<span style="color:{col};font-weight:700;">{sym}</span> '
               f'<b>{name}</b> '
               f'<span style="color:#99a;font-size:11px;">{io}</span>{d}</li>')
    return ('<div class="log-card">'
            '<h3 style="margin:0 0 4px 0;font-size:14px;">Stage 2 &mdash; phases</h3>'
            '<div style="font-size:11px;color:#888;margin-bottom:6px;">'
            'Tile &rarr; Crop &rarr; Detect &rarr; Corridor &rarr; Report &mdash; '
            'one process; each phase writes a canonical output.</div>'
            f'<ul style="list-style:none;padding-left:2px;margin:0;'
            f'font-size:13px;line-height:1.4;">{li}</ul></div>')


def render_pole_crops_panel(run_dir: Path, out_png: Path) -> None:
    """Render a sidecar PNG focused on per-pole crops (Stage 2 phase 2).

    Stage 2 first tiles the raw LAZ into ~13M-point chunks
    (progress.png shows that via the green rectangles), then crops a
    square around each customer pole. This panel zooms in on the
    per-pole granularity: one square per crop, colored by whether the
    *.las file has been written yet.

    Sources:
      02_pole_crop/poles_candidates_loose.shp    pole locations (XY)
      02_pole_crop/output/crops/P_*.las          written crops (header bbox)

    Same satellite basemap + dashed LAZ outline as progress.png.
    No-ops silently if neither the candidates shp nor any crop file
    exists (Stage 2 hasn't started cropping yet).
    """
    pole_shp = run_dir / "02_pole_crop" / "poles_candidates_loose.shp"
    # Firmatek profile deletes crops/ftUS after Stage 0c (per the disk
    # cleanup rules), leaving the metric crops in crops_metric/. Prefer
    # crops_metric/ when it exists so the pole_crops panel still renders
    # after Stage 0c. The crop bboxes are read header-only and are in
    # the LAS file's native CRS — get reprojected later via axes_crs.
    crops_dir = run_dir / "02_pole_crop" / "output" / "crops_metric"
    if not crops_dir.is_dir() or not _crop_files(crops_dir):
        crops_dir = run_dir / "02_pole_crop" / "output" / "crops"
    if not pole_shp.exists():
        return  # Stage 2 hasn't even filtered candidates yet
    has_crops = bool(_crop_files(crops_dir))
    # Don't render before Stage 2 starts cropping — would just duplicate
    # the main progress.png view.
    if not has_crops:
        return

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as _mpatches
    import geopandas as gpd
    import numpy as np

    try:
        gdf = gpd.read_file(pole_shp)
    except Exception:
        return
    if gdf.empty:
        return
    # Reproject pole shp to the same CRS the crop bboxes will be in
    # (== axes_crs). On Firmatek post-Stage-0c, pole_shp is ftUS but
    # crops_metric is EPSG:26911 — without this, pole dots and crop
    # rectangles plot in different coordinate ranges and the panel
    # is unusable.
    panel_crs = _detect_axes_crs(run_dir, fallback_gdf=gdf)
    gdf = _reproject_gdf(gdf, panel_crs)

    # Cache crop bboxes (header-only reads).
    cache_path = run_dir / "viz" / "crop_bboxes.json"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    n_new = 0
    if crops_dir.is_dir():
        import laspy as _laspy
        for las_path in _crop_files(crops_dir):
            ce = cache.get(las_path.name)
            if ce is not None and len(ce) >= 5:
                continue  # already cached WITH point count
            try:
                h = _laspy.open(las_path).header
                cache[las_path.name] = [float(h.x_min), float(h.y_min),
                                         float(h.x_max), float(h.y_max),
                                         int(h.point_count)]
                n_new += 1
            except Exception:
                continue
    if n_new:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, indent=1),
                                  encoding="utf-8")
        except Exception:
            pass
    n_written = len(cache)
    n_total = len(gdf)

    # Figure aspect from data extent.
    xs = gdf.geometry.x.values
    ys = gdf.geometry.y.values
    dx = float(xs.max() - xs.min())
    dy = float(ys.max() - ys.min())
    data_aspect = dx / max(dy, 1e-6)
    fig_w = 18.0
    fig_h = max(4.0, min(14.0, fig_w / max(data_aspect, 0.5)))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=110)

    # Per-pole squares + dots. Crops are square in source CRS, so
    # we use the cached bbox if available, otherwise fall back to
    # a small symbolic square at the pole center (pending state).
    # Prefer the "Pole" column written by csv_pole_tops_to_shapefile.py
    # (Firmatek: values like "P_394"). Row-index fallback for Aecon /
    # legacy shapefiles that don't carry a Pole column.
    if "Pole" in gdf.columns:
        pole_ids = list(gdf["Pole"].astype(str))
    else:
        pole_ids = [f"P_{i:03d}" for i in range(len(gdf))]
    import os as _os
    # Map "P_NNN" -> cached bbox (+ point count). Crops are .laz under
    # compress_intermediates (or .las); strip either suffix so the pole id
    # from the "Pole" column matches the cached filename.
    crop_by_id = {_os.path.splitext(n)[0]: bb for n, bb in cache.items()}
    written_ids = set(crop_by_id.keys())

    # Estimate the typical crop half-size from the first VALID cached bbox
    # (skip half-written placeholder bboxes — using one would blow up
    # half_size to ~DBL_MAX and wreck the axis limits + placeholder squares).
    half_size = None
    for bbox in crop_by_id.values():
        if _valid_crop_bbox(bbox[:4]):
            half_size = (bbox[2] - bbox[0]) / 2.0
            break
    if half_size is None:
        # Sane default: small placeholder square (~30 m / 100 ft).
        half_size = 30.0

    # Per-crop point density (points / area), normalized across this run so
    # the green fill opacity is comparable crop-to-crop. This is the headline
    # of the panel: where the LiDAR is dense inside each pole crop.
    max_density = 0.0
    for bbox in crop_by_id.values():
        if _valid_crop_bbox(bbox[:4]) and len(bbox) >= 5 and bbox[4]:
            area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 1.0)
            d = float(bbox[4]) / area
            if d > max_density:
                max_density = d

    written_xy = []
    pending_xy = []
    for pid, x, y in zip(pole_ids, xs, ys):
        if pid in written_ids:
            written_xy.append((pid, x, y))
        else:
            pending_xy.append((pid, x, y))

    for pid, x, y in written_xy:
        bbox = crop_by_id.get(pid)
        if bbox and _valid_crop_bbox(bbox[:4]):
            xmin, ymin, xmax, ymax = bbox[:4]
            # Green fill, opacity scaled by interior point density
            # (0.12 sparse -> 0.62 densest). This standalone panel can be
            # bolder than the faint density overlay on the main map.
            if max_density > 0 and len(bbox) >= 5 and bbox[4]:
                area = max((xmax - xmin) * (ymax - ymin), 1.0)
                a = 0.12 + 0.50 * (float(bbox[4]) / area / max_density)
            else:
                a = 0.25
            ax.add_patch(_mpatches.Rectangle(
                (xmin, ymin), xmax - xmin, ymax - ymin,
                facecolor="#1a8e3a", edgecolor="#0d5c1a",
                linewidth=0.3, alpha=a, zorder=2))
        elif bbox:
            # Half-written placeholder bbox — treat as pending so we draw a
            # small symbolic square at the pole instead of a giant fill.
            ax.add_patch(_mpatches.Rectangle(
                (x - half_size, y - half_size),
                2 * half_size, 2 * half_size,
                facecolor="#ff1f1c", edgecolor="#b00000",
                linewidth=0.3, alpha=0.22, zorder=2))
    for pid, x, y in pending_xy:
        ax.add_patch(_mpatches.Rectangle(
            (x - half_size, y - half_size),
            2 * half_size, 2 * half_size,
            facecolor="#ff1f1c", edgecolor="#b00000",
            linewidth=0.3, alpha=0.22, zorder=2))

    # Pole top dots on top of squares.
    if written_xy:
        wx, wy = zip(*[(x, y) for _, x, y in written_xy])
        ax.scatter(wx, wy, s=18, c="#1a8e3a", edgecolors="white",
                   linewidths=0.4, zorder=5,
                   label=f"pole crop — green shaded by point density "
                         f"({len(written_xy)})")
    if pending_xy:
        px, py = zip(*[(x, y) for _, x, y in pending_xy])
        ax.scatter(px, py, s=12, c="#ff1f1c", edgecolors="white",
                   linewidths=0.4, zorder=4,
                   label=f"no crop written ({len(pending_xy)})")

    # LAZ extent rectangle (same dashed blue as progress.png).
    try:
        _sp = _read_laz_bbox_facts(run_dir)
        bb = _sp.get("laz_bbox_in_pole_crs") or _sp.get("laz_bbox") or {}
        if bb:
            ax.add_patch(_mpatches.Rectangle(
                (bb["xmin"], bb["ymin"]),
                bb["xmax"] - bb["xmin"], bb["ymax"] - bb["ymin"],
                facecolor="none", edgecolor="#1c6dd0",
                linewidth=1.5, linestyle="--", alpha=0.85, zorder=1))
    except Exception:
        pass

    # Padded extents (use pole positions, since we want to focus on the
    # corridor, not the wider LAZ extent if it's bigger).
    pad = max(half_size * 2, 0.02 * max(dx, dy, 1.0))
    ax.set_xlim(xs.min() - pad, xs.max() + pad)
    ax.set_ylim(ys.min() - pad, ys.max() + pad)
    ax.set_aspect("equal")
    try:
        crs = gdf.crs.to_string() if gdf.crs is not None else ""
    except Exception:
        crs = ""
    ax.set_xlabel(f"X ({crs})" if crs else "X")
    ax.set_ylabel(f"Y ({crs})" if crs else "Y")
    ax.set_title(f"Stage 2 — per-pole crops, shaded by point density: "
                 f"{n_written}/{n_total}",
                 fontsize=11, loc="left")
    ax.grid(True, linestyle=":", linewidth=0.4, alpha=0.5)
    ax.legend(loc="lower left", fontsize=9, framealpha=0.9)

    # Public basemap (shared cache with progress.png).
    basemap_mode = os.environ.get("DASHBOARD_BASEMAP", "satellite").lower()
    if basemap_mode != "none":
        try:
            import contextily as cx
            providers = {
                "satellite":   cx.providers.Esri.WorldImagery,
                "streets":     cx.providers.OpenStreetMap.Mapnik,
                "light":       cx.providers.CartoDB.Positron,
                "dark":        cx.providers.CartoDB.DarkMatter,
                "voyager":     cx.providers.CartoDB.Voyager,
            }
            provider = providers.get(basemap_mode,
                                      cx.providers.Esri.WorldImagery)
            if crs:
                # Match the extent-based zoom-cap logic from progress.png
                # (see comment there). Otherwise tiny extents (Firmatek
                # small-data) hit auto-zoom > 19 and Esri returns "Map
                # data not yet available" placeholder tiles.
                _xlim = ax.get_xlim(); _ylim = ax.get_ylim()
                ext_max = max(_xlim[1] - _xlim[0], _ylim[1] - _ylim[0])
                ext_max_m = ext_max * 0.3048 if "6424" in str(crs) else ext_max
                if ext_max_m < 200:
                    bm_zoom = 19
                elif ext_max_m < 2000:
                    bm_zoom = 17
                elif ext_max_m < 20000:
                    bm_zoom = 15
                elif ext_max_m < 200000:
                    bm_zoom = 13
                else:
                    bm_zoom = 11
                cx.add_basemap(ax, crs=crs, source=provider,
                               zoom=bm_zoom, attribution_size=6,
                               zorder=0)
        except Exception as e:
            print(f"[basemap pole_crops] skipped: {e}")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"[{time.strftime('%H:%M:%S')}] wrote {out_png}  "
          f"(crops {n_written}/{n_total})")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--out", type=Path, default=None,
                   help="Output PNG path (default <run_dir>/viz/progress.png)")
    p.add_argument("--once", action="store_true", help="Render once and exit")
    p.add_argument("--watch", type=int, default=60,
                   help="Loop every N seconds (default 60). Ignored with --once.")
    p.add_argument("--max-idle-renders", type=int, default=2,
                   help="After viz/status.json reports the run is done/failed, "
                        "do this many additional render passes (to capture "
                        "any final tile-state updates), then exit. Default 2.")
    p.add_argument("--max-iters", type=int, default=1440,
                   help="Hard safety cap on the watch loop in case status.json "
                        "never reaches a terminal state (e.g. the orchestrator "
                        "was kill-9'd). Default 1440 iterations (= 24 h at "
                        "the default 60 s interval).")
    args = p.parse_args()
    out_png = args.out or args.run_dir / "viz" / "progress.png"
    crops_png = args.run_dir / "viz" / "pole_crops.png"

    if args.once:
        # Crops panel FIRST so render() can embed it in progress.html.
        try:
            render_pole_crops_panel(args.run_dir, crops_png)
        except Exception as e:
            print(f"[crops] error: {e}")
        render(args.run_dir, out_png)
        return

    # Issue #74: auto-exit when the chain completes. Polling status.json
    # is the simplest cross-process signal — the orchestrator writes the
    # terminal state ("done" / "failed") into viz/status.json at the end
    # of its main loop. We do `max_idle_renders` additional passes after
    # the first terminal-state read so the final tile-state shows up in
    # the PNG even if the orchestrator wrote status.json a few seconds
    # before flushing the last manifest.
    status_p = args.run_dir / "viz" / "status.json"
    idle_after_done = 0
    iters = 0
    while True:
        iters += 1
        if iters > args.max_iters:
            print(f"[{time.strftime('%H:%M:%S')}] watcher hit --max-iters="
                  f"{args.max_iters}, exiting to avoid runaway. status.json "
                  f"never reached a terminal state — orchestrator may have "
                  f"died without flushing.")
            break
        # Crops panel FIRST so the progress.html that render() writes embeds
        # it on the SAME tick (otherwise the panel lags one tick and, on a
        # fast run that finishes before the next tick, never shows at all).
        try:
            render_pole_crops_panel(args.run_dir, crops_png)
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] crops render error: {e}")
        try:
            render(args.run_dir, out_png)
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] render error: {e}")

        # Check terminal state.
        overall_state = "unknown"
        try:
            if status_p.exists():
                _s = json.loads(status_p.read_text(encoding="utf-8"))
                overall_state = _s.get("overall_state", "unknown")
        except Exception:
            pass
        # Terminal states: "done" (all stages OK), "failed" (any stage
        # raised), "partial" (per-source mode with some sources failed).
        if overall_state in ("done", "failed", "partial"):
            idle_after_done += 1
            if idle_after_done >= args.max_idle_renders:
                print(f"[{time.strftime('%H:%M:%S')}] chain reached "
                      f"'{overall_state}' state and {idle_after_done} extra "
                      f"render(s) captured. Watcher exiting cleanly.")
                break
        # Run dir disappeared (e.g. user deleted the run): exit too.
        if not args.run_dir.is_dir():
            print(f"[{time.strftime('%H:%M:%S')}] run dir vanished "
                  f"({args.run_dir}), watcher exiting.")
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
