@echo off
REM Stop and disable Windows Update services
net stop wuauserv 2>nul
sc config wuauserv start=disabled

net stop UsoSvc 2>nul
sc config UsoSvc start=disabled

REM Disable Update Orchestrator
net stop bits 2>nul
sc config bits start=disabled

echo Windows Update services disabled.

REM data directory reference -- set this for your machine (holds las_in\, class_out\, model\)
SET DATA_ROOT=C:\path\to\class_exp
if not exist "%DATA_ROOT%\" ( echo ERROR: set DATA_ROOT to your data directory & pause & exit /b 2 )

REM data directory containing the las files. Relative to DATA_ROOT
SET DATA_LAS=las_in

REM Classification output dir. Relative to DATA_ROOT
SET CLASS_DIR=class_out

REM Model directory. Relative to DATA_ROOT
SET MODEL_DIR=model

REM path to code (mount for dev work).  Comment for production work.
REM Defaults to this .bat's own folder (the bundled tf1 classification code).
SET "CLASSIFICATION=%~dp0"
IF "%CLASSIFICATION:~-1%"=="\" SET "CLASSIFICATION=%CLASSIFICATION:~0,-1%"

REM path to ToolsConfig file (Use template as default, or specify path relative to INPUT_DIR)
SET INPUT_CONFIG=Classification/inputconfig.yml

REM Name of input dir to mount. Doesn't need to be touched
SET INPUT="input"

REM Container name
SET CONTAINER="750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1"

aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin 750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1

REM Run the workflow
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

REM Enable Windows Update services
echo Re-enabling Windows Update services...
sc config wuauserv start=auto
sc config UsoSvc start=auto
sc config bits start=auto

net start wuauserv 2>nul
net start UsoSvc 2>nul
net start bits 2>nul
echo Windows Update services enabled.
pause