@echo off
REM ===========================================================================
REM Docker launcher for the FINE-TUNED PointCONV TF1 6-class model
REM   PointCONV_model_6class_Mobile_v0.0.10
REM Mirrors Docker_Run_Classification.bat but uses inputconfig_finetune.yml.
REM
REM Behavior:
REM - Mounts %DATA_ROOT% into the container, plus the host's ~/.aws so the
REM   classification.py wrapper can download the fine-tuned model from
REM     s3://sdai-model/lidar_ml/PointCONV_model_6class_Mobile_v0.0.10
REM   on first use if the directory is missing under %DATA_ROOT%\%MODEL_DIR%.
REM - Uses inputconfig_finetune.yml so model_directory points at the
REM   fine-tuned model name.
REM ===========================================================================

REM Stop and disable Windows Update services (matches the legacy launcher).
net stop wuauserv 2>nul
sc config wuauserv start=disabled

net stop UsoSvc 2>nul
sc config UsoSvc start=disabled

net stop bits 2>nul
sc config bits start=disabled

echo Windows Update services disabled.

REM ---------------------------------------------------------------------------
REM Configure these for your machine before running.
REM ---------------------------------------------------------------------------

REM Data directory reference -- set this for your machine (holds las_in\, class_out\, model\)
SET DATA_ROOT=C:\path\to\class_exp
if not exist "%DATA_ROOT%\" ( echo ERROR: set DATA_ROOT to your data directory & pause & exit /b 2 )

REM Data directory containing the LAS files to classify (relative to DATA_ROOT).
SET DATA_LAS=las_in

REM Classification output dir (relative to DATA_ROOT).
SET CLASS_DIR=class_out

REM Model directory (relative to DATA_ROOT). The wrapper will look for
REM <DATA_ROOT>\<MODEL_DIR>\PointCONV_model_6class_Mobile_v0.0.10; if absent,
REM it will pull it from S3 (sdai-model bucket, lidar_ml prefix).
SET MODEL_DIR=model

REM Path to local Classification code to mount as /app/Classification.
REM Comment out for production (image-baked) runs.
REM Defaults to this .bat's own folder (the bundled tf1 classification code).
SET "CLASSIFICATION=%~dp0"
IF "%CLASSIFICATION:~-1%"=="\" SET "CLASSIFICATION=%CLASSIFICATION:~0,-1%"

REM Path to the YAML config inside the container.
REM (relative to /app/Classification when CLASSIFICATION is mounted)
SET INPUT_CONFIG=Classification/inputconfig_finetune.yml

REM Name of input dir to mount inside the container.
SET INPUT="input"

REM Container image.
SET CONTAINER="750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1"

REM Authenticate to ECR so --pull=always can fetch the image if needed.
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin 750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1

REM Run the workflow.
IF DEFINED CLASSIFICATION (

docker run -it --shm-size=8gb --gpus all --pull=always^
 -v "%DATA_ROOT%:/app/%INPUT%"^
 -v "%CLASSIFICATION%:/app/Classification"^
 -v "%homedrive%%homepath%/.aws:/root/.aws"^
 %CONTAINER%^
 python /app/Classification/classification.py --input_inputconfig %INPUT_CONFIG% --input_folder %INPUT%/%DATA_LAS% --out_folder %INPUT%/%CLASS_DIR% --model_folder %INPUT%/%MODEL_DIR%

) ELSE (

docker run -it --shm-size=8gb --gpus all --pull=always^
 -v "%DATA_ROOT%:/app/%INPUT%"^
 -v "%homedrive%%homepath%/.aws:/root/.aws"^
 %CONTAINER%^
 python /app/Classification/classification.py --input_inputconfig %INPUT_CONFIG% --input_folder %INPUT%/%DATA_LAS% --out_folder %INPUT%/%CLASS_DIR% --model_folder %INPUT%/%MODEL_DIR%

)

REM Re-enable Windows Update services.
echo Re-enabling Windows Update services...
sc config wuauserv start=auto
sc config UsoSvc start=auto
sc config bits start=auto

net start wuauserv 2>nul
net start UsoSvc 2>nul
net start bits 2>nul
echo Windows Update services enabled.
pause
