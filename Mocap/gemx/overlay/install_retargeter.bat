@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv was not found. Run setup.bat first.
    exit /b 1
)

set "RETARGET_DIR=third_party\soma-retargeter"
set "ENTRY_COUNT="

echo.
echo [1/4] Syncing soma-retargeter submodule URL...
git config submodule.third_party/soma-retargeter.url https://github.com/NVIDIA/soma-retargeter.git >nul 2>&1
git submodule sync -- third_party/soma-retargeter
if errorlevel 1 (
    echo [WARN ] submodule sync failed. Continuing with fallback logic...
)

echo.
echo [2/4] Fetching soma-retargeter source...
git submodule update --init --recursive third_party/soma-retargeter
if errorlevel 1 (
    echo [WARN ] Submodule fetch failed. Trying direct clone fallback...

    if exist "%RETARGET_DIR%\" (
        for /f %%I in ('dir /b "%RETARGET_DIR%" 2^>nul ^| find /c /v ""') do set "ENTRY_COUNT=%%I"
        if not defined ENTRY_COUNT set "ENTRY_COUNT=0"
        if "!ENTRY_COUNT!"=="0" (
            rmdir "%RETARGET_DIR%" >nul 2>&1
        ) else (
            echo [ERROR] %RETARGET_DIR% is not empty; cannot safely replace it.
            echo         Clean it manually, then rerun this script.
            exit /b 1
        )
    )

    git clone https://github.com/NVIDIA/soma-retargeter.git "%RETARGET_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to fetch soma-retargeter source.
        exit /b 1
    )
)

echo.
echo [3/4] Installing soma-retargeter into .venv...
".venv\Scripts\python.exe" -m pip install --upgrade -e third_party/soma-retargeter
if errorlevel 1 (
    echo [ERROR] Failed to install third_party/soma-retargeter.
    exit /b 1
)

echo.
echo [4/4] Verifying import...
".venv\Scripts\python.exe" -c "import soma_retargeter, newton; print('soma_retargeter:', soma_retargeter.__file__); print('newton:', newton.__file__)"
if errorlevel 1 (
    echo [ERROR] Install verification failed: import soma_retargeter, newton
    exit /b 1
)

echo.
echo [INFO ] soma_retargeter is installed. You can now run rig export in the Qt UI.
exit /b 0
