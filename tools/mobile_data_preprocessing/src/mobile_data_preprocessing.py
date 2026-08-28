r"""SDAI mobile-data preprocessing — one front door for the DFX and AECON pipelines.

(Renamed 2026-08-26 from `sdai-process` after the Cambridge_P3_60 validation run.)

Both clients share the same processing shape, and this tool drives the two
existing sdtools tool sources (tools/aecon_real, tools/dfx_process) with ONE
unified stage vocabulary. Everything runs natively in this tool's environment
(the aecon_real pixi env — a superset carrying cv2/PIL for pano, geopandas for
shp, and python-pdal for colorize). No Docker, no Solv3D: the pano stage for
BOTH clients is the shared pano_generator.py, invoked for AECON through
aecon.py's --engine command-string hook.

Unified stages — named by PURPOSE, not file format (mapped per client; a
stage a client doesn't have is skipped with a note). Old names `csv` and
`shp` are accepted as aliases of `metadata` and `index`.

    stage      what / why                          AECON (aecon.py)   DFX (dfx.py)
    --------   ---------------------------------   ----------------   ------------
    organize   normalize the raw vendor delivery   yaml,organize      (n/a — DFX
               into the standard project layout                       arrives
               (AECON ships 6 loose per-camera                        organized)
               folders named by the .iprj)
    metadata   extract the camera pose table:      metadata           csv
               forward-camera frames, HRP angles
               converted to Roll/Pitch/Yaw — the
               format pano + colorize consume
    index      persist camera-points + LAS-extent  (n/a — built as    shp,lasindex
               shapefiles (viewer/QC deliverable)  temp files inside
                                                   colorize instead)
    pano       cubemap faces -> equirectangular    pano (--engine =   pano
               panoramas; also rewrites the pose   python pano_
               table onto the pano filenames       generator.py)
    colorize   paint LAS points with pano RGB,     colorize           (n/a — DFX
               clip to the camera-track buffer;                       clouds arrive
               merges per-run pose tables first                       colorized)

The full artifact-by-artifact story (what every YML/CSV is and why it
exists) lives in C:\sdtools\docs\WORKFLOW.md.

Canonical LAS layout — every stage owns one subfolder under <project>/Lidar/
so future steps slot in without inventing new locations:

    <project>/Lidar/
        *.las           raw delivery (untouched — the AECON input format)
        Colorized/      full colorized LAS (*_colorized.las; transient by
                        default, kept with --keep-colorized)
        Clipped/        colorized + buffer-clipped LAS (*_clipped.las) —
                        the classification input
        Classified/     reserved for the pointconv output (next step)

Classification is deliberately NOT a stage here: PointCONV inference needs the
mmworkflow GPU Docker image (see the `classification` tool) and this tool is
the no-external-dependency path. After `all` completes, the classify input is:
  AECON: <project>/Lidar/Clipped/*_clipped.las (colorized)
  DFX:   the clipped LAS from tile-thin-clip
Downstream: `sdtools classification` -> Lidar/Classified, then
`sdtools pole-vec` -> Vectors/Poles.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS_ROOT = HERE.parent.parent            # .../tools
AECON_SCRIPTS = Path(os.environ.get("SDAI_AECON_SRC")
                     or TOOLS_ROOT / "aecon_real" / "src" / "scripts")
DFX_SCRIPTS = Path(os.environ.get("SDAI_DFX_SRC")
                   or TOOLS_ROOT / "dfx_process" / "src" / "scripts")

UNIFIED = ["organize", "metadata", "index", "pano", "colorize"]
ALIASES = {"csv": "metadata", "shp": "index"}   # pre-rename stage names
STAGE_MAP = {
    "aecon": {"organize": "yaml,organize", "metadata": "metadata",
              "index": None, "pano": "pano", "colorize": "colorize"},
    "dfx":   {"organize": None, "metadata": "csv",
              "index": "shp,lasindex", "pano": "pano", "colorize": None},
}


def detect_client(root: Path) -> str:
    """AECON raw deliveries carry a Solv3D .iprj; DFX missions carry
    'Raw Project Data/Image Project/Image Project.lst'."""
    candidates = [root] + [d for d in root.iterdir() if d.is_dir()]
    for d in candidates:
        if list(d.glob("*.iprj")) or list(d.glob("*/*.iprj")):
            return "aecon"
    for d in candidates:
        if (d / "Raw Project Data" / "Image Project" / "Image Project.lst").is_file():
            return "dfx"
    sys.exit(f"ERROR: could not detect client under {root} — no *.iprj (AECON) "
             f"and no 'Raw Project Data/Image Project/Image Project.lst' (DFX) "
             f"found; pass --client explicitly.")


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Unified DFX/AECON processing pipeline (native, no Docker)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("data_root", help="dataset root (missions/projects inside)")
    ap.add_argument("--client", default="auto", choices=["auto", "aecon", "dfx"])
    ap.add_argument("--stages", default="all",
                    help="comma-separated unified stages "
                         f"({','.join(UNIFIED)}) or 'all'; "
                         "csv/shp accepted as aliases of metadata/index")
    ap.add_argument("--epsg", type=positive_int, required=True,
                    help="working CRS from the work order / dataset memory")
    ap.add_argument("--pano-workers", type=positive_int, default=8,
                    help="pano workers (DFX) / threads per LAS (AECON --threads)")
    ap.add_argument("--project-workers", type=positive_int, default=2,
                    help="AECON: parallel projects")
    ap.add_argument("--las-workers", type=positive_int, default=2,
                    help="AECON colorize: parallel LAS files per project")
    ap.add_argument("--search-radius", type=positive_int, default=45,
                    help="AECON colorize search radius (m)")
    ap.add_argument("--buffer", type=positive_int, default=45,
                    help="AECON colorize clip buffer (m)")
    ap.add_argument("--keep-colorized", action="store_true",
                    help="AECON: keep the full colorized LAS in Lidar/Colorized "
                         "after clipping (default: delete it to save disk)")
    ap.add_argument("--pn", default="", help="DFX: project number for viewer hyperlinks")
    ap.add_argument("--platform", default="viewer", choices=["viewer", "cloud"],
                    help="DFX: hyperlink platform")
    ap.add_argument("--addhp", default="yes", choices=["yes", "no"],
                    help="DFX: add viewer hyperlinks to camera points")
    ap.add_argument("--plan", action="store_true",
                    help="print the child command(s) without running them")
    args = ap.parse_args()

    root = Path(args.data_root)
    if not root.is_dir():
        sys.exit(f"ERROR: data_root not found: {root}")

    client = args.client if args.client != "auto" else detect_client(root)
    wanted = UNIFIED if args.stages == "all" else [
        ALIASES.get(s.strip(), s.strip())
        for s in args.stages.split(",") if s.strip()]
    bad = [s for s in wanted if s not in UNIFIED]
    if bad:
        sys.exit(f"ERROR: unknown stage(s) {bad}; valid: {UNIFIED} "
                 f"(aliases: {ALIASES}) or 'all'")

    mapped, skipped = [], []
    for s in wanted:
        child_stage = STAGE_MAP[client][s]
        (mapped if child_stage else skipped).append(child_stage or s)
    if skipped:
        print(f"[mobile-data-preprocessing] {client}: stage(s) {skipped} not applicable — skipped")
    if not mapped:
        print("[mobile-data-preprocessing] nothing to do for this client/stage selection")
        return 0
    child_stages = ",".join(mapped)

    if client == "aecon":
        entry = AECON_SCRIPTS / "aecon.py"
        pano_gen = AECON_SCRIPTS / "pano_generator.py"
        cmd = [sys.executable, str(entry), str(root),
               "--stages", child_stages,
               "--crs", f"EPSG:{args.epsg}",
               "--search-radius", str(args.search_radius),
               "--buffer", str(args.buffer),
               "--project-workers", str(args.project_workers),
               "--las-workers", str(args.las_workers),
               "--threads", str(args.pano_workers)]
        if args.keep_colorized:
            cmd += ["--keep-colorized"]
        if "pano" in child_stages:
            # native pano: same generator DFX uses, via aecon.py's
            # command-string engine hook (no Solv3D, no Docker).
            # NO quotes: aecon.py splits with shlex(posix=False), which would
            # keep quote characters on the tokens and break CreateProcess.
            if " " in sys.executable or " " in str(pano_gen):
                sys.exit("ERROR: the interpreter or pano_generator path contains "
                         "a space — aecon.py's --engine string can't carry that. "
                         "Move the toolkit/env to a space-free path.")
            # embed --workers in the engine string: stage_panorama appends its
            # own args after it, and without an explicit count pano_generator
            # defaults to ALL cores — which OOMs 32GB boxes (each worker holds
            # 6 cubemap faces + the 8K equirect + cv2.remap buffers)
            cmd += ["--engine",
                    f"{sys.executable} {pano_gen} --workers {args.pano_workers}"]
    else:
        entry = DFX_SCRIPTS / "dfx.py"
        cmd = [sys.executable, str(entry), str(root),
               "--stages", child_stages,
               "--epsg", str(args.epsg),
               "--pn", args.pn,
               "--platform", args.platform,
               "--addhp", args.addhp,
               "--pano-workers", str(args.pano_workers)]

    if not entry.is_file():
        sys.exit(f"ERROR: child entry not found: {entry} — is the "
                 f"{'aecon_real' if client == 'aecon' else 'dfx_process'} "
                 f"tool present in this toolkit?")

    print(f"[mobile-data-preprocessing] client={client}  stages={child_stages}")
    print("[mobile-data-preprocessing] $ " + " ".join(shlex.quote(c) for c in cmd), flush=True)
    if args.plan:
        return 0
    rc = subprocess.run(cmd).returncode
    if rc == 0 and args.stages == "all":
        print("[mobile-data-preprocessing] done. Next steps for both clients: "
              "sdtools classification (Lidar/Clipped -> Lidar/Classified; stage1 "
              "needs the GPU box), then sdtools pole-vec (-> Vectors/Poles).")
    return rc


if __name__ == "__main__":
    sys.exit(main())
