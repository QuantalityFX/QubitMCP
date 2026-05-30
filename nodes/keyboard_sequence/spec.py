from __future__ import annotations

import ctypes
import json
import os
import re
import threading
import time
from pathlib import Path

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant


_SEQUENCE_PARAM = "keyboard_sequence_data"
_LEAD_IN_PARAM = "keyboard_lead_in_ms"
_KEY_HOLD_PARAM = "keyboard_key_hold_ms"
_INJECTION_MODE_PARAM = "keyboard_injection_mode"
_HIDDEN_PARAM = "__ui_hidden_params"

_DEFAULT_DELAY_MS = 250
_DEFAULT_LEAD_IN_MS = 1200
_DEFAULT_KEY_HOLD_MS = 40
_DEFAULT_CLICK_MS = 40
_MIN_DELAY_MS = 0
_MAX_DELAY_MS = 600000
_MAX_LEAD_IN_MS = 60000
_MAX_KEY_HOLD_MS = 2000
_MAX_ACTION_HOLD_MS = 600000
_MIN_SCREEN_COORD = -200000
_MAX_SCREEN_COORD = 200000
_MIN_STEPS = 1
_MAX_STEPS = 64
_PRESET_VERSION = 1

_ACTION_TYPE_KEY = "key"
_ACTION_TYPE_CLICK = "click"
_ACTION_TYPE_TEXT = "text"
_ACTION_TYPE_HOVER = "hover"
_ACTION_TYPE_LOOP_START = "loop_start"
_ACTION_TYPE_LOOP_END = "loop_end"
_MOUSE_BUTTON_LEFT = "left"
_MOUSE_BUTTON_RIGHT = "right"
_MOUSE_BUTTON_MIDDLE = "middle"
_CLICK_COORD_SCREEN = "screen"
_CLICK_COORD_WINDOW = "window"
_TEXT_SOURCE_SINGLE = "single"
_TEXT_SOURCE_LOOP_TABLE = "loop_table"
_LOOP_CONDITION_EQ = "=="
_LOOP_CONDITION_GT = ">"
_LOOP_CONDITION_LT = "<"
_LOOP_CONDITION_GTE = ">="
_LOOP_CONDITION_LTE = "<="

KEYBOARD_SEQUENCE_BODY_W = 980
KEYBOARD_SEQUENCE_BODY_H = 318

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_SCANCODE = 0x0008

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_MOUSEEVENTF_ABSOLUTE = 0x8000

_SM_CXSCREEN = 0
_SM_CYSCREEN = 1
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

_GA_ROOT = 2
_MONITOR_DEFAULTTONEAREST = 2
_CCHDEVICENAME = 32

_MAPVK_VK_TO_VSC = 0
_MAPVK_VK_TO_VSC_EX = 4

_MODE_VK = "vk"
_MODE_SCANCODE = "scancode"
_MODE_HYBRID = "hybrid"

_INTER_EVENT_MS = 2
_MIN_TEXT_KEY_HOLD_MS = 10
_TEXT_INTER_CHAR_MS = 25
_TEXT_POST_WRITE_BASE_MS = 120
_TEXT_POST_WRITE_PER_CHAR_MS = 8
_TEXT_POST_WRITE_MAX_MS = 3000
_MIN_LOOP_COUNT = 1
_MAX_LOOP_COUNT = 100000
_MIN_LOOP_NUMBER = 1
_MAX_LOOP_NUMBER = 9999

_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_CHAR = 0x0102
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105

_USER32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None


if os.name == "nt":
    _ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", ctypes.c_ulong),
            ("wParamL", ctypes.c_ushort),
            ("wParamH", ctypes.c_ushort),
        ]

    class _POINT(ctypes.Structure):
        _fields_ = [
            ("x", ctypes.c_long),
            ("y", ctypes.c_long),
        ]

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class _MONITORINFOEX(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("rcMonitor", _RECT),
            ("rcWork", _RECT),
            ("dwFlags", ctypes.c_ulong),
            ("szDevice", ctypes.c_wchar * _CCHDEVICENAME),
        ]

    _WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    _MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_RECT),
        ctypes.c_ssize_t,
    )

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", _MOUSEINPUT),
            ("ki", _KEYBDINPUT),
            ("hi", _HARDWAREINPUT),
        ]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_ulong), ("u", _INPUT_UNION)]

    try:
        _USER32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int)
        _USER32.SendInput.restype = ctypes.c_uint
        _USER32.VkKeyScanW.argtypes = (ctypes.c_wchar,)
        _USER32.VkKeyScanW.restype = ctypes.c_short
        _USER32.MapVirtualKeyW.argtypes = (ctypes.c_uint, ctypes.c_uint)
        _USER32.MapVirtualKeyW.restype = ctypes.c_uint
        _USER32.GetForegroundWindow.argtypes = ()
        _USER32.GetForegroundWindow.restype = ctypes.c_void_p
        _USER32.PostMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
        _USER32.PostMessageW.restype = ctypes.c_int
        _USER32.GetSystemMetrics.argtypes = (ctypes.c_int,)
        _USER32.GetSystemMetrics.restype = ctypes.c_int
        _USER32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        _USER32.SetCursorPos.restype = ctypes.c_int
        _USER32.GetCursorPos.argtypes = (ctypes.POINTER(_POINT),)
        _USER32.GetCursorPos.restype = ctypes.c_int
        _USER32.ClipCursor.argtypes = (ctypes.c_void_p,)
        _USER32.ClipCursor.restype = ctypes.c_int
        _USER32.ReleaseCapture.argtypes = ()
        _USER32.ReleaseCapture.restype = ctypes.c_int
        _USER32.WindowFromPoint.argtypes = (_POINT,)
        _USER32.WindowFromPoint.restype = ctypes.c_void_p
        _USER32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        _USER32.GetAncestor.restype = ctypes.c_void_p
        _USER32.IsWindow.argtypes = (ctypes.c_void_p,)
        _USER32.IsWindow.restype = ctypes.c_int
        _USER32.IsWindowVisible.argtypes = (ctypes.c_void_p,)
        _USER32.IsWindowVisible.restype = ctypes.c_int
        _USER32.GetWindowTextW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
        _USER32.GetWindowTextW.restype = ctypes.c_int
        _USER32.GetClassNameW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
        _USER32.GetClassNameW.restype = ctypes.c_int
        _USER32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        _USER32.GetWindowThreadProcessId.restype = ctypes.c_ulong
        _USER32.ScreenToClient.argtypes = (ctypes.c_void_p, ctypes.POINTER(_POINT))
        _USER32.ScreenToClient.restype = ctypes.c_int
        _USER32.ClientToScreen.argtypes = (ctypes.c_void_p, ctypes.POINTER(_POINT))
        _USER32.ClientToScreen.restype = ctypes.c_int
        _USER32.GetClientRect.argtypes = (ctypes.c_void_p, ctypes.POINTER(_RECT))
        _USER32.GetClientRect.restype = ctypes.c_int
        _USER32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
        _USER32.SetForegroundWindow.restype = ctypes.c_int
        _USER32.EnumWindows.argtypes = (_WNDENUMPROC, ctypes.c_void_p)
        _USER32.EnumWindows.restype = ctypes.c_int
        _USER32.MonitorFromPoint.argtypes = (_POINT, ctypes.c_ulong)
        _USER32.MonitorFromPoint.restype = ctypes.c_void_p
        _USER32.GetMonitorInfoW.argtypes = (ctypes.c_void_p, ctypes.POINTER(_MONITORINFOEX))
        _USER32.GetMonitorInfoW.restype = ctypes.c_int
        _USER32.EnumDisplayMonitors.argtypes = (ctypes.c_void_p, ctypes.c_void_p, _MONITORENUMPROC, ctypes.c_ssize_t)
        _USER32.EnumDisplayMonitors.restype = ctypes.c_int
    except Exception:
        pass


_MODIFIER_KEYS = {
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "win": 0x5B,
    "windows": 0x5B,
    "meta": 0x5B,
}

_SPECIAL_KEYS = {
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "spacebar": 0x20,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "pgup": 0x21,
    "pgdn": 0x22,
    "backspace": 0x08,
    "bs": 0x08,
    "capslock": 0x14,
    "printscreen": 0x2C,
    "scrolllock": 0x91,
    "pause": 0x13,
    "numlock": 0x90,
    "plus": 0xBB,
    "-": 0xBD,
    "minus": 0xBD,
    "comma": 0xBC,
    "period": 0xBE,
    "dot": 0xBE,
    "/": 0xBF,
    "slash": 0xBF,
    "backslash": 0xDC,
    "divide": 0x6F,
    "numpaddivide": 0x6F,
    "keypaddivide": 0x6F,
    "semicolon": 0xBA,
    "quote": 0xDE,
    "apostrophe": 0xDE,
    "lbracket": 0xDB,
    "rbracket": 0xDD,
    "grave": 0xC0,
    "tilde": 0xC0,
}
for _idx in range(1, 25):
    _SPECIAL_KEYS[f"f{_idx}"] = 0x6F + _idx

_SCANCODE_EXTENDED_KEYS = {
    0x21,  # Page Up
    0x22,  # Page Down
    0x23,  # End
    0x24,  # Home
    0x25,  # Left
    0x26,  # Up
    0x27,  # Right
    0x28,  # Down
    0x2D,  # Insert
    0x2E,  # Delete
    0x5B,  # LWin
    0x5C,  # RWin
    0x5D,  # Apps
}


def _normalize_token(token: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(token or "").strip().lower())


def _dedupe_keep_order(values: list[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value in seen:
            continue
        out.append(int(value))
        seen.add(int(value))
    return out


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value", "") or "")
    return ""


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            model.params = params
            return
    params.append({"name": name, "value": default})
    model.params = params


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    hidden_entry = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == _HIDDEN_PARAM:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": _HIDDEN_PARAM, "value": ""}
        params.append(hidden_entry)
    hidden = {part.strip().lower() for part in str(hidden_entry.get("value", "")).split(",") if part.strip()}
    for name in names or []:
        token = str(name or "").strip().lower()
        if token:
            hidden.add(token)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_param_value(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    found = False
    changed = False
    text = str(value or "")
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            found = True
            if str(entry.get("value", "") or "") != text:
                entry["value"] = text
                changed = True
            break
    if not found:
        params.append({"name": name, "value": text})
        changed = True
    if not changed:
        return
    model.params = params
    if not notify_scene:
        return
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return
    if hasattr(scene, "set_node_params"):
        try:
            scene.set_node_params(model.name, params, rebuild=False, emit=True)
            return
        except Exception:
            pass
    if hasattr(scene, "paramChanged"):
        try:
            scene.paramChanged.emit(model.name, list(params))
        except Exception:
            pass


def _notify_node_params_changed(node_item) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return
    if hasattr(scene, "set_node_params"):
        try:
            scene.set_node_params(model.name, params, rebuild=False, emit=True)
            return
        except Exception:
            pass
    if hasattr(scene, "paramChanged"):
        try:
            scene.paramChanged.emit(model.name, params)
        except Exception:
            pass


def _coerce_int(value, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _coerce_delay_ms(value) -> int:
    return max(_MIN_DELAY_MS, min(_MAX_DELAY_MS, _coerce_int(value, _DEFAULT_DELAY_MS)))


def _coerce_lead_in_ms(value) -> int:
    return max(0, min(_MAX_LEAD_IN_MS, _coerce_int(value, _DEFAULT_LEAD_IN_MS)))


def _coerce_key_hold_ms(value) -> int:
    return max(0, min(_MAX_KEY_HOLD_MS, _coerce_int(value, _DEFAULT_KEY_HOLD_MS)))


def _coerce_action_hold_ms(value) -> int:
    return max(0, min(_MAX_ACTION_HOLD_MS, _coerce_int(value, _DEFAULT_KEY_HOLD_MS)))


def _coerce_click_ms(value) -> int:
    return max(0, min(_MAX_ACTION_HOLD_MS, _coerce_int(value, _DEFAULT_CLICK_MS)))


def _coerce_screen_coord(value, default: int = 0) -> int:
    return max(_MIN_SCREEN_COORD, min(_MAX_SCREEN_COORD, _coerce_int(value, default)))


def _coerce_loop_number(value, default: int = 1) -> int:
    return max(_MIN_LOOP_NUMBER, min(_MAX_LOOP_NUMBER, _coerce_int(value, default)))


def _coerce_loop_count(value, default: int = 2) -> int:
    return max(_MIN_LOOP_COUNT, min(_MAX_LOOP_COUNT, _coerce_int(value, default)))


def _normalize_action_type(raw) -> str:
    token = _normalize_token(str(raw or ""))
    if token in {"click", "mouseclick", "mouse"}:
        return _ACTION_TYPE_CLICK
    if token in {"text", "write", "writetext", "type", "typetext", "string"}:
        return _ACTION_TYPE_TEXT
    if token in {"hover", "move", "mousemove", "movecursor", "cursor"}:
        return _ACTION_TYPE_HOVER
    if token in {"loopstart", "startloop", "loopbegin", "beginloop", "loopopen"}:
        return _ACTION_TYPE_LOOP_START
    if token in {"loopend", "endloop", "loopfinish", "finishloop", "loopclose"}:
        return _ACTION_TYPE_LOOP_END
    return _ACTION_TYPE_KEY


def _normalize_mouse_button(raw) -> str:
    token = _normalize_token(str(raw or ""))
    if token in {"right", "r", "secondary"}:
        return _MOUSE_BUTTON_RIGHT
    if token in {"middle", "mid", "m", "wheel"}:
        return _MOUSE_BUTTON_MIDDLE
    return _MOUSE_BUTTON_LEFT


def _normalize_click_coord_mode(raw) -> str:
    token = _normalize_token(str(raw or ""))
    if token in {"window", "client", "app", "activewindow", "active"}:
        return _CLICK_COORD_WINDOW
    return _CLICK_COORD_SCREEN


def _action_uses_pointer_target(action_type: str) -> bool:
    return _normalize_action_type(action_type) in {_ACTION_TYPE_CLICK, _ACTION_TYPE_HOVER}


def _action_uses_loop_settings(action_type: str) -> bool:
    return _normalize_action_type(action_type) in {_ACTION_TYPE_LOOP_START, _ACTION_TYPE_LOOP_END}


def _raw_has_value(raw: dict, *names: str) -> bool:
    if not isinstance(raw, dict):
        return False
    for name in names:
        if name not in raw:
            continue
        value = raw.get(name)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return True
    return False


def _raw_first_value(raw: dict, *names: str, default=None):
    if not isinstance(raw, dict):
        return default
    for name in names:
        if name not in raw:
            continue
        value = raw.get(name)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default


def _raw_text_value(raw: dict, *names: str, default: str = "") -> str:
    if not isinstance(raw, dict):
        return str(default or "")
    for name in names:
        if name in raw and raw.get(name) is not None:
            return str(raw.get(name))
    return str(default or "")


def _text_preview(text: str, *, limit: int = 42) -> str:
    clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    clean = clean.replace("\n", "\\n").replace("\t", "\\t")
    if len(clean) > int(limit):
        return clean[: max(1, int(limit) - 3)] + "..."
    return clean


def _text_key_hold_ms(value) -> int:
    return max(_MIN_TEXT_KEY_HOLD_MS, _coerce_action_hold_ms(value))


def _text_inter_char_seconds() -> float:
    return max(0.0, float(_TEXT_INTER_CHAR_MS) / 1000.0)


def _text_post_write_delay_ms(text: str) -> int:
    char_count = sum(1 for ch in str(text or "") if ch != "\r")
    if char_count <= 0:
        return 0
    delay = _TEXT_POST_WRITE_BASE_MS + (char_count * _TEXT_POST_WRITE_PER_CHAR_MS)
    return max(0, min(_TEXT_POST_WRITE_MAX_MS, int(delay)))


def _normalize_text_source(raw) -> str:
    token = _normalize_token(str(raw or ""))
    if token in {"looptable", "table", "list", "looplist", "looptext", "texttable", "rows"}:
        return _TEXT_SOURCE_LOOP_TABLE
    return _TEXT_SOURCE_SINGLE


def _normalize_loop_text_rows(raw) -> list[dict[str, object]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw_rows = raw.values()
    elif isinstance(raw, (list, tuple)):
        raw_rows = raw
    else:
        return []
    rows: list[dict[str, object]] = []
    for idx, item in enumerate(raw_rows):
        if isinstance(item, dict):
            loop_number = _coerce_loop_count(
                item.get("loop_number", item.get("loop", item.get("iteration", item.get("index", idx + 1)))),
                idx + 1,
            )
            text_value = _raw_text_value(item, "text", "write_text", "content", default="")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            loop_number = _coerce_loop_count(item[0], idx + 1)
            text_value = str(item[1] if item[1] is not None else "")
        else:
            continue
        if text_value == "":
            continue
        rows.append({"loop_number": loop_number, "text": text_value})
    rows.sort(key=lambda row: int(row.get("loop_number", 1)))
    return rows


def _text_action_source(step: dict[str, object]) -> str:
    source = _normalize_text_source(step.get("text_source", step.get("text_mode", "")))
    if source == _TEXT_SOURCE_LOOP_TABLE:
        return source
    if _normalize_loop_text_rows(step.get("loop_text_rows", step.get("text_rows", None))):
        return _TEXT_SOURCE_LOOP_TABLE
    return _TEXT_SOURCE_SINGLE


def _active_loop_context(
    action_index: int,
    steps: list[dict[str, object]],
    start_to_end: dict[int, int],
    loop_iteration: dict[int, int],
) -> tuple[int, int, int] | None:
    candidates: list[tuple[int, int, int]] = []
    for start_idx, end_idx in start_to_end.items():
        if int(start_idx) < int(action_index) < int(end_idx):
            step = steps[int(start_idx)] if 0 <= int(start_idx) < len(steps) else {}
            loop_number = _coerce_loop_number(step.get("loop_number", 1), 1) if isinstance(step, dict) else 1
            iteration = _coerce_loop_count(loop_iteration.get(int(start_idx), 1), 1)
            candidates.append((int(start_idx), loop_number, iteration))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])


def _resolve_text_action_value(
    step: dict[str, object],
    action_index: int,
    steps: list[dict[str, object]],
    start_to_end: dict[int, int],
    loop_iteration: dict[int, int],
) -> tuple[bool, str, str]:
    if _text_action_source(step) != _TEXT_SOURCE_LOOP_TABLE:
        return True, _raw_text_value(step, "text", "write_text", "type_text", "content"), ""
    rows = _normalize_loop_text_rows(step.get("loop_text_rows", step.get("text_rows", None)))
    if not rows:
        return False, "", "Loop text list has no rows."
    context = _active_loop_context(action_index, steps, start_to_end, loop_iteration)
    if context is None:
        return False, "", "Loop text list requires this text action to run inside a loop."
    _start_idx, loop_number, iteration = context
    for row in rows:
        if int(row.get("loop_number", 1)) == int(iteration):
            return True, str(row.get("text", "") or ""), f"loop {loop_number}, row {iteration}"
    return False, "", f"No text row for loop {loop_number} iteration {iteration}."


def _key_loop_condition_iteration(step: dict[str, object]) -> int | None:
    if not isinstance(step, dict):
        return None
    enabled = bool(step.get("loop_condition_enabled", step.get("condition_enabled", False)))
    raw_value = _raw_first_value(
        step,
        "loop_condition_iteration",
        "condition_loop_iteration",
        "condition_loop_number",
        default=None,
    )
    if raw_value is None and not enabled:
        return None
    return _coerce_loop_count(raw_value if raw_value is not None else 1, 1)


def _normalize_loop_condition_operator(raw) -> str:
    text = str(raw or "").strip()
    token = _normalize_token(text)
    if text in {">=", "=>"} or token in {"gte", "ge", "greaterthanorequal", "greaterorequal"}:
        return _LOOP_CONDITION_GTE
    if text == "<=" or token in {"lte", "le", "lessthanorequal", "lessorequal"}:
        return _LOOP_CONDITION_LTE
    if text == ">" or token in {"gt", "greater", "greaterthan"}:
        return _LOOP_CONDITION_GT
    if text == "<" or token in {"lt", "less", "lessthan"}:
        return _LOOP_CONDITION_LT
    return _LOOP_CONDITION_EQ


def _loop_condition_operator_display(operator: str) -> str:
    clean = _normalize_loop_condition_operator(operator)
    if clean == _LOOP_CONDITION_GTE:
        return "=>"
    return clean


def _key_loop_condition_operator(step: dict[str, object]) -> str:
    if not isinstance(step, dict):
        return _LOOP_CONDITION_EQ
    return _normalize_loop_condition_operator(step.get("loop_condition_operator", step.get("condition_operator", _LOOP_CONDITION_EQ)))


def _loop_condition_matches(current_iteration: int, operator: str, required_iteration: int) -> bool:
    current = int(current_iteration)
    required = int(required_iteration)
    clean = _normalize_loop_condition_operator(operator)
    if clean == _LOOP_CONDITION_GT:
        return current > required
    if clean == _LOOP_CONDITION_LT:
        return current < required
    if clean == _LOOP_CONDITION_GTE:
        return current >= required
    if clean == _LOOP_CONDITION_LTE:
        return current <= required
    return current == required


def _key_loop_condition_label(step: dict[str, object]) -> str:
    iteration = _key_loop_condition_iteration(step)
    if iteration is None:
        return ""
    return f"Loop pass {_loop_condition_operator_display(_key_loop_condition_operator(step))} {iteration}"


def _key_loop_condition_requirement_label(step: dict[str, object]) -> str:
    iteration = _key_loop_condition_iteration(step)
    if iteration is None:
        return ""
    return f"loop pass {_loop_condition_operator_display(_key_loop_condition_operator(step))} {iteration}"


def _key_loop_condition_allows(
    step: dict[str, object],
    action_index: int,
    steps: list[dict[str, object]],
    start_to_end: dict[int, int],
    loop_iteration: dict[int, int],
) -> tuple[bool, str]:
    required_iteration = _key_loop_condition_iteration(step)
    if required_iteration is None:
        return True, ""
    requirement = _key_loop_condition_requirement_label(step)
    context = _active_loop_context(action_index, steps, start_to_end, loop_iteration)
    if context is None:
        return False, f"requires {requirement}; no active loop"
    _start_idx, loop_number, current_iteration = context
    if _loop_condition_matches(current_iteration, _key_loop_condition_operator(step), required_iteration):
        return True, f"loop {loop_number}, pass {current_iteration}"
    return False, f"requires {requirement}; current loop {loop_number} pass {current_iteration}"


def _normalize_screen_metadata(raw) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, object] = {}
    name = str(raw.get("name", "") or "").strip()
    if name:
        out["name"] = name
    if _raw_has_value(raw, "left"):
        out["left"] = _coerce_screen_coord(raw.get("left"))
    if _raw_has_value(raw, "top"):
        out["top"] = _coerce_screen_coord(raw.get("top"))
    if _raw_has_value(raw, "width"):
        out["width"] = max(1, _coerce_int(raw.get("width"), 1))
    if _raw_has_value(raw, "height"):
        out["height"] = max(1, _coerce_int(raw.get("height"), 1))
    if _raw_has_value(raw, "native_left"):
        out["native_left"] = _coerce_screen_coord(raw.get("native_left"))
    if _raw_has_value(raw, "native_top"):
        out["native_top"] = _coerce_screen_coord(raw.get("native_top"))
    if _raw_has_value(raw, "native_width"):
        out["native_width"] = max(1, _coerce_int(raw.get("native_width"), 1))
    if _raw_has_value(raw, "native_height"):
        out["native_height"] = max(1, _coerce_int(raw.get("native_height"), 1))
    if _raw_has_value(raw, "device_pixel_ratio"):
        try:
            out["device_pixel_ratio"] = max(0.1, min(8.0, float(raw.get("device_pixel_ratio"))))
        except Exception:
            pass
    return out or None


def _normalize_window_metadata(raw) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, object] = {}
    if _raw_has_value(raw, "hwnd"):
        hwnd = _coerce_int(raw.get("hwnd"), 0)
        if hwnd > 0:
            out["hwnd"] = hwnd
    title = str(raw.get("title", "") or "").strip()
    if title:
        out["title"] = title
    class_name = str(raw.get("class", raw.get("class_name", "")) or "").strip()
    if class_name:
        out["class"] = class_name
    if _raw_has_value(raw, "pid"):
        pid = _coerce_int(raw.get("pid"), 0)
        if pid > 0:
            out["pid"] = pid
    if _raw_has_value(raw, "client_x"):
        out["client_x"] = _coerce_screen_coord(raw.get("client_x"))
    if _raw_has_value(raw, "client_y"):
        out["client_y"] = _coerce_screen_coord(raw.get("client_y"))
    if _raw_has_value(raw, "client_width"):
        out["client_width"] = max(1, _coerce_int(raw.get("client_width"), 1))
    if _raw_has_value(raw, "client_height"):
        out["client_height"] = max(1, _coerce_int(raw.get("client_height"), 1))
    if "client_x" not in out or "client_y" not in out:
        return None
    return out or None


def _window_metadata_label(meta: dict[str, object] | None) -> str:
    clean = _normalize_window_metadata(meta)
    if clean is None:
        return "No window captured"
    title = str(clean.get("title", "") or "").strip()
    class_name = str(clean.get("class", "") or "").strip()
    label = title or class_name or "Window"
    width = _coerce_int(clean.get("client_width"), 0)
    height = _coerce_int(clean.get("client_height"), 0)
    cx = _coerce_screen_coord(clean.get("client_x"))
    cy = _coerce_screen_coord(clean.get("client_y"))
    size = f"{width}x{height}" if width > 0 and height > 0 else "size unknown"
    return f"{label} | client {cx},{cy} | {size}"


def _monitor_name_key(name: str) -> str:
    return str(name or "").replace("\\\\.\\", "").replace("\\", "").strip().upper()


def _rect_dict(left: int, top: int, right: int, bottom: int, *, name: str = "") -> dict[str, object]:
    return {
        "name": str(name or ""),
        "left": int(left),
        "top": int(top),
        "width": max(1, int(right) - int(left)),
        "height": max(1, int(bottom) - int(top)),
    }


def _native_monitor_rects() -> list[dict[str, object]]:
    if os.name != "nt" or _USER32 is None:
        return []
    monitors: list[dict[str, object]] = []
    try:
        def _enum_monitor(hmon, _hdc, _rect, _data):
            info = _MONITORINFOEX()  # type: ignore[name-defined]
            info.cbSize = ctypes.sizeof(_MONITORINFOEX)  # type: ignore[name-defined]
            if int(_USER32.GetMonitorInfoW(hmon, ctypes.byref(info))):
                rect = info.rcMonitor
                monitors.append(
                    _rect_dict(
                        int(rect.left),
                        int(rect.top),
                        int(rect.right),
                        int(rect.bottom),
                        name=str(info.szDevice or ""),
                    )
                )
            return 1

        callback = _MONITORENUMPROC(_enum_monitor)  # type: ignore[name-defined]
        _USER32.EnumDisplayMonitors(None, None, callback, 0)
    except Exception:
        return []
    return monitors


def _native_monitor_rect_for_screen(screen) -> dict[str, object] | None:
    if screen is None:
        return None
    try:
        screen_name = _monitor_name_key(str(screen.name() or ""))
    except Exception:
        screen_name = ""
    if screen_name:
        for monitor in _native_monitor_rects():
            if _monitor_name_key(str(monitor.get("name", "") or "")) == screen_name:
                return monitor
    try:
        geo = screen.geometry()
        ratio = float(screen.devicePixelRatio() or 1.0)
        return {
            "name": str(screen.name() or ""),
            "left": int(round(float(geo.left()) * ratio)),
            "top": int(round(float(geo.top()) * ratio)),
            "width": max(1, int(round(float(geo.width()) * ratio))),
            "height": max(1, int(round(float(geo.height()) * ratio))),
        }
    except Exception:
        return None


def _map_point_between_rects(
    x: int,
    y: int,
    source_left: int,
    source_top: int,
    source_width: int,
    source_height: int,
    target_left: int,
    target_top: int,
    target_width: int,
    target_height: int,
) -> tuple[int, int]:
    source_w = max(1, int(source_width))
    source_h = max(1, int(source_height))
    target_w = max(1, int(target_width))
    target_h = max(1, int(target_height))
    norm_x = (float(int(x) - int(source_left)) / float(source_w))
    norm_y = (float(int(y) - int(source_top)) / float(source_h))
    return (
        int(round(float(target_left) + (norm_x * float(target_w)))),
        int(round(float(target_top) + (norm_y * float(target_h)))),
    )


def _qt_point_to_win32_tuple(point: QtCore.QPoint) -> tuple[int, int]:
    try:
        screen = QtGui.QGuiApplication.screenAt(point)
    except Exception:
        screen = None
    if screen is None:
        try:
            for candidate in QtGui.QGuiApplication.screens():
                if candidate.geometry().contains(point):
                    screen = candidate
                    break
        except Exception:
            screen = None
    if screen is None:
        return int(point.x()), int(point.y())
    try:
        geo = screen.geometry()
        native = _native_monitor_rect_for_screen(screen)
        if native is None:
            return int(point.x()), int(point.y())
        return _map_point_between_rects(
            int(point.x()),
            int(point.y()),
            int(geo.left()),
            int(geo.top()),
            int(geo.width()),
            int(geo.height()),
            int(native.get("left", geo.left())),
            int(native.get("top", geo.top())),
            int(native.get("width", geo.width())),
            int(native.get("height", geo.height())),
        )
    except Exception:
        return int(point.x()), int(point.y())


def _win32_tuple_to_qt_point(x: int, y: int) -> QtCore.QPoint:
    x_int = int(x)
    y_int = int(y)
    try:
        screens = list(QtGui.QGuiApplication.screens())
    except Exception:
        screens = []
    for screen in screens:
        try:
            geo = screen.geometry()
            native = _native_monitor_rect_for_screen(screen)
            if native is None:
                continue
            native_left = int(native.get("left", geo.left()))
            native_top = int(native.get("top", geo.top()))
            native_width = max(1, int(native.get("width", geo.width())))
            native_height = max(1, int(native.get("height", geo.height())))
            if not (native_left <= x_int < native_left + native_width and native_top <= y_int < native_top + native_height):
                continue
            qt_x, qt_y = _map_point_between_rects(
                x_int,
                y_int,
                native_left,
                native_top,
                native_width,
                native_height,
                int(geo.left()),
                int(geo.top()),
                int(geo.width()),
                int(geo.height()),
            )
            return QtCore.QPoint(qt_x, qt_y)
        except Exception:
            continue
    return QtCore.QPoint(x_int, y_int)


def _captured_point_to_screen_coords(point: QtCore.QPoint) -> tuple[int, int]:
    cursor = _cursor_pos()
    if cursor is not None:
        return _coerce_screen_coord(cursor[0]), _coerce_screen_coord(cursor[1])
    return _qt_point_to_win32_tuple(point)


def _step_has_click_point(step: dict) -> bool:
    return isinstance(step, dict) and _raw_has_value(step, "x", "screen_x") and _raw_has_value(step, "y", "screen_y")


def _screen_metadata_at(point: QtCore.QPoint) -> dict[str, object] | None:
    try:
        screen = QtGui.QGuiApplication.screenAt(point)
    except Exception:
        screen = None
    if screen is None:
        try:
            for candidate in QtGui.QGuiApplication.screens():
                if candidate.geometry().contains(point):
                    screen = candidate
                    break
        except Exception:
            screen = None
    if screen is None:
        return None
    try:
        geo = screen.geometry()
        out: dict[str, object] = {
            "name": str(screen.name() or ""),
            "left": int(geo.left()),
            "top": int(geo.top()),
            "width": int(geo.width()),
            "height": int(geo.height()),
        }
        try:
            out["device_pixel_ratio"] = float(screen.devicePixelRatio() or 1.0)
        except Exception:
            pass
        native = _native_monitor_rect_for_screen(screen)
        if native is not None:
            out["native_left"] = int(native.get("left", geo.left()))
            out["native_top"] = int(native.get("top", geo.top()))
            out["native_width"] = max(1, int(native.get("width", geo.width())))
            out["native_height"] = max(1, int(native.get("height", geo.height())))
        return out
    except Exception:
        return None


def _screen_metadata_at_screen_coords(x: int, y: int) -> dict[str, object] | None:
    return _screen_metadata_at(_win32_tuple_to_qt_point(int(x), int(y)))


def _hwnd_int(hwnd) -> int:
    try:
        return int(hwnd or 0)
    except Exception:
        return 0


def _window_is_valid(hwnd: int) -> bool:
    if os.name != "nt" or _USER32 is None or not hwnd:
        return False
    try:
        return bool(int(_USER32.IsWindow(ctypes.c_void_p(int(hwnd)))))
    except Exception:
        return False


def _window_text(hwnd: int) -> str:
    if os.name != "nt" or _USER32 is None or not hwnd:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(512)
        _USER32.GetWindowTextW(ctypes.c_void_p(int(hwnd)), buffer, len(buffer))
        return str(buffer.value or "").strip()
    except Exception:
        return ""


def _window_class(hwnd: int) -> str:
    if os.name != "nt" or _USER32 is None or not hwnd:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(256)
        _USER32.GetClassNameW(ctypes.c_void_p(int(hwnd)), buffer, len(buffer))
        return str(buffer.value or "").strip()
    except Exception:
        return ""


def _window_pid(hwnd: int) -> int:
    if os.name != "nt" or _USER32 is None or not hwnd:
        return 0
    try:
        pid = ctypes.c_ulong(0)
        _USER32.GetWindowThreadProcessId(ctypes.c_void_p(int(hwnd)), ctypes.byref(pid))
        return int(pid.value or 0)
    except Exception:
        return 0


def _window_client_size(hwnd: int) -> tuple[int, int] | None:
    if os.name != "nt" or _USER32 is None or not hwnd:
        return None
    try:
        rect = _RECT()  # type: ignore[name-defined]
        if int(_USER32.GetClientRect(ctypes.c_void_p(int(hwnd)), ctypes.byref(rect))):
            width = max(1, int(rect.right) - int(rect.left))
            height = max(1, int(rect.bottom) - int(rect.top))
            return width, height
    except Exception:
        return None
    return None


def _screen_to_client_xy(hwnd: int, x: int, y: int) -> tuple[int, int] | None:
    if os.name != "nt" or _USER32 is None or not hwnd:
        return None
    try:
        raw = _POINT(int(x), int(y))  # type: ignore[name-defined]
        if int(_USER32.ScreenToClient(ctypes.c_void_p(int(hwnd)), ctypes.byref(raw))):
            return int(raw.x), int(raw.y)
    except Exception:
        return None
    return None


def _screen_to_client(hwnd: int, point: QtCore.QPoint) -> tuple[int, int] | None:
    x, y = _qt_point_to_win32_tuple(point)
    return _screen_to_client_xy(hwnd, x, y)


def _client_to_screen(hwnd: int, client_x: int, client_y: int) -> tuple[int, int] | None:
    if os.name != "nt" or _USER32 is None or not hwnd:
        return None
    try:
        raw = _POINT(int(client_x), int(client_y))  # type: ignore[name-defined]
        if int(_USER32.ClientToScreen(ctypes.c_void_p(int(hwnd)), ctypes.byref(raw))):
            return int(raw.x), int(raw.y)
    except Exception:
        return None
    return None


def _top_level_window_from_screen_coords(x: int, y: int) -> int:
    if os.name != "nt" or _USER32 is None:
        return 0
    try:
        raw = _POINT(int(x), int(y))  # type: ignore[name-defined]
        hwnd = _hwnd_int(_USER32.WindowFromPoint(raw))
        if hwnd:
            root = _hwnd_int(_USER32.GetAncestor(ctypes.c_void_p(hwnd), _GA_ROOT))
            if root:
                hwnd = root
        return hwnd
    except Exception:
        return 0


def _top_level_window_from_point(point: QtCore.QPoint) -> int:
    x, y = _qt_point_to_win32_tuple(point)
    return _top_level_window_from_screen_coords(x, y)


def _window_metadata_at_screen_coords(x: int, y: int) -> dict[str, object] | None:
    hwnd = _top_level_window_from_screen_coords(int(x), int(y))
    if not hwnd or not _window_is_valid(hwnd):
        return None
    client = _screen_to_client_xy(hwnd, int(x), int(y))
    if client is None:
        return None
    size = _window_client_size(hwnd)
    out: dict[str, object] = {
        "hwnd": int(hwnd),
        "client_x": int(client[0]),
        "client_y": int(client[1]),
    }
    title = _window_text(hwnd)
    if title:
        out["title"] = title
    class_name = _window_class(hwnd)
    if class_name:
        out["class"] = class_name
    pid = _window_pid(hwnd)
    if pid > 0:
        out["pid"] = pid
    if size is not None:
        out["client_width"] = int(size[0])
        out["client_height"] = int(size[1])
    return out


def _window_metadata_at(point: QtCore.QPoint) -> dict[str, object] | None:
    x, y = _qt_point_to_win32_tuple(point)
    return _window_metadata_at_screen_coords(x, y)


def _window_matches_metadata(hwnd: int, meta: dict[str, object]) -> bool:
    if not _window_is_valid(hwnd):
        return False
    try:
        if not int(_USER32.IsWindowVisible(ctypes.c_void_p(int(hwnd)))):  # type: ignore[union-attr]
            return False
    except Exception:
        pass
    expected_class = str(meta.get("class", "") or "").strip()
    expected_title = str(meta.get("title", "") or "").strip()
    expected_pid = _coerce_int(meta.get("pid"), 0)
    if expected_class and _window_class(hwnd) != expected_class:
        return False
    current_title = _window_text(hwnd)
    if expected_title and current_title != expected_title:
        return False
    if expected_pid > 0 and _window_pid(hwnd) != expected_pid:
        return False
    return bool(expected_class or expected_title or expected_pid)


def _find_window_for_metadata(meta: dict[str, object]) -> int:
    if os.name != "nt" or _USER32 is None:
        return 0
    clean = _normalize_window_metadata(meta)
    if clean is None:
        return 0
    hwnd = _coerce_int(clean.get("hwnd"), 0)
    if hwnd > 0 and _window_matches_metadata(hwnd, clean):
        return hwnd
    found = {"hwnd": 0}
    try:
        def _enum_proc(candidate, _lparam):
            candidate_int = _hwnd_int(candidate)
            if candidate_int and _window_matches_metadata(candidate_int, clean):
                found["hwnd"] = candidate_int
                return 0
            return 1

        callback = _WNDENUMPROC(_enum_proc)  # type: ignore[name-defined]
        _USER32.EnumWindows(callback, None)
    except Exception:
        return 0
    return int(found["hwnd"])


def _resolve_window_click_point(meta: dict[str, object]) -> tuple[int, int] | None:
    clean = _normalize_window_metadata(meta)
    if clean is None:
        return None
    hwnd = _find_window_for_metadata(clean)
    if not hwnd:
        return None
    try:
        _USER32.SetForegroundWindow(ctypes.c_void_p(int(hwnd)))  # type: ignore[union-attr]
    except Exception:
        pass
    current_size = _window_client_size(hwnd)
    original_w = max(1, _coerce_int(clean.get("client_width"), 1))
    original_h = max(1, _coerce_int(clean.get("client_height"), 1))
    client_x = _coerce_screen_coord(clean.get("client_x"))
    client_y = _coerce_screen_coord(clean.get("client_y"))
    if current_size is not None:
        current_w, current_h = current_size
        if current_w != original_w:
            client_x = int(round((float(client_x) / float(original_w)) * float(current_w)))
        if current_h != original_h:
            client_y = int(round((float(client_y) / float(original_h)) * float(current_h)))
    return _client_to_screen(hwnd, client_x, client_y)


def _resolve_click_point(step: dict) -> tuple[int, int, str]:
    if isinstance(step, dict) and _raw_has_value(step, "coord_mode", "coordinate_mode", "target_mode"):
        mode = _normalize_click_coord_mode(_raw_first_value(step, "coord_mode", "coordinate_mode", "target_mode", default=_CLICK_COORD_SCREEN))
    elif isinstance(step, dict) and _normalize_window_metadata(step.get("window")) is not None:
        mode = _CLICK_COORD_WINDOW
    else:
        mode = _CLICK_COORD_SCREEN
    if isinstance(step, dict) and mode == _CLICK_COORD_WINDOW:
        window_meta = _normalize_window_metadata(step.get("window"))
        if window_meta is not None:
            resolved = _resolve_window_click_point(window_meta)
            if resolved is not None:
                return _coerce_screen_coord(resolved[0]), _coerce_screen_coord(resolved[1]), "window"
    return (
        _coerce_screen_coord(_raw_first_value(step, "x", "screen_x", default=0)),
        _coerce_screen_coord(_raw_first_value(step, "y", "screen_y", default=0)),
        "screen fallback" if mode == _CLICK_COORD_WINDOW else "screen",
    )


def _normalize_injection_mode(raw) -> str:
    token = _normalize_token(str(raw or ""))
    if token in {"hybrid", "both", "combo"}:
        return _MODE_HYBRID
    if token in {"scancode", "scan", "scanmode", "game"}:
        return _MODE_SCANCODE
    return _MODE_VK


def _new_default_action(index: int) -> dict[str, object]:
    return {
        "action": _default_action_label(_ACTION_TYPE_KEY),
        "type": _ACTION_TYPE_KEY,
        "key": "",
        "delay_ms": _DEFAULT_DELAY_MS,
    }


def _default_action_label(action_type: str) -> str:
    clean_type = _normalize_action_type(action_type)
    if clean_type == _ACTION_TYPE_CLICK:
        return "Click"
    if clean_type == _ACTION_TYPE_HOVER:
        return "Hover"
    if clean_type == _ACTION_TYPE_TEXT:
        return "Text"
    if clean_type == _ACTION_TYPE_LOOP_START:
        return "Loop Start"
    if clean_type == _ACTION_TYPE_LOOP_END:
        return "Loop End"
    return "Key"


def _is_type_default_action_label(label: str) -> bool:
    clean = str(label or "").strip()
    return clean in {
        _default_action_label(_ACTION_TYPE_KEY),
        _default_action_label(_ACTION_TYPE_CLICK),
        _default_action_label(_ACTION_TYPE_HOVER),
        _default_action_label(_ACTION_TYPE_TEXT),
        _default_action_label(_ACTION_TYPE_LOOP_START),
        _default_action_label(_ACTION_TYPE_LOOP_END),
    }


def _next_loop_number(steps: list[dict[str, object]]) -> int:
    highest = 0
    for step in list(steps or []):
        if isinstance(step, dict) and _action_uses_loop_settings(step.get("type", "")):
            highest = max(highest, _coerce_loop_number(step.get("loop_number", step.get("loop_id", step.get("loop", 1))), 1))
    return _coerce_loop_number(highest + 1, 1)


def _new_loop_marker(loop_number: int, marker_type: str, *, loop_count: int = 2) -> dict[str, object]:
    clean_type = _normalize_action_type(marker_type)
    if clean_type != _ACTION_TYPE_LOOP_END:
        clean_type = _ACTION_TYPE_LOOP_START
    clean_number = _coerce_loop_number(loop_number, 1)
    clean_count = _coerce_loop_count(loop_count, 2)
    suffix = "Start" if clean_type == _ACTION_TYPE_LOOP_START else "End"
    return {
        "action": f"Loop {clean_number} {suffix}",
        "type": clean_type,
        "loop_number": clean_number,
        "loop_count": clean_count,
        "delay_ms": 0,
    }


def _default_sequence() -> list[dict[str, object]]:
    return [_new_default_action(0)]


def _normalize_step(raw, index: int) -> dict[str, object] | None:
    if isinstance(raw, dict):
        delay_ms = _coerce_delay_ms(raw.get("delay_ms", raw.get("delay", _DEFAULT_DELAY_MS)))
        inferred_type = raw.get("type", raw.get("action_type", ""))
        if not inferred_type and (_raw_has_value(raw, "x", "screen_x") or _raw_has_value(raw, "y", "screen_y")):
            inferred_type = _ACTION_TYPE_CLICK
        if not inferred_type and any(name in raw for name in ("text", "write_text", "type_text", "content", "loop_text_rows", "text_rows")):
            inferred_type = _ACTION_TYPE_TEXT
        action_type = _normalize_action_type(inferred_type)
        default_name = _default_action_label(action_type)
        action = str(raw.get("action", default_name) or "").strip() or default_name
        if action_type in {_ACTION_TYPE_CLICK, _ACTION_TYPE_HOVER}:
            step = {
                "action": action,
                "type": action_type,
                "delay_ms": delay_ms,
            }
            if action_type == _ACTION_TYPE_CLICK:
                step["button"] = _normalize_mouse_button(raw.get("button", raw.get("mouse_button", _MOUSE_BUTTON_LEFT)))
                step["click_ms"] = _coerce_click_ms(raw.get("click_ms", raw.get("mouse_hold_ms", _DEFAULT_CLICK_MS)))
            if _raw_has_value(raw, "x", "screen_x") and _raw_has_value(raw, "y", "screen_y"):
                step["x"] = _coerce_screen_coord(_raw_first_value(raw, "x", "screen_x", default=0))
                step["y"] = _coerce_screen_coord(_raw_first_value(raw, "y", "screen_y", default=0))
            screen_meta = _normalize_screen_metadata(raw.get("screen"))
            if screen_meta is not None:
                step["screen"] = screen_meta
            window_meta = _normalize_window_metadata(raw.get("window"))
            if window_meta is not None:
                step["window"] = window_meta
            mode_raw = raw.get("coord_mode", raw.get("coordinate_mode", raw.get("target_mode", "")))
            if mode_raw:
                step["coord_mode"] = _normalize_click_coord_mode(mode_raw)
            elif window_meta is not None:
                step["coord_mode"] = _CLICK_COORD_WINDOW
            else:
                step["coord_mode"] = _CLICK_COORD_SCREEN
            return step

        if action_type == _ACTION_TYPE_TEXT:
            text_source = _normalize_text_source(raw.get("text_source", raw.get("text_mode", "")))
            text_rows = _normalize_loop_text_rows(raw.get("loop_text_rows", raw.get("text_rows", None)))
            if text_rows:
                text_source = _TEXT_SOURCE_LOOP_TABLE
            step = {
                "action": action,
                "type": _ACTION_TYPE_TEXT,
                "text": _raw_text_value(raw, "text", "write_text", "type_text", "content"),
                "delay_ms": delay_ms,
            }
            if text_source == _TEXT_SOURCE_LOOP_TABLE:
                step["text_source"] = _TEXT_SOURCE_LOOP_TABLE
                step["loop_text_rows"] = text_rows
            return step

        if _action_uses_loop_settings(action_type):
            loop_number = _coerce_loop_number(raw.get("loop_number", raw.get("loop_id", raw.get("loop", 1))), 1)
            return {
                "action": action,
                "type": action_type,
                "loop_number": loop_number,
                "loop_count": _coerce_loop_count(raw.get("loop_count", raw.get("iterations", raw.get("repeat_count", 2))), 2),
                "delay_ms": delay_ms,
            }

        key = str(raw.get("key", raw.get("hotkey", "")) or "").strip()
        step = {"action": action, "type": _ACTION_TYPE_KEY, "key": key, "delay_ms": delay_ms}
        hold_flag = bool(raw.get("hold", False) or raw.get("press_and_hold", False))
        if hold_flag:
            step["hold"] = True
        if "hold_ms" in raw:
            step["hold_ms"] = _coerce_action_hold_ms(raw.get("hold_ms", raw.get("hold", _DEFAULT_KEY_HOLD_MS)))
        loop_condition = _key_loop_condition_iteration(raw)
        if loop_condition is not None:
            step["loop_condition_enabled"] = True
            step["loop_condition_operator"] = _key_loop_condition_operator(raw)
            step["loop_condition_iteration"] = loop_condition
        return step
    if isinstance(raw, str):
        action = _default_action_label(_ACTION_TYPE_KEY)
        key = raw.strip()
        return {"action": action, "type": _ACTION_TYPE_KEY, "key": key, "delay_ms": _DEFAULT_DELAY_MS}
    return None


def _normalize_sequence(raw) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    if isinstance(raw, list):
        for index, entry in enumerate(raw):
            step = _normalize_step(entry, index)
            if step is not None:
                steps.append(step)
            if len(steps) >= _MAX_STEPS:
                break
    if not steps:
        return _default_sequence()
    if len(steps) < _MIN_STEPS:
        while len(steps) < _MIN_STEPS:
            steps.append(_normalize_step({}, len(steps)) or _new_default_action(len(steps)))
    return steps


def _loop_pair_maps(steps: list[dict[str, object]]) -> tuple[dict[int, int], dict[int, int]]:
    start_to_end: dict[int, int] = {}
    end_to_start: dict[int, int] = {}
    stack: list[tuple[int, int]] = []
    for idx, step in enumerate(list(steps or [])):
        action_type = _normalize_action_type(step.get("type", _ACTION_TYPE_KEY)) if isinstance(step, dict) else _ACTION_TYPE_KEY
        if action_type == _ACTION_TYPE_LOOP_START:
            stack.append((idx, _coerce_loop_number(step.get("loop_number", 1), 1)))
            continue
        if action_type != _ACTION_TYPE_LOOP_END:
            continue
        loop_number = _coerce_loop_number(step.get("loop_number", 1), 1)
        for stack_idx in range(len(stack) - 1, -1, -1):
            start_idx, start_number = stack[stack_idx]
            if start_number != loop_number:
                continue
            del stack[stack_idx]
            start_to_end[start_idx] = idx
            end_to_start[idx] = start_idx
            break
    return start_to_end, end_to_start


def _enclosing_loop_end_index(steps: list[dict[str, object]], action_index: int) -> int | None:
    target = int(action_index)
    start_to_end, _end_to_start = _loop_pair_maps(steps)
    candidates: list[tuple[int, int]] = []
    for start_idx, end_idx in start_to_end.items():
        if int(start_idx) < target <= int(end_idx):
            candidates.append((int(start_idx), int(end_idx)))
    if not candidates:
        return None
    _start_idx, end_idx = max(candidates, key=lambda pair: pair[0])
    return int(end_idx)


def _sync_loop_marker_settings(steps: list[dict[str, object]], edited_index: int, previous_loop_number: int | None) -> None:
    if not (0 <= int(edited_index) < len(steps)):
        return
    edited = steps[int(edited_index)]
    if not isinstance(edited, dict) or not _action_uses_loop_settings(edited.get("type", "")):
        return
    new_number = _coerce_loop_number(edited.get("loop_number", 1), 1)
    count = _coerce_loop_count(edited.get("loop_count", 2), 2)
    old_number = _coerce_loop_number(previous_loop_number, new_number) if previous_loop_number is not None else new_number
    for idx, step in enumerate(steps):
        if idx == int(edited_index) or not isinstance(step, dict) or not _action_uses_loop_settings(step.get("type", "")):
            continue
        current_number = _coerce_loop_number(step.get("loop_number", 1), 1)
        if current_number != old_number and current_number != new_number:
            continue
        step["loop_number"] = new_number
        step["loop_count"] = count


def _read_sequence(node_item) -> list[dict[str, object]]:
    model = getattr(node_item, "model", None)
    if model is None:
        return _default_sequence()
    raw = _param_value(model, _SEQUENCE_PARAM).strip()
    if not raw:
        return _default_sequence()
    try:
        parsed = json.loads(raw)
    except Exception:
        return _default_sequence()
    return _normalize_sequence(parsed)


def _write_sequence(node_item, steps: list[dict[str, object]], *, notify_scene: bool = True) -> list[dict[str, object]]:
    clean = _normalize_sequence(steps)
    _set_param_value(
        node_item,
        _SEQUENCE_PARAM,
        json.dumps(clean, separators=(",", ":")),
        notify_scene=notify_scene,
    )
    return clean


def _read_lead_in_ms(node_item) -> int:
    model = getattr(node_item, "model", None)
    if model is None:
        return _DEFAULT_LEAD_IN_MS
    return _coerce_lead_in_ms(_param_value(model, _LEAD_IN_PARAM))


def _write_lead_in_ms(node_item, value: int, *, notify_scene: bool = True) -> int:
    clean = _coerce_lead_in_ms(value)
    _set_param_value(node_item, _LEAD_IN_PARAM, str(clean), notify_scene=notify_scene)
    return clean


def _read_key_hold_ms(node_item) -> int:
    model = getattr(node_item, "model", None)
    if model is None:
        return _DEFAULT_KEY_HOLD_MS
    return _coerce_key_hold_ms(_param_value(model, _KEY_HOLD_PARAM))


def _write_key_hold_ms(node_item, value: int, *, notify_scene: bool = True) -> int:
    clean = _coerce_key_hold_ms(value)
    _set_param_value(node_item, _KEY_HOLD_PARAM, str(clean), notify_scene=notify_scene)
    return clean


def _read_injection_mode(node_item) -> str:
    model = getattr(node_item, "model", None)
    if model is None:
        return _MODE_VK
    return _normalize_injection_mode(_param_value(model, _INJECTION_MODE_PARAM))


def _write_injection_mode(node_item, value: str, *, notify_scene: bool = True) -> str:
    clean = _normalize_injection_mode(value)
    _set_param_value(node_item, _INJECTION_MODE_PARAM, clean, notify_scene=notify_scene)
    return clean


def _dialog_parent(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                if win is not None:
                    return win
        except Exception:
            pass
    try:
        win = node_item.window()
        if win is not None:
            return win
    except Exception:
        pass
    try:
        active = QtWidgets.QApplication.activeWindow()
        if active is not None and active.isWindow():
            return active
    except Exception:
        pass
    return None


def _apply_dialog_style(dialog, parent=None) -> None:
    try:
        icon = None
        if parent is not None:
            icon = parent.window().windowIcon()
        app = QtWidgets.QApplication.instance()
        if (icon is None or icon.isNull()) and app is not None:
            icon = app.windowIcon()
        if icon is not None and not icon.isNull():
            dialog.setWindowIcon(icon)
    except Exception:
        pass
    dialog.setStyleSheet(
        "QDialog{background:#0f1216;color:#e6edf3;}"
        "QLabel{color:#e6edf3;}"
        "QLineEdit{background:#111827;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:5px 7px;}"
        "QTextEdit{background:#111827;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:5px 7px;}"
        "QTableWidget{background:#111827;color:#e2e8f0;gridline-color:#334155;border:1px solid #475569;}"
        "QHeaderView::section{background:#1f2937;color:#cbd5e1;border:0;border-right:1px solid #334155;padding:4px 6px;}"
        "QSpinBox{background:#111827;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:5px 7px;}"
        "QComboBox{background:#111827;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:5px 7px;}"
        "QComboBox QAbstractItemView{background:#0f1216;color:#e2e8f0;selection-background-color:#1d4ed8;}"
        "QCheckBox{color:#e6edf3;spacing:6px;}"
        "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:4px 12px;min-width:72px;}"
        "QPushButton:hover{background:#273449;}"
    )


def _preset_payload(
    steps: list[dict[str, object]],
    lead_in_ms: int,
    key_hold_ms: int,
    injection_mode: str,
) -> dict[str, object]:
    return {
        "version": _PRESET_VERSION,
        "lead_in_ms": _coerce_lead_in_ms(lead_in_ms),
        "key_hold_ms": _coerce_key_hold_ms(key_hold_ms),
        "injection_mode": _normalize_injection_mode(injection_mode),
        "actions": _normalize_sequence(steps),
    }


def _normalize_preset_payload(raw) -> tuple[dict[str, object] | None, str]:
    if not isinstance(raw, dict):
        return None, "Preset JSON must be an object."

    actions = raw.get("actions")
    if actions is None:
        actions = raw.get("steps")
    if not isinstance(actions, list) or not actions:
        return None, "Preset JSON must include a non-empty 'actions' list."

    payload = _preset_payload(
        _normalize_sequence(actions),
        raw.get("lead_in_ms", raw.get("lead_in", _DEFAULT_LEAD_IN_MS)),
        raw.get("key_hold_ms", raw.get("key_hold", _DEFAULT_KEY_HOLD_MS)),
        raw.get("injection_mode", raw.get("input_mode", _MODE_VK)),
    )
    return payload, ""


def _vk_for_named_token(token: str) -> int | None:
    raw = str(token or "").strip()
    if raw == "-":
        return int(_SPECIAL_KEYS["-"])
    key = _normalize_token(token)
    if not key:
        return None
    if key in _SPECIAL_KEYS:
        return int(_SPECIAL_KEYS[key])
    if len(key) == 1 and key.isalpha():
        return ord(key.upper())
    if len(key) == 1 and key.isdigit():
        return ord(key)
    return None


def _vk_and_modifiers_for_char(ch: str) -> tuple[int | None, list[int]]:
    if os.name != "nt" or _USER32 is None or len(ch) != 1:
        return None, []
    try:
        mapped = int(_USER32.VkKeyScanW(ch))
    except Exception:
        return None, []
    if mapped == -1:
        return None, []
    vk = mapped & 0xFF
    if vk <= 0:
        return None, []
    mods: list[int] = []
    state = (mapped >> 8) & 0xFF
    if state & 0x01:
        mods.append(_MODIFIER_KEYS["shift"])
    if state & 0x02:
        mods.append(_MODIFIER_KEYS["ctrl"])
    if state & 0x04:
        mods.append(_MODIFIER_KEYS["alt"])
    return int(vk), mods


def _key_event_for_vk(vk: int, keyup: bool, *, injection_mode: str) -> tuple[int, int, int]:
    use_scancode = _normalize_injection_mode(injection_mode) == _MODE_SCANCODE
    if use_scancode and os.name == "nt" and _USER32 is not None:
        scan_ex = 0
        scan = 0
        extended = False
        try:
            scan_ex = int(_USER32.MapVirtualKeyW(int(vk), _MAPVK_VK_TO_VSC_EX))
        except Exception:
            scan_ex = 0
        if scan_ex:
            scan = int(scan_ex & 0xFF)
            if (scan_ex & 0xFF00) in (0xE000, 0xE100):
                extended = True
        if scan <= 0:
            try:
                scan = int(_USER32.MapVirtualKeyW(int(vk), _MAPVK_VK_TO_VSC) & 0xFF)
            except Exception:
                scan = 0
        if scan > 0:
            flags = _KEYEVENTF_SCANCODE
            if keyup:
                flags |= _KEYEVENTF_KEYUP
            if extended or int(vk) in _SCANCODE_EXTENDED_KEYS:
                flags |= _KEYEVENTF_EXTENDEDKEY
            return 0, int(scan), int(flags)
    return int(vk), 0, (_KEYEVENTF_KEYUP if keyup else 0)


def _build_hotkey_events(key_text: str, *, injection_mode: str = _MODE_VK) -> tuple[list[tuple[int, int, int]] | None, str]:
    text = str(key_text or "").strip()
    if not text:
        return None, "Key is empty."
    parts = [part.strip() for part in re.split(r"\s*\+\s*", text) if part.strip()]
    if not parts:
        return None, "Key is empty."

    explicit_modifiers: list[int] = []
    primary_tokens: list[str] = []
    for token in parts:
        normalized = _normalize_token(token)
        if normalized in _MODIFIER_KEYS:
            explicit_modifiers.append(int(_MODIFIER_KEYS[normalized]))
        else:
            primary_tokens.append(token)
    if not primary_tokens:
        return None, "Action needs one non-modifier key."
    if len(primary_tokens) > 1:
        return None, "Use one key per action plus optional modifiers."

    primary = primary_tokens[0]
    vk = _vk_for_named_token(primary)
    all_modifiers = list(explicit_modifiers)
    if vk is None and len(primary) == 1:
        mapped_vk, char_modifiers = _vk_and_modifiers_for_char(primary)
        if mapped_vk is not None:
            vk = mapped_vk
            all_modifiers.extend(char_modifiers)
        elif explicit_modifiers:
            return None, f"Cannot combine modifiers with '{primary}'."
        else:
            codepoint = ord(primary)
            return [
                (0, codepoint, _KEYEVENTF_UNICODE),
                (0, codepoint, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP),
            ], ""
    if vk is None:
        return None, f"Unknown key token '{primary}'."

    modifiers = _dedupe_keep_order(all_modifiers)
    events: list[tuple[int, int, int]] = []
    for mod_vk in modifiers:
        events.append(_key_event_for_vk(int(mod_vk), False, injection_mode=injection_mode))
    events.append(_key_event_for_vk(int(vk), False, injection_mode=injection_mode))
    events.append(_key_event_for_vk(int(vk), True, injection_mode=injection_mode))
    for mod_vk in reversed(modifiers):
        events.append(_key_event_for_vk(int(mod_vk), True, injection_mode=injection_mode))
    return events, ""


def _build_text_events(text: str, *, injection_mode: str = _MODE_VK) -> tuple[list[tuple[int, int, int]] | None, str]:
    clean = str(text or "")
    if not clean:
        return None, "Text action is empty."
    events: list[tuple[int, int, int]] = []
    for ch in clean:
        if ch == "\r":
            continue
        if ch == "\n":
            events.append(_key_event_for_vk(0x0D, False, injection_mode=injection_mode))
            events.append(_key_event_for_vk(0x0D, True, injection_mode=injection_mode))
            continue
        vk, mods = _vk_and_modifiers_for_char(ch)
        if vk is None:
            codepoint = ord(ch)
            events.append((0, codepoint, _KEYEVENTF_UNICODE))
            events.append((0, codepoint, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP))
            continue
        dedup_mods = _dedupe_keep_order(mods)
        for mod_vk in dedup_mods:
            events.append(_key_event_for_vk(int(mod_vk), False, injection_mode=injection_mode))
        events.append(_key_event_for_vk(int(vk), False, injection_mode=injection_mode))
        events.append(_key_event_for_vk(int(vk), True, injection_mode=injection_mode))
        for mod_vk in reversed(dedup_mods):
            events.append(_key_event_for_vk(int(mod_vk), True, injection_mode=injection_mode))
    return events, ""


def _send_input_events(events: list[tuple[int, int, int]]) -> tuple[bool, str]:
    if os.name != "nt" or _USER32 is None:
        return False, "Keyboard playback currently requires Windows."
    if not events:
        return False, "No keyboard events to send."
    try:
        ctypes.set_last_error(0)
        buffer = (_INPUT * len(events))()  # type: ignore[name-defined]
        for idx, (vk, scan, flags) in enumerate(events):
            buffer[idx].type = _INPUT_KEYBOARD
            buffer[idx].ki = _KEYBDINPUT(  # type: ignore[name-defined]
                wVk=int(vk),
                wScan=int(scan),
                dwFlags=int(flags),
                time=0,
                dwExtraInfo=0,
            )
        cb_size = int(ctypes.sizeof(_INPUT))  # type: ignore[name-defined]
        sent = int(_USER32.SendInput(len(events), buffer, cb_size))  # type: ignore[name-defined]
        last_error = int(ctypes.get_last_error() or 0)
    except Exception as exc:
        return False, f"Keyboard send failed: {exc}"
    if sent != len(events):
        if last_error:
            return False, f"Keyboard send incomplete ({sent}/{len(events)} events), winerr={last_error}."
        return False, f"Keyboard send incomplete ({sent}/{len(events)} events)."
    return True, ""


def _is_matching_keyup_event(
    down_event: tuple[int, int, int],
    up_event: tuple[int, int, int],
) -> bool:
    down_vk, down_scan, down_flags = int(down_event[0]), int(down_event[1]), int(down_event[2])
    up_vk, up_scan, up_flags = int(up_event[0]), int(up_event[1]), int(up_event[2])
    return (
        down_vk == up_vk
        and down_scan == up_scan
        and up_flags == (down_flags | _KEYEVENTF_KEYUP)
        and not bool(down_flags & _KEYEVENTF_KEYUP)
    )


def _send_input_event_stream(events: list[tuple[int, int, int]], *, key_hold_ms: int) -> tuple[bool, str]:
    if not events:
        return False, "No keyboard events to send."
    hold_ms = _coerce_action_hold_ms(key_hold_ms)
    inter_seconds = max(0.0, float(_INTER_EVENT_MS) / 1000.0)
    hold_seconds = max(0.0, float(hold_ms) / 1000.0)
    for idx, event in enumerate(events):
        ok, message = _send_input_events([event])
        if not ok:
            return ok, message
        if idx + 1 >= len(events):
            continue
        if _is_matching_keyup_event(event, events[idx + 1]) and hold_seconds > 0.0:
            time.sleep(hold_seconds)
        elif inter_seconds > 0.0:
            time.sleep(inter_seconds)
    return True, ""


def _lparam_for_vk(vk: int, keyup: bool) -> int:
    scan_ex = 0
    scan = 0
    extended = False
    if os.name == "nt" and _USER32 is not None:
        try:
            scan_ex = int(_USER32.MapVirtualKeyW(int(vk), _MAPVK_VK_TO_VSC_EX))
        except Exception:
            scan_ex = 0
        if scan_ex:
            scan = int(scan_ex & 0xFF)
            if (scan_ex & 0xFF00) in (0xE000, 0xE100):
                extended = True
        if scan <= 0:
            try:
                scan = int(_USER32.MapVirtualKeyW(int(vk), _MAPVK_VK_TO_VSC) & 0xFF)
            except Exception:
                scan = 0
    lparam = 1 | ((int(scan) & 0xFF) << 16)
    if extended or int(vk) in _SCANCODE_EXTENDED_KEYS:
        lparam |= (1 << 24)
    if keyup:
        lparam |= (1 << 30) | (1 << 31)
    return int(lparam)


def _window_message_for_vk(vk: int, keyup: bool) -> int:
    if int(vk) == _MODIFIER_KEYS["alt"]:
        return _WM_SYSKEYUP if keyup else _WM_SYSKEYDOWN
    return _WM_KEYUP if keyup else _WM_KEYDOWN


def _post_window_event(hwnd, event: tuple[int, int, int]) -> tuple[bool, str]:
    if os.name != "nt" or _USER32 is None:
        return False, "Window-message keyboard path requires Windows."
    vk, scan, flags = int(event[0]), int(event[1]), int(event[2])
    keyup = bool(flags & _KEYEVENTF_KEYUP)
    if flags & _KEYEVENTF_UNICODE:
        if keyup:
            return True, ""
        codepoint = int(scan) if int(scan) > 0 else int(vk)
        if codepoint <= 0:
            return False, "Unicode event had no codepoint."
        ctypes.set_last_error(0)
        ok = int(_USER32.PostMessageW(hwnd, _WM_CHAR, int(codepoint), 1))
        if ok:
            return True, ""
        err = int(ctypes.get_last_error() or 0)
        if err:
            return False, f"PostMessage(WM_CHAR) failed, winerr={err}."
        return False, "PostMessage(WM_CHAR) failed."
    if vk <= 0:
        return False, "Window-message event missing VK code."
    msg = _window_message_for_vk(vk, keyup)
    lparam = _lparam_for_vk(vk, keyup)
    ctypes.set_last_error(0)
    ok = int(_USER32.PostMessageW(hwnd, int(msg), int(vk), int(lparam)))
    if ok:
        return True, ""
    err = int(ctypes.get_last_error() or 0)
    if err:
        return False, f"PostMessage failed, winerr={err}."
    return False, "PostMessage failed."


def _post_window_event_stream(events: list[tuple[int, int, int]], *, key_hold_ms: int) -> tuple[bool, str]:
    if os.name != "nt" or _USER32 is None:
        return False, "Window-message keyboard path requires Windows."
    hwnd = _USER32.GetForegroundWindow()
    if not hwnd:
        return False, "No foreground window for window-message keyboard path."
    hold_ms = _coerce_action_hold_ms(key_hold_ms)
    inter_seconds = max(0.0, float(_INTER_EVENT_MS) / 1000.0)
    hold_seconds = max(0.0, float(hold_ms) / 1000.0)
    for idx, event in enumerate(events):
        ok, message = _post_window_event(hwnd, event)
        if not ok:
            return ok, message
        if idx + 1 >= len(events):
            continue
        if _is_matching_keyup_event(event, events[idx + 1]) and hold_seconds > 0.0:
            time.sleep(hold_seconds)
        elif inter_seconds > 0.0:
            time.sleep(inter_seconds)
    return True, ""


def _send_text_by_character(
    text: str,
    *,
    injection_mode: str,
    key_hold_ms: int,
    send_stream,
) -> tuple[bool, str]:
    clean = str(text or "")
    if clean == "":
        return False, "Text action is empty."
    hold_ms = _text_key_hold_ms(key_hold_ms)
    char_gap = _text_inter_char_seconds()
    for ch in clean:
        if ch == "\r":
            continue
        events, error = _build_text_events(ch, injection_mode=injection_mode)
        if events is None:
            return False, error or f"Invalid text character {repr(ch)}."
        if not events:
            continue
        ok, message = send_stream(events, key_hold_ms=hold_ms)
        if not ok:
            return ok, message
        if char_gap > 0.0:
            time.sleep(char_gap)
    return True, ""


def _dispatch_key_action_window_message(text: str, *, key_hold_ms: int = _DEFAULT_KEY_HOLD_MS) -> tuple[bool, str]:
    clean = str(text or "").strip()
    if not clean:
        return False, "Key is empty."
    events, error = _build_hotkey_events(clean, injection_mode=_MODE_VK)
    if events is None and "+" not in clean:
        events, error = _build_text_events(clean, injection_mode=_MODE_VK)
    if events is None:
        return False, error or "Invalid key action."
    return _post_window_event_stream(events, key_hold_ms=key_hold_ms)


def _dispatch_text_action_window_message(text: str, *, key_hold_ms: int = _DEFAULT_KEY_HOLD_MS) -> tuple[bool, str]:
    clean = str(text or "")
    if clean == "":
        return False, "Text action is empty."
    return _send_text_by_character(
        clean,
        injection_mode=_MODE_VK,
        key_hold_ms=key_hold_ms,
        send_stream=_post_window_event_stream,
    )


def _dispatch_key_action(
    text: str,
    *,
    injection_mode: str = _MODE_VK,
    key_hold_ms: int = _DEFAULT_KEY_HOLD_MS,
) -> tuple[bool, str]:
    clean = str(text or "").strip()
    if not clean:
        return False, "Key is empty."

    mode = _normalize_injection_mode(injection_mode)
    if mode == _MODE_HYBRID:
        ok_si, msg_si = _dispatch_key_action(clean, injection_mode=_MODE_SCANCODE, key_hold_ms=key_hold_ms)
        ok_wm, msg_wm = _dispatch_key_action_window_message(clean, key_hold_ms=key_hold_ms)
        if ok_si or ok_wm:
            return True, ""
        return False, f"Hybrid path failed. SendInput: {msg_si} | WindowMsg: {msg_wm}"
    events, error = _build_hotkey_events(clean, injection_mode=mode)
    if events is None and "+" not in clean:
        events, error = _build_text_events(clean, injection_mode=mode)
    if events is None:
        return False, error or "Invalid key action."
    if _coerce_action_hold_ms(key_hold_ms) > 0:
        return _send_input_event_stream(events, key_hold_ms=key_hold_ms)
    return _send_input_events(events)


def _dispatch_text_action(
    text: str,
    *,
    injection_mode: str = _MODE_VK,
    key_hold_ms: int = _DEFAULT_KEY_HOLD_MS,
) -> tuple[bool, str]:
    clean = str(text or "")
    if clean == "":
        return False, "Text action is empty."

    mode = _normalize_injection_mode(injection_mode)
    if mode == _MODE_HYBRID:
        ok_si, msg_si = _dispatch_text_action(clean, injection_mode=_MODE_SCANCODE, key_hold_ms=key_hold_ms)
        ok_wm, msg_wm = _dispatch_text_action_window_message(clean, key_hold_ms=key_hold_ms)
        if ok_si or ok_wm:
            return True, ""
        return False, f"Hybrid text path failed. SendInput: {msg_si} | WindowMsg: {msg_wm}"
    return _send_text_by_character(
        clean,
        injection_mode=mode,
        key_hold_ms=key_hold_ms,
        send_stream=_send_input_event_stream,
    )


def _virtual_desktop_bounds() -> tuple[int, int, int, int] | None:
    if os.name != "nt" or _USER32 is None:
        return None
    try:
        left = int(_USER32.GetSystemMetrics(_SM_XVIRTUALSCREEN))
        top = int(_USER32.GetSystemMetrics(_SM_YVIRTUALSCREEN))
        width = int(_USER32.GetSystemMetrics(_SM_CXVIRTUALSCREEN))
        height = int(_USER32.GetSystemMetrics(_SM_CYVIRTUALSCREEN))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return left, top, width, height


def _primary_desktop_bounds() -> tuple[int, int, int, int] | None:
    if os.name != "nt" or _USER32 is None:
        return None
    try:
        width = int(_USER32.GetSystemMetrics(_SM_CXSCREEN))
        height = int(_USER32.GetSystemMetrics(_SM_CYSCREEN))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return 0, 0, width, height


def _absolute_mouse_coord(value: int, origin: int, size: int) -> int:
    if size <= 1:
        return 0
    normalized = round(((int(value) - int(origin)) * 65535) / max(1, int(size) - 1))
    return max(0, min(65535, int(normalized)))


def _mouse_button_flags(button: str) -> tuple[int, int]:
    clean = _normalize_mouse_button(button)
    if clean == _MOUSE_BUTTON_RIGHT:
        return _MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP
    if clean == _MOUSE_BUTTON_MIDDLE:
        return _MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP
    return _MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP


def _send_mouse_input_events(events: list[tuple[int, int, int, int]]) -> tuple[bool, str]:
    if os.name != "nt" or _USER32 is None:
        return False, "Mouse playback currently requires Windows."
    if not events:
        return False, "No mouse events to send."
    try:
        ctypes.set_last_error(0)
        buffer = (_INPUT * len(events))()  # type: ignore[name-defined]
        for idx, (dx, dy, mouse_data, flags) in enumerate(events):
            buffer[idx].type = _INPUT_MOUSE
            buffer[idx].mi = _MOUSEINPUT(  # type: ignore[name-defined]
                dx=int(dx),
                dy=int(dy),
                mouseData=int(mouse_data),
                dwFlags=int(flags),
                time=0,
                dwExtraInfo=0,
            )
        cb_size = int(ctypes.sizeof(_INPUT))  # type: ignore[name-defined]
        sent = int(_USER32.SendInput(len(events), buffer, cb_size))  # type: ignore[name-defined]
        last_error = int(ctypes.get_last_error() or 0)
    except Exception as exc:
        return False, f"Mouse send failed: {exc}"
    if sent != len(events):
        if last_error:
            return False, f"Mouse send incomplete ({sent}/{len(events)} events), winerr={last_error}."
        return False, f"Mouse send incomplete ({sent}/{len(events)} events)."
    return True, ""


def _dispatch_click_action(
    x: int,
    y: int,
    *,
    button: str = _MOUSE_BUTTON_LEFT,
    click_ms: int = _DEFAULT_CLICK_MS,
) -> tuple[bool, str]:
    bounds = _virtual_desktop_bounds()
    if bounds is None:
        return False, "Mouse click playback currently requires Windows."
    left, top, width, height = bounds
    x_int = _coerce_screen_coord(x)
    y_int = _coerce_screen_coord(y)
    right = left + width - 1
    bottom = top + height - 1
    if x_int < left or x_int > right or y_int < top or y_int > bottom:
        return False, f"Click coordinate {x_int},{y_int} is outside the current virtual desktop."

    ok, message = _set_cursor_pos_verified(x_int, y_int)
    if not ok:
        return ok, message
    time.sleep(0.02)

    down_flag, up_flag = _mouse_button_flags(button)
    ok, message = _send_mouse_input_events([(0, 0, 0, down_flag)])
    if not ok:
        return ok, message
    hold_seconds = max(0.0, float(_coerce_click_ms(click_ms)) / 1000.0)
    if hold_seconds > 0.0:
        time.sleep(hold_seconds)
    return _send_mouse_input_events([(0, 0, 0, up_flag)])


def _dispatch_mouse_move(x: int, y: int) -> tuple[bool, str]:
    bounds = _virtual_desktop_bounds()
    if bounds is None:
        return False, "Mouse movement currently requires Windows."
    left, top, width, height = bounds
    x_int = _coerce_screen_coord(x)
    y_int = _coerce_screen_coord(y)
    right = left + width - 1
    bottom = top + height - 1
    if x_int < left or x_int > right or y_int < top or y_int > bottom:
        return False, f"Click coordinate {x_int},{y_int} is outside the current virtual desktop."
    abs_x = _absolute_mouse_coord(x_int, left, width)
    abs_y = _absolute_mouse_coord(y_int, top, height)
    move_flags = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK
    return _send_mouse_input_events([(abs_x, abs_y, 0, move_flags)])


def _dispatch_mouse_move_primary(x: int, y: int) -> tuple[bool, str]:
    bounds = _primary_desktop_bounds()
    if bounds is None:
        return False, "Primary-screen mouse movement currently requires Windows."
    left, top, width, height = bounds
    x_int = _coerce_screen_coord(x)
    y_int = _coerce_screen_coord(y)
    right = left + width - 1
    bottom = top + height - 1
    if x_int < left or x_int > right or y_int < top or y_int > bottom:
        return False, f"Click coordinate {x_int},{y_int} is outside the current primary screen."
    abs_x = _absolute_mouse_coord(x_int, left, width)
    abs_y = _absolute_mouse_coord(y_int, top, height)
    move_flags = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE
    return _send_mouse_input_events([(abs_x, abs_y, 0, move_flags)])


def _dispatch_mouse_move_relative(dx: int, dy: int) -> tuple[bool, str]:
    dx_int = max(-500, min(500, _coerce_int(dx, 0)))
    dy_int = max(-500, min(500, _coerce_int(dy, 0)))
    if dx_int == 0 and dy_int == 0:
        return True, ""
    return _send_mouse_input_events([(dx_int, dy_int, 0, _MOUSEEVENTF_MOVE)])


def _cursor_pos() -> tuple[int, int] | None:
    if os.name != "nt" or _USER32 is None:
        return None
    try:
        point = _POINT()  # type: ignore[name-defined]
        if int(_USER32.GetCursorPos(ctypes.byref(point))):
            return int(point.x), int(point.y)
    except Exception:
        return None
    return None


def _release_cursor_capture() -> str:
    if os.name != "nt" or _USER32 is None:
        return ""
    messages: list[str] = []
    try:
        _USER32.ReleaseCapture()
    except Exception:
        pass
    try:
        ctypes.set_last_error(0)
        if not int(_USER32.ClipCursor(None)):
            last_error = int(ctypes.get_last_error() or 0)
            messages.append(f"ClipCursor release failed, winerr={last_error}." if last_error else "ClipCursor release failed.")
    except Exception as exc:
        messages.append(f"ClipCursor release failed: {exc}")
    return " ".join(messages)


def _set_cursor_pos_verified(x: int, y: int) -> tuple[bool, str]:
    if os.name != "nt" or _USER32 is None:
        return False, "Cursor positioning currently requires Windows."
    x_int = _coerce_screen_coord(x)
    y_int = _coerce_screen_coord(y)
    bounds = _virtual_desktop_bounds()
    if bounds is None:
        return False, "Cursor positioning currently requires Windows virtual desktop metrics."
    left, top, width, height = bounds
    right = left + width - 1
    bottom = top + height - 1
    if x_int < left or x_int > right or y_int < top or y_int > bottom:
        return False, f"Click coordinate {x_int},{y_int} is outside the current virtual desktop."

    last_pos = None
    last_message = ""

    def _verify_after_move() -> bool:
        nonlocal last_pos
        time.sleep(0.03)
        last_pos = _cursor_pos()
        return last_pos is not None and abs(last_pos[0] - x_int) <= 2 and abs(last_pos[1] - y_int) <= 2

    def _try_move(label: str, move_func) -> bool:
        nonlocal last_message
        ok, message = move_func()
        if not ok:
            last_message = message
            return False
        if _verify_after_move():
            return True
        if last_pos is not None:
            last_message = (
                f"{label} landed at {last_pos[0]},{last_pos[1]} instead of {x_int},{y_int}."
            )
        return False

    for _attempt in range(4):
        release_message = _release_cursor_capture()
        if release_message:
            last_message = release_message
        try:
            ctypes.set_last_error(0)
            set_ok = bool(int(_USER32.SetCursorPos(int(x_int), int(y_int))))
            if not set_ok:
                last_error = int(ctypes.get_last_error() or 0)
                last_message = f"SetCursorPos failed, winerr={last_error}." if last_error else "SetCursorPos failed."
        except Exception as exc:
            last_message = f"SetCursorPos failed: {exc}"
            set_ok = False
        if set_ok:
            if _verify_after_move():
                return True, ""

        if _try_move("Virtual desktop mouse move", lambda: _dispatch_mouse_move(x_int, y_int)):
            return True, ""
        if _try_move("Primary-screen mouse move", lambda: _dispatch_mouse_move_primary(x_int, y_int)):
            return True, ""
        if last_pos is not None:
            delta_x = int(x_int - last_pos[0])
            delta_y = int(y_int - last_pos[1])
            if abs(delta_x) <= 1000 and abs(delta_y) <= 1000:
                if _try_move("Relative mouse correction", lambda: _dispatch_mouse_move_relative(delta_x, delta_y)):
                    return True, ""
    if last_pos is None:
        return False, last_message or "Could not verify cursor position."
    suffix = f" Last movement error: {last_message}" if last_message else ""
    return False, f"Cursor did not reach target {x_int},{y_int}; current position is {last_pos[0]},{last_pos[1]}.{suffix}"


def _connected_serial_target_configs(node_item):
    try:
        from nodes.serial_com.spec import connected_serial_target_configs
    except Exception:
        return []
    try:
        return list(connected_serial_target_configs(node_item) or [])
    except Exception:
        return []


def _serial_route_label(configs) -> str:
    ports = [str(getattr(config, "port", "") or "").strip() for config in list(configs or [])]
    ports = [port for port in ports if port]
    if not ports:
        return "Pico HID"
    return f"Pico HID via {', '.join(ports)}"


def _serial_key_for_text_char(ch: str) -> str | None:
    if ch == "\n":
        return "enter"
    if ch == "\t":
        return "tab"
    if ch == " ":
        return "space"
    if ch == "\r":
        return None
    return ch


def _send_serial_text_action(serial_sessions, text: str, hold_ms: int, stop_event=None) -> tuple[bool, str]:
    clean = str(text or "")
    if clean == "":
        return False, "Text action is empty."
    hold = _text_key_hold_ms(hold_ms)
    char_gap = _text_inter_char_seconds()
    for ch in clean:
        if stop_event is not None and stop_event.is_set():
            return False, "Playback stopped."
        key = _serial_key_for_text_char(ch)
        if key is None:
            continue
        if "|" in key:
            return False, "Pico text playback cannot send the '|' character with the current firmware command format."
        for session in list(serial_sessions or []):
            ok, message = session.tap(key, hold)
            if not ok:
                return ok, message
        if char_gap > 0.0:
            time.sleep(char_gap)
    return True, ""


def _move_serial_mouse_relative(serial_sessions, dx: int, dy: int) -> tuple[bool, str]:
    remaining_x = _coerce_int(dx, 0)
    remaining_y = _coerce_int(dy, 0)
    if remaining_x == 0 and remaining_y == 0:
        return True, ""
    step_size = 50
    max_steps = 256
    for _step in range(max_steps):
        if remaining_x == 0 and remaining_y == 0:
            return True, ""
        chunk_x = max(-step_size, min(step_size, remaining_x))
        chunk_y = max(-step_size, min(step_size, remaining_y))
        for session in list(serial_sessions or []):
            move_rel = getattr(session, "mouse_move_rel", None)
            if move_rel is None:
                return False, "Pico backend does not expose relative mouse movement."
            ok, message = move_rel(chunk_x, chunk_y)
            if not ok:
                return False, message
        remaining_x -= chunk_x
        remaining_y -= chunk_y
        time.sleep(0.004)
    return False, f"Relative mouse movement exceeded {max_steps} steps."


def _serial_mouse_step_for_delta(delta: int, largest_delta: int, tolerance: int) -> int:
    if abs(delta) <= tolerance:
        return 0
    if largest_delta > 300:
        max_step = 32
    elif largest_delta > 120:
        max_step = 20
    elif largest_delta > 40:
        max_step = 12
    elif largest_delta > 12:
        max_step = 6
    else:
        max_step = 1
    magnitude = min(max_step, abs(delta))
    return magnitude if delta > 0 else -magnitude


def _move_serial_mouse_to_target(
    serial_sessions,
    target_x: int,
    target_y: int,
    *,
    tolerance: int = 3,
    max_steps: int = 420,
) -> tuple[bool, str]:
    x_int = _coerce_screen_coord(target_x)
    y_int = _coerce_screen_coord(target_y)
    last_pos = _cursor_pos()
    if last_pos is None:
        return False, "Could not read cursor position for Pico relative cursor movement."
    best_pos = last_pos
    best_distance = max(abs(x_int - last_pos[0]), abs(y_int - last_pos[1]))
    unchanged_steps = 0
    for _step in range(max_steps):
        last_pos = _cursor_pos()
        if last_pos is None:
            return False, "Could not verify cursor position during Pico relative cursor movement."
        delta_x = int(x_int - last_pos[0])
        delta_y = int(y_int - last_pos[1])
        distance = max(abs(delta_x), abs(delta_y))
        if distance <= tolerance:
            return True, ""
        if distance < best_distance:
            best_distance = distance
            best_pos = last_pos

        move_x = _serial_mouse_step_for_delta(delta_x, distance, tolerance)
        move_y = _serial_mouse_step_for_delta(delta_y, distance, tolerance)
        if move_x == 0 and move_y == 0:
            return True, ""
        ok, message = _move_serial_mouse_relative(serial_sessions, move_x, move_y)
        if not ok:
            return False, message
        time.sleep(0.012)

        new_pos = _cursor_pos()
        if new_pos == last_pos:
            unchanged_steps += 1
        else:
            unchanged_steps = 0
        if unchanged_steps >= 10:
            return (
                False,
                f"Pico relative mouse movement did not change the reported cursor position from {last_pos[0]},{last_pos[1]}.",
            )
    return (
        False,
        f"Pico relative cursor movement did not reach {x_int},{y_int}; closest position was {best_pos[0]},{best_pos[1]} ({best_distance}px away).",
    )


def _position_pointer_target(serial_sessions, x: int, y: int) -> tuple[bool, str, bool]:
    ok, message = _set_cursor_pos_verified(x, y)
    used_feedback_fallback = False
    if serial_sessions and not ok and "outside the current virtual desktop" not in str(message):
        rel_ok, rel_message = _move_serial_mouse_to_target(serial_sessions, x, y)
        if rel_ok:
            ok = True
            message = ""
            used_feedback_fallback = True
        else:
            message = f"{message} Pico feedback cursor fallback failed: {rel_message}"
    if ok:
        time.sleep(0.08)
    return ok, message, used_feedback_fallback


def _virtual_desktop_geometry_qt() -> QtCore.QRect:
    rect = None
    try:
        for screen in QtGui.QGuiApplication.screens():
            geometry = screen.geometry()
            rect = QtCore.QRect(geometry) if rect is None else rect.united(geometry)
    except Exception:
        rect = None
    if rect is None or not rect.isValid():
        try:
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is not None:
                rect = QtCore.QRect(screen.geometry())
        except Exception:
            rect = None
    if rect is None or not rect.isValid():
        rect = QtCore.QRect(0, 0, 800, 600)
    return rect


def _global_pos_from_mouse_event(event) -> QtCore.QPoint:
    try:
        return event.globalPosition().toPoint()
    except Exception:
        pass
    try:
        return event.globalPos()
    except Exception:
        return QtGui.QCursor.pos()


class _ClickCaptureOverlay(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._point: QtCore.QPoint | None = None
        self.setWindowTitle("Capture Click")
        self.setModal(True)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowStaysOnTopHint
        )
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(_virtual_desktop_geometry_qt())
        try:
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        except Exception:
            pass

    def captured_point(self) -> QtCore.QPoint | None:
        return QtCore.QPoint(self._point) if self._point is not None else None

    def keyPressEvent(self, event):
        try:
            if int(event.key()) == int(QtCore.Qt.Key_Escape):
                self.reject()
                return
        except Exception:
            pass
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self._point = _global_pos_from_mouse_event(event)
        self.accept()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(2, 6, 23, 92))
        painter.setPen(QtGui.QColor("#e2e8f0"))
        font = painter.font()
        font.setPointSize(15)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Click the target point or press Esc")


class _ActionEditDialog(QtWidgets.QDialog):
    def __init__(
        self,
        action_text: str,
        key_text: str,
        delay_ms: int,
        hold: bool = False,
        hold_ms: int | None = None,
        key_loop_condition_enabled: bool = False,
        key_loop_condition_operator: str = _LOOP_CONDITION_EQ,
        key_loop_condition_iteration: int = 1,
        action_type: str = _ACTION_TYPE_KEY,
        click_x: int | None = None,
        click_y: int | None = None,
        click_button: str = _MOUSE_BUTTON_LEFT,
        click_ms: int = _DEFAULT_CLICK_MS,
        click_screen: dict[str, object] | None = None,
        click_window: dict[str, object] | None = None,
        click_coord_mode: str = _CLICK_COORD_SCREEN,
        write_text: str = "",
        text_source: str = _TEXT_SOURCE_SINGLE,
        loop_text_rows: list[dict[str, object]] | None = None,
        loop_number: int = 1,
        loop_count: int = 2,
        parent=None,
    ):
        super().__init__(parent)
        self._click_has_point = click_x is not None and click_y is not None
        self._click_screen = _normalize_screen_metadata(click_screen)
        self._click_window = _normalize_window_metadata(click_window)
        if self._click_window is not None and not click_coord_mode:
            click_coord_mode = _CLICK_COORD_WINDOW
        self._click_coord_mode = _normalize_click_coord_mode(click_coord_mode)

        self.setWindowTitle("Edit Sequence Action")
        self.setModal(True)
        self.resize(620, 460)
        _apply_dialog_style(self, parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._action_edit = QtWidgets.QLineEdit(str(action_text or ""))
        self._action_edit.setPlaceholderText("Action label (for display only)")
        form.addRow("Action", self._action_edit)

        self._type_combo = QtWidgets.QComboBox()
        self._type_combo.addItem("Key", _ACTION_TYPE_KEY)
        self._type_combo.addItem("Click", _ACTION_TYPE_CLICK)
        self._type_combo.addItem("Hover", _ACTION_TYPE_HOVER)
        self._type_combo.addItem("Text", _ACTION_TYPE_TEXT)
        self._type_combo.addItem("Loop Start", _ACTION_TYPE_LOOP_START)
        self._type_combo.addItem("Loop End", _ACTION_TYPE_LOOP_END)
        form.addRow("Type", self._type_combo)
        layout.addLayout(form, 0)

        self._stack = QtWidgets.QStackedWidget(self)
        self._stack.addWidget(
            self._build_key_page(
                key_text,
                delay_ms,
                hold,
                hold_ms,
                key_loop_condition_enabled,
                key_loop_condition_operator,
                key_loop_condition_iteration,
            )
        )
        self._stack.addWidget(self._build_click_page(click_x, click_y, click_button, click_ms))
        self._stack.addWidget(self._build_text_page(write_text, text_source, loop_text_rows))
        self._stack.addWidget(self._build_loop_page(loop_number, loop_count))
        layout.addWidget(self._stack, 0)

        self._delay_spin = QtWidgets.QSpinBox()
        self._delay_spin.setRange(int(_MIN_DELAY_MS), int(_MAX_DELAY_MS))
        self._delay_spin.setSingleStep(50)
        self._delay_spin.setSuffix(" ms")
        self._delay_spin.setValue(int(_coerce_delay_ms(delay_ms)))
        delay_form = QtWidgets.QFormLayout()
        delay_form.setContentsMargins(0, 0, 0, 0)
        delay_form.setSpacing(6)
        delay_form.addRow("Delay After", self._delay_spin)
        layout.addLayout(delay_form, 0)

        hint = QtWidgets.QLabel(
            "Key actions send keyboard input. Text writes literal text. Click and Hover capture a target. Loop blocks repeat actions between matching numbers."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        layout.addWidget(hint, 0)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 0)

        desired_type = _normalize_action_type(action_type)
        for idx in range(self._type_combo.count()):
            if str(self._type_combo.itemData(idx) or "") == desired_type:
                self._type_combo.setCurrentIndex(idx)
                break
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        self._on_type_changed(self._type_combo.currentIndex())
        self._update_click_status()
        QtCore.QTimer.singleShot(0, self._focus_initial_field)

    def _build_key_page(
        self,
        key_text: str,
        delay_ms: int,
        hold: bool,
        hold_ms: int | None,
        loop_condition_enabled: bool,
        loop_condition_operator: str,
        loop_condition_iteration: int,
    ) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        form = QtWidgets.QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._key_edit = QtWidgets.QLineEdit(str(key_text or ""))
        self._key_edit.setPlaceholderText("Example: ctrl+shift+s, F5, enter, a")
        form.addRow("Key", self._key_edit)

        self._hold_check = QtWidgets.QCheckBox("Hold key")
        self._hold_check.setChecked(bool(hold))
        form.addRow("Mode", self._hold_check)

        hold_default = hold_ms if hold_ms is not None else delay_ms
        self._hold_spin = QtWidgets.QSpinBox()
        self._hold_spin.setRange(1, int(_MAX_ACTION_HOLD_MS))
        self._hold_spin.setSingleStep(100)
        self._hold_spin.setSuffix(" ms")
        self._hold_spin.setValue(int(_coerce_action_hold_ms(hold_default)))
        self._hold_spin.setEnabled(self._hold_check.isChecked())
        self._hold_check.toggled.connect(self._hold_spin.setEnabled)
        form.addRow("Hold Time", self._hold_spin)

        condition_row = QtWidgets.QWidget(page)
        condition_layout = QtWidgets.QHBoxLayout(condition_row)
        condition_layout.setContentsMargins(0, 0, 0, 0)
        condition_layout.setSpacing(6)
        self._key_loop_condition_check = QtWidgets.QCheckBox("If loop pass")
        self._key_loop_condition_check.setChecked(bool(loop_condition_enabled))
        condition_layout.addWidget(self._key_loop_condition_check, 0)
        self._key_loop_condition_operator_combo = QtWidgets.QComboBox()
        self._key_loop_condition_operator_combo.addItem("==", _LOOP_CONDITION_EQ)
        self._key_loop_condition_operator_combo.addItem(">", _LOOP_CONDITION_GT)
        self._key_loop_condition_operator_combo.addItem("<", _LOOP_CONDITION_LT)
        self._key_loop_condition_operator_combo.addItem("=>", _LOOP_CONDITION_GTE)
        self._key_loop_condition_operator_combo.addItem("<=", _LOOP_CONDITION_LTE)
        selected_operator = _normalize_loop_condition_operator(loop_condition_operator)
        for idx in range(self._key_loop_condition_operator_combo.count()):
            if str(self._key_loop_condition_operator_combo.itemData(idx) or "") == selected_operator:
                self._key_loop_condition_operator_combo.setCurrentIndex(idx)
                break
        self._key_loop_condition_operator_combo.setEnabled(self._key_loop_condition_check.isChecked())
        self._key_loop_condition_check.toggled.connect(self._key_loop_condition_operator_combo.setEnabled)
        condition_layout.addWidget(self._key_loop_condition_operator_combo, 0)
        self._key_loop_condition_spin = QtWidgets.QSpinBox()
        self._key_loop_condition_spin.setRange(_MIN_LOOP_COUNT, _MAX_LOOP_COUNT)
        self._key_loop_condition_spin.setValue(_coerce_loop_count(loop_condition_iteration, 1))
        self._key_loop_condition_spin.setEnabled(self._key_loop_condition_check.isChecked())
        self._key_loop_condition_check.toggled.connect(self._key_loop_condition_spin.setEnabled)
        condition_layout.addWidget(self._key_loop_condition_spin, 0)
        condition_layout.addStretch(1)
        form.addRow("Condition", condition_row)
        return page

    def _build_click_page(
        self,
        click_x: int | None,
        click_y: int | None,
        click_button: str,
        click_ms: int,
    ) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        form = QtWidgets.QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        capture_row = QtWidgets.QWidget(page)
        capture_layout = QtWidgets.QHBoxLayout(capture_row)
        capture_layout.setContentsMargins(0, 0, 0, 0)
        capture_layout.setSpacing(6)
        self._capture_btn = QtWidgets.QPushButton("Capture Click")
        self._capture_btn.clicked.connect(self._capture_click)
        capture_layout.addWidget(self._capture_btn, 0)
        self._click_status = QtWidgets.QLabel("")
        self._click_status.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        capture_layout.addWidget(self._click_status, 1)
        form.addRow("Target", capture_row)

        self._click_x_spin = QtWidgets.QSpinBox()
        self._click_x_spin.setRange(_MIN_SCREEN_COORD, _MAX_SCREEN_COORD)
        self._click_x_spin.setValue(_coerce_screen_coord(click_x, 0) if click_x is not None else 0)
        form.addRow("X", self._click_x_spin)

        self._click_y_spin = QtWidgets.QSpinBox()
        self._click_y_spin.setRange(_MIN_SCREEN_COORD, _MAX_SCREEN_COORD)
        self._click_y_spin.setValue(_coerce_screen_coord(click_y, 0) if click_y is not None else 0)
        form.addRow("Y", self._click_y_spin)

        self._click_button_combo = QtWidgets.QComboBox()
        self._click_button_combo.addItem("Left", _MOUSE_BUTTON_LEFT)
        self._click_button_combo.addItem("Right", _MOUSE_BUTTON_RIGHT)
        self._click_button_combo.addItem("Middle", _MOUSE_BUTTON_MIDDLE)
        selected_button = _normalize_mouse_button(click_button)
        for idx in range(self._click_button_combo.count()):
            if str(self._click_button_combo.itemData(idx) or "") == selected_button:
                self._click_button_combo.setCurrentIndex(idx)
                break
        form.addRow("Button", self._click_button_combo)
        self._click_button_label = form.labelForField(self._click_button_combo)

        self._click_ms_spin = QtWidgets.QSpinBox()
        self._click_ms_spin.setRange(0, int(_MAX_ACTION_HOLD_MS))
        self._click_ms_spin.setSingleStep(10)
        self._click_ms_spin.setSuffix(" ms")
        self._click_ms_spin.setValue(_coerce_click_ms(click_ms))
        form.addRow("Click Time", self._click_ms_spin)
        self._click_ms_label = form.labelForField(self._click_ms_spin)

        self._coord_mode_combo = QtWidgets.QComboBox()
        self._coord_mode_combo.addItem("Window", _CLICK_COORD_WINDOW)
        self._coord_mode_combo.addItem("Screen", _CLICK_COORD_SCREEN)
        for idx in range(self._coord_mode_combo.count()):
            if str(self._coord_mode_combo.itemData(idx) or "") == self._click_coord_mode:
                self._coord_mode_combo.setCurrentIndex(idx)
                break
        form.addRow("Coordinate Source", self._coord_mode_combo)

        self._click_window_status = QtWidgets.QLabel("")
        self._click_window_status.setWordWrap(True)
        self._click_window_status.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        form.addRow("Window", self._click_window_status)

        self._click_x_spin.valueChanged.connect(self._on_click_coord_edited)
        self._click_y_spin.valueChanged.connect(self._on_click_coord_edited)
        self._coord_mode_combo.currentIndexChanged.connect(lambda _idx: self._update_click_status())
        return page

    def _build_text_page(
        self,
        write_text: str,
        text_source: str = _TEXT_SOURCE_SINGLE,
        loop_text_rows: list[dict[str, object]] | None = None,
    ) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._text_list_check = QtWidgets.QCheckBox("Use loop text list")
        self._text_list_check.setChecked(_normalize_text_source(text_source) == _TEXT_SOURCE_LOOP_TABLE)
        layout.addWidget(self._text_list_check, 0)

        self._text_source_stack = QtWidgets.QStackedWidget(page)

        self._text_edit = QtWidgets.QTextEdit(str(write_text or ""))
        self._text_edit.setAcceptRichText(False)
        self._text_edit.setPlaceholderText("Text to write")
        self._text_edit.setMinimumHeight(96)
        self._text_source_stack.addWidget(self._text_edit)

        table_page = QtWidgets.QWidget(page)
        table_layout = QtWidgets.QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(6)

        self._loop_text_table = QtWidgets.QTableWidget(0, 2, table_page)
        self._loop_text_table.setHorizontalHeaderLabels(["Loop #", "Text"])
        self._loop_text_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._loop_text_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._loop_text_table.setAlternatingRowColors(True)
        self._loop_text_table.verticalHeader().setVisible(False)
        self._loop_text_table.setMinimumHeight(130)
        header = self._loop_text_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        table_layout.addWidget(self._loop_text_table, 1)

        table_buttons = QtWidgets.QHBoxLayout()
        table_buttons.setContentsMargins(0, 0, 0, 0)
        table_buttons.setSpacing(6)
        self._add_loop_text_row_btn = QtWidgets.QPushButton("Add Row")
        self._remove_loop_text_row_btn = QtWidgets.QPushButton("Remove Row")
        self._add_loop_text_row_btn.clicked.connect(self._add_loop_text_row)
        self._remove_loop_text_row_btn.clicked.connect(self._remove_loop_text_row)
        table_buttons.addWidget(self._add_loop_text_row_btn, 0)
        table_buttons.addWidget(self._remove_loop_text_row_btn, 0)
        table_buttons.addStretch(1)
        table_layout.addLayout(table_buttons, 0)

        self._text_source_stack.addWidget(table_page)
        layout.addWidget(self._text_source_stack, 1)
        self._populate_loop_text_table(loop_text_rows)
        self._text_list_check.toggled.connect(self._on_text_list_toggled)
        self._on_text_list_toggled(self._text_list_check.isChecked())
        return page

    def _on_text_list_toggled(self, checked: bool):
        try:
            self._text_source_stack.setCurrentIndex(1 if checked else 0)
        except Exception:
            pass

    def _populate_loop_text_table(self, rows: list[dict[str, object]] | None):
        clean_rows = _normalize_loop_text_rows(rows)
        if not clean_rows:
            clean_rows = [{"loop_number": 1, "text": ""}]
        self._loop_text_table.setRowCount(0)
        for row in clean_rows:
            self._insert_loop_text_row(int(row.get("loop_number", 1)), str(row.get("text", "") or ""))

    def _insert_loop_text_row(self, loop_number: int, text: str = ""):
        row = self._loop_text_table.rowCount()
        self._loop_text_table.insertRow(row)
        loop_spin = QtWidgets.QSpinBox(self._loop_text_table)
        loop_spin.setRange(_MIN_LOOP_COUNT, _MAX_LOOP_COUNT)
        loop_spin.setValue(_coerce_loop_count(loop_number, row + 1))
        self._loop_text_table.setCellWidget(row, 0, loop_spin)
        text_item = QtWidgets.QTableWidgetItem(str(text or ""))
        text_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEditable)
        self._loop_text_table.setItem(row, 1, text_item)
        self._loop_text_table.selectRow(row)

    def _add_loop_text_row(self):
        rows = self._loop_text_rows_from_table(include_empty=True)
        next_loop = 1
        if rows:
            next_loop = max(int(row.get("loop_number", 1)) for row in rows) + 1
        self._insert_loop_text_row(_coerce_loop_count(next_loop, 1), "")

    def _remove_loop_text_row(self):
        row = self._loop_text_table.currentRow()
        if row < 0:
            row = self._loop_text_table.rowCount() - 1
        if row >= 0:
            self._loop_text_table.removeRow(row)
        if self._loop_text_table.rowCount() <= 0:
            self._insert_loop_text_row(1, "")

    def _loop_text_rows_from_table(self, *, include_empty: bool = False) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for row in range(self._loop_text_table.rowCount()):
            widget = self._loop_text_table.cellWidget(row, 0)
            if isinstance(widget, QtWidgets.QSpinBox):
                loop_number = _coerce_loop_count(widget.value(), row + 1)
            else:
                item = self._loop_text_table.item(row, 0)
                loop_number = _coerce_loop_count(item.text() if item is not None else row + 1, row + 1)
            text_item = self._loop_text_table.item(row, 1)
            text_value = str(text_item.text() if text_item is not None else "")
            if not include_empty and text_value == "":
                continue
            rows.append({"loop_number": loop_number, "text": text_value})
        rows.sort(key=lambda item: int(item.get("loop_number", 1)))
        return rows

    def _build_loop_page(self, loop_number: int, loop_count: int) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        form = QtWidgets.QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._loop_number_spin = QtWidgets.QSpinBox()
        self._loop_number_spin.setRange(_MIN_LOOP_NUMBER, _MAX_LOOP_NUMBER)
        self._loop_number_spin.setValue(_coerce_loop_number(loop_number, 1))
        form.addRow("Loop ID", self._loop_number_spin)

        self._loop_count_spin = QtWidgets.QSpinBox()
        self._loop_count_spin.setRange(_MIN_LOOP_COUNT, _MAX_LOOP_COUNT)
        self._loop_count_spin.setValue(_coerce_loop_count(loop_count, 2))
        form.addRow("Run Count", self._loop_count_spin)

        self._loop_status = QtWidgets.QLabel("")
        self._loop_status.setWordWrap(True)
        self._loop_status.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        form.addRow("Loop", self._loop_status)

        self._loop_number_spin.valueChanged.connect(lambda _value: self._update_loop_status())
        self._loop_count_spin.valueChanged.connect(lambda _value: self._update_loop_status())
        return page

    def _current_action_type(self) -> str:
        return _normalize_action_type(self._type_combo.currentData())

    def _current_click_coord_mode(self) -> str:
        return _normalize_click_coord_mode(self._coord_mode_combo.currentData())

    def _on_type_changed(self, _index: int):
        action_type = self._current_action_type()
        action_label = str(self._action_edit.text() or "").strip()
        if not action_label or _is_type_default_action_label(action_label):
            self._action_edit.setText(_default_action_label(action_type))
        if _action_uses_pointer_target(action_type):
            self._stack.setCurrentIndex(1)
            self._update_pointer_page_for_type()
        elif action_type == _ACTION_TYPE_TEXT:
            self._stack.setCurrentIndex(2)
        elif _action_uses_loop_settings(action_type):
            self._stack.setCurrentIndex(3)
            self._update_loop_status()
        else:
            self._stack.setCurrentIndex(0)

    def _focus_initial_field(self):
        if _action_uses_pointer_target(self._current_action_type()):
            self._capture_btn.setFocus()
        elif self._current_action_type() == _ACTION_TYPE_TEXT:
            if self._text_list_check.isChecked():
                self._loop_text_table.setFocus()
            else:
                self._text_edit.setFocus()
        elif _action_uses_loop_settings(self._current_action_type()):
            self._loop_count_spin.setFocus()
        else:
            self._key_edit.setFocus()

    def _update_pointer_page_for_type(self):
        is_hover = self._current_action_type() == _ACTION_TYPE_HOVER
        try:
            self._capture_btn.setText("Capture Hover" if is_hover else "Capture Click")
            self._click_button_combo.setVisible(not is_hover)
            self._click_ms_spin.setVisible(not is_hover)
            if self._click_button_label is not None:
                self._click_button_label.setVisible(not is_hover)
            if self._click_ms_label is not None:
                self._click_ms_label.setVisible(not is_hover)
        except Exception:
            pass

    def _update_loop_status(self):
        try:
            marker = "start" if self._current_action_type() == _ACTION_TYPE_LOOP_START else "end"
            self._loop_status.setText(
                f"Loop ID {int(self._loop_number_spin.value())} {marker} block, matched by ID. Run count is {int(self._loop_count_spin.value())}."
            )
        except Exception:
            pass

    def _on_click_coord_edited(self, _value: int):
        self._click_has_point = True
        x = _coerce_screen_coord(self._click_x_spin.value())
        y = _coerce_screen_coord(self._click_y_spin.value())
        self._click_screen = _screen_metadata_at_screen_coords(x, y)
        self._click_window = _window_metadata_at_screen_coords(x, y)
        self._update_click_status()

    def _update_click_status(self):
        if self._click_has_point:
            self._click_status.setText(f"{int(self._click_x_spin.value())}, {int(self._click_y_spin.value())}")
        else:
            self._click_status.setText("No coordinate captured")
        try:
            self._click_window_status.setText(_window_metadata_label(self._click_window))
            if self._click_window is None and self._current_click_coord_mode() == _CLICK_COORD_WINDOW:
                self._click_window_status.setStyleSheet("QLabel{color:#f59e0b;font-size:11px;}")
            else:
                self._click_window_status.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        except Exception:
            pass

    def _capture_click(self):
        was_visible = self.isVisible()
        if was_visible:
            self.hide()
            QtWidgets.QApplication.processEvents()
        overlay = _ClickCaptureOverlay(None)
        try:
            result = overlay.exec()
        except Exception:
            result = overlay.exec_()
        point = overlay.captured_point() if result == QtWidgets.QDialog.Accepted else None
        if point is not None:
            overlay.hide()
            QtWidgets.QApplication.processEvents()
            native_x, native_y = _captured_point_to_screen_coords(point)
            captured_window = _window_metadata_at_screen_coords(native_x, native_y)
        else:
            native_x = 0
            native_y = 0
            captured_window = None
        overlay.deleteLater()
        if point is None:
            if was_visible:
                self.show()
                self.raise_()
                self.activateWindow()
            return
        self._click_x_spin.blockSignals(True)
        self._click_y_spin.blockSignals(True)
        try:
            self._click_x_spin.setValue(_coerce_screen_coord(native_x))
            self._click_y_spin.setValue(_coerce_screen_coord(native_y))
        finally:
            self._click_x_spin.blockSignals(False)
            self._click_y_spin.blockSignals(False)
        self._click_has_point = True
        self._click_screen = _screen_metadata_at(point)
        self._click_window = captured_window
        if self._click_window is not None:
            for idx in range(self._coord_mode_combo.count()):
                if str(self._coord_mode_combo.itemData(idx) or "") == _CLICK_COORD_WINDOW:
                    self._coord_mode_combo.setCurrentIndex(idx)
                    break
        self._update_click_status()
        self.accept()

    def accept(self):
        if _action_uses_pointer_target(self._current_action_type()) and not self._click_has_point:
            QtWidgets.QMessageBox.warning(self, "Keyboard Sequence", "Capture or enter a target coordinate before saving this action.")
            return
        if _action_uses_pointer_target(self._current_action_type()) and self._current_click_coord_mode() == _CLICK_COORD_WINDOW and self._click_window is None:
            QtWidgets.QMessageBox.warning(self, "Keyboard Sequence", "Capture a target window or switch Coordinate Source to Screen before saving this action.")
            return
        if self._current_action_type() == _ACTION_TYPE_TEXT:
            if self._text_list_check.isChecked():
                if not self._loop_text_rows_from_table():
                    QtWidgets.QMessageBox.warning(self, "Keyboard Sequence", "Add at least one loop text row before saving this action.")
                    return
            elif self._text_edit.toPlainText() == "":
                QtWidgets.QMessageBox.warning(self, "Keyboard Sequence", "Enter text before saving this action.")
                return
        super().accept()

    def values(self) -> dict[str, object]:
        action = str(self._action_edit.text() or "").strip()
        delay_ms = _coerce_delay_ms(self._delay_spin.value())
        if _action_uses_pointer_target(self._current_action_type()):
            action_type = self._current_action_type()
            step: dict[str, object] = {
                "action": action,
                "type": action_type,
                "delay_ms": delay_ms,
                "coord_mode": self._current_click_coord_mode(),
            }
            if action_type == _ACTION_TYPE_CLICK:
                step["button"] = _normalize_mouse_button(self._click_button_combo.currentData())
                step["click_ms"] = _coerce_click_ms(self._click_ms_spin.value())
            if self._click_has_point:
                step["x"] = _coerce_screen_coord(self._click_x_spin.value())
                step["y"] = _coerce_screen_coord(self._click_y_spin.value())
            if self._click_screen is not None:
                step["screen"] = self._click_screen
            if self._click_window is not None:
                step["window"] = self._click_window
            return step

        if self._current_action_type() == _ACTION_TYPE_TEXT:
            step = {
                "action": action,
                "type": _ACTION_TYPE_TEXT,
                "text": self._text_edit.toPlainText(),
                "delay_ms": delay_ms,
            }
            if self._text_list_check.isChecked():
                step["text_source"] = _TEXT_SOURCE_LOOP_TABLE
                step["loop_text_rows"] = self._loop_text_rows_from_table()
            return step

        if _action_uses_loop_settings(self._current_action_type()):
            return {
                "action": action,
                "type": self._current_action_type(),
                "loop_number": _coerce_loop_number(self._loop_number_spin.value(), 1),
                "loop_count": _coerce_loop_count(self._loop_count_spin.value(), 2),
                "delay_ms": delay_ms,
            }

        hold = bool(self._hold_check.isChecked())
        step = {
            "action": action,
            "type": _ACTION_TYPE_KEY,
            "key": str(self._key_edit.text() or "").strip(),
            "delay_ms": delay_ms,
        }
        if hold:
            step["hold"] = True
            step["hold_ms"] = _coerce_action_hold_ms(self._hold_spin.value())
        if self._key_loop_condition_check.isChecked():
            step["loop_condition_enabled"] = True
            step["loop_condition_operator"] = _normalize_loop_condition_operator(self._key_loop_condition_operator_combo.currentData())
            step["loop_condition_iteration"] = _coerce_loop_count(self._key_loop_condition_spin.value(), 1)
        return step


class _DelayEditDialog(QtWidgets.QDialog):
    def __init__(self, delay_ms: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Delay")
        self.setModal(True)
        self.resize(360, 132)
        _apply_dialog_style(self, parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._delay_spin = QtWidgets.QSpinBox()
        self._delay_spin.setRange(int(_MIN_DELAY_MS), int(_MAX_DELAY_MS))
        self._delay_spin.setSingleStep(50)
        self._delay_spin.setSuffix(" ms")
        self._delay_spin.setValue(int(_coerce_delay_ms(delay_ms)))
        form.addRow("Delay", self._delay_spin)
        layout.addLayout(form, 0)

        hint = QtWidgets.QLabel("Delay after this action before the next action starts.")
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        layout.addWidget(hint, 0)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 0)

        QtCore.QTimer.singleShot(0, self._delay_spin.setFocus)

    def value(self) -> int:
        return _coerce_delay_ms(self._delay_spin.value())


class _PlaybackSignals(QtCore.QObject):
    status = QtCore.Signal(str)
    finished = QtCore.Signal(bool, str)


class _KeyboardSequenceTableDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def _draw_insert_outline(self, painter, option, index):
        selected = getattr(self._owner, "_selected_action_for_insert", None)
        if selected is None or int(index.row()) != 0 or int(index.column()) != int(selected) * 2:
            return
        painter.save()
        pen = QtGui.QPen(QtGui.QColor("#93c5fd"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
        painter.restore()

    def paint(self, painter, option, index):
        try:
            super().paint(painter, option, index)
            self._draw_insert_outline(painter, option, index)
        except Exception:
            super().paint(painter, option, index)
            self._draw_insert_outline(painter, option, index)


class _KeyboardSequenceHeaderView(QtWidgets.QHeaderView):
    _ARROW_SIZE = 18
    _ARROW_MARGIN = 7

    def __init__(self, owner, parent=None):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self._owner = owner
        self.setSectionsClickable(True)

    def _event_pos(self, event) -> QtCore.QPoint:
        try:
            return event.position().toPoint()
        except Exception:
            return event.pos()

    def _selected_action_index(self) -> int | None:
        selected = getattr(self._owner, "_selected_action_for_insert", None)
        if selected is None:
            return None
        try:
            return int(selected)
        except Exception:
            return None

    def _arrow_rects(self, rect: QtCore.QRect) -> tuple[QtCore.QRect, QtCore.QRect]:
        size = int(self._ARROW_SIZE)
        top = int(rect.top() + max(0, (rect.height() - size) // 2))
        left_rect = QtCore.QRect(int(rect.left()) + self._ARROW_MARGIN, top, size, size)
        right_rect = QtCore.QRect(int(rect.right()) - self._ARROW_MARGIN - size + 1, top, size, size)
        return left_rect, right_rect

    def _draw_arrow(self, painter, rect: QtCore.QRect, icon: QtGui.QIcon, fallback: str, enabled: bool):
        painter.save()
        if not enabled:
            painter.setOpacity(0.35)
        if icon is not None and not icon.isNull():
            mode = QtGui.QIcon.Normal if enabled else QtGui.QIcon.Disabled
            icon.paint(painter, rect, QtCore.Qt.AlignCenter, mode)
        else:
            painter.setPen(QtGui.QColor("#cbd5e1" if enabled else "#64748b"))
            painter.drawText(rect, QtCore.Qt.AlignCenter, fallback)
        painter.restore()

    def _draw_action_label(self, painter, rect: QtCore.QRect, logicalIndex: int):
        if int(logicalIndex) % 2 != 0:
            return
        step_index = int(logicalIndex) // 2
        steps = list(getattr(self._owner, "_steps", []) or [])
        if not (0 <= step_index < len(steps)):
            return
        step = steps[step_index]
        action_type = _normalize_action_type(step.get("type", _ACTION_TYPE_KEY)) if isinstance(step, dict) else _ACTION_TYPE_KEY
        text = f"Action {step_index + 1}"
        icon = getattr(self._owner, "_loop_icon", QtGui.QIcon()) if _action_uses_loop_settings(action_type) else QtGui.QIcon()
        icon_size = 16 if icon is not None and not icon.isNull() else 0
        gap = 5 if icon_size else 0
        metrics = QtGui.QFontMetrics(painter.font())
        try:
            text_width = metrics.horizontalAdvance(text)
        except Exception:
            text_width = metrics.width(text)
        total_width = icon_size + gap + text_width
        x = int(rect.left() + max(0, (rect.width() - total_width) // 2))
        y = int(rect.top() + max(0, (rect.height() - icon_size) // 2))

        painter.save()
        if icon_size:
            icon_rect = QtCore.QRect(x, y, icon_size, icon_size)
            icon.paint(painter, icon_rect, QtCore.Qt.AlignCenter, QtGui.QIcon.Normal)
            x += icon_size + gap
        painter.setPen(QtGui.QColor("#cbd5e1"))
        text_rect = QtCore.QRect(x, int(rect.top()), text_width + 2, int(rect.height()))
        painter.drawText(text_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)
        painter.restore()

    def paintSection(self, painter, rect, logicalIndex):
        super().paintSection(painter, rect, logicalIndex)
        self._draw_action_label(painter, rect, logicalIndex)
        selected = self._selected_action_index()
        if selected is None or int(logicalIndex) != selected * 2:
            return
        step_count = len(getattr(self._owner, "_steps", []) or [])
        left_rect, right_rect = self._arrow_rects(rect)
        self._draw_arrow(painter, left_rect, getattr(self._owner, "_move_left_icon", QtGui.QIcon()), "<", selected > 0)
        self._draw_arrow(
            painter,
            right_rect,
            getattr(self._owner, "_move_right_icon", QtGui.QIcon()),
            ">",
            selected < step_count - 1,
        )

    def mouseReleaseEvent(self, event):
        try:
            button = event.button()
        except Exception:
            button = None
        if button == QtCore.Qt.LeftButton:
            pos = self._event_pos(event)
            logical = int(self.logicalIndexAt(pos))
            selected = self._selected_action_index()
            if selected is not None and logical == selected * 2:
                rect = QtCore.QRect(self.sectionViewportPosition(logical), 0, self.sectionSize(logical), self.height())
                left_rect, right_rect = self._arrow_rects(rect)
                if left_rect.contains(pos):
                    self._owner._move_selected_action(-1)
                    return
                if right_rect.contains(pos):
                    self._owner._move_selected_action(1)
                    return
        super().mouseReleaseEvent(event)


class KeyboardSequenceWidget(QtWidgets.QFrame):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._sync_pending = False
        self._syncing_ui = False

        self._steps = _read_sequence(node_item)
        self._lead_in_ms = _read_lead_in_ms(node_item)
        self._key_hold_ms = _read_key_hold_ms(node_item)
        self._injection_mode = _read_injection_mode(node_item)
        self._selected_action_for_insert: int | None = None
        self._loop_icon = QtGui.QIcon()
        loop_icon_path = Path(__file__).resolve().parents[2] / "icons" / "LoopArrows_Icon_s.png"
        if loop_icon_path.exists():
            self._loop_icon = QtGui.QIcon(str(loop_icon_path))
        self._move_left_icon = QtGui.QIcon()
        move_left_icon_path = Path(__file__).resolve().parents[2] / "icons" / "StreightArrow_Left_Icon.png"
        if move_left_icon_path.exists():
            self._move_left_icon = QtGui.QIcon(str(move_left_icon_path))
        self._move_right_icon = QtGui.QIcon()
        move_right_icon_path = Path(__file__).resolve().parents[2] / "icons" / "StreightArrow_right_Icon.png"
        if move_right_icon_path.exists():
            self._move_right_icon = QtGui.QIcon(str(move_right_icon_path))

        self._running = False
        self._run_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._signals = _PlaybackSignals(self)
        self._signals.status.connect(self._on_worker_status)
        self._signals.finished.connect(self._on_worker_finished)

        self.setObjectName("KeyboardSequenceWidget")
        self.setStyleSheet(
            "QFrame#KeyboardSequenceWidget{background:#0f1216;border:1px solid #334155;border-radius:0px;}"
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#334155;}"
            "QPushButton:disabled{background:#1f2937;color:#6b7280;}"
            "QTableWidget{background:#0b1016;color:#e2e8f0;gridline-color:#2b3440;border:1px solid #334155;}"
            "QHeaderView::section{background:#111827;color:#cbd5e1;padding:4px;border:1px solid #334155;}"
            "QMenu{background:#0f1216;color:#e2e8f0;border:1px solid #334155;padding:4px;}"
            "QMenu::item{padding:5px 24px 5px 18px;background:transparent;}"
            "QMenu::item:selected{background:#334155;}"
            "QMenu::item:disabled{color:#6b7280;}"
            "QMenu::separator{height:1px;background:#334155;margin:4px 6px;}"
            "QComboBox{background:#111827;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:2px 6px;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e2e8f0;selection-background-color:#1d4ed8;}"
            "QSpinBox{background:#111827;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:2px 6px;}"
        )

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)

        self._add_btn = QtWidgets.QPushButton("Add Action")
        self._add_btn.setToolTip("Add a new action at the end of the sequence.")
        self._add_btn.clicked.connect(self._on_add_action)
        button_row.addWidget(self._add_btn, 0)

        self._insert_btn = QtWidgets.QPushButton("Insert Action")
        self._insert_btn.setToolTip("Right-click an action to select where the new action should be inserted.")
        self._insert_btn.clicked.connect(self._on_insert_action)
        button_row.addWidget(self._insert_btn, 0)

        self._loop_btn = QtWidgets.QPushButton("Make Loop")
        self._loop_btn.setToolTip("Create loop start/end blocks around the selected action, or at the end.")
        self._loop_btn.clicked.connect(self._on_make_loop)
        button_row.addWidget(self._loop_btn, 0)

        self._remove_btn = QtWidgets.QPushButton("Remove Action")
        self._remove_btn.clicked.connect(self._on_remove_action)
        button_row.addWidget(self._remove_btn, 0)

        button_row.addStretch(1)

        self._lead_in_label = QtWidgets.QLabel("Lead-In")
        self._lead_in_label.setStyleSheet("QLabel{color:#94a3b8;font-weight:600;}")
        button_row.addWidget(self._lead_in_label, 0)

        self._lead_in_spin = QtWidgets.QSpinBox()
        self._lead_in_spin.setRange(0, _MAX_LEAD_IN_MS)
        self._lead_in_spin.setSingleStep(100)
        self._lead_in_spin.setSuffix(" ms")
        self._lead_in_spin.setValue(int(self._lead_in_ms))
        self._lead_in_spin.valueChanged.connect(self._on_lead_in_changed)
        button_row.addWidget(self._lead_in_spin, 0)

        self._key_hold_label = QtWidgets.QLabel("Key Hold")
        self._key_hold_label.setStyleSheet("QLabel{color:#94a3b8;font-weight:600;}")
        button_row.addWidget(self._key_hold_label, 0)

        self._key_hold_spin = QtWidgets.QSpinBox()
        self._key_hold_spin.setRange(0, _MAX_KEY_HOLD_MS)
        self._key_hold_spin.setSingleStep(5)
        self._key_hold_spin.setSuffix(" ms")
        self._key_hold_spin.setValue(int(self._key_hold_ms))
        self._key_hold_spin.valueChanged.connect(self._on_key_hold_changed)
        button_row.addWidget(self._key_hold_spin, 0)

        self._mode_label = QtWidgets.QLabel("Input Mode")
        self._mode_label.setStyleSheet("QLabel{color:#94a3b8;font-weight:600;}")
        button_row.addWidget(self._mode_label, 0)

        self._mode_combo = QtWidgets.QComboBox()
        self._mode_combo.addItem("Standard", _MODE_VK)
        self._mode_combo.addItem("Game (Scan Code)", _MODE_SCANCODE)
        self._mode_combo.addItem("Game (Hybrid)", _MODE_HYBRID)
        self._mode_combo.currentIndexChanged.connect(self._on_injection_mode_changed)
        self._mode_combo.setMinimumWidth(150)
        button_row.addWidget(self._mode_combo, 0)

        self._run_btn = QtWidgets.QPushButton("Run Sequence")
        self._run_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border:1px solid #3b82f6;border-radius:4px;padding:4px 10px;font-weight:600;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#1e3a8a;color:#94a3b8;border-color:#1e40af;}"
        )
        self._run_btn.clicked.connect(self._on_run_clicked)
        button_row.addWidget(self._run_btn, 0)

        self._stop_btn = QtWidgets.QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.setEnabled(False)
        button_row.addWidget(self._stop_btn, 0)

        root.addLayout(button_row, 0)

        preset_row = QtWidgets.QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(6)

        self._preset_label = QtWidgets.QLabel("Preset")
        self._preset_label.setStyleSheet("QLabel{color:#94a3b8;font-weight:600;}")
        preset_row.addWidget(self._preset_label, 0)

        self._load_preset_btn = QtWidgets.QPushButton("Load JSON")
        self._load_preset_btn.clicked.connect(self._on_load_preset)
        preset_row.addWidget(self._load_preset_btn, 0)

        self._save_preset_btn = QtWidgets.QPushButton("Save JSON")
        self._save_preset_btn.clicked.connect(self._on_save_preset)
        preset_row.addWidget(self._save_preset_btn, 0)

        preset_row.addStretch(1)
        root.addLayout(preset_row, 0)

        self._table = QtWidgets.QTableWidget(1, 1, self)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setHorizontalHeader(_KeyboardSequenceHeaderView(self, self._table))
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setIconSize(QtCore.QSize(20, 20))
        self._table.setItemDelegate(_KeyboardSequenceTableDelegate(self, self._table))
        self._table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._table.cellClicked.connect(self._on_table_cell_clicked)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        root.addWidget(self._table, 1)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        self._status.setWordWrap(True)
        status_row.addWidget(self._status, 1)

        self._copy_status_btn = QtWidgets.QPushButton("")
        self._copy_status_btn.setToolTip("Copy output log")
        self._copy_status_btn.setFixedSize(24, 24)
        self._copy_status_btn.setStyleSheet(
            "QPushButton{background:#1f2937;border:1px solid #334155;border-radius:4px;padding:2px;}"
            "QPushButton:hover{background:#334155;}"
            "QPushButton:disabled{background:#111827;border-color:#1f2937;}"
        )
        copy_icon_path = Path(__file__).resolve().parents[2] / "icons" / "Copy_Icon.png"
        if copy_icon_path.exists():
            self._copy_status_btn.setIcon(QtGui.QIcon(str(copy_icon_path)))
            self._copy_status_btn.setIconSize(QtCore.QSize(16, 16))
        self._copy_status_btn.clicked.connect(self._copy_status_to_clipboard)
        status_row.addWidget(self._copy_status_btn, 0)
        root.addLayout(status_row, 0)

        self._rebuild_table()
        self._set_status("Click action cells to edit. Right-click an action to select it as the insert target.")
        self._set_running(False)
        for idx in range(self._mode_combo.count()):
            if str(self._mode_combo.itemData(idx) or "") == self._injection_mode:
                self._mode_combo.setCurrentIndex(idx)
                break

        QtCore.QTimer.singleShot(0, self._ensure_scene_connections)
        QtCore.QTimer.singleShot(0, self._sync_from_model)

    def sizeHint(self):
        return QtCore.QSize(KEYBOARD_SEQUENCE_BODY_W, KEYBOARD_SEQUENCE_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(700, 258)

    def closeEvent(self, event):
        self._request_stop()
        super().closeEvent(event)

    def _ensure_scene_connections(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(self._on_scene_param_changed)
            except Exception:
                pass
        self._scene_connected = True

    def _on_scene_param_changed(self, changed_name=None, _params=None):
        if not _param_change_relevant(self._node_item, changed_name):
            return
        self._schedule_sync()

    def _schedule_sync(self):
        if self._sync_pending:
            return
        self._sync_pending = True
        QtCore.QTimer.singleShot(0, self._sync_from_model)

    def _sync_from_model(self):
        self._sync_pending = False
        self._syncing_ui = True
        try:
            latest_steps = _read_sequence(self._node_item)
            latest_lead = _read_lead_in_ms(self._node_item)
            latest_key_hold = _read_key_hold_ms(self._node_item)
            latest_mode = _read_injection_mode(self._node_item)
            if latest_steps != self._steps:
                self._steps = latest_steps
                self._clamp_selected_action_for_insert()
                self._rebuild_table()
            if latest_lead != self._lead_in_ms:
                self._lead_in_ms = latest_lead
                self._lead_in_spin.blockSignals(True)
                self._lead_in_spin.setValue(int(self._lead_in_ms))
                self._lead_in_spin.blockSignals(False)
            if latest_key_hold != self._key_hold_ms:
                self._key_hold_ms = latest_key_hold
                self._key_hold_spin.blockSignals(True)
                self._key_hold_spin.setValue(int(self._key_hold_ms))
                self._key_hold_spin.blockSignals(False)
            if latest_mode != self._injection_mode:
                self._injection_mode = latest_mode
                self._mode_combo.blockSignals(True)
                for idx in range(self._mode_combo.count()):
                    if str(self._mode_combo.itemData(idx) or "") == self._injection_mode:
                        self._mode_combo.setCurrentIndex(idx)
                        break
                self._mode_combo.blockSignals(False)
        finally:
            self._syncing_ui = False

    def _set_status(self, text: str):
        clean = str(text or "")
        self._status.setText(clean)
        try:
            self._copy_status_btn.setEnabled(bool(clean.strip()))
        except Exception:
            pass

    def _copy_status_to_clipboard(self):
        text = str(self._status.text() or "")
        if not text:
            return
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    def _clamp_selected_action_for_insert(self):
        if self._selected_action_for_insert is None:
            return
        if not self._steps:
            self._selected_action_for_insert = None
            return
        self._selected_action_for_insert = max(0, min(len(self._steps) - 1, int(self._selected_action_for_insert)))

    def _update_insert_button_state(self):
        try:
            if self._selected_action_for_insert is None:
                self._insert_btn.setEnabled(False)
                self._insert_btn.setToolTip("Right-click an action to select where the new action should be inserted.")
            else:
                self._insert_btn.setEnabled(not self._running)
                self._insert_btn.setToolTip(f"Insert a new action before action {int(self._selected_action_for_insert) + 1}.")
        except Exception:
            pass

    def _set_running(self, running: bool):
        self._running = bool(running)
        self._run_btn.setEnabled(not self._running)
        self._stop_btn.setEnabled(self._running)
        self._add_btn.setEnabled(not self._running)
        self._insert_btn.setEnabled(not self._running and self._selected_action_for_insert is not None)
        self._loop_btn.setEnabled(not self._running)
        self._remove_btn.setEnabled(not self._running)
        self._lead_in_spin.setEnabled(not self._running)
        self._key_hold_spin.setEnabled(not self._running)
        self._mode_combo.setEnabled(not self._running)
        self._load_preset_btn.setEnabled(not self._running)
        self._save_preset_btn.setEnabled(not self._running)
        self._table.setEnabled(not self._running)
        self._update_insert_button_state()

    def _persist_steps(self, notify_scene: bool = True):
        self._steps = _write_sequence(self._node_item, self._steps, notify_scene=notify_scene)
        self._clamp_selected_action_for_insert()

    def _selected_action_index(self) -> int:
        self._clamp_selected_action_for_insert()
        if self._selected_action_for_insert is not None:
            return int(self._selected_action_for_insert)
        indexes = list(self._table.selectedIndexes() or [])
        if not indexes:
            return len(self._steps) - 1
        col = int(indexes[0].column())
        return max(0, min(len(self._steps) - 1, col // 2))

    def _on_add_action(self):
        if self._running:
            return
        if len(self._steps) >= _MAX_STEPS:
            self._set_status(f"Maximum actions reached ({_MAX_STEPS}).")
            return
        insert_at = len(self._steps)
        self._steps.append(_new_default_action(insert_at))
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        self._set_status(f"Added action {insert_at + 1}.")

    def _on_insert_action(self):
        if self._running:
            return
        if len(self._steps) >= _MAX_STEPS:
            self._set_status(f"Maximum actions reached ({_MAX_STEPS}).")
            return
        if self._selected_action_for_insert is None:
            self._set_status("Right-click an action first, then use Insert Action.")
            return
        self._clamp_selected_action_for_insert()
        insert_at = int(self._selected_action_for_insert)
        self._steps.insert(insert_at, _new_default_action(insert_at))
        self._selected_action_for_insert = insert_at
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        self._set_status(f"Inserted action before action {insert_at + 1}.")

    def _move_selected_action(self, direction: int):
        if self._running:
            return
        if self._selected_action_for_insert is None:
            self._set_status("Right-click an action first, then use the move arrows.")
            return
        self._clamp_selected_action_for_insert()
        if self._selected_action_for_insert is None:
            return
        old_index = int(self._selected_action_for_insert)
        new_index = old_index + (1 if int(direction) > 0 else -1)
        if not (0 <= new_index < len(self._steps)):
            edge = "last" if new_index >= len(self._steps) else "first"
            self._set_status(f"Action {old_index + 1} is already the {edge} action.")
            try:
                self._table.horizontalHeader().viewport().update()
            except Exception:
                pass
            return
        moved_step = self._steps.pop(old_index)
        self._steps.insert(new_index, moved_step)
        self._selected_action_for_insert = new_index
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        self._set_status(f"Moved action {old_index + 1} to action {new_index + 1}.")

    def _on_make_loop(self):
        if self._running:
            return
        if len(self._steps) > _MAX_STEPS - 2:
            self._set_status(f"Need room for two loop blocks. Maximum actions is {_MAX_STEPS}.")
            return
        loop_number = _next_loop_number(self._steps)
        loop_count = 2
        start_marker = _new_loop_marker(loop_number, _ACTION_TYPE_LOOP_START, loop_count=loop_count)
        end_marker = _new_loop_marker(loop_number, _ACTION_TYPE_LOOP_END, loop_count=loop_count)
        if self._selected_action_for_insert is not None and self._steps:
            self._clamp_selected_action_for_insert()
            insert_at = int(self._selected_action_for_insert)
            enclosing_end = _enclosing_loop_end_index(self._steps, insert_at)
            self._steps.insert(insert_at, start_marker)
            if enclosing_end is None:
                self._steps.append(end_marker)
                close_label = "the end"
            else:
                self._steps.insert(enclosing_end + 1, end_marker)
                close_label = f"before loop end action {enclosing_end + 2}"
            self._selected_action_for_insert = insert_at
            message = f"Created loop {loop_number} from action {insert_at + 2} to {close_label}."
        else:
            insert_at = len(self._steps)
            self._steps.extend([start_marker, end_marker])
            self._selected_action_for_insert = insert_at
            message = f"Added loop {loop_number} blocks at the end."
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        self._set_status(message)

    def _on_remove_action(self):
        if self._running:
            return
        if len(self._steps) <= _MIN_STEPS:
            self._set_status("At least one action row must remain.")
            return
        target = self._selected_action_index()
        start_to_end, end_to_start = _loop_pair_maps(self._steps)
        remove_indices = {target}
        target_step = self._steps[target] if 0 <= target < len(self._steps) else {}
        target_type = _normalize_action_type(target_step.get("type", _ACTION_TYPE_KEY)) if isinstance(target_step, dict) else _ACTION_TYPE_KEY
        if target_type == _ACTION_TYPE_LOOP_START and target in start_to_end:
            remove_indices.add(int(start_to_end[target]))
        elif target_type == _ACTION_TYPE_LOOP_END and target in end_to_start:
            remove_indices.add(int(end_to_start[target]))
        removed_names: list[str] = []
        for remove_idx in sorted(remove_indices, reverse=True):
            if 0 <= remove_idx < len(self._steps):
                removed = self._steps.pop(remove_idx)
                removed_names.append(str(removed.get("action") or "").strip() or "action")
        if self._selected_action_for_insert is not None:
            self._selected_action_for_insert = min(target, max(0, len(self._steps) - 1)) if self._steps else None
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        if len(removed_names) > 1:
            self._set_status("Removed matching loop blocks.")
        else:
            self._set_status(f"Removed '{removed_names[0] if removed_names else 'action'}'.")

    def _on_lead_in_changed(self, value: int):
        if self._syncing_ui or self._running:
            return
        self._lead_in_ms = _write_lead_in_ms(self._node_item, int(value), notify_scene=True)

    def _on_key_hold_changed(self, value: int):
        if self._syncing_ui or self._running:
            return
        self._key_hold_ms = _write_key_hold_ms(self._node_item, int(value), notify_scene=True)

    def _on_injection_mode_changed(self, _index: int):
        if self._syncing_ui or self._running:
            return
        mode = str(self._mode_combo.currentData() or _MODE_VK)
        self._injection_mode = _write_injection_mode(self._node_item, mode, notify_scene=True)

    def _preset_dialog_start_path(self) -> str:
        suggested = "keyboard_sequence_preset.json"
        try:
            win = self.window()
            current_path = str(getattr(win, "_current_path", "") or "").strip()
            if current_path:
                suggested = str(Path(current_path).resolve().parent / suggested)
        except Exception:
            pass
        return suggested

    def _apply_preset_payload(self, payload: dict[str, object]) -> None:
        _write_lead_in_ms(self._node_item, payload.get("lead_in_ms", _DEFAULT_LEAD_IN_MS), notify_scene=False)
        _write_key_hold_ms(self._node_item, payload.get("key_hold_ms", _DEFAULT_KEY_HOLD_MS), notify_scene=False)
        _write_injection_mode(self._node_item, str(payload.get("injection_mode", _MODE_VK)), notify_scene=False)
        _write_sequence(self._node_item, list(payload.get("actions") or []), notify_scene=False)
        self._sync_from_model()
        _notify_node_params_changed(self._node_item)

    def _on_load_preset(self):
        if self._running:
            return
        parent = _dialog_parent(self._node_item) or self
        opts = QtWidgets.QFileDialog.Options()
        try:
            opts |= QtWidgets.QFileDialog.DontUseNativeDialog
        except Exception:
            pass
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            "Load Keyboard Sequence Preset",
            self._preset_dialog_start_path(),
            "JSON Files (*.json)",
            options=opts,
        )
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Keyboard Sequence", f"Could not load preset:\n{exc}")
            return
        payload, error = _normalize_preset_payload(raw)
        if payload is None:
            QtWidgets.QMessageBox.warning(self, "Keyboard Sequence", error or "Preset JSON is invalid.")
            return
        self._apply_preset_payload(payload)
        self._set_status(f"Loaded preset: {Path(path).name}")

    def _on_save_preset(self):
        if self._running:
            return
        parent = _dialog_parent(self._node_item) or self
        opts = QtWidgets.QFileDialog.Options()
        try:
            opts |= QtWidgets.QFileDialog.DontUseNativeDialog
        except Exception:
            pass
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent,
            "Save Keyboard Sequence Preset",
            self._preset_dialog_start_path(),
            "JSON Files (*.json)",
            options=opts,
        )
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".json")
        payload = _preset_payload(
            self._steps,
            self._lead_in_ms,
            self._key_hold_ms,
            self._injection_mode,
        )
        try:
            target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Keyboard Sequence", f"Could not save preset:\n{exc}")
            return
        self._set_status(f"Saved preset: {target.name}")

    def _rebuild_table(self):
        steps = list(self._steps or [])
        if not steps:
            steps = _default_sequence()
            self._steps = steps
        self._clamp_selected_action_for_insert()

        col_count = max(1, (len(steps) * 2) - 1)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(1)
            self._table.setColumnCount(col_count)
            for col in range(col_count):
                header_item = QtWidgets.QTableWidgetItem()
                header_item.setTextAlignment(QtCore.Qt.AlignCenter)
                if col % 2 == 0:
                    header_item.setText("")
                    self._table.setColumnWidth(col, 190)
                else:
                    header_item.setText("Delay")
                    self._table.setColumnWidth(col, 102)
                self._table.setHorizontalHeaderItem(col, header_item)
            self._table.setRowHeight(0, 78)

            for col in range(col_count):
                item = QtWidgets.QTableWidgetItem()
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                if col % 2 == 0:
                    step_index = col // 2
                    step = steps[step_index]
                    is_insert_target = self._selected_action_for_insert == step_index
                    action_type = _normalize_action_type(step.get("type", _ACTION_TYPE_KEY))
                    label = str(step.get("action") or "").strip() or _default_action_label(action_type)
                    if action_type == _ACTION_TYPE_CLICK:
                        button = _normalize_mouse_button(step.get("button", _MOUSE_BUTTON_LEFT))
                        if _step_has_click_point(step):
                            x = _coerce_screen_coord(_raw_first_value(step, "x", "screen_x", default=0))
                            y = _coerce_screen_coord(_raw_first_value(step, "y", "screen_y", default=0))
                            click_ms = _coerce_click_ms(step.get("click_ms", _DEFAULT_CLICK_MS))
                            coord_label = _normalize_click_coord_mode(step.get("coord_mode", _CLICK_COORD_WINDOW if _normalize_window_metadata(step.get("window")) is not None else _CLICK_COORD_SCREEN))
                            body = f"Click {button} @ {x},{y} ({coord_label}) | {click_ms} ms"
                        else:
                            body = "Click target not set"
                    elif action_type == _ACTION_TYPE_HOVER:
                        if _step_has_click_point(step):
                            x = _coerce_screen_coord(_raw_first_value(step, "x", "screen_x", default=0))
                            y = _coerce_screen_coord(_raw_first_value(step, "y", "screen_y", default=0))
                            coord_label = _normalize_click_coord_mode(step.get("coord_mode", _CLICK_COORD_WINDOW if _normalize_window_metadata(step.get("window")) is not None else _CLICK_COORD_SCREEN))
                            body = f"Hover @ {x},{y} ({coord_label})"
                        else:
                            body = "Hover target not set"
                    elif action_type == _ACTION_TYPE_TEXT:
                        if _text_action_source(step) == _TEXT_SOURCE_LOOP_TABLE:
                            row_count = len(_normalize_loop_text_rows(step.get("loop_text_rows", step.get("text_rows", None))))
                            body = f"Write loop list | {row_count} row(s)" if row_count else "Loop text list not set"
                        else:
                            text_value = _raw_text_value(step, "text", "write_text", "type_text", "content")
                            preview = _text_preview(text_value)
                            body = f"Write \"{preview}\"" if text_value != "" else "Text not set"
                    elif _action_uses_loop_settings(action_type):
                        loop_number = _coerce_loop_number(step.get("loop_number", 1), 1)
                        loop_count = _coerce_loop_count(step.get("loop_count", 2), 2)
                        marker = "Start" if action_type == _ACTION_TYPE_LOOP_START else "End"
                        body = f"Loop {loop_number} {marker} | {loop_count}x"
                    else:
                        key_text = str(step.get("key") or "").strip()
                        body = key_text if key_text else "Click to assign key"
                        if key_text and (bool(step.get("hold")) or "hold_ms" in step):
                            hold_label = _coerce_action_hold_ms(step.get("hold_ms")) if "hold_ms" in step else _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS))
                            body = f"{key_text} | Hold {hold_label} ms"
                        condition_label = _key_loop_condition_label(step)
                        if condition_label:
                            body = f"{body} | {condition_label}"
                    item.setText(f"{label}\n{body}")
                    if action_type == _ACTION_TYPE_CLICK:
                        item.setBackground(QtGui.QColor("#5a4420" if is_insert_target else "#3a2d16"))
                    elif action_type == _ACTION_TYPE_HOVER:
                        item.setBackground(QtGui.QColor("#365524" if is_insert_target else "#243a16"))
                    elif action_type == _ACTION_TYPE_TEXT:
                        item.setBackground(QtGui.QColor("#20544e" if is_insert_target else "#163a35"))
                    elif _action_uses_loop_settings(action_type):
                        item.setBackground(QtGui.QColor("#000000"))
                    elif bool(step.get("hold")) or "hold_ms" in step:
                        item.setBackground(QtGui.QColor("#3479c8" if is_insert_target else "#245a9e"))
                    else:
                        item.setBackground(QtGui.QColor("#3a6892" if is_insert_target else "#1f3f5f"))
                    item.setForeground(QtGui.QColor("#e2e8f0"))
                else:
                    step_index = col // 2
                    delay_ms = _coerce_delay_ms(steps[step_index].get("delay_ms", _DEFAULT_DELAY_MS))
                    item.setText(f"{delay_ms} ms")
                    item.setBackground(QtGui.QColor("#1f2937"))
                    item.setForeground(QtGui.QColor("#cbd5e1"))
                self._table.setItem(0, col, item)
        finally:
            self._table.blockSignals(False)
        self._restore_selected_action_visuals()
        self._update_insert_button_state()

    def _on_table_cell_clicked(self, row: int, col: int):
        if self._running or row != 0:
            return
        if col % 2 == 0:
            self._edit_action_cell(col // 2)
        else:
            self._edit_delay_cell(col // 2)

    def _restore_selected_action_visuals(self):
        try:
            self._table.clearSelection()
            self._table.viewport().update()
        except Exception:
            pass

    def _select_action_for_insert(self, index: int):
        if not self._steps:
            self._selected_action_for_insert = None
            self._update_insert_button_state()
            return
        self._selected_action_for_insert = max(0, min(len(self._steps) - 1, int(index)))
        self._rebuild_table()
        self._set_status(f"Action {int(self._selected_action_for_insert) + 1} selected. Insert Action will place a new action before it.")

    def _on_table_context_menu(self, pos):
        if self._running:
            return
        index = self._table.indexAt(pos)
        if not index.isValid() or int(index.row()) != 0 or int(index.column()) % 2 != 0:
            return
        selected_index = int(index.column()) // 2
        self._select_action_for_insert(selected_index)

        menu = QtWidgets.QMenu(self._table)
        insert_act = menu.addAction(f"Insert Action Before {selected_index + 1}")
        add_act = menu.addAction("Add Action To End")
        loop_act = menu.addAction("Make Loop")
        menu.addSeparator()
        remove_act = menu.addAction("Remove Action")

        at_max = len(self._steps) >= _MAX_STEPS
        insert_act.setEnabled(not at_max)
        add_act.setEnabled(not at_max)
        loop_act.setEnabled(len(self._steps) <= _MAX_STEPS - 2)
        remove_act.setEnabled(len(self._steps) > _MIN_STEPS)

        global_pos = self._table.viewport().mapToGlobal(pos)
        try:
            chosen = menu.exec(global_pos)
        except Exception:
            chosen = menu.exec_(global_pos)
        if chosen is None:
            return
        if chosen == insert_act:
            self._on_insert_action()
        elif chosen == add_act:
            self._on_add_action()
        elif chosen == loop_act:
            self._on_make_loop()
        elif chosen == remove_act:
            self._on_remove_action()

    def _edit_action_cell(self, index: int):
        if not (0 <= index < len(self._steps)):
            return
        step = dict(self._steps[index] or {})
        initial_type = _normalize_action_type(step.get("type", _ACTION_TYPE_KEY))
        initial_action = str(step.get("action") or "").strip() or _default_action_label(initial_type)
        initial_key = str(step.get("key") or "").strip()
        initial_delay = _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS))
        initial_hold = bool(step.get("hold")) or "hold_ms" in step
        initial_hold_ms = _coerce_action_hold_ms(step.get("hold_ms")) if "hold_ms" in step else None
        initial_key_loop_condition = _key_loop_condition_iteration(step)
        initial_key_loop_condition_enabled = initial_key_loop_condition is not None
        initial_key_loop_condition_operator = _key_loop_condition_operator(step)
        initial_key_loop_condition_iteration = initial_key_loop_condition if initial_key_loop_condition is not None else 1
        initial_click_x = _coerce_screen_coord(_raw_first_value(step, "x", "screen_x", default=0)) if _step_has_click_point(step) else None
        initial_click_y = _coerce_screen_coord(_raw_first_value(step, "y", "screen_y", default=0)) if _step_has_click_point(step) else None
        initial_click_button = _normalize_mouse_button(step.get("button", _MOUSE_BUTTON_LEFT))
        initial_click_ms = _coerce_click_ms(step.get("click_ms", _DEFAULT_CLICK_MS))
        initial_click_screen = step.get("screen") if isinstance(step.get("screen"), dict) else None
        initial_click_window = step.get("window") if isinstance(step.get("window"), dict) else None
        initial_click_coord_mode = _normalize_click_coord_mode(step.get("coord_mode", _CLICK_COORD_WINDOW if initial_click_window else _CLICK_COORD_SCREEN))
        initial_write_text = _raw_text_value(step, "text", "write_text", "type_text", "content")
        initial_text_source = _text_action_source(step) if initial_type == _ACTION_TYPE_TEXT else _TEXT_SOURCE_SINGLE
        initial_loop_text_rows = _normalize_loop_text_rows(step.get("loop_text_rows", step.get("text_rows", None))) if initial_type == _ACTION_TYPE_TEXT else []
        initial_loop_number = _coerce_loop_number(step.get("loop_number", step.get("loop_id", step.get("loop", 1))), 1)
        initial_loop_count = _coerce_loop_count(step.get("loop_count", step.get("iterations", step.get("repeat_count", 2))), 2)
        parent = _dialog_parent(self._node_item) or self
        dialog = _ActionEditDialog(
            initial_action,
            initial_key,
            initial_delay,
            initial_hold,
            initial_hold_ms,
            initial_key_loop_condition_enabled,
            initial_key_loop_condition_operator,
            initial_key_loop_condition_iteration,
            initial_type,
            initial_click_x,
            initial_click_y,
            initial_click_button,
            initial_click_ms,
            initial_click_screen,
            initial_click_window,
            initial_click_coord_mode,
            initial_write_text,
            initial_text_source,
            initial_loop_text_rows,
            initial_loop_number,
            initial_loop_count,
            parent,
        )
        self._position_action_dialog(dialog, index)
        try:
            result = dialog.exec()
        except Exception:
            result = dialog.exec_()
        if result != QtWidgets.QDialog.Accepted:
            return
        previous_loop_number = initial_loop_number if _action_uses_loop_settings(initial_type) else None
        new_step = dict(dialog.values())
        action_text = str(new_step.get("action") or "").strip()
        if not action_text:
            action_text = _default_action_label(new_step.get("type", _ACTION_TYPE_KEY))
        new_step["action"] = action_text
        self._steps[index] = _normalize_step(new_step, index) or new_step
        _sync_loop_marker_settings(self._steps, index, previous_loop_number)
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        self._set_status(f"Updated action {index + 1}.")

    def _position_table_dialog(self, dialog: QtWidgets.QDialog, col: int) -> None:
        try:
            model_index = self._table.model().index(0, max(0, int(col)))
            cell_rect = self._table.visualRect(model_index)
            if not cell_rect.isValid():
                raise RuntimeError("invalid table cell")

            viewport = self._table.viewport()
            top_left = viewport.mapToGlobal(cell_rect.topLeft())
            bottom_left = viewport.mapToGlobal(cell_rect.bottomLeft())
            screen = QtGui.QGuiApplication.screenAt(top_left)
            if screen is None:
                screen = QtGui.QGuiApplication.primaryScreen()
            if screen is None:
                return

            dialog.adjustSize()
            size = dialog.size()
            bounds = screen.availableGeometry()
            margin = 8
            x = top_left.x() + ((cell_rect.width() - size.width()) // 2)
            y = bottom_left.y() + margin
            if y + size.height() > bounds.bottom() - margin:
                y = top_left.y() - size.height() - margin
            max_x = bounds.right() - size.width() + 1 - margin
            max_y = bounds.bottom() - size.height() + 1 - margin
            x = max(bounds.left() + margin, min(x, max_x))
            y = max(bounds.top() + margin, min(y, max_y))
            dialog.move(int(x), int(y))
        except Exception:
            try:
                cursor = QtGui.QCursor.pos()
                dialog.move(cursor + QtCore.QPoint(12, 12))
            except Exception:
                pass

    def _position_action_dialog(self, dialog: QtWidgets.QDialog, index: int) -> None:
        self._position_table_dialog(dialog, max(0, int(index)) * 2)

    def _edit_delay_cell(self, index: int):
        if not (0 <= index < len(self._steps)):
            return
        step = dict(self._steps[index] or {})
        current_delay = _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS))
        parent = _dialog_parent(self._node_item) or self
        dialog = _DelayEditDialog(current_delay, parent)
        self._position_table_dialog(dialog, (max(0, int(index)) * 2) + 1)
        try:
            result = dialog.exec()
        except Exception:
            result = dialog.exec_()
        if result != QtWidgets.QDialog.Accepted:
            return
        step["delay_ms"] = dialog.value()
        self._steps[index] = step
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        self._set_status(f"Delay for action {index + 1} set to {int(step['delay_ms'])} ms.")

    def _collect_runnable_steps(self) -> list[dict[str, object]]:
        runnable: list[dict[str, object]] = []
        for index, step in enumerate(self._steps or []):
            action_type = _normalize_action_type(step.get("type", _ACTION_TYPE_KEY))
            action = str(step.get("action") or "").strip() or _default_action_label(action_type)
            if action_type in {_ACTION_TYPE_CLICK, _ACTION_TYPE_HOVER}:
                if not _step_has_click_point(step):
                    continue
                pointer_step = {
                    "index": index,
                    "action": action,
                    "type": action_type,
                    "x": _coerce_screen_coord(_raw_first_value(step, "x", "screen_x", default=0)),
                    "y": _coerce_screen_coord(_raw_first_value(step, "y", "screen_y", default=0)),
                    "delay_ms": _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS)),
                    "coord_mode": _normalize_click_coord_mode(step.get("coord_mode", _CLICK_COORD_WINDOW if _normalize_window_metadata(step.get("window")) is not None else _CLICK_COORD_SCREEN)),
                }
                if action_type == _ACTION_TYPE_CLICK:
                    pointer_step["button"] = _normalize_mouse_button(step.get("button", _MOUSE_BUTTON_LEFT))
                    pointer_step["click_ms"] = _coerce_click_ms(step.get("click_ms", _DEFAULT_CLICK_MS))
                window_meta = _normalize_window_metadata(step.get("window"))
                if window_meta is not None:
                    pointer_step["window"] = window_meta
                runnable.append(pointer_step)
                continue

            if action_type == _ACTION_TYPE_TEXT:
                text_source = _text_action_source(step)
                text_value = _raw_text_value(step, "text", "write_text", "type_text", "content")
                text_rows = _normalize_loop_text_rows(step.get("loop_text_rows", step.get("text_rows", None)))
                if text_source == _TEXT_SOURCE_LOOP_TABLE:
                    if text_rows:
                        runnable.append(
                            {
                                "index": index,
                                "action": action,
                                "type": _ACTION_TYPE_TEXT,
                                "text": text_value,
                                "text_source": _TEXT_SOURCE_LOOP_TABLE,
                                "loop_text_rows": text_rows,
                                "delay_ms": _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS)),
                            }
                        )
                elif text_value != "":
                    runnable.append(
                        {
                            "index": index,
                            "action": action,
                            "type": _ACTION_TYPE_TEXT,
                            "text": text_value,
                            "delay_ms": _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS)),
                        }
                    )
                continue

            if _action_uses_loop_settings(action_type):
                runnable.append(
                    {
                        "index": index,
                        "action": action,
                        "type": action_type,
                        "loop_number": _coerce_loop_number(step.get("loop_number", 1), 1),
                        "loop_count": _coerce_loop_count(step.get("loop_count", 2), 2),
                        "delay_ms": _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS)),
                    }
                )
                continue

            key_text = str(step.get("key") or "").strip()
            if key_text:
                runnable.append(
                    {
                        "index": index,
                        "action": action,
                        "type": _ACTION_TYPE_KEY,
                        "key": key_text,
                        "delay_ms": _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS)),
                    }
                )
                if bool(step.get("hold")):
                    runnable[-1]["hold"] = True
                if "hold_ms" in step:
                    runnable[-1]["hold_ms"] = _coerce_action_hold_ms(step.get("hold_ms"))
                loop_condition = _key_loop_condition_iteration(step)
                if loop_condition is not None:
                    runnable[-1]["loop_condition_enabled"] = True
                    runnable[-1]["loop_condition_operator"] = _key_loop_condition_operator(step)
                    runnable[-1]["loop_condition_iteration"] = loop_condition
        return runnable

    def _on_run_clicked(self):
        if self._running:
            return
        runnable = self._collect_runnable_steps()
        if not runnable:
            self._set_status("Assign at least one key, text, hover, or captured click before running.")
            return
        self._request_stop()
        self._stop_event = threading.Event()
        self._set_running(True)
        self._set_status("Preparing playback...")

        worker_steps = json.loads(json.dumps(runnable))
        lead_in_ms = int(self._lead_in_ms)
        key_hold_ms = int(self._key_hold_ms)
        injection_mode = str(self._injection_mode or _MODE_VK)
        serial_configs = _connected_serial_target_configs(self._node_item)
        if serial_configs:
            try:
                from nodes.serial_com.spec import prompt_install_pyserial, serial_runtime_available
            except Exception as exc:
                self._set_status(f"Pico HID backend unavailable: {exc}")
                self._set_running(False)
                return
            if not serial_runtime_available():
                ready, message = prompt_install_pyserial(self)
                if not ready:
                    self._set_status(message or "pyserial is required for Pico HID playback.")
                    self._set_running(False)
                    return

        thread = threading.Thread(
            target=self._playback_worker,
            args=(worker_steps, lead_in_ms, key_hold_ms, injection_mode, serial_configs),
            daemon=True,
        )
        self._run_thread = thread
        thread.start()

    def _on_stop_clicked(self):
        self._request_stop()
        if self._running:
            self._set_status("Stop requested.")

    def _request_stop(self):
        try:
            self._stop_event.set()
        except Exception:
            pass

    def _sleep_with_cancel(self, delay_ms: int) -> bool:
        total_seconds = max(0.0, float(delay_ms) / 1000.0)
        end = time.monotonic() + total_seconds
        while True:
            if self._stop_event.is_set():
                return False
            now = time.monotonic()
            if now >= end:
                return True
            wait_time = min(0.05, max(0.0, end - now))
            if self._stop_event.wait(wait_time):
                return False

    def _playback_worker(
        self,
        steps: list[dict[str, object]],
        lead_in_ms: int,
        key_hold_ms: int,
        injection_mode: str,
        serial_configs,
    ):
        use_serial = bool(serial_configs)
        if (not use_serial) and os.name != "nt":
            self._signals.finished.emit(False, "Keyboard and click playback is currently available on Windows only.")
            return
        mode = _normalize_injection_mode(injection_mode)
        default_hold_ms = _coerce_key_hold_ms(key_hold_ms)
        serial_sessions = []
        if use_serial:
            try:
                from nodes.serial_com.spec import open_pico_sessions
            except Exception as exc:
                self._signals.finished.emit(False, f"Pico HID backend unavailable: {exc}")
                return
            serial_sessions, serial_error = open_pico_sessions(list(serial_configs or []))
            if not serial_sessions:
                self._signals.finished.emit(False, serial_error or "Could not open Pico HID serial session.")
                return
        if lead_in_ms > 0:
            self._signals.status.emit(
                f"Lead-in {int(lead_in_ms)} ms. Switch focus to the target app now."
            )
            if not self._sleep_with_cancel(int(lead_in_ms)):
                if serial_sessions:
                    try:
                        from nodes.serial_com.spec import close_pico_sessions
                        close_pico_sessions(serial_sessions, release_all=True)
                    except Exception:
                        pass
                self._signals.finished.emit(False, "Playback stopped before start.")
                return

        try:
            action_count = len(steps)
            start_to_end, end_to_start = _loop_pair_maps(steps)
            loop_remaining: dict[int, int] = {}
            loop_iteration: dict[int, int] = {}
            pc = 0
            while pc < action_count:
                if self._stop_event.is_set():
                    self._signals.finished.emit(False, "Playback stopped.")
                    return
                idx = pc
                step = steps[idx]
                action_type = _normalize_action_type(step.get("type", _ACTION_TYPE_KEY))
                action_name = str(step.get("action") or "").strip() or _default_action_label(action_type)
                if action_type == _ACTION_TYPE_LOOP_START:
                    loop_number = _coerce_loop_number(step.get("loop_number", 1), 1)
                    loop_count = _coerce_loop_count(step.get("loop_count", 2), 2)
                    delay_ms = _coerce_delay_ms(step.get("delay_ms", 0))
                    if idx not in start_to_end:
                        self._signals.status.emit(f"Loop {loop_number} start has no matching end; continuing.")
                    else:
                        if idx not in loop_remaining:
                            loop_remaining[idx] = loop_count
                            loop_iteration[idx] = 1
                        self._signals.status.emit(
                            f"Loop {loop_number} start: iteration {loop_iteration.get(idx, 1)}/{loop_count}"
                        )
                    if delay_ms > 0 and not self._sleep_with_cancel(delay_ms):
                        self._signals.finished.emit(False, "Playback stopped.")
                        return
                    pc += 1
                    continue

                if action_type == _ACTION_TYPE_LOOP_END:
                    loop_number = _coerce_loop_number(step.get("loop_number", 1), 1)
                    delay_ms = _coerce_delay_ms(step.get("delay_ms", 0))
                    start_idx = end_to_start.get(idx)
                    if start_idx is None:
                        self._signals.status.emit(f"Loop {loop_number} end has no matching start; continuing.")
                        if delay_ms > 0 and not self._sleep_with_cancel(delay_ms):
                            self._signals.finished.emit(False, "Playback stopped.")
                            return
                        pc += 1
                        continue
                    remaining = int(loop_remaining.get(start_idx, 1)) - 1
                    loop_count = _coerce_loop_count(steps[start_idx].get("loop_count", step.get("loop_count", 2)), 2)
                    if delay_ms > 0 and not self._sleep_with_cancel(delay_ms):
                        self._signals.finished.emit(False, "Playback stopped.")
                        return
                    if remaining > 0:
                        loop_remaining[start_idx] = remaining
                        loop_iteration[start_idx] = int(loop_iteration.get(start_idx, 1)) + 1
                        self._signals.status.emit(
                            f"Loop {loop_number} repeat: iteration {loop_iteration[start_idx]}/{loop_count}"
                        )
                        pc = start_idx + 1
                        continue
                    loop_remaining.pop(start_idx, None)
                    loop_iteration.pop(start_idx, None)
                    self._signals.status.emit(f"Loop {loop_number} complete.")
                    pc += 1
                    continue

                if action_type == _ACTION_TYPE_CLICK:
                    x, y, coordinate_mode = _resolve_click_point(step)
                    button = _normalize_mouse_button(step.get("button", _MOUSE_BUTTON_LEFT))
                    click_ms = _coerce_click_ms(step.get("click_ms", _DEFAULT_CLICK_MS))
                    delay_ms = _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS))
                    self._signals.status.emit(
                        f"Clicking {idx + 1}/{action_count}: {action_name} [{button} @ {x},{y}, {coordinate_mode}]"
                    )
                    used_relative_fallback = False
                    if serial_sessions:
                        ok, message, used_relative_fallback = _position_pointer_target(serial_sessions, x, y)
                        if ok:
                            for session in serial_sessions:
                                ok, message = session.mouse_click(button, click_ms)
                                if not ok:
                                    break
                            if ok:
                                time.sleep(0.04)
                    else:
                        ok, message = _dispatch_click_action(x, y, button=button, click_ms=click_ms)
                    if not ok:
                        self._signals.finished.emit(
                            False,
                            f"Action {idx + 1} '{action_name}' failed: {message}",
                        )
                        return
                    click_mode = _serial_route_label(serial_configs) if serial_sessions else "Windows mouse"
                    if used_relative_fallback:
                        click_mode = f"{click_mode}, feedback cursor fallback"
                    self._signals.status.emit(
                        f"Clicked {idx + 1}/{action_count}: {action_name} [{button} @ {x},{y}, {coordinate_mode}] ({click_mode}, hold={click_ms}ms)"
                    )
                    if idx < action_count - 1:
                        if delay_ms > 0 and not self._sleep_with_cancel(delay_ms):
                            self._signals.finished.emit(False, "Playback stopped.")
                            return
                    pc += 1
                    continue

                if action_type == _ACTION_TYPE_HOVER:
                    x, y, coordinate_mode = _resolve_click_point(step)
                    delay_ms = _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS))
                    self._signals.status.emit(
                        f"Hovering {idx + 1}/{action_count}: {action_name} [@ {x},{y}, {coordinate_mode}]"
                    )
                    ok, message, used_relative_fallback = _position_pointer_target(serial_sessions, x, y)
                    if not ok:
                        self._signals.finished.emit(
                            False,
                            f"Action {idx + 1} '{action_name}' failed: {message}",
                        )
                        return
                    hover_mode = _serial_route_label(serial_configs) if serial_sessions else "Windows mouse"
                    if used_relative_fallback:
                        hover_mode = f"{hover_mode}, feedback cursor fallback"
                    self._signals.status.emit(
                        f"Hovered {idx + 1}/{action_count}: {action_name} [@ {x},{y}, {coordinate_mode}] ({hover_mode})"
                    )
                    if idx < action_count - 1:
                        if delay_ms > 0 and not self._sleep_with_cancel(delay_ms):
                            self._signals.finished.emit(False, "Playback stopped.")
                            return
                    pc += 1
                    continue

                if action_type == _ACTION_TYPE_TEXT:
                    ok, text_value, source_label = _resolve_text_action_value(step, idx, steps, start_to_end, loop_iteration)
                    if not ok:
                        self._signals.finished.emit(
                            False,
                            f"Action {idx + 1} '{action_name}' failed: {source_label}",
                        )
                        return
                    preview = _text_preview(text_value)
                    delay_ms = _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS))
                    hold_ms = _text_key_hold_ms(default_hold_ms)
                    source_suffix = f" ({source_label})" if source_label else ""
                    self._signals.status.emit(
                        f"Writing {idx + 1}/{action_count}: {action_name} [\"{preview}\"]{source_suffix}"
                    )
                    if serial_sessions:
                        ok, message = _send_serial_text_action(serial_sessions, text_value, hold_ms, self._stop_event)
                    else:
                        ok, message = _dispatch_text_action(text_value, injection_mode=mode, key_hold_ms=hold_ms)
                    if not ok:
                        self._signals.finished.emit(
                            False,
                            f"Action {idx + 1} '{action_name}' failed: {message}",
                        )
                        return
                    if serial_sessions:
                        mode_label = _serial_route_label(serial_configs)
                    elif mode == _MODE_SCANCODE:
                        mode_label = "ScanCode"
                    elif mode == _MODE_HYBRID:
                        mode_label = "Hybrid"
                    else:
                        mode_label = "Standard"
                    self._signals.status.emit(
                        f"Wrote {idx + 1}/{action_count}: {action_name} [\"{preview}\"] ({mode_label}){source_suffix}"
                    )
                    if idx < action_count - 1:
                        post_text_delay_ms = max(delay_ms, _text_post_write_delay_ms(text_value))
                        if post_text_delay_ms > 0 and not self._sleep_with_cancel(post_text_delay_ms):
                            self._signals.finished.emit(False, "Playback stopped.")
                            return
                    pc += 1
                    continue

                key_text = str(step.get("key") or "").strip()
                has_explicit_hold = bool(step.get("hold")) or "hold_ms" in step
                delay_ms = _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS))
                uses_separate_hold_duration = "hold_ms" in step
                hold_ms = (
                    _coerce_action_hold_ms(step.get("hold_ms"))
                    if uses_separate_hold_duration
                    else delay_ms
                    if has_explicit_hold
                    else default_hold_ms
                )
                post_action_delay_ms = delay_ms if (not has_explicit_hold or uses_separate_hold_duration) else 0
                condition_allows, condition_label = _key_loop_condition_allows(step, idx, steps, start_to_end, loop_iteration)
                if not condition_allows:
                    self._signals.status.emit(
                        f"Skipped {idx + 1}/{action_count}: {action_name} [{key_text}] ({condition_label})"
                    )
                    pc += 1
                    continue
                if has_explicit_hold and hold_ms > 0:
                    self._signals.status.emit(
                        f"Holding {idx + 1}/{action_count}: {action_name} [{key_text}] for {hold_ms}ms"
                    )
                if serial_sessions:
                    ok = True
                    message = ""
                    if has_explicit_hold and hold_ms > 0:
                        for session in serial_sessions:
                            ok, message = session.key_down(key_text)
                            if not ok:
                                break
                        if ok and not self._sleep_with_cancel(hold_ms):
                            for session in serial_sessions:
                                try:
                                    session.release_all()
                                except Exception:
                                    pass
                            self._signals.finished.emit(False, "Playback stopped.")
                            return
                        if ok:
                            for session in serial_sessions:
                                ok, message = session.release_all()
                                if not ok:
                                    break
                    else:
                        for session in serial_sessions:
                            ok, message = session.tap(key_text, hold_ms)
                            if not ok:
                                break
                else:
                    ok, message = _dispatch_key_action(key_text, injection_mode=mode, key_hold_ms=hold_ms)
                if not ok:
                    self._signals.finished.emit(
                        False,
                        f"Action {idx + 1} '{action_name}' failed: {message}",
                    )
                    return
                if serial_sessions:
                    mode_label = _serial_route_label(serial_configs)
                elif mode == _MODE_SCANCODE:
                    mode_label = "ScanCode"
                elif mode == _MODE_HYBRID:
                    mode_label = "Hybrid"
                else:
                    mode_label = "Standard"
                self._signals.status.emit(
                    f"Sent {idx + 1}/{action_count}: {action_name} [{key_text}] ({mode_label}, hold={hold_ms}ms)"
                )
                if idx < action_count - 1:
                    if post_action_delay_ms > 0 and not self._sleep_with_cancel(post_action_delay_ms):
                        self._signals.finished.emit(False, "Playback stopped.")
                        return
                pc += 1

            self._signals.finished.emit(True, f"Completed {action_count} actions.")
        finally:
            if serial_sessions:
                try:
                    from nodes.serial_com.spec import close_pico_sessions
                    close_pico_sessions(serial_sessions, release_all=True)
                except Exception:
                    pass

    def _on_worker_status(self, text: str):
        self._set_status(text)

    def _on_worker_finished(self, success: bool, message: str):
        self._set_running(False)
        self._run_thread = None
        if success:
            self._set_status(message)
        else:
            self._set_status(message or "Playback stopped.")


def build_ports(node_item) -> None:
    _ensure_param(node_item, _SEQUENCE_PARAM, json.dumps(_default_sequence(), separators=(",", ":")))
    _ensure_param(node_item, _LEAD_IN_PARAM, str(_DEFAULT_LEAD_IN_MS))
    _ensure_param(node_item, _KEY_HOLD_PARAM, str(_DEFAULT_KEY_HOLD_MS))
    _ensure_param(node_item, _INJECTION_MODE_PARAM, _MODE_VK)
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [_SEQUENCE_PARAM, _LEAD_IN_PARAM, _KEY_HOLD_PARAM, _INJECTION_MODE_PARAM],
    )


def render_node_body(node_item, y_cursor: int) -> int:
    body = KeyboardSequenceWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    min_h = max(
        int(body.sizeHint().height()),
        int(body.minimumSizeHint().height()),
        int(body.minimumHeight() or 0),
    )
    try:
        available_h = int(
            max(
                float(min_h),
                float(node_item.height) - float(y_cursor) - float(getattr(node_item, "_PADDING", 0.0)),
            )
        )
    except Exception:
        available_h = int(min_h)
    proxy.resize(node_item.width, available_h)
    try:
        proxy.setPreferredSize(node_item.width, available_h)
    except Exception:
        pass
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + available_h


KEYBOARD_SEQUENCE_SPEC = Spec(
    stripe_color="#0ea5e9",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
