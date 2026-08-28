"""
dfx.py — DFX project pipeline orchestrator (runs inside Docker)

Stages: csv | shp | lasindex | pano

Usage:
  python dfx.py <maindir> [--stages all] [--epsg 26914] [--pn <id>]
                [--platform viewer|cloud] [--addhp yes|no] [--pano-workers 16]

Expects maindir to contain one or more DFX mission folders, each with:
  <mission>/
    Raw Project Data/
      Image Project/
        Image Project.lst
        <Run N Camera 4 360>/   (one dir per run)
      LAS Files/
        *.las

Outputs per mission (all stages idempotent):
  <mission>/
    <pname>_Image Project.lst         (csv stage)
    <pname>_CSVs/<pname>.csv          (csv stage)
    <pname>_CSVs/<pname>_CameraPoints2.shp  (shp stage)
    <pname>_LASINDEX.shp              (lasindex stage)
    Pano_output/*.jpg                 (pano stage)
"""
import argparse
import os
import shutil
import subprocess
import sys

import geopandas as gpd
import laspy
from shapely.geometry import Polygon

sys.path.insert(0, os.path.dirname(__file__))
from dfx_csv import create_csv
from dfx_shp import create_camera_points
from preflight import parse_stages, validate

PANO_SCRIPT = os.environ.get("DFX_PANO_SCRIPT") or os.path.join(os.path.dirname(__file__), "pano_generator.py")


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def get_las_bounds(las_path):
    with laspy.open(las_path) as f:
        h = f.header
        return h.mins[0], h.mins[1], h.maxs[0], h.maxs[1]


def stage_csv(mission_dir, pname):
    out_csv = os.path.join(mission_dir, pname + "_CSVs", pname + ".csv")
    if os.path.exists(out_csv):
        print(f"  [csv] skip — {pname}.csv exists")
        return out_csv

    lst_src  = os.path.join(mission_dir, "Raw Project Data", "Image Project", "Image Project.lst")
    lst_copy = os.path.join(mission_dir, pname + "_Image Project.lst")
    shutil.copy(lst_src, lst_copy)
    result = create_csv(lst_copy, icsv=False, shrp=True)
    print(f"  [csv] -> {result}")
    return result


def stage_shp(mission_dir, pname, csv_path, pn, epsg, addhp, platform):
    out_shp = os.path.join(mission_dir, pname + "_CSVs", pname + "_CameraPoints2.shp")
    stem = os.path.splitext(out_shp)[0]
    if all(os.path.exists(stem + ext) for ext in (".shp", ".shx", ".dbf")):
        print(f"  [shp] skip — {pname}_CameraPoints2.shp exists")
        return
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    result = create_camera_points(csv_path, pn, epsg, addhp=addhp, platform=platform)
    print(f"  [shp] -> {result}")


def stage_lasindex(mission_dir, pname, epsg):
    out_shp = os.path.join(mission_dir, pname + "_LASINDEX.shp")
    stem = os.path.splitext(out_shp)[0]
    if all(os.path.exists(stem + ext) for ext in (".shp", ".shx", ".dbf")):
        print(f"  [lasindex] skip — {pname}_LASINDEX.shp exists")
        return

    las_dir = os.path.join(mission_dir, "Raw Project Data", "LAS Files")
    data = []
    for fname in os.listdir(las_dir):
        if not fname.lower().endswith(('.las', '.laz')):
            continue
        min_x, min_y, max_x, max_y = get_las_bounds(os.path.join(las_dir, fname))
        poly = Polygon([(min_x, min_y), (min_x, max_y), (max_x, max_y), (max_x, min_y)])
        data.append({'geometry': poly, 'Las': fname, 'Lasname': fname, 'Foldername': pname})

    if not data:
        raise FileNotFoundError(f"no LAS/LAZ files in {las_dir}")

    gdf = gpd.GeoDataFrame(data)
    gdf = gdf.set_crs(epsg=epsg)
    gdf.to_file(out_shp)
    print(f"  [lasindex] {len(data)} files -> {out_shp}")


def stage_pano(mission_dir, pname, workers):
    img_proj = os.path.join(mission_dir, "Raw Project Data", "Image Project")
    pano_out  = os.path.join(mission_dir, "Pano_output")

    run_dirs = sorted(
        d for d in os.listdir(img_proj)
        if os.path.isdir(os.path.join(img_proj, d))
    )
    if not run_dirs:
        raise FileNotFoundError(f"no run dirs found in {img_proj}")

    os.makedirs(pano_out, exist_ok=True)

    for run in run_dirs:
        input_dir = os.path.join(img_proj, run)
        faces = [p for p in os.listdir(input_dir) if p.lower().endswith((".jpg", ".jpeg"))]
        expected = {os.path.splitext(p)[0][:-2] + ".jpg" for p in faces if len(os.path.splitext(p)[0]) > 2}
        missing_before = [name for name in expected if not os.path.isfile(os.path.join(pano_out, name))]
        if not missing_before:
            print(f"  [pano] skip — {run} complete")
            continue
        print(f"  [pano] {run} ...")
        subprocess.run(
            [sys.executable, PANO_SCRIPT,
             "-p", "cubemap_to_panoramic",
             "--input_folder",  input_dir,
             "--output_folder", pano_out,
             "--export_type",   "jpg",
             "--rotation_type", "leica",
             "--workers",       str(workers)],
            check=True,
        )
        missing_after = [name for name in expected if not os.path.isfile(os.path.join(pano_out, name))]
        if missing_after:
            raise RuntimeError(f"{run}: {len(missing_after)} panorama(s) still missing")

    done = glob.glob(os.path.join(pano_out, "*.jpg"))
    print(f"  [pano] {len(done)} JPGs -> {pano_out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("maindir")
    ap.add_argument("--stages",       default="all")
    ap.add_argument("--epsg",         type=positive_int, default=26914)
    ap.add_argument("--pn",           default="")
    ap.add_argument("--platform",     default="viewer", choices=["viewer", "cloud"])
    ap.add_argument("--addhp",        default="yes",    choices=["yes", "no"])
    ap.add_argument("--pano-workers", type=positive_int, default=16)
    args = ap.parse_args()

    try:
        stages = set(parse_stages(args.stages))
    except ValueError as exc:
        ap.error(str(exc))
    addhp  = args.addhp == "yes"

    maindir = args.maindir
    report = validate(maindir, args.stages)
    if not report["ok"]:
        for check in report["checks"]:
            print(f"ERROR {check['code']}: {check['message']} [{check['path']}]", file=sys.stderr)
        return 2
    missions = report["missions"]

    print(f"Found {len(missions)} mission(s): {', '.join(os.path.basename(m) for m in missions)}")

    failures = []
    for mission_dir in missions:
        pname = os.path.basename(mission_dir)
        print(f"\n=== {pname} ===")

        csv_path = os.path.join(mission_dir, pname + "_CSVs", pname + ".csv")

        try:
            if "csv" in stages:
                csv_path = stage_csv(mission_dir, pname)
            if "shp" in stages:
                stage_shp(mission_dir, pname, csv_path, args.pn, args.epsg, addhp, args.platform)
            if "lasindex" in stages:
                stage_lasindex(mission_dir, pname, args.epsg)
            if "pano" in stages:
                stage_pano(mission_dir, pname, args.pano_workers)
        except Exception as exc:
            failures.append((pname, str(exc)))
            print(f"  ERROR: {exc}", file=sys.stderr)

    if failures:
        print(f"\nDFX FAILED: {len(failures)} mission(s) failed.", file=sys.stderr)
        return 1
    print("\nAll missions complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
