@echo off
setlocal EnableExtensions
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "PYTHONW=%VENV_DIR%\Scripts\pythonw.exe"
set "PYTHONE=%VENV_DIR%\Scripts\python.exe"
set "SETUP_BAT=%SCRIPT_DIR%setup.bat"
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

REM --- Launch (force working dir to script folder so relative paths/icons work)
start "" /D "%SCRIPT_DIR%" "%LAUNCHER%" "%SCRIPT_DIR%echograph_app.py"
endlocal
