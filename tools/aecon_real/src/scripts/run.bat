@echo off
setlocal
if "%~1"=="" (
  echo Usage: %~nx0 ^<DATA_DIR^> [aecon.py flags...]
  exit /b 2
)
if defined AECON_HOST_PY (
  set "PY=%AECON_HOST_PY%"
) else if exist "%~dp0..\myenv\python.exe" (
  set "PY=%~dp0..\myenv\python.exe"
) else (
  set "PY=python"
)
"%PY%" "%~dp0aecon.py" %*
exit /b %ERRORLEVEL%
