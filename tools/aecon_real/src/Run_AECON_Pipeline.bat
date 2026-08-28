@echo off
setlocal
if "%~1"=="" (
  echo Usage: %~nx0 ^<DATA_DIR^> [--stages all] [--crs EPSG:26917] [--buffer 45] [--preflight-only] [--json]
  exit /b 2
)
if not defined AECON_PY set "AECON_PY=python"
"%AECON_PY%" "%~dp0scripts\launch.py" %*
exit /b %ERRORLEVEL%
