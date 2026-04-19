from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec


DEFAULT_API_BASE = "http://127.0.0.1:8765"
QUBIT_DECK_CONTROLLER_KINDS = {
    "qubit_deck_controller",
    "qubit deck controller",
    "qubitdeckcontroller",
    "qubitdeck controller",
}

_PARAM_DEFAULTS = {
    "api_base": DEFAULT_API_BASE,
    "button": "",
    "button_name": "",
    "button_slot": "",
    "action": "invoke",
}
_HIDDEN_PARAM = "__ui_hidden_params"

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
        try:
            txt = scene.resolve_text_value(edge.src)
        except Exception:
            txt = ""
        txt = str(txt or "").strip()
        if txt:
            parts.append(txt)
    return "\n\n".join(parts).strip()


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
        raise RuntimeError(f"Connection failed: {ex.reason}") from ex
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


def _summarize_buttons(buttons: list[dict], max_rows: int = 12) -> str:
    if not buttons:
        return "No deck buttons reported."
    lines = [f"Buttons: {len(buttons)}"]
    for idx, button in enumerate(buttons[:max_rows], 1):
        lines.append(f"{idx}. {_button_label(button)}")
    if len(buttons) > max_rows:
        lines.append(f"... {len(buttons) - max_rows} more")
    return "\n".join(lines)


def _resolve_button(buttons: list[dict], button_name: str, button_slot: str) -> tuple[dict | None, str]:
    name_text = (button_name or "").strip()
    slot_text = (button_slot or "").strip()

    if name_text:
        lowered = name_text.lower()
        for button in buttons:
            name = str(button.get("name", "") or "").strip()
            display_name = str(button.get("displayName", "") or "").strip()
            if name.lower() == lowered or display_name.lower() == lowered:
                return button, ""
        return None, f"Button '{name_text}' not found."

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


def _run_deck_action(
    *,
    action: str,
    api_base: str,
    button_name: str,
    button_slot: str,
) -> str:
    if action == "ping_health":
        payload = _request_json(api_base, "GET", "/api/health")
        status = payload.get("status") if isinstance(payload, dict) else None
        return f"Health: {status or 'OK'}"

    if action == "list_buttons":
        return _summarize_buttons(_fetch_buttons(api_base))

    if action in ("invoke", "highlight_on", "highlight_off"):
        buttons = _fetch_buttons(api_base)
        button, err = _resolve_button(buttons, button_name, button_slot)
        if button is None:
            summary = _summarize_buttons(buttons, max_rows=8)
            raise RuntimeError(f"{err}\n\n{summary}")
        target_name = quote(str(button.get("name", "") or "").strip(), safe="")
        if not target_name:
            raise RuntimeError("Target button has no valid name.")

        if action == "invoke":
            _request_json(api_base, "POST", f"/api/buttons/{target_name}/invoke")
            return f"Invoked {_button_label(button)}"

        enabled = action == "highlight_on"
        query = urlencode({"enabled": "true" if enabled else "false"})
        _request_json(api_base, "POST", f"/api/buttons/{target_name}/highlight?{query}")
        state = "ON" if enabled else "OFF"
        return f"Highlight {state} {_button_label(button)}"

    raise RuntimeError(f"Unsupported action '{action}'.")


def build_ports(node_item) -> None:
    for name, default in _PARAM_DEFAULTS.items():
        _ensure_param(node_item, name, default)
    _ensure_hidden_params(getattr(node_item, "model", None), ["button"])
    for port in ("api_base", "button", "button_name", "button_slot", "action"):
        _ensure_input(node_item, port)


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
        if scene is None:
            return
        try:
            scene.set_node_params(node.name, params)
        except Exception:
            pass

    def _val(name: str) -> str:
        wired = _text_from_input(card, node_item, name)
        if wired and wired.strip():
            return wired.strip()
        return _param_value(name)

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

    row_api = QtWidgets.QHBoxLayout()
    row_api.setContentsMargins(0, 0, 0, 0)
    row_api.setSpacing(6)

    api_label = QtWidgets.QLabel("API")
    api_edit = QtWidgets.QLineEdit(_param_value("api_base") or DEFAULT_API_BASE)
    api_edit.setPlaceholderText(DEFAULT_API_BASE)
    api_edit.setMinimumWidth(220)
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

    def _set_status(message: str, *, error: bool = False) -> None:
        text = str(message or "").strip()
        if error:
            status_view.setStyleSheet(
                "QTextEdit{background:#1f0f12;color:#fecaca;border:1px solid #7f1d1d;border-radius:4px;padding:4px;}"
            )
        else:
            status_view.setStyleSheet(
                "QTextEdit{background:#0f1216;color:#9ca3af;border:1px solid #334155;border-radius:4px;padding:4px;}"
            )
        status_view.setPlainText(text)
        _set_info_text(text)

    def _persist_manual_fields() -> None:
        _set_param_value("api_base", api_edit.text().strip() or DEFAULT_API_BASE)
        _set_param_value("button_name", button_name_edit.text().strip())
        _set_param_value("button", button_name_edit.text().strip())
        _set_param_value("button_slot", button_slot_edit.text().strip())
        _set_param_value("action", str(action_combo.currentData() or "invoke"))

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

    def _execute(fallback_action: str, *, force_action: bool = False) -> None:
        _persist_manual_fields()

        api_base = (_val("api_base") or DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
        button_name = (_val("button_name") or _val("button")).strip()
        button_slot = (_val("button_slot") or "").strip()
        if force_action:
            action = _normalize_action(fallback_action, fallback_action)
        else:
            action = _normalize_action(_val("action"), fallback_action)
        _set_param_value("action", action)

        try:
            if action == "open_debugger":
                message = _launch_debugger(api_base)
            else:
                message = _run_deck_action(
                    action=action,
                    api_base=api_base,
                    button_name=button_name,
                    button_slot=button_slot,
                )
            _set_status(message, error=False)
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), message[:220], card)
        except Exception as exc:
            _set_status(f"Action failed: {exc}", error=True)

    api_edit.editingFinished.connect(_persist_manual_fields)
    button_name_edit.editingFinished.connect(_persist_manual_fields)
    button_slot_edit.editingFinished.connect(_persist_manual_fields)
    action_combo.currentIndexChanged.connect(lambda _idx: _persist_manual_fields())

    run_btn.clicked.connect(lambda: _execute(str(action_combo.currentData() or "invoke"), force_action=False))
    invoke_btn.clicked.connect(lambda: _execute("invoke", force_action=True))
    debugger_btn.clicked.connect(lambda: _execute("open_debugger", force_action=True))

    footer_layout.addWidget(container)
    _set_status(
        "QubitDeckController ready. Wire 'action' + 'button_name'/'button_slot' then click Run Action.",
        error=False,
    )
    return True


QUBIT_DECK_CONTROLLER_SPEC = Spec(
    stripe_color="#0f766e",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)
