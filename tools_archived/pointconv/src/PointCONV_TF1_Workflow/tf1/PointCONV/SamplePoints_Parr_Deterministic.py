import os.path

import numpy as np

import os

import laspy

import logging

from tqdm import tqdm

from .sample_pts import divide_and_conquer_sample_groups


def subtract_median(xyz):
    median_xyz = np.median(xyz, axis=0)
    return xyz - median_xyz


def SamplePoints_Parr_Deterministic(las_file,
                                    point_conv_conf,
                                    nn_points_all=1024,
                                    min_samples_per_point=1,
                                    classifications_keep=None,
                                    above_ground_minimum=None,
                                    minimum_pts_in_xyz=16384,
                                    random_seed=42,
                                    Radius_NN=11.0,
                                    Ignore_Classes_list=[],
                                    point_format=7, file_version='1.4',
                                    learning_data_base_dir=None,
                                    use_geometry_features=False):
    print(' ')
    print('----------------------')
    print('Starting file: ' + las_file)
    print(' ')

    if random_seed is None:
        random_seed = 42
    np.random.seed(seed=random_seed)

    las = laspy.read(las_file)

    xyz_orig = np.array(las.xyz)

    if len(xyz_orig) < nn_points_all:
        return None

    if not hasattr(las, 'xyz'):
        print('ERROR in las file: File has no xyz: ' + las_file)
        return

    if classifications_keep is not None:
        l_ind_predict = np.zeros((xyz_orig.shape[0],), dtype=bool)
        for c_cnt in range(len(classifications_keep)):
            ind_c = np.where(las.classification == classifications_keep[c_cnt])[0]
            if len(ind_c) > 0:
                l_ind_predict[ind_c] = True

    else:
        l_ind_predict = np.ones((xyz_orig.shape[0],), dtype=bool)

    if above_ground_minimum is not None:
        ind_below_min_HAG = np.where(las.HAG < above_ground_minimum)[0]
        l_ind_predict[ind_below_min_HAG] = False

    if len(Ignore_Classes_list) > 0:
        for c_cnt in range(len(Ignore_Classes_list)):
            ind_ignore = np.where(las.classification == Ignore_Classes_list[c_cnt])[0]
            if len(ind_ignore) > 0:
                l_ind_predict[ind_ignore] = False

    color_orig = np.zeros_like(xyz_orig)
    color_orig[:, 2] = np.copy(las.red)
    color_orig[:, 1] = np.copy(las.green)
    color_orig[:, 0] = np.copy(las.blue)

    color_orig = color_orig / 65535

    xyz_orig = xyz_orig[l_ind_predict]
    color_orig = color_orig[l_ind_predict]
    xyz = np.float32(subtract_median(xyz_orig))
    xyz_class = np.copy(las.classification[l_ind_predict])

    if use_geometry_features:
        # Ablation C (dim>3 models): append hag/linearity/verticality computed
        # on the absolute filtered cloud — identical semantics to the training
        # prep (geometry_features is shared). Dataset.get_point_data centers
        # only the first 3 channels, so these flow through uncentered.
        from .geometry_features import compute_features
        feats = compute_features(xyz_orig)
        xyz_orig = np.column_stack([xyz_orig, feats.astype(np.float64)])

    if xyz.shape[0] < 0.25 * nn_points_all:
        return

    if xyz.shape[0] < nn_points_all:
        if xyz.shape[0] < minimum_pts_in_xyz:
            print('Error: Not enough points in file - ' + str(xyz.shape[0]))
            # return

        num_times_repeat_repeat_pts = np.ceil(nn_points_all / xyz.shape[0])
        for _ in range(num_times_repeat_repeat_pts):
            xyz = np.concatenate((xyz, xyz), axis=0)

    xy = xyz[:, :2]

    point_cloud_sample_ind, pnt_sample_number = divide_and_conquer_sample_groups(
        xy,
        Radius_NN,
        nn_points_all,
        min_samples_per_point,
        num_candidates_in=point_conv_conf['num_candidates'],
        max_points_per_region=point_conv_conf['max_points_per_region'],
        min_points_in_region=point_conv_conf['min_points_in_region'],
        num_threads=point_conv_conf['num_threads_PointCONV_sample'],
    )

    if point_conv_conf['training_data_config'] is not None and learning_data_base_dir is not None:
        if point_conv_conf['training_data_config']['save_learning_data']:
            data_path_orig = os.path.join(os.path.dirname(las_file), 'learning_data', os.path.basename(las_file)[:-4])
            # data_dir = data_path_orig.replace(data_base_dir,
            #                                   point_conv_conf['training_data_config']['learning_data_base_dir'])

            local_lrn_data_dir = os.path.join(learning_data_base_dir,os.path.basename(las_file)[:-4],)

            logging.info("Saving training data: " + local_lrn_data_dir + " ...")
            os.makedirs(local_lrn_data_dir, exist_ok=True)

            model_to_class = point_conv_conf['class_mapping_model']['model_to_class']
            class_to_model = point_conv_conf['class_mapping_model']['class_to_model']
            scale_type = point_conv_conf['training_data_config']['scale_type']

            data_cnt_list = np.arange(len(point_cloud_sample_ind))

            for i in tqdm(data_cnt_list):
                xyz_c = xyz_orig[point_cloud_sample_ind[i]]
                xyz_class_c = xyz_class[point_cloud_sample_ind[i]]

                xyz_class_to_model = -np.ones_like(point_cloud_sample_ind[i])
                xyz_class_to_model[:] = [class_to_model[c_c] for c_c in xyz_class_c]

                xyz_c_file = os.path.join(local_lrn_data_dir, 'lrn_xyz_' + str(i) + '.npy')
                xyz_class_c_file = os.path.join(local_lrn_data_dir, 'lrn_class_' + str(i) + '.npy')
                xyz_x_scale_sub = os.path.join(local_lrn_data_dir, 'lrn_scale_' + str(i) + '.npy')

                # Center ONLY the XYZ channels — with use_geometry_features the
                # rows are (M, 6) and the feature channels must stay absolute.
                n_center = min(3, xyz_c.shape[-1])
                if scale_type == 'mean':
                    scale_sub_xyz = np.mean(xyz_c[:, :n_center], axis=0)
                else:
                    scale_sub_xyz = np.median(xyz_c[:, :n_center], axis=0)

                xyz_scale = np.copy(xyz_c)
                xyz_scale[:, :n_center] -= scale_sub_xyz

                np.save(xyz_x_scale_sub, scale_sub_xyz.astype(np.float64))
                np.save(xyz_c_file, xyz_scale.astype(np.float32))
                np.save(xyz_class_c_file, xyz_class_to_model.astype(np.uint8))

                if point_conv_conf['training_data_config']['save_learning_las']:
                    newlas_filename = os.path.join(local_lrn_data_dir, 'lrn_data_' + str(i) + '.las')

                    las_data = laspy.create(point_format=point_format, file_version=str(file_version))
                    las_data.x = xyz_c[:, 0]
                    las_data.y = xyz_c[:, 1]
                    las_data.z = xyz_c[:, 2]
                    las_data.blue = las.blue[point_cloud_sample_ind[i]]
                    las_data.green = las.green[point_cloud_sample_ind[i]]
                    las_data.red = las.red[point_cloud_sample_ind[i]]
                    las_data.classification = xyz_class_c
                    with laspy.open(newlas_filename, mode="w", header=las_data.header) as out_las:
                        out_las.write_points(las_data.points)

    s_pts = {
        'nn_points_all': nn_points_all,
        'min_samples_per_point': min_samples_per_point,

        'point_cloud_sample_ind': point_cloud_sample_ind,

        'pnt_sample_number': pnt_sample_number,

        'xyz': xyz_orig,
        'xyz_class': xyz_class,
        'color_orig': color_orig,

        'above_ground_minimum': above_ground_minimum,

        'mask_predict_from_original_file': l_ind_predict,
        'Original_File_Name': las_file,
    }

    return s_pts
