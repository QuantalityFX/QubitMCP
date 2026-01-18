# echograph/ui/hotkeys_config.py
from __future__ import annotations

import json
from pathlib import Path

from echograph.qt_compat import QtCore

# Action ids -> default key sequences
DEFAULT_KEYMAP = {
    "big_editor": "Ctrl+B",
    "app_save": "Ctrl+S",
    "gl_frame": "F",
    "gl_reset_view": "R",
}


def _log(*args):
    print("[HOTKEYS]", *args, flush=True)

def keymap_path() -> Path:
    # echograph/ui/hotkeys_config.py -> parents[2] should be your repo root (EchoMatrixMCP)
    repo_root = Path(__file__).resolve().parents[2]
    p = repo_root / "hotkeys.json"
    _log("PATH:", str(p))
    return p


def load_keymap() -> dict:
    p = keymap_path()

    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            _log("READ FAILED:", repr(e))
            data = {}

    merged = dict(DEFAULT_KEYMAP)
    merged.update({k: v for k, v in data.items() if isinstance(v, str)})

    # write back if file missing or missing keys
    if (not p.exists()) or (merged.keys() != data.keys()):
        try:
            p.write_text(json.dumps(merged, indent=2), encoding="utf-8")
            _log("WRITTEN:", str(p))
        except Exception as e:
            _log("WRITE FAILED:", repr(e))

    _log("LOADED:", str(p))
    return merged
