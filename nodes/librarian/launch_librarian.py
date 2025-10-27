# nodes/librarian/launch_librarian.py
from __future__ import annotations
import os, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

def launch(verbose: bool = False):
    """
    Spawn the Librarian UI in a separate process using the Librarian venv.
    On success: no console output (unless verbose=True).
    On failure: raises RuntimeError. Child stdout/stderr go to librarian_child.log.
    """
    venv = HERE / ".venv" / "Scripts"
    pyw = venv / "pythonw.exe"
    pye = venv / "python.exe"
    py = pyw if pyw.exists() else pye
    if not py.exists():
        msg = (f"[launcher] Missing venv Python at: {venv}\n"
               "Run nodes\\librarian\\librarianSetup.bat first.")
        if verbose:
            print(msg)
        raise RuntimeError(msg)

    # Log file for the child process
    log_path = HERE / "librarian_child.log"
    log = open(log_path, "w", encoding="utf-8", buffering=1)

    code = (
        "import sys, os; "
        f"sys.path.insert(0, r'{HERE}'); "
        f"os.environ.setdefault('LIBRARIAN_ROOT', r'{HERE}'); "
        "from librarian_qt import launch_standalone; "
        f"launch_standalone(base=r'{HERE}')"
    )

    env = os.environ.copy()
    env.setdefault("LIBRARIAN_ROOT", str(HERE))

    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008  # DETACHED_PROCESS

    try:
        proc = subprocess.Popen(
            [str(py), "-u", "-c", code],
            cwd=str(HERE),
            env=env,
            stdout=log,
            stderr=log,
            creationflags=creationflags,
        )
        if verbose:
            print(f"[launcher] spawned with {py}")
        return proc   # <<< return the Popen handle
    except Exception as e:
        err = f"[launcher] failed to spawn: {e}"
        if verbose:
            print(err)
        raise RuntimeError(err)

if __name__ == "__main__":
    launch()
