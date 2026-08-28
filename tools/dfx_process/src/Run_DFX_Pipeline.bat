@echo off
setlocal
if "%~1"=="" (
  echo Usage: %~nx0 ^<MAINDIR^> [--stages all] [--epsg 26914] [--pn ^<id^>] [--preflight-only] [--json]
  exit /b 2
)
if not defined DFX_PY set "DFX_PY=python"
"%DFX_PY%" "%~dp0scripts\launch.py" %*
exit /b %ERRORLEVEL%
