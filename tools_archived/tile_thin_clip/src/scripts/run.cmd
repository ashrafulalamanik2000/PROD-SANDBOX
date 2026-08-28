@echo off
REM Windows CMD entrypoint. Delegates to bash (Git Bash / WSL).
REM Install Git for Windows if bash is not found: https://git-scm.com/download/win

setlocal

where bash >NUL 2>&1
if not errorlevel 1 (
    bash "%~dp0run.sh" %*
    exit /b %ERRORLEVEL%
)

echo ERROR: bash not found on PATH.
echo Install Git for Windows ^(https://git-scm.com/download/win^) or run from WSL,
echo then re-run this command.
exit /b 1
