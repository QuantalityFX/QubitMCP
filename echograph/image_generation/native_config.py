"""Native runtime settings. No Qt, PyTorch, Diffusers, or ComfyUI dependencies."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path

from .config import storage_root
from .schema import RESOURCES

MODEL_ID = "Qwen/Qwen-Image-2.1"
MODEL_REVISION = "d26bb61231c349cf6b7896fa83353113880e1ba3"
MODEL_FOLDER = "Qwen-Image-2.1"


def model_directory_for_selection(folder):
    """Resolve a parent models folder, while accepting existing checkpoints as-is."""
    folder = Path(folder).expanduser().resolve()
    if folder.name.casefold() == MODEL_FOLDER.casefold():
        return folder
    # Older versions let users choose a checkpoint with any directory name.
    for filename, expected in (("model_index.json", {"_class_name": "QwenImage21Pipeline"}),
            (".qubit-native-model.json", {"owner": "qubit-qwen-model", "model_id": MODEL_ID})):
        try:
            data = json.loads((folder / filename).read_text(encoding="utf-8"))
            if isinstance(data, dict) and all(data.get(key) == value for key, value in expected.items()):
                return folder
        except (OSError, ValueError):
            pass
    return folder / MODEL_FOLDER


def native_versions():
    return json.loads((RESOURCES / "native-versions.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class NativeConfig:
    runtime: Path = field(default_factory=lambda: storage_root() / "third_party/Qwen-Native")
    model_directory: Path | None = None

    @property
    def python(self):
        return self.runtime / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    @property
    def model(self):
        return self.model_directory if self.model_directory is not None else self.runtime / "models" / MODEL_FOLDER

    @property
    def models_root(self):
        # Keep the effective checkpoint path in settings/IPC for compatibility.
        return self.model.parent if self.model.name.casefold() == MODEL_FOLDER.casefold() else self.model


def saved_native_config():
    try:
        data = json.loads((storage_root() / "echograph/data/qwen_image/native-settings.json").read_text(encoding="utf-8"))
        model = data.get("model_directory")
        return NativeConfig(Path(data["runtime"]), Path(model) if model else None)
    except (OSError, ValueError, KeyError, TypeError):
        return NativeConfig()


def save_native_config(config):
    path = storage_root() / "echograph/data/qwen_image/native-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"runtime": str(config.runtime),
        "model_directory": str(config.model_directory) if config.model_directory is not None else ""}), encoding="utf-8")
    temporary.replace(path)


def installed_manifest(config):
    try:
        manifest = json.loads((config.runtime / ".qubit-native-installed.json").read_text(encoding="utf-8"))
        if manifest["model_id"] != MODEL_ID or manifest["model_revision"] != MODEL_REVISION:
            raise ValueError("Installed model differs from the pinned native checkpoint.")
        if manifest["packages"] != native_versions()["packages"]:
            raise ValueError("Native dependencies changed; run Install / Verify in Setup.")
        recorded_model = Path(manifest.get("model_directory", config.runtime / "models/Qwen-Image-2.1"))
        if recorded_model.resolve() != config.model.resolve():
            raise ValueError("Model folder changed. Run Install / Verify in Setup before starting the native backend.")
        for entry in manifest["files"]:
            path = config.model / entry["path"]
            if not path.is_file() or path.stat().st_size != entry["bytes"]:
                raise ValueError(f"Missing or incomplete native model file: {entry['path']}. Run Setup.")
        if not (config.model / "model_index.json").is_file() or not config.python.is_file():
            raise ValueError("Native runtime is incomplete. Run Setup.")
        return manifest
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError("Install / Verify the native runtime in Setup first.") from exc
