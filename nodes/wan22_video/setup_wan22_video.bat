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

set "WAN22_REPO_URL=https://github.com/Wan-Video/Wan2.2.git"
if defined QUBITMCP_WAN22_GIT_URL set "WAN22_REPO_URL=%QUBITMCP_WAN22_GIT_URL%"
set "THIRD_PARTY_DIR=%APP_HOME%\third_party"
set "WAN22_DIR=%THIRD_PARTY_DIR%\Wan2.2"
set "WAN22_VENV=%WAN22_DIR%\.venv"
set "WAN22_PY=%WAN22_VENV%\Scripts\python.exe"
set "WAN22_MODEL_DIR=%APP_HOME%\models\Wan2.2-TI2V-5B"
set "WAN22_DOWNLOAD_SCRIPT=%REPO_DIR%\nodes\wan22_video\download_model.py"
set "WAN22_PATCH_SCRIPT=%REPO_DIR%\nodes\wan22_video\patch_runtime.py"
set "WAN22_REQ_FILTERED=%TEMP%\qubitmcp_wan22_requirements_%RANDOM%_%RANDOM%.txt"

if /I "%QUBITMCP_SKIP_WAN22%"=="1" (
    echo [wan2.2] Skipping Wan2.2 setup because QUBITMCP_SKIP_WAN22=1.
    exit /b 0
)

if not exist "%APP_HOME%" mkdir "%APP_HOME%" >nul 2>&1
if not exist "%THIRD_PARTY_DIR%" mkdir "%THIRD_PARTY_DIR%" >nul 2>&1

where git >nul 2>&1
if errorlevel 1 (
    echo [wan2.2] ERROR: git was not found on PATH.
    exit /b 1
)

echo [wan2.2] Runtime root: %WAN22_DIR%

if exist "%WAN22_DIR%\.git" goto update_checkout
if exist "%WAN22_DIR%\" goto check_existing_dir
goto clone_checkout

:check_existing_dir
if exist "%WAN22_DIR%\generate.py" goto ensure_venv
set "ENTRY_COUNT=0"
for /f %%I in ('dir /b /a "%WAN22_DIR%" 2^>nul ^| find /c /v ""') do set "ENTRY_COUNT=%%I"
if "!ENTRY_COUNT!"=="0" (
    rmdir "%WAN22_DIR%" >nul 2>&1
    goto clone_checkout
)
echo [wan2.2] ERROR: Existing Wan2.2 directory is not a git checkout and has no generate.py:
echo          %WAN22_DIR%
echo [wan2.2]        Move or remove it, then rerun setup.
exit /b 1

:clone_checkout
echo [wan2.2] Cloning Wan2.2 from GitHub...
git clone --depth 1 "%WAN22_REPO_URL%" "%WAN22_DIR%"
if errorlevel 1 (
    echo [wan2.2] ERROR: Failed to clone Wan2.2.
    exit /b 1
)
goto ensure_venv

:update_checkout
echo [wan2.2] Updating existing Wan2.2 checkout...
git -C "%WAN22_DIR%" remote set-url origin "%WAN22_REPO_URL%" >nul 2>&1
git -C "%WAN22_DIR%" pull --ff-only
if errorlevel 1 (
    echo [wan2.2] WARNING: Could not fast-forward Wan2.2. Continuing with local checkout.
)

:ensure_venv
if exist "%WAN22_PATCH_SCRIPT%" (
    "%PY_FOR_VENV%" "%WAN22_PATCH_SCRIPT%" --wan-root "%WAN22_DIR%"
    if errorlevel 1 (
        echo [wan2.2] ERROR: Failed to patch Wan2.2 runtime.
        exit /b 1
    )
)

if not exist "%WAN22_PY%" (
    echo [wan2.2] Creating Wan2.2 virtual environment...
    "%PY_FOR_VENV%" -m venv "%WAN22_VENV%"
    if errorlevel 1 (
        echo [wan2.2] ERROR: Failed to create Wan2.2 virtual environment.
        exit /b 1
    )
)

echo [wan2.2] Preparing Wan2.2 Python environment...
"%WAN22_PY%" -m pip install --upgrade pip "setuptools<82" wheel
if errorlevel 1 (
    echo [wan2.2] ERROR: Failed to prepare pip tooling.
    exit /b 1
)

if /I "%QUBITMCP_SKIP_WAN22_DEPS%"=="1" (
    echo [wan2.2] Skipping Wan2.2 dependency install because QUBITMCP_SKIP_WAN22_DEPS=1.
    goto download_model
)

if /I "%QUBITMCP_SKIP_WAN22_TORCH%"=="1" goto install_wan_requirements
if not defined WAN22_TORCH_INDEX_URL set "WAN22_TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124"
echo [wan2.2] Installing PyTorch runtime from %WAN22_TORCH_INDEX_URL% ...
"%WAN22_PY%" -m pip install "torch>=2.4.0" "torchvision" "torchaudio" --index-url "%WAN22_TORCH_INDEX_URL%"
if errorlevel 1 (
    echo [wan2.2] ERROR: Failed to install PyTorch for Wan2.2.
    exit /b 1
)

:install_wan_requirements
if exist "%WAN22_DIR%\requirements.txt" (
    if /I "%QUBITMCP_WAN22_INSTALL_FLASH_ATTN%"=="1" (
        echo [wan2.2] Installing Wan2.2 requirements, including flash_attn...
        "%WAN22_PY%" -m pip install -r "%WAN22_DIR%\requirements.txt"
    ) else (
        echo [wan2.2] Installing Wan2.2 requirements without flash_attn...
        findstr /V /I /C:"flash_attn" /C:"flash-attn" "%WAN22_DIR%\requirements.txt" > "%WAN22_REQ_FILTERED%"
        "%WAN22_PY%" -m pip install -r "%WAN22_REQ_FILTERED%"
    )
    if errorlevel 1 (
        echo [wan2.2] ERROR: Failed to install Wan2.2 requirements.
        exit /b 1
    )
    echo [wan2.2] Installing Wan2.2 compatibility dependencies...
    "%WAN22_PY%" -m pip install einops
    if errorlevel 1 (
        echo [wan2.2] ERROR: Failed to install Wan2.2 compatibility dependencies.
        exit /b 1
    )
    "%WAN22_PY%" -m pip install decord
    if errorlevel 1 (
        echo [wan2.2] WARNING: Could not install decord. TI2V can continue with QubitMCP's optional-import patch.
    )
) else (
    echo [wan2.2] WARNING: Wan2.2 requirements.txt was not found.
)

:download_model
if /I "%QUBITMCP_SKIP_WAN22_MODEL%"=="1" (
    echo [wan2.2] Skipping Wan2.2 model download because QUBITMCP_SKIP_WAN22_MODEL=1.
    goto verify_runtime
)

if not exist "%WAN22_DOWNLOAD_SCRIPT%" (
    echo [wan2.2] ERROR: Wan2.2 download script not found: %WAN22_DOWNLOAD_SCRIPT%
    exit /b 1
)

echo [wan2.2] Installing Hugging Face downloader dependency...
"%WAN22_PY%" -m pip install --progress-bar on huggingface_hub[hf_xet]
if errorlevel 1 (
    echo [wan2.2] ERROR: Failed to install Hugging Face downloader dependency.
    exit /b 1
)

echo [wan2.2] Downloading Wan2.2 TI2V-5B model...
"%WAN22_PY%" "%WAN22_DOWNLOAD_SCRIPT%" --app-home "%APP_HOME%" --local-dir "%WAN22_MODEL_DIR%"
if errorlevel 1 (
    echo [wan2.2] ERROR: Wan2.2 model download failed.
    exit /b 1
)

:verify_runtime
if not exist "%WAN22_DIR%\generate.py" (
    echo [wan2.2] ERROR: Wan2.2 generate.py was not found.
    exit /b 1
)
if not exist "%WAN22_PY%" (
    echo [wan2.2] ERROR: Wan2.2 Python verification failed.
    exit /b 1
)
if /I not "%QUBITMCP_SKIP_WAN22_MODEL%"=="1" if not exist "%WAN22_MODEL_DIR%" (
    echo [wan2.2] ERROR: Wan2.2 model directory was not found: %WAN22_MODEL_DIR%
    exit /b 1
)

echo [wan2.2] Wan2.2 runtime is ready.
exit /b 0
