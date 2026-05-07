@echo off
setlocal EnableExtensions

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv was not found. Run setup.bat first.
    pause
    exit /b 1
)

start "GEM-X Shell" cmd /k "cd /d \"%REPO_DIR%\" && call .venv\Scripts\activate.bat && title GEM-X Shell"
exit /b 0
