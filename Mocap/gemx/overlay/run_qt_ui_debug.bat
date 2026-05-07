@echo off
setlocal EnableExtensions

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

if not exist ".venv\Scripts\python.exe" (
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

echo [INFO ] Launching GEM-X Qt UI in debug mode...
echo [INFO ] Console output is preserved for launcher/runtime errors.
echo.

".venv\Scripts\python.exe" scripts\ui\gem_qt_launcher.py %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [ERROR] Qt UI exited with code %RC%.
) else (
    echo [INFO ] Qt UI exited cleanly.
)

echo.
echo Press any key to close this console window.
pause >nul
exit /b %RC%
