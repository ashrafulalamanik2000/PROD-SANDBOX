import tensorflow.compat.v1 as tf

from utils import tf_util
from PointConv import feature_encoding_layer, feature_decoding_layer

keep_prob = 0.5


def placeholder_inputs(batch_size, num_point, dim):
    pointclouds_pl = tf.placeholder(tf.float32, shape=(batch_size, num_point, dim))
    labels_pl = tf.placeholder(tf.int32, shape=(batch_size, num_point))
    smpws_pl = tf.placeholder(tf.float32, shape=(batch_size, num_point))
    return pointclouds_pl, labels_pl, smpws_pl


def get_model(point_cloud, is_training, num_class, sigma, bn_decay=None, weight_decay=None,
              radii=[0.1, 0.2, 0.4, 0.8]):
    """ Semantic segmentation PointNet, input is BxNxdim, output Bxnum_class.

    dim may exceed 3 (ablation C: XYZ + geometry features). Geometric
    operations (FPS sampling, KNN grouping, three_nn interpolation) must see
    ONLY the XYZ channels; the full channel set flows through l0_points as
    features. For dim=3 the slice is a no-op (numerically identical)."""
    end_points = {}

    l0_xyz = point_cloud[:, :, :3]
    l0_points = point_cloud

    print(l0_xyz, l0_points)
    # Feature encoding layers
    l1_xyz, l1_points = feature_encoding_layer(l0_xyz, l0_points, npoint=1024, radius=radii[0], sigma=sigma, K=32,
                                               mlp=[32, 32, 64], is_training=is_training, bn_decay=bn_decay,
                                               weight_decay=weight_decay, scope='layer1')
    l2_xyz, l2_points = feature_encoding_layer(l1_xyz, l1_points, npoint=256, radius=radii[1], sigma=2 * sigma, K=32,
                                               mlp=[64, 64, 128], is_training=is_training, bn_decay=bn_decay,
                                               weight_decay=weight_decay, scope='layer2')
    l3_xyz, l3_points = feature_encoding_layer(l2_xyz, l2_points, npoint=64, radius=radii[2], sigma=4 * sigma, K=32,
                                               mlp=[128, 128, 256], is_training=is_training, bn_decay=bn_decay,
                                               weight_decay=weight_decay, scope='layer3')
    l4_xyz, l4_points = feature_encoding_layer(l3_xyz, l3_points, npoint=36, radius=radii[3], sigma=8 * sigma, K=32,
                                               mlp=[256, 256, 512], is_training=is_training, bn_decay=bn_decay,
                                               weight_decay=weight_decay, scope='layer4')

    # Feature decoding layers
    l3_points = feature_decoding_layer(l3_xyz, l4_xyz, l3_points, l4_points, radii[3], 8 * sigma, 16, [512, 512],
                                       is_training, bn_decay, weight_decay, scope='fa_layer1')
    l2_points = feature_decoding_layer(l2_xyz, l3_xyz, l2_points, l3_points, radii[2], 4 * sigma, 16, [256, 256],
                                       is_training, bn_decay, weight_decay, scope='fa_layer2')
    l1_points = feature_decoding_layer(l1_xyz, l2_xyz, l1_points, l2_points, radii[1], 2 * sigma, 16, [256, 128],
                                       is_training, bn_decay, weight_decay, scope='fa_layer3')
    l0_points = feature_decoding_layer(l0_xyz, l1_xyz, l0_points, l1_points, radii[0], sigma, 16, [128, 128, 128],
                                       is_training, bn_decay, weight_decay, scope='fa_layer4')

    # FC layers
    net = tf_util.conv1d(l0_points, 128, 1, padding='VALID', bn=True, is_training=is_training, scope='fc1',
                         bn_decay=bn_decay, weight_decay=weight_decay)
    end_points['feats'] = net
    rate = 1 - keep_prob
    net = tf_util.dropout(net, rate=rate, is_training=is_training, scope='dp1')
    # net = tf_util.conv1d(net, num_class, 1, padding='VALID', activation_fn=None, weight_decay=weight_decay, scope='fc2')
    net = tf_util.conv1d(net, num_class, 1, padding='VALID', activation_fn=tf.nn.softmax,
                         weight_decay=weight_decay, scope='fc2')

    return net, end_points


def Loss_Mean_IoU_Probabilities(y_true_in, y_pred):
    y_true = tf.one_hot(tf.squeeze(y_true_in), y_pred.shape[-1])

    intersection = tf.multiply(y_true, y_pred)

    union = y_true + y_pred - intersection

    i_sum = tf.reduce_sum(intersection, axis=-2)
    u_sum = tf.reduce_sum(union, axis=-2)

    iou = tf.divide(i_sum, u_sum + 1e-6)

    miou = tf.reduce_mean(iou)

    return 1 - miou


def Loss_Mean_IoU_Probabilities_Weighted(y_true_in, y_pred, smpw):
    smpw_e = tf.expand_dims(smpw, axis=-1)

    tiled_tensor = tf.tile(smpw_e, [1, 1, y_pred.shape[-1]]) / y_pred.shape[-1]

    y_true = tf.one_hot(tf.squeeze(y_true_in), y_pred.shape[-1])

    intersection = tf.multiply(y_true, y_pred)
    intersection = tf.multiply(intersection, tiled_tensor)

    union = y_true + y_pred - intersection

    i_sum = tf.reduce_sum(intersection, axis=-2)
    u_sum = tf.reduce_sum(union, axis=-2)

    iou = tf.divide(i_sum, u_sum + 1e-6)

    miou = tf.reduce_mean(iou)

    return 1 - miou


def get_loss_weighted(pred, label, smpw):
    IoU_loss = Loss_Mean_IoU_Probabilities_Weighted(label, pred, smpw)
    # # classify_loss = tf.losses.sparse_softmax_cross_entropy(labels=label, logits=pred, weights=smpw)
    # weight_reg = tf.add_n(tf.get_collection('losses'))
    # classify_loss_mean = tf.reduce_mean(classify_loss, name='classify_loss_mean')
    # total_loss = classify_loss_mean + weight_reg
    tf.summary.scalar('classify loss', IoU_loss)
    tf.summary.scalar('total loss', IoU_loss)
    return IoU_loss

def get_loss_IoU_Weighted(pred, label, smpw):
    IoU_loss = Loss_Mean_IoU_Probabilities_Weighted(label, pred, smpw)
    # # classify_loss = tf.losses.sparse_softmax_cross_entropy(labels=label, logits=pred, weights=smpw)
    # weight_reg = tf.add_n(tf.get_collection('losses'))
    # classify_loss_mean = tf.reduce_mean(classify_loss, name='classify_loss_mean')
    # total_loss = classify_loss_mean + weight_reg
    tf.summary.scalar('classify loss', IoU_loss)
    tf.summary.scalar('total loss', IoU_loss)
    return IoU_loss


def get_loss_none_weighted(pred, label, smpw):
    IoU_loss = Loss_Mean_IoU_Probabilities(label, pred)
    # # classify_loss = tf.losses.sparse_softmax_cross_entropy(labels=label, logits=pred, weights=smpw)
    # weight_reg = tf.add_n(tf.get_collection('losses'))
    # classify_loss_mean = tf.reduce_mean(classify_loss, name='classify_loss_mean')
    # total_loss = classify_loss_mean + weight_reg
    tf.summary.scalar('classify loss', IoU_loss)
    tf.summary.scalar('total loss', IoU_loss)
    return IoU_loss


def get_loss_IoU(pred, label, smpw):
    IoU_loss = Loss_Mean_IoU_Probabilities(label, pred)
    # # classify_loss = tf.losses.sparse_softmax_cross_entropy(labels=label, logits=pred, weights=smpw)
    # weight_reg = tf.add_n(tf.get_collection('losses'))
    # classify_loss_mean = tf.reduce_mean(classify_loss, name='classify_loss_mean')
    # total_loss = classify_loss_mean + weight_reg
    tf.summary.scalar('classify loss', IoU_loss)
    tf.summary.scalar('total loss', IoU_loss)
    return IoU_loss


def get_loss_orig(pred, label, smpw):
    classify_loss = tf.losses.sparse_softmax_cross_entropy(labels=label, logits=pred, weights=smpw)
    weight_reg = tf.add_n(tf.get_collection('losses'))
    classify_loss_mean = tf.reduce_mean(classify_loss, name='classify_loss_mean')
    total_loss = classify_loss_mean + weight_reg
    tf.summary.scalar('classify loss', classify_loss)
    tf.summary.scalar('total loss', total_loss)
    return total_loss


# def get_loss(pred, label, smpw):
#     classify_loss = tf.losses.sparse_softmax_cross_entropy(labels=label, logits=pred, weights=smpw)
#     weight_reg = tf.add_n(tf.get_collection('losses'))
#     classify_loss_mean = tf.reduce_mean(classify_loss, name='classify_loss_mean')
#     total_loss = classify_loss_mean + weight_reg
#     tf.summary.scalar('classify loss', classify_loss)
#     tf.summary.scalar('total loss', total_loss)
#     return total_loss


if __name__ == '__main__':
    import pdb

    pdb.set_trace()

    with tf.Graph().as_default():
        inputs = tf.zeros((32, 2048, 3))
        net, _ = get_model(inputs, tf.constant(True), 10, 1.0)
        print(net)
