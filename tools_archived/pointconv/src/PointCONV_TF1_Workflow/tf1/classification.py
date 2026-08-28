import os
import sys
import argparse
from datetime import datetime
import logging

from preprocessing.preprocessing import preprocessing

from PointCONV.PointCONV import PointCONV

import yaml

import gzip

import pickle


def read_yml(input_yml):
    """
    Reads a .yml file and parses its contents into a Python-compatible
    data structure. The function attempts to load the specified YAML file
    using a safe YAML loader. If an error occurs during the process, it
    logs the error message and terminates the program.

    :param input_yml: The path to the .yml file that needs to be read.
    :type input_yml: str
    :return: A Python dictionary or list representing the contents of
        the parsed .yml file.
    :rtype: dict or list
    """
    try:
        logging.info(f"Reading .yml file {input_yml}")
        with open(input_yml, 'r') as file:
            yml_file = yaml.safe_load(file)
    except Exception as e:
        logging.error(f"Error in reading .yml file {input_yml}!")
        logging.error(e)
        sys.exit(1)
    else:
        logging.info(f"Successfully read {input_yml}")
        return yml_file


def main(input_inputconfig, input_folder, output_folder, model_folder):
    """
    Main function for processing input data and performing specific operations based
    on configuration. This includes reading input configurations from a YAML file,
    executing preprocessing steps, managing file outputs, and conditionally applying
    PointConv operations based on the input configuration.

    :param input_inputconfig: Path to the YAML configuration file. This file defines
        processing settings, including preprocessing and PointConv configurations.
    :type input_inputconfig: str
    :param input_folder: Path to the input folder containing the data files to be
        processed.
    :type input_folder: str
    :param output_folder: Path to the folder where processing results and intermediate
        files will be written.
    :type output_folder: str
    :param model_folder: Path to the folder containing model files required for
        PointConv operations.
    :type model_folder: str
    :return: None
    """
    logging.info(f"Started processing")
    s_time = datetime.now()

    in_input_toolsconfig = read_yml(input_inputconfig)

    pre_pro_files_name = os.path.join(output_folder, "pre_pro_files.pklz")

    if os.path.exists(pre_pro_files_name):
        with gzip.open(pre_pro_files_name, 'rb') as f:
            pre_pro_files = pickle.load(f)
    else:
        pre_pro_files = preprocessing(in_input_toolsconfig['preprocessing'], input_folder, output_folder)

        os.makedirs(output_folder, exist_ok=True)

        with gzip.open(pre_pro_files_name, 'wb') as f:
            pickle.dump(pre_pro_files, f)

    if "PointConv" in in_input_toolsconfig and pre_pro_files is not None:
        point_format = in_input_toolsconfig['preprocessing']['OutputLAS_PointFormat']
        file_version = str(in_input_toolsconfig['preprocessing']['OutputLAS_FileFormat'])

        PointCONV(pre_pro_files,
                  in_input_toolsconfig['PointConv'],
                  input_folder,
                  output_folder,
                  model_folder,
                  point_format,
                  file_version,
                  )
    else:
        logging.error("No PointConv section in input_inputconfig.yml")

    e_time = datetime.now()
    t_time = e_time - s_time
    logging.info(f"**************************Processing Time Breakdown**************************")
    logging.info(f"***1.Total Processing Time: {t_time}*****************************************")

    return


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='This is the main script for point cloud classification',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--input_inputconfig", dest='in_inputconfig', required=True,
                        help='file location for the input data config .yml file')
    parser.add_argument("--input_folder", dest='in_folder', required=True,
                        help='input folder of las data')
    parser.add_argument("--out_folder", dest='out_folder', required=True,
                        help='output folder of classification results')
    parser.add_argument("--model_folder", dest='model_folder', required=True,
                        help='The folder containing the model directory')

    '''
    --input_inputconfig "inputconfig.yml"  --out_folder "/mnt/f/Pole_Vec_StandAlone/2026_01_26/las_class"  --input_folder "/mnt/f/Pole_Vec_StandAlone/2026_01_26/las_in"  --model_folder "/mnt/f/tmp/class_exp/models"
    '''

    args = parser.parse_args()

    start_time = datetime.now()
    log_dir = os.path.join(args.out_folder, 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_filename = start_time.strftime("%Y_%m_%d_%I_%M_%S_%p") + '.log'
    log_filename = os.path.splitext(os.path.basename(__file__))[0] + '_' + log_filename
    log_path = os.path.join(log_dir, log_filename)

    # Create logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)  # Set global logging level
    # Remove any existing handlers (important!)
    logger.handlers.clear()
    # Create formatter
    formatter = logging.Formatter('%(asctime)s | %(filename)s | %(message)s')
    # Create and add file handler
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    # Create and add stream handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(filename)s | %(message)s',
    #                     handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)],
    #                     filemode = "w")

    logging.info(f"Starting at {start_time.strftime('%H:%M:%S')}")
    logging.info(f"Command line = {' '.join(sys.argv)}")
    logging.info(f"Input inputConfig.yml = {args.in_inputconfig}")
    logging.info(f"Input folder = {args.in_folder}")
    logging.info(f"Output folder = {args.out_folder}")
    # logging.info(f"Output folder = {args.out_folder}")

    main(args.in_inputconfig, args.in_folder, args.out_folder, args.model_folder)

    end_time = datetime.now()

    logging.info(f"Complete at {end_time.strftime('%H:%M:%S')} : elapsed time = {end_time - start_time}\n")

    sys.exit(0)
