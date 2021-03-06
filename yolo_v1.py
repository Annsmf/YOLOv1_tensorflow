import argparse
import gzip
import os
import sys
import time
import cv2
import numpy
import tensorflow as tf
from PIL import Image
from six.moves import urllib
from six.moves import xrange
from nets import nets_factory


flags = tf.app.flags
flags.DEFINE_integer("epoch", 25, "Epoch to train [25]")
flags.DEFINE_integer("S", 7, "cut the img to S*S grids[7]")
flags.DEFINE_integer("num_classes", 2, "number of classes [2]")
flags.DEFINE_integer("B", 2, "number of bboxs for one grid to predict [2]")
flags.DEFINE_float("learning_rate", 0.001, "Learning rate of for d network [0.0001]")
flags.DEFINE_float("alpha", 0.1, "alpha of leaky relu [0.1]")
flags.DEFINE_float("nms_threshold", 0.5, "threshold of nms [0.5]")
flags.DEFINE_float("prob_threshold", 0.25, "probablity threshold of test [0.25]")
flags.DEFINE_float("coordinate_weight", 5, "weight of coordinate regression in loss function [5]")
flags.DEFINE_float("noobj_weight", 0.5, "weight of confidence regression in loss function when there is no obj in grid [0.5]")
flags.DEFINE_integer("batch_size", 1, "The size of batch images [128]")
flags.DEFINE_integer("img_size", 224, "image size [224]")
flags.DEFINE_integer("channel_dim", 3, "Dimension of image color [3]")
flags.DEFINE_string("model_name", 'inception_v4', "which model to use")
flags.DEFINE_string("img_pattern", 'jpg', "jpg or png")
flags.DEFINE_integer("save_summary_step", 100, "save summary per [] steps [100]")
flags.DEFINE_integer("save_model_step", 100, "save model per [] steps [100]")
flags.DEFINE_integer("log_loss_step", 100, "log loss information per [] steps [100]")
flags.DEFINE_string("checkpoint_dir", '/home/yy/yolo_/ckpt', "Directory name to save the checkpoints")
flags.DEFINE_string("tensorboard_dir", '/home/yy/yolo_/tb', "Directory name to save the tensorboard")
flags.DEFINE_string("train_dir", '/home/yy/yolo_/train', "Directory name to train images")
flags.DEFINE_string("train_label", '/home/yy/yolo_/label', "Directory name to train labels")
flags.DEFINE_string("test_res_dir", None, "Directory name to save test images")
flags.DEFINE_string("test_data", None, "Directory name to test images")
flags.DEFINE_string("test_label", None, "Directory name to test labels")
flags.DEFINE_boolean("is_test", False, "True for testing, False for training [False]")
FLAGS = flags.FLAGS

slim = tf.contrib.slim
CLASSES_NAME = ["DaLai","NonDaLai"]


def nms(dets, thresh):
  """Non maximum suppression"""
  """code from rbg/py-faster-rcnn"""
  x1 = dets[:, 0]
  y1 = dets[:, 1]
  x2 = dets[:, 2]
  y2 = dets[:, 3]
  scores = dets[:, 4]

  areas = (x2 - x1 + 1) * (y2 - y1 + 1)
  order = scores.argsort()[::-1]

  keep = []
  while order.size > 0:
    i = order[0]
    keep.append(i)
    xx1 = numpy.maximum(x1[i], x1[order[1:]])
    yy1 = numpy.maximum(y1[i], y1[order[1:]])
    xx2 = numpy.minimum(x2[i], x2[order[1:]])
    yy2 = numpy.minimum(y2[i], y2[order[1:]])

    w = numpy.maximum(0.0, xx2 - xx1 + 1)
    h = numpy.maximum(0.0, yy2 - yy1 + 1)
    inter = w * h
    ovr = inter / (areas[i] + areas[order[1:]] - inter)

    inds = numpy.where(ovr <= thresh)[0]
    order = order[inds + 1]

  return keep

def get_results(output):
  results = []
  classes = []
  probs = numpy.ndarray(shape=[FLAGS.num_classes,])
  for p in range(FLAGS.B):
    for j in range(4 + p*5, FLAGS.S*FLAGS.S*(FLAGS.B*5+FLAGS.num_classes), FLAGS.B*5+FLAGS.num_classes):
      for i in range(FLAGS.num_classes):
        probs[i] = output[0][j] * output[0][j + 1+ (FLAGS.B-1-p)*5 + i]

      cls_ind = probs.argsort()[::-1][0]
      if probs[cls_ind] > FLAGS.prob_threshold:
        results.append([output[0][j-4] - output[0][j-2]/2, output[0][j-3] - output[0][j-3]/2, output[0][j-4] + output[0][j-2]/2, output[0][j-3] + output[0][j-3]/2, probs[cls_ind]])
        classes.append(cls_ind)

  res = numpy.array(results).astype(numpy.float32)
  if len(res) != 0:
    keep = nms(res, FLAGS.nms_threshold)
    results_ = []
    classes_ = []
    for i in keep:
      results_.append(results[i])
      classes_.append(classes[i])

    return results_,classes_
  else:
    return [],[]

def show_results(img_path, results, classes):
  img = cv2.imread(img_path).copy()
  if len(results) != 0:
    for i in range(len(results)):
      x1 = int(results[i][0]*img.shape[1])
      y1 = int(results[i][1]*img.shape[0])
      x2 = int(results[i][2]*img.shape[1])
      y2 = int(results[i][3]*img.shape[0])
      score = results[i][4]
      cv2.rectangle(img, (x1,y1), (x2,y2), (0,255,0), 2)
      cv2.putText(img, CLASSES_NAME[classes[i]] + ' : %.2f' % results[i][4], (x1+5,y1-7), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 2)

  cv2.imwrite(FLAGS.test_res_dir + '/' + img_path.split('/')[-1], img)

def get_next_minibatch(offset, path_list):
  if offset+FLAGS.batch_size > len(path_list):
    random.shuffle(path_list)
    return path_list[:FLAGS.batch_size]
  else:
    return path_list[offset:offset+FLAGS.batch_size]

def extract_data_yolo(path_list, train=True):
  if train:
    data = numpy.ndarray(shape=(len(path_list),FLAGS.img_size,FLAGS.img_size,FLAGS.channel_dim),dtype=numpy.float32)

    for i in range(len(path_list)):
      img = Image.open(FLAGS.train_dir+'/'+path_list[i]+'.'+FLAGS.img_pattern)
      img_resize = img.resize((FLAGS.img_size,FLAGS.img_size))
      data[i] = numpy.array(img_resize).astype(numpy.float32).reshape(FLAGS.img_size,FLAGS.img_size,FLAGS.channel_dim)

    data = (data - 127.5) / 127.5
    return data
  else:
    data = numpy.ndarray(shape=(1,FLAGS.img_size,FLAGS.img_size,FLAGS.channel_dim), dtype=numpy.float32)
    img = Image.open(path_list)
    img_resize = img.resize((FLAGS.img_size,FLAGS.img_size))
    data = numpy.array(img_resize).astype(numpy.float32).reshape(1,FLAGS.img_size,FLAGS.img_size,FLAGS.channel_dim)
    data = (data - 127.5) / 127.5
    return data

def iou(box1,box2):
  tb = min(box1[0]+0.5*box1[2],box2[0]+0.5*box2[2])-max(box1[0]-0.5*box1[2],box2[0]-0.5*box2[2])
  lr = min(box1[1]+0.5*box1[3],box2[1]+0.5*box2[3])-max(box1[1]-0.5*box1[3],box2[1]-0.5*box2[3])
  if tb < 0 or lr < 0 : intersection = 0
  else : intersection =  tb*lr
  return intersection / (box1[2]*box1[3] + box2[2]*box2[3] - intersection)


def extract_labels_yolo(path_list, train=True):
  if train:
    root = FLAGS.train_label
  else:
    root = FLAGS.test_labels
  labels = numpy.ndarray(shape=(len(path_list),FLAGS.S*FLAGS.S*(FLAGS.B*5+FLAGS.num_classes)), dtype=numpy.float32)
  for i in range(labels.shape[0]):
    for j in range(labels.shape[1]):
      if j%(FLAGS.B*5+FLAGS.num_classes) == 0 or j%(FLAGS.B*5+FLAGS.num_classes) == 5:
        labels[i][j] = 1.00001
      else:
        labels[i][j] = 0
  for i in range(len(path_list)):
    with open(root + '/' + path_list[i] + '.txt',"r") as f:
      lines = f.readlines()
      for j in range(len(lines)):
        data = lines[j].split()
        col_no = int(float(data[1])*FLAGS.img_size/(FLAGS.img_size/FLAGS.S)+1)
        row_no = int(float(data[2])*FLAGS.img_size/(FLAGS.img_size/FLAGS.S)+1)
        grid_no = (row_no-1)*FLAGS.S+col_no
        # labels[i,(B*5+CLASSES)*grid_no-1] = float(data[0])
        labels[i,(FLAGS.B*5+FLAGS.num_classes)*grid_no-FLAGS.num_classes + int(data[0])] = 1
        for k in range(FLAGS.B):
          labels[i,(FLAGS.B*5+FLAGS.num_classes)*(grid_no-1) + 5*k] = float(data[1])
          labels[i,(FLAGS.B*5+FLAGS.num_classes)*(grid_no-1) + 5*k + 1] = float(data[2])
          labels[i,(FLAGS.B*5+FLAGS.num_classes)*(grid_no-1) + 5*k + 2] = float(data[3])
          labels[i,(FLAGS.B*5+FLAGS.num_classes)*(grid_no-1) + 5*k + 3] = float(data[4])
          labels[i,(FLAGS.B*5+FLAGS.num_classes)*(grid_no-1) + 5*k + 4] = 1

  return labels

def loss_func_yolo(output, label):
  res = 0

  for i in range(FLAGS.batch_size):
    for j in range(0, FLAGS.S*FLAGS.S*(FLAGS.B*5+FLAGS.num_classes), FLAGS.B*5+FLAGS.num_classes):
      highest_bbox = output[i][j+4]-output[i][j+9]
      """here we only compute the loss of bbox which have the highest confidence"""
      """we use tf.sign(tf.maximum(highest_bbox,0)) to do that"""

      res += FLAGS.coordinate_weight * tf.sign(tf.maximum(highest_bbox,0)) * tf.sign(label[i][j+2]) * (
                                                             tf.square(output[i][j] - label[i][j]) + 
                                                             tf.square(output[i][j+1]-label[i][j+1]) + 
                                                             tf.square(tf.sqrt(output[i][j+2])-tf.sqrt(label[i][j+2])) + 
                                                             tf.square(tf.sqrt(output[i][j+3])-tf.sqrt(label[i][j+3])))

      res += tf.sign(tf.maximum(highest_bbox,0)) * tf.sign(label[i][j+2]) * (tf.square(output[i][j+4] - label[i][j+4]))

      res += FLAGS.noobj_weight * tf.sign(tf.maximum(highest_bbox,0)) * tf.sign(tf.floor(label[i][j])) * (tf.square(output[i][j+4] - label[i][j+4]))

      res += FLAGS.coordinate_weight * tf.sign(tf.maximum(-highest_bbox,0)) * tf.sign(label[i][j+7]) * (
                                                              tf.square(output[i][j+5] - label[i][j+5]) + 
                                                              tf.square(output[i][j+6]-label[i][j+6]) + 
                                                              tf.square(tf.sqrt(output[i][j+7])-tf.sqrt(label[i][j+7])) + 
                                                              tf.square(tf.sqrt(output[i][j+8])-tf.sqrt(label[i][j+8])))

      res += tf.sign(tf.maximum(-highest_bbox,0)) * tf.sign(label[i][j+7]) * (tf.square(output[i][j+9] - label[i][j+9]))

      res += FLAGS.noobj_weight * tf.sign(tf.maximum(-highest_bbox,0)) * tf.sign(tf.floor(label[i][j+5])) * (tf.square(output[i][j+9] - label[i][j+9]))

      res += tf.sign(label[i][j+7]) * (tf.square(output[i][j+10] - label[i][j+10]) + tf.square(output[i][j+11] - label[i][j+11]))

  return res/FLAGS.batch_size

def test_from_dir(imgdir,display_loss=False):
  network_fn = nets_factory.get_network_fn(FLAGS.model_name,
    FLAGS.S*FLAGS.S*(FLAGS.B*5+FLAGS.num_classes),
    is_training=False)
  with tf.Session() as sess:
    tf.global_variables_initializer().run()
    saver = tf.train.Saver()
    print("Reading checkpoints...")

    ckpt = tf.train.get_checkpoint_state(FLAGS.checkpoint_dir)
    if ckpt and ckpt.model_checkpoint_path:
      ckpt_name = os.path.basename(ckpt.model_checkpoint_path)
      saver.restore(sess, os.path.join(FLAGS.checkpoint_dir, ckpt_name))
      print("Success to read {}".format(ckpt_name))
    else:
      print("Failed to find a checkpoint")

    #saver.restore(sess, FLAGS.checkpoint_dir)

    for root, dirs, files in os.walk(imgdir):
      for file in files:
        img = os.path.join(root, file)
        data = extract_data_yolo(img, train=False)
        output,_ = network_fn(data)
        out = sess.run(output)
        results,classes = get_results(out)
        show_results(img, results, classes)

def main(_):
  train_img_list = []
  for rt,dirs,filenames in os.walk(FLAGS.train_dir):
    for filename in filenames:
      train_img_list.append(filename[:-4])

  train_size = len(train_img_list)
  numpy.random.shuffle(train_img_list)
  train_data_node = tf.placeholder(
    tf.float32,
    shape=(FLAGS.batch_size, FLAGS.img_size, FLAGS.img_size, FLAGS.channel_dim))
  train_labels_node = tf.placeholder(tf.float32,
    shape=(FLAGS.batch_size, FLAGS.S*FLAGS.S*(FLAGS.B*5+FLAGS.num_classes)))

  network_fn = nets_factory.get_network_fn(FLAGS.model_name,
    FLAGS.S*FLAGS.S*(FLAGS.B*5+FLAGS.num_classes),
    is_training=True)

  logits,_ = network_fn(train_data_node)
  logtis = tf.nn.sigmoid(logits)
  loss = loss_func_yolo(logits, train_labels_node)

  batch = slim.create_global_step()

  optimizer = tf.train.AdamOptimizer(FLAGS.learning_rate).minimize(loss, global_step=batch)

  tf.summary.scalar("loss", loss)
  merged_summary = tf.summary.merge_all()
  with tf.Session() as sess:

    tf.global_variables_initializer().run()
    saver = tf.train.Saver()
    print('Initialized!')
    writer = tf.summary.FileWriter(FLAGS.tensorboard_dir, sess.graph)

    print("loding models...")
    ckpt = tf.train.get_checkpoint_state(FLAGS.checkpoint_dir)
    if ckpt and ckpt.model_checkpoint_path:
      ckpt_name = os.path.basename(ckpt.model_checkpoint_path)
      saver.restore(sess, os.path.join(FLAGS.checkpoint_dir, ckpt_name))
      print("Success to load {}".format(ckpt_name))
    else:
      print("Failed to find a checkpoint")

    start_time = time.time()
    for step in xrange(int(FLAGS.epoch * train_size) // FLAGS.batch_size):
      offset = (step * FLAGS.batch_size) % (train_size - FLAGS.batch_size)
      batch_data = extract_data_yolo(get_next_minibatch(offset, train_img_list))
      batch_labels = extract_labels_yolo(get_next_minibatch(offset, train_img_list))

      feed_dict = {train_data_node: batch_data,
                   train_labels_node: batch_labels}

      _, los, summary = sess.run([optimizer, loss, merged_summary], feed_dict=feed_dict)

      if step%FLAGS.log_loss_step == 0:
        end_time = time.time()
        print('loss: %.6f time: %.2f' % (los, end_time-start_time))
        start_time = time.time()
      if step%FLAGS.save_summary_step == 0:
        writer.add_summary(summary, step)
      if step%FLAGS.save_model_step == 0:
        save_path = saver.save(sess, os.path.join(FLAGS.checkpoint_dir, "yolo.model"), global_step=step)

if __name__ == '__main__':
  if not FLAGS.is_test:
    tf.app.run()
  else:
    test_from_dir(FLAGS.test_data)
