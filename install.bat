@echo off
setlocal
cd /d "%~dp0"

echo [1/2] Checking Python virtual environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo Failed to create virtual environment. Ensure Python 3 is installed and in PATH.
        pause
        exit /b 1
    )
)

echo [2/2] Installing ChatCLI dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo Setup complete. Run chatcli.bat to start.
pause
