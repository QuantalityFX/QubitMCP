# echograph/ui/hotkeys_config.py
from __future__ import annotations

import json
from pathlib import Path

from echograph.qt_compat import QtCore, QtGui
_KEYMAP_CACHE = None

CAMERA_VIEW_ACTIONS = (
    "gl_view_perspective", "gl_view_top_bottom", "gl_view_front_back", "gl_view_left_right",
)
MOUSE_ACTIONS = {"wire_add_pin", "wire_remove", "timeline_graph_pan", "timeline_graph_scale"}

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
    "node_frame_selected": "Shift+F",
    "shelf_snippet_maximize": "Shift+F",
    "view_mode_2d": "1",
    "view_mode_split": "2",
    "view_mode_3d": "3",
    "gl_frame": "F",
    "gl_reset_view": "Shift+R",
    "gl_view_perspective": "Space+1",
    "gl_view_top_bottom": "Space+2",
    "gl_view_front_back": "Space+3",
    "gl_view_left_right": "Space+4",
    "gizmo_translate": "T",
    "gizmo_rotate": "R",
    "gizmo_scale": "E",
    "app_undo": "Ctrl+Z",
    "app_redo": "Ctrl+Y",
    "gl_wireframe_toggle": "H",
    "gl_xray_toggle": "Shift+H",
    "gl_mesh_select_path_loop": "Shift+A",
    "gl_mesh_deselect_path_loop": "Ctrl+A",
    "gl_grid_toggle": "G",
    "timeline_play_toggle": "Space+P",
    "timeline_set_key": "K",
    "timeline_graph_pan": "Alt+MiddleClick",
    "timeline_graph_scale": "RightClick",
    "timeline_graph_frame_selected": "F",
    "timeline_join_ik_ranges": "Ctrl+J",
    "app_fullscreen": "F11",
    "wire_add_pin": "Ctrl+LeftClick",
    "wire_remove": "Alt+LeftClick",
}

ACTION_LABELS = {
    "shelf_snippet_maximize": "Shelf popup: maximize / restore",
    "timeline_play_toggle": "Timeline: play / pause",
    "timeline_graph_pan": "Timeline graph: pan",
    "timeline_graph_scale": "Timeline graph: scale ranges",
    "timeline_graph_frame_selected": "Timeline graph: frame keys",
    "timeline_join_ik_ranges": "Timeline graph: join IK ranges",
    "gl_view_perspective": "3D: Perspective view",
    "gl_view_top_bottom": "3D: Top / Bottom views",
    "gl_view_front_back": "3D: Front / Back views",
    "gl_view_left_right": "3D: Left / Right views",
    "gl_xray_toggle": "3D: toggle X-ray wireframe",
    "gl_mesh_select_path_loop": "Mesh: add path / loop (hold)",
    "gl_mesh_deselect_path_loop": "Mesh: deselect path / loop (hold)",
}

ACTION_DESCRIPTIONS = {
    "shelf_snippet_maximize": "Toggle the shelf snippet window between maximized and its previous size. Default: Shift + F.",
    "timeline_play_toggle": "Play or pause with the pointer over the timeline panel. Default: hold Space and press P. Video review uses its own Space shortcut.",
    "timeline_graph_pan": "Drag to grab the curve graph and pan in time and value without changing its scale or moving the 3D camera. Default: Alt + middle mouse.",
    "timeline_graph_scale": "Drag down/up to expand/contract the value range, or right/left to contract/expand the time range. Default: right mouse. Alt may also be held while dragging.",
    "timeline_graph_frame_selected": "Fit selected curve points in time and value, or all keys on visible curves when no points are selected. Includes keys outside the current view. Applies inside the timeline graph; the 3D viewport keeps its own frame shortcut. Default: F.",
    "timeline_join_ik_ranges": "Extend IK overwrite between the selected End IK and next Start IK on the same control. Both keys keep their timing and poses as intermediate IK keys in the continuous range. Default: Ctrl + J.",
    "gl_view_perspective": "Restore the previous perspective camera view. Hold Space while pressing the key when Hold Space is checked.",
    "gl_view_top_bottom": "Alternate between orthographic Top and Bottom views.",
    "gl_view_front_back": "Alternate between orthographic Front and Back views.",
    "gl_view_left_right": "Alternate between orthographic Left and Right views.",
    "gl_xray_toggle": "Toggle ghosted X-ray wireframe and rear-point picking in the 3D viewport.",
    "gl_mesh_select_path_loop": "Hold this shortcut: left-click adds a path; middle-click adds the full loop. Works with points, edges and polygons; previews are blue.",
    "gl_mesh_deselect_path_loop": "Hold this shortcut: left-click deselects a path; middle-click deselects the full loop. Works with points, edges and polygons; previews are red.",
}

_MOD_MASK = (
    QtCore.Qt.ControlModifier
    | QtCore.Qt.AltModifier
    | QtCore.Qt.ShiftModifier
    | QtCore.Qt.MetaModifier
)


def split_space_binding(spec: str) -> tuple[bool, str]:
    """Separate our held-Space chord prefix from a Qt key combination."""
    text = str(spec or "").strip()
    prefix, separator, remainder = text.partition("+")
    held = bool(separator) and prefix.strip().lower() == "space"
    return held, remainder.strip() if held else text


def chord_binding(action_id: str):
    global _KEYMAP_CACHE
    if _KEYMAP_CACHE is None:
        _KEYMAP_CACHE = load_keymap()
    # Unlike legacy keyseq(), an empty binding explicitly disables this action.
    spec = _KEYMAP_CACHE.get(action_id, DEFAULT_KEYMAP[action_id])
    held, combination = split_space_binding(spec)
    key, mods = parse_key_binding(combination)
    return held, key, mods


def parse_key_binding(spec: str):
    """Return a single key and modifiers on either PySide6 or PySide2."""
    sequence = QtGui.QKeySequence(str(spec or ""))
    if sequence.count() != 1:
        return None, QtCore.Qt.NoModifier
    combination = sequence[0]
    value = combination.toCombined() if hasattr(combination, "toCombined") else int(combination)
    mask = _MOD_MASK.value if hasattr(_MOD_MASK, "value") else int(_MOD_MASK)
    return value & ~mask, QtCore.Qt.KeyboardModifiers(value & mask)

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
    # Migrate the previous global playback default to the panel-only chord.
    if merged.get("timeline_play_toggle") == "Space":
        merged["timeline_play_toggle"] = "Space+P"

    # write back if file missing or missing keys
    if (not p.exists()) or merged != data:
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
