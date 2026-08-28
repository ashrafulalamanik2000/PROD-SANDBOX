

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow.compat.v1 as tf

from utils.tf_ops.interpolation.tf_interpolate import three_nn, three_interpolate
from utils import pointconv_util
from utils import tf_util

def weight_net_hidden(xyz, hidden_units, scope, is_training, bn_decay=None, weight_decay = None, activation_fn=tf.nn.relu):

    with tf.variable_scope(scope) as sc:
        net = xyz
        for i, num_hidden_units in enumerate(hidden_units):
            net = tf_util.conv2d(net, num_hidden_units, [1, 1],
                                padding = 'VALID', stride=[1, 1],
                                bn = True, is_training = is_training, activation_fn=activation_fn,
                                scope = 'wconv%d'%(i), bn_decay=bn_decay, weight_decay = weight_decay)

            #net = tf_util.dropout(net, keep_prob=0.5, is_training=is_training, scope='wconv_dp%d'%(i))
    return net

def weight_net(xyz, hidden_units, scope, is_training, bn_decay=None, weight_decay = None, activation_fn=tf.nn.relu):

    with tf.variable_scope(scope) as sc:
        net = xyz
        for i, num_hidden_units in enumerate(hidden_units):
            if i != len(hidden_units) -1:
                net = tf_util.conv2d(net, num_hidden_units, [1, 1],
                                    padding = 'VALID', stride=[1, 1],
                                    bn = True, is_training = is_training, activation_fn=activation_fn,
                                    scope = 'wconv%d'%(i), bn_decay=bn_decay, weight_decay = weight_decay)
            else:
                net = tf_util.conv2d(net, num_hidden_units, [1, 1],
                                    padding = 'VALID', stride=[1, 1],
                                    bn = False, is_training = is_training, activation_fn=None,
                                    scope = 'wconv%d'%(i), bn_decay=bn_decay, weight_decay = weight_decay)
            #net = tf_util.dropout(net, keep_prob=0.5, is_training=is_training, scope='wconv_dp%d'%(i))
    return net

def nonlinear_transform(data_in, mlp, scope, is_training, bn_decay=None, weight_decay = None, activation_fn = tf.nn.relu):

    with tf.variable_scope(scope) as sc:

        net = data_in
        l = len(mlp)
        if l > 1:
            for i, out_ch in enumerate(mlp[0:(l-1)]):
                net = tf_util.conv2d(net, out_ch, [1, 1],
                                    padding = 'VALID', stride=[1, 1],
                                    bn = True, is_training = is_training, activation_fn=tf.nn.relu,
                                    scope = 'nonlinear%d'%(i), bn_decay=bn_decay, weight_decay = weight_decay)

                #net = tf_util.dropout(net, keep_prob=0.5, is_training=is_training, scope='dp_nonlinear%d'%(i))
        net = tf_util.conv2d(net, mlp[-1], [1, 1],
                            padding = 'VALID', stride=[1, 1],
                            bn = False, is_training = is_training,
                            scope = 'nonlinear%d'%(l-1), bn_decay=bn_decay,
                            activation_fn=tf.nn.sigmoid, weight_decay = weight_decay)

    return net

def feature_encoding_layer(xyz, feature, npoint, radius, sigma, K, mlp, is_training, bn_decay, weight_decay, scope, bn=True, use_xyz=True):
    with tf.variable_scope(scope) as sc:
        num_points = xyz.get_shape()[1]
        if num_points == npoint:
            new_xyz = xyz
        else:
            new_xyz = pointconv_util.sampling(npoint, xyz)

        grouped_xyz, grouped_feature, idx = pointconv_util.grouping(feature, K, xyz, new_xyz)

        density = pointconv_util.kernel_density_estimation_ball(xyz, radius, sigma)
        inverse_density = tf.math.divide(1.0, density)
        grouped_density = tf.gather_nd(inverse_density, idx)
        inverse_max_density = tf.reduce_max(grouped_density, axis = 2, keepdims = True)
        density_scale = tf.math.divide(grouped_density, inverse_max_density)


        for i, num_out_channel in enumerate(mlp):
            if i != len(mlp) - 1:
                grouped_feature = tf_util.conv2d(grouped_feature, num_out_channel, [1,1],
                                            padding='VALID', stride=[1,1],
                                            bn=bn, is_training=is_training,
                                            scope='conv%d'%(i), bn_decay=bn_decay, weight_decay = weight_decay) 

        weight = weight_net_hidden(grouped_xyz, [32], scope = 'weight_net', is_training=is_training, bn_decay = bn_decay, weight_decay = weight_decay)

        density_scale = nonlinear_transform(density_scale, [16, 1], scope = 'density_net', is_training=is_training, bn_decay = bn_decay, weight_decay = weight_decay)

        new_points = tf.multiply(grouped_feature, density_scale)

        new_points = tf.transpose(new_points, [0, 1, 3, 2])

        new_points = tf.matmul(new_points, weight)

        new_points = tf_util.conv2d(new_points, mlp[-1], [1,new_points.get_shape()[2]],
                                        padding='VALID', stride=[1,1],
                                        bn=bn, is_training=is_training,
                                        scope='after_conv', bn_decay=bn_decay, weight_decay = weight_decay) 

        new_points = tf.squeeze(new_points, [2]) # (batch_size, npoints, mlp2[-1])

        return new_xyz, new_points

def feature_decoding_layer(xyz1, xyz2, points1, points2, radius, sigma, K, mlp, is_training, bn_decay, weight_decay, scope, bn=True, use_xyz = True):
    with tf.variable_scope(scope) as sc:
        dist, idx = three_nn(xyz1, xyz2)
        dist = tf.maximum(dist, 1e-10)
        norm = tf.reduce_sum((1.0/dist),axis=2,keepdims=True)
        norm = tf.tile(norm,[1,1,3])
        weight = (1.0/dist) / norm
        interpolated_points = three_interpolate(points2, idx, weight)

        grouped_xyz, grouped_feature, idx = pointconv_util.grouping(interpolated_points, K, xyz1, xyz1, use_xyz=use_xyz)

        density = pointconv_util.kernel_density_estimation_ball(xyz1, radius, sigma)
        inverse_density = tf.math.divide(1.0, density)
        grouped_density = tf.gather_nd(inverse_density, idx) # (batch_size, npoint, nsample, 1)
        inverse_max_density = tf.reduce_max(grouped_density, axis = 2, keepdims = True)
        density_scale = tf.math.divide(grouped_density, inverse_max_density)


        weight = weight_net_hidden(grouped_xyz, [32], scope = 'decode_weight_net', is_training=is_training, bn_decay = bn_decay, weight_decay = weight_decay)

        density_scale = nonlinear_transform(density_scale, [16, 1], scope = 'decode_density_net', is_training=is_training, bn_decay = bn_decay, weight_decay = weight_decay)

        new_points = tf.multiply(grouped_feature, density_scale)

        new_points = tf.transpose(new_points, [0, 1, 3, 2])

        new_points = tf.matmul(new_points, weight)

        new_points = tf_util.conv2d(new_points, mlp[0], [1,new_points.get_shape()[2]],
                                        padding='VALID', stride=[1,1],
                                        bn=bn, is_training=is_training,
                                        scope='decode_after_conv', bn_decay=bn_decay, weight_decay = weight_decay) 

        if points1 is not None:
            new_points1 = tf.concat(axis=-1, values=[new_points, tf.expand_dims(points1, axis = 2)]) # B,ndataset1,nchannel1+nchannel2
        else:
            new_points1 = new_points

        for i, num_out_channel in enumerate(mlp):
            if i != 0:
                new_points1 = tf_util.conv2d(new_points1, num_out_channel, [1,1],
                                            padding='VALID', stride=[1,1],
                                            bn=bn, is_training=is_training,
                                            scope='conv_%d'%(i), bn_decay=bn_decay, weight_decay = weight_decay)
        new_points1 = tf.squeeze(new_points1, [2]) # B,ndataset1,mlp[-1]
        return new_points1

def feature_decoding_layer_depthwise(xyz1, xyz2, points1, points2, radius, sigma, K, mlp, is_training, bn_decay, weight_decay, scope, bn=True, use_xyz = True):
    with tf.variable_scope(scope) as sc:
        dist, idx = three_nn(xyz1, xyz2)
        dist = tf.maximum(dist, 1e-10)
        norm = tf.reduce_sum((1.0/dist),axis=2,keepdims=True)
        norm = tf.tile(norm,[1,1,3])
        weight = (1.0/dist) / norm
        interpolated_points = three_interpolate(points2, idx, weight)

        #setup for deConv
        grouped_xyz, grouped_feature, idx = pointconv_util.grouping(interpolated_points, K, xyz1, xyz1, use_xyz=use_xyz)

        density = pointconv_util.kernel_density_estimation_ball(xyz1, radius, sigma)
        inverse_density = tf.math.divide(1.0, density)
        grouped_density = tf.gather_nd(inverse_density, idx) # (batch_size, npoint, nsample, 1)
        inverse_max_density = tf.reduce_max(grouped_density, axis = 2, keepdims = True)
        density_scale = tf.math.divide(grouped_density, inverse_max_density)


        weight = weight_net(grouped_xyz, [32, grouped_feature.get_shape()[3]], scope = 'decode_weight_net', is_training=is_training, bn_decay = bn_decay, weight_decay = weight_decay)

        density_scale = nonlinear_transform(density_scale, [16, 1], scope = 'decode_density_net', is_training=is_training, bn_decay = bn_decay, weight_decay = weight_decay)

        new_points = tf.multiply(grouped_feature, density_scale)

        new_points = tf.multiply(grouped_feature, weight)

        new_points = tf_util.reduce_sum2d_conv(new_points, axis = 2, scope = 'fp_sumpool', bn=True,
                                        bn_decay = bn_decay, is_training = is_training, keepdims = False)    

        if points1 is not None:
            new_points1 = tf.concat(axis=-1, values=[new_points, points1]) # B,ndataset1,nchannel1+nchannel2
        else:
            new_points1 = new_points
        new_points1 = tf.expand_dims(new_points1, 2)
        for i, num_out_channel in enumerate(mlp):
            new_points1 = tf_util.conv2d(new_points1, num_out_channel, [1,1],
                                            padding='VALID', stride=[1,1],
                                            bn=bn, is_training=is_training,
                                            scope='conv_%d'%(i), bn_decay=bn_decay, weight_decay = weight_decay)
        new_points1 = tf.squeeze(new_points1, [2]) # B,ndataset1,mlp[-1]
        return new_points1

def placeholder_inputs(batch_size, num_point, channel):
    pointclouds_pl = tf.placeholder(tf.float32, shape=(batch_size, num_point, 3))
    feature_pts_pl = tf.placeholder(tf.float32, shape=(batch_size, num_point, channel))
    labels_pl = tf.placeholder(tf.int32, shape=(batch_size, num_point))
    return pointclouds_pl, feature_pts_pl, labels_pl

if __name__=='__main__':
    import numpy as np
    pts = np.random.random((32, 2048, 3)).astype('float32')
    fpts = pts
    sigma = 0.1
    N = 512
    K = 64
    D = 1
    C_list = [64, 128]
    mlp_w = [64]
    mlp_d = [64]
    is_training = tf.placeholder(tf.bool, shape=())

    import pdb
    pdb.set_trace()

    with tf.device('/gpu:1'):
        points_pl, features_pl, labels_pl = placeholder_inputs(32, 2048, 3)
        sub_pts, features = feature_encoding_layer(points_pl, features_pl, N, sigma, K, [10, 20], is_training, bn_decay = 0.1, weight_decay = 0.1, scope = "FE")
        feature_decode = feature_decoding_layer(points_pl, sub_pts, features_pl, features, sigma, K, [10, 23], is_training, bn_decay=0.1, weight_decay = 0.1, scope= "FD")





