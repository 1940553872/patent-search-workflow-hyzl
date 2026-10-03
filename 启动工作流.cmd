@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "CNPS_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CNPS_PYTHON%" (
  "%CNPS_PYTHON%" run.py serve --open
) else (
  python run.py serve --open
)
if errorlevel 1 pause
