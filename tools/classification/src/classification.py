r"""Classification — the Topology-Aerial classification spine on OUR canonical layout.

Wraps the vendored `topology-aerial-classification` skill (src/skill/) so a
classified cloud is produced from a mobile-data-preprocessing project without
touching the skill's sdaiprod-era paths. Folder mapping:

    <project>\Lidar\Clipped\*_clipped.las      INPUT  (colorized + clipped)
    <project>\Lidar\Classified\                OUTPUT (final classified LAZ + summaries)
    <project>\Lidar\Classified\_work\          TEMP   (everything intermediate;
                                               `--stages clean` deletes it)
        class_out\<tile>\<tile>_t_raw.las      stage1 GPU output
        chain_run\...                          CPU-chain run dir (hardlinks + stage dirs)

Stages
    inspect   print point count / CRS / class histogram per input (start here)
    stage1    PointCONV 6-class GPU inference — needs Docker + the classify_las
              tree (--classify-src) + models (--models-dir). Runs on a GPU box,
              NOT on a Blackwell laptop (cuDNN corruption — see memory).
    refine    the CPU chain: walls -> stage6 overrides -> veg 3/4/5 split ->
              optional class-7 noise, then PUBLISH final clouds up to Lidar\Classified.
              Without --chain-scripts the external stages are auto-skipped and the
              veg split runs directly on stage1 output (self-contained).
    verify    aggregate class histogram over the published outputs
    clean     delete Lidar\Classified\_work (all temp/intermediate files)

`all` = inspect,stage1,refine,verify. `clean` never runs implicitly.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from glob import glob
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_SCRIPTS = HERE / "skill" / "scripts"

STAGES = ["inspect", "stage1", "refine", "verify", "clean"]
DEFAULT_STAGES = "inspect,stage1,refine,verify"


def positive_int(v):
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def log(msg: str) -> None:
    print(f"[classification] {msg}", flush=True)


def find_bash() -> str:
    """A bash that can actually run classify.sh. On Windows a bare "bash"
    resolves to System32's WSL relay, which dies with execvpe(/bin/bash)
    when no distro is installed — prefer Git Bash next to git.exe."""
    if os.environ.get("SDAI_BASH"):
        return os.environ["SDAI_BASH"]
    git = shutil.which("git")
    if git:
        cand = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if cand.is_file():
            return str(cand)
    for cand in (r"C:\Program Files\Git\bin\bash.exe",
                 r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(cand).is_file():
            return cand
    return "bash"


def run_cmd(cmd: list, plan: bool, check: bool = True,
            env: dict | None = None) -> int:
    printable = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)
    log(("PLAN $ " if plan else "$ ") + printable)
    if plan:
        return 0
    rc = subprocess.call([str(c) for c in cmd], env=env)
    if check and rc != 0:
        sys.exit(f"ERROR: step failed with rc={rc}")
    return rc


def classify_env() -> dict:
    """classify.sh tiles oversize clouds with the pdal CLI and refuses to
    start without it ("Set $PDAL_EXE"). This tool's own pixi env (topo_chain)
    ships pdal/laszip — point the script at them unless already overridden."""
    env = os.environ.copy()
    libbin = Path(sys.executable).resolve().parent / "Library" / "bin"
    for var, exe in (("PDAL_EXE", "pdal.exe"), ("LASZIP_EXE", "laszip.exe")):
        if not env.get(var) and (libbin / exe).is_file():
            env[var] = str(libbin / exe)
    return env


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Topology-Aerial classification on the canonical project layout",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("data_root", help="project root (canonical Lidar\\... layout)")
    ap.add_argument("--stages", default="all",
                    help=f"comma-separated: {','.join(STAGES)} or 'all' "
                         f"(= {DEFAULT_STAGES}; clean only runs when asked)")
    ap.add_argument("--epsg", type=positive_int, required=True,
                    help="working CRS — stage1 does NOT carry CRS into its output, "
                         "so this is load-bearing for the veg split")
    ap.add_argument("--input-dir", default="",
                    help="override input dir (default <root>\\Lidar\\Clipped)")
    ap.add_argument("--out-dir", default="",
                    help="override output dir (default <root>\\Lidar\\Classified)")
    # stage1 (GPU) knobs
    ap.add_argument("--classify-src", default=os.environ.get("SDAI_CLASSIFY_SRC", ""),
                    help="classify_las Classification source tree (stage1; "
                         "env SDAI_CLASSIFY_SRC)")
    ap.add_argument("--models-dir", default=os.environ.get("SDAI_MODELS_DIR", ""),
                    help="PointCONV models dir, e.g. D:\\LL\\models (stage1; "
                         "env SDAI_MODELS_DIR)")
    ap.add_argument("--max-points", type=positive_int, default=40_000_000,
                    help="stage1 auto-tile threshold (100M default OOMs on dense data)")
    ap.add_argument("--tile-size", type=positive_int, default=500,
                    help="stage1 tile size (m) when auto-tiling")
    # refine (CPU) knobs
    ap.add_argument("--chain-scripts", default=os.environ.get("SDAI_CHAIN_SCRIPTS", ""),
                    help="chain-orchestrator scripts dir for walls/stage6 "
                         "(env SDAI_CHAIN_SCRIPTS); empty = skip those, veg split "
                         "runs straight on stage1 output")
    ap.add_argument("--road-surface", default="",
                    help="optional road polygon shp -> class 40 in stage6")
    ap.add_argument("--mark-noise", action="store_true",
                    help="label statistical outliers class 7 (kept, not removed)")
    ap.add_argument("--low-max", type=float, default=0.5,
                    help="veg: HAG below this (m) -> class 3")
    ap.add_argument("--med-max", type=float, default=2.0,
                    help="veg: HAG below this (m) -> class 4, above -> 5")
    ap.add_argument("--plan", action="store_true",
                    help="print every command without running")
    args = ap.parse_args()

    root = Path(args.data_root)
    if not root.is_dir():
        sys.exit(f"ERROR: data_root not found: {root}")
    input_dir = Path(args.input_dir) if args.input_dir else root / "Lidar" / "Clipped"
    out_dir = Path(args.out_dir) if args.out_dir else root / "Lidar" / "Classified"
    work = out_dir / "_work"
    class_out = work / "class_out"
    chain_run = work / "chain_run"

    wanted = DEFAULT_STAGES.split(",") if args.stages == "all" else [
        s.strip() for s in args.stages.split(",") if s.strip()]
    bad = [s for s in wanted if s not in STAGES]
    if bad:
        sys.exit(f"ERROR: unknown stage(s) {bad}; valid: {STAGES} or 'all'")

    py = sys.executable

    if "inspect" in wanted:
        clouds = sorted(glob(str(input_dir / "*.la[sz]")))
        if not clouds and not args.plan:
            sys.exit(f"ERROR: no LAS/LAZ in {input_dir}")
        log(f"inspect: {len(clouds)} input cloud(s) in {input_dir}")
        for c in clouds:
            run_cmd([py, SKILL_SCRIPTS / "inspect_cloud.py", c], args.plan)

    if "stage1" in wanted:
        if not (args.classify_src and args.models_dir) and not args.plan:
            sys.exit(
                "ERROR: stage1 needs --classify-src and --models-dir (or env "
                "SDAI_CLASSIFY_SRC / SDAI_MODELS_DIR) plus Docker + an NVIDIA "
                "GPU with a pre-Blackwell card. Run this stage on the GPU box; "
                "then run the remaining stages here with --stages refine,verify.")
        if args.classify_src:
            classify_sh = Path(args.classify_src).parent / \
                "skills" / "classify_las" / "scripts" / "classify.sh"
        else:  # plan-mode placeholder
            classify_sh = Path("<classify-src>") / ".." / "skills" / \
                "classify_las" / "scripts" / "classify.sh"
        if not args.plan:
            work.mkdir(parents=True, exist_ok=True)
        run_cmd([find_bash(), classify_sh,
                 "--input", input_dir, "--output", work,
                 "--models", args.models_dir or "<models-dir>",
                 "--src", args.classify_src or "<classify-src>",
                 "--max-points", args.max_points,
                 "--tile-size", args.tile_size], args.plan, env=classify_env())

    if "refine" in wanted:
        if not class_out.is_dir() and not args.plan:
            sys.exit(f"ERROR: no stage1 output at {class_out} — run stage1 first "
                     f"(on the GPU box), or point --out-dir at where it ran.")
        cmd = [py, SKILL_SCRIPTS / "run_classification.py",
               "--stage1-dir", class_out, "--run-dir", chain_run,
               "--epsg", args.epsg, "--units", "m",
               "--low-max", args.low_max, "--med-max", args.med_max]
        if args.chain_scripts:
            cmd += ["--chain-scripts", args.chain_scripts]
        else:
            cmd += ["--skip-walls", "--skip-stage6"]
            log("no --chain-scripts: walls/stage6 skipped, veg split runs on "
                "stage1 output directly")
        if args.road_surface:
            cmd += ["--road-surface", args.road_surface]
        if args.mark_noise:
            cmd += ["--mark-noise"]
        run_cmd(cmd, args.plan)

        # publish: final clouds + summaries up to Lidar\Classified
        final_dir = chain_run / "06_final_classification"
        if args.plan:
            log(f"PLAN publish: move {final_dir}\\*_final_classified.la[sz] "
                f"+ summaries -> {out_dir}")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            published = 0
            for f in sorted(final_dir.glob("*.la[sz]")):
                shutil.move(str(f), str(out_dir / f.name))
                published += 1
            for j in list(final_dir.glob("*.json")) + list(chain_run.glob("*.json")):
                shutil.copy2(j, out_dir / j.name)
            if not published:
                sys.exit(f"ERROR: refine produced no final clouds in {final_dir}")
            log(f"published {published} classified cloud(s) -> {out_dir}")

    if "verify" in wanted:
        run_cmd([py, SKILL_SCRIPTS / "class_totals.py", out_dir], args.plan)

    if "clean" in wanted:
        if args.plan:
            log(f"PLAN clean: delete {work}")
        elif work.is_dir():
            size = sum(f.stat().st_size for f in work.rglob("*") if f.is_file())
            shutil.rmtree(work)
            log(f"clean: deleted {work} ({size / 1e9:.1f} GB of temp files)")
        else:
            log(f"clean: nothing to delete ({work} absent)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
