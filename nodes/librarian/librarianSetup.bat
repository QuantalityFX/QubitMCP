@echo off
setlocal EnableExtensions
REM ─────────────────────────────────────────────────────────────
REM Librarian setup: venv + deps + folders + .env (always pauses + logs)
REM Double-click to run OR run from any terminal.
REM ─────────────────────────────────────────────────────────────

REM 1) Move to this script’s folder
pushd "%~dp0"
if %ERRORLEVEL% NEQ 0 (
  echo [setup] Cannot cd to script folder
  pause
  exit /b 1
)

set "VENV=.venv"
set "PYEXE=%CD%\%VENV%\Scripts\python.exe"
set "LOG=%CD%\setup.log"

echo [setup] ==== START (%DATE% %TIME%) ==== > "%LOG%"
echo [setup] CWD: %CD% >> "%LOG%"
echo [setup] PYEXE: %PYEXE% >> "%LOG%"

REM 2) Create venv if missing (prefer py -3.11, fallback)
if not exist "%PYEXE%" (
  echo [setup] Creating virtual environment...
  echo [setup] Creating virtual environment... >> "%LOG%"

  where py >nul 2>&1
  if %ERRORLEVEL% EQU 0 (
    py -3.11 -m venv "%VENV%" >> "%LOG%" 2>&1
    if not exist "%PYEXE%" py -3 -m venv "%VENV%" >> "%LOG%" 2>&1
  ) else (
    where python >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
      python -m venv "%VENV%" >> "%LOG%" 2>&1
    ) else (
      echo [setup] No 'py' or 'python' found on PATH. >> "%LOG%"
      echo [setup] ERROR: No Python found. See setup.log.
      goto :PAUSE_FAIL
    )
  )
)

if not exist "%PYEXE%" (
  echo [setup] Failed to create venv at "%VENV%". >> "%LOG%"
  echo [setup] ERROR: venv creation failed. See setup.log.
  goto :PAUSE_FAIL
)

REM 3) Upgrade pip tools
echo [setup] Upgrading pip/setuptools/wheel...
echo [setup] Upgrading pip/setuptools/wheel... >> "%LOG%"
"%PYEXE%" -m pip install --upgrade pip setuptools wheel >> "%LOG%" 2>&1
if %ERRORLEVEL% NEQ 0 (
  echo [setup] ERROR: pip upgrade failed. See setup.log.
  goto :PAUSE_FAIL
)

REM 4) Install requirements (if present)
if exist "requirements.txt" (
  echo [setup] Installing requirements.txt ...
  echo [setup] Installing requirements.txt ... >> "%LOG%"
  "%PYEXE%" -m pip install -r "requirements.txt" >> "%LOG%" 2>&1
  if %ERRORLEVEL% NEQ 0 (
    echo [setup] ERROR: requirements install failed. See setup.log.
    goto :PAUSE_FAIL
  )
) else (
  echo [setup] No requirements.txt found. Skipping packages.
  echo [setup] No requirements.txt found. Skipping packages. >> "%LOG%"
)

REM 5) Ensure local data dirs
if not exist "storage" mkdir "storage" >> "%LOG%" 2>&1
if not exist "docs"    mkdir "docs"    >> "%LOG%" 2>&1

REM 6) Seed .env (quoted paths)
set "CWD=%CD%"
if not exist ".env" (
  >".env"  echo LIBRARIAN_ROOT="%CWD%"
  >>".env" echo OBSIDIAN_DIR="C:\Users\YourName\Documents\Obsidian Vault"
) else (
  findstr /b /c:"LIBRARIAN_ROOT=" ".env" >nul 2>&1 || (
    >>".env" echo LIBRARIAN_ROOT="%CWD%"
  )
)

echo [setup] SUCCESS. >> "%LOG%"
echo.
echo [setup] Done. Log saved to:
echo         %LOG%
echo.
pause
popd
endlocal
exit /b 0

:PAUSE_FAIL
echo.
echo [setup] FAILED. See log:
echo         %LOG%
echo.
type "%LOG%" | more
echo.
pause
popd
endlocal
exit /b 1
