from tqdm import tqdm

from .PointCONV_Segment import segment_point_clouds

from .process_combined_seg_results import process_combined_seg_results

import boto3

import os

import sys

import logging

def download_s3(bucket_name, s3_folder, local_dir=None):
    try:
        """
        Download the contents of a folder directory
        Args:
            bucket_name: the name of the s3 bucket
            s3_folder: the folder path in the s3 bucket
            local_dir: a relative or absolute directory path in the local file system
        """
        # aws_access_key_id=ak, aws_secret_access_key=sk
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(bucket_name)
        for obj in bucket.objects.filter(Prefix=s3_folder):
            target = obj.key if local_dir is None \
                else os.path.join(local_dir, os.path.relpath(obj.key, s3_folder))
            if not os.path.exists(os.path.dirname(target)):
                os.makedirs(os.path.dirname(target))
            if obj.key[-1] == '/':
                continue
            bucket.download_file(obj.key, target)
    except Exception as e:
        logging.info("Failure in downloading models from S3! Exception: {}".format(e))
        sys.exit(1)
    return True



def PointCONV(pre_pro_files,
              point_conv_conf,
              input_folder,
              output_folder,
              model_folder,
              point_format,
              file_version,
              ):
    pointCONV_model_dir = point_conv_conf['model_directory']
    pointconv_path = os.path.join(model_folder, point_conv_conf['model_directory'])

    if not os.path.exists(pointconv_path):
        logging.info(f"PointCONV model not found, downloading...")
        download_s3('sdai-model', f'lidar_ml/{pointCONV_model_dir}', pointconv_path)
    else:
        logging.info(f"Found exisiting PointCONV model, skipping")

    for pre_pro_file in tqdm(pre_pro_files):
        segment_point_clouds(point_conv_conf, pre_pro_file, model_folder)

        process_combined_seg_results(point_conv_conf,
                                     pre_pro_file,
                                     point_format,
                                     file_version,
                                     )

    return
