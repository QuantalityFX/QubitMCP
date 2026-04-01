@echo off
setlocal EnableExtensions

pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
  echo [setup] ERROR: Could not enter repository root.
  exit /b 1
)

set "LOG_DIR=%CD%\logs"
if not exist "%LOG_DIR%" (
  mkdir "%LOG_DIR%" >nul 2>&1
)
if not exist "%LOG_DIR%" (
  echo [setup] ERROR: Could not create logs folder: %LOG_DIR%
  popd
  exit /b 1
)
set "LOG=%LOG_DIR%\setup.log"
set "MAIN_REQ=%CD%\requirements.txt"
set "LIB_REQ=%CD%\nodes\librarian\requirements.txt"
set "VOICE_DEPS=SpeechRecognition pyttsx3 pyaudio gTTS pygame faster-whisper pymongo"
set "BASE_PY_EXE="
set "BASE_PY_ARG="
set "BASE_PY_MM="
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
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
echo [setup] Repo: %CD% >> "%LOG%"
echo [setup] Mode: %SETUP_MODE% >> "%LOG%"

call :try_base_python "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" ""
call :try_base_python "py" "-3.10"
call :try_base_python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" ""
call :try_base_python "py" "-3.12"
call :try_base_python "py" "-3"
call :try_base_python "python" ""

if not defined BASE_PY_EXE (
  echo [setup] ERROR: Could not find a usable Python interpreter. >> "%LOG%"
  echo [setup] ERROR: Could not find Python 3.x. Install Python and retry.
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

call :ensure_venv ".venv" "root" || goto :fail
call :install_requirements ".venv\Scripts\python.exe" "%MAIN_REQ%" "root requirements" || goto :fail
call :install_voice_deps ".venv\Scripts\python.exe" "root voice dependencies" || goto :fail
call :check_medigator_runtime

if /I "%SETUP_MODE%"=="full" (
  call :ensure_venv "nodes\librarian\.venv" "librarian" || goto :fail
  call :install_requirements "nodes\librarian\.venv\Scripts\python.exe" "%LIB_REQ%" "librarian requirements" || goto :fail
) else (
  echo [setup] Skipping librarian setup ^(mode=%SETUP_MODE%^).
  echo [setup] Skipping librarian setup ^(mode=%SETUP_MODE%^). >> "%LOG%"
)

call :create_windows_shortcuts

echo [setup] SUCCESS (%SETUP_MODE%)
echo [setup] SUCCESS (%SETUP_MODE%) >> "%LOG%"
echo [setup] Log: %LOG%
popd
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
"%PY%" -m pip install --upgrade --progress-bar on pip setuptools wheel
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

:check_medigator_runtime
set "HAS_NODE=1"
set "HAS_NPX=1"
set "HAS_CODEX_SCRIPT=0"

where node >nul 2>&1
if errorlevel 1 set "HAS_NODE=0"

where npx >nul 2>&1
if errorlevel 1 set "HAS_NPX=0"

if exist "%CD%\tools\codex.ps1" set "HAS_CODEX_SCRIPT=1"

if "%HAS_NODE%%HAS_NPX%"=="11" (
  echo [setup] Medigator runtime tools ready ^(node, npx^).
  echo [setup] Medigator runtime tools ready ^(node, npx^). >> "%LOG%"
) else (
  echo [setup] WARNING: Medigator runtime is incomplete.
  echo [setup] WARNING: Medigator runtime is incomplete. >> "%LOG%"
  if "%HAS_NODE%"=="0" (
    echo [setup] WARNING: Missing tool: node
    echo [setup] WARNING: Missing tool: node >> "%LOG%"
  )
  if "%HAS_NPX%"=="0" (
    echo [setup] WARNING: Missing tool: npx
    echo [setup] WARNING: Missing tool: npx >> "%LOG%"
  )
  echo [setup] WARNING: Install Node.js to run Medigator via tools\codex.ps1.
  echo [setup] WARNING: Install Node.js to run Medigator via tools\codex.ps1. >> "%LOG%"
)

if "%HAS_CODEX_SCRIPT%"=="1" (
  echo [setup] Medigator launch script found: tools\codex.ps1
  echo [setup] Medigator launch script found: tools\codex.ps1 >> "%LOG%"
) else (
  echo [setup] WARNING: Medigator launch script not found: tools\codex.ps1
  echo [setup] WARNING: Medigator launch script not found: tools\codex.ps1 >> "%LOG%"
)
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
echo   core = setup root app env only
echo   full = setup root + librarian envs (default)
exit /b 0
