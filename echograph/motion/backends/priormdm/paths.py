"""Writable shared storage for packaged installations and source checkouts."""
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def protected_install(path: Path) -> bool:
    return any(path.resolve().is_relative_to(Path(root).resolve())
               for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
               if (root := os.environ.get(key)))


def storage_root() -> Path:
    override = os.environ.get("QUBITMCP_HOME", "").strip()
    candidate = Path(override).expanduser() if override else PROJECT_ROOT
    if not protected_install(candidate) and not (getattr(sys, "frozen", False) and not override):
        return candidate
    local = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(local) / "QubitMCP" if local else Path.home() / ".qubitmcp"


def default_repository() -> Path:
    return storage_root() / "third_party" / "PriorMDM"


def migrate_managed_paths(paths: dict) -> dict:
    """Repair an uninstalled legacy default without changing custom locations."""
    legacy = PROJECT_ROOT / "third_party" / "PriorMDM"
    repository = Path(paths["repository"])
    target = default_repository()
    if (repository.resolve() != legacy.resolve() or target.resolve() == legacy.resolve()
            or (legacy / "utils/model_util.py").is_file()):
        return paths
    result = dict(paths)
    for key in ("repository", "python", "dataset", "download_cache", "checkpoint"):
        if paths.get(key):
            path = Path(paths[key]).resolve()
            if path.is_relative_to(legacy.resolve()):
                result[key] = target / path.relative_to(legacy.resolve())
    return result
