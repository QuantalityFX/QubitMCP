@echo off
setlocal EnableExtensions

pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
  echo [setup] ERROR: Could not enter repository root.
  exit /b 1
)

set "LOG=%CD%\setup.log"
set "MAIN_REQ=%CD%\requirements.txt"
set "LIB_REQ=%CD%\nodes\librarian\requirements.txt"
set "BASE_PY_EXE="
set "BASE_PY_ARG="
set "BASE_PY_MM="
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

echo [setup] ==== START %DATE% %TIME% ==== > "%LOG%"
echo [setup] Repo: %CD% >> "%LOG%"

if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
  set "BASE_PY_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
)

if not defined BASE_PY_EXE (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3.10 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
      set "BASE_PY_EXE=py"
      set "BASE_PY_ARG=-3.10"
    )
  )
)

if not defined BASE_PY_EXE (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "BASE_PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  )
)

if not defined BASE_PY_EXE (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
      set "BASE_PY_EXE=py"
      set "BASE_PY_ARG=-3.12"
    )
  )
)

if not defined BASE_PY_EXE (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
      set "BASE_PY_EXE=py"
      set "BASE_PY_ARG=-3"
    )
  )
)

if not defined BASE_PY_EXE (
  where python >nul 2>&1
  if not errorlevel 1 set "BASE_PY_EXE=python"
)

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

call :ensure_venv "nodes\librarian\.venv" "librarian" || goto :fail
call :install_requirements "nodes\librarian\.venv\Scripts\python.exe" "%LIB_REQ%" "librarian requirements" || goto :fail

echo [setup] SUCCESS
echo [setup] SUCCESS >> "%LOG%"
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

"%PY%" -m pip install --upgrade pip setuptools wheel >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [setup] ERROR: Failed to upgrade tooling in %LABEL% venv. >> "%LOG%"
  exit /b 1
)
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
"%PY%" -m pip install -r "%REQ%" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [setup] ERROR: Failed to install %LABEL%. >> "%LOG%"
  exit /b 1
)
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
set "VER_TMP=%TEMP%\_qubit_setup_base_py_%RANDOM%.txt"
call :base_py -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" > "%VER_TMP%" 2>nul
if exist "%VER_TMP%" set /p BASE_PY_MM=<"%VER_TMP%"
if exist "%VER_TMP%" del /f /q "%VER_TMP%" >nul 2>&1
exit /b 0

:fail
echo [setup] FAILED. See %LOG%
echo [setup] FAILED. See %LOG% >> "%LOG%"
type "%LOG%"
popd
exit /b 1
