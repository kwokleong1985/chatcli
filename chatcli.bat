@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" (
    set "VIRTUAL_ENV=%~dp0.venv"
    set "PATH=%~dp0.venv\Scripts;%PATH%"
    set "PYTHONPATH=%~dp0;%PYTHONPATH%"
    "%~dp0.venv\Scripts\python.exe" -m chatcli %*
) else (
    set "PYTHONPATH=%~dp0;%PYTHONPATH%"
    python -m chatcli %*
)
