@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "PYTHONW=%VENV_DIR%\Scripts\pythonw.exe"

REM --- Create venv if missing
if not exist "%PYTHONW%" (
  echo [setup] Creating venv...
  py -3 -m venv "%VENV_DIR%" || (echo Failed to create venv. Ensure the Python launcher 'py' is installed. & pause & exit /b 1)
  call "%VENV_DIR%\Scripts\activate.bat"
  python -m pip install --upgrade pip setuptools wheel
  if exist "%SCRIPT_DIR%requirements.txt" (
    pip install -r "%SCRIPT_DIR%requirements.txt"
  ) else (
    pip install PySide6
  )
)

REM --- Launch (force working dir to script folder so relative paths/icons work)
start "" /D "%SCRIPT_DIR%" "%PYTHONW%" "%SCRIPT_DIR%echograph_app.py"
endlocal
