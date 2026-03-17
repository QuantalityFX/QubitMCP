from __future__ import annotations

import threading
from pathlib import Path

from nodes.core import Spec
from echograph.qt_compat import QtWidgets, QtCore, QtGui

try:
    import speech_recognition as sr  # type: ignore
except Exception:
    sr = None

try:
    import pyttsx3  # type: ignore
except Exception:
    pyttsx3 = None


VOICE_ACTOR_NODE_KIND = "voice_actor"
VOICE_ACTOR_NODE_ALIASES = [
    "voice actor",
    "voiceactor",
]

VOICE_ACTOR_BODY_W = 460
VOICE_ACTOR_BODY_H = 300
VOICE_ACTOR_SELECTED_PARAM_KEY = "__voice_actor_selected_param"
VOICE_ACTOR_MODE_KEY = "__voice_actor_mode"


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
            raw = getattr(edge, attr)
            if raw is not None:
                text = str(raw).strip()
                if text:
                    return text
    return ""


def _input_edges(scene, node_item, port_name: str) -> list:
    edges = _ordered_in_edges(scene, node_item)
    if not edges:
        return []
    target = (port_name or "").strip().lower()
    if not target:
        return edges
    out = []
    for edge in edges:
        if _edge_port_name(edge).strip().lower() == target:
            out.append(edge)
    if out:
        return out
    # Fall back to all incoming edges when no explicit port-name match exists.
    return edges


def _text_from_input(scene, node_item, port_name: str) -> str:
    named_edges = _input_edges(scene, node_item, port_name)
    if not named_edges:
        return ""

    parts = []
    for edge in named_edges:
        src = getattr(edge, "src", None)
        if src is None:
            continue
        try:
            text = scene.resolve_text_value(src)
        except Exception:
            text = ""
        if text:
            parts.append(text.strip())
    return "\n\n".join(parts).strip()


def _set_node_info(node_item, text: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    value = text or ""
    if (getattr(model, "info", "") or "") == value:
        return
    model.info = value
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return
    try:
        scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
    except Exception:
        pass


def _param_value(model, name: str, default: str = "") -> str:
    if model is None:
        return default
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value", "") or "")
    return default


def _ensure_hidden_params(model, names) -> bool:
    if model is None:
        return False
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    existing = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            existing = entry
            break
    changed = False
    if existing is None:
        existing = {"name": store_key, "value": ""}
        params.append(existing)
        changed = True
    raw = str(existing.get("value", "") or "")
    hidden = {part.strip().lower() for part in raw.split(",") if part.strip()}
    for name in names or []:
        key = str(name or "").strip().lower()
        if key and key not in hidden:
            hidden.add(key)
            changed = True
    packed = ",".join(sorted(hidden))
    if str(existing.get("value", "") or "") != packed:
        existing["value"] = packed
        changed = True
    if changed:
        model.params = params
    return changed


def _set_node_param(node_item, name: str, value: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    changed = False
    found = False
    clean_value = str(value or "")
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            found = True
            if str(entry.get("value", "") or "") != clean_value:
                entry["value"] = clean_value
                changed = True
            break
    if not found:
        params.append({"name": name, "value": clean_value})
        changed = True
    model.params = params
    hidden_changed = _ensure_hidden_params(model, [name])
    if not changed and not hidden_changed:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return
    try:
        scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
    except Exception:
        pass


def _copy_icon() -> QtGui.QIcon:
    try:
        icon_path = Path(__file__).resolve().parents[2] / "icons" / "Copy_Icon.png"
        if icon_path.exists():
            icon = QtGui.QIcon(str(icon_path))
            if not icon.isNull():
                return icon
    except Exception:
        pass
    return QtGui.QIcon()


def _voice_action_icon(filename: str) -> QtGui.QIcon:
    try:
        icon_path = Path(__file__).resolve().parents[2] / "icons" / filename
        if icon_path.exists():
            icon = QtGui.QIcon(str(icon_path))
            if not icon.isNull():
                return icon
    except Exception:
        pass
    return QtGui.QIcon()


def _button_style(background: str, border: str, hover: str) -> str:
    return (
        f"QPushButton{{background:{background};color:#f8fafc;border:1px solid {border};"
        "border-radius:4px;padding:4px 10px;}"
        f"QPushButton:hover{{background:{hover};}}"
        "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#334155;}"
    )


def _format_stt_error(exc: Exception) -> str:
    if sr is not None:
        wait_timeout = getattr(sr, "WaitTimeoutError", None)
        unknown = getattr(sr, "UnknownValueError", None)
        request = getattr(sr, "RequestError", None)
        if wait_timeout and isinstance(exc, wait_timeout):
            return "Listening timed out before speech was detected."
        if unknown and isinstance(exc, unknown):
            return "Speech was captured but could not be understood."
        if request and isinstance(exc, request):
            return f"Speech recognition service failed: {exc}"
    if isinstance(exc, OSError):
        return f"Microphone error: {exc}"
    return f"Speech capture failed: {exc}"


def build_ports(node_item) -> None:
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("text")


class VoiceActorWidget(QtWidgets.QWidget):
    _stt_done = QtCore.Signal(str, str)
    _tts_done = QtCore.Signal(str)

    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._busy = False
        self._syncing_text = False
        self._listen_icon = _voice_action_icon("Mic_Icon.png")
        self._speak_icon = _voice_action_icon("Voice_Icon.png")
        self._scene = None
        self._scene_connected = False
        self._param_refresh_pending = False
        self._syncing_param_combo = False
        self._source_param_options = []
        model = getattr(self._node_item, "model", None)
        saved_mode = _param_value(model, VOICE_ACTOR_MODE_KEY, "").strip().lower()
        self._mode = "text_to_voice" if saved_mode == "text_to_voice" else "voice_to_text"
        self._selected_param_key = _param_value(model, VOICE_ACTOR_SELECTED_PARAM_KEY, "")
        self._had_note_input = False
        if self._selected_param_key:
            _ensure_hidden_params(model, [VOICE_ACTOR_SELECTED_PARAM_KEY])
        if saved_mode:
            _ensure_hidden_params(model, [VOICE_ACTOR_MODE_KEY])

        self.setMinimumSize(VOICE_ACTOR_BODY_W, VOICE_ACTOR_BODY_H)
        try:
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass

        self._mode_btn = QtWidgets.QPushButton()
        self._mode_btn.clicked.connect(self._toggle_mode)

        self._action_btn = QtWidgets.QPushButton()
        self._action_btn.setIconSize(QtCore.QSize(14, 14))
        self._action_btn.clicked.connect(self._on_action_clicked)

        self._copy_btn = QtWidgets.QPushButton("Copy")
        self._copy_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:4px 8px;}"
            "QPushButton:hover{background:#273449;}"
        )
        copy_icon = _copy_icon()
        if not copy_icon.isNull():
            self._copy_btn.setIcon(copy_icon)
            self._copy_btn.setIconSize(QtCore.QSize(14, 14))
        self._copy_btn.clicked.connect(self._copy_transcript)

        self._transcript = QtWidgets.QPlainTextEdit()
        self._transcript.setPlaceholderText(
            "Transcript appears here in Voice -> Text mode.\n"
            "In Text -> Voice mode, this text is spoken if no input is wired."
        )
        self._transcript.setStyleSheet(
            "QPlainTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #334;"
            "border-radius:6px;padding:6px;}"
        )
        self._transcript.textChanged.connect(self._on_transcript_changed)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("QLabel{color:#94a3b8;}")

        self._param_label = QtWidgets.QLabel("Read param:")
        self._param_label.setStyleSheet("QLabel{color:#94a3b8;}")
        self._param_combo = QtWidgets.QComboBox()
        self._param_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:3px 8px;}"
            "QComboBox:disabled{background:#1f2937;color:#94a3b8;border-color:#334155;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        self._param_combo.setToolTip("Pick which connected parameter to speak in Text -> Voice mode.")
        self._param_combo.currentIndexChanged.connect(self._on_param_selection_changed)

        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self._mode_btn, 1)
        top.addWidget(self._action_btn, 0)
        top.addWidget(self._copy_btn, 0)

        selector = QtWidgets.QHBoxLayout()
        selector.setContentsMargins(0, 0, 0, 0)
        selector.setSpacing(6)
        selector.addWidget(self._param_label, 0)
        selector.addWidget(self._param_combo, 1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(top, 0)
        layout.addLayout(selector, 0)
        layout.addWidget(self._transcript, 1)
        layout.addWidget(self._status, 0)

        self._stt_done.connect(self._finish_stt)
        self._tts_done.connect(self._finish_tts)

        initial_text = (getattr(getattr(self._node_item, "model", None), "info", "") or "").strip()
        if initial_text:
            self._set_transcript(initial_text)
        self._apply_mode_ui()
        self._ensure_scene_connections()
        self._refresh_source_param_options()

    def sizeHint(self):
        return QtCore.QSize(VOICE_ACTOR_BODY_W, VOICE_ACTOR_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(VOICE_ACTOR_BODY_W, VOICE_ACTOR_BODY_H)

    def _apply_mode_ui(self) -> None:
        if self._mode == "voice_to_text":
            self._mode_btn.setText("Voice -> Text")
            self._mode_btn.setStyleSheet(_button_style("#4d1616", "#6a2222", "#5a1b1b"))
            self._action_btn.setText("Listen")
            self._action_btn.setStyleSheet(_button_style("#6b1f1f", "#8b2b2b", "#7a2323"))
            self._action_btn.setIcon(self._listen_icon)
            self._action_btn.setToolTip("Listen on microphone and transcribe speech.")
        else:
            self._mode_btn.setText("Text -> Voice")
            self._mode_btn.setStyleSheet(_button_style("#1e3a8a", "#3b5bb0", "#23459f"))
            self._action_btn.setText("Speak")
            self._action_btn.setStyleSheet(_button_style("#1d4ed8", "#60a5fa", "#2563eb"))
            self._action_btn.setIcon(self._speak_icon)
            self._action_btn.setToolTip("Speak text from input or transcript box.")

    def _set_mode(self, mode: str, *, persist: bool = True) -> None:
        normalized = "text_to_voice" if str(mode or "").strip().lower() == "text_to_voice" else "voice_to_text"
        if self._mode != normalized:
            self._mode = normalized
            self._apply_mode_ui()
        if persist:
            _set_node_param(self._node_item, VOICE_ACTOR_MODE_KEY, self._mode)

    def _ensure_scene_connections(self) -> None:
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
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

    def _on_scene_links_changed(self, *_args) -> None:
        self._schedule_param_refresh()

    def _on_scene_param_changed(self, *_args) -> None:
        self._schedule_param_refresh()

    def _schedule_param_refresh(self) -> None:
        self._ensure_scene_connections()
        if self._param_refresh_pending:
            return
        self._param_refresh_pending = True
        QtCore.QTimer.singleShot(0, self._refresh_source_param_options)

    def _collect_source_param_options(self) -> tuple[list, bool]:
        self._ensure_scene_connections()
        scene = self._scene
        if scene is None:
            return [], False
        edges = _input_edges(scene, self._node_item, "text")
        if not edges:
            return [], False
        multiple_sources = len(edges) > 1
        options = []
        note_input_connected = False
        for edge_index, edge in enumerate(edges, start=1):
            src_item = getattr(edge, "src", None)
            src_model = getattr(src_item, "model", None)
            if src_model is None:
                continue
            kind = str(getattr(src_model, "kind", "") or "").strip().lower()
            if kind == "note":
                note_input_connected = True
            source_name = str(getattr(src_model, "name", "") or "").strip() or f"Input {edge_index}"
            source_key = str(getattr(src_model, "name", "") or source_name).strip().lower()
            for param_index, entry in enumerate(getattr(src_model, "params", None) or []):
                if not isinstance(entry, dict):
                    continue
                param_name = str(entry.get("name", "") or "").strip()
                if not param_name:
                    continue
                key_name = param_name.lower()
                if key_name == "__ui_hidden_params" or key_name.startswith("__"):
                    continue
                value = str(entry.get("value", "") or "")
                label = f"[{param_index}] {param_name}"
                if multiple_sources:
                    label = f"{source_name} - {label}"
                if not value.strip():
                    label = f"{label} (empty)"
                option_key = f"{source_key}::{param_index}::{key_name}"
                options.append(
                    {
                        "key": option_key,
                        "label": label,
                        "value": value,
                    }
                )
        return options, note_input_connected

    def _refresh_source_param_options(self) -> None:
        self._param_refresh_pending = False
        options, note_connected = self._collect_source_param_options()
        if note_connected and not self._had_note_input and self._mode != "text_to_voice":
            self._set_mode("text_to_voice")
        self._had_note_input = note_connected
        self._source_param_options = options
        current_key = str(self._selected_param_key or "").strip()
        if current_key and not any(str(opt.get("key", "")) == current_key for opt in options):
            current_key = ""
            self._selected_param_key = ""
            _set_node_param(self._node_item, VOICE_ACTOR_SELECTED_PARAM_KEY, "")

        self._syncing_param_combo = True
        try:
            self._param_combo.blockSignals(True)
            self._param_combo.clear()
            self._param_combo.addItem("Auto (wired text / transcript)", "")
            for opt in options:
                self._param_combo.addItem(str(opt.get("label", "")), str(opt.get("key", "")))

            selected_index = 0
            if current_key:
                for idx in range(self._param_combo.count()):
                    if str(self._param_combo.itemData(idx) or "") == current_key:
                        selected_index = idx
                        break
            self._param_combo.setCurrentIndex(selected_index)
        finally:
            self._param_combo.blockSignals(False)
            self._syncing_param_combo = False

        self._param_combo.setEnabled(bool(options) and not self._busy)
        if options:
            self._param_combo.setToolTip("Pick which connected parameter to speak in Text -> Voice mode.")
        else:
            self._param_combo.setToolTip("Connect a node with parameters to choose a value for speech.")

    def _on_param_selection_changed(self, _index: int) -> None:
        if self._syncing_param_combo:
            return
        selected_key = str(self._param_combo.currentData() or "").strip()
        if selected_key == str(self._selected_param_key or "").strip():
            return
        self._selected_param_key = selected_key
        _set_node_param(self._node_item, VOICE_ACTOR_SELECTED_PARAM_KEY, selected_key)

    def _selected_source_param_value(self) -> tuple[str, str]:
        selected_key = str(self._selected_param_key or "").strip()
        if not selected_key:
            return "", ""
        for opt in self._source_param_options:
            if str(opt.get("key", "")) == selected_key:
                return str(opt.get("value", "") or ""), str(opt.get("label", "") or "")
        return "", ""

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status.setText(message or "")
        if error:
            self._status.setStyleSheet("QLabel{color:#fca5a5;}")
        else:
            self._status.setStyleSheet("QLabel{color:#94a3b8;}")

    def _set_busy(self, active: bool, label: str = "") -> None:
        self._busy = bool(active)
        self._mode_btn.setEnabled(not self._busy)
        self._action_btn.setEnabled(not self._busy)
        self._copy_btn.setEnabled(not self._busy)
        self._param_combo.setEnabled(bool(self._source_param_options) and not self._busy)
        try:
            self._node_item.setBusyState(bool(active), label if active else "")
        except Exception:
            pass

    def _set_transcript(self, text: str) -> None:
        value = text or ""
        try:
            self._syncing_text = True
            self._transcript.blockSignals(True)
            if self._transcript.toPlainText() != value:
                self._transcript.setPlainText(value)
        finally:
            self._transcript.blockSignals(False)
            self._syncing_text = False
        _set_node_info(self._node_item, value)

    def _toggle_mode(self) -> None:
        if self._busy:
            return
        if self._mode == "voice_to_text":
            self._set_mode("text_to_voice")
        else:
            self._set_mode("voice_to_text")

    def _on_transcript_changed(self) -> None:
        if self._syncing_text:
            return
        _set_node_info(self._node_item, self._transcript.toPlainText())

    def _copy_transcript(self) -> None:
        text = (self._transcript.toPlainText() or "").strip()
        if not text:
            self._set_status("Transcript is empty.", error=True)
            return
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is None:
            self._set_status("Clipboard is unavailable.", error=True)
            return
        clipboard.setText(text)
        self._set_status("Transcript copied.")

    def _on_action_clicked(self) -> None:
        if self._busy:
            return
        self._refresh_source_param_options()
        if self._mode == "voice_to_text":
            self._start_stt()
        else:
            self._start_tts()

    def _start_stt(self) -> None:
        if sr is None:
            self._set_status(
                "Missing dependency: SpeechRecognition. Install: pip install SpeechRecognition pyaudio",
                error=True,
            )
            return
        self._set_busy(True, "listening")
        self._set_status("Listening...")
        threading.Thread(target=self._stt_worker, daemon=True).start()

    def _stt_worker(self) -> None:
        transcript = ""
        error = ""
        try:
            recognizer = sr.Recognizer()
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.4)
                audio = recognizer.listen(source, timeout=8, phrase_time_limit=20)
            transcript = (recognizer.recognize_google(audio) or "").strip()
            if not transcript:
                error = "Speech detected but transcript was empty."
        except Exception as exc:
            error = _format_stt_error(exc)
        self._stt_done.emit(transcript, error)

    @QtCore.Slot(str, str)
    def _finish_stt(self, transcript: str, error: str) -> None:
        self._set_busy(False, "")
        if error:
            self._set_status(error, error=True)
            return
        self._set_transcript(transcript)
        self._set_status("Transcript updated.")

    def _resolve_tts_text(self) -> tuple[str, str]:
        self._refresh_source_param_options()
        selected_key = str(self._selected_param_key or "").strip()
        if selected_key:
            value, _label = self._selected_source_param_value()
            if value.strip():
                return value.strip(), "selected_param"
            return "", "selected_param"

        scene = self._node_item.scene()
        wired = _text_from_input(scene, self._node_item, "text")
        if wired.strip():
            return wired.strip(), "input"
        local = (self._transcript.toPlainText() or "").strip()
        return local, "transcript"

    def _start_tts(self) -> None:
        if pyttsx3 is None:
            self._set_status(
                "Missing dependency: pyttsx3. Install: pip install pyttsx3",
                error=True,
            )
            return
        text, source = self._resolve_tts_text()
        if not text:
            if source == "selected_param":
                self._set_status(
                    "Selected parameter is empty. Choose another parameter or fill its value.",
                    error=True,
                )
            else:
                self._set_status("No text to speak. Connect input 'text' or type transcript.", error=True)
            return
        if source == "selected_param":
            self._set_status("Speaking selected parameter value...")
        elif source == "input":
            self._set_status("Speaking text from wired input...")
        else:
            self._set_status("Speaking transcript...")
        self._set_busy(True, "speaking")
        threading.Thread(target=self._tts_worker, args=(text,), daemon=True).start()

    def _tts_worker(self, text: str) -> None:
        error = ""
        try:
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
            try:
                engine.stop()
            except Exception:
                pass
        except Exception as exc:
            error = f"Text-to-speech failed: {exc}"
        self._tts_done.emit(error)

    @QtCore.Slot(str)
    def _finish_tts(self, error: str) -> None:
        self._set_busy(False, "")
        if error:
            self._set_status(error, error=True)
            return
        self._set_status("Speech playback complete.")


def render_node_body(node_item, y_cursor: int) -> int:
    try:
        body = VoiceActorWidget(node_item, None)
    except Exception as exc:
        print("[EchoGraph] Voice Actor UI init failed:", exc)
        body = QtWidgets.QFrame()
        body.setStyleSheet(
            "QFrame{background:#0f1216;color:#e5e7eb;border:1px solid #334;border-radius:6px;}"
        )
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        msg = QtWidgets.QLabel(
            "Voice Actor UI failed to load. Check console output for details."
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


VOICE_ACTOR_SPEC = Spec(
    stripe_color="#0ea5e9",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
