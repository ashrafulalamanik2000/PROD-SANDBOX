@echo off
REM ============================================================================
REM  Run PointCONV  --  classify a folder of LAS/LAZ with PointCONV, GPU-tuned.
REM
REM  Optimized automatically for your GPU (RTX 4090 / Ada and RTX 5080 / Blackwell
REM  are both detected) and for LARGE clouds (streaming tile builder keeps memory
REM  bounded). On a Blackwell GPU the first run JIT-compiles PTX (~a few minutes,
REM  cached afterward) -- this is expected.
REM
REM  USAGE
REM    run_pointconv.bat <INPUT_FOLDER> <OUTPUT_FOLDER>
REM    run_pointconv.bat <INPUT_FOLDER> <OUTPUT_FOLDER> --check-models
REM    run_pointconv.bat <INPUT_FOLDER> <OUTPUT_FOLDER> --model <MODEL_NAME>
REM    (drag the input folder onto this .bat, or run with no args to be prompted.)
REM
REM    --check-models   check S3 for the latest 6-class Mobile model and use it
REM                     (downloads it if you don't have it locally)
REM    --model NAME     use a specific model directory from models\ or S3
REM
REM  OUTPUT: <OUTPUT_FOLDER>\combined_outputs\<source>_tf1_pointconv_combined_0p1m.las
REM
REM  REQUIRES: Docker Desktop + an NVIDIA GPU + Git Bash (bash on PATH) + the AWS
REM  CLI with credentials (first run pulls the mmworkflow image / models from ECR/S3).
REM ============================================================================
setlocal
set "HERE=%~dp0"

where bash >nul 2>&1
if not %ERRORLEVEL%==0 (
    echo ERROR: this tool needs Git Bash ^(bash on PATH^). Install "Git for Windows".
    pause & exit /b 9
)
docker info >nul 2>&1
if not %ERRORLEVEL%==0 (
    echo ERROR: Docker Desktop is not running. Start it and re-run.
    pause & exit /b 1
)
where nvidia-smi >nul 2>&1
if not %ERRORLEVEL%==0 (
    echo WARNING: nvidia-smi not found; GPU auto-tuning will fall back to safe defaults.
)

REM Hand the worker to Git Bash. run_pointconv.sh is stored with LF endings
REM (.gitattributes: *.sh eol=lf) so bash runs it directly.
bash "%HERE%run_pointconv.sh" %*
set "RC=%ERRORLEVEL%"

echo.
echo ============================================================================
echo  run_pointconv finished (exit %RC%).
if "%RC%"=="0" echo  Classified clouds are in your output folder under  combined_outputs\
echo ============================================================================
pause
endlocal & exit /b %RC%
