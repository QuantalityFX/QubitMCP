# echograph/ui/hotkeys_config.py
from __future__ import annotations

import json
from pathlib import Path

from echograph.qt_compat import QtCore
_KEYMAP_CACHE = None

# Action ids -> default key sequences
DEFAULT_KEYMAP = {
    "big_editor": "Ctrl+B",
    "app_save": "Ctrl+S",
    "node_delete": "Del",
    "comment_group": "C",
    "node_copy": "Ctrl+C",
    "node_paste": "Ctrl+V",
    "node_menu": "Tab",
    "node_view": "V",
    "view_mode_2d": "1",
    "view_mode_split": "2",
    "view_mode_3d": "3",
    "gl_frame": "F",
    "gl_reset_view": "Shift+R",
    "gizmo_translate": "T",
    "gizmo_rotate": "R",
    "gizmo_scale": "E",
    "app_undo": "Ctrl+Z",
    "app_redo": "Ctrl+Y",
    "gl_wireframe_toggle": "H",
    "gl_grid_toggle": "G",
    "timeline_play_toggle": "Space",
    "timeline_set_key": "K",
    "app_fullscreen": "F11",
    "wire_add_pin": "Ctrl+LeftClick",
    "wire_remove": "Alt+LeftClick",
}

_MOD_MASK = (
    QtCore.Qt.ControlModifier
    | QtCore.Qt.AltModifier
    | QtCore.Qt.ShiftModifier
    | QtCore.Qt.MetaModifier
)

def parse_mouse_binding(spec: str) -> tuple[QtCore.Qt.KeyboardModifiers, QtCore.Qt.MouseButton]:
    """
    Parse a mouse binding string like "Ctrl+LeftClick" into modifiers + button.
    Only modifiers are supported (Ctrl/Alt/Shift/Meta).
    """
    s = str(spec or "").lower().replace(" ", "")
    mods = QtCore.Qt.KeyboardModifiers()
    if "ctrl" in s or "control" in s:
        mods |= QtCore.Qt.ControlModifier
    if "alt" in s:
        mods |= QtCore.Qt.AltModifier
    if "shift" in s:
        mods |= QtCore.Qt.ShiftModifier
    if "meta" in s or "cmd" in s or "command" in s:
        mods |= QtCore.Qt.MetaModifier

    btn = QtCore.Qt.NoButton
    if ("left" in s) or ("lmb" in s):
        btn = QtCore.Qt.LeftButton
    elif ("right" in s) or ("rmb" in s):
        btn = QtCore.Qt.RightButton
    elif ("middle" in s) or ("mmb" in s):
        btn = QtCore.Qt.MiddleButton
    return mods, btn

def mouse_binding(action_id: str, fallback: str) -> tuple[QtCore.Qt.KeyboardModifiers, QtCore.Qt.MouseButton]:
    spec = keyseq(action_id, fallback)
    return parse_mouse_binding(spec)

def normalize_mods(mods: QtCore.Qt.KeyboardModifiers) -> QtCore.Qt.KeyboardModifiers:
    return QtCore.Qt.KeyboardModifiers(int(mods) & int(_MOD_MASK))

def _log(*args):
    print("[HOTKEYS]", *args, flush=True)

def keymap_path() -> Path:
    # echograph/ui/hotkeys_config.py -> parents[2] should be your repo root
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

def keyseq(action_id: str, fallback_seq: str) -> str:
    """
    Return configured key sequence for action_id (from hotkeys.json),
    falling back to fallback_seq if not present.
    Caches the loaded keymap.
    """
    global _KEYMAP_CACHE
    if _KEYMAP_CACHE is None:
        _KEYMAP_CACHE = load_keymap()
    try:
        v = _KEYMAP_CACHE.get(action_id, fallback_seq)
        return str(v or fallback_seq)
    except Exception:
        return str(fallback_seq)
