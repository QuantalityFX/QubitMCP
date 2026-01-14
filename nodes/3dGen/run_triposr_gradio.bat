@echo off
setlocal

set "ROOT=%~dp0"
set "TRIPOSR_DIR=%ROOT%TripoSR"
set "PYEXE=%TRIPOSR_DIR%\.venv\Scripts\python.exe"

if not exist "%PYEXE%" (
  echo [TripoSR] venv not found: "%PYEXE%"
  echo [TripoSR] Run setup first.
  pause
  exit /b 1
)

set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1"
set "CUDA_PATH_V13_1=%CUDA_PATH%"
set "CUDAToolkit_ROOT=%CUDA_PATH%"
set "CUDAToolkitDir=%CUDA_PATH%"
set "NVTOOLSEXT_PATH=C:\tmp\nvtx\nsight_nvtx-windows-x86_64-1.21018621-archive\NvToolsExt"
set "PATH=%CUDA_PATH%\bin;%CUDA_PATH%\libnvvp;%PATH%"

pushd "%TRIPOSR_DIR%"
"%PYEXE%" gradio_app.py
popd

endlocal
