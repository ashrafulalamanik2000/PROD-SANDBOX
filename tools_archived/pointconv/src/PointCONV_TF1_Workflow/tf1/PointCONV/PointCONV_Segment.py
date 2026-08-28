import logging

import os

import numpy as np

import gzip

from pickle import load

import sys

import tensorflow.compat.v1 as tf

import importlib

from datetime import datetime

import time

from .SamplePoints_Parr_Deterministic import SamplePoints_Parr_Deterministic

from .get_gpu_with_least_utilization import get_gpu_with_least_utilization

from .Dataset import Dataset

import gc


def segment_point_clouds(
        point_conv_conf,
        voxel_info_filename,
        model_folder):
    start_time_PointCONV = datetime.now()

    with gzip.open(voxel_info_filename, 'rb') as f:
        voxel_info = load(f)

    MODEL_PATH = os.path.join(model_folder, point_conv_conf['model_directory'],
                              'Best_Model/model.ckpt')

    BATCH_SIZE = point_conv_conf['BATCH_SIZE']

    filename_exp_defn = os.path.join(model_folder, point_conv_conf['model_directory'],'exp_def.p')

    exp_def = load(open(filename_exp_defn, "rb"))

    NUM_CLASSES = exp_def['NUM_CLASSES']

    BANDWIDTH = exp_def['BANDWIDTH']
    radii = exp_def['radii']

    NUM_POINT = exp_def['NUM_POINT']
    nn_points_all = NUM_POINT

    dim = exp_def['dim']

    if 'LABELS_original' in exp_def:
        LABELS_original = exp_def['LABELS_original']
        LABELS_Map = exp_def['LABELS_Map']
        # LABELS = exp_def['LABELS']
    else:
        LABELS_original = None
        LABELS_Map = None
        # LABELS = None

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(BASE_DIR)
    sys.path.append(os.path.join(BASE_DIR, 'model_code_PointCONV'))

    if 'model' in exp_def:
        model = exp_def['model']
    else:
        model = exp_def['FLAGS'].model
    MODEL = importlib.import_module(model)  # import network module

    if point_conv_conf['GPU_INDEX'] == -1:
        GPU_INDEX = get_gpu_with_least_utilization()
    else:
        GPU_INDEX = point_conv_conf['GPU_INDEX']

    print(f"Using GPU {GPU_INDEX}")

    Ignore_Classes = point_conv_conf['noise_labels']
    min_samples_per_point = point_conv_conf['min_samples_per_point']
    learning_data_base_dir = point_conv_conf['learning_data_base_dir']

    if 'data_definition' in exp_def:
        # nn_type = exp_def['data_definition']['nn_type']
        Radius_NN = exp_def['data_definition']['Radius_NN']
    else:
        # nn_type = Point_Conv_config['nn_type']
        Radius_NN = exp_def['Radius_NN']

    def Model_Predict_Prob(sess, ops, DATASET_SEG, file_name, BATCH_SIZE=8):

        def get_batch(dataset, idxs, start_idx, end_idx, number_of_resamples=1):
            if number_of_resamples == 1:
                bsize = end_idx - start_idx
                batch_data = np.zeros((bsize, NUM_POINT, dim))
                batch_label = np.zeros((bsize, NUM_POINT), dtype=np.int32)
                batch_smpw = np.zeros((bsize, NUM_POINT), dtype=np.float32)
                for i in range(bsize):
                    ps, seg, smpw = dataset[idxs[i + start_idx]]
                    batch_data[i, ...] = ps
                    batch_label[i, :] = seg
                    batch_smpw[i, :] = smpw
            else:
                bsize = end_idx - start_idx
                batch_data = np.zeros((bsize, NUM_POINT, dim))
                batch_label = np.zeros((bsize, NUM_POINT), dtype=np.int32)
                batch_smpw = np.zeros((bsize, NUM_POINT), dtype=np.float32)
                for i in range(bsize):
                    idx = idxs[i + start_idx]
                    ind_data_set = np.remainder(idx, len(dataset))
                    ps, seg, smpw = dataset[ind_data_set]
                    batch_data[i, ...] = ps
                    batch_label[i, :] = seg
                    batch_smpw[i, :] = smpw

            return batch_data, batch_label, batch_smpw

        is_training = False

        test_idxs = np.arange(0, len(DATASET_SEG))
        num_batches = int(len(DATASET_SEG) / BATCH_SIZE)

        logging.info(str(datetime.now()))
        logging.info('---- EVALUATION WHOLE las ----')
        logging.info(file_name)
        logging.info(' ')

        actual_label_seg = []
        pred_label_seg = []
        pred_label_prob = []
        for batch_idx in range(num_batches):
            start_idx = batch_idx * BATCH_SIZE
            end_idx = (batch_idx + 1) * BATCH_SIZE
            batch_data, batch_label, batch_smpw = get_batch(DATASET_SEG, test_idxs, start_idx, end_idx,
                                                            number_of_resamples=1)

            feed_dict = {ops['pointclouds_pl']: batch_data,
                         ops['labels_pl']: batch_label,
                         ops['smpws_pl']: batch_smpw,
                         ops['is_training_pl']: is_training}

            pred_prob_all = sess.run(ops['pred'], feed_dict=feed_dict)  # BxNxNUM_CLASSES

            for bc in range(pred_prob_all.shape[0]):
                pred_prob = np.reshape(pred_prob_all[bc, ...], (NUM_POINT, NUM_CLASSES))

                whole_scene_label = batch_label[bc, ...]

                pred_label = np.argmax(pred_prob, 1)

                pred_label_seg.append(pred_label)
                pred_label_prob.append(pred_prob)
                actual_label_seg.append(whole_scene_label)

        cal_data = {
            'actual_label_seg': actual_label_seg,
            'pred_label_seg': pred_label_seg,
            'pred_label_prob': pred_label_prob,
        }

        return cal_data

    def evaluate():
        with tf.device('/gpu:' + str(GPU_INDEX)):
            pointclouds_pl = tf.placeholder(tf.float32, shape=(BATCH_SIZE, NUM_POINT, dim))
            labels_pl = tf.placeholder(tf.int32, shape=(BATCH_SIZE, NUM_POINT))
            smpws_pl = tf.placeholder(tf.float32, shape=(BATCH_SIZE, NUM_POINT))
            is_training_pl = tf.placeholder(tf.bool, shape=())

            pred, end_points = MODEL.get_model(pointclouds_pl, is_training_pl, NUM_CLASSES, BANDWIDTH,
                                               radii=radii)
            # MODEL.get_loss(pred, labels_pl, smpws_pl)
            saver = tf.train.Saver()

        # Create a session
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        config.allow_soft_placement = True
        config.log_device_placement = False
        sess = tf.Session(config=config)

        # Restore variables from disk.
        saver.restore(sess, MODEL_PATH)
        logging.info("Model restored.")

        ops = {'pointclouds_pl': pointclouds_pl,
               'labels_pl': labels_pl,
               'is_training_pl': is_training_pl,
               'smpws_pl': smpws_pl,
               'pred': pred}

        # las_file_locations_vox = [voxel_info['las_file_locations_vox']]
        vox_las_filename = voxel_info['vox_las_filename']
        # las_file_output_location = voxel_info['las_file_locations_orig']

        if not os.path.exists(vox_las_filename):
            logging.info("No voxe las file found.")
            return

        total_elapsed_time = 0
        str_info = 'Processing File: ' + vox_las_filename
        logging.info(str_info)

        # single_file_name = las_file_name[:-4] + '.p'
        single_file_name = os.path.basename(vox_las_filename)[:-4]

        out_dir = os.path.dirname(vox_las_filename)

        filename_output_final = os.path.join(out_dir, single_file_name + '_prob.npz')
        os.makedirs(os.path.dirname(filename_output_final), exist_ok=True)

        start_time = time.perf_counter()

        if not os.path.exists(filename_output_final):
            gc.collect()

            # Record the start time

            os.makedirs(out_dir, exist_ok=True)

            pts = SamplePoints_Parr_Deterministic(vox_las_filename,
                                                  point_conv_conf,
                                                  nn_points_all=nn_points_all,
                                                  min_samples_per_point=min_samples_per_point,
                                                  classifications_keep=None,
                                                  Radius_NN=Radius_NN,
                                                  Ignore_Classes_list=Ignore_Classes,
                                                  learning_data_base_dir=learning_data_base_dir,
                                                  random_seed=point_conv_conf['random_seed_sample_points'],
                                                  # dim>3 models (ablation C) self-describe via exp_def;
                                                  # dim=3 models take the unchanged path.
                                                  use_geometry_features=(int(dim) > 3),
                                                  )

            if pts is None:
                return

            if point_conv_conf['training_data_config'] is not None:
                if not point_conv_conf['training_data_config']['apply_current_model']:
                    return

            DATA_SET = Dataset(pts, exp_def, weight_datas=False,
                               BATCH_SIZE=BATCH_SIZE,
                               MAP_Y=False)

            model_pred = Model_Predict_Prob(sess, ops, DATA_SET, single_file_name, BATCH_SIZE=BATCH_SIZE)
            if point_conv_conf['training_data_config'] is not None:
                if point_conv_conf['training_data_config']['save_model_output_data']:
                    data_path_orig = os.path.join(os.path.dirname(out_dir), 'learning_data',
                                                  single_file_name)
                    data_dir = data_path_orig.replace(learning_data_base_dir,
                                                      point_conv_conf['training_data_config'][
                                                          'learning_data_base_dir'])

                    logging.info("Saving training data: " + data_dir + " ...")
                    os.makedirs(data_dir, exist_ok=True)

                    # data_dir = os.path.join(os.path.dirname(las_file_location), 'learning_data',
                    #                         os.path.basename(las_file_location)[:-4])
                    # os.makedirs(data_dir, exist_ok=True)

                    logging.info("Saving Model Predictions: " + data_dir + " ...")

                    for i in range(len(model_pred['pred_label_prob'])):
                        model_pred_file = os.path.join(data_dir, 'prediction_from_model_' + str(i) + '.npy')
                        np.save(model_pred_file, model_pred['pred_label_prob'][i])

            xyz_class_actual_orig = pts['xyz_class']
            point_cloud_sample_ind = pts['point_cloud_sample_ind']

            if LABELS_original is not None:
                ''' map labels '''
                y_or = np.copy(xyz_class_actual_orig)
                y_bad_label = -1111111
                y_new = y_bad_label * np.ones_like(y_or)
                for lc in range(len(LABELS_original)):
                    y_new[y_or == LABELS_original[lc]] = LABELS_Map[lc]

                xyz_class_actual = y_bad_label * np.ones_like(y_new)
                labels_unique = np.unique(LABELS_Map)
                for lc in range(len(labels_unique)):
                    xyz_class_actual[y_new == labels_unique[lc]] = lc
            else:
                xyz_class_actual = xyz_class_actual_orig
                labels_unique = np.arange(NUM_CLASSES)

            pred_label_prob = model_pred['pred_label_prob']

            point_vote_prob = np.zeros((xyz_class_actual.shape[0], NUM_CLASSES), dtype=float)

            tot_votes_per_point = np.zeros((xyz_class_actual.shape[0],), dtype=int)

            for s_cnt in range(point_cloud_sample_ind.shape[0]):
                curr_ind = point_cloud_sample_ind[s_cnt]
                pred_prob = np.copy(pred_label_prob[s_cnt])
                point_vote_prob[curr_ind, :] += pred_prob

                tot_votes_per_point[curr_ind] += 1

            ind_not_valid = np.where(tot_votes_per_point == 0)[0]
            if len(ind_not_valid) > 0:
                tot_votes_per_point[ind_not_valid] = 1
                logging.info("Total number of points not classified: " + str(len(ind_not_valid)) +
                             " out of " + str(xyz_class_actual.shape[0]) + " points")

            for cc in range(point_vote_prob.shape[1]):
                point_vote_prob[:, cc] = point_vote_prob[:, cc] / tot_votes_per_point
                if len(ind_not_valid) > 0:
                    point_vote_prob[ind_not_valid, cc] = 0.0

            raw_cal_data = {
                'point_vote_prob': point_vote_prob,
                'xyz': pts['xyz'],
                'color': pts['color_orig'],
                'labels_unique': labels_unique,
                'mask_predict_from_original_file': pts['mask_predict_from_original_file'],
            }
            # np.savez_compressed(filename_output_final, **raw_cal_data)
            # Atomic write: build the .npz at a temp path, then os.replace it into
            # place. A kill mid-write (SIGPIPE / OOM / Ctrl-C during the multi-hour
            # GPU stage) must not leave a truncated _prob.npz that the resume guard
            # (line 219, os.path.exists) would then treat as a completed tile.
            # Passing a file handle stops np.savez appending a 2nd '.npz' to the tmp.
            _tmp_npz = filename_output_final + '.tmp'
            with open(_tmp_npz, 'wb') as _fh:
                np.savez(_fh, **raw_cal_data)
            os.replace(_tmp_npz, filename_output_final)

        # Record the end time
        end_time = time.perf_counter()

        # Compute and logging.info the elapsed time
        elapsed_time = end_time - start_time
        logging.info("Prediction Elapsed time: {:.6f} seconds".format(elapsed_time))

        total_elapsed_time += elapsed_time

        # '''' remove file .p - too big and not needed '''
        # if os.path.exists(os.path.join(out_dir, single_file_name)):
        #     os.remove(os.path.join(out_dir, single_file_name))

        logging.info("Total Prediction Elapsed time: {:.6f} seconds".format(total_elapsed_time))

        return

    with tf.Graph().as_default():
        evaluate()

    end_time_PointCONV = datetime.now()
    delta_time = end_time_PointCONV - start_time_PointCONV
    logging.info(
        f"PointCONV completed at {end_time_PointCONV.strftime('%H:%M:%S')} : elapsed time = {delta_time}\n")

    return
