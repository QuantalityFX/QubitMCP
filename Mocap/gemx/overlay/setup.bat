@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem GEM-X one-step Windows setup script.
rem Usage:
rem   setup.bat
rem   setup.bat --cuda cu130
rem   setup.bat --skip-retarget
rem   setup.bat --skip-detectron2
rem   setup.bat --strict-detectron2
rem   setup.bat --skip-smoke-test
rem   setup.bat --python "C:\Path\To\python.exe"

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

rem Keep uv state local to this managed GEM-X checkout. This avoids Windows
rem cache permission issues and suppresses hardlink warnings on mixed drives.
set "UV_CACHE_DIR=%REPO_DIR%.uv-cache"
set "UV_LINK_MODE=copy"

if not exist "setup.py" (
    echo [ERROR] setup.py not found. Run this script from the GEM-X repository root.
    exit /b 1
)

set "PYTHON_EXE=python"
set "CUDA_TAG=cu126"
set "INSTALL_RETARGET=1"
set "SKIP_DETECTRON2=0"
set "STRICT_DETECTRON2=0"
set "RUN_SMOKE_TEST=1"
set "USAGE_EXIT_CODE=1"

:parse_args
if "%~1"=="" goto args_done

if /I "%~1"=="--cuda" (
    if "%~2"=="" (
        echo [ERROR] --cuda requires a value like cu126 or cu130.
        exit /b 1
    )
    set "CUDA_TAG=%~2"
    shift
    shift
    goto parse_args
)

if /I "%~1"=="--python" (
    if "%~2"=="" (
        echo [ERROR] --python requires a full path or command name.
        exit /b 1
    )
    set "PYTHON_EXE=%~2"
    shift
    shift
    goto parse_args
)

if /I "%~1"=="--with-retarget" (
    set "INSTALL_RETARGET=1"
    shift
    goto parse_args
)

if /I "%~1"=="--skip-retarget" (
    set "INSTALL_RETARGET=0"
    shift
    goto parse_args
)

if /I "%~1"=="--skip-detectron2" (
    set "SKIP_DETECTRON2=1"
    shift
    goto parse_args
)

if /I "%~1"=="--strict-detectron2" (
    set "STRICT_DETECTRON2=1"
    shift
    goto parse_args
)

if /I "%~1"=="--skip-smoke-test" (
    set "RUN_SMOKE_TEST=0"
    shift
    goto parse_args
)

if /I "%~1"=="--help" (
    set "USAGE_EXIT_CODE=0"
    goto usage
)
if /I "%~1"=="-h" (
    set "USAGE_EXIT_CODE=0"
    goto usage
)

echo [ERROR] Unknown argument: %~1
goto usage

:args_done
echo.
echo [1/10] Checking prerequisites...

where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] git was not found on PATH.
    exit /b 1
)

"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python command failed: "%PYTHON_EXE%"
    echo         Install Python 3.12+ and/or pass --python "C:\Path\To\python.exe"
    exit /b 1
)

set "PY_VER="
for /f "tokens=2" %%V in ('"%PYTHON_EXE%" --version 2^>^&1') do set "PY_VER=%%V"
if not defined PY_VER (
    echo [ERROR] Could not detect Python version.
    exit /b 1
)

for /f "tokens=1,2 delims=." %%A in ("!PY_VER!") do (
    set "PY_MAJOR=%%A"
    set "PY_MINOR=%%B"
)

if !PY_MAJOR! LSS 3 (
    echo [ERROR] Python 3.12+ is required. Found !PY_VER!.
    exit /b 1
)
if !PY_MAJOR! EQU 3 if !PY_MINOR! LSS 12 (
    echo [ERROR] Python 3.12+ is required. Found !PY_VER!.
    exit /b 1
)

where git-lfs >nul 2>&1
if errorlevel 1 (
    set "HAS_GIT_LFS=0"
    echo [WARN ] git-lfs not found. SOMA assets may remain LFS pointer files.
) else (
    set "HAS_GIT_LFS=1"
    git lfs install >nul 2>&1
)

echo.
echo [2/10] Initializing required submodules...
git submodule update --init --recursive third_party/soma third_party/sam-3d-body
if errorlevel 1 (
    echo [ERROR] Failed to initialize submodules third_party/soma and third_party/sam-3d-body.
    exit /b 1
)

if "%INSTALL_RETARGET%"=="1" (
    echo.
    echo [3/10] Initializing soma-retargeter submodule...
    git submodule sync -- third_party/soma-retargeter >nul 2>&1
    git submodule update --init --recursive third_party/soma-retargeter
    if errorlevel 1 (
        echo [ERROR] Failed to initialize third_party/soma-retargeter.
        echo         Run:
        echo           git submodule sync -- third_party/soma-retargeter
        echo           git submodule update --init --recursive third_party/soma-retargeter
        exit /b 1
    )
) else (
    echo.
    echo [3/10] Skipping soma-retargeter submodule. Use --with-retarget to include it.
)

echo.
echo [4/10] Creating virtual environment (.venv)...
if exist ".venv\Scripts\python.exe" (
    echo [INFO ] Reusing existing .venv
) else (
    "%PYTHON_EXE%" -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        exit /b 1
    )
)

set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] Virtual environment python not found: %VENV_PY%
    exit /b 1
)

echo.
echo [5/10] Installing uv in the virtual environment...
"%VENV_PY%" -m ensurepip --upgrade >nul 2>&1
"%VENV_PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [WARN ] Existing .venv is missing pip. Recreating .venv...
    rmdir /s /q ".venv" >nul 2>&1
    "%PYTHON_EXE%" -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to recreate virtual environment.
        exit /b 1
    )
    set "VENV_PY=.venv\Scripts\python.exe"
    "%VENV_PY%" -m ensurepip --upgrade >nul 2>&1
    "%VENV_PY%" -m pip --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not bootstrap pip in .venv.
        echo         Delete .venv manually, then rerun setup.bat.
        exit /b 1
    )
)

"%VENV_PY%" -m pip install --upgrade pip "setuptools<82" wheel
if errorlevel 1 (
    echo [ERROR] Failed to bootstrap pip/setuptools/wheel in .venv.
    exit /b 1
)

"%VENV_PY%" -m pip install --upgrade uv
if errorlevel 1 (
    echo [ERROR] Failed to install uv in .venv.
    exit /b 1
)

echo.
echo [6/10] Installing PyTorch (CUDA %CUDA_TAG%)...
"%VENV_PY%" -m uv pip install torch torchvision --index-url https://download.pytorch.org/whl/%CUDA_TAG%
if errorlevel 1 (
    echo [ERROR] Failed to install torch/torchvision for %CUDA_TAG%.
    echo         Try setup.bat --cuda cu130 if your driver matches CUDA 13.0.
    exit /b 1
)

echo.
echo [7/10] Installing SOMA body model package...
if not exist "third_party\soma\setup.py" if not exist "third_party\soma\pyproject.toml" (
    echo [ERROR] third_party/soma appears empty. Submodule checkout failed.
    exit /b 1
)

"%VENV_PY%" -m uv pip install -e third_party/soma
if errorlevel 1 (
    echo [ERROR] Failed to install third_party/soma.
    exit /b 1
)

if "%HAS_GIT_LFS%"=="1" (
    pushd "third_party\soma"
    git lfs pull
    if errorlevel 1 (
        popd
        echo [ERROR] git lfs pull failed in third_party/soma.
        exit /b 1
    )
    popd
) else (
    echo [WARN ] Skipping git lfs pull because git-lfs is unavailable.
)

echo.
echo [8/10] Installing GEM and runtime dependencies...
"%VENV_PY%" -m uv pip install -e .
if errorlevel 1 (
    echo [ERROR] Failed to install GEM package in editable mode.
    exit /b 1
)

"%VENV_PY%" -m uv pip install -e ".[ui]"
if errorlevel 1 (
    echo [ERROR] Failed installing Qt UI dependencies (PySide6).
    exit /b 1
)

"%VENV_PY%" -m uv pip install cloudpickle fvcore iopath pycocotools braceexpand roma termcolor portalocker yacs tabulate setuptools^<75
if errorlevel 1 (
    echo [ERROR] Failed installing SAM-3D-Body runtime dependencies.
    exit /b 1
)

if "%SKIP_DETECTRON2%"=="1" (
    echo [WARN ] Skipping detectron2 install by request.
) else (
    echo [INFO ] Installing ninja build helper...
    "%VENV_PY%" -m uv pip install ninja >nul 2>&1

    "%VENV_PY%" -m uv pip install "git+https://github.com/facebookresearch/detectron2.git@a1ce2f9" --no-build-isolation --no-deps
    if errorlevel 1 (
        if "%STRICT_DETECTRON2%"=="1" (
            echo [ERROR] detectron2 install failed.
            echo         Check Visual Studio C++ build tools, CUDA toolkit, and compiler compatibility.
            exit /b 1
        ) else (
            echo [WARN ] detectron2 install failed. Continuing without it.
            echo [WARN ] If needed, rerun with --strict-detectron2 to enforce success.
            echo [WARN ] Note: vitdet-based 2D detection may not work without detectron2.
        )
    ) else (
        echo [INFO ] detectron2 installed successfully.
    )
)

if "%INSTALL_RETARGET%"=="1" (
    echo.
    echo [9/10] Installing soma-retargeter...
    "%VENV_PY%" -m pip install --upgrade -e third_party/soma-retargeter
    if errorlevel 1 (
        echo [ERROR] Failed to install third_party/soma-retargeter.
        exit /b 1
    )

    "%VENV_PY%" -c "import soma_retargeter, newton; print('soma_retargeter:', soma_retargeter.__file__); print('newton:', newton.__file__)"
    if errorlevel 1 (
        echo [ERROR] Retarget install verification failed.
        echo         Unitree G1 export requires both soma_retargeter and newton.
        exit /b 1
    )
) else (
    echo.
    echo [9/10] Skipping soma-retargeter package install.
)

echo.
echo [10/10] Running smoke test...
if "%RUN_SMOKE_TEST%"=="1" (
    "%VENV_PY%" -c "import torch, hydra, lightning, cv2, PySide6; import gem; import braceexpand, cloudpickle, fvcore, iopath, pycocotools, roma, termcolor, portalocker, yacs, tabulate; print('Core imports OK')"
    if errorlevel 1 (
        echo [ERROR] Smoke test failed.
        exit /b 1
    )
) else (
    echo [INFO ] Smoke test skipped by request.
)

echo.
echo Setup complete.
echo.
echo Next:
echo   1. Activate the environment:
echo      .venv\Scripts\activate
echo   2. Run demo or evaluation:
echo      python scripts\demo\demo_soma.py --video path\to\video.mp4
echo      python scripts\train.py exp=gem_soma_regression task=test
echo   3. Launch the Qt UI:
echo      run_qt_ui.bat
echo.
echo Notes:
echo   - The evaluation command requires dataset/assets prepared under inputs/.
echo   - Use --with-retarget to install Unitree G1 retargeting support.
exit /b 0

:usage
echo.
echo GEM-X Windows setup script
echo.
echo Usage:
echo   setup.bat [options]
echo.
echo Options:
echo   --cuda ^<tag^>          CUDA tag for PyTorch index (default: cu126)
echo   --python ^<exe^>        Python executable or command (default: python)
echo   --with-retarget         Install soma-retargeter too ^(default^)
echo   --skip-retarget         Skip soma-retargeter installation
echo   --skip-detectron2       Skip detectron2 installation
echo   --strict-detectron2     Fail setup if detectron2 install fails
echo   --skip-smoke-test       Do not run import smoke test
echo   --help, -h              Show this help
echo.
exit /b %USAGE_EXIT_CODE%
