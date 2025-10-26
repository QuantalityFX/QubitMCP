@echo off
setlocal
cd /d "%~dp0"

set "VENV=%CD%\.venv"
set "PIP=%VENV%\Scripts\pip.exe"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PIP%" (
  echo [fix] Venv not found at "%VENV%".
  echo [fix] Run librarianSetup.bat first.
  exit /b 1
)

echo [fix] Upgrading pip / setuptools / wheel ...
"%PIP%" install --upgrade pip setuptools wheel

echo [fix] Forcing prebuilt wheels for Pydantic v2 (includes pydantic-core) ...
"%PIP%" install --only-binary=:all: "pydantic>=2.7,<3" "pydantic-core>=2.18,<3" typing_extensions>=4.10

echo [fix] Reinstalling project requirements ...
"%PIP%" install -r requirements.txt

echo [fix] Verifying imports ...
"%PY%" -c "import pydantic, pydantic_core, llama_index; print('OK: pydantic', pydantic.__version__)"

echo [fix] Done.
endlocal
