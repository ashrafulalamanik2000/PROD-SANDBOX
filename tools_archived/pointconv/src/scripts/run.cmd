@echo off
REM PointCONV classification - deterministic one-path Windows entry point.
REM   run.cmd <las|laz|directory> [--run-dir <run>] [flags...]
setlocal
set "HERE=%~dp0"
if not defined POINTCONV_PY if exist "%USERPROFILE%\.conda\envs\gdal_env\python.exe" set "POINTCONV_PY=%USERPROFILE%\.conda\envs\gdal_env\python.exe"
if not defined POINTCONV_PY set "POINTCONV_PY=python"
if "%~1"=="" (
  echo Usage: %~nx0 ^<las^|laz^|directory^> [--run-dir ^<run^>] [flags...]
  exit /b 2
)
"%POINTCONV_PY%" "%HERE%run_pipeline.py" %*
exit /b %ERRORLEVEL%
