from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urljoin
from urllib.request import Request, urlopen

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec


DEFAULT_API_BASE = "http://127.0.0.1:8765"
MASKED_ADDRESS_TEXT = "******"
CONNECTION_RECOVERY_HINT = "Refresh the Qubit Deck app to reestablish the connection, then try again."
CONNECTION_USER_MESSAGE = f"Qubit Deck app API is not reachable. {CONNECTION_RECOVERY_HINT}"
QUBIT_DECK_CONTROLLER_KINDS = {
    "qubit_deck_controller",
    "qubit deck controller",
    "qubitdeckcontroller",
    "qubitdeck controller",
}
MEDIGATOR_NODE_KINDS = {
    "medigator_agent",
    "mediator_agent",
    "medigator agent",
    "mediator agent",
    "medigator",
    "mediator",
}
MEDIGATOR_OUTPUT_TOKEN_PARAM = "__medigator_output_token"
MEDIATOR_INPUT_PORT = "mediator_input"
LEGACY_MEDIATOR_INPUT_PORTS = ("medigator_input",)

_PARAM_DEFAULTS = {
    "api_base": DEFAULT_API_BASE,
    "button": "",
    "button_name": "",
    "button_slot": "",
    "action": "invoke",
    MEDIATOR_INPUT_PORT: "",
}
_HIDDEN_PARAM = "__ui_hidden_params"
_MODE_PARAM = "__qdeck_mode"
_MODE_CONTEXT = "context"
_MODE_EXECUTOR = "executor"
_LAST_BUTTON_LABEL_PARAM = "__qdeck_last_button_label"
_LAST_THUMBNAIL_SOURCE_PARAM = "__qdeck_last_thumbnail_source"
_QDECK_HIDDEN_PARAMS = (
    "button",
    _MODE_PARAM,
    _LAST_BUTTON_LABEL_PARAM,
    _LAST_THUMBNAIL_SOURCE_PARAM,
)
_THUMBNAIL_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".ico"}
_THUMBNAIL_ICON_EXTS = {".exe", ".lnk", ".ico", ".bat", ".cmd", ".com"}
_THUMBNAIL_PATH_KEYS = (
    "thumbnail",
    "thumbnailPath",
    "thumbnail_path",
    "image",
    "imagePath",
    "image_path",
    "icon",
    "iconPath",
    "icon_path",
    "shortcut",
    "shortcutPath",
    "shortcut_path",
    "exe",
    "exePath",
    "exe_path",
    "executable",
    "executablePath",
    "executable_path",
    "target",
    "targetPath",
    "target_path",
    "path",
    "commandPath",
    "command_path",
    "command",
)
_THUMBNAIL_NESTED_KEYS = ("action", "app", "button", "config", "payload", "settings")
_THUMBNAIL_URL_KEYS = (
    "thumbnailUrl",
    "thumbnail_url",
    "iconUrl",
    "icon_url",
    "imageUrl",
    "image_url",
    "url",
    "href",
)


def _api_base_mask_variants(*api_bases: str) -> list[str]:
    seen: set[str] = set()
    variants: list[str] = []
    for raw in api_bases:
        text = str(raw or "").strip()
        if not text:
            continue
        candidates = [text]
        trimmed = text.rstrip("/")
        if trimmed and trimmed != text:
            candidates.append(trimmed)
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                variants.append(candidate)
    variants.sort(key=len, reverse=True)
    return variants


def _mask_api_base_text(text: str, *api_bases: str) -> str:
    out = str(text or "")
    for api_base in _api_base_mask_variants(*api_bases):
        out = out.replace(api_base, MASKED_ADDRESS_TEXT)
    return out


def _connection_failure_message(reason: object = "") -> str:
    detail = str(reason or "").strip()
    if detail:
        return f"{CONNECTION_USER_MESSAGE} Detail: {detail}"
    return CONNECTION_USER_MESSAGE


def _action_error_message(exc: Exception) -> str:
    text = str(exc or "").strip()
    if CONNECTION_USER_MESSAGE in text:
        return text
    if text.lower().startswith("connection failed:"):
        return _connection_failure_message(text)
    return f"Action failed: {text}"


def _line_edit_echo_mode(mode_name: str):
    mode = getattr(QtWidgets.QLineEdit, mode_name, None)
    if mode is not None:
        return mode
    echo_mode = getattr(QtWidgets.QLineEdit, "EchoMode", None)
    if echo_mode is not None:
        return getattr(echo_mode, mode_name, None)
    return None


class _RevealOnFocusLineEditFilter(QtCore.QObject):
    def __init__(self, edit: QtWidgets.QLineEdit):
        super().__init__(edit)
        self._edit = edit

    def eventFilter(self, obj, ev):
        if obj is self._edit:
            event_type = ev.type()
            if event_type == QtCore.QEvent.FocusIn:
                _set_line_edit_masked(self._edit, False)
            elif event_type == QtCore.QEvent.FocusOut:
                _set_line_edit_masked(self._edit, True)
        return False


def _set_line_edit_masked(edit: QtWidgets.QLineEdit, masked: bool) -> None:
    mode = _line_edit_echo_mode("Password" if masked else "Normal")
    if mode is not None:
        edit.setEchoMode(mode)


def _mask_line_edit_when_unfocused(edit: QtWidgets.QLineEdit) -> None:
    _set_line_edit_masked(edit, not edit.hasFocus())
    focus_filter = _RevealOnFocusLineEditFilter(edit)
    edit._reveal_on_focus_filter = focus_filter
    edit.installEventFilter(focus_filter)


_ACTION_ALIASES = {
    "run": "invoke",
    "trigger": "invoke",
    "press": "invoke",
    "push": "invoke",
    "invoke_button": "invoke",
    "highlight": "highlight_on",
    "highlighton": "highlight_on",
    "highlighteon": "highlight_on",
    "enable_highlight": "highlight_on",
    "highlightoff": "highlight_off",
    "disable_highlight": "highlight_off",
    "list": "list_buttons",
    "buttons": "list_buttons",
    "refresh": "list_buttons",
    "refresh_buttons": "list_buttons",
    "health": "ping_health",
    "ping": "ping_health",
    "open": "open_debugger",
    "open_ui": "open_debugger",
    "open_qt": "open_debugger",
    "debugger": "open_debugger",
}
_ALLOWED_ACTIONS = {
    "invoke",
    "highlight_on",
    "highlight_off",
    "list_buttons",
    "ping_health",
    "open_debugger",
}


def _kind_key(node) -> str:
    return (getattr(node, "kind", "") or "").strip().lower()


def _kind_of_item(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "kind", "") or "").strip().lower()


def _ensure_input(node_item, name: str) -> None:
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input(name)
    elif hasattr(node_item, "add_input_port"):
        node_item.add_input_port(name)
    elif hasattr(node_item, "add_input"):
        node_item.add_input(name)


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


def _migrate_mediator_input_param(node_item) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    legacy_keys = {name.strip().lower() for name in LEGACY_MEDIATOR_INPUT_PORTS}
    new_key = MEDIATOR_INPUT_PORT.strip().lower()
    legacy_value = ""
    for entry in params:
        key = (entry.get("name") or "").strip().lower()
        if key in legacy_keys:
            legacy_value = str(entry.get("value", "") or "")
            if legacy_value:
                break
    has_new = False
    out = []
    for entry in params:
        key = (entry.get("name") or "").strip().lower()
        if key in legacy_keys:
            continue
        if key == new_key:
            has_new = True
            if not str(entry.get("value", "") or "").strip() and legacy_value:
                entry["value"] = legacy_value
        out.append(entry)
    if not has_new:
        out.append({"name": MEDIATOR_INPUT_PORT, "value": legacy_value})
    model.params = out
    try:
        names = [str(name) for name in getattr(model, "_named_inputs", []) if str(name).strip()]
        model._named_inputs = [
            name for name in names if name.strip().lower() not in legacy_keys
        ]
    except Exception:
        pass


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
    hidden = {
        part.strip().lower()
        for part in str(hidden_entry.get("value", "")).split(",")
        if part.strip()
    }
    for name in names or []:
        token = str(name or "").strip().lower()
        if token:
            hidden.add(token)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _source_text(scene, src_item) -> str:
    model = getattr(src_item, "model", None)
    kind = _kind_of_item(src_item)
    info_text = str(getattr(model, "info", "") or "").strip() if model is not None else ""
    # Mediator publishes command JSON on node info; prefer it over parameter text.
    if kind in MEDIGATOR_NODE_KINDS and info_text:
        return info_text

    try:
        resolved = str(scene.resolve_text_value(src_item) or "").strip()
    except Exception:
        resolved = ""

    if info_text:
        if _looks_like_clipboard_blob(resolved):
            return info_text
        if ("{" in info_text and "}" in info_text and '"action"' in info_text):
            return info_text
    return resolved or info_text


def _text_from_input(card, node_item, port_name: str) -> str:
    scene = getattr(card, "_graph_scene", None)
    if not scene or not node_item:
        return ""
    try:
        in_edges = list(scene._in_edges(node_item))
    except Exception:
        in_edges = []
    if not in_edges:
        return ""

    target = (port_name or "").strip()

    def _edge_matches_name(edge) -> bool:
        for attr in ("dst_port_name", "dst_label", "dst_name"):
            if hasattr(edge, attr):
                try:
                    if str(getattr(edge, attr) or "").strip() == target:
                        return True
                except Exception:
                    pass
        return False

    named_edges = [e for e in in_edges if _edge_matches_name(e)]
    if not named_edges:
        return ""

    try:
        ordered = [e for e in scene._ordered_in_edges(node_item) if e in named_edges]
        if ordered:
            named_edges = ordered
    except Exception:
        pass

    parts: list[str] = []
    for edge in named_edges:
        src = getattr(edge, "src", None)
        if src is None:
            continue
        txt = _source_text(scene, src)
        txt = str(txt or "").strip()
        if txt:
            parts.append(txt)
    return "\n\n".join(parts).strip()


def _text_from_default_input(card, node_item) -> str:
    scene = getattr(card, "_graph_scene", None)
    if not scene or not node_item:
        return ""
    try:
        in_edges = list(scene._in_edges(node_item))
    except Exception:
        in_edges = []
    if not in_edges:
        return ""

    def _edge_is_default(edge) -> bool:
        for attr in ("dst_port_name", "dst_label", "dst_name"):
            if hasattr(edge, attr):
                try:
                    if str(getattr(edge, attr) or "").strip():
                        return False
                except Exception:
                    pass
        return True

    default_edges = [e for e in in_edges if _edge_is_default(e)]
    if not default_edges:
        return ""

    try:
        ordered = [e for e in scene._ordered_in_edges(node_item) if e in default_edges]
        if ordered:
            default_edges = ordered
    except Exception:
        pass

    parts: list[str] = []
    for edge in default_edges:
        src = getattr(edge, "src", None)
        if src is None:
            continue
        txt = _source_text(scene, src)
        txt = str(txt or "").strip()
        if txt:
            parts.append(txt)
    return "\n\n".join(parts).strip()


def _all_input_texts(card, node_item) -> list[str]:
    scene = getattr(card, "_graph_scene", None)
    if not scene or not node_item:
        return []
    try:
        in_edges = list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            in_edges = list(scene._in_edges(node_item))
        except Exception:
            in_edges = []
    out: list[str] = []
    seen: set[str] = set()
    for edge in in_edges:
        src = getattr(edge, "src", None)
        if src is None:
            continue
        txt = _source_text(scene, src)
        text = str(txt or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _parse_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _normalize_action(raw: str, fallback: str = "invoke") -> str:
    candidate = (raw or "").strip().lower().replace(" ", "_")
    if not candidate:
        candidate = fallback
    candidate = _ACTION_ALIASES.get(candidate, candidate)
    if candidate in _ALLOWED_ACTIONS:
        return candidate
    return fallback


def _normalize_mode(raw: str, fallback: str = _MODE_EXECUTOR) -> str:
    text = str(raw or "").strip().lower().replace(" ", "_")
    if text == _MODE_CONTEXT:
        return _MODE_CONTEXT
    if text == _MODE_EXECUTOR:
        return _MODE_EXECUTOR
    return _MODE_CONTEXT if str(fallback or "").strip().lower() == _MODE_CONTEXT else _MODE_EXECUTOR


def _looks_like_clipboard_blob(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    if '"format"' in low and "echographclipboard" in low:
        return True
    if low.startswith("{") and '"nodes"' in low and '"edges"' in low and '"action"' not in low:
        return True
    return False


def _coerce_slot_text(raw: str) -> str:
    slot = _parse_int(raw)
    if slot is None or slot <= 0:
        return ""
    return str(slot)


def _param_value_from_model(model, name: str, default: str = "") -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value", "") or "")
    return str(default or "")


def _source_event_token(src_item) -> str:
    model = getattr(src_item, "model", None)
    if model is None:
        return ""
    if _kind_of_item(src_item) in MEDIGATOR_NODE_KINDS:
        return _param_value_from_model(model, MEDIGATOR_OUTPUT_TOKEN_PARAM, "").strip()
    return ""


def _ordered_in_edges(scene, node_item) -> list:
    if not scene or not node_item:
        return []
    try:
        ordered = list(scene._ordered_in_edges(node_item))
        if ordered:
            return ordered
    except Exception:
        pass
    try:
        return list(scene._in_edges(node_item))
    except Exception:
        return []


def _edge_port_name(edge) -> str:
    for attr in ("dst_port_name", "dst_label", "dst_name"):
        if hasattr(edge, attr):
            try:
                raw = str(getattr(edge, attr) or "").strip()
            except Exception:
                raw = ""
            if raw:
                return raw
    return ""


def _input_texts_from_scene(scene, node_item, *, port_name: str = "", default_only: bool = False) -> list[str]:
    edges = _ordered_in_edges(scene, node_item)
    if not edges:
        return []
    target = str(port_name or "").strip().lower()
    filtered = []
    for edge in edges:
        label = _edge_port_name(edge).strip().lower()
        if default_only:
            if label:
                continue
        elif target:
            if label != target:
                continue
        filtered.append(edge)
    out: list[str] = []
    seen: set[str] = set()
    for edge in filtered:
        src = getattr(edge, "src", None)
        if src is None:
            continue
        text = str(_source_text(scene, src) or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _master_candidates_from_scene(scene, node_item) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        value = str(text or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        out.append(value)

    for port_name in (MEDIATOR_INPUT_PORT, *LEGACY_MEDIATOR_INPUT_PORTS):
        for text in _input_texts_from_scene(scene, node_item, port_name=port_name):
            _add(text)
    for text in _input_texts_from_scene(scene, node_item, default_only=True):
        _add(text)
    for text in _input_texts_from_scene(scene, node_item):
        _add(text)

    model = getattr(node_item, "model", None)
    if model is not None:
        _add(_param_value_from_model(model, MEDIATOR_INPUT_PORT))
        for port_name in LEGACY_MEDIATOR_INPUT_PORTS:
            _add(_param_value_from_model(model, port_name))
    return out


def _parse_master_command_text(raw_text: str) -> tuple[dict[str, str], str]:
    raw = str(raw_text or "").strip()
    if not raw:
        return {}, ""

    candidate = raw
    if candidate.startswith("```"):
        lines = []
        for line in candidate.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                continue
            lines.append(line)
        candidate = "\n".join(lines).strip()

    if "{" not in candidate or "}" not in candidate:
        # Master wire may carry plain text; ignore unless it looks like JSON.
        return {}, ""

    parsed_obj: dict | None = None
    parse_errors: list[str] = []
    for payload in (candidate, candidate[candidate.find("{") : candidate.rfind("}") + 1]):
        payload = str(payload or "").strip()
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except Exception as exc:
            parse_errors.append(str(exc))
            continue
        if isinstance(parsed, dict):
            parsed_obj = parsed
            break
        return {}, "Master command JSON must be an object."

    if parsed_obj is None:
        detail = parse_errors[-1] if parse_errors else "unknown parse error"
        return {}, f"Master command is not valid JSON ({detail})."

    out: dict[str, str] = {}

    action_raw = str(parsed_obj.get("action", "") or "").strip()
    if action_raw:
        out["action"] = _normalize_action(action_raw, "invoke")

    button_name = str(parsed_obj.get("button_name", "") or parsed_obj.get("button", "") or "").strip()
    if button_name:
        out["button_name"] = button_name

    slot_text = str(parsed_obj.get("button_slot", "") or "").strip()
    if slot_text:
        slot = _parse_int(slot_text)
        if slot is None or slot <= 0:
            return {}, f"Invalid master button_slot '{slot_text}'. Use a 1-based slot number."
        out["button_slot"] = str(slot)

    api_base = str(parsed_obj.get("api_base", "") or "").strip()
    if api_base:
        out["api_base"] = api_base

    return out, ""


def _parse_master_from_candidates(candidates: list[str]) -> tuple[dict[str, str], str]:
    first_err = ""
    for text in candidates or []:
        if _looks_like_clipboard_blob(text):
            continue
        cmd, err = _parse_master_command_text(text)
        if cmd:
            return cmd, ""
        if err and not first_err:
            first_err = err
    return {}, first_err


def _connected_source_names(scene, node_item) -> set[str]:
    out: set[str] = set()
    for edge in _ordered_in_edges(scene, node_item):
        src = getattr(edge, "src", None)
        if src is None:
            continue
        name = str(getattr(getattr(src, "model", None), "name", "") or "").strip().lower()
        if name:
            out.add(name)
    return out


def _master_source_event_key(scene, node_item) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for edge in _ordered_in_edges(scene, node_item):
        src = getattr(edge, "src", None)
        if src is None:
            continue
        token = _source_event_token(src)
        if not token:
            continue
        name = str(getattr(getattr(src, "model", None), "name", "") or "").strip().lower()
        part = f"{name}:{token}" if name else token
        if part in seen:
            continue
        seen.add(part)
        parts.append(part)
    return "|".join(sorted(parts))


def _set_param_in_list(params: list[dict], name: str, value: str) -> list[dict]:
    key = (name or "").strip().lower()
    text = str(value or "")
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            entry["value"] = text
            return params
    params.append({"name": name, "value": text})
    return params


def _command_signature(mode: str, command: dict[str, str], event_key: str = "") -> str:
    try:
        body = json.dumps(command or {}, sort_keys=True, separators=(",", ":"))
    except Exception:
        body = str(command or "")
    payload = f"{str(mode or '').strip().lower()}\n{body}\n{str(event_key or '').strip()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _execute_command_on_node_item(node_item, command: dict[str, str]) -> tuple[bool, str]:
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    model = getattr(node_item, "model", None)
    if model is None:
        return False, "Node model unavailable."

    api_base = (
        str(command.get("api_base") or _param_value_from_model(model, "api_base", DEFAULT_API_BASE)).strip()
        or DEFAULT_API_BASE
    )
    if not (api_base.lower().startswith("http://") or api_base.lower().startswith("https://")):
        api_base = DEFAULT_API_BASE

    button_name = str(
        command.get("button_name")
        or _param_value_from_model(model, "button_name")
        or _param_value_from_model(model, "button")
    ).strip()
    if _looks_like_clipboard_blob(button_name):
        button_name = ""
    button_slot = _coerce_slot_text(command.get("button_slot") or _param_value_from_model(model, "button_slot"))
    action = _normalize_action(command.get("action") or _param_value_from_model(model, "action"), "invoke")

    params = list(getattr(model, "params", None) or [])
    _set_param_in_list(params, "api_base", api_base)
    _set_param_in_list(params, "button_name", button_name)
    _set_param_in_list(params, "button", button_name)
    _set_param_in_list(params, "button_slot", button_slot)
    _set_param_in_list(params, "action", action)
    _set_param_in_list(params, _MODE_PARAM, _normalize_mode(_param_value_from_model(model, _MODE_PARAM), _MODE_EXECUTOR))
    model.params = params
    _ensure_hidden_params(model, _QDECK_HIDDEN_PARAMS)
    params = list(getattr(model, "params", None) or [])
    if scene is not None:
        try:
            scene.set_node_params(model.name, params)
        except Exception:
            pass

    try:
        node_item.setBusyState(True, "qdeck-action")
    except Exception:
        pass

    success = False
    message = ""
    resolved_button: dict | None = None
    try:
        if action == "open_debugger":
            message = "Auto-exec skipped open_debugger; use button to open debugger."
        else:
            message, resolved_button = _run_deck_action_details(
                action=action,
                api_base=api_base,
                button_name=button_name,
                button_slot=button_slot,
            )
        success = True
    except Exception as exc:
        message = _action_error_message(exc)
        success = False
    finally:
        try:
            node_item.setBusyState(False, "")
        except Exception:
            pass

    if success and resolved_button is not None:
        try:
            params = list(getattr(model, "params", None) or [])
            _remember_button_thumbnail_params(params, resolved_button, api_base)
            model.params = params
            _ensure_hidden_params(model, _QDECK_HIDDEN_PARAMS)
        except Exception:
            pass

    try:
        model.info = _mask_api_base_text(str(message or "").strip(), api_base)
    except Exception:
        pass
    if scene is not None:
        try:
            scene.set_node_params(model.name, list(getattr(model, "params", None) or []))
        except Exception:
            pass
        try:
            scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
        except Exception:
            pass
    return success, _mask_api_base_text(str(message or ""), api_base)


def _maybe_auto_execute_on_scene(node_item, *, changed_name: str = "", force: bool = False) -> None:
    if getattr(node_item, "_qdeck_auto_running", False):
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    model = getattr(node_item, "model", None)
    if scene is None or model is None:
        return

    mode = _normalize_mode(_param_value_from_model(model, _MODE_PARAM), _MODE_EXECUTOR)
    if mode != _MODE_EXECUTOR:
        setattr(node_item, "_qdeck_last_auto_signature", "")
        return

    changed_key = str(changed_name or "").strip().lower()
    if changed_key:
        self_name = str(getattr(model, "name", "") or "").strip().lower()
        if self_name and changed_key == self_name:
            return
        source_names = _connected_source_names(scene, node_item)
        if source_names and changed_key not in source_names:
            return

    command, parse_err = _parse_master_from_candidates(_master_candidates_from_scene(scene, node_item))
    if not command:
        if force and parse_err:
            try:
                model.info = f"Master input parse failed: {parse_err}"
                scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
            except Exception:
                pass
        return

    signature = _command_signature(mode, command, _master_source_event_key(scene, node_item))
    last_signature = str(getattr(node_item, "_qdeck_last_auto_signature", "") or "")
    if not force and signature and signature == last_signature:
        return

    setattr(node_item, "_qdeck_auto_running", True)
    try:
        ok, _message = _execute_command_on_node_item(node_item, command)
        if ok:
            setattr(node_item, "_qdeck_last_auto_signature", signature)
        elif force:
            setattr(node_item, "_qdeck_last_auto_signature", "")
    finally:
        setattr(node_item, "_qdeck_auto_running", False)


def _current_auto_signature_on_scene(node_item) -> str:
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    model = getattr(node_item, "model", None)
    if scene is None or model is None:
        return ""
    mode = _normalize_mode(_param_value_from_model(model, _MODE_PARAM), _MODE_EXECUTOR)
    if mode != _MODE_EXECUTOR:
        return ""
    command, _parse_err = _parse_master_from_candidates(_master_candidates_from_scene(scene, node_item))
    if not command:
        return ""
    return _command_signature(mode, command, _master_source_event_key(scene, node_item))


def _install_scene_auto_executor(node_item) -> None:
    if getattr(node_item, "_qdeck_auto_executor_installed", False):
        return
    setattr(node_item, "_qdeck_auto_executor_installed", True)
    setattr(node_item, "_qdeck_last_auto_signature", "")
    setattr(node_item, "_qdeck_auto_running", False)

    def _connect_once() -> bool:
        scene = node_item.scene() if hasattr(node_item, "scene") else None
        if scene is None:
            return False
        if getattr(node_item, "_qdeck_auto_executor_connected", False):
            return True

        def _on_links_changed(*_args):
            setattr(node_item, "_qdeck_last_auto_signature", _current_auto_signature_on_scene(node_item))

        def _on_param_changed(name=None, _params=None):
            _maybe_auto_execute_on_scene(node_item, changed_name=str(name or ""), force=False)

        if hasattr(scene, "linksChanged"):
            try:
                scene.linksChanged.connect(_on_links_changed)
            except Exception:
                pass
        if hasattr(scene, "paramChanged"):
            try:
                scene.paramChanged.connect(_on_param_changed)
            except Exception:
                pass

        setattr(node_item, "_qdeck_auto_executor_connected", True)
        setattr(node_item, "_qdeck_auto_links_cb", _on_links_changed)
        setattr(node_item, "_qdeck_auto_params_cb", _on_param_changed)
        return True

    def _deferred_bootstrap():
        if _connect_once():
            current = _current_auto_signature_on_scene(node_item)
            if current:
                setattr(node_item, "_qdeck_last_auto_signature", current)

    for delay in (0, 120, 400, 900):
        try:
            QtCore.QTimer.singleShot(delay, _deferred_bootstrap)
        except Exception:
            pass


def _request_json(base_url: str, method: str, path: str, body: dict | None = None) -> dict:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    url = urljoin((base_url.rstrip("/") + "/"), path.lstrip("/"))
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url=url, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=3.5) as response:
            raw = response.read().decode("utf-8", errors="replace").strip()
            if not raw:
                return {}
            return json.loads(raw)
    except HTTPError as ex:
        detail = ex.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"HTTP {ex.code}: {detail or ex.reason}") from ex
    except URLError as ex:
        raise RuntimeError(_connection_failure_message(ex.reason)) from ex
    except json.JSONDecodeError as ex:
        raise RuntimeError(f"Invalid JSON response: {ex}") from ex


def _fetch_buttons(base_url: str) -> list[dict]:
    payload = _request_json(base_url, "GET", "/api/buttons")
    if isinstance(payload, dict):
        buttons = payload.get("buttons", [])
    elif isinstance(payload, list):
        buttons = payload
    else:
        buttons = []
    if not isinstance(buttons, list):
        raise RuntimeError("Unexpected /api/buttons payload.")
    out: list[dict] = []
    for row in buttons:
        if isinstance(row, dict):
            out.append(row)
    return out


def _button_label(button: dict) -> str:
    slot_idx = _parse_int(button.get("index"))
    slot_text = "slot n/a" if slot_idx is None else f"slot {slot_idx + 1}"
    name = str(button.get("name", "") or "").strip() or "<unnamed>"
    display_name = str(button.get("displayName", "") or "").strip()
    if display_name and display_name != name:
        return f"{slot_text}: {name} ({display_name})"
    return f"{slot_text}: {name}"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _dict_value_case_insensitive(data: dict, key: str):
    wanted = str(key or "").strip().lower()
    if not wanted:
        return None, False
    for raw_key, value in data.items():
        if str(raw_key or "").strip().lower() == wanted:
            return value, True
    return None, False


def _button_path_values(button: dict):
    if not isinstance(button, dict):
        return
    for key in _THUMBNAIL_PATH_KEYS:
        value, found = _dict_value_case_insensitive(button, key)
        if found:
            yield key, value
    for parent_key in _THUMBNAIL_NESTED_KEYS:
        nested, found = _dict_value_case_insensitive(button, parent_key)
        if not found or not isinstance(nested, dict):
            continue
        for key in _THUMBNAIL_PATH_KEYS:
            value, nested_found = _dict_value_case_insensitive(nested, key)
            if nested_found:
                yield f"{parent_key}.{key}", value


def _absolute_thumbnail_url(api_base: str, raw_url: str) -> str:
    text = str(raw_url or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        return text
    if lowered.startswith("data:"):
        return ""
    base = str(api_base or "").strip()
    if not base:
        return text
    return urljoin((base.rstrip("/") + "/"), text.lstrip("/"))


def _button_thumbnail_url_text(button: dict, api_base: str = "") -> str:
    if not isinstance(button, dict):
        return ""

    thumbnail, found = _dict_value_case_insensitive(button, "thumbnail")
    if found:
        if isinstance(thumbnail, dict):
            for key in _THUMBNAIL_URL_KEYS:
                value, url_found = _dict_value_case_insensitive(thumbnail, key)
                if url_found:
                    url = _absolute_thumbnail_url(api_base, str(value or ""))
                    if url:
                        return url
        else:
            url = _absolute_thumbnail_url(api_base, str(thumbnail or ""))
            if url:
                return url

    for key in _THUMBNAIL_URL_KEYS:
        value, found = _dict_value_case_insensitive(button, key)
        if found:
            url = _absolute_thumbnail_url(api_base, str(value or ""))
            if url:
                return url
    return ""


def _looks_like_thumbnail_url(source_text: str) -> bool:
    lowered = str(source_text or "").strip().lower()
    return lowered.startswith(("http://", "https://"))


def _extract_local_path_text(raw_value) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith(("http://", "https://", "data:")):
        return ""
    if lowered.startswith("file:///"):
        text = unquote(text[8:])
    elif lowered.startswith("file://"):
        text = unquote(text[7:])

    quoted = re.match(r"^[\"']([^\"']+)[\"']", text)
    if quoted:
        text = quoted.group(1).strip()
    else:
        ext_re = "|".join(re.escape(ext.lstrip(".")) for ext in sorted(_THUMBNAIL_IMAGE_EXTS | _THUMBNAIL_ICON_EXTS))
        match = re.match(rf"^(.+?\.({ext_re}))(?=\s|$)", text, re.IGNORECASE)
        if match:
            text = match.group(1).strip()

    return text.strip().strip("\"'")


def _resolve_local_thumbnail_path(raw_value) -> Path | None:
    text = _extract_local_path_text(raw_value)
    if not text:
        return None
    expanded = os.path.expandvars(text)
    candidate = Path(expanded).expanduser()
    candidates = [candidate]
    if not candidate.is_absolute():
        candidates.append(_repo_root() / candidate)

    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue

    if not candidate.is_absolute():
        try:
            resolved = shutil.which(expanded)
        except Exception:
            resolved = None
        if resolved:
            path = Path(resolved)
            try:
                if path.is_file():
                    return path
            except OSError:
                pass
    return None


def _button_thumbnail_source_path(button: dict) -> tuple[Path | None, str]:
    for key, value in _button_path_values(button):
        path = _resolve_local_thumbnail_path(value)
        if path is not None:
            return path, key

    # Last resort: executable-looking names can resolve through PATH.
    for key in ("displayName", "name"):
        value, found = _dict_value_case_insensitive(button, key)
        if not found:
            continue
        path = _resolve_local_thumbnail_path(value)
        if path is not None:
            return path, key
    return None, ""


def _button_thumbnail_source_text(button: dict | None) -> str:
    if not isinstance(button, dict):
        return ""
    path, _key = _button_thumbnail_source_path(button)
    return str(path) if path is not None else ""


def _button_thumbnail_source_text_for_api(button: dict | None, api_base: str = "") -> str:
    if not isinstance(button, dict):
        return ""
    url = _button_thumbnail_url_text(button, api_base)
    if url:
        return url
    return _button_thumbnail_source_text(button)


def _qt_enum_value(enum_name: str, member_name: str, fallback):
    enum_group = getattr(QtCore.Qt, enum_name, None)
    if enum_group is not None:
        value = getattr(enum_group, member_name, None)
        if value is not None:
            return value
    return getattr(QtCore.Qt, member_name, fallback)


def _scaled_thumbnail_pixmap(pixmap, size: int = 56):
    if pixmap is None or pixmap.isNull():
        return None
    aspect = _qt_enum_value("AspectRatioMode", "KeepAspectRatio", getattr(QtCore.Qt, "KeepAspectRatio", 1))
    transform = _qt_enum_value("TransformationMode", "SmoothTransformation", getattr(QtCore.Qt, "SmoothTransformation", 0))
    return pixmap.scaled(QtCore.QSize(int(size), int(size)), aspect, transform)


def _thumbnail_pixmap_for_source(source_text: str, size: int = 56):
    source = str(source_text or "").strip()
    if _looks_like_thumbnail_url(source):
        try:
            request = Request(
                url=source,
                headers={"Accept": "image/png,image/*;q=0.9,*/*;q=0.1"},
                method="GET",
            )
            with urlopen(request, timeout=3.5) as response:
                raw = response.read()
            pixmap = QtGui.QPixmap()
            if raw and pixmap.loadFromData(raw):
                return _scaled_thumbnail_pixmap(pixmap, size)
        except Exception:
            return None
        return None

    path = _resolve_local_thumbnail_path(source_text)
    if path is None:
        return None

    suffix = path.suffix.lower()
    pixmap = None
    if suffix in _THUMBNAIL_IMAGE_EXTS:
        pixmap = QtGui.QPixmap(str(path))
    if pixmap is None or pixmap.isNull():
        icon = QtGui.QIcon(str(path))
        if not icon.isNull():
            pixmap = icon.pixmap(int(size), int(size))
    if pixmap is None or pixmap.isNull():
        try:
            provider = QtWidgets.QFileIconProvider()
            icon = provider.icon(QtCore.QFileInfo(str(path)))
            if not icon.isNull():
                pixmap = icon.pixmap(int(size), int(size))
        except Exception:
            pixmap = None

    return _scaled_thumbnail_pixmap(pixmap, size)


def _remember_button_thumbnail_params(params: list[dict], button: dict | None, api_base: str = "") -> list[dict]:
    if not isinstance(button, dict):
        return params
    _set_param_in_list(params, _LAST_BUTTON_LABEL_PARAM, _button_label(button))
    _set_param_in_list(params, _LAST_THUMBNAIL_SOURCE_PARAM, _button_thumbnail_source_text_for_api(button, api_base))
    return params


def _summarize_buttons(buttons: list[dict], max_rows: int = 12) -> str:
    if not buttons:
        return "No deck buttons reported."
    lines = [f"Buttons: {len(buttons)}"]
    for idx, button in enumerate(buttons[:max_rows], 1):
        lines.append(f"{idx}. {_button_label(button)}")
    if len(buttons) > max_rows:
        lines.append(f"... {len(buttons) - max_rows} more")
    return "\n".join(lines)


def _normalize_match_text(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"\bo[\s\._-]*b[\s\._-]*s\b", "obs", text)
    text = re.sub(r"\bovs\b", "obs", text)
    text = re.sub(r"\bobs\s+stand\b", "obs", text)
    text = re.sub(r"\bovs\s+stand\b", "obs", text)
    text = re.sub(r"\bobs\s+studio\b", "obs", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _button_similarity(target_norm: str, candidate_norm: str) -> float:
    if not target_norm or not candidate_norm:
        return 0.0
    if target_norm == candidate_norm:
        return 1.0
    score = SequenceMatcher(None, target_norm, candidate_norm).ratio()
    if candidate_norm.startswith(target_norm) or target_norm.startswith(candidate_norm):
        score += 0.18
    if target_norm in candidate_norm or candidate_norm in target_norm:
        score += 0.12
    if candidate_norm.endswith("exe"):
        score += 0.03
    elif candidate_norm.endswith(("bat", "cmd")):
        score += 0.02
    if target_norm == "obs" and "obsidian" in candidate_norm:
        score -= 0.08
    return max(0.0, min(1.0, score))


def _resolve_button_by_name(buttons: list[dict], name_text: str) -> tuple[dict | None, str]:
    lowered = str(name_text or "").strip().lower()
    for button in buttons:
        name = str(button.get("name", "") or "").strip()
        display_name = str(button.get("displayName", "") or "").strip()
        if name.lower() == lowered or display_name.lower() == lowered:
            return button, ""

    target_norm = _normalize_match_text(name_text)
    if not target_norm:
        return None, f"Button '{name_text}' not found."

    scored: list[tuple[float, str, str, dict]] = []
    for button in buttons:
        name = str(button.get("name", "") or "").strip()
        display_name = str(button.get("displayName", "") or "").strip()
        best = 0.0
        for candidate in (name, display_name):
            candidate_norm = _normalize_match_text(candidate)
            if not candidate_norm:
                continue
            best = max(best, _button_similarity(target_norm, candidate_norm))
        if best > 0.0:
            scored.append((best, name, display_name, button))

    if not scored:
        return None, f"Button '{name_text}' not found."

    scored.sort(key=lambda row: (-row[0], len(row[2] or row[1] or ""), str(row[1] or "").lower()))
    best_score, _best_name, _best_display, best_button = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0

    if best_score >= 0.72:
        return best_button, ""
    if best_score >= 0.62 and (best_score - second_score) >= 0.08:
        return best_button, ""
    if best_score >= 0.58 and len(scored) == 1:
        return best_button, ""
    return None, f"Button '{name_text}' matched multiple candidates; use exact name or slot."


def _resolve_button(buttons: list[dict], button_name: str, button_slot: str) -> tuple[dict | None, str]:
    name_text = (button_name or "").strip()
    slot_text = (button_slot or "").strip()

    if name_text:
        return _resolve_button_by_name(buttons, name_text)

    if slot_text:
        slot = _parse_int(slot_text)
        if slot is None or slot <= 0:
            return None, f"Invalid button_slot '{slot_text}'. Use a 1-based slot number."
        wanted_index = slot - 1
        for button in buttons:
            idx = _parse_int(button.get("index"))
            if idx is not None and idx == wanted_index:
                return button, ""
        if wanted_index < len(buttons):
            return buttons[wanted_index], ""
        return None, f"Button slot {slot} not found."

    if len(buttons) == 1:
        return buttons[0], ""
    return None, "Set 'button_name' or 'button_slot' to target a button."


def _run_deck_action_details(
    *,
    action: str,
    api_base: str,
    button_name: str,
    button_slot: str,
) -> tuple[str, dict | None]:
    if action == "ping_health":
        payload = _request_json(api_base, "GET", "/api/health")
        status = payload.get("status") if isinstance(payload, dict) else None
        return f"Health: {status or 'OK'}", None

    if action == "list_buttons":
        return _summarize_buttons(_fetch_buttons(api_base)), None

    if action in ("invoke", "highlight_on", "highlight_off"):
        buttons = _fetch_buttons(api_base)
        button, err = _resolve_button(buttons, button_name, button_slot)
        if button is None:
            summary = _summarize_buttons(buttons, max_rows=8)
            raise RuntimeError(
                f"{err}\nAsk user to clarify the exact app/button name or slot.\n\n{summary}"
            )
        target_name = quote(str(button.get("name", "") or "").strip(), safe="")
        if not target_name:
            raise RuntimeError("Target button has no valid name.")

        if action == "invoke":
            _request_json(api_base, "POST", f"/api/buttons/{target_name}/invoke")
            return f"Invoked {_button_label(button)}", button

        enabled = action == "highlight_on"
        query = urlencode({"enabled": "true" if enabled else "false"})
        _request_json(api_base, "POST", f"/api/buttons/{target_name}/highlight?{query}")
        state = "ON" if enabled else "OFF"
        return f"Highlight {state} {_button_label(button)}", button

    raise RuntimeError(f"Unsupported action '{action}'.")


def _run_deck_action(
    *,
    action: str,
    api_base: str,
    button_name: str,
    button_slot: str,
) -> str:
    message, _button = _run_deck_action_details(
        action=action,
        api_base=api_base,
        button_name=button_name,
        button_slot=button_slot,
    )
    return message


def build_ports(node_item) -> None:
    _migrate_mediator_input_param(node_item)
    for name, default in _PARAM_DEFAULTS.items():
        _ensure_param(node_item, name, default)
    _ensure_param(node_item, _MODE_PARAM, _MODE_EXECUTOR)
    _ensure_param(node_item, _LAST_BUTTON_LABEL_PARAM, "")
    _ensure_param(node_item, _LAST_THUMBNAIL_SOURCE_PARAM, "")
    _ensure_hidden_params(getattr(node_item, "model", None), _QDECK_HIDDEN_PARAMS)
    for port in ("api_base", "button", "button_name", "button_slot", "action", MEDIATOR_INPUT_PORT):
        _ensure_input(node_item, port)
    # Keep a visible primary/default socket in addition to named parameter sockets.
    try:
        setattr(node_item, "_show_default_input_with_named", True)
    except Exception:
        pass
    _install_scene_auto_executor(node_item)


def augment_infocard_footer(card, footer_layout) -> bool:
    node = getattr(card, "_node_ref", None)
    if node is None:
        return False
    if _kind_key(node) not in QUBIT_DECK_CONTROLLER_KINDS:
        return False

    scene = getattr(card, "_graph_scene", None)
    node_item = None
    if scene is not None:
        try:
            node_item = scene._node_items.get(node.name)
        except Exception:
            node_item = None
    if node_item is not None:
        try:
            _migrate_mediator_input_param(node_item)
            _ensure_param(node_item, MEDIATOR_INPUT_PORT, "")
            _ensure_param(node_item, _MODE_PARAM, _MODE_EXECUTOR)
            _ensure_param(node_item, _LAST_BUTTON_LABEL_PARAM, "")
            _ensure_param(node_item, _LAST_THUMBNAIL_SOURCE_PARAM, "")
            _ensure_hidden_params(getattr(node_item, "model", None), _QDECK_HIDDEN_PARAMS)
            _ensure_input(node_item, MEDIATOR_INPUT_PORT)
            setattr(node_item, "_show_default_input_with_named", True)
            node_item.update()
        except Exception:
            pass
    _ensure_hidden_params(node, _QDECK_HIDDEN_PARAMS)
    _auto_scene_connected = False
    _last_master_signature = ""

    def _param_value(name: str) -> str:
        key = (name or "").strip().lower()
        for entry in (getattr(node, "params", None) or []):
            if (entry.get("name") or "").strip().lower() == key:
                return str(entry.get("value", "") or "")
        return ""

    def _set_param_value(name: str, value: str) -> None:
        key = (name or "").strip().lower()
        text = str(value or "")
        params = list(getattr(node, "params", None) or [])
        found = False
        for entry in params:
            if (entry.get("name") or "").strip().lower() == key:
                entry["value"] = text
                found = True
                break
        if not found:
            params.append({"name": name, "value": text})
        node.params = params
        _ensure_hidden_params(node, _QDECK_HIDDEN_PARAMS)
        if scene is None:
            return
        try:
            scene.set_node_params(node.name, params)
        except Exception:
            pass

    def _master_input_candidates() -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def _add(text: str) -> None:
            value = str(text or "").strip()
            if not value or value in seen:
                return
            seen.add(value)
            out.append(value)

        _add(_text_from_input(card, node_item, MEDIATOR_INPUT_PORT))
        for port_name in LEGACY_MEDIATOR_INPUT_PORTS:
            _add(_text_from_input(card, node_item, port_name))
        _add(_text_from_default_input(card, node_item))
        for text in _all_input_texts(card, node_item):
            _add(text)
        _add(_param_value(MEDIATOR_INPUT_PORT))
        for port_name in LEGACY_MEDIATOR_INPUT_PORTS:
            _add(_param_value(port_name))
        return out

    def _parse_master_from_candidates(candidates: list[str]) -> tuple[dict[str, str], str]:
        first_err = ""
        for text in candidates:
            if _looks_like_clipboard_blob(text):
                continue
            cmd, err = _parse_master_command_text(text)
            if cmd:
                return cmd, ""
            if err and not first_err:
                first_err = err
        return {}, first_err

    def _connected_source_names() -> set[str]:
        nonlocal node_item
        if scene is None:
            return set()
        if node_item is None:
            try:
                node_item = scene._node_items.get(node.name)
            except Exception:
                node_item = None
        if node_item is None:
            return set()
        try:
            in_edges = list(scene._in_edges(node_item))
        except Exception:
            in_edges = []
        out: set[str] = set()
        for edge in in_edges:
            src = getattr(edge, "src", None)
            if src is None:
                continue
            src_name = str(getattr(getattr(src, "model", None), "name", "") or "").strip().lower()
            if src_name:
                out.add(src_name)
        return out

    def _set_info_text(message: str) -> None:
        text = str(message or "").strip()
        node.info = text
        browser = getattr(card, "_text_browser", None)
        if browser is not None:
            try:
                browser.setVisible(True)
                browser.setMinimumHeight(80)
                browser.setMaximumHeight(16777215)
                browser.setPlainText(text or "No info.")
            except Exception:
                pass

    container = QtWidgets.QWidget(card)
    root = QtWidgets.QVBoxLayout(container)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(6)

    row_mode = QtWidgets.QHBoxLayout()
    row_mode.setContentsMargins(0, 0, 0, 0)
    row_mode.setSpacing(6)

    mode_label = QtWidgets.QLabel("Mode")
    mode_combo = QtWidgets.QComboBox()
    mode_combo.addItem("Context", _MODE_CONTEXT)
    mode_combo.addItem("Executor", _MODE_EXECUTOR)
    wanted_mode = _normalize_mode(_param_value(_MODE_PARAM), _MODE_EXECUTOR)
    for idx in range(mode_combo.count()):
        if str(mode_combo.itemData(idx) or "") == wanted_mode:
            mode_combo.setCurrentIndex(idx)
            break
    mode_combo.setMinimumWidth(130)

    row_mode.addWidget(mode_label, 0)
    row_mode.addWidget(mode_combo, 0)
    row_mode.addStretch(1)
    root.addLayout(row_mode)

    row_api = QtWidgets.QHBoxLayout()
    row_api.setContentsMargins(0, 0, 0, 0)
    row_api.setSpacing(6)

    api_label = QtWidgets.QLabel("API")
    api_edit = QtWidgets.QLineEdit(_param_value("api_base") or DEFAULT_API_BASE)
    api_edit.setPlaceholderText(DEFAULT_API_BASE)
    api_edit.setMinimumWidth(220)
    _mask_line_edit_when_unfocused(api_edit)
    row_api.addWidget(api_label, 0)
    row_api.addWidget(api_edit, 1)
    root.addLayout(row_api)

    row_target = QtWidgets.QHBoxLayout()
    row_target.setContentsMargins(0, 0, 0, 0)
    row_target.setSpacing(6)

    button_name_edit = QtWidgets.QLineEdit(_param_value("button_name") or _param_value("button"))
    button_name_edit.setPlaceholderText("button name")
    button_slot_edit = QtWidgets.QLineEdit(_param_value("button_slot"))
    button_slot_edit.setPlaceholderText("slot #")
    button_slot_edit.setMaximumWidth(90)

    action_combo = QtWidgets.QComboBox()
    action_combo.addItem("Invoke", "invoke")
    action_combo.addItem("Highlight On", "highlight_on")
    action_combo.addItem("Highlight Off", "highlight_off")
    action_combo.addItem("List Buttons", "list_buttons")
    action_combo.addItem("Ping Health", "ping_health")
    action_combo.addItem("Open Debugger", "open_debugger")
    wanted_action = _normalize_action(_param_value("action"), "invoke")
    for idx in range(action_combo.count()):
        if str(action_combo.itemData(idx) or "") == wanted_action:
            action_combo.setCurrentIndex(idx)
            break
    action_combo.setMinimumWidth(170)

    row_target.addWidget(QtWidgets.QLabel("Button"), 0)
    row_target.addWidget(button_name_edit, 1)
    row_target.addWidget(QtWidgets.QLabel("Slot"), 0)
    row_target.addWidget(button_slot_edit, 0)
    row_target.addWidget(action_combo, 0)
    root.addLayout(row_target)

    row_btns = QtWidgets.QHBoxLayout()
    row_btns.setContentsMargins(0, 0, 0, 0)
    row_btns.setSpacing(6)

    run_btn = QtWidgets.QPushButton("Run Action")
    invoke_btn = QtWidgets.QPushButton("Invoke")
    debugger_btn = QtWidgets.QPushButton("Open Debugger")
    row_btns.addWidget(run_btn, 0)
    row_btns.addWidget(invoke_btn, 0)
    row_btns.addWidget(debugger_btn, 0)
    row_btns.addStretch(1)
    root.addLayout(row_btns)

    status_view = QtWidgets.QTextEdit()
    status_view.setReadOnly(True)
    status_view.setMaximumHeight(104)
    status_view.setStyleSheet(
        "QTextEdit{background:#0f1216;color:#9ca3af;border:1px solid #334155;border-radius:4px;padding:4px;}"
    )
    root.addWidget(status_view)

    preview_panel = QtWidgets.QWidget(container)
    preview_panel.setObjectName("QDeckLastButtonPreview")
    preview_panel.setStyleSheet(
        "#QDeckLastButtonPreview{background:#111827;border:1px solid #334155;border-radius:4px;}"
        "#QDeckLastButtonPreview QLabel{color:#cbd5e1;}"
    )
    preview_layout = QtWidgets.QHBoxLayout(preview_panel)
    preview_layout.setContentsMargins(6, 6, 6, 6)
    preview_layout.setSpacing(8)

    preview_icon = QtWidgets.QLabel(preview_panel)
    preview_icon.setFixedSize(64, 64)
    preview_icon.setAlignment(QtCore.Qt.AlignCenter)
    preview_icon.setStyleSheet(
        "QLabel{background:#0f1216;border:1px solid #475569;border-radius:4px;color:#64748b;font-weight:700;}"
    )

    preview_text = QtWidgets.QLabel(preview_panel)
    preview_text.setWordWrap(True)
    preview_text.setMinimumHeight(42)
    preview_text.setStyleSheet("QLabel{font-size:12px;}")

    preview_layout.addWidget(preview_icon, 0, QtCore.Qt.AlignTop)
    preview_layout.addWidget(preview_text, 1)
    preview_panel.setVisible(False)
    root.addWidget(preview_panel)

    def _set_button_preview(label_text: str, source_text: str) -> None:
        label = str(label_text or "").strip()
        source = str(source_text or "").strip()
        if not label and not source:
            preview_panel.setVisible(False)
            return

        pixmap = _thumbnail_pixmap_for_source(source, 56) if source else None
        if pixmap is not None:
            preview_icon.setPixmap(pixmap)
            preview_icon.setText("")
            preview_icon.setStyleSheet(
                "QLabel{background:#0f1216;border:1px solid #475569;border-radius:4px;color:#64748b;font-weight:700;}"
            )
        else:
            preview_icon.clear()
            preview_icon.setText("?")
            preview_icon.setStyleSheet(
                "QLabel{background:#0f1216;border:1px dashed #475569;border-radius:4px;color:#64748b;font-weight:700;}"
            )

        if _looks_like_thumbnail_url(source):
            source_label = source.rstrip("/").split("/")[-1] or "thumbnail"
        else:
            source_label = Path(source).name if source else "No thumbnail source in API response"
        preview_text.setText(f"Last button: {label or '<unnamed>'}\n{source_label}")
        preview_panel.setToolTip(source or "No image/icon/executable path was returned for the resolved button.")
        preview_panel.setVisible(True)

    def _refresh_button_preview_from_params() -> None:
        _set_button_preview(
            _param_value(_LAST_BUTTON_LABEL_PARAM),
            _param_value(_LAST_THUMBNAIL_SOURCE_PARAM),
        )

    def _remember_button_preview(button: dict | None) -> None:
        if not isinstance(button, dict):
            return
        label = _button_label(button)
        source = _button_thumbnail_source_text_for_api(button, api_edit.text().strip() or DEFAULT_API_BASE)
        _set_param_value(_LAST_BUTTON_LABEL_PARAM, label)
        _set_param_value(_LAST_THUMBNAIL_SOURCE_PARAM, source)
        _set_button_preview(label, source)

    def _set_status(message: str, *, error: bool = False) -> None:
        text = str(message or "").strip()
        display_text = _mask_api_base_text(
            text,
            api_edit.text().strip(),
            _param_value("api_base"),
            DEFAULT_API_BASE,
        )
        if error:
            status_view.setStyleSheet(
                "QTextEdit{background:#1f0f12;color:#fecaca;border:1px solid #7f1d1d;border-radius:4px;padding:4px;}"
            )
        else:
            status_view.setStyleSheet(
                "QTextEdit{background:#0f1216;color:#9ca3af;border:1px solid #334155;border-radius:4px;padding:4px;}"
            )
        status_view.setPlainText(display_text)
        _set_info_text(display_text)

    def _persist_manual_fields() -> None:
        _set_param_value(_MODE_PARAM, _normalize_mode(str(mode_combo.currentData() or _MODE_EXECUTOR), _MODE_EXECUTOR))
        _set_param_value("api_base", api_edit.text().strip() or DEFAULT_API_BASE)
        _set_param_value("button_name", button_name_edit.text().strip())
        _set_param_value("button", button_name_edit.text().strip())
        _set_param_value("button_slot", button_slot_edit.text().strip())
        _set_param_value("action", str(action_combo.currentData() or "invoke"))

    def _apply_mode_ui() -> None:
        mode = _normalize_mode(str(mode_combo.currentData() or _MODE_EXECUTOR), _MODE_EXECUTOR)
        is_context = mode == _MODE_CONTEXT
        button_name_edit.setEnabled(not is_context)
        button_slot_edit.setEnabled(not is_context)
        action_combo.setEnabled(not is_context)
        invoke_btn.setEnabled(not is_context)
        if is_context:
            for idx in range(action_combo.count()):
                if str(action_combo.itemData(idx) or "") == "list_buttons":
                    action_combo.setCurrentIndex(idx)
                    break

    def _launch_debugger(api_base: str) -> str:
        try:
            from nodes.qubit_deck_controller import launch_qdeck_debugger as launcher
        except Exception:
            import importlib.util
            from pathlib import Path

            file_path = Path(__file__).resolve().with_name("launch_qdeck_debugger.py")
            spec = importlib.util.spec_from_file_location("qdeck_launcher", str(file_path))
            if not spec or not spec.loader:
                raise RuntimeError("Cannot import launch_qdeck_debugger.py")
            launcher = importlib.util.module_from_spec(spec)  # type: ignore[assignment]
            spec.loader.exec_module(launcher)  # type: ignore[union-attr]

        launcher.launch(api_base=api_base, verbose=False)  # type: ignore[attr-defined]
        return f"Opened debugger window at {api_base}"

    def _execute(
        fallback_action: str,
        *,
        force_action: bool = False,
        master_override: dict[str, str] | None = None,
    ) -> bool:
        _persist_manual_fields()
        mode = _normalize_mode(_param_value(_MODE_PARAM), _MODE_EXECUTOR)

        master_cmd: dict[str, str] = dict(master_override or {})
        if mode == _MODE_EXECUTOR and not master_cmd:
            master_cmd, _master_err = _parse_master_from_candidates(_master_input_candidates())

        api_base = (master_cmd.get("api_base") or _param_value("api_base") or DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
        if not (api_base.lower().startswith("http://") or api_base.lower().startswith("https://")):
            api_base = DEFAULT_API_BASE
        button_name = (master_cmd.get("button_name") or _param_value("button_name") or _param_value("button")).strip()
        if _looks_like_clipboard_blob(button_name):
            button_name = ""
        slot_raw = master_cmd.get("button_slot") or _param_value("button_slot")
        button_slot = _coerce_slot_text(slot_raw)
        if mode == _MODE_CONTEXT and not (force_action and _normalize_action(fallback_action, fallback_action) == "open_debugger"):
            action = "list_buttons"
        elif force_action:
            action = _normalize_action(fallback_action, fallback_action)
        else:
            action = _normalize_action(master_cmd.get("action") or _param_value("action"), fallback_action)

        if master_cmd:
            api_edit.setText(api_base)
            button_name_edit.setText(button_name)
            button_slot_edit.setText(button_slot)
            for idx in range(action_combo.count()):
                if str(action_combo.itemData(idx) or "") == action:
                    action_combo.setCurrentIndex(idx)
                    break

        _set_param_value("api_base", api_base)
        _set_param_value("button_name", button_name)
        _set_param_value("button", button_name)
        _set_param_value("button_slot", button_slot)
        _set_param_value("action", action)

        try:
            if node_item is not None:
                node_item.setBusyState(True, "qdeck-action")
        except Exception:
            pass
        try:
            resolved_button: dict | None = None
            if action == "open_debugger":
                message = _launch_debugger(api_base)
            else:
                message, resolved_button = _run_deck_action_details(
                    action=action,
                    api_base=api_base,
                    button_name=button_name,
                    button_slot=button_slot,
                )
                if resolved_button is not None:
                    _remember_button_preview(resolved_button)
            _set_status(message, error=False)
            display_message = _mask_api_base_text(message, api_base)
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), display_message[:220], card)
            return True
        except Exception as exc:
            _set_status(_action_error_message(exc), error=True)
            return False
        finally:
            try:
                if node_item is not None:
                    node_item.setBusyState(False, "")
            except Exception:
                pass

    def _auto_master_signature(mode: str, command: dict[str, str], event_key: str = "") -> str:
        try:
            body = json.dumps(command or {}, sort_keys=True, separators=(",", ":"))
        except Exception:
            body = str(command or "")
        payload = f"{str(mode or '').strip().lower()}\n{body}\n{str(event_key or '').strip()}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _current_master_signature() -> str:
        mode = _normalize_mode(_param_value(_MODE_PARAM), _MODE_EXECUTOR)
        if mode != _MODE_EXECUTOR or node_item is None:
            return ""
        parsed_cmd, _parse_err = _parse_master_from_candidates(_master_input_candidates())
        if not parsed_cmd:
            return ""
        return _auto_master_signature(mode, parsed_cmd, _master_source_event_key(scene, node_item))

    def _maybe_auto_execute(changed_name=None, *, force: bool = False) -> None:
        nonlocal _last_master_signature
        mode = _normalize_mode(_param_value(_MODE_PARAM), _MODE_EXECUTOR)
        if mode != _MODE_EXECUTOR:
            _last_master_signature = ""
            return
        source_names = _connected_source_names()
        if not source_names:
            return

        changed_key = str(changed_name or "").strip().lower()
        self_name = str(getattr(node, "name", "") or "").strip().lower()
        if changed_key:
            if self_name and changed_key == self_name:
                return
            if changed_key not in source_names:
                return

        parsed_cmd, parse_err = _parse_master_from_candidates(_master_input_candidates())
        if parse_err and force:
            _set_status(f"Master input parse failed: {parse_err}", error=True)
        if not parsed_cmd:
            return
        signature = _auto_master_signature(mode, parsed_cmd, _master_source_event_key(scene, node_item))
        if not force and signature == _last_master_signature:
            return

        if _execute("invoke", force_action=False, master_override=parsed_cmd):
            _last_master_signature = signature
        elif force:
            _last_master_signature = ""

    def _on_scene_links_changed(*_args) -> None:
        nonlocal _last_master_signature
        _last_master_signature = _current_master_signature()

    def _on_scene_param_changed(name=None, _params=None) -> None:
        changed_key = str(name or "").strip().lower()
        self_name = str(getattr(node, "name", "") or "").strip().lower()
        if self_name and changed_key == self_name:
            _refresh_button_preview_from_params()
            return
        _maybe_auto_execute(changed_name=name, force=False)

    def _ensure_scene_connections() -> None:
        nonlocal scene, node_item, _auto_scene_connected, _last_master_signature
        if scene is None:
            try:
                scene = getattr(card, "_graph_scene", None)
            except Exception:
                scene = None
        if scene is None and node_item is not None:
            try:
                scene = node_item.scene()
            except Exception:
                scene = None
        if scene is None:
            return
        if node_item is None:
            try:
                node_item = scene._node_items.get(node.name)
            except Exception:
                node_item = None
        if _auto_scene_connected:
            return
        if hasattr(scene, "linksChanged"):
            try:
                scene.linksChanged.connect(_on_scene_links_changed)
            except Exception:
                pass
        if hasattr(scene, "paramChanged"):
            try:
                scene.paramChanged.connect(_on_scene_param_changed)
            except Exception:
                pass
        _auto_scene_connected = True
        _last_master_signature = _current_master_signature()

    api_edit.editingFinished.connect(_persist_manual_fields)
    button_name_edit.editingFinished.connect(_persist_manual_fields)
    button_slot_edit.editingFinished.connect(_persist_manual_fields)
    action_combo.currentIndexChanged.connect(lambda _idx: _persist_manual_fields())
    mode_combo.currentIndexChanged.connect(lambda _idx: (_apply_mode_ui(), _persist_manual_fields()))

    run_btn.clicked.connect(lambda: _execute(str(action_combo.currentData() or "invoke"), force_action=False))
    invoke_btn.clicked.connect(lambda: _execute("invoke", force_action=True))
    debugger_btn.clicked.connect(lambda: _execute("open_debugger", force_action=True))

    _apply_mode_ui()
    _refresh_button_preview_from_params()
    footer_layout.addWidget(container)
    _set_status(
        "QubitDeckController ready. Mode=Context lists deck buttons. Mode=Executor accepts Mediator JSON on 'mediator_input' or master pin.",
        error=False,
    )
    return True


def _qdeck_canvas_preview_text(label_text: str, source_text: str) -> str:
    text = str(label_text or "").strip()
    if text:
        return text
    source = str(source_text or "").strip()
    if _looks_like_thumbnail_url(source):
        return source.rstrip("/").split("/")[-1] or "thumbnail"
    if source:
        return Path(source).name
    return ""


def render_node_body(node_item, y_cursor: int) -> int:
    model = getattr(node_item, "model", None)
    if model is None:
        return y_cursor

    source = _param_value_from_model(model, _LAST_THUMBNAIL_SOURCE_PARAM, "").strip()
    label_text = _param_value_from_model(model, _LAST_BUTTON_LABEL_PARAM, "").strip()
    if not source and not label_text:
        return y_cursor

    preview_h = 72
    pad = float(getattr(node_item, "_PADDING", 8.0) or 8.0)
    min_required = float(y_cursor) + float(preview_h) + pad
    try:
        if float(getattr(node_item, "height", 0.0) or 0.0) < min_required:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = min_required
    except Exception:
        pass

    body = QtWidgets.QWidget()
    body.setObjectName("QDeckCanvasPreview")
    body.setStyleSheet(
        "#QDeckCanvasPreview{background:#111827;border:1px solid #334155;border-radius:5px;}"
        "#QDeckCanvasPreview QLabel{color:#cbd5e1;font-size:11px;}"
    )
    layout = QtWidgets.QHBoxLayout(body)
    layout.setContentsMargins(7, 7, 7, 7)
    layout.setSpacing(8)

    icon_label = QtWidgets.QLabel(body)
    icon_label.setFixedSize(58, 58)
    icon_label.setAlignment(QtCore.Qt.AlignCenter)
    icon_label.setStyleSheet(
        "QLabel{background:#0f1216;border:1px solid #475569;border-radius:4px;color:#64748b;font-weight:700;}"
    )

    pixmap = _thumbnail_pixmap_for_source(source, 56) if source else None
    if pixmap is not None and not pixmap.isNull():
        icon_label.setPixmap(pixmap)
        icon_label.setText("")
    else:
        icon_label.setText("?")
        icon_label.setStyleSheet(
            "QLabel{background:#0f1216;border:1px dashed #475569;border-radius:4px;color:#64748b;font-weight:700;}"
        )

    text_label = QtWidgets.QLabel(_qdeck_canvas_preview_text(label_text, source), body)
    text_label.setWordWrap(True)
    text_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
    text_label.setMinimumHeight(40)

    layout.addWidget(icon_label, 0, QtCore.Qt.AlignVCenter)
    layout.addWidget(text_label, 1)
    body.setMinimumHeight(preview_h)

    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)

    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    body_h = max(preview_h, int(hint.height()))
    width = max(120, int(float(getattr(node_item, "width", 260) or 260)) - 12)
    proxy.resize(width, body_h)

    try:
        preview_y = max(float(y_cursor), float(node_item.height) - float(body_h) - pad)
    except Exception:
        preview_y = float(y_cursor)
    proxy.setPos(6, preview_y)

    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    bottom_y = int(preview_y + body_h)
    try:
        required_height = float(bottom_y) + pad
        if float(getattr(node_item, "height", 0.0) or 0.0) < required_height:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = required_height
    except Exception:
        pass
    return y_cursor


QUBIT_DECK_CONTROLLER_SPEC = Spec(
    stripe_color="#0f766e",
    augment_infocard_footer=augment_infocard_footer,
    render_node_body=render_node_body,
    build_ports=build_ports,
)
