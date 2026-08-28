@echo off
REM ============================================================
REM  Build_And_Push_Image.bat
REM  Builds the AECON Docker image and pushes to the shared
REM  ECR registry (same one used by mmworkflow).
REM
REM  Run this once when the image changes. The Run_AECON_Pipeline.bat
REM  will then pull it on every run (--pull=always).
REM ============================================================

SET REGISTRY=750433818015.dkr.ecr.us-west-2.amazonaws.com
SET IMAGE=aecon
SET TAG=latest
SET FULL_IMAGE=%REGISTRY%/%IMAGE%:%TAG%

echo.
echo ============================================================
echo  Build + Push AECON image
echo  Target: %FULL_IMAGE%
echo ============================================================

REM -- Activate portable Dockerfile (the v2 with pano included) --
IF EXIST "%~dp0Dockerfile.v2" (
    IF EXIST "%~dp0Dockerfile" (
        echo Backing up existing Dockerfile -^> Dockerfile.v1.bak
        copy /Y "%~dp0Dockerfile" "%~dp0Dockerfile.v1.bak" >nul
    )
    copy /Y "%~dp0Dockerfile.v2" "%~dp0Dockerfile" >nul
)
IF EXIST "%~dp0requirements.v2.txt" (
    IF EXIST "%~dp0requirements.txt" (
        copy /Y "%~dp0requirements.txt" "%~dp0requirements.v1.bak" >nul
    )
    copy /Y "%~dp0requirements.v2.txt" "%~dp0requirements.txt" >nul
)

REM -- ECR login --
echo.
echo ECR login...
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin %REGISTRY%
if %ERRORLEVEL% NEQ 0 goto :ecr_failed

REM -- Build --
echo.
echo Building image...
docker build -t %FULL_IMAGE% "%~dp0."
if %ERRORLEVEL% NEQ 0 goto :build_failed

REM -- Push --
echo.
echo Pushing to ECR...
docker push %FULL_IMAGE%
if %ERRORLEVEL% NEQ 0 goto :push_failed

echo.
echo ============================================================
echo  DONE. Image available at %FULL_IMAGE%
echo ============================================================
exit /b 0

:ecr_failed
echo ERROR: ECR login failed.
pause
exit /b 1
:build_failed
echo ERROR: docker build failed.
pause
exit /b 1
:push_failed
echo ERROR: docker push failed.
pause
exit /b 1
