from __future__ import annotations

import ctypes
import json
import os
import re
import threading
import time

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
_MIN_DELAY_MS = 0
_MAX_DELAY_MS = 600000
_MAX_LEAD_IN_MS = 60000
_MAX_KEY_HOLD_MS = 2000
_MIN_STEPS = 1
_MAX_STEPS = 64

KEYBOARD_SEQUENCE_BODY_W = 980
KEYBOARD_SEQUENCE_BODY_H = 280

_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_SCANCODE = 0x0008

_MAPVK_VK_TO_VSC = 0
_MAPVK_VK_TO_VSC_EX = 4

_MODE_VK = "vk"
_MODE_SCANCODE = "scancode"
_MODE_HYBRID = "hybrid"

_INTER_EVENT_MS = 2

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
    "minus": 0xBD,
    "comma": 0xBC,
    "period": 0xBE,
    "dot": 0xBE,
    "slash": 0xBF,
    "backslash": 0xDC,
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


def _normalize_injection_mode(raw) -> str:
    token = _normalize_token(str(raw or ""))
    if token in {"hybrid", "both", "combo"}:
        return _MODE_HYBRID
    if token in {"scancode", "scan", "scanmode", "game"}:
        return _MODE_SCANCODE
    return _MODE_VK


def _default_sequence() -> list[dict[str, object]]:
    return [{"action": "Action 1", "key": "", "delay_ms": _DEFAULT_DELAY_MS}]


def _normalize_step(raw, index: int) -> dict[str, object] | None:
    idx = max(0, int(index))
    default_name = f"Action {idx + 1}"
    if isinstance(raw, dict):
        action = str(raw.get("action", default_name) or "").strip() or default_name
        key = str(raw.get("key", raw.get("hotkey", "")) or "").strip()
        delay_ms = _coerce_delay_ms(raw.get("delay_ms", raw.get("delay", _DEFAULT_DELAY_MS)))
        return {"action": action, "key": key, "delay_ms": delay_ms}
    if isinstance(raw, str):
        action = default_name
        key = raw.strip()
        return {"action": action, "key": key, "delay_ms": _DEFAULT_DELAY_MS}
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
            steps.append(_normalize_step({}, len(steps)) or {"action": "Action", "key": "", "delay_ms": _DEFAULT_DELAY_MS})
    return steps


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


def _vk_for_named_token(token: str) -> int | None:
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
    hold_ms = _coerce_key_hold_ms(key_hold_ms)
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
    hold_ms = _coerce_key_hold_ms(key_hold_ms)
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
    if _coerce_key_hold_ms(key_hold_ms) > 0:
        return _send_input_event_stream(events, key_hold_ms=key_hold_ms)
    return _send_input_events(events)


class _ActionEditDialog(QtWidgets.QDialog):
    def __init__(self, action_text: str, key_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Keyboard Action")
        self.setModal(True)
        self.resize(420, 170)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._action_edit = QtWidgets.QLineEdit(str(action_text or ""))
        self._action_edit.setPlaceholderText("Action label (for display only)")
        form.addRow("Action", self._action_edit)

        self._key_edit = QtWidgets.QLineEdit(str(key_text or ""))
        self._key_edit.setPlaceholderText("Example: ctrl+shift+s, F5, enter, a")
        form.addRow("Key", self._key_edit)
        layout.addLayout(form, 0)

        hint = QtWidgets.QLabel(
            "Use one key per action. Modifiers are supported with '+'.\n"
            "If no special key is provided, plain text will be typed."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        layout.addWidget(hint, 0)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 0)

        QtCore.QTimer.singleShot(0, self._key_edit.setFocus)

    def values(self) -> tuple[str, str]:
        action = str(self._action_edit.text() or "").strip()
        key_text = str(self._key_edit.text() or "").strip()
        return action, key_text


class _PlaybackSignals(QtCore.QObject):
    status = QtCore.Signal(str)
    finished = QtCore.Signal(bool, str)


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
        self._add_btn.clicked.connect(self._on_add_action)
        button_row.addWidget(self._add_btn, 0)

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
        self._run_btn.clicked.connect(self._on_run_clicked)
        button_row.addWidget(self._run_btn, 0)

        self._stop_btn = QtWidgets.QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.setEnabled(False)
        button_row.addWidget(self._stop_btn, 0)

        root.addLayout(button_row, 0)

        self._table = QtWidgets.QTableWidget(1, 1, self)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.cellClicked.connect(self._on_table_cell_clicked)
        root.addWidget(self._table, 1)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        self._status.setWordWrap(True)
        root.addWidget(self._status, 0)

        self._rebuild_table()
        self._set_status("Click action cells to set keys. Click delay cells to set timing.")
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
        return QtCore.QSize(700, 220)

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
        self._status.setText(str(text or ""))

    def _set_running(self, running: bool):
        self._running = bool(running)
        self._run_btn.setEnabled(not self._running)
        self._stop_btn.setEnabled(self._running)
        self._add_btn.setEnabled(not self._running)
        self._remove_btn.setEnabled(not self._running)
        self._lead_in_spin.setEnabled(not self._running)
        self._key_hold_spin.setEnabled(not self._running)
        self._mode_combo.setEnabled(not self._running)
        self._table.setEnabled(not self._running)

    def _persist_steps(self, notify_scene: bool = True):
        self._steps = _write_sequence(self._node_item, self._steps, notify_scene=notify_scene)

    def _selected_action_index(self) -> int:
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
        next_index = len(self._steps)
        self._steps.append(
            {
                "action": f"Action {next_index + 1}",
                "key": "",
                "delay_ms": _DEFAULT_DELAY_MS,
            }
        )
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        self._set_status(f"Added action {next_index + 1}.")

    def _on_remove_action(self):
        if self._running:
            return
        if len(self._steps) <= _MIN_STEPS:
            self._set_status("At least one action row must remain.")
            return
        target = self._selected_action_index()
        removed = self._steps.pop(target)
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        self._set_status(f"Removed '{str(removed.get('action') or '').strip() or 'action'}'.")

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

    def _rebuild_table(self):
        steps = list(self._steps or [])
        if not steps:
            steps = _default_sequence()
            self._steps = steps

        col_count = max(1, (len(steps) * 2) - 1)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(1)
            self._table.setColumnCount(col_count)
            headers: list[str] = []
            for col in range(col_count):
                if col % 2 == 0:
                    headers.append(f"Action {(col // 2) + 1}")
                    self._table.setColumnWidth(col, 190)
                else:
                    headers.append("Delay")
                    self._table.setColumnWidth(col, 102)
            self._table.setHorizontalHeaderLabels(headers)
            self._table.setRowHeight(0, 78)

            for col in range(col_count):
                item = QtWidgets.QTableWidgetItem()
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                if col % 2 == 0:
                    step_index = col // 2
                    step = steps[step_index]
                    label = str(step.get("action") or "").strip() or f"Action {step_index + 1}"
                    key_text = str(step.get("key") or "").strip()
                    body = key_text if key_text else "Click to assign key"
                    item.setText(f"{label}\n{body}")
                    item.setBackground(QtGui.QColor("#162433"))
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

    def _on_table_cell_clicked(self, row: int, col: int):
        if self._running or row != 0:
            return
        if col % 2 == 0:
            self._edit_action_cell(col // 2)
        else:
            self._edit_delay_cell(col // 2)

    def _edit_action_cell(self, index: int):
        if not (0 <= index < len(self._steps)):
            return
        step = dict(self._steps[index] or {})
        initial_action = str(step.get("action") or "").strip() or f"Action {index + 1}"
        initial_key = str(step.get("key") or "").strip()
        dialog = _ActionEditDialog(initial_action, initial_key, self)
        try:
            result = dialog.exec()
        except Exception:
            result = dialog.exec_()
        if result != QtWidgets.QDialog.Accepted:
            return
        action_text, key_text = dialog.values()
        if not action_text:
            action_text = f"Action {index + 1}"
        new_step = dict(step)
        new_step["action"] = action_text
        new_step["key"] = key_text
        new_step["delay_ms"] = _coerce_delay_ms(new_step.get("delay_ms", _DEFAULT_DELAY_MS))
        self._steps[index] = new_step
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        self._set_status(f"Updated action {index + 1}.")

    def _edit_delay_cell(self, index: int):
        if not (0 <= index < len(self._steps)):
            return
        step = dict(self._steps[index] or {})
        current_delay = _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS))
        value, ok = QtWidgets.QInputDialog.getInt(
            self,
            "Set Delay",
            "Delay after this action (ms):",
            int(current_delay),
            int(_MIN_DELAY_MS),
            int(_MAX_DELAY_MS),
            50,
        )
        if not ok:
            return
        step["delay_ms"] = _coerce_delay_ms(value)
        self._steps[index] = step
        self._persist_steps(notify_scene=True)
        self._rebuild_table()
        self._set_status(f"Delay for action {index + 1} set to {int(step['delay_ms'])} ms.")

    def _collect_runnable_steps(self) -> list[dict[str, object]]:
        runnable: list[dict[str, object]] = []
        for index, step in enumerate(self._steps or []):
            action = str(step.get("action") or "").strip() or f"Action {index + 1}"
            key_text = str(step.get("key") or "").strip()
            if not key_text:
                continue
            runnable.append(
                {
                    "index": index,
                    "action": action,
                    "key": key_text,
                    "delay_ms": _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS)),
                }
            )
        return runnable

    def _on_run_clicked(self):
        if self._running:
            return
        runnable = self._collect_runnable_steps()
        if not runnable:
            self._set_status("Assign at least one action key before running.")
            return
        self._request_stop()
        self._stop_event = threading.Event()
        self._set_running(True)
        self._set_status("Preparing playback...")

        worker_steps = json.loads(json.dumps(runnable))
        lead_in_ms = int(self._lead_in_ms)
        key_hold_ms = int(self._key_hold_ms)
        injection_mode = str(self._injection_mode or _MODE_VK)

        thread = threading.Thread(
            target=self._playback_worker,
            args=(worker_steps, lead_in_ms, key_hold_ms, injection_mode),
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
    ):
        if os.name != "nt":
            self._signals.finished.emit(False, "Keyboard playback is currently available on Windows only.")
            return
        mode = _normalize_injection_mode(injection_mode)
        hold_ms = _coerce_key_hold_ms(key_hold_ms)
        if lead_in_ms > 0:
            self._signals.status.emit(
                f"Lead-in {int(lead_in_ms)} ms. Switch focus to the target app now."
            )
            if not self._sleep_with_cancel(int(lead_in_ms)):
                self._signals.finished.emit(False, "Playback stopped before start.")
                return

        action_count = len(steps)
        for idx, step in enumerate(steps):
            if self._stop_event.is_set():
                self._signals.finished.emit(False, "Playback stopped.")
                return
            action_name = str(step.get("action") or "").strip() or f"Action {idx + 1}"
            key_text = str(step.get("key") or "").strip()
            ok, message = _dispatch_key_action(key_text, injection_mode=mode, key_hold_ms=hold_ms)
            if not ok:
                self._signals.finished.emit(
                    False,
                    f"Action {idx + 1} '{action_name}' failed: {message}",
                )
                return
            if mode == _MODE_SCANCODE:
                mode_label = "ScanCode"
            elif mode == _MODE_HYBRID:
                mode_label = "Hybrid"
            else:
                mode_label = "Standard"
            self._signals.status.emit(
                f"Sent {idx + 1}/{action_count}: {action_name} [{key_text}] ({mode_label}, hold={hold_ms}ms)"
            )
            if idx < action_count - 1:
                delay_ms = _coerce_delay_ms(step.get("delay_ms", _DEFAULT_DELAY_MS))
                if delay_ms > 0 and not self._sleep_with_cancel(delay_ms):
                    self._signals.finished.emit(False, "Playback stopped.")
                    return

        self._signals.finished.emit(True, f"Completed {action_count} keyboard actions.")

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
