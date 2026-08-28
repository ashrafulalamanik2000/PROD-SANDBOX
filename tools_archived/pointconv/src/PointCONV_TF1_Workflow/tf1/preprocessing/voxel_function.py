import os
import numpy as np

import logging

import yaml

import laspy
import open3d as o3d

from scipy.spatial import cKDTree

import gzip

import pickle

import gc


def convert_numpy_to_native(obj):
    if isinstance(obj, dict):
        # If it's a dictionary, apply the conversion to its values recursively
        return {k: convert_numpy_to_native(v) for k, v in obj.items()}
    elif isinstance(obj, np.ndarray):
        # Convert numpy arrays to lists
        return obj.tolist()
    elif isinstance(obj, (np.generic, np.int_, np.float_)):
        # Convert numpy scalars to Python native types
        return obj.item()
    else:
        # Return the object unchanged if it's not a NumPy type
        return obj


def process_las_file(
        lasfile,
        preprocessing_config,
        input_folder,
        output_folder,
):
    def numpy_to_point_cloud(array):
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(array)
        return pcd

    def closest_point(xyz, xyz_vox):
        tree = cKDTree(xyz)
        _, idx = tree.query(xyz_vox)
        return idx

    if 'add_jitter' in preprocessing_config:
        add_jitter = preprocessing_config['add_jitter']
    else:
        add_jitter = None

    point_format = preprocessing_config['OutputLAS_PointFormat']
    file_version = str(preprocessing_config['OutputLAS_FileFormat'])
    thin_data = preprocessing_config['thin_data']
    voxel_size = preprocessing_config['voxel_size']

    min_num_pts = preprocessing_config['min_num_pts_voxel']

    file_name_base = os.path.basename(lasfile)

    results_dir_base = file_name_base[:-4]

    results_dir = os.path.join(output_folder, results_dir_base)

    os.makedirs(results_dir, exist_ok=True)

    thin_las_filename = os.path.join(results_dir, results_dir_base + '_t.las')
    vox_las_filename = os.path.join(results_dir, results_dir_base + '_v.las')

    voxel_info_filename = os.path.join(results_dir, "voxel_info.pklz")

    if os.path.exists(thin_las_filename) and os.path.isfile(vox_las_filename) and os.path.isfile(voxel_info_filename):
        return

    las = laspy.read(lasfile)
    xyz_original = np.array(las.xyz)
    xyz = np.copy(xyz_original)

    if preprocessing_config['convert_feet_to_meters']:
        xyz = xyz * 0.3048

    if hasattr(las, 'blue'):
        colors = np.zeros((xyz.shape[0], 3), dtype=np.float32)
        colors[:, 0] = las.blue / 65535
        colors[:, 1] = las.green / 65535
        colors[:, 2] = las.red / 65535
    else:
        colors = np.zeros((xyz.shape[0], 3), dtype=np.float32)

    if hasattr(las, 'classification'):
        is_las_1_2 = las.header.version.major == 1 and las.header.version.minor == 2
        if is_las_1_2:
            logging.info(f"{file_name_base} is in LAS 1.2!")
            raw_classification = las.classification.copy()

            # Add back the flag bits
            if hasattr(las, 'synthetic'):
                synthetic = np.array(las.synthetic, dtype=np.uint8)
                raw_classification = raw_classification | (synthetic << 5)
            if hasattr(las, 'key_point'):
                key_point = np.array(las.key_point, dtype=np.uint8)
                raw_classification = raw_classification | (key_point << 6)
            if hasattr(las, 'withheld'):
                withheld = np.array(las.withheld, dtype=np.uint8)
                raw_classification = raw_classification | (withheld << 7)

            classes = np.array(raw_classification)
        else:
            classes = np.array(las.classification)

        unique_classes, class_counts = np.unique(classes, return_counts=True)

        class_info = {
            'unique_classes': unique_classes,
            'class_counts': class_counts,
        }

        # Specify the file name for saving the YAML
        output_file = os.path.join(results_dir, 'class_info.npz')
        np.savez(output_file, **class_info)

        python_int_list_c = [int(num) for num in unique_classes]
        python_int_list_cnt = [int(num) for num in class_counts]

        class_info = {
            'unique_classes': python_int_list_c,
            'class_counts': python_int_list_cnt,
        }

        # Specify the file name for saving the YAML
        output_file = os.path.join(results_dir, 'class_info.yml')

        cleaned_data = convert_numpy_to_native(class_info)

        # Save the data into a YAML file
        with open(output_file, "w") as file:
            yaml.dump(cleaned_data, file, default_flow_style=False)  # Pretty formatting

    else:
        classes = np.zeros((xyz.shape[0],), dtype=int)

    if add_jitter is not None:
        if add_jitter['resample_surface'] is not None:
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(xyz)
            pcd.estimate_normals(
                search_param=o3d.geometry.KDTreeSearchParamKNN(knn=add_jitter['resample_surface']['knn']))

            mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
                pcd, depth=add_jitter['resample_surface']['depth']
            )

            upsampled_pcd = mesh.sample_points_uniformly(number_of_points=2 * xyz.shape[0])
            xyz_add = np.asarray(upsampled_pcd.points)

            idx = closest_point(xyz, xyz_add)

            colors = colors[idx]
            classes = classes[idx]

            xyz = xyz_add
        else:
            xyz_jit = np.random.normal(loc=0, scale=add_jitter['sigma'], size=xyz.shape)
            xyz_jit = np.clip(xyz_jit, -add_jitter['max_jitter'], add_jitter['max_jitter'])
            xyz_add = np.copy(xyz) + xyz_jit
            xyz = np.concatenate((xyz, xyz_add), axis=0)
            colors = np.concatenate((colors, colors), axis=0)
            classes = np.concatenate((classes, classes), axis=0)

    ### saved thinned filen
    if thin_data is not None:
        pcd = numpy_to_point_cloud(xyz)
        downpcd = pcd.voxel_down_sample(voxel_size=float(thin_data))

        xyz_vox = np.asarray(downpcd.points)

        idx_voxel = closest_point(xyz, xyz_vox)

        idx_voxel = np.unique(idx_voxel)

        xyz = xyz[idx_voxel]
        colors = colors[idx_voxel]
        classes = classes[idx_voxel]

    las_data = laspy.create(point_format=point_format, file_version=str(file_version))
    las_data.x = xyz[:, 0]
    las_data.y = xyz[:, 1]
    las_data.z = xyz[:, 2]
    las_data.blue = colors[:, 0] * 65535
    las_data.green = colors[:, 1] * 65535
    las_data.red = colors[:, 2] * 65535
    las_data.classification = classes
    with laspy.open(thin_las_filename, mode="w", header=las_data.header) as out_las:
        out_las.write_points(las_data.points)

    if voxel_size > 0.0 and xyz.shape[0] > 2 * min_num_pts:
        pcd = numpy_to_point_cloud(xyz)
        downpcd = pcd.voxel_down_sample(voxel_size=voxel_size)

        xyz_vox = np.asarray(downpcd.points)

        idx_voxel = closest_point(xyz, xyz_vox)

        idx_voxel = np.unique(idx_voxel)

        xyz = xyz[idx_voxel]
        colors = colors[idx_voxel]
        classes = classes[idx_voxel]

    if xyz.shape[0] < 2 * min_num_pts:
        num_pts_add = 2 * min_num_pts - xyz.shape[0]

        ind_resample = np.random.choice(xyz.shape[0], num_pts_add, replace=True)

        xyz_add = np.copy(xyz[ind_resample])

        if add_jitter is None:
            add_jitter = {'sigma': 0.05, 'max_jitter': 0.05}

        xyz_jit = np.random.normal(loc=0, scale=add_jitter['sigma'], size=xyz_add.shape)
        xyz_jit = np.clip(xyz_jit, -add_jitter['max_jitter'], add_jitter['max_jitter'])
        xyz_add = xyz_add + xyz_jit

        xyz = np.concatenate((xyz, xyz_add), axis=0)
        colors = np.concatenate((colors, colors[ind_resample]), axis=0)
        classes = np.concatenate((classes, classes[ind_resample]), axis=0)

    las_data = laspy.create(point_format=point_format, file_version=str(file_version))
    las_data.x = xyz[:, 0]
    las_data.y = xyz[:, 1]
    las_data.z = xyz[:, 2]
    las_data.blue = colors[:, 0] * 65535
    las_data.green = colors[:, 1] * 65535
    las_data.red = colors[:, 2] * 65535
    las_data.classification = classes
    with laspy.open(vox_las_filename, mode="w", header=las_data.header) as out_las:
        out_las.write_points(las_data.points)

    voxel_info = {
        'lasfile': lasfile,
        'thin_las_filename': thin_las_filename,
        'vox_las_filename': vox_las_filename,
        'voxel_info_filename': voxel_info_filename,
        'output_folder': output_folder,
        'input_folder': input_folder,
        'results_dir': results_dir,
    }

    with gzip.open(voxel_info_filename, 'wb') as f:
        pickle.dump(voxel_info, f)

    gc.collect()

    return voxel_info_filename


def process_las_file_wrapper(
        lasfile,
        preprocessing_config,
        input_folder,
        output_folder,
):
    return process_las_file(
        lasfile,
        preprocessing_config,
        input_folder,
        output_folder,
    )
