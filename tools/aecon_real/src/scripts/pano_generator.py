"""
pano_generator.py

Pure-Python, Linux-compatible replacement for Solv3D engine.exe
for the "cubemap_to_panoramic" operation. Drop-in CLI-compatible:

    python pano_generator.py -p cubemap_to_panoramic \
        --input_folder <cubemap_dir> \
        --output_folder <pano_dir> \
        --rotation_type {leica,encompass} \
        --export_type {jpg,png}

Removes all Solv3D speutils dependencies:
  * speutils.license.check          -> no-op
  * speutils.misc.check_values      -> inline validation
  * speutils.csv_tools / opk_to_rpy -> dropped (downstream colorize reads the
                                       Stage 3 Run_N_metadata.csv, not the
                                       engine-generated -encompass.csv).
  * speutils.multiprocess.Pool      -> concurrent.futures.ProcessPoolExecutor.

All cubemap->equirectangular math is unchanged from Solv3D's
cubemap_to_panoramic.py so output is pixel-equivalent.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import Manager
from pathlib import Path

import cv2
import numpy
from numpy import pi, sin, cos, tan
from PIL import Image

SAVE_QUALITY = 85

SIDE_MAPPING = {
    "Left": 0, "Front": 1, "Right": 2, "Back": 3, "Top": 4, "Bottom": 5,
}


# ─── Projection math (unchanged from Solv3D source) ──────────────────
def cot(angle):
    zero_value = numpy.where(angle == 0)[0]
    angle[zero_value] = 1e-8
    return 1.0 / tan(angle)


def flat_array(n, value):
    return numpy.ones(n) * value


def projection(theta, phi):
    pi_4 = pi / 4
    results = numpy.zeros((theta.shape[0], 4)) - 1

    idx = numpy.where(theta < 0.615)[0]
    results[idx] = project_top(theta[idx], phi[idx])

    idx = numpy.where(theta > 2.527)[0]
    results[idx] = project_bottom(theta[idx], phi[idx])

    idx = numpy.where((phi <= pi_4) | (phi >= 7 * pi_4))[0]
    results[idx] = project_left(theta[idx], phi[idx])

    idx = numpy.where((phi > pi_4) & (phi <= 3 * pi_4))[0]
    results[idx] = project_front(theta[idx], phi[idx])

    idx = numpy.where((phi > 3 * pi_4) & (phi < 5 * pi_4))[0]
    results[idx] = project_right(theta[idx], phi[idx])

    idx = numpy.where((phi >= 5 * pi_4) & (phi < 7 * pi_4))[0]
    results[idx] = project_back(theta[idx], phi[idx])

    return results


def project_left(theta, phi):
    x = flat_array(theta.shape[0], 1.0)
    y = tan(phi)
    z = cot(theta) / cos(phi)
    s = flat_array(theta.shape[0], SIDE_MAPPING["Left"])
    results = numpy.vstack((s, x, y, z)).T
    return map_top_and_bottom(results, theta, phi, z)


def project_front(theta, phi):
    x = tan(phi - pi / 2)
    y = flat_array(theta.shape[0], 1.0)
    z = cot(theta) / cos(phi - pi / 2)
    s = flat_array(theta.shape[0], SIDE_MAPPING["Front"])
    results = numpy.vstack((s, x, y, z)).T
    return map_top_and_bottom(results, theta, phi, z)


def project_right(theta, phi):
    x = flat_array(theta.shape[0], -1.0)
    y = tan(phi)
    z = -cot(theta) / cos(phi)
    s = flat_array(theta.shape[0], SIDE_MAPPING["Right"])
    results = numpy.vstack((s, x, -y, z)).T
    return map_top_and_bottom(results, theta, phi, z)


def project_back(theta, phi):
    x = tan(phi - (3 * pi / 2))
    y = flat_array(theta.shape[0], -1.0)
    z = cot(theta) / cos(phi - (3 * pi / 2))
    s = flat_array(theta.shape[0], SIDE_MAPPING["Back"])
    results = numpy.vstack((s, -x, y, z)).T
    return map_top_and_bottom(results, theta, phi, z)


def project_top(theta, phi):
    x = tan(theta) * cos(phi)
    y = tan(theta) * sin(phi)
    s = flat_array(theta.shape[0], SIDE_MAPPING["Top"])
    z = flat_array(theta.shape[0], 1.0)
    return numpy.vstack((s, x, y, z)).T


def project_bottom(theta, phi):
    x = -tan(theta) * cos(phi)
    y = -tan(theta) * sin(phi)
    s = flat_array(theta.shape[0], SIDE_MAPPING["Bottom"])
    z = flat_array(theta.shape[0], -1.0)
    return numpy.vstack((s, x, y, z)).T


def map_top_and_bottom(results, theta, phi, z):
    idx = numpy.where(z < -1)[0]
    results[idx] = project_bottom(theta[idx], phi[idx])
    idx = numpy.where(z > 1)[0]
    results[idx] = project_top(theta[idx], phi[idx])
    return results


def cube_to_image(coords, edge):
    sides = coords[:, 0]
    x = numpy.zeros(coords.shape[0])
    y = numpy.zeros(coords.shape[0])

    idx = numpy.where(sides == SIDE_MAPPING["Left"])[0]
    x[idx] = edge * (coords[idx, 2] + 1) / 2
    y[idx] = edge * (3 - coords[idx, 3]) / 2

    idx = numpy.where(sides == SIDE_MAPPING["Front"])[0]
    x[idx] = edge * (coords[idx, 1] + 3) / 2
    y[idx] = edge * (3 - coords[idx, 3]) / 2

    idx = numpy.where(sides == SIDE_MAPPING["Right"])[0]
    x[idx] = edge * (5 - coords[idx, 2]) / 2
    y[idx] = edge * (3 - coords[idx, 3]) / 2

    idx = numpy.where(sides == SIDE_MAPPING["Back"])[0]
    x[idx] = edge * (7 - coords[idx, 1]) / 2
    y[idx] = edge * (3 - coords[idx, 3]) / 2

    idx = numpy.where(sides == SIDE_MAPPING["Top"])[0]
    x[idx] = edge * (3 - coords[idx, 1]) / 2
    y[idx] = edge * (1 + coords[idx, 2]) / 2

    idx = numpy.where(sides == SIDE_MAPPING["Bottom"])[0]
    x[idx] = edge * (3 - coords[idx, 1]) / 2
    y[idx] = edge * (5 - coords[idx, 2]) / 2

    return x, y


def add_image_borders(cube_arr, edge, num_pixels=2):
    sz = int(edge)
    sz2 = sz * 2
    for i in range(num_pixels):
        offset = i + 1
        cube_arr[0:sz, sz - offset] = cube_arr[0:sz, sz]
        cube_arr[sz2:, sz - offset] = cube_arr[sz2:, sz]
        cube_arr[sz - offset, sz2:] = cube_arr[sz, sz2:]
        cube_arr[sz - offset, 0:sz] = cube_arr[sz, 0:sz]
        offset = i
        cube_arr[0:sz, sz2 + offset] = cube_arr[0:sz, sz2 - 1]
        cube_arr[sz2:, sz2 + offset] = cube_arr[sz2:, sz2 - 1]
        cube_arr[sz2 + offset, 0:sz] = cube_arr[sz2 - 1, 0:sz]
        cube_arr[sz2 + offset, sz2:] = cube_arr[sz2 - 1, sz2:]
    return cube_arr


def rotate_90_degrees(image, edge):
    right_side = image[-edge:, :].copy()
    left_side = image[:-edge, :].copy()
    image[:edge, :] = right_side
    image[edge:, :] = left_side
    return image


def transpose_and_flip(image):
    image = numpy.rot90(image)
    return numpy.flip(image, axis=0)


def generate_memmap_data(image_width):
    sz_full = int(image_width)
    sz_half = int(image_width / 2)
    out_size = (sz_full, sz_half)
    edge = int(image_width / 4)

    oldpix = numpy.mgrid[:sz_half, :sz_full].T
    oldpix = oldpix.reshape(-1, 2)

    phi = oldpix[:, 1] * 2 * pi / out_size[0]
    theta = oldpix[:, 0] * pi / out_size[1]

    coords = projection(theta, phi)
    x, y = cube_to_image(coords, edge)
    map_x_32 = x.astype("f4").reshape(out_size)
    map_y_32 = y.astype("f4").reshape(out_size)

    map_x_32 = rotate_90_degrees(map_x_32, edge)
    map_y_32 = rotate_90_degrees(map_y_32, edge)
    map_x_32 = transpose_and_flip(map_x_32)
    map_y_32 = transpose_and_flip(map_y_32)
    return map_x_32, map_y_32


def open_memmap_file(file_path, width, mode="r"):
    shape = (int(width / 2), width, 2)
    return numpy.memmap(file_path, dtype="float32", mode=mode, shape=shape)


# ─── Per-image worker (unchanged math, stdlib lock) ──────────────────
def save_to_panoramic(conf):
    (panoramic_output_folder, cubemap_base_path, image_width,
     export_type, mmap_path, rotation_type, suffix, lock) = conf

    mmap_mtrx = open_memmap_file(mmap_path, image_width)
    map_x_32 = mmap_mtrx[:, :, 0]
    map_y_32 = mmap_mtrx[:, :, 1]

    basename = Path(cubemap_base_path).stem

    try:
        img_0 = Image.open(f"{cubemap_base_path}_0{suffix}")
        img_1 = Image.open(f"{cubemap_base_path}_1{suffix}")
        img_2 = Image.open(f"{cubemap_base_path}_2{suffix}")
        img_3 = Image.open(f"{cubemap_base_path}_3{suffix}")
        img_4 = Image.open(f"{cubemap_base_path}_4{suffix}")
        img_5 = Image.open(f"{cubemap_base_path}_5{suffix}")
    except Exception:
        logging.info(f"Error reading file: {basename}")
        return None

    cube_size = img_0.size[0]
    output_image = numpy.zeros((cube_size * 3, cube_size * 4, 3), dtype="u1")

    if rotation_type == "leica":
        output_image[:cube_size, cube_size:cube_size * 2] = numpy.array(img_4.rotate(90))
        output_image[cube_size:cube_size * 2, cube_size * 3:] = numpy.array(img_1)
        output_image[cube_size:cube_size * 2, :cube_size] = numpy.array(img_2)
        output_image[cube_size:cube_size * 2, cube_size:cube_size * 2] = numpy.array(img_3)
        output_image[cube_size:cube_size * 2, cube_size * 2:cube_size * 3] = numpy.array(img_0)
        output_image[cube_size * 2:, cube_size:cube_size * 2] = numpy.array(img_5.rotate(-90))
    else:
        output_image[:cube_size, cube_size:cube_size * 2] = numpy.array(img_0.rotate(90))
        output_image[cube_size:cube_size * 2, cube_size * 3:] = numpy.array(img_1.rotate(-90))
        output_image[cube_size:cube_size * 2, :cube_size] = numpy.array(img_2.rotate(180))
        output_image[cube_size:cube_size * 2, cube_size:cube_size * 2] = numpy.array(img_3.rotate(90))
        output_image[cube_size:cube_size * 2, cube_size * 2:cube_size * 3] = numpy.array(img_4)
        output_image[cube_size * 2:, cube_size:cube_size * 2] = numpy.array(img_5.rotate(90))

    edge = int(image_width / 4.0)
    output_image = add_image_borders(output_image, edge)

    if lock is not None:
        with lock:
            panoramic = cv2.remap(
                output_image, map_x_32, map_y_32,
                interpolation=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
    else:
        panoramic = cv2.remap(
            output_image, map_x_32, map_y_32,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )

    save_name = f"{panoramic_output_folder}/{basename}.{export_type}"
    Image.fromarray(panoramic).save(save_name, quality=SAVE_QUALITY)
    del mmap_mtrx
    return basename


# ─── Orchestrator ────────────────────────────────────────────────────
def run_cubemap_to_panoramic(input_folder, output_folder, export_type, rotation_type, workers=None, use_lock=False):
    assert export_type in {"png", "jpg"}, "export_type must be jpg or png"
    assert rotation_type in {"leica", "encompass"}, "rotation_type must be leica or encompass"

    os.makedirs(output_folder, exist_ok=True)
    input_path = Path(input_folder)

    cubemap_parts = [item for item in input_path.iterdir() if item.suffix.lower() in {".jpg", ".jpeg"}]
    if not cubemap_parts:
        logging.info("No .jpg cubemap frames found.")
        return
    groups = {}
    for item in cubemap_parts:
        if len(item.stem) > 2:
            base = item.with_name(item.stem[:-2])
            groups.setdefault((base, item.suffix.lower()), set()).add(item.stem[-1])
    base_suffix = {base: suffix for (base, suffix), faces in groups.items() if faces == set("012345")}
    if not base_suffix:
        raise RuntimeError("No complete _0.._5 cubemap found")
    unique_cubemaps = sorted(
        base for base in base_suffix
        if not (Path(output_folder) / f"{base.name}.{export_type}").is_file()
    )
    if not unique_cubemaps:
        logging.info("All panoramas already exist; nothing to do.")
        return

    image_width = int(Image.open(cubemap_parts[0]).size[0] * 4)

    logging.info("Generating mapping matrix...")
    map_x_32, map_y_32 = generate_memmap_data(image_width)

    temp_fp = tempfile.NamedTemporaryFile(delete=False)
    mmap_path = temp_fp.name
    mmap_mtrx = open_memmap_file(mmap_path, image_width, mode="w+")
    mmap_mtrx[:, :, 0] = map_x_32
    mmap_mtrx[:, :, 1] = map_y_32
    mmap_mtrx.flush()

    total = len(unique_cubemaps)
    logging.info(f"Starting task: Cubemap to Panoramic ({total} frames)")

    max_workers = workers if workers else min(os.cpu_count() or 4, 8)

    failed = 0
    if use_lock:
        with Manager() as manager:
            lock = manager.Lock()
            arguments = [
                (output_folder, str(base), image_width, export_type, mmap_path, rotation_type, base_suffix[base], lock)
                for base in unique_cubemaps
            ]
            done = 0
            with ProcessPoolExecutor(max_workers=max_workers) as pool:
                for result in pool.map(save_to_panoramic, arguments, chunksize=1):
                    failed += result is None
                    done += 1
                    if done % max(1, total // 100) == 0 or done == total:
                        logging.info(f"Cubemap to Panoramic: {100.0 * done / total:.2f}% Complete")
    else:
        arguments = [
            (output_folder, str(base), image_width, export_type, mmap_path, rotation_type, base_suffix[base], None)
            for base in unique_cubemaps
        ]
        done = 0
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            for result in pool.map(save_to_panoramic, arguments, chunksize=1):
                failed += result is None
                done += 1
                if done % max(1, total // 100) == 0 or done == total:
                    logging.info(f"Cubemap to Panoramic: {100.0 * done / total:.2f}% Complete")

    mmap_mtrx._mmap.close()
    del mmap_mtrx
    temp_fp.close()
    os.unlink(mmap_path)

    if failed:
        raise RuntimeError(f"{failed} cubemap(s) failed")

    logging.info("--- Finished ---")


# ─── CLI compatible with Solv3D engine.exe ───────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Cubemap -> equirectangular panorama generator (Solv3D engine.exe drop-in).")
    parser.add_argument("-p", "--process", default="cubemap_to_panoramic",
                        help="Operation name (only cubemap_to_panoramic supported).")
    parser.add_argument("--input_folder", required=True)
    parser.add_argument("--output_folder", required=True)
    parser.add_argument("--export_type", default="jpg", choices=["jpg", "png"])
    parser.add_argument("--rotation_type", default="leica", choices=["leica", "encompass"])
    parser.add_argument("--workers", type=int,
                        default=int(os.environ.get("AECON_PANO_WORKERS") or 0) or None,
                        help="Parallel pano workers (default: AECON_PANO_WORKERS env or min(cpu, 8)).")
    parser.add_argument("--lock-remap", action="store_true",
                        help="Serialize cv2.remap with a lock (Solv3D default). "
                             "Default OFF for speed; enable on low-RAM machines.")
    args = parser.parse_args()

    if args.process != "cubemap_to_panoramic":
        print(f"Unsupported process: {args.process}", file=sys.stderr)
        sys.exit(2)

    logging.basicConfig(
        format="INFO  : %(asctime)s | %(message)s",
        datefmt="%H:%M:%S",
        level=logging.INFO,
    )

    run_cubemap_to_panoramic(
        args.input_folder, args.output_folder,
        args.export_type, args.rotation_type, args.workers,
        use_lock=args.lock_remap,
    )


if __name__ == "__main__":
    main()
