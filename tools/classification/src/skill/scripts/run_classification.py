"""Drive the FULL classification chain over Stage-1 output and emit one final
cloud per source carrying every class this chain can produce.

Stage 1 (PointCONV, GPU) is NOT run here — it is a long GPU job with its own
tiling/resume logic, so run it first with `classify.sh` (see SKILL.md) and point
`--stage1-dir` at the resulting `class_out`. Everything after Stage 1 is CPU and
runs here, in order:

    assemble run-dir   ->  01_pointconv/combined_outputs/   (hardlinks, no copy)
    stage4w            ->  04w_building_walls/Building_Walls.shp     (class 47)
    stage6             ->  06_final_classification/*_final_classified.laz
    stage6v            ->  veg split into 3 / 4 / 5
    [--mark-noise]     ->  class 7 on statistical outliers

WHAT YOU GET, AND WHAT YOU CANNOT GET
-------------------------------------
Produced by this driver:

    0  Never classified      Stage 1 (densification points)
    2  Ground                Stage 1
    3  Low Vegetation        Stage 6v   (HAG < 0.5 m)
    4  Medium Vegetation     Stage 6v   (0.5 - 2.0 m)
    5  High Vegetation       Stage 1, narrowed by Stage 6v (>= 2.0 m)
    6  Building / manmade    Stage 1    (conflated: facades + fences + vehicles)
    7  Low point / noise     --mark-noise (optional)
   14  Wire                  Stage 1
   15  Tower                 Stage 1
   18  Pole                  Stage 1
   47  Building wall         Stage 4w -> Stage 6 override on class 6

NOT obtainable from this chain, and why:

   40  Road / Pavement   Stage 6 can stamp it, but the road polygon comes from
                         Stage 5, which needs Stage 4 curb-skill ground+HAG
                         artifacts, which need a PRETRAINED CURB MODEL. Pass
                         --road-surface <shp> if you have a road polygon from
                         anywhere else and Stage 6 will apply it.
   19  Pole body         Needs Stage 3 pole-vec in FULL mode. Documented
                         KNOWN-INERT (2026-06-10) for corridor deliveries: the
                         body-only layout writes no pole_body_pts.las at all.
   51  Sidewalk          No available model emits it. Both models in
                         models.json are 6-class {2,5,6,14,15,18}.
    9  Water             Nothing in the chain produces it.

So "all classes" for a corridor/AOI delivery means the 10-11 codes above, not
the whole ASPRS table. The driver prints the final histogram so the coverage is
explicit rather than assumed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
# The chain workers live in the chain-orchestrator checkout, not in this skill.
DEFAULT_CHAIN_SCRIPTS = Path(
    "C:/Users/sdaiprod/source/agentic-workflows/Greg_Sandbox/"
    "agentic_development/Claude/projects/chain-orchestrator/scripts")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str], step: str, allow_codes: tuple[int, ...] = (0,)) -> int:
    log(f"--- {step}")
    log("    " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    rc = subprocess.call([str(c) for c in cmd])
    if rc in allow_codes:
        log(f"    {step}: rc={rc} OK")
    else:
        log(f"    {step}: rc={rc} -- NOT in allowed {allow_codes}")
    return rc


def link_or_copy(src: Path, dst: Path) -> str:
    """Hardlink Stage-1 output into the run-dir; copy only if that fails.

    These are multi-hundred-MB per tile, so a hardlink keeps the assembled
    run-dir effectively free. Falls back to copy across volumes.
    """
    if dst.exists():
        return "exists"
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stage1-dir", required=True, type=Path,
                   help="Stage-1 output root: the `class_out` directory holding "
                        "<tile>/<tile>_t_raw.las per tile.")
    p.add_argument("--run-dir", required=True, type=Path,
                   help="Chain run dir to assemble and write into.")
    p.add_argument("--epsg", type=int, default=26917,
                   help="Working CRS. REQUIRED in practice: Stage 1 drops the "
                        "input CRS, so the wall extractor cannot infer one. "
                        "Default 26917 (UTM 17N, Ontario).")
    p.add_argument("--stage1-pattern", default="*_t_raw.la[sz]",
                   help="Glob for Stage-1 clouds under <stage1-dir>/<tile>/.")
    p.add_argument("--chain-scripts", type=Path, default=DEFAULT_CHAIN_SCRIPTS,
                   help="chain-orchestrator scripts/ dir (Stage 4w + Stage 6 "
                        "workers live there).")
    p.add_argument("--road-surface", type=Path, default=None,
                   help="Optional road polygon .shp. Staged into "
                        "05_road_surface/ so Stage 6 stamps class 40. Without "
                        "it there is no road class (see module docstring).")
    p.add_argument("--low-max", type=float, default=0.5)
    p.add_argument("--med-max", type=float, default=2.0)
    p.add_argument("--dem-cell", type=float, default=1.0)
    p.add_argument("--units", choices=("auto", "m", "ft"), default="m",
                   help="Default 'm' (not 'auto') because Stage 1 output "
                        "carries no CRS to auto-detect from.")
    p.add_argument("--mark-noise", action="store_true",
                   help="Post-pass: label statistical outliers class 7.")
    p.add_argument("--noise-k", type=int, default=16)
    p.add_argument("--noise-std-ratio", type=float, default=6.0)
    p.add_argument("--wall-min-component-height", type=float, default=4.0,
                   help="Stage 4w: drop wall components whose median cell "
                        "z-extent is under this (m). Default 4.0 -- the probe's "
                        "recommendation for PointCONV's CONFLATED class 6, "
                        "which includes vehicles/fences at ~3 m. Use 0 on "
                        "clean truth labels.")
    p.add_argument("--skip-walls", action="store_true")
    p.add_argument("--skip-stage6", action="store_true",
                   help="Go straight from Stage 1 to the veg split (no wall/"
                        "road override, no original_class).")
    p.add_argument("--skip-veg", action="store_true")
    p.add_argument("--python", default=sys.executable,
                   help="Interpreter for the workers (needs laspy/numpy/scipy/"
                        "geopandas). Defaults to the current one.")
    args = p.parse_args()

    py = args.python
    scripts = args.chain_scripts
    run_dir = args.run_dir
    if not args.stage1_dir.is_dir():
        raise SystemExit(f"--stage1-dir not found: {args.stage1_dir}")
    for w in ("extract_building_walls.py", "final_classified_pointcloud.py"):
        if not (scripts / w).is_file():
            raise SystemExit(f"chain worker missing: {scripts / w}\n"
                             f"pass --chain-scripts <chain-orchestrator/scripts>")

    t0 = time.time()
    summary: dict = {"run_dir": str(run_dir), "epsg": args.epsg, "steps": {}}

    # ---- assemble the run-dir Stage 6 expects ------------------------------
    combined = run_dir / "01_pointconv" / "combined_outputs"
    combined.mkdir(parents=True, exist_ok=True)
    sources = sorted(args.stage1_dir.glob(f"*/{args.stage1_pattern}"))
    if not sources:
        sources = sorted(args.stage1_dir.glob(args.stage1_pattern))
    if not sources:
        raise SystemExit(
            f"no Stage-1 clouds matching */{args.stage1_pattern} under "
            f"{args.stage1_dir}. Has Stage 1 finished?")
    log(f"assembling run-dir from {len(sources)} Stage-1 cloud(s)")
    modes: dict[str, int] = {}
    for s in sources:
        how = link_or_copy(s, combined / s.name)
        modes[how] = modes.get(how, 0) + 1
    log(f"  01_pointconv/combined_outputs/: {modes}")
    summary["steps"]["assemble"] = {"n_sources": len(sources), "modes": modes}

    # ---- Stage 4w: building walls -> class 47 ------------------------------
    walls_shp = run_dir / "04w_building_walls" / "Building_Walls.shp"
    if args.skip_walls:
        log("--- stage4w SKIPPED (--skip-walls): no class 47")
    else:
        walls_shp.parent.mkdir(parents=True, exist_ok=True)
        rc = run([py, scripts / "extract_building_walls.py",
                  "--input", combined,
                  "--pattern", args.stage1_pattern,
                  "--output", walls_shp,
                  "--class-code", 6,
                  "--epsg", args.epsg,
                  # class 6 is conflated on PointCONV output; the probe's
                  # recommendation for conflated input is a median-height floor
                  # so vehicles/fences (~3 m) don't become walls.
                  "--min-component-height",
                  args.wall_min_component_height],
                 "stage4w building walls", allow_codes=(0, 3))
        got = walls_shp.is_file()
        summary["steps"]["stage4w"] = {"rc": rc, "shp_written": got}
        if not got:
            log("    no Building_Walls.shp produced -> no class 47 "
                "(legitimate when the AOI has no sensed facades)")

    # ---- optional road polygon -> class 40 --------------------------------
    if args.road_surface:
        dst_dir = run_dir / "05_road_surface"
        dst_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for side in args.road_surface.parent.glob(
                args.road_surface.stem + ".*"):
            shutil.copy2(side, dst_dir / f"road_surface{side.suffix}")
            n += 1
        log(f"staged road polygon ({n} sidecar files) -> {dst_dir} "
            f"(Stage 6 will stamp class 40)")
        summary["steps"]["road_surface"] = {"staged_files": n}

    # ---- Stage 6: merge overrides -----------------------------------------
    final_dir = run_dir / "06_final_classification"
    if args.skip_stage6:
        log("--- stage6 SKIPPED (--skip-stage6); veg split will run on the "
            "Stage-1 clouds directly")
        veg_target = ["--input", str(combined),
                      "--input-pattern", args.stage1_pattern]
    else:
        rc = run([py, scripts / "final_classified_pointcloud.py",
                  "--run-dir", run_dir,
                  "--pattern", args.stage1_pattern,
                  "--chunk-size", 5_000_000],
                 "stage6 final classification")
        outs = sorted(final_dir.glob("*_final_classified.la[sz]")) \
            if final_dir.is_dir() else []
        summary["steps"]["stage6"] = {"rc": rc, "n_outputs": len(outs)}
        if not outs:
            log("    stage6 produced nothing -- falling back to the Stage-1 "
                "clouds for the veg split")
            veg_target = ["--input", str(combined),
                          "--input-pattern", args.stage1_pattern]
        else:
            log(f"    {len(outs)} final-classified cloud(s)")
            veg_target = ["--run-dir", str(run_dir)]

    # ---- Stage 6v: veg -> 3 / 4 / 5 ---------------------------------------
    if args.skip_veg:
        log("--- stage6v SKIPPED (--skip-veg): no class 3 / 4")
    else:
        cmd = [py, SKILL_DIR / "stratify_vegetation.py"] + veg_target + [
            "--low-max", args.low_max, "--med-max", args.med_max,
            "--dem-cell", args.dem_cell, "--units", args.units]
        if "--input" in veg_target:
            # not in place: keep the Stage-1 clouds pristine
            cmd += ["--out-dir", str(final_dir), "--suffix", "_final_classified"]
        rc = run(cmd, "stage6v vegetation stratification", allow_codes=(0, 3))
        summary["steps"]["stage6v"] = {"rc": rc}

    # ---- optional class 7 --------------------------------------------------
    if args.mark_noise:
        targets = sorted(final_dir.glob("*.la[sz]")) if final_dir.is_dir() else []
        if not targets:
            log("--- mark-noise skipped: no final clouds")
        else:
            rc = run([py, SKILL_DIR / "mark_noise.py",
                      "--input", final_dir,
                      "--k", args.noise_k,
                      "--std-ratio", args.noise_std_ratio],
                     "class-7 noise marking", allow_codes=(0, 3))
            summary["steps"]["mark_noise"] = {"rc": rc}

    # ---- report -----------------------------------------------------------
    finals = sorted(final_dir.glob("*.la[sz]")) if final_dir.is_dir() else []
    log("")
    log(f"=== DONE in {(time.time()-t0)/60:.1f} min -- "
        f"{len(finals)} final cloud(s) in {final_dir}")
    summary["n_final_clouds"] = len(finals)
    summary["final_clouds"] = [f.name for f in finals]
    sj = run_dir / "classification_chain_summary.json"
    try:
        sj.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        log(f"summary -> {sj}")
    except OSError as exc:
        log(f"could not write {sj}: {exc}")

    if finals:
        log("")
        log("Final class coverage (aggregate over all outputs):")
        rc = subprocess.call([str(py), str(SKILL_DIR / "class_totals.py"),
                              str(final_dir)])
        if rc != 0:
            log("  (class_totals.py failed; inspect manually with "
                "inspect_cloud.py)")
    return 0 if finals else 1


if __name__ == "__main__":
    sys.exit(main())
