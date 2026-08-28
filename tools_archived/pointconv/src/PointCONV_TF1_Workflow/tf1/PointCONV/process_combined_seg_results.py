import laspy
import numpy as np
import os

import logging

from scipy.spatial import cKDTree

import gzip

from pickle import load

from typing import Optional, Sequence, Union

INT32_MIN = -2_147_483_648
INT32_MAX = 2_147_483_647

def valid_las_points(
        pts: np.ndarray,
        point_format: Union[int, "laspy.PointFormat"],
        file_version: str,
        min_pts: int = 10,
        *,
        offsets: Optional[Sequence[float]] = None,
        scales: Optional[Sequence[float]] = None,
        strict_empty_only: bool = False,
) -> bool:
    """
    Validate whether points can be represented as LAS scaled int32 coordinates.

    - If strict_empty_only is True, only an empty array (N == 0) is considered "no points".
      Otherwise, N < min_pts returns False.
    - If offsets/scales are not provided, offsets default to per-axis mins of pts and
      scales default to 1 cm (0.01) per axis.

    Returns True if all finite points fit the int32 range after (coord - offset) / scale.
    Returns False on shape/dtype issues or any laspy compatibility issue.
    """
    # Basic shape checks
    if pts is None or not isinstance(pts, np.ndarray) or pts.ndim != 2 or pts.shape[1] != 3:
        return False

    n = pts.shape[0]
    if strict_empty_only:
        if n == 0:
            return False
    else:
        if n < min_pts:
            return False

    # Finite check
    if not np.all(np.isfinite(pts)):
        return False

    # Ensure positive, non-zero scales
    if scales is None:
        scales = np.array([0.01, 0.01, 0.01], dtype=np.float64)
    else:
        scales = np.asarray(scales, dtype=np.float64)
    if scales.shape != (3,) or np.any(scales <= 0.0) or not np.all(np.isfinite(scales)):
        return False

    # Offsets
    if offsets is None:
        offsets = np.min(pts, axis=0)
    else:
        offsets = np.asarray(offsets, dtype=np.float64)
    if offsets.shape != (3,) or not np.all(np.isfinite(offsets)):
        return False

    # Vectorized int32 range check
    scaled = (pts - offsets) / scales
    # LAS stores int32; laspy typically rounds to nearest int
    # Check if rounding would still fit the range:
    scaled_rounded = np.rint(scaled)

    # Also ensure that values are close to integer after rounding (optional, but safer)
    # This avoids extreme precision issues where rounding is ambiguous
    if not np.all(np.isfinite(scaled_rounded)):
        return False

    min_ok = scaled_rounded.min(axis=0) >= INT32_MIN
    max_ok = scaled_rounded.max(axis=0) <= INT32_MAX
    if not (min_ok.all() and max_ok.all()):
        return False

    # Optional: Validate version/point_format compatibility using laspy (best-effort)
    try:
        import laspy
        # Construct header just to validate the combination; avoid assigning x/y/z
        _ = laspy.LasHeader(version=file_version, point_format=point_format)
    except Exception:
        # Any laspy error means incompatible combination or unavailable laspy
        return False

    return True


def set_newdim(lasdata, features_list, features_name_list):
    '''
    :param lasdata: laspy data that will get new dimensions
    :param features_list: a list of one or multiple numpy arrays, each array must be the same size of points in las data
    :param features_name_list: a list of names for each array in the feature_list. len(features_list) = len(features_name_list)
    :return: lasdata will be returned and contains new dimensions for each array in feature_list
    '''

    data = [(name, feature_data) for name, feature_data in zip(features_name_list, features_list)]
    new_dimensions = [
        laspy.ExtraBytesParams(name=name, type=feature_data.dtype, description=name)
        for name, feature_data in data
    ]
    lasdata.add_extra_dims(new_dimensions)
    lasdata.update_header()
    for name, array in data:
        setattr(lasdata, name, array)

    return lasdata


def save_las_complete(
        xyz: np.ndarray,
        classification: np.ndarray = None,
        intensity: np.ndarray = None,
        red: np.ndarray = None,
        green: np.ndarray = None,
        blue: np.ndarray = None,
        outLasFname: str = "<output_path>.las",
        point_format=7,
        file_version="1.4",
        features_list=None,
        features_name_list=None,
):
    """
    Validate and save LAS data to outLasFname.

    Parameters:
        xyz: (N, 3) array of float coordinates.
        classification: (N,) array; will be cast to uint8 (0..255).
        intensity: (N,) array; will be cast to uint16 (0..65535).
        red, green, blue: (N,) arrays; will be cast to uint16 (0..65535).
            If any are float with max <= 1.5, they are interpreted as [0,1] and scaled to [0,65535].
        outLasFname: Output LAS filename (directories will be created if needed).
        point_format: LAS point format id or laspy PointFormat.
        file_version: LAS file version string (e.g., "1.2", "1.4").
    """
    # ---------- basic validation ----------
    if not isinstance(xyz, np.ndarray) or xyz.ndim != 2 or xyz.shape[1] != 3:
        raise ValueError("xyz must be a numpy array with shape (N, 3).")
    if not np.all(np.isfinite(xyz)):
        bad = np.argwhere(~np.isfinite(xyz))
        raise ValueError(f"xyz contains non-finite values; example indices: {bad[:5].tolist()}")

    n = xyz.shape[0]

    def _check_len(name, arr):
        if arr is not None and (not isinstance(arr, np.ndarray) or arr.shape[0] != n):
            raise ValueError(f"{name} must be a numpy array of length {n} (got {None if arr is None else arr.shape}).")

    _check_len("classification", classification)
    _check_len("intensity", intensity)
    _check_len("red", red)
    _check_len("green", green)
    _check_len("blue", blue)

    def _ensure_finite(name, arr):
        if arr is None:
            return
        if not np.all(np.isfinite(arr)):
            bad_idx = np.flatnonzero(~np.isfinite(arr))
            raise ValueError(f"{name} contains non-finite values at indices {bad_idx[:10].tolist()}")

    _ensure_finite("classification", classification)
    _ensure_finite("intensity", intensity)
    _ensure_finite("red", red)
    _ensure_finite("green", green)
    _ensure_finite("blue", blue)

    def _to_uint16_channel(name, arr):
        if arr is None:
            return None
        a = np.asarray(arr)
        # If float in [0, 1] (allow a small tolerance), scale to [0, 65535]
        if np.issubdtype(a.dtype, np.floating):
            a_min, a_max = float(np.nanmin(a)), float(np.nanmax(a))
            if a_max <= 1.5:  # treat as normalized
                a = np.clip(a, 0.0, 1.0) * 65535.0
            # after scaling (or not), clip to valid range
            a = np.clip(a, 0.0, 65535.0).astype(np.uint16, copy=False)
        else:
            a = np.clip(a, 0, 65535).astype(np.uint16, copy=False)
        return a

    def _to_uint16_intensity(arr):
        if arr is None:
            return None
        a = np.asarray(arr)
        if np.issubdtype(a.dtype, np.floating):
            a_min, a_max = float(np.nanmin(a)), float(np.nanmax(a))
            if a_max <= 1.5:
                a = np.clip(a, 0.0, 1.0) * 65535.0
            a = np.clip(a, 0.0, 65535.0).astype(np.uint16, copy=False)
        else:
            a = np.clip(a, 0, 65535).astype(np.uint16, copy=False)
        return a

    def _to_uint8_classification(arr):
        if arr is None:
            return None
        a = np.asarray(arr)
        if np.issubdtype(a.dtype, np.floating):
            a = np.clip(a, 0.0, 255.0).astype(np.uint8, copy=False)
        else:
            a = np.clip(a, 0, 255).astype(np.uint8, copy=False)
        return a

    # ---------- cast/normalize optional fields ----------
    classification_u8 = _to_uint8_classification(classification)
    intensity_u16 = _to_uint16_intensity(intensity)
    red_u16 = _to_uint16_channel("red", red)
    green_u16 = _to_uint16_channel("green", green)
    blue_u16 = _to_uint16_channel("blue", blue)

    # ---------- write LAS ----------
    os.makedirs(os.path.dirname(outLasFname) or ".", exist_ok=True)

    las = laspy.create(point_format=point_format, file_version=str(file_version))
    # las.xyz = xyz.astype(np.float64, copy=False)

    # Use component-wise assignment for compatibility across laspy versions
    las.x = xyz[:, 0].astype(np.float64, copy=False)
    las.y = xyz[:, 1].astype(np.float64, copy=False)
    las.z = xyz[:, 2].astype(np.float64, copy=False)
    # laspy expects float coords; header scale/offset handle storage

    if classification_u8 is not None:
        las.classification = classification_u8
    if intensity_u16 is not None:
        las.intensity = intensity_u16
    if red_u16 is not None:
        las.red = red_u16
    if green_u16 is not None:
        las.green = green_u16
    if blue_u16 is not None:
        las.blue = blue_u16

    if features_list is not None:
        las = set_newdim(las, features_list, features_name_list)

    # Use context manager to ensure proper file closure
    with laspy.open(outLasFname, mode="w", header=las.header) as out_las:
        out_las.write_points(las.points)


def process_combined_seg_results(point_conv_conf,
                                 voxel_info_filename,
                                 point_format,
                                 file_version,
                                 ):
    with gzip.open(voxel_info_filename, 'rb') as f:
        voxel_info = load(f)

    thin_las_filename = voxel_info['thin_las_filename']
    vox_las_filename = voxel_info['vox_las_filename']

    curr_filename_seg_combined_seg_out = vox_las_filename[:-4] + '_seg_out.las'

    curr_filename_seg_combined = thin_las_filename[:-4] + '_raw.las'

    npz_point_cloud_prob_mapping_filename = thin_las_filename[:-4] + '_prob_mapping.npz'

    if (os.path.exists(npz_point_cloud_prob_mapping_filename) and
            os.path.getsize(npz_point_cloud_prob_mapping_filename) > 0 and
            os.path.isfile(npz_point_cloud_prob_mapping_filename)):
        return

    single_file_name = os.path.basename(vox_las_filename)[:-4]

    out_dir = os.path.dirname(vox_las_filename)

    filename_output_final = os.path.join(out_dir, single_file_name + '_prob.npz')

    segmentation_results = np.load(filename_output_final)

    labels_unique = segmentation_results['labels_unique']
    # dim>3 models (ablation C) carry extra feature channels in the saved
    # array; everything downstream here (LAS write, cKDTree voxel->_t.las
    # mapping) needs pure 3-D coordinates. Identity slice for dim=3.
    xyz_seg = segmentation_results['xyz'][:, :3]
    point_vote_prob = segmentation_results['point_vote_prob']
    color_seg = segmentation_results['color']

    logging.info("Extracting initial class probability")
    if point_conv_conf['model_directory'] == 'PointCONV_model_v1.0.0':
        class_label = []
        prob = []
        for class_lab in point_conv_conf['class_extract_config']['combined_classes']:
            if class_lab == 'class_distribution_pole':
                continue
            class_num = point_conv_conf['class_extract_config'][class_lab]
            prob_obj = None
            for label_cnt in range(labels_unique.shape[0]):
                if labels_unique[label_cnt] in class_num:
                    if prob_obj is None:
                        prob_obj = point_vote_prob[:, label_cnt]
                    else:
                        prob_obj = np.maximum(prob_obj, point_vote_prob[:, label_cnt])
            class_label.append(class_num[0])
            if prob_obj is not None:
                prob.append(prob_obj)
            else:
                prob.append(np.zeros(len(point_vote_prob), dtype=float))

        class_label = np.array(class_label)

        prob = np.array(prob).T
        ind_max_prob = np.argmax(prob, axis=1)

    else:
        prob = point_vote_prob
        class_label = np.array(point_conv_conf['class_mapping_model']['class_label'])
        ind_max_prob = np.argmax(prob, axis=1)

    prob_pointCONV_output = np.copy(prob)
    xyz_seg_pointCONV_output = np.copy(xyz_seg)
    prob_max_pointCONV_output = np.max(prob_pointCONV_output, axis=1)

    class_pointCONV_output = class_label[ind_max_prob]

    valid_pts = valid_las_points(xyz_seg_pointCONV_output, point_format, str(file_version))
    if valid_pts:
        red = (color_seg[:, 2] * 65535).astype(np.uint16)
        green = (color_seg[:, 1] * 65535).astype(np.uint16)
        blue = (color_seg[:, 0] * 65535).astype(np.uint16)
        intensity = (prob_max_pointCONV_output * 65535).astype(np.uint16)
        classification = class_pointCONV_output.astype(np.uint8)

        save_las_complete(xyz_seg_pointCONV_output,
                          classification=classification,
                          intensity=intensity,
                          red=red, green=green, blue=blue,
                          outLasFname=curr_filename_seg_combined_seg_out,
                          point_format=point_format,
                          file_version=str(file_version))
    else:
        logging.warning("No valid points in the final segmentation result")
        return

    prob_update = np.copy(prob)
    xyz_seg_update = np.copy(xyz_seg)

    las = laspy.read(thin_las_filename)
    xyz_orig = np.array(las.xyz)

    colors_orig = np.zeros((xyz_orig.shape[0], 3), dtype=np.float32)
    if hasattr(las, 'blue'):
        colors_orig[:, 0] = las.blue / 65535
        colors_orig[:, 1] = las.green / 65535
        colors_orig[:, 2] = las.red / 65535

    if point_conv_conf['map_to_original_points']:
        tree = cKDTree(xyz_seg_update)

        # r = float(point_conv_conf['voxel_size_baseline']) / 2.0 + 1e-12
        r = float(point_conv_conf['voxel_size_baseline'])

        distances, indices = tree.query(xyz_orig, distance_upper_bound=r)

        nearby_indices = np.where(distances != np.inf)[0]

        nearby_indices = np.unique(nearby_indices)

        associated_B_index = indices[nearby_indices]

        # associated_B_index = np.unique(associated_B_index)
        xyz_seg_update = xyz_orig[nearby_indices]
        colors_seg_update = colors_orig[nearby_indices]

        prob_update = prob[associated_B_index, :]
        ind_max_prob = ind_max_prob[associated_B_index]
    else:
        tree = cKDTree(xyz_orig)
        _, idx = tree.query(xyz_seg_update)
        colors_seg_update = colors_orig[idx]

    classification_raw = class_label[ind_max_prob]

    extracted_prob_mapping = {
        'ind_max_prob': ind_max_prob,

        'class_label': class_label,

        'prob_update': prob_update,
        'xyz_seg_update': xyz_seg_update,
        'colors_seg_update': colors_seg_update,
        'classification_raw': classification_raw,
    }

    valid_pts = valid_las_points(xyz_seg_update, point_format, str(file_version))
    if valid_pts:
        prob_update_max_tmp = np.max(prob_update, axis=1)

        red = (colors_seg_update[:, 2] * 65535).astype(np.uint16)
        green = (colors_seg_update[:, 1] * 65535).astype(np.uint16)
        blue = (colors_seg_update[:, 0] * 65535).astype(np.uint16)
        intensity = (prob_update_max_tmp * 65535).astype(np.uint16)
        classification = classification_raw.astype(np.uint8)

        save_las_complete(xyz_seg_update,
                          classification=classification,
                          intensity=intensity,
                          red=red, green=green, blue=blue,
                          outLasFname=curr_filename_seg_combined,
                          point_format=point_format,
                          file_version=str(file_version))
    else:
        logging.warning("No valid points in the final segmentation result")
        return

    np.savez_compressed(npz_point_cloud_prob_mapping_filename, **extracted_prob_mapping)
    logging.info("Completed - Extracting initial class probability: " + npz_point_cloud_prob_mapping_filename)

    return
