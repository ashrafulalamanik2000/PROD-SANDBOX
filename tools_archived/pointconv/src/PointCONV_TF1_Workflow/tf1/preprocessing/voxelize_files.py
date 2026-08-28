import os
import logging

import numpy as np
from joblib import Parallel, delayed
from tqdm import tqdm

from .voxel_function import process_las_file_wrapper
from datetime import datetime


def voxelize_las_files(
        preprocessing_config,
        input_folder,
        output_folder,
        las_file_list,
):
    start_time = datetime.now()

    num_threads_file_voxelization = preprocessing_config['num_threads_file_voxelization']

    os.makedirs(output_folder, exist_ok=True)

    las_file_list_cnt = np.arange(len(las_file_list))

    unique_dir_name = []
    for cnt in range(len(las_file_list)):
        unique_dir_name.append(str(cnt))

    if num_threads_file_voxelization > 1 or num_threads_file_voxelization == -1:
        results = Parallel(n_jobs=num_threads_file_voxelization)(
            delayed(process_las_file_wrapper)(
                las_file_list[cnt],
                preprocessing_config,
                input_folder,
                output_folder,
            ) for cnt in tqdm(las_file_list_cnt))
    else:
        results = []
        for cnt in tqdm(las_file_list_cnt):
            results.append(
                process_las_file_wrapper(
                    las_file_list[cnt],
                    preprocessing_config,
                    input_folder,
                    output_folder,
                ))

    end_time = datetime.now()
    delta_time = end_time - start_time
    logging.info(
        f"PointCONV vox completed at {end_time.strftime('%H:%M:%S')} : elapsed time = {delta_time}\n")

    return results


'''
            las_file_location = os.path.join(root_folder, las_file_locations_vox[file_cnt])

--input_inputconfig "/mnt/g/Airborne_Model_Development/exp_2025_03_18/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/g/Airborne_Model_Development/exp_2025_03_18"

--input_inputconfig "/mnt/g/Airborne_Model_Training_Datasets/Raw/InputConfig_PointCloud_Learning_Data_2025_03_19.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/g/Airborne_Model_Training_Datasets/Raw"

--input_inputconfig "/mnt/g/Airborne_Model_Training_Datasets/Raw/InputConfig_PointCloud_Learning_Data_2025_03_25.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/g/Airborne_Model_Training_Datasets/Raw"

--input_inputconfig "/mnt/g/Airborne_Model_Training_Datasets/Raw/InputConfig_PointCloud_Learning_Data_2025_03_28.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/g/Airborne_Model_Training_Datasets/Raw"

--input_inputconfig "/mnt/g/Airborne_Model_Training_Datasets/Raw/InputConfig_PointCloud_Filter_2025_03_27.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/g/Airborne_Model_Training_Datasets/Raw"

--input_inputconfig "/mnt/g/Airborne_Model_Training_Datasets/Raw/InputConfig_PointCloud_Filter_2025_04_09.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/g/Airborne_Model_Training_Datasets/Raw"


--input_inputconfig "//mnt/g/AEP/InputConfig_PointCloud_2025_04_01.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/g/AEP"

--input_inputconfig "/mnt/d/Image_Prejection_Debug_WGI/SDAI/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_WGI_debug.yml   --input_folder "/mnt/d/Image_Prejection_Debug_WGI/SDAI"


--input_inputconfig "/mnt/g/Airborne_Model_Training_Datasets/Raw/InputConfig_PointCloud_Learning_Data_2025_04_09.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/g/Airborne_Model_Training_Datasets/Raw"

--input_inputconfig "/mnt/g/Airborne_Model_Training_Datasets/Raw/InputConfig_PointCloud_Learning_Data_2025_04_20.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_Training_Data_2025_04_20.yml   --input_folder "/mnt/g/Airborne_Model_Training_Datasets/Raw"
--input_inputconfig "/mnt/g/Airborne_Model_Training_Datasets/Raw/InputConfig_PointCloud_Learning_Data_2025_04_20_test.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_Training_Data_2025_04_20_test.yml   --input_folder "/mnt/g/Airborne_Model_Training_Datasets/Raw"

--input_inputconfig "/mnt/g/AEP/Data/03172025_LiDAR_Rerun/InputConfig_PointCloud_2025_04_14.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_test_1.yml   --input_folder "/mnt/g/AEP/Data/03172025_LiDAR_Rerun"

--input_inputconfig "/mnt/g/AEP/Data/03172025_LiDAR_Rerun/InputConfig_PointCloud_2025_04_14.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/g/AEP/Data/03172025_LiDAR_Rerun"

--input_inputconfig "/mnt/g/AEP/Data/03192025_LiDAR_Rerun/InputConfig_PointCloud_2025_04_17.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_test_2.yml   --input_folder "/mnt/g/AEP/Data/03192025_LiDAR_Rerun"

--input_inputconfig "/mnt/g/AEP/Data/03192025_LiDAR_Rerun/InputConfig_PointCloud_2025_04_17.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_verda_2025_05_12.yml   --input_folder "/mnt/g/AEP/Data/03192025_LiDAR_Rerun"

--input_inputconfig "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData/InputConfig_PointCloud_2025_05_20.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_GSI.yml   --input_folder "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData"

--input_inputconfig "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData/InputConfig_PointCloud_2025_05_20_desert_rural.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_GSI_Desert.yml   --input_folder "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData"

--input_inputconfig "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData/InputConfig_PointCloud_2025_05_20_Urban_Trans.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_GSI_Urban_Transmission.yml   --input_folder "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData"

--input_inputconfig "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData/InputConfig_PointCloud_2025_05_20_forest.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_GSI_Forest.yml   --input_folder "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData"
--input_inputconfig "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData/InputConfig_PointCloud_2025_05_20_forest_Dev.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_GSI_Forest.yml   --input_folder "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData"

--input_inputconfig "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData/InputConfig_PointCloud_2025_05_20_Urban_Dist_A.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_GSI_Urban_Dist_A.yml   --input_folder "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData"

--input_inputconfig "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData/InputConfig_PointCloud_2025_05_20_Urban_Dist_B.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_GSI_Urban_Dist_B.yml   --input_folder "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData"

--input_inputconfig "/mnt/s/Test_Datasets/LOOQ/InputConfig_PointCloud_2025_05_28.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_looq.yml   --input_folder "/mnt/s/Test_Datasets/LOOQ"


--input_inputconfig "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData/InputConfig_PointCloud_2025_05_20_Urban_Dist_A.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/s/Test_Datasets/GSI/GSI_SampleTestData"

--input_inputconfig "/mnt/g/debug/InputConfig_PointCloud_2025_06_30.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV.yml   --input_folder "/mnt/g/debug"

--input_inputconfig "/mnt/g/debug/InputConfig_PointCloud_2025_06_30.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_G5.yml   --input_folder "/mnt/g/debug"

--input_inputconfig "/mnt/s/Test_Datasets/Aethon_Sample_Transmission_Data/project_1/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/s/Test_Datasets/Aethon_Sample_Transmission_Data/project_1"

--input_inputconfig "/mnt/g/Test_Datasets/AEP1/p0/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/Test_Datasets/AEP1/p0"

--input_inputconfig "/mnt/g/Test_Datasets/AEP1/p1/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/Test_Datasets/AEP1/p1"

--input_inputconfig "/mnt/g/Test_Datasets/Arbormetrics-Fortis/Project_Area_90002/20250529_Drive2/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/Test_Datasets/Arbormetrics-Fortis/Project_Area_90002/20250529_Drive2"


--input_inputconfig "/mnt/g/Test_Datasets/Arbormetrics-Fortis-Testing/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_Only_PointCONV_G5.yml   --input_folder "/mnt/g/Test_Datasets/Arbormetrics-Fortis-Testing"

--input_inputconfig "/mnt/g/Test_Datasets/Arbormetrics-Fortis-Testing/Panoramic_Imagery/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/Test_Datasets/Arbormetrics-Fortis-Testing/Panoramic_Imagery"


--input_inputconfig "/mnt/s/Test_Datasets/AEP/tower_pole_test/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/s/Test_Datasets/AEP/tower_pole_test"

--input_inputconfig "/mnt/g/Test_Datasets/AEP1/p2/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/Test_Datasets/AEP1/p2"


--input_inputconfig "/mnt/g/Test_Datasets/AST/group_2/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_low_density.yml   --input_folder "/mnt/g/Test_Datasets/AST/group_2"

--input_inputconfig "/mnt/g/Test_Datasets/LOOQ/Pickett/Loveland/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/Test_Datasets/LOOQ/Pickett/Loveland"


--input_inputconfig "/mnt/s/data/AEP/t1/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "//mnt/s/data/AEP/t1"

--input_inputconfig "/mnt/s/data/AST/t2/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/s/data/AST/t2"



--input_inputconfig "/mnt/g/Test_Datasets/AEP1/p2/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/Test_Datasets/AEP1/p2"

--input_inputconfig "/mnt/s/Test_Datasets/AEP1/p2/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/s/Test_Datasets/AEP1/p2"

--input_inputconfig "/mnt/s/data/AST/t2/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/s/data/AST/t2"

--input_inputconfig "/mnt/h/group_5/InputConfig_PointCloud_AEP.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/h/group_5"

--input_inputconfig "/mnt/h/group_3/InputConfig_PointCloud_AEP.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/h/group_3"

--input_inputconfig "/mnt/h/group_2/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/h/group_2"

--input_inputconfig "/mnt/h/group_1/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/h/group_1"

--input_inputconfig "/mnt/h/group_1/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/h/group_1"

--input_inputconfig "/mnt/g/Mobile_Data/75dot1_m2_dec14/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_Mobile.yml   --input_folder "/mnt/g/Mobile_Data/75dot1_m2_dec14"

--input_inputconfig "/mnt/s/LOOQ/dev/pl_2/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/s/LOOQ/dev/pl_2"


--input_inputconfig "/mnt/g/Mobile_Data/75dot1_m2_dec14/group_10/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/Mobile_Data/75dot1_m2_dec14/group_10"

--input_inputconfig "/mnt/g/Mobile_Data/75dot1_m2_dec14/group_1/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/Mobile_Data/75dot1_m2_dec14/group_1"


--input_inputconfig "/mnt/g/LOOQ/VRHF_DOUGLAS/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/LOOQ/VRHF_DOUGLAS"


--input_inputconfig "/mnt/g/Mobile_Data/75dot1_m2_dec14/group_10/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup.yml   --input_folder "/mnt/g/Mobile_Data/75dot1_m2_dec14/group_10"


--input_inputconfig "/mnt/g/LOOQ/VRHF_DOUGLAS/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_LOOQ.yml   --input_folder "/mnt/g/LOOQ/VRHF_DOUGLAS"

--input_inputconfig "/mnt/g/LOOQ/ENCEPTA_LOOQ/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_LOOQ.yml   --input_folder "/mnt/g/LOOQ/ENCEPTA_LOOQ"

--input_inputconfig "/mnt/g/LOOQ/ENCEPTA_LOOQ_Run_2/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_LOOQ.yml   --input_folder "/mnt/g/LOOQ/ENCEPTA_LOOQ_Run_2"

--input_inputconfig "/mnt/g/LOOQ/ENCEPTA_LOOQ_Run_5/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_LOOQ.yml   --input_folder "/mnt/g/LOOQ/ENCEPTA_LOOQ_Run_5"

--input_inputconfig "/mnt/g/ENCEPTA_LOOQ_r5/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_LOOQ.yml   --input_folder "/mnt/g/ENCEPTA_LOOQ_r5"

--input_inputconfig "/mnt/g/ENCEPTA_LOOQ_r6/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_LOOQ.yml   --input_folder "/mnt/g/ENCEPTA_LOOQ_r6"

--input_inputconfig "/mnt/g/MediaShuttle/tile6/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/g/MediaShuttle/tile6"

--input_inputconfig "/mnt/g/le6/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_LOOQ.yml   --input_folder "/mnt/g/le6"

MediaShuttle

tile6

--input_inputconfig "/mnt/f/missing_cross_A/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/missing_cross_A"

--input_inputconfig "/mnt/f/walls/walls_Ex/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/walls/walls_Ex"


--input_inputconfig "/mnt/f/trans_A/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/trans_A"

--input_inputconfig "/mnt/f/t_triple_pole/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/t_triple_pole"

--input_inputconfig "/mnt/f/ground_error/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/ground_error"
--input_inputconfig "/mnt/f/missed_poles_3_unit_test/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/missed_poles_3_unit_test"


--input_inputconfig "/mnt/f/project_dir_debug/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/project_dir_debug"

--input_inputconfig "/mnt/f/UnitTestsFirmetek/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/UnitTestsFirmetek"
--input_inputconfig "/mnt/f/Claverack_t1/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/Claverack_t1"

--input_inputconfig "/mnt/f/9_Extracted_Network_1/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/9_Extracted_Network_1"

--input_inputconfig "/mnt/f/FIRMATEK/VERIZON_TEST/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/FIRMATEK/VERIZON_TEST"

--input_inputconfig "/mnt/f/LOOQ_Encepta/CHASBC0002A_10_15_20/20_METER/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_LOOQ.yml   --input_folder "/mnt/f/LOOQ_Encepta/CHASBC0002A_10_15_20/20_METER"

--input_inputconfig "/mnt/f/LOOQ_Encepta/BULK0001C/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_LOOQ.yml   --input_folder "/mnt/f/LOOQ_Encepta/BULK0001C"


/mnt/f/9_Extracted_Network_1

--input_inputconfig "/mnt/f/FIRMATEK/CHINO_HILLS_POLES/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/FIRMATEK/CHINO_HILLS_POLES"

--input_inputconfig "/mnt/f/FIRMATEK/Santa_Ana_1/InputConfig.yml"  --input_toolsconfig ToolsConfig_Template_PointCONV_Cleanup_FIRMATEK.yml   --input_folder "/mnt/f/FIRMATEK/Santa_Ana_1"

'''
