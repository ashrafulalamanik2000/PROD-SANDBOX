import numpy as np

from tensorflow import keras


def get_point_data(pts, exp_def, BATCH_SIZE=1, MAP_Y=True):
    point_cloud_sample_ind = pts['point_cloud_sample_ind']
    xyz = pts['xyz']
    xyz_class = pts['xyz_class']

    point_cloud_sample = xyz[point_cloud_sample_ind]

    point_cloud_sample_class = xyz_class[point_cloud_sample_ind]

    x_in = np.copy(point_cloud_sample)
    y = np.copy(point_cloud_sample_class)

    ''' normalize data '''
    scale_type = exp_def['scale_type']

    x = np.zeros_like(x_in)

    # Center ONLY the first 3 (XYZ) channels. Extra geometry-feature channels
    # (hag/linearity/verticality, ablation C / dim>3) are absolute [0,1]
    # scalars that the trainer feeds UNcentered — centering them here would
    # break train/inference parity.
    n_center = min(3, x_in.shape[-1])
    for pnt_cnt in range(x_in.shape[0]):
        x[pnt_cnt, :] = np.copy(x_in[pnt_cnt])
        if scale_type == 0:
            x[pnt_cnt, :, :n_center] -= np.mean(x_in[pnt_cnt][:, :n_center], axis=0)
        else:
            x[pnt_cnt, :, :n_center] -= np.median(x_in[pnt_cnt][:, :n_center], axis=0)

    if 'LABELS_original' in exp_def:
        LABELS_original = exp_def['LABELS_original']
        LABELS_Map = exp_def['LABELS_Map']
        LABELS = exp_def['LABELS']
        if MAP_Y:
            ''' map labels '''
            y_or = np.copy(y)
            y_bad_label = -1111111
            y_new = y_bad_label * np.ones_like(y_or)
            for lc in range(len(LABELS_original)):
                y_new[y_or == LABELS_original[lc]] = LABELS_Map[lc]

            ind_not_valid = np.where(y_new == y_bad_label)[0]
            if len(ind_not_valid) > 0:
                print('Error in get_point_data: Not a valid label transformation')
                return None, None, None

            y = np.copy(y_new)

    else:
        LABELS = np.arange(exp_def['NUM_CLASSES'])

    if BATCH_SIZE > 1:
        curr_num_pts = x.shape[0]

        number_of_pts_batches = int(curr_num_pts / BATCH_SIZE) * BATCH_SIZE

        if number_of_pts_batches < curr_num_pts:
            pts_needed = (int(curr_num_pts / BATCH_SIZE) + 1) * BATCH_SIZE

            pts_to_add = pts_needed - curr_num_pts

            ind_keep = np.arange(curr_num_pts - pts_to_add, curr_num_pts)
            x = np.concatenate((x, x[ind_keep, :, :]), axis=0)
            y = np.concatenate((y, y[ind_keep, :]), axis=0)

    ''' add labels '''
    NUM_SAMPLE_POINTS = y.shape[1]

    label_data_all = []
    for pc in range(y.shape[0]):
        label_data = np.zeros((NUM_SAMPLE_POINTS,), dtype=np.float32)
        for c_cnt in range(len(LABELS)):
            label_data[y[pc] == LABELS[c_cnt]] = c_cnt

        # Apply one-hot encoding to the dense label representation.
        label_data = keras.utils.to_categorical(label_data, num_classes=len(LABELS) + 1)

        label_data_all.append(label_data)

    label_cloud_one_hot = np.array(label_data_all)

    return x, y, label_cloud_one_hot


class Dataset():
    def __init__(self, s_pts, exp_def, weight_datas=False, BATCH_SIZE=1, MAP_Y=True):

        train_point_clouds, train_all_labels, _ = get_point_data(s_pts, exp_def, BATCH_SIZE=BATCH_SIZE, MAP_Y=MAP_Y)

        if train_point_clouds is None:
            print('No Data')
            exit()

        self.npoints = train_point_clouds.shape[1]
        self.dim = train_point_clouds.shape[2]
        self.scene_points_list, self.semantic_labels_list = [], []
        for cnt_s in range(train_point_clouds.shape[0]):
            self.scene_points_list.append(train_point_clouds[cnt_s, :, :])
            self.semantic_labels_list.append(train_all_labels[cnt_s, :])

        labels_unique, label_cnt = np.unique(train_all_labels, return_counts=True)

        ''' map the labels'''
        for cnt_s in range(len(self.semantic_labels_list)):
            semantic_labels_list = np.copy(self.semantic_labels_list[cnt_s])
            for lc in range(len(labels_unique)):
                semantic_labels_list[semantic_labels_list == labels_unique[lc]] = lc
            self.semantic_labels_list[cnt_s] = np.copy(semantic_labels_list)

        self.labels_unique = labels_unique
        if weight_datas:
            labelweights = label_cnt
            labelweights = labelweights.astype(np.float32)
            labelweights = labelweights / np.sum(labelweights)
            labelweights[labelweights < 1e-7] = 1.0
            self.labelweights = np.power(np.amax(labelweights) / (labelweights), 1 / 3.0)
            print(self.labelweights)
        else:
            self.labelweights = np.ones(labels_unique.shape[0])

    def __getitem__(self, index):
        point_set = self.scene_points_list[index]
        semantic_seg = self.semantic_labels_list[index].astype(np.int32)
        sample_weight = self.labelweights[semantic_seg]
        return point_set, semantic_seg, sample_weight

    def __len__(self):
        return len(self.scene_points_list)
