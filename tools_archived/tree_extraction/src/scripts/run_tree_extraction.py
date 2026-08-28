#!/usr/bin/env python
"""Thin, self-contained wrapper for AECON tree-stem + canopy extraction.

Runs the vendored classical extractor ``extract_tree_trunk_canopy.py`` directly
with the current Python interpreter (this machine's ``gdal_env`` conda env, which
already carries numpy/scipy/laspy/lazrs/geopandas/pyogrio/shapely). No pixi, no
Docker, no chain-orchestrator package runner -- this skill is fully vendored into
the AECON workflow and has ZERO dependency on Greg_Sandbox.

All flags pass straight through to the worker; see its --help. Typical call:

    python run_tree_extraction.py \\
        --input  <3_Classified_LAS>/scene_tf1_pointconv_combined_0p1m.laz \\
        --out-dir <4_Extracted_SHP> \\
        --epsg 26917 --canopy-class 5 --ground-class 2 --measure-height 1.0

Exit codes (propagated from the worker): 0 = success, 3 = benign-empty
(tree-less tile), 1 = error.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WORKER = Path(__file__).resolve().parent / "extract_tree_trunk_canopy.py"


def _gdal_env() -> dict:
    """Point GDAL/PROJ at the conda env's data dirs so shapefile .prj writes and
    EPSG reprojection resolve, regardless of how the interpreter was launched."""
    env = dict(os.environ)
    prefix = Path(sys.executable).resolve().parent          # ...\envs\gdal_env
    gdal_data = prefix / "Library" / "share" / "gdal"
    proj_data = prefix / "Library" / "share" / "proj"
    if gdal_data.is_dir():
        env.setdefault("GDAL_DATA", str(gdal_data))
    if proj_data.is_dir():
        env.setdefault("PROJ_LIB", str(proj_data))
    return env


def main(argv: list[str]) -> int:
    if not WORKER.is_file():
        print(f"FAIL: worker not found next to wrapper: {WORKER}", file=sys.stderr)
        return 1
    cmd = [sys.executable, str(WORKER)] + list(argv)
    print("[run_tree_extraction] " + " ".join(cmd))
    return subprocess.run(cmd, env=_gdal_env()).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
