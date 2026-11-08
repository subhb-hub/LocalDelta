@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [LocalDelta] Python 3.10+ was not found on PATH.
    echo Install Python or run "python localdelta.py gui" in your configured terminal.
    pause
    exit /b 1
)
python localdelta.py gui
if errorlevel 1 (
    echo [LocalDelta] The desktop window could not be started.
    echo Try "python localdelta.py menu" for the terminal interface.
    pause
)
endlocal
