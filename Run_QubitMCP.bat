@echo off
setlocal EnableExtensions
set "SCRIPT_DIR=%~dp0"
if defined SCRIPT_DIR if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "RESOLVED_APP_HOME="
call :resolve_app_home "%SCRIPT_DIR%"
if errorlevel 1 (
  echo [setup] Could not resolve writable app home.
  pause
  exit /b 1
)
if not defined RESOLVED_APP_HOME (
  echo [setup] Writable app home is empty.
  pause
  exit /b 1
)
set "APP_HOME=%RESOLVED_APP_HOME%"
set "QUBITMCP_HOME=%APP_HOME%"

if /I not "%APP_HOME%"=="%SCRIPT_DIR%" (
  echo [setup] Repo is read-only. Using writable app home:
  echo         "%APP_HOME%"
)

set "VENV_DIR=%APP_HOME%\.venv"
set "PYTHONW=%VENV_DIR%\Scripts\pythonw.exe"
set "PYTHONE=%VENV_DIR%\Scripts\python.exe"
set "SETUP_BAT=%SCRIPT_DIR%\setup.bat"
set "SHORTCUT_SCRIPT=%SCRIPT_DIR%\create_qubit_shortcut.ps1"
set "LAUNCHER="

REM --- Ensure core app env exists (delegates install logic to setup.bat)
if not exist "%PYTHONE%" (
  if not exist "%SETUP_BAT%" (
    echo [setup] Missing setup.bat at "%SETUP_BAT%"
    pause
    exit /b 1
  )
  echo [setup] Core environment missing. Running setup.bat core...
  call "%SETUP_BAT%" core
  if errorlevel 1 (
    echo [setup] setup.bat core failed.
    pause
    exit /b 1
  )
)

if exist "%PYTHONW%" set "LAUNCHER=%PYTHONW%"
if not defined LAUNCHER if exist "%PYTHONE%" set "LAUNCHER=%PYTHONE%"

if not defined LAUNCHER (
  echo [setup] Waiting for venv launcher...
  for /L %%I in (1,1,5) do (
    timeout /t 1 /nobreak >nul
    if exist "%PYTHONW%" set "LAUNCHER=%PYTHONW%"
    if not defined LAUNCHER if exist "%PYTHONE%" set "LAUNCHER=%PYTHONE%"
  )
)

if not defined LAUNCHER (
  echo [setup] Missing venv launcher after setup:
  echo         "%PYTHONW%"
  echo         "%PYTHONE%"
  pause
  exit /b 1
)

if exist "%SHORTCUT_SCRIPT%" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%SHORTCUT_SCRIPT%" -Scope Desktop >nul 2>&1
)

REM --- Launch (force working dir to script folder so relative paths/icons work)
start "" /D "%SCRIPT_DIR%" "%LAUNCHER%" "%SCRIPT_DIR%\echograph_app.py"
endlocal
exit /b 0

:resolve_app_home
set "TARGET_DIR=%~f1"
set "RESOLVED_APP_HOME="

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
