@echo off
setlocal EnableExtensions
REM ─────────────────────────────────────────────────────────────
REM Librarian setup: venv + pinned deps + (optional) CUDA PyTorch + sanity checks
REM Falls back to CPU PyTorch if CUDA wheel fails.
REM All actions are logged to setup.log in this folder.
REM ─────────────────────────────────────────────────────────────

pushd "%~dp0"
if %ERRORLEVEL% NEQ 0 (
  echo [setup] Cannot cd to script folder
  pause
  exit /b 1
)

set "VENV=.venv"
set "PYEXE=%CD%\%VENV%\Scripts\python.exe"
set "PIPEXE=%CD%\%VENV%\Scripts\pip.exe"
set "LOG=%CD%\setup.log"
set "TMPPY=%TEMP%\_librarian_setup_check_%RANDOM%.py"

echo [setup] ==== START (%DATE% %TIME%) ==== > "%LOG%"
echo [setup] CWD: %CD% >> "%LOG%"
echo [setup] PYEXE: %PYEXE% >> "%LOG%"

REM 0) Create venv (prefers Python 3.11)
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
      echo [setup] ERROR: No Python found on PATH. >> "%LOG%"
      echo [setup] ERROR: No Python found. See setup.log.
      goto :PAUSE_FAIL
    )
  )
)

if not exist "%PYEXE%" (
  echo [setup] ERROR: Failed to create venv at "%VENV%". >> "%LOG%"
  echo [setup] ERROR: venv creation failed. See setup.log.
  goto :PAUSE_FAIL
)

REM 1) Upgrade pip/setuptools/wheel
echo [setup] Upgrading pip/setuptools/wheel...
echo [setup] Upgrading pip/setuptools/wheel... >> "%LOG%"
"%PYEXE%" -m pip install --upgrade pip setuptools wheel >> "%LOG%" 2>&1
if %ERRORLEVEL% NEQ 0 (
  echo [setup] ERROR: pip upgrade failed. >> "%LOG%"
  echo [setup] ERROR: pip upgrade failed. See setup.log.
  goto :PAUSE_FAIL
)

REM 2) Install pinned requirements (your requirements.txt)
if exist "requirements.txt" (
  echo [setup] Installing requirements.txt ...
  echo [setup] Installing requirements.txt ... >> "%LOG%"
  "%PYEXE%" -m pip install -r "requirements.txt" >> "%LOG%" 2>&1
  if %ERRORLEVEL% NEQ 0 (
    echo [setup] ERROR: requirements install failed. >> "%LOG%"
    echo [setup] ERROR: requirements install failed. See setup.log.
    goto :PAUSE_FAIL
  )
) else (
  echo [setup] WARNING: No requirements.txt found. Skipping core packages. >> "%LOG%"
  echo [setup] WARNING: No requirements.txt found. Skipping core packages.
)

REM 3) (Optional) Install PyTorch GPU, fallback to CPU
set "TORCH_VER=2.2.2"
echo [setup] Installing PyTorch CUDA build (cu121)...
echo [setup] Installing PyTorch CUDA build (cu121)... >> "%LOG%"
"%PYEXE%" -m pip install --index-url https://download.pytorch.org/whl/cu121 torch==%TORCH_VER% --no-cache-dir >> "%LOG%" 2>&1

REM Verify torch & CUDA via a temp Python file
> "%TMPPY%" echo import sys
>>"%TMPPY%" echo try:
>>"%TMPPY%" echo^    import torch
>>"%TMPPY%" echo^    print("torch_version", torch.__version__)
>>"%TMPPY%" echo^    print("cuda_available", torch.cuda.is_available())
>>"%TMPPY%" echo^    print("cuda_device", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no cuda")
>>"%TMPPY%" echo^    sys.exit(0)
>>"%TMPPY%" echo except Exception as e:
>>"%TMPPY%" echo^    print("verify_error", repr(e))
>>"%TMPPY%" echo^    sys.exit(2)

"%PYEXE%" "%TMPPY%" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
del "%TMPPY%" >nul 2>&1

if NOT "%RC%"=="0" (
  echo [setup] CUDA torch not usable. Falling back to CPU wheel... >> "%LOG%"
  echo [setup] Installing PyTorch CPU wheel (fallback)...
  "%PYEXE%" -m pip install --index-url https://download.pytorch.org/whl/cpu torch==%TORCH_VER% --no-cache-dir >> "%LOG%" 2>&1
  if %ERRORLEVEL% NEQ 0 (
    echo [setup] ERROR: PyTorch CPU install failed. >> "%LOG%"
    echo [setup] ERROR: PyTorch CPU install failed. See setup.log.
    goto :PAUSE_FAIL
  )
)

REM 4) Add Ollama embedding adapter once (needed for GPU-friendly embeddings without Torch)
echo [setup] Adding Ollama embedding adapter...
echo [setup] Adding Ollama embedding adapter... >> "%LOG%"
"%PYEXE%" -m pip install llama-index-embeddings-ollama==0.1.3 >> "%LOG%" 2>&1
if %ERRORLEVEL% NEQ 0 (
  echo [setup] ERROR: ollama embedding adapter install failed. See setup.log.
  goto :PAUSE_FAIL
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

REM 7) Sanity diagnostics
set "TMPPY=%TEMP%\_librarian_diag_%RANDOM%.py"
> "%TMPPY%" echo import sys, faiss
>>"%TMPPY%" echo print("inner.python", sys.version)
>>"%TMPPY%" echo
>>"%TMPPY%" echo try:
>>"%TMPPY%" echo^    import PySide6; print("inner.qt", "PySide6")
>>"%TMPPY%" echo except Exception:
>>"%TMPPY%" echo^    try:
>>"%TMPPY%" echo^        import PySide2; print("inner.qt", "PySide2")
>>"%TMPPY%" echo^    except Exception:
>>"%TMPPY%" echo^        print("inner.qt", "NONE")
>>"%TMPPY%" echo
>>"%TMPPY%" echo try:
>>"%TMPPY%" echo^    import llama_index; print("inner.llama_index", True)
>>"%TMPPY%" echo except Exception:
>>"%TMPPY%" echo^    print("inner.llama_index", False)
>>"%TMPPY%" echo
>>"%TMPPY%" echo try:
>>"%TMPPY%" echo^    import pydantic_core; print("inner.pydantic_core", True)
>>"%TMPPY%" echo except Exception:
>>"%TMPPY%" echo^    print("inner.pydantic_core", False)
>>"%TMPPY%" echo
>>"%TMPPY%" echo try:
>>"%TMPPY%" echo^    import transformers, torch
>>"%TMPPY%" echo^    print("inner.transformers", transformers.__version__)
>>"%TMPPY%" echo^    print("inner.torch", torch.__version__)
>>"%TMPPY%" echo^    print("inner.cuda", torch.cuda.is_available())
>>"%TMPPY%" echo except Exception as e:
>>"%TMPPY%" echo^    print("inner.transformers_or_torch_error", repr(e))
>>"%TMPPY%" echo
>>"%TMPPY%" echo print("inner.faiss", faiss.__version__)

"%PYEXE%" "%TMPPY%" >> "%LOG%" 2>&1
del "%TMPPY%" >nul 2>&1

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
