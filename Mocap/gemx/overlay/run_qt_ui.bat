@echo off
setlocal EnableExtensions

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] .venv was not found. Run setup.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PySide6 is not installed in .venv.
    echo [INFO ] Install it with:
    echo        .venv\Scripts\python.exe -m pip install -e ".[ui]"
    pause
    exit /b 1
)

start "GEM-X Qt UI" ".venv\Scripts\pythonw.exe" scripts\ui\gem_qt_launcher.py %*
exit /b 0
