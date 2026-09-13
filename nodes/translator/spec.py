from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echograph.qt_compat import QtCore, QtGui, QtWidgets
from nodes.core import Spec


TRANSLATOR_NODE_KIND = "translator"
TRANSLATOR_NODE_ALIASES = [
    "translate",
    "translation",
    "direct_translator",
    "direct translator",
    "voice_translator",
    "voice translator",
]
TRANSLATOR_NODE_KINDS = {TRANSLATOR_NODE_KIND, *TRANSLATOR_NODE_ALIASES}
TRANSLATOR_BODY_W = 430
TRANSLATOR_BODY_H = 224

TEXT_INPUT_PORT = "text"
PROMPT_INPUT_PORT = "llm_prompt"
TEXT_OUTPUT_PORT = "text"

TRANSLATOR_OUTPUT_TOKEN_KEY = "__translator_output_token"
TRANSLATOR_SESSION_TOKEN_KEY = "__translator_session_token"

_PARAM_SOURCE_LANG = "__translator_source_lang"
_PARAM_TARGET_LANG = "__translator_target_lang"
_PARAM_STYLE = "__translator_style"
_PARAM_AUTO = "__translator_auto"
_PARAM_DEBUG = "__translator_debug_log"
_PARAM_TRANSLATION = "translation"
_PARAM_STATUS = "__translator_status"
_HIDDEN_PARAM = "__ui_hidden_params"

_LANGUAGES = (
    ("Auto", "auto"),
    ("English", "English"),
    ("Mandarin Chinese", "Mandarin Chinese"),
    ("Japanese", "Japanese"),
    ("Korean", "Korean"),
    ("Spanish", "Spanish"),
    ("French", "French"),
    ("German", "German"),
)
_STYLES = (
    ("Natural", "natural"),
    ("Literal", "literal"),
)
_HIDDEN_PARAMS = (
    _PARAM_SOURCE_LANG,
    _PARAM_TARGET_LANG,
    _PARAM_STYLE,
    _PARAM_AUTO,
    _PARAM_DEBUG,
    _PARAM_TRANSLATION,
    _PARAM_STATUS,
    TRANSLATOR_OUTPUT_TOKEN_KEY,
    TRANSLATOR_SESSION_TOKEN_KEY,
)

_LIVE_TRANSLATOR_WORKERS: list[QtCore.QThread] = []


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _debug_log_path() -> Path:
    return _repo_root() / "logs" / "translator_debug.log"


def _preview(value: Any, limit: int = 180) -> str:
    text = str(value or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _debug_icon(active: bool = False) -> QtGui.QIcon:
    icon_name = "debug_002_Active_Icon_s.png" if active else "debug_002_Icon_s.png"
    path = _repo_root() / "icons" / icon_name
    if path.is_file():
        return QtGui.QIcon(str(path))
    return QtGui.QIcon()


def _append_debug_log(payload: dict[str, Any]) -> None:
    try:
        path = _debug_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = dict(payload)
        record.setdefault("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def _kind_of_item(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "kind", "") or "").strip().lower()


def _param_value(model, name: str, default: str = "") -> str:
    if model is None:
        return default
    key = str(name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and str(entry.get("name", "") or "").strip().lower() == key:
            return str(entry.get("value", "") or "")
    return default


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if not isinstance(params, list):
        params = list(params or [])
        model.params = params
    key = str(name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    hidden_entry = None
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "") or "").strip().lower() == _HIDDEN_PARAM:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": _HIDDEN_PARAM, "value": ""}
        params.append(hidden_entry)
    hidden = {part.strip().lower() for part in str(hidden_entry.get("value", "") or "").split(",") if part.strip()}
    for name in names or []:
        key = str(name or "").strip().lower()
        if key:
            hidden.add(key)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_param_value(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = str(name or "").strip().lower()
    text = str(value or "")
    changed = False
    found = False
    for entry in params:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "") or "").strip().lower() != key:
            continue
        found = True
        if str(entry.get("value", "") or "") != text:
            entry["value"] = text
            changed = True
        break
    if not found:
        params.append({"name": name, "value": text})
        changed = True
    model.params = params
    _ensure_hidden_params(model, _HIDDEN_PARAMS)
    if not changed or not notify_scene:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is not None and hasattr(scene, "paramChanged"):
        try:
            scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
        except Exception:
            pass


def _emit_param_changed(node_item) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None or not hasattr(scene, "paramChanged"):
        return
    try:
        scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
    except Exception:
        pass


def _publish_translation(node_item, text: str, *, source_signature: str = "") -> str:
    model = getattr(node_item, "model", None)
    if model is None:
        return ""
    clean = str(text or "").strip()
    model.info = clean
    token_seed = f"{source_signature}\n{clean}\n{time.time():.6f}\n{uuid.uuid4().hex}"
    token = f"{int(time.time() * 1000)}-{hashlib.sha1(token_seed.encode('utf-8', errors='ignore')).hexdigest()}"
    _set_param_value(node_item, _PARAM_TRANSLATION, clean, notify_scene=False)
    _set_param_value(node_item, TRANSLATOR_OUTPUT_TOKEN_KEY, token, notify_scene=False)
    _set_param_value(node_item, TRANSLATOR_SESSION_TOKEN_KEY, source_signature or token, notify_scene=False)
    _set_param_value(node_item, _PARAM_STATUS, "Translated.", notify_scene=False)
    _emit_param_changed(node_item)
    _poke_downstream_voice_actors(node_item)
    return token


def _edge_port_name(edge) -> str:
    for attr in ("dst_port_name", "dst_label", "dst_name"):
        try:
            value = getattr(edge, attr, "")
        except Exception:
            value = ""
        if value:
            return str(value or "")
    return ""


def _ordered_input_edges(scene, node_item, port_name: str = "") -> list:
    if scene is None or node_item is None:
        return []
    try:
        edges = list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            edges = list(scene._in_edges(node_item))
        except Exception:
            edges = []
    if not port_name:
        return edges
    target = str(port_name or "").strip().lower()
    matching = [edge for edge in edges if _edge_port_name(edge).strip().lower() == target]
    if matching:
        return matching
    if target == TEXT_INPUT_PORT:
        prompt_kinds = _prompt_node_kinds()
        text_edges = [
            edge
            for edge in edges
            if _kind_of_item(getattr(edge, "src", None)) not in prompt_kinds
        ]
        return text_edges
    if target == PROMPT_INPUT_PORT:
        prompt_kinds = _prompt_node_kinds()
        return [
            edge
            for edge in edges
            if _kind_of_item(getattr(edge, "src", None)) in prompt_kinds
        ]
    return []


def _ordered_output_edges(scene, node_item) -> list:
    if scene is None or node_item is None:
        return []
    try:
        edges = list(getattr(scene, "_edges", []) or [])
    except Exception:
        return []
    return [edge for edge in edges if getattr(edge, "src", None) is node_item]


def _prompt_node_kinds() -> set[str]:
    try:
        from nodes.gpt_prompt import spec as gpt_spec  # type: ignore
        return {
            str(kind or "").strip().lower()
            for kind in getattr(gpt_spec, "PROMPT_NODE_KINDS", set())
            if str(kind or "").strip()
        }
    except Exception:
        return {"llm_prompt", "llm prompt", "prompt"}


def _poke_downstream_voice_actors(node_item) -> None:
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    model = getattr(node_item, "model", None)
    changed_name = str(getattr(model, "name", "") or "")
    for edge in _ordered_output_edges(scene, node_item):
        dst = getattr(edge, "dst", None)
        if _kind_of_item(dst) not in {"voice_actor", "voice actor", "voiceactor"}:
            continue
        widget = getattr(dst, "_voice_actor_widget", None)
        if widget is None:
            continue

        def _trigger(target=widget, name=changed_name) -> None:
            for method_name in ("_ensure_scene_connections", "_refresh_source_param_options"):
                method = getattr(target, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass
            method = getattr(target, "_maybe_trigger_chatbot_auto", None)
            if callable(method):
                try:
                    method(name)
                except Exception:
                    pass

        try:
            QtCore.QTimer.singleShot(0, _trigger)
        except Exception:
            _trigger()


def _resolve_text_input(scene, node_item) -> tuple[str, str]:
    edges = _ordered_input_edges(scene, node_item, TEXT_INPUT_PORT)
    if not edges:
        return "", ""
    parts: list[str] = []
    tokens: list[str] = []
    for edge in edges:
        src = getattr(edge, "src", None)
        model = getattr(src, "model", None)
        text = ""
        try:
            text = str(scene.resolve_text_value(src) or "").strip()
        except Exception:
            text = ""
        if not text:
            text = str(getattr(model, "info", "") or "").strip()
        if text:
            parts.append(text)
        token = _source_token(model)
        if token:
            tokens.append(token)
    source_text = "\n\n".join(parts).strip()
    if tokens:
        return source_text, "|".join(tokens)
    if source_text:
        return source_text, f"text:{hashlib.sha1(source_text.encode('utf-8', errors='ignore')).hexdigest()}"
    return "", ""


def _resolve_prompt_input_text(scene, prompt_node) -> tuple[str, str]:
    if scene is None or prompt_node is None:
        return "", ""
    edges = _ordered_input_edges(scene, prompt_node, "prompt")
    if not edges:
        return "", ""
    parts: list[str] = []
    tokens: list[str] = []
    for edge in edges:
        src = getattr(edge, "src", None)
        model = getattr(src, "model", None)
        text = ""
        try:
            text = str(scene.resolve_text_value(src) or "").strip()
        except Exception:
            text = ""
        if not text:
            text = str(getattr(model, "info", "") or "").strip()
        if text:
            parts.append(text)
        token = _source_token(model)
        if token:
            tokens.append(token)
    source_text = "\n\n".join(parts).strip()
    if tokens:
        return source_text, "|".join(tokens)
    if source_text:
        digest = hashlib.sha1(source_text.encode("utf-8", errors="ignore")).hexdigest()
        return source_text, f"prompt_input:{digest}"
    return "", ""


def _source_token(model) -> str:
    if model is None:
        return ""
    for key in (
        "__voice_actor_send_token",
        "__chatbot_response_token",
        "__medigator_speech_token",
        "__medigator_output_token",
        TRANSLATOR_OUTPUT_TOKEN_KEY,
    ):
        value = _param_value(model, key, "").strip()
        if value:
            return value
    text = str(getattr(model, "info", "") or "").strip()
    if text:
        return f"info:{hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()}"
    return ""


def _connected_prompt_node(scene, node_item):
    try:
        from nodes.gpt_prompt import spec as gpt_spec  # type: ignore
    except Exception:
        return None
    for edge in _ordered_input_edges(scene, node_item, PROMPT_INPUT_PORT):
        src = getattr(edge, "src", None)
        if src is not None and _kind_of_item(src) in getattr(gpt_spec, "PROMPT_NODE_KINDS", set()):
            return src
    return None


def _prompt_config(prompt_node) -> dict[str, Any]:
    from nodes.gpt_prompt import spec as gpt_spec  # type: ignore

    model = getattr(prompt_node, "model", None)
    model_name = _param_value(model, "model", "") or gpt_spec.DEFAULT_MODEL
    provider = gpt_spec._normalize_provider(_param_value(model, "provider", ""), model_name)
    if provider == "ollama":
        model_name = model_name or getattr(gpt_spec, "DEFAULT_OLLAMA_MODEL", "deepseek-r1:14b")
    elif provider == "deepseek":
        model_name = model_name or "deepseek-chat"
    else:
        model_name = model_name or gpt_spec.DEFAULT_MODEL
    temperature = gpt_spec.DEFAULT_TEMPERATURE
    try:
        temperature = float(_param_value(model, "temperature", str(gpt_spec.DEFAULT_TEMPERATURE)) or gpt_spec.DEFAULT_TEMPERATURE)
    except Exception:
        pass
    api_key = _param_value(model, "api_key", "").strip()
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip() if provider == "deepseek" else os.environ.get("OPENAI_API_KEY", "").strip()
    return {
        "provider": provider,
        "model": model_name,
        "temperature": temperature,
        "api_key": api_key,
        "ollama_url": gpt_spec._normalize_ollama_url(_param_value(model, "ollama_url", "")),
        "instructions": _param_value(model, "prompt", "").strip(),
    }


def _prompt_signature(prompt_node) -> str:
    if prompt_node is None:
        return "prompt:none"
    try:
        from nodes.gpt_prompt import spec as gpt_spec  # type: ignore
        cfg = _prompt_config(prompt_node)
    except Exception:
        model = getattr(prompt_node, "model", None)
        raw = "|".join(
            [
                _param_value(model, "provider", ""),
                _param_value(model, "model", ""),
                _param_value(model, "ollama_url", ""),
                _param_value(model, "temperature", ""),
                _param_value(model, "prompt", ""),
            ]
        )
        return f"prompt:{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()}"
    key_hint = hashlib.sha1(str(cfg.get("api_key", "") or "").encode("utf-8", errors="ignore")).hexdigest()[:12]
    raw = "|".join(
        [
            str(cfg.get("provider", "")),
            str(cfg.get("model", "")),
            str(cfg.get("ollama_url", "")),
            str(cfg.get("temperature", "")),
            str(cfg.get("instructions", "")),
            key_hint,
        ]
    )
    return f"prompt:{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()}"


def _provider_label(provider: str) -> str:
    key = str(provider or "").strip().lower()
    if key == "deepseek":
        return "DeepSeek"
    if key == "ollama":
        return "Ollama"
    return "OpenAI"


def _language_label(value: str) -> str:
    clean = str(value or "").strip()
    if not clean or clean.lower() == "auto":
        return "auto-detected source language"
    return clean


def _build_translation_prompt(text: str, source_lang: str, target_lang: str, style: str, extra: str = "") -> str:
    source = _language_label(source_lang)
    target = str(target_lang or "English").strip() or "English"
    style_key = str(style or "natural").strip().lower()
    fidelity = (
        "Translate as literally as possible while keeping the target language grammatical."
        if style_key == "literal"
        else "Translate naturally for live conversation while preserving the exact meaning."
    )
    parts = [
        "You are a low-latency live speech translator.",
        f"Source language: {source}.",
        f"Target language: {target}.",
        fidelity,
        "Do not summarize, explain, answer, add context, or include labels.",
        "Return only the translated text to speak.",
    ]
    if extra:
        parts.append(f"Additional instructions:\n{extra}")
    parts.append(f"Text:\n{text}")
    return "\n".join(parts)


def translate_text(text: str, *, source_lang: str, target_lang: str, style: str, prompt_node) -> str:
    clean = str(text or "").strip()
    if not clean:
        raise ValueError("No text to translate.")
    if prompt_node is None:
        raise ValueError("Connect an LLM Prompt node to the translator llm_prompt input.")
    from nodes.gpt_prompt import spec as gpt_spec  # type: ignore

    cfg = _prompt_config(prompt_node)
    prompt = _build_translation_prompt(clean, source_lang, target_lang, style, cfg.get("instructions", ""))
    if cfg["provider"] == "openai":
        api_key = str(cfg.get("api_key", "") or "").strip()
        if not api_key:
            raise ValueError(
                f"Connected LLM Prompt is set to OpenAI model '{cfg.get('model', '')}'. "
                "Select DeepSeek on that LLM Prompt node or provide an OpenAI API key."
            )
        translated, _payload = gpt_spec._call_openai(api_key, str(cfg["model"]), float(cfg["temperature"]), prompt)
    elif cfg["provider"] == "deepseek":
        api_key = str(cfg.get("api_key", "") or "").strip()
        if not api_key:
            raise ValueError("Provide a DeepSeek API key on the connected LLM Prompt node or set DEEPSEEK_API_KEY.")
        translated, _payload = gpt_spec._call_deepseek(api_key, str(cfg["model"]), float(cfg["temperature"]), prompt)
    else:
        translated, _payload = gpt_spec._call_ollama(str(cfg["ollama_url"]), str(cfg["model"]), float(cfg["temperature"]), prompt)
    translated = str(translated or "").strip()
    if not translated:
        raise RuntimeError("Translator returned empty text.")
    return translated


@dataclass
class TranslatorJob:
    text: str
    source_signature: str
    source_lang: str
    target_lang: str
    style: str
    prompt_node: Any


class TranslatorWorker(QtCore.QThread):
    resultReady = QtCore.Signal(str, str, str)

    def __init__(self, job: TranslatorJob, parent=None):
        super().__init__(parent)
        self._job = job

    def run(self):
        try:
            out = translate_text(
                self._job.text,
                source_lang=self._job.source_lang,
                target_lang=self._job.target_lang,
                style=self._job.style,
                prompt_node=self._job.prompt_node,
            )
            self.resultReady.emit(out, self._job.source_signature, "")
        except Exception as exc:
            self.resultReady.emit("", self._job.source_signature, str(exc))


def build_ports(node_item) -> None:
    for name, default in (
        (_PARAM_SOURCE_LANG, "auto"),
        (_PARAM_TARGET_LANG, "Mandarin Chinese"),
        (_PARAM_STYLE, "natural"),
        (_PARAM_AUTO, "1"),
        (_PARAM_DEBUG, "0"),
        (_PARAM_TRANSLATION, ""),
        (_PARAM_STATUS, "Ready."),
        (TRANSLATOR_OUTPUT_TOKEN_KEY, ""),
        (TRANSLATOR_SESSION_TOKEN_KEY, ""),
    ):
        _ensure_param(node_item, name, default)
    _ensure_hidden_params(getattr(node_item, "model", None), _HIDDEN_PARAMS)
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input(TEXT_INPUT_PORT)
        node_item.ensure_input(PROMPT_INPUT_PORT)
    if hasattr(node_item, "ensure_output"):
        node_item.ensure_output(TEXT_OUTPUT_PORT)


class TranslatorWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._active = True
        self._scene = None
        self._scene_connected = False
        self._syncing = False
        self._worker: TranslatorWorker | None = None
        self._last_source_signature = ""
        self._pending_signature = ""
        self._rerun_after_current = False
        self._auto_timer = QtCore.QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._maybe_auto_translate)
        try:
            self.destroyed.connect(self._mark_inactive)
        except Exception:
            pass

        build_ports(node_item)
        self.setMinimumSize(TRANSLATOR_BODY_W, TRANSLATOR_BODY_H)
        self.setStyleSheet(
            "QWidget{background:#0f1216;color:#cbd5e1;}"
            "QComboBox,QPlainTextEdit{background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:3px 5px;}"
            "QCheckBox{color:#cbd5e1;}"
            "QPushButton{background:#155e75;color:#ecfeff;border:1px solid #22d3ee;border-radius:4px;padding:5px 8px;}"
            "QPushButton:disabled{background:#1f2937;color:#64748b;border-color:#334155;}"
        )

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(QtWidgets.QLabel("From"), 0)
        self._source_combo = QtWidgets.QComboBox()
        for label, value in _LANGUAGES:
            self._source_combo.addItem(label, value)
        row.addWidget(self._source_combo, 1)
        row.addWidget(QtWidgets.QLabel("To"), 0)
        self._target_combo = QtWidgets.QComboBox()
        for label, value in _LANGUAGES[1:]:
            self._target_combo.addItem(label, value)
        row.addWidget(self._target_combo, 1)
        self._debug_btn = QtWidgets.QToolButton()
        self._debug_btn.setCheckable(True)
        self._debug_btn.setFixedSize(24, 24)
        self._debug_btn.setToolTip("Write Translator diagnostics to logs/translator_debug.log")
        self._debug_btn.setIcon(_debug_icon(False))
        if self._debug_btn.icon().isNull():
            self._debug_btn.setText("D")
        self._debug_btn.setIconSize(QtCore.QSize(16, 16))
        self._debug_btn.setStyleSheet(
            "QToolButton{background:#111827;color:#cbd5e1;border:1px solid #334155;border-radius:4px;}"
            "QToolButton:checked{background:#164e63;border-color:#22d3ee;}"
        )
        row.addWidget(self._debug_btn, 0)
        root.addLayout(row, 0)

        opts = QtWidgets.QHBoxLayout()
        opts.setContentsMargins(0, 0, 0, 0)
        opts.setSpacing(6)
        self._style_combo = QtWidgets.QComboBox()
        for label, value in _STYLES:
            self._style_combo.addItem(label, value)
        self._auto_check = QtWidgets.QCheckBox("Auto")
        self._translate_btn = QtWidgets.QPushButton("Translate")
        opts.addWidget(QtWidgets.QLabel("Style"), 0)
        opts.addWidget(self._style_combo, 1)
        opts.addWidget(self._auto_check, 0)
        opts.addWidget(self._translate_btn, 0)
        root.addLayout(opts, 0)

        self._output = QtWidgets.QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText("Translation output")
        self._output.setFixedHeight(82)
        root.addWidget(self._output, 1)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        root.addWidget(self._status, 0)

        self._source_combo.currentIndexChanged.connect(self._commit_controls)
        self._target_combo.currentIndexChanged.connect(self._commit_controls)
        self._style_combo.currentIndexChanged.connect(self._commit_controls)
        self._auto_check.toggled.connect(self._commit_controls)
        self._debug_btn.toggled.connect(self._on_debug_toggled)
        self._translate_btn.clicked.connect(lambda: self._start_translation(force=True))

        self._refresh_from_params()
        self._ensure_scene_connections()
        self._schedule_auto_translate(0)

    def sizeHint(self):
        return QtCore.QSize(TRANSLATOR_BODY_W, TRANSLATOR_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(TRANSLATOR_BODY_W, TRANSLATOR_BODY_H)

    def _model(self):
        return getattr(self._node_item, "model", None)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status.setText(str(text or ""))
        self._status.setStyleSheet("QLabel{color:#fca5a5;font-size:11px;}" if error else "QLabel{color:#94a3b8;font-size:11px;}")

    def _mark_inactive(self, *_args) -> None:
        self._active = False
        try:
            self._auto_timer.stop()
        except Exception:
            pass
        self._worker = None

    def _schedule_auto_translate(self, delay_ms: int = 0) -> None:
        if not bool(getattr(self, "_active", True)):
            return
        try:
            self._auto_timer.start(max(0, int(delay_ms)))
        except Exception:
            pass

    def _set_combo_value(self, combo, value: str) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(max(0, idx))

    def _refresh_from_params(self) -> None:
        model = self._model()
        self._syncing = True
        try:
            self._set_combo_value(self._source_combo, _param_value(model, _PARAM_SOURCE_LANG, "auto") or "auto")
            self._set_combo_value(self._target_combo, _param_value(model, _PARAM_TARGET_LANG, "Mandarin Chinese") or "Mandarin Chinese")
            self._set_combo_value(self._style_combo, _param_value(model, _PARAM_STYLE, "natural") or "natural")
            self._auto_check.setChecked((_param_value(model, _PARAM_AUTO, "1") or "1") != "0")
            debug_value = (_param_value(model, _PARAM_DEBUG, "0") or "0").strip().lower()
            self._debug_btn.setChecked(debug_value not in {"", "0", "false", "off", "no"})
            self._debug_btn.setIcon(_debug_icon(self._debug_btn.isChecked()))
            if self._debug_btn.icon().isNull():
                self._debug_btn.setText("D")
            self._output.setPlainText(_param_value(model, _PARAM_TRANSLATION, "") or str(getattr(model, "info", "") or ""))
            self._set_status(_param_value(model, _PARAM_STATUS, "Ready.") or "Ready.")
            self._last_source_signature = _param_value(model, TRANSLATOR_SESSION_TOKEN_KEY, "")
        finally:
            self._syncing = False

    def _commit_controls(self) -> None:
        if self._syncing:
            return
        _set_param_value(self._node_item, _PARAM_SOURCE_LANG, str(self._source_combo.currentData() or "auto"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_TARGET_LANG, str(self._target_combo.currentData() or "Mandarin Chinese"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_STYLE, str(self._style_combo.currentData() or "natural"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_AUTO, "1" if self._auto_check.isChecked() else "0", notify_scene=False)
        _set_param_value(self._node_item, _PARAM_DEBUG, "1" if self._debug_btn.isChecked() else "0", notify_scene=False)

    def _debug_enabled(self) -> bool:
        try:
            return bool(self._debug_btn.isChecked())
        except Exception:
            return False

    def _on_debug_toggled(self, checked: bool) -> None:
        self._debug_btn.setIcon(_debug_icon(bool(checked)))
        if self._debug_btn.icon().isNull():
            self._debug_btn.setText("D")
        else:
            self._debug_btn.setText("")
        _set_param_value(self._node_item, _PARAM_DEBUG, "1" if checked else "0", notify_scene=False)
        if self._syncing:
            return
        if checked:
            self._write_debug("debug_enabled")

    def _edge_debug_rows(self, node_item, port_name: str = "") -> list[dict[str, str]]:
        rows = []
        for edge in _ordered_input_edges(self._scene, node_item, port_name):
            src = getattr(edge, "src", None)
            model = getattr(src, "model", None)
            rows.append(
                {
                    "src": str(getattr(model, "name", "") or ""),
                    "src_kind": str(getattr(model, "kind", "") or ""),
                    "dst_port": _edge_port_name(edge),
                    "src_info": _preview(getattr(model, "info", "") or ""),
                    "src_token": _preview(_source_token(model), 80),
                }
            )
        return rows

    def _write_debug(self, event: str, **extra) -> None:
        if not self._debug_enabled():
            return
        prompt_node = _connected_prompt_node(self._scene, self._node_item)
        text, signature = _resolve_text_input(self._scene, self._node_item)
        prompt_text, prompt_signature = _resolve_prompt_input_text(self._scene, prompt_node)
        prompt_model = getattr(prompt_node, "model", None)
        cfg = {}
        cfg_error = ""
        try:
            cfg = _prompt_config(prompt_node) if prompt_node is not None else {}
        except Exception as exc:
            cfg_error = str(exc)
        payload = {
            "event": event,
            "translator": str(getattr(getattr(self._node_item, "model", None), "name", "") or ""),
            "status": str(self._status.text() or ""),
            "source_lang": str(self._source_combo.currentData() or ""),
            "target_lang": str(self._target_combo.currentData() or ""),
            "auto": bool(self._auto_check.isChecked()),
            "text_edges": self._edge_debug_rows(self._node_item, TEXT_INPUT_PORT),
            "llm_prompt_edges": self._edge_debug_rows(self._node_item, PROMPT_INPUT_PORT),
            "resolved_text_preview": _preview(text),
            "resolved_text_signature": _preview(signature, 100),
            "prompt_input_text_preview": _preview(prompt_text),
            "prompt_input_signature": _preview(prompt_signature, 100),
            "prompt_node": str(getattr(prompt_model, "name", "") or ""),
            "prompt_kind": str(getattr(prompt_model, "kind", "") or ""),
            "prompt_provider": str(cfg.get("provider", "")),
            "prompt_model": str(cfg.get("model", "")),
            "prompt_api_key_present": bool(str(cfg.get("api_key", "") or "").strip()),
            "prompt_ollama_url": str(cfg.get("ollama_url", "")),
            "prompt_config_error": cfg_error,
        }
        payload.update(extra)
        _append_debug_log(payload)

    def _ensure_scene_connections(self) -> None:
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
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._on_scene_links_changed)
            except Exception:
                pass
        self._scene_connected = True

    def _on_scene_links_changed(self, *_args) -> None:
        self._ensure_scene_connections()
        self._write_debug("links_changed")
        self._schedule_auto_translate(150)

    def _on_scene_param_changed(self, name=None, _params=None) -> None:
        model = self._model()
        if model is not None and str(name or "").strip().lower() == str(getattr(model, "name", "") or "").strip().lower():
            return
        self._write_debug("param_changed", changed_node=str(name or ""))
        self._schedule_auto_translate(0)

    def _current_job(self) -> TranslatorJob | None:
        self._ensure_scene_connections()
        text, signature = _resolve_text_input(self._scene, self._node_item)
        prompt_node = _connected_prompt_node(self._scene, self._node_item)
        if not text:
            text, signature = _resolve_prompt_input_text(self._scene, prompt_node)
        if not text:
            return None
        prompt_sig = _prompt_signature(prompt_node)
        base_signature = signature
        if not base_signature:
            digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
            base_signature = f"text:{digest}"
        combined_signature = f"{base_signature}|{prompt_sig}"
        return TranslatorJob(
            text=text,
            source_signature=combined_signature,
            source_lang=str(self._source_combo.currentData() or "auto"),
            target_lang=str(self._target_combo.currentData() or "Mandarin Chinese"),
            style=str(self._style_combo.currentData() or "natural"),
            prompt_node=prompt_node,
        )

    def _maybe_auto_translate(self) -> None:
        if not bool(getattr(self, "_active", True)):
            return
        if not self._auto_check.isChecked():
            return
        if self._worker is not None:
            self._rerun_after_current = True
            return
        job = self._current_job()
        if job is None:
            self._write_debug("auto_no_job")
            return
        if job.source_signature and job.source_signature == self._last_source_signature:
            return
        self._start_translation(job=job, force=False)

    def _start_translation(self, *, job: TranslatorJob | None = None, force: bool = False) -> None:
        if self._worker is not None:
            self._set_status("Translation already running.")
            return
        self._commit_controls()
        job = job or self._current_job()
        if job is None:
            self._set_status("No text input to translate.", error=True)
            self._write_debug("manual_no_job")
            return
        if not force and job.source_signature and job.source_signature == self._last_source_signature:
            return
        self._pending_signature = job.source_signature
        provider_hint = ""
        try:
            cfg = _prompt_config(job.prompt_node)
            provider_hint = f"{_provider_label(str(cfg.get('provider', '')))} {cfg.get('model', '')}".strip()
        except Exception:
            provider_hint = ""
        self._write_debug(
            "translation_start",
            provider_hint=provider_hint,
            text_preview=_preview(job.text),
            source_signature=_preview(job.source_signature, 120),
        )
        self._set_busy(True)
        self._set_prompt_busy(job.prompt_node, True)
        self._set_status(f"Translating with {provider_hint}..." if provider_hint else "Translating...")
        worker = TranslatorWorker(job, None)
        self._worker = worker
        _LIVE_TRANSLATOR_WORKERS.append(worker)
        worker.resultReady.connect(self._on_translation_ready)
        worker.finished.connect(self._on_translation_finished)
        worker.finished.connect(lambda w=worker: _LIVE_TRANSLATOR_WORKERS.remove(w) if w in _LIVE_TRANSLATOR_WORKERS else None)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _set_busy(self, busy: bool) -> None:
        self._translate_btn.setEnabled(not busy)
        self._source_combo.setEnabled(not busy)
        self._target_combo.setEnabled(not busy)
        self._style_combo.setEnabled(not busy)
        self._auto_check.setEnabled(not busy)
        try:
            if hasattr(self._node_item, "setBusyState"):
                self._node_item.setBusyState(bool(busy), "translator" if busy else "")
        except Exception:
            pass

    def _set_prompt_busy(self, prompt_node, busy: bool) -> None:
        if prompt_node is None or not hasattr(prompt_node, "setBusyState"):
            return
        try:
            prompt_node.setBusyState(bool(busy), "llm" if busy else "")
        except Exception:
            pass

    @QtCore.Slot(str, str, str)
    def _on_translation_ready(self, text: str, signature: str, error: str) -> None:
        if not bool(getattr(self, "_active", True)):
            return
        if error:
            self._last_source_signature = str(signature or self._pending_signature or "")
            _set_param_value(self._node_item, _PARAM_STATUS, error, notify_scene=True)
            self._set_status(error, error=True)
            self._write_debug("translation_error", error=str(error), source_signature=_preview(signature, 120))
            return
        clean = str(text or "").strip()
        self._output.setPlainText(clean)
        self._last_source_signature = str(signature or self._pending_signature or "")
        _publish_translation(self._node_item, clean, source_signature=self._last_source_signature)
        self._set_status("Translated.")
        self._write_debug("translation_success", output_preview=_preview(clean), source_signature=_preview(signature, 120))

    def _on_translation_finished(self) -> None:
        if not bool(getattr(self, "_active", True)):
            return
        self._worker = None
        self._set_busy(False)
        self._set_prompt_busy(_connected_prompt_node(self._scene, self._node_item), False)
        rerun = bool(self._rerun_after_current)
        self._rerun_after_current = False
        if rerun:
            self._schedule_auto_translate(0)


def render_node_body(node_item, y_cursor: int) -> int:
    try:
        body = TranslatorWidget(node_item, None)
    except Exception as exc:
        print("[EchoGraph] Translator UI init failed:", exc)
        body = QtWidgets.QFrame()
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        msg = QtWidgets.QLabel("Translator UI failed to load.")
        msg.setWordWrap(True)
        layout.addWidget(msg)

    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    size_hint = body.sizeHint()
    minimum_hint = body.minimumSizeHint()
    h = max(int(size_hint.height()), int(minimum_hint.height()), TRANSLATOR_BODY_H)
    try:
        pad = float(getattr(node_item, "_PADDING", 0))
        available = float(node_item.height) - float(y_cursor) - pad
        if available > h:
            h = int(available)
        node_item.height = max(float(node_item.height), float(y_cursor) + float(h) + pad)
    except Exception:
        pass
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


TRANSLATOR_SPEC = Spec(
    stripe_color="#22c55e",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
