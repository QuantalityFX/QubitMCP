from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
VENV_DIR = HERE / ".venv"
REQ_FILE = HERE / "requirements.txt"
APP_SCRIPT = HERE / "qubit_deck_qt.py"
LOG_FILE = HERE / "qdeck_debugger_child.log"


def _hidden_subprocess_kwargs(include_no_window: bool = True) -> dict:
    kwargs: dict = {}
    if os.name != "nt":
        return kwargs
    creationflags = 0
    if include_no_window and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW"))
    if creationflags:
        kwargs["creationflags"] = creationflags
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        try:
            startupinfo = subprocess.STARTUPINFO()
            if hasattr(subprocess, "STARTF_USESHOWWINDOW"):
                startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW"))
            if hasattr(subprocess, "SW_HIDE"):
                startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE"))
        except Exception:
            startupinfo = None
    if startupinfo is not None:
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _venv_pythonw() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "pythonw.exe"
    return _venv_python()


def _run_checked(cmd: list[str], *, cwd: Path, env: dict | None = None, verbose: bool = False) -> None:
    if verbose:
        print(f"[qdeck-launcher] $ {subprocess.list2cmdline(cmd)}")
    process = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **_hidden_subprocess_kwargs(include_no_window=True),
    )
    if process.returncode == 0:
        if verbose and process.stdout:
            print(process.stdout.strip())
        return
    output = (process.stdout or "").strip()
    if output:
        raise RuntimeError(output)
    raise RuntimeError(f"Command failed with exit code {process.returncode}: {cmd}")


def _module_available(py: Path, module_name: str, *, verbose: bool = False) -> bool:
    if not py.exists():
        return False
    cmd = [str(py), "-c", f"import {module_name}"]
    process = subprocess.run(
        cmd,
        cwd=str(HERE),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **_hidden_subprocess_kwargs(include_no_window=True),
    )
    if process.returncode == 0:
        return True
    if verbose:
        out = (process.stdout or "").strip()
        if out:
            print(f"[qdeck-launcher] dependency probe failed: {out}")
    return False


def _create_venv(verbose: bool = False) -> Path:
    py = _venv_python()
    if py.exists():
        return py

    base_cmds: list[list[str]] = []
    if os.name == "nt":
        base_cmds.append(["py", "-3.11", "-m", "venv", str(VENV_DIR)])
        base_cmds.append(["py", "-3", "-m", "venv", str(VENV_DIR)])
    base_cmds.append([sys.executable, "-m", "venv", str(VENV_DIR)])
    base_cmds.append(["python", "-m", "venv", str(VENV_DIR)])

    created = False
    last_error = ""
    for cmd in base_cmds:
        try:
            _run_checked(cmd, cwd=HERE, verbose=verbose)
            created = True
            break
        except Exception as exc:
            last_error = str(exc)
            continue

    if not created or not py.exists():
        raise RuntimeError(
            "Failed to create node-local venv at "
            f"{VENV_DIR}. Last error: {last_error or 'unknown'}"
        )
    return py


def ensure_venv(verbose: bool = False) -> Path:
    py = _create_venv(verbose=verbose)
    if _module_available(py, "PySide6", verbose=verbose):
        return py
    _run_checked(
        [str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
        cwd=HERE,
        verbose=verbose,
    )
    if REQ_FILE.exists():
        _run_checked(
            [str(py), "-m", "pip", "install", "-r", str(REQ_FILE)],
            cwd=HERE,
            verbose=verbose,
        )
    return py


def launch(api_base: str = "http://127.0.0.1:8765", verbose: bool = False):
    if not APP_SCRIPT.exists():
        raise RuntimeError(f"Missing debugger script: {APP_SCRIPT}")

    py = ensure_venv(verbose=verbose)
    pyw = _venv_pythonw()
    runner = pyw if pyw.exists() else py

    log = open(LOG_FILE, "w", encoding="utf-8", buffering=1)
    env = os.environ.copy()
    env.setdefault("QUBIT_DECK_CONTROLLER_ROOT", str(HERE))

    cmd = [str(runner), str(APP_SCRIPT), "--api-base", str(api_base or "").strip() or "http://127.0.0.1:8765"]
    popen_kwargs = _hidden_subprocess_kwargs(include_no_window=False)
    if os.name == "nt":
        creationflags = 0x00000008  # DETACHED_PROCESS
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW"))
        popen_kwargs["creationflags"] = creationflags

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(HERE),
            env=env,
            stdout=log,
            stderr=log,
            **popen_kwargs,
        )
        if verbose:
            print(f"[qdeck-launcher] spawned debugger with {runner}")
        return proc
    except Exception as exc:
        raise RuntimeError(f"Failed to spawn debugger: {exc}") from exc


if __name__ == "__main__":
    launch(verbose=True)
