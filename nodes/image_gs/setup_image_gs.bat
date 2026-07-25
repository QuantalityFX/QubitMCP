@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%R in ("%SCRIPT_DIR%..\..") do set "REPO_DIR=%%~fR"

set "APP_HOME=%~1"
if not defined APP_HOME set "APP_HOME=%QUBITMCP_HOME%"
if not defined APP_HOME set "APP_HOME=%REPO_DIR%"

set "PY_FOR_VENV=%~2"
if not defined PY_FOR_VENV set "PY_FOR_VENV=%APP_HOME%\.venv\Scripts\python.exe"
if not exist "%PY_FOR_VENV%" set "PY_FOR_VENV=python"

set "IMAGE_GS_URL=https://github.com/NYU-ICL/image-gs.git"
set "THIRD_PARTY_DIR=%APP_HOME%\third_party"
set "IMAGE_GS_DIR=%THIRD_PARTY_DIR%\image-gs"
set "IMAGE_GS_VENV=%IMAGE_GS_DIR%\.venv"
set "IMAGE_GS_PY=%IMAGE_GS_VENV%\Scripts\python.exe"

if /I "%QUBITMCP_SKIP_IMAGE_GS%"=="1" (
    echo [image-gs] Skipping Image-GS setup because QUBITMCP_SKIP_IMAGE_GS=1.
    exit /b 0
)

if not exist "%APP_HOME%" mkdir "%APP_HOME%" >nul 2>&1
if not exist "%THIRD_PARTY_DIR%" mkdir "%THIRD_PARTY_DIR%" >nul 2>&1

where git >nul 2>&1
if errorlevel 1 (
    echo [image-gs] ERROR: git was not found on PATH.
    exit /b 1
)

echo [image-gs] Runtime root: %IMAGE_GS_DIR%

if exist "%IMAGE_GS_DIR%\.git" goto update_checkout
if exist "%IMAGE_GS_DIR%\" goto check_existing_dir
goto clone_checkout

:check_existing_dir
set "ENTRY_COUNT=0"
for /f %%I in ('dir /b /a "%IMAGE_GS_DIR%" 2^>nul ^| find /c /v ""') do set "ENTRY_COUNT=%%I"
if "!ENTRY_COUNT!"=="0" (
    rmdir "%IMAGE_GS_DIR%" >nul 2>&1
    goto clone_checkout
)
echo [image-gs] ERROR: Existing Image-GS directory is not a git checkout:
echo            %IMAGE_GS_DIR%
echo [image-gs]        Move or remove it, then rerun setup.
exit /b 1

:clone_checkout
echo [image-gs] Cloning Image-GS from GitHub...
git clone --depth 1 "%IMAGE_GS_URL%" "%IMAGE_GS_DIR%"
if errorlevel 1 (
    echo [image-gs] ERROR: Failed to clone Image-GS.
    exit /b 1
)
goto ensure_venv

:update_checkout
echo [image-gs] Updating existing Image-GS checkout...
git -C "%IMAGE_GS_DIR%" remote set-url origin "%IMAGE_GS_URL%" >nul 2>&1
git -C "%IMAGE_GS_DIR%" pull --ff-only
if errorlevel 1 (
    echo [image-gs] WARNING: Could not fast-forward Image-GS. Continuing with local checkout.
)

:ensure_venv
if not exist "%IMAGE_GS_PY%" (
    echo [image-gs] Creating Image-GS virtual environment...
    "%PY_FOR_VENV%" -m venv "%IMAGE_GS_VENV%"
    if errorlevel 1 (
        echo [image-gs] ERROR: Failed to create Image-GS virtual environment.
        exit /b 1
    )
)

echo [image-gs] Preparing Image-GS Python environment...
"%IMAGE_GS_PY%" -m pip install --upgrade pip "setuptools<82" wheel
if errorlevel 1 (
    echo [image-gs] ERROR: Failed to prepare pip tooling.
    exit /b 1
)

if /I "%QUBITMCP_SKIP_IMAGE_GS_DEPS%"=="1" (
    echo [image-gs] Skipping Image-GS dependency install because QUBITMCP_SKIP_IMAGE_GS_DEPS=1.
    goto verify_runtime
)

if not defined IMAGE_GS_TORCH_INDEX_URL set "IMAGE_GS_TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124"
echo [image-gs] Installing PyTorch runtime from %IMAGE_GS_TORCH_INDEX_URL% ...
"%IMAGE_GS_PY%" -m pip install "torch==2.4.1" "torchvision==0.19.1" "torchaudio==2.4.1" --index-url "%IMAGE_GS_TORCH_INDEX_URL%"
if errorlevel 1 (
    echo [image-gs] ERROR: Failed to install PyTorch for Image-GS.
    exit /b 1
)

if exist "%IMAGE_GS_DIR%\requirements.txt" (
    echo [image-gs] Installing Image-GS requirements...
    "%IMAGE_GS_PY%" -m pip install -r "%IMAGE_GS_DIR%\requirements.txt"
    if errorlevel 1 (
        echo [image-gs] ERROR: Failed to install Image-GS requirements.
        exit /b 1
    )
) else (
    echo [image-gs] Installing Image-GS dependencies from environment.yml notes...
    "%IMAGE_GS_PY%" -m pip install "flip-evaluator" "imageio==2.36.0" "lpips==0.1.4" "matplotlib==3.9.2" "numpy==2.0.2" "opencv-python==4.12.0.88" "pillow==10.4.0" "PyYAML==6.0.2" "pytorch-msssim==1.0.0" "scikit-image==0.24.0" "scipy==1.13.1" "torchmetrics==1.5.2"
    if errorlevel 1 (
        echo [image-gs] ERROR: Failed to install Image-GS Python dependencies.
        exit /b 1
    )
)

echo [image-gs] Installing fused-ssim...
"%IMAGE_GS_PY%" -m pip install "git+https://github.com/rahul-goel/fused-ssim/" --no-build-isolation
if errorlevel 1 (
    echo [image-gs] ERROR: Failed to install fused-ssim.
    exit /b 1
)

if exist "%IMAGE_GS_DIR%\gsplat\setup.py" (
    echo [image-gs] Installing bundled gsplat package...
    "%IMAGE_GS_PY%" -m pip install -e "%IMAGE_GS_DIR%\gsplat" --no-build-isolation
    if errorlevel 1 (
        echo [image-gs] ERROR: Failed to install bundled gsplat package.
        exit /b 1
    )
) else if exist "%IMAGE_GS_DIR%\gsplat\pyproject.toml" (
    echo [image-gs] Installing bundled gsplat package...
    "%IMAGE_GS_PY%" -m pip install -e "%IMAGE_GS_DIR%\gsplat" --no-build-isolation
    if errorlevel 1 (
        echo [image-gs] ERROR: Failed to install bundled gsplat package.
        exit /b 1
    )
) else (
    echo [image-gs] WARNING: Bundled gsplat package was not found.
)

echo [image-gs] Verifying Image-GS imports...
set "OLD_PYTHONPATH=%PYTHONPATH%"
if exist "%IMAGE_GS_DIR%\gsplat\" (
    set "PYTHONPATH=%IMAGE_GS_DIR%\gsplat;%PYTHONPATH%"
)
"%IMAGE_GS_PY%" -c "import torch; import cv2; import flip_evaluator; import imageio.v3; import lpips; import matplotlib; import numpy; import scipy; import skimage; import torchmetrics; import yaml; from PIL import Image; from fused_ssim import fused_ssim; from gsplat import project_gaussians_2d_scale_rot, rasterize_gaussians_no_tiles, rasterize_gaussians_sum; from model import GaussianSplatting2D; from utils.misc_utils import load_cfg"
if errorlevel 1 (
    set "PYTHONPATH=%OLD_PYTHONPATH%"
    echo [image-gs] ERROR: Image-GS dependency verification failed.
    exit /b 1
)
set "PYTHONPATH=%OLD_PYTHONPATH%"

:verify_runtime
if not exist "%IMAGE_GS_DIR%\.git" (
    echo [image-gs] ERROR: Image-GS checkout verification failed.
    exit /b 1
)
if not exist "%IMAGE_GS_PY%" (
    echo [image-gs] ERROR: Image-GS Python verification failed.
    exit /b 1
)

echo [image-gs] Image-GS runtime is ready.
exit /b 0
