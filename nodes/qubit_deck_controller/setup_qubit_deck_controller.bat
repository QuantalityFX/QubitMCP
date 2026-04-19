@echo off
setlocal EnableExtensions

pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
  echo [qdeck-setup] ERROR: Could not enter script directory.
  exit /b 1
)

set "VENV=.venv"
set "PYEXE=%CD%\%VENV%\Scripts\python.exe"
set "REQ=%CD%\requirements.txt"

if not exist "%PYEXE%" (
  echo [qdeck-setup] Creating %VENV%...
  py -3.11 -m venv "%VENV%" >nul 2>&1
  if not exist "%PYEXE%" py -3 -m venv "%VENV%" >nul 2>&1
  if not exist "%PYEXE%" python -m venv "%VENV%" >nul 2>&1
)

if not exist "%PYEXE%" (
  echo [qdeck-setup] ERROR: Failed to create venv.
  popd
  exit /b 1
)

echo [qdeck-setup] Upgrading pip...
"%PYEXE%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
  echo [qdeck-setup] ERROR: Failed to upgrade pip tooling.
  popd
  exit /b 1
)

if exist "%REQ%" (
  echo [qdeck-setup] Installing requirements...
  "%PYEXE%" -m pip install -r "%REQ%"
  if errorlevel 1 (
    echo [qdeck-setup] ERROR: Failed to install requirements.
    popd
    exit /b 1
  )
) else (
  echo [qdeck-setup] WARNING: requirements.txt not found, skipping installs.
)

echo [qdeck-setup] Ready.
popd
exit /b 0
