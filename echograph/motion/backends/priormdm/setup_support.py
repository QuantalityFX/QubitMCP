"""Setup discovery and reports shared by the installer and node UI."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

from .backend import PriorMDMBackend, PriorMDMConfig, checkpoint_settings


def find_checkpoint(repository: Path) -> Path | None:
    candidates = []
    for args_file in (repository / "save").glob("**/args.json"):
        for model in args_file.parent.glob("model*.pt"):
            try:
                settings = checkpoint_settings(model)
            except (OSError, ValueError):
                continue
            candidates.append((settings["diffusion_steps"], str(model), model))
    return min(candidates)[2] if candidates else None


def inspect_setup(config: PriorMDMConfig, *, check_runtime: bool = False) -> dict:
    missing = PriorMDMBackend(config).check()
    runtime = {}
    runtime_error = ""
    dependencies_ready = None
    if check_runtime:
        dependencies_ready = False
        if not config.python.is_file():
            runtime_error = "PriorMDM's Python environment has not been installed."
        elif not (config.repository / "utils/model_util.py").is_file():
            runtime_error = "The PriorMDM source folder is missing."
        else:
            try:
                result = subprocess.run(
                    [str(config.python), "-u", str(Path(__file__).with_name("worker.py")),
                     "--probe", str(config.repository)], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=90,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode == 0:
                    runtime = json.loads(result.stdout.strip().splitlines()[-1])
                    dependencies_ready = True
                else:
                    runtime_error = (result.stderr or result.stdout).strip()[-4000:]
            except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
                runtime_error = str(exc)
        if not dependencies_ready:
            missing.append("Python dependencies need setup: " + (runtime_error.splitlines()[-1] if runtime_error else "runtime check failed"))
    return {"schema_version": 1, "ready": not missing and dependencies_ready is True,
            "ready_files": not PriorMDMBackend(config).check(),
            "dependencies_ready": dependencies_ready, "runtime": runtime,
            "runtime_error": runtime_error, "missing": missing,
            "repository": str(config.repository), "python": str(config.python),
            "checkpoint": str(config.checkpoint) if config.checkpoint else "", "dataset": str(config.dataset),
            "download_cache": str(config.cache_directory)}
