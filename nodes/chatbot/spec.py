from __future__ import annotations

import os
import re
import threading
from typing import List

try:
    from pymongo import MongoClient  # type: ignore
except Exception:
    MongoClient = None  # optional; DB feature disabled if missing

from nodes.core import Spec
from echograph.qt_compat import QtWidgets, QtCore
from nodes.gpt_prompt import spec as gpt_spec

CHATBOT_NODE_KIND = "chatbot"
CHATBOT_NODE_ALIASES = [
    "chat bot",
    "chat_bot",
]
CHATBOT_NODE_KINDS = {CHATBOT_NODE_KIND, *CHATBOT_NODE_ALIASES}
VOICE_ACTOR_NODE_KINDS = {"voice_actor", "voice actor", "voiceactor"}
VOICE_INPUT_PORT = "voice_input"
MEDIATOR_INPUT_PORT = "mediator_input"

DB_NAME = "my_database"
COLLECTION = "EchoGragh"

CHATBOT_BODY_W = 420
CHATBOT_BODY_H = 360
BUBBLE_MAX_W = 340


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        try:
            params = list(params)
        except Exception:
            params = []
        setattr(model, "params", params)
    key = name.strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def build_ports(node_item) -> None:
    _ensure_param(node_item, "llm_prompt", "")
    _ensure_param(node_item, "database", "")
    _ensure_param(node_item, VOICE_INPUT_PORT, "")
    _ensure_param(node_item, MEDIATOR_INPUT_PORT, "")
    model = getattr(node_item, "model", None)
    params = getattr(model, "params", None) if model is not None else None
    if isinstance(params, list):
        desired = ["llm_prompt", "database", VOICE_INPUT_PORT, MEDIATOR_INPUT_PORT]
        ordered = []
        used = set()
        for name in desired:
            key = name.strip().lower()
            for idx, entry in enumerate(params):
                if idx in used or not isinstance(entry, dict):
                    continue
                if (entry.get("name") or "").strip().lower() == key:
                    ordered.append(entry)
                    used.add(idx)
                    break
        for idx, entry in enumerate(params):
            if idx not in used:
                ordered.append(entry)
        model.params = ordered
    for port in ("llm_prompt", "database", VOICE_INPUT_PORT, MEDIATOR_INPUT_PORT):
        if hasattr(node_item, "ensure_input"):
            node_item.ensure_input(port)


def _param_value_from_node(node_item, name: str) -> str:
    model = getattr(node_item, "model", None)
    params = getattr(model, "params", None) or []
    key = (name or "").strip().lower()
    for p in params:
        if (p.get("name") or "").strip().lower() == key:
            return p.get("value") or ""
    return ""


def _text_from_input(scene, node_item, port_name: str) -> str:
    if not scene or not node_item:
        return ""
    try:
        in_edges = list(scene._in_edges(node_item))
    except Exception:
        in_edges = []

    def _edge_matches_name(edge) -> bool:
        for attr in ("dst_port_name", "dst_label", "dst_name"):
            if hasattr(edge, attr) and getattr(edge, attr) == port_name:
                return True
        return False

    named_edges = [e for e in in_edges if _edge_matches_name(e)]
    if not named_edges:
        return ""
    try:
        ordered = [
            e for e in scene._ordered_in_edges(node_item)
            if e in named_edges
        ]
        if ordered:
            named_edges = ordered
    except Exception:
        pass

    parts: List[str] = []
    for edge in named_edges:
        try:
            txt = scene.resolve_text_value(edge.src)
        except Exception:
            txt = ""
        if txt:
            parts.append(txt.strip())
    return "\n\n".join(parts).strip()


def _find_input_node(scene, node_item, port_names: set[str], kind_set: set[str] | None = None):
    if not scene or not node_item:
        return None
    try:
        in_edges = list(scene._in_edges(node_item))
    except Exception:
        in_edges = []
    port_names = {p.strip().lower() for p in (port_names or set())}
    for edge in in_edges:
        dst_port = None
        for attr in ("dst_port_name", "dst_label", "dst_name"):
            if hasattr(edge, attr):
                dst_port = getattr(edge, attr)
                if dst_port:
                    break
        if dst_port and dst_port.strip().lower() in port_names:
            src = getattr(edge, "src", None)
            if not src:
                continue
            if kind_set is None:
                return src
            kind = (getattr(getattr(src, "model", None), "kind", "") or "").strip().lower()
            if kind in kind_set:
                return src
    if kind_set:
        for edge in in_edges:
            src = getattr(edge, "src", None)
            if not src:
                continue
            kind = (getattr(getattr(src, "model", None), "kind", "") or "").strip().lower()
            if kind in kind_set:
                return src
    return None


def _connected_database(scene, node_item):
    db_item = _find_input_node(scene, node_item, {"database", "db"}, {"database"})
    if not db_item:
        return None
    return {
        "node_name": getattr(getattr(db_item, "model", None), "name", ""),
        "mongo_uri": _param_value_from_node(db_item, "mongo_uri") or "mongodb://localhost:27017",
        "project": _param_value_from_node(db_item, "project"),
        "note": _param_value_from_node(db_item, "note"),
        "collection": _param_value_from_node(db_item, "collection") or COLLECTION,
    }


def _connected_prompt_node(scene, node_item):
    return _find_input_node(scene, node_item, {"llm_prompt", "prompt_node", "llm"}, gpt_spec.PROMPT_NODE_KINDS)


def _connected_mediator_node(scene, node_item):
    try:
        from nodes.mediator_agent import spec as mediator_spec
        mediator_kinds = getattr(mediator_spec, "MEDIGATOR_NODE_KINDS", set())
    except Exception:
        mediator_kinds = {
            "mediator_agent",
            "medigator_agent",
            "mediator",
            "medigator",
            "mediator agent",
            "medigator agent",
        }
    return _find_input_node(scene, node_item, {MEDIATOR_INPUT_PORT, "mediator", "agent"}, mediator_kinds)


def _mediator_agent_block(scene, mediator_node_item) -> str:
    if mediator_node_item is None:
        return ""
    try:
        from nodes.mediator_agent import spec as mediator_spec
        helper = getattr(mediator_spec, "chatbot_agent_context_from_item", None)
        if callable(helper):
            return str(helper(scene, mediator_node_item) or "").strip()
    except Exception as exc:
        return f"Mediator agent layer unavailable: {exc}"
    try:
        return str(scene.resolve_text_value(mediator_node_item) or "").strip()
    except Exception:
        return ""


_SECURITY_MARKER_RE = re.compile(
    r"<security_(?:request|approval)\b[^>]*>.*?</security_(?:request|approval)>",
    re.IGNORECASE | re.DOTALL,
)
_DATA_NEXUS_UPDATE_RE = re.compile(
    r"<data_nexus_update\b[^>]*>.*?</data_nexus_update>",
    re.IGNORECASE | re.DOTALL,
)
_DATA_NEXUS_REQUEST_RE = re.compile(
    r"\b(data\s*nexus|nexus|vault|graph|points?|notes?|memory|mediator[\s_-]+planner|judge)\b",
    re.IGNORECASE,
)
_GENERIC_PERMISSION_RESPONSE_RE = re.compile(
    r"^\s*i\s+need\s+(?:your\s+)?permission\s+(?:to\s+proceed|before\s+i\s+proceed)\.?\s*$",
    re.IGNORECASE,
)
_QDECK_COMMAND_OUTPUT_RE = re.compile(
    r"\{[^{}]*\"action\"\s*:\s*\"(?:invoke|highlight_on|highlight_off|list_buttons|ping_health|open_debugger)\""
    r"[^{}]*(?:\"button_name\"|\"button_slot\")[^{}]*\}",
    re.IGNORECASE | re.DOTALL,
)
_USER_FEEDBACK_RE = re.compile(
    r"<user_feedback\b[^>]*>.*?</user_feedback>",
    re.IGNORECASE | re.DOTALL,
)
_APP_LAUNCH_REQUEST_RE = re.compile(
    r"\b(open|launch|run|start|press|click)\b",
    re.IGNORECASE,
)
_DIRECT_OPEN_FAILURE_RE = re.compile(
    r"\b(?:can(?:not|'t)|unable\s+to)\s+open\b|\btap\s+one\s+of\s+those\b|\brun\s+.+\s+from\s+start",
    re.IGNORECASE | re.DOTALL,
)


def _display_response_text(response_text: str) -> str:
    text = str(response_text or "").strip()
    if not text:
        return ""
    had_security_marker = bool(_SECURITY_MARKER_RE.search(text))
    had_data_nexus_update = bool(_DATA_NEXUS_UPDATE_RE.search(text))
    text = _SECURITY_MARKER_RE.sub("", text).strip()
    text = _DATA_NEXUS_UPDATE_RE.sub("", text).strip()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text and had_security_marker:
        return "Tool request sent to Mediator."
    if not text and had_data_nexus_update:
        return "Data Nexus update sent to Mediator."
    return text


def _mediator_handled_display_text(response_text: str, raw_response_text: str, user_prompt: str, mediator_handled: bool) -> str:
    text = str(response_text or "").strip()
    raw_text = str(raw_response_text or response_text or "").strip()
    if (
        mediator_handled
        and _DATA_NEXUS_REQUEST_RE.search(str(user_prompt or ""))
        and (
            _GENERIC_PERMISSION_RESPONSE_RE.match(text)
            or _QDECK_COMMAND_OUTPUT_RE.search(raw_text)
            or _USER_FEEDBACK_RE.search(raw_text)
        )
    ):
        return "Data Nexus update sent to Mediator."
    if (
        mediator_handled
        and _APP_LAUNCH_REQUEST_RE.search(str(user_prompt or ""))
        and not _DATA_NEXUS_REQUEST_RE.search(str(user_prompt or ""))
        and _DIRECT_OPEN_FAILURE_RE.search(text)
    ):
        return "Security Guard approval requested."
    return text


def _dispatch_mediator_output(scene, node_item, raw_response_text: str, user_prompt: str, history_context: str = "") -> bool:
    mediator_node = _connected_mediator_node(scene, node_item)
    if mediator_node is None:
        return False
    try:
        from nodes.mediator_agent import spec as mediator_spec
        handler = getattr(mediator_spec, "handle_chatbot_model_output_from_item", None)
        if callable(handler):
            return bool(handler(scene, mediator_node, raw_response_text, user_prompt, history_context))
    except Exception:
        return False
    return False


def _mediator_data_nexus_read_response(scene, node_item, user_prompt: str) -> str:
    mediator_node = _connected_mediator_node(scene, node_item)
    if mediator_node is None:
        return ""
    try:
        from nodes.mediator_agent import spec as mediator_spec
        helper = getattr(mediator_spec, "data_nexus_read_response_from_item", None)
        if callable(helper):
            return str(helper(scene, mediator_node, user_prompt) or "").strip()
    except Exception:
        return ""
    return ""


def _edge_port_name(edge) -> str:
    for attr in ("dst_port_name", "dst_label", "dst_name"):
        if hasattr(edge, attr):
            raw = getattr(edge, attr)
            if raw:
                return str(raw).strip().lower()
    return ""


def _voice_actor_mode_from_item(voice_item) -> str:
    mode = (_param_value_from_node(voice_item, "__voice_actor_mode") or "").strip().lower()
    return mode or "voice_to_text"


def _connected_voice_actor(scene, node_item):
    if not scene or not node_item:
        return None
    try:
        in_edges = list(scene._in_edges(node_item))
    except Exception:
        in_edges = []
    if not in_edges:
        return None

    port_names = {VOICE_INPUT_PORT, "voice", "transcript", "voice_actor"}
    candidates = []
    for edge in in_edges:
        src = getattr(edge, "src", None)
        if src is None:
            continue
        kind = (getattr(getattr(src, "model", None), "kind", "") or "").strip().lower()
        if kind not in VOICE_ACTOR_NODE_KINDS:
            continue
        port = _edge_port_name(edge)
        # Only accept explicit voice-input links (or unlabeled legacy links).
        if port and port not in port_names:
            continue
        candidates.append((port, src))
    if not candidates:
        return None

    named = [src for port, src in candidates if port in port_names]
    if named:
        for src in named:
            if _voice_actor_mode_from_item(src) == "voice_to_text":
                return src
        return named[0]

    for _port, src in candidates:
        if _voice_actor_mode_from_item(src) == "voice_to_text":
            return src
    return candidates[0][1]


def _load_history(cfg: dict) -> list[dict]:
    if MongoClient is None:
        raise RuntimeError("pymongo is not installed; cannot read history.")
    uri = cfg.get("mongo_uri") or "mongodb://localhost:27017"
    project = (cfg.get("project") or "").strip()
    collection_name = (cfg.get("collection") or COLLECTION).strip() or COLLECTION
    if not project:
        raise RuntimeError("Database node is connected but no project is selected.")
    client = MongoClient(uri)
    coll = client[DB_NAME][collection_name]
    doc = coll.find_one({"$or": [{"name": project}, {"project": project}]}) or {}
    history = doc.get("history") or []
    return list(history) if isinstance(history, list) else []


def chatbot_history_context_from_item(scene, node_item, *, max_entries: int = 24) -> str:
    db_cfg = _connected_database(scene, node_item)
    model = getattr(node_item, "model", None)
    fallback = str(getattr(model, "info", "") or "").strip() if model is not None else ""
    if not db_cfg:
        return fallback
    try:
        history = _load_history(db_cfg)
    except Exception:
        return fallback
    entries = [entry for entry in history if isinstance(entry, dict)]
    if not entries:
        return fallback
    try:
        limit = max(1, int(max_entries))
    except Exception:
        limit = 24
    entries = entries[-limit:]
    project = str(db_cfg.get("project") or "").strip()
    collection = str(db_cfg.get("collection") or "").strip()
    lines = ["Chatbot database history:"]
    if project:
        lines.append(f"Project: {project}")
    if collection:
        lines.append(f"Collection: {collection}")
    for idx, entry in enumerate(entries, 1):
        prompt = str(entry.get("prompt") or "").strip()
        response = str(entry.get("response") or "").strip()
        content = str(entry.get("content") or "").strip()
        role = str(entry.get("role") or "").strip()
        if prompt:
            lines.append(f"\nTurn {idx} User:\n{prompt}")
        if response:
            lines.append(f"Turn {idx} Assistant:\n{response}")
        if content and not (prompt or response):
            label = role.title() if role else f"Entry {idx}"
            lines.append(f"\n{label}:\n{content}")
    return "\n".join(lines).strip()


def _format_file_contexts(contexts) -> str:
    if not contexts:
        return ""
    blocks = []
    for path, snippet in contexts:
        name = getattr(path, "name", str(path))
        blocks.append(f"### {name}\n{snippet}")
    context_blob = "\n\n".join(blocks)
    return f"# Attached Files\n{context_blob}"


class ChatbotWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._sending = False
        self._pending_voice_prompt = ""
        self._last_voice_source = ""
        self._last_voice_transcript = ""
        self._last_voice_mode = ""
        self._last_voice_send_token = ""
        self.setMinimumSize(CHATBOT_BODY_W, CHATBOT_BODY_H)
        try:
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass

        self._history_view = QtWidgets.QScrollArea()
        self._history_view.setWidgetResizable(True)
        self._history_view.setStyleSheet(
            "QScrollArea{background:#0f1216;border:1px solid #334;border-radius:6px;}"
        )

        self._history_container = QtWidgets.QWidget()
        self._history_layout = QtWidgets.QVBoxLayout(self._history_container)
        self._history_layout.setContentsMargins(8, 8, 8, 8)
        self._history_layout.setSpacing(6)
        self._history_layout.addStretch(1)
        self._history_view.setWidget(self._history_container)
        self._auto_scroll_pending = True
        try:
            self._history_view.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)
        except Exception:
            pass

        self._input = QtWidgets.QLineEdit()
        self._input.setPlaceholderText("Type a message and press Enter")
        self._input.setStyleSheet(
            "QLineEdit{background:#12151a;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:4px;padding:4px 6px;}"
        )
        self._input.returnPressed.connect(self._on_send)

        self._send_btn = QtWidgets.QPushButton("Send")
        self._send_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#e2e8f0;border:1px solid #1d4ed8;"
            "border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#1d4ed8;}"
        )
        self._send_btn.clicked.connect(self._on_send)

        self._refresh_btn = QtWidgets.QPushButton("Refresh")
        self._refresh_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:4px 8px;}"
            "QPushButton:hover{background:#273449;}"
        )
        self._refresh_btn.clicked.connect(self._refresh_history)

        input_row = QtWidgets.QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(6)
        input_row.addWidget(self._input, 1)
        input_row.addWidget(self._send_btn, 0)
        input_row.addWidget(self._refresh_btn, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._history_view, 1)
        layout.addLayout(input_row, 0)

        self._init_timer = QtCore.QTimer(self)
        self._init_timer.setInterval(200)
        self._init_timer.timeout.connect(self._refresh_history)
        self._init_timer.start()

    def sizeHint(self):
        return QtCore.QSize(CHATBOT_BODY_W, CHATBOT_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(CHATBOT_BODY_W, CHATBOT_BODY_H)

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene and not self._scene_connected:
            if hasattr(self._scene, "linksChanged"):
                try:
                    self._scene.linksChanged.connect(self._on_scene_links_changed)
                except Exception:
                    pass
            if hasattr(self._scene, "paramChanged"):
                try:
                    self._scene.paramChanged.connect(self._on_scene_param_changed)
                except Exception:
                    pass
            self._scene_connected = True
            try:
                if getattr(self, "_init_timer", None):
                    self._init_timer.stop()
            except Exception:
                pass
            self._sync_voice_baseline(scene=self._scene)
        return self._scene

    def _on_scene_links_changed(self, *_args):
        self._refresh_history()
        self._sync_voice_baseline()

    def _connected_source_names(self) -> set[str]:
        scene = self._ensure_scene()
        if scene is None:
            return set()
        out = set()
        try:
            in_edges = list(scene._in_edges(self._node_item))
        except Exception:
            in_edges = []
        for edge in in_edges:
            src = getattr(edge, "src", None)
            if src is None:
                continue
            src_name = str(getattr(getattr(src, "model", None), "name", "") or "").strip().lower()
            if src_name:
                out.add(src_name)
        return out

    def _on_scene_param_changed(self, name=None, _params=None):
        changed_key = str(name or "").strip().lower()
        if changed_key:
            allowed = self._connected_source_names()
            self_name = str(getattr(getattr(self._node_item, "model", None), "name", "") or "").strip().lower()
            if self_name:
                allowed.add(self_name)
            if allowed and changed_key not in allowed:
                return
        self._refresh_history()
        self._handle_voice_param_change(name)

    def _sync_voice_baseline(self, scene=None):
        if scene is None:
            scene = self._ensure_scene()
        if scene is None:
            return
        voice_item = _connected_voice_actor(scene, self._node_item)
        if voice_item is None:
            self._last_voice_source = ""
            self._last_voice_transcript = ""
            self._last_voice_mode = ""
            self._last_voice_send_token = ""
            return
        voice_model = getattr(voice_item, "model", None)
        source_name = str(getattr(voice_model, "name", "") or "").strip().lower() if voice_model is not None else ""
        transcript = str(getattr(voice_model, "info", "") or "").strip() if voice_model is not None else ""
        mode = self._voice_mode(voice_item)
        token = self._voice_send_token(voice_item)
        self._last_voice_source = source_name
        self._last_voice_transcript = transcript
        self._last_voice_mode = mode
        self._last_voice_send_token = token

    def _voice_mode(self, voice_item) -> str:
        mode = (_param_value_from_node(voice_item, "__voice_actor_mode") or "").strip().lower()
        return mode or "voice_to_text"

    def _voice_send_token(self, voice_item) -> str:
        return (_param_value_from_node(voice_item, "__voice_actor_send_token") or "").strip()

    def _handle_voice_param_change(self, changed_name=None):
        scene = self._ensure_scene()
        if scene is None:
            return
        voice_item = _connected_voice_actor(scene, self._node_item)
        if voice_item is None:
            self._last_voice_source = ""
            self._last_voice_transcript = ""
            self._last_voice_mode = ""
            self._last_voice_send_token = ""
            return
        voice_model = getattr(voice_item, "model", None)
        if voice_model is None:
            return
        source_name = str(getattr(voice_model, "name", "") or "").strip().lower()
        changed_key = str(changed_name or "").strip().lower()
        if changed_key and source_name and changed_key != source_name:
            return

        transcript = str(getattr(voice_model, "info", "") or "").strip()
        mode = self._voice_mode(voice_item)
        token = self._voice_send_token(voice_item)
        if mode != self._last_voice_mode:
            self._last_voice_mode = mode
            self._last_voice_source = source_name
            self._last_voice_transcript = transcript
            self._last_voice_send_token = token
            return
        if mode != "voice_to_text":
            self._last_voice_source = source_name
            self._last_voice_transcript = transcript
            self._last_voice_send_token = token
            return
        if not transcript:
            self._last_voice_source = source_name
            self._last_voice_transcript = ""
            self._last_voice_send_token = token
            return
        if not token:
            self._last_voice_source = source_name
            self._last_voice_transcript = transcript
            return
        if source_name == self._last_voice_source and token == self._last_voice_send_token:
            return

        self._last_voice_mode = mode
        self._last_voice_source = source_name
        self._last_voice_transcript = transcript
        self._last_voice_send_token = token
        self._submit_prompt(transcript, from_voice=True)

    def _set_sending(self, active: bool):
        self._sending = bool(active)
        self._input.setEnabled(not active)
        self._send_btn.setEnabled(not active)
        self._refresh_btn.setEnabled(not active)
        try:
            QtCore.QMetaObject.invokeMethod(
                self._node_item,
                "setBusyState",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(bool, bool(active)),
                QtCore.Q_ARG(str, "sending" if active else ""),
            )
        except Exception:
            pass

    def _clear_history_layout(self):
        while self._history_layout.count():
            item = self._history_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._history_layout.addStretch(1)

    def _add_bubble(self, text: str, *, role: str):
        if not text:
            return
        wrapper = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        is_user = role == "user"
        is_files = role == "files"

        if is_user:
            row.addStretch(1)

        bubble = QtWidgets.QFrame()
        bubble_layout = QtWidgets.QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(8, 6, 8, 6)
        bubble_layout.setSpacing(2)

        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        bubble_layout.addWidget(label)
        bubble.setMaximumWidth(BUBBLE_MAX_W)

        if is_files:
            bubble.setStyleSheet(
                "QFrame{background:#374151;color:#f9fafb;border:1px solid #4b5563;"
                "border-radius:8px;}"
            )
        elif is_user:
            bubble.setStyleSheet(
                "QFrame{background:#2563eb;color:#f9fafb;border:1px solid #1d4ed8;"
                "border-radius:8px;}"
            )
        else:
            bubble.setStyleSheet(
                "QFrame{background:#1f2937;color:#e5e7eb;border:1px solid #334;"
                "border-radius:8px;}"
            )

        row.addWidget(bubble, 0)
        if not is_user:
            row.addStretch(1)

        self._history_layout.insertWidget(self._history_layout.count() - 1, wrapper)

    def _scroll_to_bottom(self):
        try:
            sb = self._history_view.verticalScrollBar()
            sb.setValue(sb.maximum())
        except Exception:
            pass

    def _schedule_scroll(self):
        self._auto_scroll_pending = True
        self._scroll_to_bottom()
        try:
            QtCore.QTimer.singleShot(0, self._scroll_to_bottom)
            QtCore.QTimer.singleShot(50, self._scroll_to_bottom)
            QtCore.QTimer.singleShot(150, self._scroll_to_bottom)
        except Exception:
            pass

    def _on_scroll_range_changed(self, _min: int, _max: int) -> None:
        if not self._auto_scroll_pending:
            return
        self._auto_scroll_pending = False
        self._scroll_to_bottom()
        try:
            QtCore.QTimer.singleShot(0, self._scroll_to_bottom)
        except Exception:
            pass

    def _refresh_history(self):
        scene = self._ensure_scene()
        node_item = self._node_item
        if not scene or not node_item:
            return
        self._auto_scroll_pending = True
        db_cfg = _connected_database(scene, node_item)
        self._clear_history_layout()
        if not db_cfg:
            self._add_bubble("Connect a Database node to the 'database' input.", role="files")
            self._schedule_scroll()
            return
        try:
            history = _load_history(db_cfg)
        except Exception as exc:
            self._add_bubble(f"Failed to load history: {exc}", role="files")
            self._schedule_scroll()
            return

        for entry in history:
            if not isinstance(entry, dict):
                continue
            prompt = (entry.get("prompt") or "").strip()
            response = (entry.get("response") or "").strip()
            files = entry.get("files") or []
            file_contexts = entry.get("file_contexts") or []
            if isinstance(files, str):
                files = [files]
            if isinstance(file_contexts, dict):
                file_contexts = [file_contexts]
            if prompt:
                self._add_bubble(prompt, role="user")
                if files:
                    file_list = ", ".join([str(f) for f in files if f])
                    if file_list:
                        self._add_bubble(f"Files: {file_list}", role="files")
                for context_entry in file_contexts:
                    if not isinstance(context_entry, dict):
                        continue
                    context_name = str(context_entry.get("name") or "").strip() or "attached_context.txt"
                    context_text = str(context_entry.get("content") or "").strip()
                    if context_text:
                        self._add_bubble(f"{context_name}:\n{context_text}", role="files")
            if response:
                self._add_bubble(response, role="assistant")
        try:
            self._history_container.adjustSize()
            self._history_container.updateGeometry()
        except Exception:
            pass
        self._schedule_scroll()

    def _resolve_prompt_inputs(self, prompt_node_item):
        scene = self._ensure_scene()
        def _val(name: str) -> str:
            wired = _text_from_input(scene, prompt_node_item, name)
            param = _param_value_from_node(prompt_node_item, name)
            if (name or "").strip().lower() == "prompt":
                if wired and param:
                    return f"{param.strip()}\n\n{wired.strip()}"
                return wired or param
            return wired or param
        return _val

    def _build_history_prompt(self, history: list[dict], user_prompt: str) -> str:
        parts = []
        for entry in history:
            if not isinstance(entry, dict):
                continue
            prompt = (entry.get("prompt") or "").strip()
            response = (entry.get("response") or "").strip()
            if prompt:
                parts.append(f"User:\n{prompt}")
            if response:
                parts.append(f"Assistant:\n{response}")
        parts.append(f"User:\n{user_prompt}")
        parts.append("Assistant:")
        return "\n\n".join([p for p in parts if p]).strip()

    @QtCore.Slot(bool, str, str, str, str, str)
    def _finish_send(
        self,
        ok: bool,
        message: str,
        response_text: str,
        raw_response_text: str,
        user_prompt: str,
        history_context: str,
    ):
        self._set_sending(False)
        pending = (self._pending_voice_prompt or "").strip()
        self._pending_voice_prompt = ""
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Chatbot", message or "Request failed.")
            return
        clean_response = (response_text or "").strip()
        mediator_handled = False
        try:
            scene = self._ensure_scene()
            if scene is not None and (raw_response_text or response_text):
                mediator_handled = _dispatch_mediator_output(
                    scene,
                    self._node_item,
                    raw_response_text or response_text,
                    user_prompt,
                    history_context,
                )
        except Exception:
            mediator_handled = False
        clean_response = _mediator_handled_display_text(
            clean_response,
            raw_response_text or response_text,
            user_prompt,
            mediator_handled,
        )
        if clean_response:
            model = getattr(self._node_item, "model", None)
            if model is not None:
                try:
                    model.info = clean_response
                except Exception:
                    pass
                scene = self._ensure_scene()
                if scene is not None and hasattr(scene, "paramChanged"):
                    try:
                        scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
                    except Exception:
                        pass
        self._input.setText("")
        self._refresh_history()
        if pending:
            try:
                QtCore.QTimer.singleShot(0, lambda p=pending: self._submit_prompt(p, from_voice=True))
            except Exception:
                self._submit_prompt(pending, from_voice=True)

    def _submit_prompt(self, prompt_text: str, *, from_voice: bool = False):
        prompt_text = (prompt_text or "").strip()
        if not prompt_text:
            return
        if self._sending:
            if from_voice:
                self._pending_voice_prompt = prompt_text
            return
        if from_voice:
            self._input.setText(prompt_text)

        scene = self._ensure_scene()
        node_item = self._node_item
        if not scene or not node_item:
            return

        db_cfg = _connected_database(scene, node_item)
        if not db_cfg:
            QtWidgets.QMessageBox.warning(self, "Chatbot", "Connect a Database node to the 'database' input.")
            return
        if not (db_cfg.get("project") or "").strip():
            QtWidgets.QMessageBox.warning(self, "Chatbot", "Select or create a project on the connected Database node.")
            return

        mediator_node = _connected_mediator_node(scene, node_item)
        direct_response = _mediator_data_nexus_read_response(scene, node_item, prompt_text)
        if direct_response:
            try:
                gpt_spec._write_to_mongo(
                    db_cfg,
                    prompt_text,
                    direct_response,
                    {"source": "mediator_data_nexus_read"},
                    "mediator_data_nexus_read",
                    0.0,
                )
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "Chatbot", f"Failed to write Data Nexus read to history: {exc}")
                return
            self._finish_send(True, "", direct_response, direct_response, prompt_text, "")
            return

        prompt_node = _connected_prompt_node(scene, node_item)
        if not prompt_node:
            QtWidgets.QMessageBox.warning(self, "Chatbot", "Connect an LLM Prompt node to the 'llm_prompt' input.")
            return

        val = self._resolve_prompt_inputs(prompt_node)
        model_raw = (val("model") or "").strip()
        provider = gpt_spec._normalize_provider(val("provider"), model_raw)
        model = model_raw or (gpt_spec.DEFAULT_OLLAMA_MODEL if provider == "ollama" else gpt_spec.DEFAULT_MODEL)
        temperature = gpt_spec.DEFAULT_TEMPERATURE
        try:
            temperature = float((val("temperature") or "").strip() or gpt_spec.DEFAULT_TEMPERATURE)
        except Exception:
            temperature = gpt_spec.DEFAULT_TEMPERATURE

        api_key = ""
        ollama_url = ""
        if provider == "openai":
            api_key = (val("api_key") or "").strip() or (os.environ.get("OPENAI_API_KEY") or "").strip()
            if not api_key:
                QtWidgets.QMessageBox.warning(self, "Chatbot", "Provide an API key for the connected LLM Prompt node.")
                return
        else:
            ollama_url = gpt_spec._normalize_ollama_url(val("ollama_url"))

        system_prompt = (val("prompt") or "").strip()
        files_value = (val("files") or "").strip()
        contexts, _warnings, file_labels = gpt_spec._resolve_file_contexts(
            files_value,
            getattr(prompt_node, "model", None),
        )
        history_file_contexts = gpt_spec._history_file_contexts(contexts)

        try:
            history = _load_history(db_cfg)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Chatbot", f"Failed to load history: {exc}")
            return

        mediator_block = _mediator_agent_block(scene, mediator_node)
        history_prompt = self._build_history_prompt(history, prompt_text)
        parts = []
        if system_prompt:
            parts.append(f"System:\n{system_prompt}")
        if mediator_block:
            parts.append(mediator_block)
        file_block = _format_file_contexts(contexts)
        if file_block:
            parts.append(file_block)
        if history_prompt:
            parts.append(history_prompt)
        combined_prompt = "\n\n".join([p for p in parts if p]).strip()

        self._set_sending(True)

        def _worker():
            ok = False
            message = ""
            response_text = ""
            raw_response_text = ""
            try:
                if provider == "openai":
                    raw_response_text, raw_payload = gpt_spec._call_openai(api_key, model, temperature, combined_prompt)
                else:
                    raw_response_text, raw_payload = gpt_spec._call_ollama(ollama_url, model, temperature, combined_prompt)
                raw_response_text = (raw_response_text or "").strip()
                response_text = _display_response_text(raw_response_text) or raw_response_text
                gpt_spec._write_to_mongo(
                    db_cfg,
                    prompt_text,
                    response_text,
                    raw_payload,
                    model,
                    temperature,
                    files=file_labels,
                    file_contexts=history_file_contexts,
                )
                ok = True
            except Exception as exc:
                message = str(exc)
            QtCore.QMetaObject.invokeMethod(
                self,
                "_finish_send",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(bool, bool(ok)),
                QtCore.Q_ARG(str, message),
                QtCore.Q_ARG(str, response_text),
                QtCore.Q_ARG(str, raw_response_text),
                QtCore.Q_ARG(str, prompt_text),
                QtCore.Q_ARG(str, history_prompt),
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_send(self):
        self._submit_prompt(self._input.text() or "", from_voice=False)


def render_node_body(node_item, y_cursor: int) -> int:
    try:
        body = ChatbotWidget(node_item, None)
    except Exception as exc:
        print("[EchoGraph] Chatbot UI init failed:", exc)
        body = QtWidgets.QFrame()
        body.setStyleSheet(
            "QFrame{background:#0f1216;color:#e5e7eb;border:1px solid #334;border-radius:6px;}"
        )
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        msg = QtWidgets.QLabel(
            "Chatbot UI failed to load. Check console output for details."
        )
        msg.setWordWrap(True)
        msg.setStyleSheet("QLabel{color:#e5e7eb;}")
        lay.addWidget(msg, 1)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    h = body.sizeHint().height()
    try:
        pad = float(getattr(node_item, "_PADDING", 0))
        available = float(node_item.height) - float(y_cursor) - pad
        if available > h:
            h = int(available)
    except Exception:
        pass
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


CHATBOT_SPEC = Spec(
    stripe_color="#f97316",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
