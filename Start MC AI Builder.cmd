@echo off
setlocal
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo The project Python environment is missing.
    echo See docs\m5-a-guide.md in this project to set it up.
    pause
    exit /b 1
)
cd /d "%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" -B -m tools.mcai_launcher
