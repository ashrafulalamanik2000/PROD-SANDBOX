"""Stage functions for the AECON pipeline. Each stage is idempotent."""
import os
import csv
import shutil
import subprocess
import tempfile
from glob import glob
import yaml
import pandas as pd
import geopandas as gpd
import laspy
from shapely.geometry import Point, Polygon

from rotation_utils import Frot
from colorize import colorize_one_las
from camera_utils import create_camera_buffer, clip_las_with_buffer

CAM_MAP = {0: 0, 1: 3, 2: 2, 3: 1, 4: 4, 5: 5}
LAS_FOLDER_NAME = "LAS Files"


# ─── Stage 1: LST → InputConfig.yml ─────────────────────────────────
def stage_yaml(project_dir):
    """Convert all .lst files in project to InputConfig.yml. Multi-run aware."""
    output_yml = os.path.join(project_dir, "InputConfig.yml")
    if os.path.exists(output_yml):
        try:
            with open(output_yml, 'r') as existing:
                if (yaml.safe_load(existing) or {}).get("Run_Settings"):
                    return f"skipped (valid): {os.path.basename(project_dir)}"
        except (OSError, yaml.YAMLError):
            pass

    lst_files = sorted(glob(os.path.join(project_dir, "*.lst")))
    if not lst_files:
        raise FileNotFoundError(f"no .lst: {os.path.basename(project_dir)}")

    las_files = sorted(
        os.path.basename(path)
        for path in glob(os.path.join(project_dir, "Lidar", "*.las"))
    )
    run_settings = {}
    for run_num, lst_file in enumerate(lst_files, 1):
        images = []
        current = {}
        with open(lst_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("["):
                    continue
                if "=" not in line:
                    continue
                key, value = [x.strip() for x in line.split("=", 1)]
                if key == "Image":
                    if current:
                        images.append(current)
                    current = {"Image": value}
                elif key == "Xyz":
                    current["Xyz"] = [float(x) for x in value.split()]
                elif key == "Hrp":
                    current["Hrp"] = [float(x) for x in value.split()]
                elif key == "Camera":
                    current["Camera"] = int(value)
        if current:
            images.append(current)

        run_settings[f"Run_{run_num}"] = {
            "input_imagefolder": "Images",
            "input_imagelist": images,
            "input_lasFolder": LAS_FOLDER_NAME,
            "input_laslist": las_files
        }

    with open(output_yml, 'w') as f:
        yaml.safe_dump({"Run_Settings": run_settings}, f, sort_keys=False)
    return f"yaml OK: {os.path.basename(project_dir)} ({len(lst_files)} runs)"


# ─── Stage 2: Organize files ─────────────────────────────────────────
def _splitLine(line):
    k, v = line.split("=")
    return k.strip(), v.strip()


def _parseToMap(contents):
    ret = {}
    for line in contents:
        if "=" not in line:
            continue
        k, v = _splitLine(line)
        ret[k] = v
    return ret


def _get_cam_folder_map(iprj):
    contents = open(iprj).readlines()
    parsed = _parseToMap(contents)
    return {int(k[-1]): parsed[k] for k in parsed if k.startswith("Name")}


def stage_organize(project_dir):
    """Organize images + LAS into Organized_Projects/Raw Project Data/. Idempotent via YAML check."""
    out_yaml = os.path.join(project_dir, "Organized_Projects", "Raw Project Data", "InputConfig.yml")
    input_yml = os.path.join(project_dir, "InputConfig.yml")
    if not os.path.exists(input_yml):
        raise FileNotFoundError(f"no InputConfig.yml: {os.path.basename(project_dir)}")

    iprj_files = glob(os.path.join(project_dir, "**/*.iprj"), recursive=True)
    if not iprj_files:
        raise FileNotFoundError(f"no .iprj: {os.path.basename(project_dir)}")
    cam_folder_map = _get_cam_folder_map(iprj_files[0])

    out_root = os.path.join(project_dir, "Organized_Projects", "Raw Project Data")
    out_image_dir = os.path.join(out_root, "Image Project")
    out_las_dir = os.path.join(out_root, "LAS Files")
    os.makedirs(out_las_dir, exist_ok=True)

    with open(input_yml, 'r') as f:
        yaml_in = yaml.safe_load(f.read())

    yaml_out = {"Run_Settings": {}}

    for run in yaml_in["Run_Settings"]:
        run_num = int(run.split("_")[-1])
        out_run_dir = os.path.join(out_image_dir, f"Run {run_num} Camera 4 360")
        os.makedirs(out_run_dir, exist_ok=True)

        yaml_out["Run_Settings"][run] = {
            "input_imagefolder": f"./Image Project/Run {run_num} Camera 4 360",
            "input_imagelist": [],
            "input_lasFolder": "LAS Files",
            "input_laslist": []
        }

        src_image_folder = yaml_in["Run_Settings"][run]["input_imagefolder"]
        src_las_folder = yaml_in["Run_Settings"][run]["input_lasFolder"]

        # Images — only Camera=1 gets expanded to 6 cam folders via CAM_MAP
        for image in yaml_in["Run_Settings"][run]["input_imagelist"]:
            if int(image["Camera"]) != 1:
                continue
            for i in range(6):
                cam_folder = cam_folder_map.get(i)
                if not cam_folder:
                    continue
                img_src = os.path.normpath(os.path.join(project_dir, src_image_folder, cam_folder, os.path.basename(image["Image"])).replace('\\', '/'))
                if not os.path.exists(img_src):
                    continue
                out_cam = CAM_MAP[i]
                img_name = os.path.basename(img_src)
                img_out_name = f"Run {run_num}_{os.path.splitext(img_name)[0]}_{out_cam}.jpg"
                img_out = os.path.join(out_run_dir, img_out_name)
                if not os.path.exists(img_out):
                    shutil.copy(img_src, img_out)
                yaml_out["Run_Settings"][run]["input_imagelist"].append({
                    "Camera": out_cam,
                    "Hrp": image["Hrp"],
                    "Xyz": image["Xyz"],
                    "Image": img_out_name
                })

        # LAS
        for las_file in yaml_in["Run_Settings"][run]["input_laslist"]:
            las_src = os.path.normpath(os.path.join(project_dir, src_las_folder, las_file).replace('\\', '/'))
            las_out = os.path.join(out_las_dir, las_file)
            yaml_out["Run_Settings"][run]["input_laslist"].append(las_file)
            if os.path.exists(las_src) and not os.path.exists(las_out):
                shutil.copy(las_src, las_out)

    os.makedirs(os.path.dirname(out_yaml), exist_ok=True)
    with open(out_yaml, 'w') as f:
        yaml.safe_dump(yaml_out, f)
    return f"organized: {os.path.basename(project_dir)}"


# ─── Stage 3: Metadata CSV ──────────────────────────────────────────
def stage_metadata(project_dir, shrp=True):
    """Create Run_N_metadata.csv for Camera 3 images. Idempotent per run."""
    input_yml = os.path.join(project_dir, "Organized_Projects", "Raw Project Data", "InputConfig.yml")
    if not os.path.exists(input_yml):
        raise FileNotFoundError(f"no organized yaml: {os.path.basename(project_dir)}")

    root_dir = os.path.dirname(input_yml)
    with open(input_yml, 'r') as f:
        data = yaml.safe_load(f)

    created = []
    for run in data["Run_Settings"]:
        img_dirs = set()
        for image in data["Run_Settings"][run]["input_imagelist"]:
            p = os.path.normpath(os.path.join(data["Run_Settings"][run]["input_imagefolder"],
                                              image["Image"]).replace('\\', '/'))
            img_dirs.add(os.path.dirname(p))
        if len(img_dirs) != 1:
            continue
        img_dir = img_dirs.pop()
        bigcsv = os.path.join(root_dir, img_dir, f"{run}_metadata.csv")

        if os.path.exists(bigcsv):
            continue

        header = ['Filename', 'X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw'] if shrp else \
                 ['Filename', 'X', 'Y', 'Z', 'Yaw', 'Roll', 'Pitch']

        with open(bigcsv, 'w', newline='') as fcsv:
            w = csv.writer(fcsv)
            w.writerow(header)
            for image in data["Run_Settings"][run]["input_imagelist"]:
                if image["Camera"] != 3:
                    continue
                hrp = image["Hrp"]
                xyz = image["Xyz"]
                if shrp:
                    rot = Frot(float(hrp[0]), float(hrp[1]), float(hrp[2]))
                else:
                    rot = [float(hrp[0]), float(hrp[1]), float(hrp[2])]
                w.writerow([image["Image"], xyz[0], xyz[1], xyz[2], str(rot[0]), str(rot[1]), str(rot[2])])
        created.append(run)

    all_metadata = glob(os.path.join(root_dir, "**/*_metadata.csv"), recursive=True)
    if not all_metadata:
        raise RuntimeError(f"no metadata output produced: {os.path.basename(project_dir)}")

    return f"metadata: {os.path.basename(project_dir)} ({len(created)} runs)"


# ─── Stage 4: Panorama via Solv3D engine ────────────────────────────
def stage_panorama(project_dir, engine_exe):
    """Call Solv3D engine.exe for each run's metadata CSV. Idempotent per CSV."""
    root_dir = os.path.join(project_dir, "Organized_Projects")
    out_dir = os.path.join(root_dir, "Pano_output")
    os.makedirs(out_dir, exist_ok=True)

    csv_files = [c for c in glob(os.path.join(root_dir, "**/*_metadata.csv"), recursive=True)
                 if out_dir not in c]
    if not csv_files:
        raise FileNotFoundError(f"no metadata CSVs: {os.path.basename(project_dir)}")

    done = 0
    for csv_file in csv_files:
        output_csv = os.path.join(out_dir, os.path.basename(csv_file))
        if os.path.isfile(output_csv):
            continue

        import shlex
        # engine_exe can be a single path or a command string (e.g.
        # "python3 /app/pano_generator.py"). Split only if it has spaces.
        engine_cmd = shlex.split(engine_exe, posix=False) if " " in engine_exe else [engine_exe]
        args = engine_cmd + ["-p", "cubemap_to_panoramic",
                             "--input_folder", os.path.dirname(csv_file),
                             "--export_type", "jpg",
                             "--rotation_type", "leica",
                             "--output_folder", out_dir]
        subprocess.run(args, check=True)

        with open(csv_file, newline='') as expected_file:
            expected = {
                row["Filename"][:-6] + ".jpg"
                for row in csv.DictReader(expected_file)
                if row.get("Filename") and len(row["Filename"]) > 6
            }
        missing = [name for name in expected if not os.path.isfile(os.path.join(out_dir, name))]
        if missing:
            raise RuntimeError(f"{len(missing)} panorama(s) missing after engine completed")

        with open(csv_file, newline='\n') as fin:
            reader = csv.DictReader(fin)
            headers = reader.fieldnames
            with open(output_csv, 'w', newline='') as fout:
                w = csv.writer(fout)
                w.writerow(headers)
                for line in reader:
                    filename = line["Filename"][:-6] + ".jpg"
                    w.writerow([filename, line["X"], line["Y"], line["Z"],
                                line["Roll"], line["Pitch"], line["Yaw"]])
        done += 1

    return f"pano: {os.path.basename(project_dir)} ({done} new)"


# ─── Stage 5: Colorize + Clip ───────────────────────────────────────
def _create_camera_points_from_csv(csv_path, out_shp, crs):
    df = pd.read_csv(csv_path)
    features = []
    for _, row in df.iterrows():
        try:
            features.append({
                'Filename': row['Filename'],
                'geometry': Point(float(row['X']), float(row['Y'])),
                'X': float(row['X']), 'Y': float(row['Y']), 'Z': row['Z'],
                'Roll': row['Roll'], 'Pitch': row['Pitch'], 'Yaw': row['Yaw']
            })
        except (KeyError, ValueError):
            continue
    gdf = gpd.GeoDataFrame(features, crs=crs)
    gdf.to_file(out_shp)


def _create_las_index(las_folder, out_shp, crs):
    features = []
    for las_path in glob(os.path.join(las_folder, "*.las")):
        try:
            with laspy.open(las_path) as las:
                h = las.header
                bounds = [[h.mins[0], h.mins[1]], [h.maxs[0], h.mins[1]],
                          [h.maxs[0], h.maxs[1]], [h.mins[0], h.maxs[1]], [h.mins[0], h.mins[1]]]
                features.append({
                    'filename': os.path.basename(las_path),
                    'geometry': Polygon(bounds),
                    'min_x': h.mins[0], 'min_y': h.mins[1],
                    'max_x': h.maxs[0], 'max_y': h.maxs[1]
                })
        except Exception:
            continue
    gdf = gpd.GeoDataFrame(features, crs=crs)
    gdf.to_file(out_shp)


def _colorize_clip_one(task):
    """Worker: colorize + clip one LAS. Runs in multiprocessing.Pool."""
    (las_path, eop_csv, panos_shp, lasindex_shp, images_folder,
     output_folder, colorized_folder, buffer_shp, search_radius, threads,
     keep_colorized) = task
    base = os.path.basename(las_path)
    final_name = base.replace(".las", "_clipped.las")
    final_path = os.path.join(output_folder, final_name)
    if os.path.exists(final_path):
        return (base, "skip")

    colored = os.path.join(colorized_folder, base.replace(".las", "_colorized.las"))
    try:
        if not os.path.exists(colored):
            colorize_one_las(las_path, eop_csv, panos_shp, lasindex_shp, images_folder,
                             colored, search_radius=search_radius, threads=threads)
        clip_las_with_buffer(colored, buffer_shp, final_path)
        if not keep_colorized:
            try:
                os.remove(colored)
            except Exception:
                pass
        return (base, "ok")
    except Exception as e:
        return (base, f"fail: {e}")


def stage_colorize(project_dir, crs, search_radius=45, buffer_distance_m=45,
                   threads=8, workers=2, keep_colorized=False):
    """Colorize + clip all LAS files. Parallel across files (workers), each uses threads.

    Canonical per-stage layout under <project>/Lidar/ (raw *.las stays loose —
    that's the AECON delivery format the earlier stages read):
        Colorized/   full colorized LAS (transient unless keep_colorized)
        Clipped/     colorized + buffer-clipped LAS — the classify input
        Classified/  reserved for the pointconv output (created here so the
                     slot exists; nothing in this tool writes to it yet)
    """
    las_folder = os.path.join(project_dir, "Lidar")
    if not os.path.exists(las_folder):
        raise FileNotFoundError(f"no Lidar/: {os.path.basename(project_dir)}")

    pano_dir = os.path.join(project_dir, "Organized_Projects", "Pano_output")
    eop_files = sorted(glob(os.path.join(pano_dir, "Run_*_metadata.csv")))
    if not eop_files:
        raise FileNotFoundError(f"no Run_*_metadata.csv: {os.path.basename(project_dir)}")
    eop_csv = eop_files[0]
    if len(eop_files) > 1:
        eop_csv = os.path.join(pano_dir, "All_Runs_metadata.csv")
        rows = []
        headers = None
        seen = set()
        for source in eop_files:
            with open(source, newline='') as stream:
                reader = csv.DictReader(stream)
                headers = headers or reader.fieldnames
                for row in reader:
                    key = row.get("Filename")
                    if key and key not in seen:
                        seen.add(key)
                        rows.append(row)
        if not headers or not rows:
            raise RuntimeError("multi-run metadata files contain no camera rows")
        temp_csv = eop_csv + ".tmp"
        with open(temp_csv, 'w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_csv, eop_csv)

    las_files = sorted(glob(os.path.join(las_folder, "*.las")))
    las_files = [f for f in las_files if not f.endswith("_cl.las")]
    if not las_files:
        raise FileNotFoundError(f"no LAS: {os.path.basename(project_dir)}")

    output_folder = os.path.join(las_folder, "Clipped")
    colorized_folder = os.path.join(las_folder, "Colorized")
    for slot in (output_folder, colorized_folder,
                 os.path.join(las_folder, "Classified")):
        os.makedirs(slot, exist_ok=True)

    # Build temp shapefiles once per project
    tmp_dir = tempfile.mkdtemp(prefix="aecon_", dir="E:/temp" if os.path.isdir("E:/temp") else None)
    try:
        panos_shp = os.path.join(tmp_dir, "CAMPTS.shp")
        lasindex_shp = os.path.join(tmp_dir, "LASIDX.shp")
        _create_camera_points_from_csv(eop_csv, panos_shp, crs)
        _create_las_index(las_folder, lasindex_shp, crs)
        buffer_shp = create_camera_buffer(panos_shp, tmp_dir, buffer_distance_m)

        tasks = [(p, eop_csv, panos_shp, lasindex_shp, pano_dir,
                  output_folder, colorized_folder, buffer_shp, search_radius,
                  threads, keep_colorized) for p in las_files]

        # Parallel across LAS files (threads=8 per file)
        from concurrent.futures import ProcessPoolExecutor
        results = []
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(_colorize_clip_one, tasks):
                results.append(r)
    finally:
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass

    ok = sum(1 for _, s in results if s == "ok")
    skip = sum(1 for _, s in results if s == "skip")
    fail = [(f, s) for f, s in results if s.startswith("fail")]
    summary = f"colorize: {os.path.basename(project_dir)} — ok={ok} skip={skip} fail={len(fail)}"
    if fail:
        raise RuntimeError(f"{summary}; failed: {fail[:3]}")
    return summary
