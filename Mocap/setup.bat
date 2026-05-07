@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem QubitMCP GEM-X integration setup.
rem Installs NVIDIA GEM-X under Mocap\GEM-X, applies the local QubitMCP
rem patch/overlay, then runs the GEM-X env setup. If QubitMCP has a registered
rem GEM-X submodule, it initializes that submodule. Otherwise it clones GEM-X
rem into the same path so a fresh checkout can still be set up with one command.

set "MOCAP_DIR=%~dp0"
for %%I in ("%MOCAP_DIR%..") do set "PROJECT_ROOT=%%~fI"

set "SUBMODULE_PATH=Mocap/GEM-X"
set "GEMX_DIR=%MOCAP_DIR%GEM-X"
set "INTEGRATION_DIR=%MOCAP_DIR%gemx"
set "OVERLAY_DIR=%INTEGRATION_DIR%\overlay"
set "PATCH_FILE=%INTEGRATION_DIR%\patches\0001-qubit-gemx-windows-ui-retarget.patch"
set "UPSTREAM_URL=https://github.com/NVlabs/GEM-X.git"
set "BASELINE_COMMIT=953b3871c15a38acec6b7cccec18ed6019b4512b"

if /I "%~1"=="--help" goto usage
if /I "%~1"=="-h" goto usage

echo.
echo QubitMCP GEM-X setup
echo ====================
echo Project root: %PROJECT_ROOT%
echo GEM-X path:   %GEMX_DIR%
echo.

if not exist "%PROJECT_ROOT%\.git" (
    echo [ERROR] QubitMCP .git directory was not found at:
    echo         %PROJECT_ROOT%\.git
    echo         Run this script from inside a QubitMCP checkout.
    exit /b 1
)

if not exist "%PATCH_FILE%" (
    echo [ERROR] Patch file was not found:
    echo         %PATCH_FILE%
    exit /b 1
)

if not exist "%OVERLAY_DIR%\" (
    echo [ERROR] Overlay directory was not found:
    echo         %OVERLAY_DIR%
    exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] git was not found on PATH.
    exit /b 1
)

echo [1/6] Ensuring GEM-X submodule exists...
if exist "%GEMX_DIR%\.git" (
    echo [INFO ] Existing GEM-X checkout found.
) else (
    pushd "%PROJECT_ROOT%" >nul
    git submodule status "%SUBMODULE_PATH%" >nul 2>&1
    if errorlevel 1 (
        if exist "%GEMX_DIR%\" (
            for /f %%I in ('dir /b "%GEMX_DIR%" 2^>nul ^| find /c /v ""') do set "ENTRY_COUNT=%%I"
            if not defined ENTRY_COUNT set "ENTRY_COUNT=0"
            if not "!ENTRY_COUNT!"=="0" (
                popd >nul
                echo [ERROR] %GEMX_DIR% exists but is not a Git checkout and is not empty.
                echo         Move or clean that folder, then rerun setup.
                exit /b 1
            )
        )

        echo [INFO ] No registered submodule entry found. Cloning GEM-X from %UPSTREAM_URL%
        git clone "%UPSTREAM_URL%" "%SUBMODULE_PATH%"
        if errorlevel 1 (
            popd >nul
            echo [ERROR] Failed to clone GEM-X.
            exit /b 1
        )
    ) else (
        echo [INFO ] Initializing existing submodule entry.
        git submodule update --init "%SUBMODULE_PATH%"
        if errorlevel 1 (
            popd >nul
            echo [ERROR] Failed to initialize GEM-X submodule.
            exit /b 1
        )
    )
    popd >nul
)

if not exist "%GEMX_DIR%\.git" (
    echo [ERROR] GEM-X checkout was not created:
    echo         %GEMX_DIR%
    exit /b 1
)

echo.
echo [2/6] Pinning GEM-X to upstream baseline %BASELINE_COMMIT%...
git -C "%GEMX_DIR%" fetch origin
if errorlevel 1 (
    echo [WARN ] git fetch failed. Continuing with the local checkout if the baseline commit exists.
)

git -C "%GEMX_DIR%" checkout "%BASELINE_COMMIT%"
if errorlevel 1 (
    echo [ERROR] Failed to checkout GEM-X baseline commit.
    exit /b 1
)

echo.
echo [3/6] Applying QubitMCP GEM-X patch...
git -C "%GEMX_DIR%" apply --check "%PATCH_FILE%" >nul 2>&1
if not errorlevel 1 (
    git -C "%GEMX_DIR%" apply "%PATCH_FILE%"
    if errorlevel 1 (
        echo [ERROR] Patch check passed, but patch apply failed.
        exit /b 1
    )
    echo [INFO ] Patch applied.
) else (
    git -C "%GEMX_DIR%" apply --reverse --check "%PATCH_FILE%" >nul 2>&1
    if not errorlevel 1 (
        echo [INFO ] Patch already applied.
    ) else (
        echo [ERROR] Patch cannot be applied cleanly and does not look already applied.
        echo         Current GEM-X status:
        git -C "%GEMX_DIR%" status --short
        exit /b 1
    )
)

echo.
echo [4/6] Copying QubitMCP GEM-X overlay files...
robocopy "%OVERLAY_DIR%" "%GEMX_DIR%" /E /NFL /NDL /NJH /NJS /NP >nul
set "ROBOCOPY_RC=!ERRORLEVEL!"
if !ROBOCOPY_RC! GEQ 8 (
    echo [ERROR] Overlay copy failed. robocopy exit code: !ROBOCOPY_RC!
    exit /b 1
)
echo [INFO ] Overlay copied.

echo.
echo [5/6] Syncing GEM-X nested submodule URLs...
git -C "%GEMX_DIR%" submodule sync --recursive
if errorlevel 1 (
    echo [ERROR] Failed to sync GEM-X nested submodules.
    exit /b 1
)

echo.
echo [6/6] Running GEM-X environment setup...
call "%GEMX_DIR%\setup.bat" %*
if errorlevel 1 (
    echo [ERROR] GEM-X environment setup failed.
    exit /b 1
)

echo.
echo QubitMCP GEM-X setup complete.
echo.
echo Useful commands:
echo   Mocap\GEM-X\run_qt_ui.bat
echo   Mocap\GEM-X\open_env.bat
echo.
echo To install retargeting support during setup, rerun:
echo   Mocap\setup.bat --with-retarget
echo.
exit /b 0

:usage
echo.
echo QubitMCP GEM-X setup
echo.
echo Usage:
echo   Mocap\setup.bat [GEM-X setup options]
echo.
echo Common options passed to GEM-X setup:
echo   --with-retarget         Install soma-retargeter support
echo   --cuda ^<tag^>          CUDA tag for PyTorch index, for example cu126 or cu130
echo   --python ^<exe^>        Python executable or full path
echo   --skip-detectron2       Skip detectron2 installation
echo   --skip-smoke-test       Skip final import smoke test
echo   --help, -h              Show this help
echo.
exit /b 0
