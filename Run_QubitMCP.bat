@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "PYTHONW=%VENV_DIR%\Scripts\pythonw.exe"
set "SETUP_BAT=%SCRIPT_DIR%setup.bat"

REM --- Ensure core app env exists (delegates install logic to setup.bat)
if not exist "%PYTHONW%" (
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

if not exist "%PYTHONW%" (
  echo [setup] Missing venv launcher after setup: "%PYTHONW%"
  pause
  exit /b 1
)

REM --- Launch (force working dir to script folder so relative paths/icons work)
start "" /D "%SCRIPT_DIR%" "%PYTHONW%" "%SCRIPT_DIR%echograph_app.py"
endlocal
