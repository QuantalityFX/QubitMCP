"""Dependency-free paths, also usable by the clean setup Python."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def storage_root():
    override = os.environ.get("QUBITMCP_HOME", "").strip()
    candidate = Path(override).expanduser() if override else PROJECT_ROOT
    protected = any(candidate.resolve().is_relative_to(Path(root).resolve())
                    for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
                    if (root := os.environ.get(name)))
    if not protected and not (getattr(sys, "frozen", False) and not override):
        return candidate
    local = os.environ.get("LOCALAPPDATA", "")
    return Path(local) / "QubitMCP" if local else Path.home() / ".qubitmcp"


@dataclass(frozen=True)
class RuntimeConfig:
    repository: Path = field(default_factory=lambda: storage_root() / "third_party/Qwen-image")
    runtime: Path = field(default_factory=lambda: storage_root() / "third_party/ComfyUI-Qwen21")
    port: int = 8188

    @property
    def python(self):
        return self.runtime / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


def saved_config():
    path = storage_root() / "echograph/data/qwen_image/settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RuntimeConfig(repository=Path(data["repository"]), runtime=Path(data["runtime"]))
    except (OSError, ValueError, KeyError, TypeError):
        return RuntimeConfig()


def save_config(config):
    path = storage_root() / "echograph/data/qwen_image/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"repository": str(config.repository), "runtime": str(config.runtime)}), encoding="utf-8")
    temporary.replace(path)
