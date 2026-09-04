@echo off
setlocal EnableExtensions

pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
  echo [setup] ERROR: Could not enter repository root.
  exit /b 1
)

set "REPO_DIR=%CD%"
set "RESOLVED_APP_HOME="
call :resolve_app_home "%REPO_DIR%"
if errorlevel 1 (
  echo [setup] ERROR: Could not resolve writable app home directory.
  popd
  exit /b 1
)
if not defined RESOLVED_APP_HOME (
  echo [setup] ERROR: Writable app home directory is empty.
  popd
  exit /b 1
)
set "APP_HOME=%RESOLVED_APP_HOME%"
set "QUBITFIELD_HOME=%APP_HOME%"
set "QUBITMCP_HOME=%APP_HOME%"

if not exist "%APP_HOME%" (
  mkdir "%APP_HOME%" >nul 2>&1
)
if not exist "%APP_HOME%" (
  echo [setup] ERROR: Could not create app home: %APP_HOME%
  popd
  exit /b 1
)

if /I not "%APP_HOME%"=="%REPO_DIR%" (
  echo [setup] Repo is read-only. Using writable app home:
  echo         "%APP_HOME%"
)

set "LOG_DIR=%APP_HOME%\logs"
if not exist "%LOG_DIR%" (
  mkdir "%LOG_DIR%" >nul 2>&1
)
if not exist "%LOG_DIR%" (
  echo [setup] ERROR: Could not create logs folder: %LOG_DIR%
  popd
  exit /b 1
)
set "LOG=%LOG_DIR%\setup.log"
set "MAIN_REQ=%REPO_DIR%\requirements.txt"
set "LIB_REQ=%REPO_DIR%\nodes\librarian\requirements.txt"
set "ROOT_VENV=%APP_HOME%\.venv"
set "ROOT_PY=%ROOT_VENV%\Scripts\python.exe"
set "LIB_VENV=%APP_HOME%\librarian\.venv"
set "LIB_PY=%LIB_VENV%\Scripts\python.exe"
set "QDECK_SETUP_SCRIPT=%REPO_DIR%\nodes\qubit_deck_controller\setup_qubit_deck_controller.bat"
set "IMAGE_GS_SETUP_SCRIPT=%REPO_DIR%\nodes\image_gs\setup_image_gs.bat"
set "VOICE_DEPS=SpeechRecognition pyttsx3 pyaudio soundcard gTTS pygame faster-whisper pymongo"
set "KOKORO_DEPS=kokoro misaki[ja,zh] unidic-lite"
set "BASE_PY_EXE="
set "BASE_PY_ARG="
set "BASE_PY_MM="
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PRIVATE_PYTHON_VERSION=3.10.11"
set "PRIVATE_PYTHON_MM=3.10"
set "PRIVATE_PYTHON_INSTALLER_NAME=python-%PRIVATE_PYTHON_VERSION%-amd64.exe"
set "PRIVATE_PYTHON_INSTALLER_URL=https://www.python.org/ftp/python/%PRIVATE_PYTHON_VERSION%/%PRIVATE_PYTHON_INSTALLER_NAME%"
if defined LOCALAPPDATA (
  set "PRIVATE_PYTHON_HOME=%LOCALAPPDATA%\QubitField\Python310"
  set "PRIVATE_PYTHON_INSTALLER=%LOCALAPPDATA%\QubitField\installers\%PRIVATE_PYTHON_INSTALLER_NAME%"
) else (
  set "PRIVATE_PYTHON_HOME=%APP_HOME%\python\Python310"
  set "PRIVATE_PYTHON_INSTALLER=%APP_HOME%\installers\%PRIVATE_PYTHON_INSTALLER_NAME%"
)
set "PRIVATE_PYTHON_EXE=%PRIVATE_PYTHON_HOME%\python.exe"
set "SETUP_MODE=%~1"

if not defined SETUP_MODE set "SETUP_MODE=full"
if /I "%SETUP_MODE%"=="--core" set "SETUP_MODE=core"
if /I "%SETUP_MODE%"=="--full" set "SETUP_MODE=full"
if /I "%SETUP_MODE%"=="/core" set "SETUP_MODE=core"
if /I "%SETUP_MODE%"=="/full" set "SETUP_MODE=full"
if /I "%SETUP_MODE%"=="help" goto :usage
if /I "%SETUP_MODE%"=="--help" goto :usage
if /I "%SETUP_MODE%"=="/?" goto :usage

if /I not "%SETUP_MODE%"=="core" if /I not "%SETUP_MODE%"=="full" (
  echo [setup] ERROR: Unknown mode "%SETUP_MODE%".
  goto :usage_fail
)

echo [setup] ==== START %DATE% %TIME% ==== > "%LOG%"
echo [setup] Repo: %REPO_DIR% >> "%LOG%"
echo [setup] Home: %APP_HOME% >> "%LOG%"
echo [setup] Mode: %SETUP_MODE% >> "%LOG%"

call :ensure_private_python
if not errorlevel 1 call :try_base_python "%PRIVATE_PYTHON_EXE%" ""

if not defined BASE_PY_EXE (
  echo [setup] WARNING: App-private Python is not available; checking existing system Python.
  echo [setup] WARNING: App-private Python is not available; checking existing system Python. >> "%LOG%"
  call :try_base_python "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" ""
  call :try_base_python "py" "-3.10"
  call :try_base_python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" ""
  call :try_base_python "py" "-3.12"
  call :try_base_python "py" "-3"
  call :try_base_python "python" ""
)

if not defined BASE_PY_EXE (
  echo [setup] ERROR: Could not find a usable Python interpreter. >> "%LOG%"
  echo [setup] ERROR: Could not install app-private Python and could not find Python 3.x. Install Python and retry.
  goto :fail
)

if defined BASE_PY_ARG (
  echo [setup] Base Python: %BASE_PY_EXE% %BASE_PY_ARG%
  echo [setup] Base Python: %BASE_PY_EXE% %BASE_PY_ARG% >> "%LOG%"
) else (
  echo [setup] Base Python: %BASE_PY_EXE%
  echo [setup] Base Python: %BASE_PY_EXE% >> "%LOG%"
)

call :get_base_py_mm
if not defined BASE_PY_MM (
  echo [setup] ERROR: Could not detect base Python version. >> "%LOG%"
  echo [setup] ERROR: Could not detect base Python version.
  goto :fail
)
echo [setup] Base Python version: %BASE_PY_MM%
echo [setup] Base Python version: %BASE_PY_MM% >> "%LOG%"

call :ensure_venv "%ROOT_VENV%" "root" || goto :fail
call :install_requirements "%ROOT_PY%" "%MAIN_REQ%" "root requirements" || goto :fail
call :check_ffmpeg_runtime "%ROOT_PY%"
call :install_voice_deps "%ROOT_PY%" "root voice dependencies" || goto :fail
call :install_optional_deps "%ROOT_PY%" "%KOKORO_DEPS%" "Kokoro-82M voice dependencies"
call :check_mediator_runtime
call :check_keyboard_sequence_runtime "%ROOT_PY%"
echo [setup] FBX SDK setup is handled by FBX nodes when needed.
echo [setup] FBX SDK setup is handled by FBX nodes when needed. >> "%LOG%"
call :setup_image_gs_runtime "%ROOT_PY%"

if /I "%SETUP_MODE%"=="full" (
  call :ensure_venv "%LIB_VENV%" "librarian" || goto :fail
  call :install_requirements "%LIB_PY%" "%LIB_REQ%" "librarian requirements" || goto :fail
  call :setup_qubit_deck_controller || goto :fail
) else (
  echo [setup] Skipping librarian setup ^(mode=%SETUP_MODE%^).
  echo [setup] Skipping librarian setup ^(mode=%SETUP_MODE%^). >> "%LOG%"
  echo [setup] Skipping QubitDeckController setup ^(mode=%SETUP_MODE%^).
  echo [setup] Skipping QubitDeckController setup ^(mode=%SETUP_MODE%^). >> "%LOG%"
)

call :create_windows_shortcuts

echo [setup] SUCCESS (%SETUP_MODE%)
echo [setup] SUCCESS (%SETUP_MODE%) >> "%LOG%"
echo [setup] Log: %LOG%
popd
exit /b 0

:ensure_private_python
if exist "%PRIVATE_PYTHON_EXE%" (
  "%PRIVATE_PYTHON_EXE%" -c "import sys, venv; raise SystemExit(0 if '.'.join(map(str, sys.version_info[:2])) == '%PRIVATE_PYTHON_MM%' else 1)" >nul 2>&1
  if errorlevel 1 (
    echo [setup] WARNING: Existing app-private Python failed validation: %PRIVATE_PYTHON_EXE%
    echo [setup] WARNING: Existing app-private Python failed validation: %PRIVATE_PYTHON_EXE% >> "%LOG%"
    exit /b 1
  )
  echo [setup] App-private Python ready: %PRIVATE_PYTHON_EXE%
  echo [setup] App-private Python ready: %PRIVATE_PYTHON_EXE% >> "%LOG%"
  exit /b 0
)

if /I not "%PROCESSOR_ARCHITECTURE%"=="AMD64" if /I not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
  echo [setup] WARNING: App-private Python auto-install requires 64-bit Windows.
  echo [setup] WARNING: App-private Python auto-install requires 64-bit Windows. >> "%LOG%"
  exit /b 1
)

echo [setup] Installing app-private Python %PRIVATE_PYTHON_VERSION%...
echo [setup] Installing app-private Python %PRIVATE_PYTHON_VERSION% to %PRIVATE_PYTHON_HOME% >> "%LOG%"
echo [setup] Python installer URL: %PRIVATE_PYTHON_INSTALLER_URL% >> "%LOG%"

for %%I in ("%PRIVATE_PYTHON_INSTALLER%") do set "PRIVATE_PYTHON_INSTALLER_DIR=%%~dpI"
if not exist "%PRIVATE_PYTHON_INSTALLER%" (
  if not exist "%PRIVATE_PYTHON_INSTALLER_DIR%" (
    mkdir "%PRIVATE_PYTHON_INSTALLER_DIR%" >nul 2>&1
  )
  if not exist "%PRIVATE_PYTHON_INSTALLER_DIR%" (
    echo [setup] WARNING: Could not create Python installer cache: %PRIVATE_PYTHON_INSTALLER_DIR%
    echo [setup] WARNING: Could not create Python installer cache: %PRIVATE_PYTHON_INSTALLER_DIR% >> "%LOG%"
    exit /b 1
  )

  where powershell >nul 2>&1
  if errorlevel 1 (
    echo [setup] WARNING: powershell.exe not found; cannot download app-private Python.
    echo [setup] WARNING: powershell.exe not found; cannot download app-private Python. >> "%LOG%"
    exit /b 1
  )

  echo [setup] Downloading app-private Python installer...
  echo [setup] Downloading app-private Python installer to %PRIVATE_PYTHON_INSTALLER% >> "%LOG%"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile($env:PRIVATE_PYTHON_INSTALLER_URL, $env:PRIVATE_PYTHON_INSTALLER)"
  if errorlevel 1 (
    echo [setup] WARNING: Failed to download app-private Python installer.
    echo [setup] WARNING: Failed to download app-private Python installer. >> "%LOG%"
    exit /b 1
  )
)

if not exist "%PRIVATE_PYTHON_INSTALLER%" (
  echo [setup] WARNING: Python installer is missing: %PRIVATE_PYTHON_INSTALLER%
  echo [setup] WARNING: Python installer is missing: %PRIVATE_PYTHON_INSTALLER% >> "%LOG%"
  exit /b 1
)

echo [setup] Running app-private Python installer...
echo [setup] Running app-private Python installer: %PRIVATE_PYTHON_INSTALLER% >> "%LOG%"
"%PRIVATE_PYTHON_INSTALLER%" /passive InstallAllUsers=0 TargetDir="%PRIVATE_PYTHON_HOME%" PrependPath=0 Include_launcher=0 Include_exe=1 Include_lib=1 Include_pip=1 Include_tcltk=1 Include_test=0 Include_doc=0 Shortcuts=0 /log "%LOG_DIR%\python-install.log"
if errorlevel 1 (
  echo [setup] WARNING: App-private Python installer failed.
  echo [setup] WARNING: App-private Python installer failed. See %LOG_DIR%\python-install.log >> "%LOG%"
  exit /b 1
)

if not exist "%PRIVATE_PYTHON_EXE%" (
  echo [setup] WARNING: App-private Python install finished, but python.exe was not found.
  echo [setup] WARNING: Expected app-private Python at %PRIVATE_PYTHON_EXE% >> "%LOG%"
  exit /b 1
)

"%PRIVATE_PYTHON_EXE%" -c "import sys, venv; raise SystemExit(0 if '.'.join(map(str, sys.version_info[:2])) == '%PRIVATE_PYTHON_MM%' else 1)" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [setup] WARNING: App-private Python validation failed.
  echo [setup] WARNING: App-private Python validation failed. >> "%LOG%"
  exit /b 1
)

echo [setup] App-private Python installed: %PRIVATE_PYTHON_EXE%
echo [setup] App-private Python installed: %PRIVATE_PYTHON_EXE% >> "%LOG%"
exit /b 0

:ensure_venv
set "TARGET=%~1"
set "LABEL=%~2"
set "PY=%TARGET%\Scripts\python.exe"
set "VENV_MM="
set "RECREATE=0"

echo [setup] Preparing %LABEL% venv...
echo [setup] Preparing %LABEL% venv (%TARGET%) >> "%LOG%"

if exist "%PY%" if exist "%TARGET%\pyvenv.cfg" for /f "tokens=3" %%i in ('findstr /b /c:"version = " "%TARGET%\pyvenv.cfg"') do set "VENV_MM=%%i"
if defined VENV_MM set "VENV_MM=%VENV_MM:~0,4%"

if not exist "%PY%" set "RECREATE=1"
if exist "%PY%" if not defined VENV_MM set "RECREATE=1"
if exist "%PY%" if defined VENV_MM if /I not "%VENV_MM%"=="%BASE_PY_MM%" set "RECREATE=1"

if "%RECREATE%"=="1" (
  echo [setup] Recreating %LABEL% venv...
  echo [setup] Recreating %LABEL% venv... >> "%LOG%"
  call :base_py -m venv --clear "%TARGET%" >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo [setup] ERROR: Failed to recreate %LABEL% venv at %TARGET%. >> "%LOG%"
    exit /b 1
  )
)

if not exist "%PY%" (
  echo [setup] Creating %LABEL% venv...
  echo [setup] Creating %LABEL% venv... >> "%LOG%"
  call :base_py -m venv "%TARGET%" >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo [setup] ERROR: Failed to create %LABEL% venv at %TARGET%. >> "%LOG%"
    exit /b 1
  )
)

"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo [setup] Bootstrapping pip in %LABEL% venv...
  echo [setup] Bootstrapping pip in %LABEL% venv... >> "%LOG%"
  "%PY%" -m ensurepip --upgrade >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo [setup] ERROR: ensurepip failed for %LABEL% venv. >> "%LOG%"
    exit /b 1
  )
)

echo [setup] Upgrading pip tooling in %LABEL% venv...
echo [setup] Upgrading pip tooling in %LABEL% venv... >> "%LOG%"
"%PY%" -m pip install --upgrade --progress-bar on pip "setuptools<82" wheel
if errorlevel 1 (
  echo [setup] ERROR: Failed to upgrade tooling in %LABEL% venv. >> "%LOG%"
  exit /b 1
)
echo [setup] Tooling ready in %LABEL% venv.
echo [setup] Tooling ready in %LABEL% venv. >> "%LOG%"
exit /b 0

:install_requirements
set "PY=%~1"
set "REQ=%~2"
set "LABEL=%~3"

if not exist "%REQ%" (
  echo [setup] ERROR: Missing requirements file: %REQ% >> "%LOG%"
  echo [setup] ERROR: Missing requirements file: %REQ%
  exit /b 1
)

echo [setup] Installing %LABEL%...
echo [setup] Installing %LABEL% from %REQ% >> "%LOG%"
echo [setup] This step may take several minutes; live pip output follows.
echo [setup] Live pip output follows for %LABEL%. >> "%LOG%"
"%PY%" -m pip install --progress-bar on -r "%REQ%"
if errorlevel 1 (
  echo [setup] ERROR: Failed to install %LABEL%. >> "%LOG%"
  exit /b 1
)
echo [setup] Completed %LABEL%.
echo [setup] Completed %LABEL%. >> "%LOG%"
exit /b 0

:install_voice_deps
set "PY=%~1"
set "LABEL=%~2"

echo [setup] Installing %LABEL%...
echo [setup] Installing %LABEL%: %VOICE_DEPS% >> "%LOG%"
echo [setup] Live pip output follows for %LABEL%. >> "%LOG%"
"%PY%" -m pip install --progress-bar on %VOICE_DEPS%
if errorlevel 1 (
  echo [setup] ERROR: Failed to install %LABEL%. >> "%LOG%"
  exit /b 1
)
echo [setup] Completed %LABEL%.
echo [setup] Completed %LABEL%. >> "%LOG%"
exit /b 0

:install_optional_deps
set "PY=%~1"
set "DEPS=%~2"
set "LABEL=%~3"

if "%DEPS%"=="" exit /b 0
echo [setup] Installing optional %LABEL%...
echo [setup] Installing optional %LABEL%: %DEPS% >> "%LOG%"
echo [setup] Live pip output follows for optional %LABEL%. >> "%LOG%"
"%PY%" -m pip install --progress-bar on %DEPS%
if errorlevel 1 (
  echo [setup] WARNING: Failed to install optional %LABEL%. >> "%LOG%"
  echo [setup] WARNING: Optional %LABEL% failed. Kokoro voice will remain disabled until installed.
  exit /b 0
)
echo [setup] Completed optional %LABEL%.
echo [setup] Completed optional %LABEL%. >> "%LOG%"
exit /b 0

:check_ffmpeg_runtime
set "PY=%~1"

if not exist "%PY%" (
  echo [setup] WARNING: ffmpeg check skipped ^(missing Python: %PY%^).
  echo [setup] WARNING: ffmpeg check skipped ^(missing Python: %PY%^). >> "%LOG%"
  exit /b 0
)

echo [setup] Checking bundled ffmpeg runtime...
echo [setup] Checking bundled ffmpeg runtime with %PY% >> "%LOG%"
"%PY%" -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
if errorlevel 1 (
  echo [setup] WARNING: bundled ffmpeg check failed; Sequence to MP4 may require ffmpeg on PATH.
  echo [setup] WARNING: bundled ffmpeg check failed; Sequence to MP4 may require ffmpeg on PATH. >> "%LOG%"
  exit /b 0
)
echo [setup] Bundled ffmpeg runtime is ready.
echo [setup] Bundled ffmpeg runtime is ready. >> "%LOG%"
exit /b 0

:check_mediator_runtime
set "HAS_NODE=1"
set "HAS_NPX=1"
set "HAS_CODEX_SCRIPT=0"

where node >nul 2>&1
if errorlevel 1 set "HAS_NODE=0"

where npx >nul 2>&1
if errorlevel 1 set "HAS_NPX=0"

if exist "%CD%\tools\codex.ps1" set "HAS_CODEX_SCRIPT=1"

if "%HAS_NODE%%HAS_NPX%"=="11" (
  echo [setup] Mediator runtime tools ready ^(node, npx^).
  echo [setup] Mediator runtime tools ready ^(node, npx^). >> "%LOG%"
) else (
  echo [setup] WARNING: Mediator runtime is incomplete.
  echo [setup] WARNING: Mediator runtime is incomplete. >> "%LOG%"
  if "%HAS_NODE%"=="0" (
    echo [setup] WARNING: Missing tool: node
    echo [setup] WARNING: Missing tool: node >> "%LOG%"
  )
  if "%HAS_NPX%"=="0" (
    echo [setup] WARNING: Missing tool: npx
    echo [setup] WARNING: Missing tool: npx >> "%LOG%"
  )
  echo [setup] WARNING: Install Node.js to run Mediator via tools\codex.ps1.
  echo [setup] WARNING: Install Node.js to run Mediator via tools\codex.ps1. >> "%LOG%"
)

if "%HAS_CODEX_SCRIPT%"=="1" (
  echo [setup] Mediator launch script found: tools\codex.ps1
  echo [setup] Mediator launch script found: tools\codex.ps1 >> "%LOG%"
) else (
  echo [setup] WARNING: Mediator launch script not found: tools\codex.ps1
  echo [setup] WARNING: Mediator launch script not found: tools\codex.ps1 >> "%LOG%"
)
exit /b 0

:check_keyboard_sequence_runtime
set "PY=%~1"

if not exist "%PY%" (
  echo [setup] WARNING: keyboard_sequence check skipped ^(missing Python: %PY%^).
  echo [setup] WARNING: keyboard_sequence check skipped ^(missing Python: %PY%^). >> "%LOG%"
  exit /b 0
)

echo [setup] Checking keyboard_sequence runtime...
echo [setup] Checking keyboard_sequence runtime with %PY% >> "%LOG%"
"%PY%" -c "import importlib; from PySide6 import QtCore, QtGui, QtWidgets; importlib.import_module('nodes.keyboard_sequence.spec')"
if errorlevel 1 (
  echo [setup] WARNING: keyboard_sequence sanity check failed.
  echo [setup] WARNING: keyboard_sequence sanity check failed. >> "%LOG%"
  echo [setup] WARNING: Verify PySide6 and node imports in the root venv.
  echo [setup] WARNING: Verify PySide6 and node imports in the root venv. >> "%LOG%"
  exit /b 0
)
echo [setup] keyboard_sequence runtime is ready.
echo [setup] keyboard_sequence runtime is ready. >> "%LOG%"
exit /b 0

:setup_image_gs_runtime
set "PY=%~1"

if /I "%QUBITMCP_SKIP_IMAGE_GS%"=="1" (
  echo [setup] Skipping Image-GS runtime ^(QUBITMCP_SKIP_IMAGE_GS=1^).
  echo [setup] Skipping Image-GS runtime ^(QUBITMCP_SKIP_IMAGE_GS=1^). >> "%LOG%"
  exit /b 0
)

if not exist "%IMAGE_GS_SETUP_SCRIPT%" (
  echo [setup] WARNING: Image-GS setup script not found: %IMAGE_GS_SETUP_SCRIPT%
  echo [setup] WARNING: Image-GS setup script not found: %IMAGE_GS_SETUP_SCRIPT% >> "%LOG%"
  exit /b 0
)

if not exist "%PY%" (
  echo [setup] WARNING: Image-GS setup skipped ^(missing Python: %PY%^).
  echo [setup] WARNING: Image-GS setup skipped ^(missing Python: %PY%^). >> "%LOG%"
  exit /b 0
)

echo [setup] Preparing Image-GS runtime...
echo [setup] Preparing Image-GS runtime with %IMAGE_GS_SETUP_SCRIPT% >> "%LOG%"
call "%IMAGE_GS_SETUP_SCRIPT%" "%APP_HOME%" "%PY%"
if errorlevel 1 (
  echo [setup] WARNING: Image-GS runtime setup failed. Image-to-splat tools will remain unavailable until setup is rerun.
  echo [setup] WARNING: Image-GS runtime setup failed. >> "%LOG%"
  exit /b 0
)

echo [setup] Image-GS runtime is ready.
echo [setup] Image-GS runtime is ready. >> "%LOG%"
exit /b 0

:setup_qubit_deck_controller
if not exist "%QDECK_SETUP_SCRIPT%" (
  echo [setup] WARNING: QubitDeckController setup script not found: %QDECK_SETUP_SCRIPT%
  echo [setup] WARNING: QubitDeckController setup script not found: %QDECK_SETUP_SCRIPT% >> "%LOG%"
  exit /b 0
)

echo [setup] Running QubitDeckController setup...
echo [setup] Running QubitDeckController setup script: %QDECK_SETUP_SCRIPT% >> "%LOG%"
call "%QDECK_SETUP_SCRIPT%"
if errorlevel 1 (
  echo [setup] ERROR: QubitDeckController setup failed.
  echo [setup] ERROR: QubitDeckController setup failed. >> "%LOG%"
  exit /b 1
)
echo [setup] QubitDeckController setup complete.
echo [setup] QubitDeckController setup complete. >> "%LOG%"
exit /b 0

:create_windows_shortcuts
set "SHORTCUT_SCRIPT=%CD%\create_qubit_shortcut.ps1"

if not exist "%SHORTCUT_SCRIPT%" (
  echo [setup] WARNING: Shortcut helper not found: %SHORTCUT_SCRIPT%
  echo [setup] WARNING: Shortcut helper not found: %SHORTCUT_SCRIPT% >> "%LOG%"
  exit /b 0
)

where powershell >nul 2>&1
if errorlevel 1 (
  echo [setup] WARNING: powershell.exe not found; skipping shortcut creation.
  echo [setup] WARNING: powershell.exe not found; skipping shortcut creation. >> "%LOG%"
  exit /b 0
)

echo [setup] Creating launcher shortcuts...
echo [setup] Creating launcher shortcuts... >> "%LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SHORTCUT_SCRIPT%" -Scope Desktop >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [setup] WARNING: Launcher shortcut creation failed.
  echo [setup] WARNING: Launcher shortcut creation failed. >> "%LOG%"
  exit /b 0
)

echo [setup] Launcher shortcuts refreshed.
echo [setup] Launcher shortcuts refreshed. >> "%LOG%"
exit /b 0

:resolve_app_home
set "TARGET_DIR=%~f1"
set "RESOLVED_APP_HOME="

if defined QUBITFIELD_HOME (
  set "RESOLVED_APP_HOME=%QUBITFIELD_HOME%"
  exit /b 0
)

if defined QUBITMCP_HOME (
  set "RESOLVED_APP_HOME=%QUBITMCP_HOME%"
  exit /b 0
)

if not defined TARGET_DIR exit /b 1

set "WRITE_TEST=%TARGET_DIR%\_qubit_write_test_%RANDOM%_%RANDOM%.tmp"
(echo write-test>"%WRITE_TEST%") >nul 2>&1
if exist "%WRITE_TEST%" (
  del /f /q "%WRITE_TEST%" >nul 2>&1
  set "RESOLVED_APP_HOME=%TARGET_DIR%"
  exit /b 0
)

if defined LOCALAPPDATA (
  set "RESOLVED_APP_HOME=%LOCALAPPDATA%\QubitMCP"
) else (
  set "RESOLVED_APP_HOME=%TEMP%\QubitMCP"
)
exit /b 0

:try_base_python
if defined BASE_PY_EXE exit /b 0
set "CANDIDATE_EXE=%~1"
set "CANDIDATE_ARG=%~2"

if not defined CANDIDATE_EXE exit /b 1

if /I "%CANDIDATE_EXE%"=="py" (
  where py >nul 2>&1
  if errorlevel 1 exit /b 1
  if defined CANDIDATE_ARG (
    py %CANDIDATE_ARG% -c "import sys" >nul 2>&1
  ) else (
    py -3 -c "import sys" >nul 2>&1
  )
  if errorlevel 1 exit /b 1
  set "BASE_PY_EXE=py"
  set "BASE_PY_ARG=%CANDIDATE_ARG%"
  exit /b 0
)

if /I "%CANDIDATE_EXE%"=="python" (
  where python >nul 2>&1
  if errorlevel 1 exit /b 1
  python -c "import sys" >nul 2>&1
  if errorlevel 1 exit /b 1
  set "BASE_PY_EXE=python"
  set "BASE_PY_ARG="
  exit /b 0
)

if not exist "%CANDIDATE_EXE%" exit /b 1
if defined CANDIDATE_ARG (
  "%CANDIDATE_EXE%" %CANDIDATE_ARG% -c "import sys" >nul 2>&1
) else (
  "%CANDIDATE_EXE%" -c "import sys" >nul 2>&1
)
if errorlevel 1 exit /b 1

set "BASE_PY_EXE=%CANDIDATE_EXE%"
set "BASE_PY_ARG="
exit /b 0

:base_py
if defined BASE_PY_ARG (
  "%BASE_PY_EXE%" %BASE_PY_ARG% %*
) else (
  "%BASE_PY_EXE%" %*
)
exit /b %ERRORLEVEL%

:get_base_py_mm
set "BASE_PY_MM="
if /I "%BASE_PY_ARG%"=="-3.10" (
  set "BASE_PY_MM=3.10"
  exit /b 0
)
if /I "%BASE_PY_ARG%"=="-3.12" (
  set "BASE_PY_MM=3.12"
  exit /b 0
)
if /I "%BASE_PY_EXE%"=="%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
  set "BASE_PY_MM=3.10"
  exit /b 0
)
if /I "%BASE_PY_EXE%"=="%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  set "BASE_PY_MM=3.12"
  exit /b 0
)
set "VER_TMP=%TEMP%\_qubit_setup_base_py_%RANDOM%.txt"
call :base_py --version > "%VER_TMP%" 2>&1
if exist "%VER_TMP%" set /p BASE_PY_MM=<"%VER_TMP%"
if defined BASE_PY_MM for /f "tokens=2" %%i in ("%BASE_PY_MM%") do set "BASE_PY_MM=%%i"
if defined BASE_PY_MM set "BASE_PY_MM=%BASE_PY_MM:~0,4%"
if exist "%VER_TMP%" del /f /q "%VER_TMP%" >nul 2>&1
exit /b 0

:fail
echo [setup] FAILED. See %LOG%
echo [setup] FAILED. See %LOG% >> "%LOG%"
type "%LOG%"
popd
exit /b 1

:usage
call :print_usage
popd
exit /b 0

:usage_fail
call :print_usage
popd
exit /b 1

:print_usage
echo Usage: setup.bat [core^|full]
echo   Installs app-private Python %PRIVATE_PYTHON_VERSION% if needed; it is not added to PATH.
echo   core = setup root app env only
echo   full = setup root + librarian + QubitDeckController envs (default)
echo   FBX SDK setup is handled by FBX nodes when needed.
echo   optional env var: QUBITFIELD_HOME=C:\path\to\app_home
echo   optional env var: QUBITMCP_SKIP_IMAGE_GS=1 skips Image-GS download/setup
exit /b 0
