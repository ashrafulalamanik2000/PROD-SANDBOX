"""
AECON Processing Pipeline — efficient orchestrator.

Usage:
    python aecon.py <data_root> [--stages yaml,organize,metadata,pano,colorize]
                    [--crs EPSG:26917] [--search-radius 45] [--buffer 45]
                    [--project-workers 2] [--las-workers 2] [--threads 8]
                    [--engine <path-to-solv3d-engine.exe>]

Stages (run in order, all are idempotent — re-running skips completed work):
    yaml       — Parse .lst files → InputConfig.yml (multi-run aware)
    organize   — Copy/rename images + LAS → Organized_Projects/Raw Project Data/
    metadata   — Generate Run_N_metadata.csv (Camera 3 EOPs with Frot)
    pano       — Call Solv3D engine → Organized_Projects/Pano_output/
    colorize   — Colorize each LAS from panos + clip to camera buffer

Efficiency improvements over original scripts:
    • Single entry point (replaces run_portable.bat + 5 scripts)
    • Project-level parallelism for yaml/organize/metadata/colorize stages
    • LAS-file parallelism within colorize (ProcessPoolExecutor over files)
    • Idempotent: each stage skips projects/files that already have output
    • Per-project error isolation (one failure doesn't kill others)
    • Configurable paths (no hardcoded D:\\AECON_PORTABLE)
    • Multi-run support: parses ALL .lst files, not just the first
"""
import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

# Prepend portable conda env bins to PATH so pdal.exe can be found
# (must happen BEFORE any pdal import)
_env_root = os.path.dirname(sys.executable)
_extra_paths = [_env_root,
                os.path.join(_env_root, "Library", "bin"),
                os.path.join(_env_root, "Scripts")]
os.environ["PATH"] = os.pathsep.join(_extra_paths + [os.environ.get("PATH", "")])

# Force UTF-8 stdout on Windows (cp1252 default can't encode non-ASCII)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Make stages importable when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stages import (stage_yaml, stage_organize, stage_metadata,
                    stage_panorama, stage_colorize)
from preflight import parse_stages, validate


DEFAULT_ENGINE = os.environ.get("AECON_ENGINE") or os.path.join(
    os.getenv('LOCALAPPDATA', ''),
    r"Programs\solv3d-engine\resources\exe-engine\engine.exe",
)


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _run_parallel(fn, projects, workers, *args):
    """Run fn(project, *args) in parallel across projects."""
    results = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fn, p, *args): p for p in projects}
        for fut in as_completed(futures):
            try:
                results.append((True, fut.result()))
            except Exception as e:
                results.append((False, f"ERROR {os.path.basename(futures[fut])}: {e}"))
    return results


def main():
    ap = argparse.ArgumentParser(description="AECON pipeline orchestrator",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("data_root", help="Folder containing project subfolders")
    ap.add_argument("--stages", default="yaml,organize,metadata,pano,colorize",
                    help="Comma-separated stages to run")
    ap.add_argument("--crs", default="EPSG:26917", help="CRS for colorize stage")
    ap.add_argument("--search-radius", type=positive_int, default=45, help="Colorize search radius (m)")
    ap.add_argument("--buffer", type=positive_int, default=45, help="Clip buffer distance (m)")
    ap.add_argument("--project-workers", type=positive_int, default=2,
                    help="Parallel projects (for yaml/organize/metadata/colorize)")
    ap.add_argument("--las-workers", type=positive_int, default=2,
                    help="Parallel LAS files per project in colorize stage")
    ap.add_argument("--threads", type=positive_int, default=8,
                    help="Threads per LAS file (pano processing)")
    ap.add_argument("--engine", default=DEFAULT_ENGINE,
                    help="Path to Solv3D engine.exe")
    ap.add_argument("--keep-colorized", action="store_true",
                    help="Keep the full colorized LAS in Lidar/Colorized after "
                         "clipping (default: delete it to save disk)")
    args = ap.parse_args()

    if not os.path.isdir(args.data_root):
        sys.exit(f"ERROR: data_root not found: {args.data_root}")

    try:
        stages = list(parse_stages(args.stages))
    except ValueError as exc:
        ap.error(str(exc))
    report = validate(args.data_root, args.stages)
    if not report["ok"]:
        for check in report["checks"]:
            print(f"ERROR {check['code']}: {check['message']} [{check['path']}]", file=sys.stderr)
        return 2
    projects = report["projects"]
    pw = args.project_workers

    print(f"Found {len(projects)} projects:")
    for p in projects:
        print(f"  - {os.path.basename(p)}")
    print(f"\nStages: {stages}\n")

    total_start = time.time()
    failures = []

    if "yaml" in stages:
        print("━━━ STAGE 1: YAML ━━━")
        t = time.time()
        for ok, message in _run_parallel(stage_yaml, projects, pw):
            print(f"  {message}")
            if not ok:
                failures.append(message)
        print(f"  done in {time.time()-t:.1f}s\n")

    if "organize" in stages:
        print("━━━ STAGE 2: ORGANIZE ━━━")
        t = time.time()
        for ok, message in _run_parallel(stage_organize, projects, pw):
            print(f"  {message}")
            if not ok:
                failures.append(message)
        print(f"  done in {time.time()-t:.1f}s\n")

    if "metadata" in stages:
        print("━━━ STAGE 3: METADATA CSV ━━━")
        t = time.time()
        for ok, message in _run_parallel(stage_metadata, projects, pw):
            print(f"  {message}")
            if not ok:
                failures.append(message)
        print(f"  done in {time.time()-t:.1f}s\n")

    if "pano" in stages:
        print("━━━ STAGE 4: PANORAMA (Solv3D) ━━━")
        # Engine can be a single path (engine.exe) or a command string
        # (e.g. "python3 /app/pano_generator.py"). Only check existence
        # when it's a single path with no spaces.
        engine_first = args.engine.split()[0] if args.engine else ""
        engine_ok = (" " in args.engine) or (engine_first and os.path.exists(args.engine))
        if not engine_ok:
            message = f"engine not found: {args.engine}"
            print(f"  ERROR: {message}")
            failures.append(message)
        else:
            t = time.time()
            # Pano must run sequentially (external exe) — no project parallelism
            for p in projects:
                try:
                    print(f"  {stage_panorama(p, args.engine)}")
                except Exception as exc:
                    message = f"ERROR {os.path.basename(p)}: {exc}"
                    print(f"  {message}")
                    failures.append(message)
            print(f"  done in {time.time()-t:.1f}s\n")

    if "colorize" in stages:
        print("━━━ STAGE 5: COLORIZE + CLIP ━━━")
        t = time.time()
        # Fewer project workers here because each uses las-workers × threads
        colorize_workers = max(1, pw)
        for ok, message in _run_parallel(stage_colorize, projects, colorize_workers,
                                         args.crs, args.search_radius, args.buffer,
                                         args.threads, args.las_workers,
                                         args.keep_colorized):
            print(f"  {message}")
            if not ok:
                failures.append(message)
        print(f"  done in {time.time()-t:.1f}s\n")

    if failures:
        print(f"FAILED: {len(failures)} stage/project operation(s) failed", file=sys.stderr)
        return 1
    print(f"✅ ALL STAGES COMPLETED in {time.time()-total_start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
