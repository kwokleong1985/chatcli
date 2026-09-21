@echo off
echo Installing ChatCLI dependencies...
pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo.
    echo Install failed. Make sure Python and pip are available.
    pause
    exit /b 1
)
echo.
echo Done. Run chatcli.bat to start.
pause
