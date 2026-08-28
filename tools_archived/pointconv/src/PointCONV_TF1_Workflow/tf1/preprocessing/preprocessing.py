
from glob import glob

import os

from .voxelize_files import voxelize_las_files

# from tqdm import tqdm
#
# import numpy as np
#
# import laspy

import logging


def preprocessing(preprocessing_config, input_folder, output_folder):

    las_files = glob(os.path.join(input_folder, "*.las"))

    if len(las_files) == 0:
        logging.error(f"No las files found in {input_folder}!")
        return None

    else:
        logging.info(f"Found {len(las_files)} las files in {input_folder}")

    pre_pro_files = voxelize_las_files(
        preprocessing_config,
        input_folder,
        output_folder,
        las_files,
    )

    return pre_pro_files