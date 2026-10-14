"""Optional app-local folder defaults; graph nodes retain their own explicit paths."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .backend import PROJECT_ROOT, PriorMDMConfig
from .paths import storage_root, migrate_managed_paths

SETTINGS_PATH = storage_root() / "echograph/data/priormdm/settings.json"
LOCATION_KEYS = ("repository", "download_cache", "dataset")


def default_paths() -> dict[str, str]:
    config = PriorMDMConfig()
    locations = {"repository": str(config.repository), "download_cache": str(config.cache_directory),
                 "dataset": str(config.dataset)}
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(saved, dict) and saved.get("schema_version") == 1:
            locations.update({key: saved[key] for key in LOCATION_KEYS
                              if isinstance(saved.get(key), str) and saved[key].strip()})
    except (OSError, ValueError):
        pass
    locations = {key: str(path) for key, path in migrate_managed_paths(locations).items()}
    repository = Path(locations["repository"])
    return {**locations, "python": str(repository / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
            "checkpoint": "", "output_root": str(config.output_root)}


def save_locations(config: PriorMDMConfig) -> None:
    """Atomic write to a dedicated feature settings file, without touching app_settings."""
    data = {"schema_version": 1, "repository": str(config.repository.resolve()),
            "download_cache": str(config.cache_directory.resolve()), "dataset": str(config.dataset.resolve())}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    partial = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=SETTINGS_PATH.parent,
                                         prefix=".priormdm-settings-", delete=False) as stream:
            partial = Path(stream.name)
            json.dump(data, stream, indent=2)
        partial.replace(SETTINGS_PATH)
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
