r"""Pole-vec — pole network + catenary vectorization on OUR canonical layout.

Wraps the vendored `pole-network-catenary` skill (src/skill/) — the pole/wire
half of the Topology-Aerial chain — consuming the classification tool's output.
Folder mapping:

    <project>\Lidar\Classified\*.la[sz]        INPUT  (classes 14 wire + 18 pole)
    <project>\Vectors\Poles\                   OUTPUT (published deliverables)
        Network\   pole-network nodes/edges shapefiles + QC (stage: network)
        PoleVec\   body lines, linear/parabola wires, catenary spans, DXF (stage: polevec)
    <project>\Vectors\Poles\_work\             TEMP   (`--stages clean` deletes it)
        poles\           discovery shapefile + processed seed CSV
        02_pole_crop\    per-pole LAS crops (the big one)
        network_in\      crops hardlinked to the chain naming convention
        utility_topology\  network stage raw output
        polevec_run\     staged pole-vec run dir (incl. PoleVec\Temp)

Stages
    inspect    class histogram of the input — need 14 + 18 present
    discover   DBSCAN class-18 clustering -> one seed point per pole (external script)
    crop       Stage 2: detect + crop per pole (external pipeline.py, CPU)
    network    Stage 3.5: wire-stub span graph -> nodes/edges (external, CPU)
    polevec    Stage 3 FULL: body/wires/catenaries/DXF (GPU Docker + the
               sdai_chain_polevec volume) — runs on the GPU box
    clean      delete Vectors\Poles\_work (crops + all temp files)

`all` = inspect,discover,crop,network,polevec. `clean` never runs implicitly.
External chain scripts resolve under --chain-src (env SDAI_CHAIN_SRC), the
agentic-workflows checkout root.
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

STAGES = ["inspect", "discover", "crop", "network", "polevec", "clean"]
DEFAULT_STAGES = "inspect,discover,crop,network,polevec"
POLEVEC_IMAGE = "750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1"

# sub-paths under the agentic-workflows checkout (--chain-src)
GREG = Path("Greg_Sandbox") / "agentic_development" / "Claude" / "projects"
DISCOVERY = GREG / "pole-vectorization" / "scripts" / "reextract_poles_loose_bridge_multi.py"
CROPPER = GREG / "pole-cropping" / "croping_around_poles" / "pipeline.py"
NETWORK = GREG / "chain-orchestrator" / "scripts" / "estimate_pole_network.py"


def positive_int(v):
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def log(msg: str) -> None:
    print(f"[pole-vec] {msg}", flush=True)


def run_cmd(cmd: list, plan: bool, check: bool = True) -> int:
    printable = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)
    log(("PLAN $ " if plan else "$ ") + printable)
    if plan:
        return 0
    rc = subprocess.call([str(c) for c in cmd])
    if check and rc != 0:
        sys.exit(f"ERROR: step failed with rc={rc}")
    return rc


def chain_script(chain_src: str, rel: Path, plan: bool) -> Path:
    if not chain_src:
        if plan:
            return Path("<chain-src>") / rel
        sys.exit(f"ERROR: this stage needs --chain-src (or env SDAI_CHAIN_SRC) — "
                 f"the agentic-workflows checkout carrying {rel}")
    p = Path(chain_src) / rel
    if not p.is_file() and not plan:
        sys.exit(f"ERROR: chain script not found: {p}")
    return p


def publish(src_dir: Path, dst_dir: Path, patterns: list[str], plan: bool) -> int:
    if plan:
        log(f"PLAN publish: copy {patterns} from {src_dir} -> {dst_dir}")
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for pat in patterns:
        for f in sorted(src_dir.glob(pat)):
            if f.is_file():
                shutil.copy2(f, dst_dir / f.name)
                n += 1
    log(f"published {n} file(s) -> {dst_dir}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Pole network + catenary vectorization on the canonical layout",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("data_root", help="project root (canonical layout)")
    ap.add_argument("--stages", default="all",
                    help=f"comma-separated: {','.join(STAGES)} or 'all' "
                         f"(= {DEFAULT_STAGES}; clean only runs when asked)")
    ap.add_argument("--epsg", type=positive_int, required=True,
                    help="metric CRS of the classified cloud (everything is hard metric)")
    ap.add_argument("--input-dir", default="",
                    help="override input dir (default <root>\\Lidar\\Classified)")
    ap.add_argument("--out-dir", default="",
                    help="override output dir (default <root>\\Vectors\\Poles)")
    ap.add_argument("--chain-src", default=os.environ.get("SDAI_CHAIN_SRC", ""),
                    help="agentic-workflows checkout root (env SDAI_CHAIN_SRC) — "
                         "carries discovery/cropping/network scripts")
    ap.add_argument("--las-pattern", default="*.las",
                    help="input cloud glob for discovery (.laz needs an explicit "
                         "pattern — the script's fallback only globs .las)")
    ap.add_argument("--half-size", type=positive_int, default=50,
                    help="crop half-size (m): must reach past mid-span")
    ap.add_argument("--min-pole-height", type=positive_int, default=4)
    ap.add_argument("--max-pole-height", type=positive_int, default=30)
    ap.add_argument("--max-workers", type=positive_int, default=8,
                    help="crop stage parallelism")
    ap.add_argument("--max-span", type=positive_int, default=100,
                    help="network: max span length (m); raise for transmission")
    ap.add_argument("--las-threads", type=positive_int, default=8,
                    help="polevec: las_files_num_threads (lower to 2 on OOM/SIGKILL)")
    ap.add_argument("--plan", action="store_true",
                    help="print every command without running")
    args = ap.parse_args()

    root = Path(args.data_root)
    if not root.is_dir():
        sys.exit(f"ERROR: data_root not found: {root}")
    input_dir = Path(args.input_dir) if args.input_dir else root / "Lidar" / "Classified"
    out_dir = Path(args.out_dir) if args.out_dir else root / "Vectors" / "Poles"
    work = out_dir / "_work"
    poles_shp = work / "poles" / "poles_candidates.shp"
    crop_out = work / "02_pole_crop" / "output"
    crops = crop_out / "crops"
    network_in = work / "network_in"
    topo_out = work / "utility_topology"
    pv_run = work / "polevec_run"
    project = root.name

    wanted = DEFAULT_STAGES.split(",") if args.stages == "all" else [
        s.strip() for s in args.stages.split(",") if s.strip()]
    bad = [s for s in wanted if s not in STAGES]
    if bad:
        sys.exit(f"ERROR: unknown stage(s) {bad}; valid: {STAGES} or 'all'")

    py = sys.executable

    if "inspect" in wanted:
        clouds = sorted(glob(str(input_dir / "*.la[sz]")))
        if not clouds and not args.plan:
            sys.exit(f"ERROR: no LAS/LAZ in {input_dir} — run classification first")
        log(f"inspect: {len(clouds)} classified cloud(s) in {input_dir} "
            f"(need classes 14 + 18 in the histogram)")
        insp = HERE.parent.parent / "classification" / "src" / "skill" / \
            "scripts" / "inspect_cloud.py"
        for c in clouds:
            run_cmd([py, insp, c], args.plan)

    # The cropper (pipeline.py) scans --input-dir RECURSIVELY, so leftovers
    # under Lidar\Classified (chain intermediates, _work debris) get swept in
    # as extra input tiles — duplicated points, bloated crops, and crops cut
    # from unclassified variants. Stage a flat, pattern-matched view of the
    # published clouds and hand the stages that instead.
    stage_in = work / "00_input_las"
    if ("discover" in wanted or "crop" in wanted) and not args.plan:
        srcs = sorted(glob(str(input_dir / args.las_pattern)))
        if not srcs:
            sys.exit(f"ERROR: nothing matches {args.las_pattern} in {input_dir}")
        stage_in.mkdir(parents=True, exist_ok=True)
        for s in srcs:
            dst = stage_in / Path(s).name
            if not dst.exists():
                try:
                    os.link(s, dst)
                except OSError:
                    shutil.copy2(s, dst)
        log(f"staged {len(srcs)} input cloud(s) ({args.las_pattern}, top level "
            f"only) -> {stage_in}")

    if "discover" in wanted:
        script = chain_script(args.chain_src, DISCOVERY, args.plan)
        if not args.plan:
            poles_shp.parent.mkdir(parents=True, exist_ok=True)
        run_cmd([py, script, stage_in if not args.plan else input_dir, poles_shp,
                 "--pattern", args.las_pattern, "--epsg", args.epsg,
                 "--min-pts", 15, "--min-z-range", 1.5], args.plan)

    if "crop" in wanted:
        script = chain_script(args.chain_src, CROPPER, args.plan)
        run_cmd([py, script,
                 "--input-dir", stage_in if not args.plan else input_dir,
                 "--pole-shapefile", poles_shp,
                 "--output-dir", crop_out,
                 "--data-srs", f"EPSG:{args.epsg}",
                 "--half-size", args.half_size,
                 "--search-radius", 5, "--voxel-size", 0.025,
                 "--min-pole-height", args.min_pole_height,
                 "--max-pole-height", args.max_pole_height,
                 "--max-points-per-tile", 40_000_000,
                 "--max-workers", args.max_workers], args.plan)
        # NOTE: never --compress-intermediates — .laz crops are invisible to pole-vec

    if "network" in wanted:
        run_cmd([py, SKILL_SCRIPTS / "link_crops_for_network.py",
                 "--crops-dir", crops, "--out-dir", network_in], args.plan)
        script = chain_script(args.chain_src, NETWORK, args.plan)
        run_cmd([py, script,
                 "--pole-tops", poles_shp.with_name("poles_candidates_processed.shp"),
                 "--waveb-dir", network_in,
                 "--waveb-crs", f"EPSG:{args.epsg}",
                 "--output-crs", f"EPSG:{args.epsg}",
                 "--output-dir", topo_out,
                 "--max-span-m", args.max_span,
                 "--project-name", project], args.plan)
        publish(topo_out, out_dir / "Network", ["*.*"], args.plan)

    if "polevec" in wanted:
        run_cmd([py, SKILL_SCRIPTS / "prepare_polevec_run.py",
                 "--crops-dir", crops,
                 "--processed-csv", poles_shp.with_name("poles_candidates_processed.csv"),
                 "--run-dir", pv_run, "--crs", args.epsg,
                 "--las-threads", args.las_threads], args.plan)
        docker_cmd = ["docker", "run", "--rm", "--gpus", "all", "--shm-size=8gb",
                      "-v", "sdai_chain_polevec:/app",
                      "-v", f"{pv_run}:/data",
                      POLEVEC_IMAGE,
                      "python", "/app/PoleVec_workflow.py",
                      "--input_inputconfig", "/app/inputconfig_FIRMATEK_csv_seed.yml",
                      "--input_folder", "/data",
                      "--input_inputcontrol", "PoleVec_control_runtime.yml",
                      "--input_segmented_folder", "02_pole_crop/output/crops_metric"]
        run_cmd(docker_cmd, args.plan)
        # exit 0 LIES — gate on the layers actually existing
        combined = pv_run / "PoleVec" / "Combined"
        if not args.plan:
            missing = [n for n in ("Grp0_Body_Lines.shp", "catenary.shp")
                       if not (combined / n).is_file()]
            if missing:
                sys.exit(f"ERROR: pole-vec exited 0 but produced no {missing} in "
                         f"{combined} — check class-14 density in the crops")
        publish(combined, out_dir / "PoleVec", ["*.*"], args.plan)
        publish(pv_run / "PoleVec" / "topology", out_dir / "PoleVec" / "topology",
                ["*.*"], args.plan)

    if "clean" in wanted:
        if args.plan:
            log(f"PLAN clean: delete {work}")
        elif work.is_dir():
            size = sum(f.stat().st_size for f in work.rglob("*") if f.is_file())
            shutil.rmtree(work)
            log(f"clean: deleted {work} ({size / 1e9:.1f} GB of temp files "
                f"incl. per-pole crops)")
        else:
            log(f"clean: nothing to delete ({work} absent)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
