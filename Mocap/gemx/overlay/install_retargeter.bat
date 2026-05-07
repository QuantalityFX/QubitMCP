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
set "UV_CACHE_DIR=%REPO_DIR%.uv-cache"
set "UV_LINK_MODE=copy"
set "PIP_TMP_DIR=%REPO_DIR%.tmp\pip"
set "SITE_DIR=%REPO_DIR%.venv\Lib\site-packages"
if not exist "%PIP_TMP_DIR%\" mkdir "%PIP_TMP_DIR%"
set "TMP=%PIP_TMP_DIR%"
set "TEMP=%PIP_TMP_DIR%"

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
    echo [WARN ] Submodule fetch failed.
    if exist "%RETARGET_DIR%\pyproject.toml" (
        if exist "%RETARGET_DIR%\soma_retargeter\__init__.py" (
            echo [WARN ] Existing soma-retargeter source is present. Continuing with local checkout.
        ) else (
            echo [WARN ] Existing soma-retargeter folder is incomplete. Trying direct clone fallback...
            goto clone_fallback
        )
    ) else (
        echo [WARN ] Trying direct clone fallback...
        goto clone_fallback
    )
)
goto install_package

:clone_fallback
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

:install_package

echo.
echo [3/4] Installing soma-retargeter into .venv...
".venv\Scripts\python.exe" -m uv --version >nul 2>&1
if errorlevel 1 (
    echo [INFO ] Installing uv helper into .venv...
    ".venv\Scripts\python.exe" -m pip install uv
    if errorlevel 1 (
        echo [ERROR] Failed to install uv helper.
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m uv pip install -e third_party/soma-retargeter
if errorlevel 1 (
    echo [WARN ] uv failed to install third_party/soma-retargeter. Trying pip fallback...
    ".venv\Scripts\python.exe" -m pip install --upgrade -e third_party/soma-retargeter
    if errorlevel 1 (
        echo [WARN ] pip failed to install third_party/soma-retargeter.
        call :copy_reference_runtime
        if errorlevel 1 (
            echo [ERROR] Failed to install third_party/soma-retargeter.
            exit /b 1
        )
    )
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

:copy_reference_runtime
set "REFERENCE_GEMX_DIR=%REPO_DIR%..\..\..\GEM-X"
for %%R in ("%REFERENCE_GEMX_DIR%") do set "REFERENCE_GEMX_DIR=%%~fR"
set "REFERENCE_SITE_DIR=%REFERENCE_GEMX_DIR%\.venv\Lib\site-packages"

if not exist "%REFERENCE_SITE_DIR%\newton\__init__.py" (
    echo [WARN ] No reusable reference retarget runtime found at:
    echo        %REFERENCE_SITE_DIR%
    echo [WARN ] Expected a working sibling checkout like V:\Source\Repos\GEM-X.
    exit /b 1
)

echo [INFO ] Reusing retarget runtime from:
echo        %REFERENCE_GEMX_DIR%

for /d %%D in ("%SITE_DIR%\warp_lang-*.dist-info") do rmdir /s /q "%%~fD" >nul 2>&1
if exist "%SITE_DIR%\warp\" rmdir /s /q "%SITE_DIR%\warp" >nul 2>&1

for %%D in (
    imgui_bundle
    imgui_bundle-1.92.4.dist-info
    newton
    newton-1.0.0.dist-info
    newton_actuators
    newton_actuators-0.1.0.dist-info
    pxr
    pyglet
    pyglet-2.1.13.dist-info
    soma_retargeter-0.1.0.dist-info
    usd_core-26.3.dist-info
    warp
    warp_lang-1.12.0.dist-info
) do (
    if exist "%REFERENCE_SITE_DIR%\%%D\" (
        if exist "%SITE_DIR%\%%D\" rmdir /s /q "%SITE_DIR%\%%D" >nul 2>&1
        robocopy "%REFERENCE_SITE_DIR%\%%D" "%SITE_DIR%\%%D" /E /NFL /NDL /NJH /NJS /NP >nul
        if !ERRORLEVEL! GEQ 8 (
            echo [ERROR] Failed to copy %%D from reference runtime.
            exit /b 1
        )
    ) else (
        echo [WARN ] Reference runtime missing %%D
    )
)

> "%SITE_DIR%\soma_retargeter_local.pth" echo %REPO_DIR%third_party\soma-retargeter
exit /b 0
