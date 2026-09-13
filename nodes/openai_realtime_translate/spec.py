from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
from typing import Any

from echograph.qt_compat import QtCore, QtGui, QtWidgets
from nodes.core import Spec

try:
    import pyaudio  # type: ignore
except Exception:
    pyaudio = None

try:
    import websockets  # type: ignore
except Exception:
    websockets = None


OPENAI_REALTIME_TRANSLATE_NODE_KIND = "openai_realtime_translate"
OPENAI_REALTIME_TRANSLATE_NODE_ALIASES = [
    "openai realtime translate",
    "openai realtime translation",
    "realtime translate",
    "realtime translation",
]
OPENAI_REALTIME_TRANSLATE_NODE_KINDS = {
    OPENAI_REALTIME_TRANSLATE_NODE_KIND,
    *OPENAI_REALTIME_TRANSLATE_NODE_ALIASES,
}
OPENAI_REALTIME_TRANSLATE_BODY_W = 480
OPENAI_REALTIME_TRANSLATE_BODY_H = 360

_PARAM_API_KEY = "__openai_realtime_api_key"
_PARAM_MODEL = "__openai_realtime_model"
_PARAM_SOURCE = "__openai_realtime_source_language"
_PARAM_TARGET = "__openai_realtime_target_language"
_PARAM_SOURCE_2 = "__openai_realtime_source_language_2"
_PARAM_TARGET_2 = "__openai_realtime_target_language_2"
_PARAM_BIDIRECTIONAL = "__openai_realtime_bidirectional"
_PARAM_VOICE = "__openai_realtime_voice"
_PARAM_SILENCE = "__openai_realtime_silence_ms"
_PARAM_STATUS = "__openai_realtime_status"
_PARAM_TEXT = "realtime_translation"
_PARAM_TOKEN = "__openai_realtime_output_token"
_HIDDEN_PARAM = "__ui_hidden_params"

_LANGUAGES = (
    ("None", ""),
    ("English", "English"),
    ("Mandarin Chinese", "Mandarin Chinese"),
    ("Japanese", "Japanese"),
    ("Korean", "Korean"),
    ("Spanish", "Spanish"),
    ("French", "French"),
    ("German", "German"),
)
_DEFAULT_MODEL = "gpt-realtime"
_LEGACY_TRANSLATE_MODEL = "gpt-realtime-translate"
_SAMPLE_RATE = 24000
_CHANNELS = 1
_SAMPLE_WIDTH = 2
_BLOCK_FRAMES = 480
_HIDDEN_PARAMS = (
    _PARAM_API_KEY,
    _PARAM_MODEL,
    _PARAM_SOURCE,
    _PARAM_TARGET,
    _PARAM_SOURCE_2,
    _PARAM_TARGET_2,
    _PARAM_BIDIRECTIONAL,
    _PARAM_VOICE,
    _PARAM_SILENCE,
    _PARAM_STATUS,
    _PARAM_TEXT,
    _PARAM_TOKEN,
)


def _microphone_icon() -> QtGui.QIcon:
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "icons", "Mic_Icon.png"))
    return QtGui.QIcon(path) if os.path.isfile(path) else QtGui.QIcon()


class _RevealOnFocusLineEdit(QtWidgets.QLineEdit):
    def focusInEvent(self, event) -> None:
        self.setEchoMode(QtWidgets.QLineEdit.Normal)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self.setEchoMode(QtWidgets.QLineEdit.Password)
        super().focusOutEvent(event)


def _param_value(model, name: str, default: str = "") -> str:
    key = str(name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and str(entry.get("name", "")).strip().lower() == key:
            return str(entry.get("value", "") or "")
    return default


def _ensure_param(node_item, name: str, default: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = str(name).strip().lower()
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "")).strip().lower() == key:
            return
    params.append({"name": name, "value": default})
    model.params = params


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    hidden_entry = next(
        (
            entry for entry in params
            if isinstance(entry, dict)
            and str(entry.get("name", "")).strip().lower() == _HIDDEN_PARAM
        ),
        None,
    )
    if hidden_entry is None:
        hidden_entry = {"name": _HIDDEN_PARAM, "value": ""}
        params.append(hidden_entry)
    hidden = {
        item.strip().lower()
        for item in str(hidden_entry.get("value", "") or "").split(",")
        if item.strip()
    }
    hidden.update(str(name).strip().lower() for name in names if str(name).strip())
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_param(node_item, name: str, value: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = str(name).strip().lower()
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "")).strip().lower() == key:
            entry["value"] = str(value or "")
            break
    else:
        params.append({"name": name, "value": str(value or "")})
    model.params = params
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is not None and hasattr(scene, "paramChanged"):
        try:
            scene.paramChanged.emit(model.name, params)
        except Exception:
            pass


def _set_info(node_item, text: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    clean = str(text or "").strip()
    model.info = clean
    _set_param(node_item, _PARAM_TEXT, clean)
    _set_param(node_item, _PARAM_TOKEN, str(int(QtCore.QDateTime.currentMSecsSinceEpoch())))


def _selected_mic_device_index(node_item) -> int | None:
    """Use the same microphone index selected in Qubit's top-bar settings."""
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    raw = None
    if scene is not None:
        raw = getattr(scene, "_voice_actor_mic_device_index", None)
        if raw in (None, "", "none", "default", "system", "auto", -1, "-1"):
            settings = getattr(scene, "_view_settings", None)
            if isinstance(settings, dict):
                raw = settings.get("voice_mic_device_index")
    if raw in (None, "", "none", "default", "system", "auto", -1, "-1"):
        return None
    try:
        index = int(float(str(raw).strip()))
    except Exception:
        return None
    return index if index >= 0 else None


class RealtimeTranslateWorker(QtCore.QThread):
    statusChanged = QtCore.Signal(str)
    textChanged = QtCore.Signal(str)

    def __init__(self, config: dict[str, Any], parent=None):
        super().__init__(parent)
        self.config = config
        self.stop_event = threading.Event()
        self._response_active = False
        self._response_pending = False

    def stop(self) -> None:
        self.stop_event.set()

    def _status(self, text: str) -> None:
        self.statusChanged.emit(str(text or ""))

    async def _connect(self):
        headers = {"Authorization": f"Bearer {self.config['api_key']}"}
        url = f"wss://api.openai.com/v1/realtime?model={self.config['model']}"
        try:
            return await websockets.connect(url, additional_headers=headers)
        except TypeError:
            return await websockets.connect(url, extra_headers=headers)

    async def _send_audio(self, ws, stream) -> None:
        while not self.stop_event.is_set():
            if self._response_active:
                # Do not feed the microphone while the translated audio is playing.
                # This prevents speaker bleed/loopback from becoming a new turn.
                await asyncio.sleep(0.02)
                continue
            data = await asyncio.to_thread(
                stream.read, _BLOCK_FRAMES, exception_on_overflow=False
            )
            if self._response_active:
                continue
            await ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(data).decode("ascii"),
            }))

    async def _receive_audio(self, ws, speaker) -> None:
        async for raw in ws:
            if self.stop_event.is_set():
                return
            event = json.loads(raw)
            event_type = str(event.get("type", ""))
            if event_type == "session.updated":
                self._status("Connected. Listening...")
            elif event_type == "input_audio_buffer.speech_started":
                self._status("Speech detected...")
            elif event_type == "input_audio_buffer.speech_stopped":
                self._status("Translating...")
                if self._response_active:
                    self._response_pending = True
                else:
                    await self._request_response(ws)
            elif event_type == "response.created":
                self._response_active = True
                self._status("Speaking translation...")
            elif event_type in {"response.done", "response.cancelled", "response.failed"}:
                self._response_active = False
                if self._response_pending and not self.stop_event.is_set():
                    self._response_pending = False
                    await self._request_response(ws)
                else:
                    self._status("Listening...")
            if event_type in {"response.output_audio.delta", "response.audio.delta"}:
                audio = base64.b64decode(str(event.get("delta", "")))
                if audio:
                    await asyncio.to_thread(speaker.write, audio)
            elif event_type in {
                "response.output_audio_transcript.delta",
                "response.audio_transcript.delta",
            }:
                self.textChanged.emit(str(event.get("delta", "")))
            elif event_type in {
                "response.output_audio_transcript.done",
                "response.audio_transcript.done",
            }:
                transcript = str(event.get("transcript", ""))
                if transcript:
                    self.textChanged.emit(transcript)
            elif event_type == "error":
                error = event.get("error") or event
                message = str(error.get("message", error))
                if "active response" in message.lower() and "in progress" in message.lower():
                    # A second speaker/turn arrived before the current response ended.
                    # Keep the session alive and let response.done trigger the queued turn.
                    self._response_active = True
                    self._response_pending = True
                    self._status("Waiting for current translation...")
                    continue
                self._response_active = False
                raise RuntimeError(message)

    async def _request_response(self, ws) -> None:
        self._response_active = True
        await ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "output_modalities": ["audio"],
                "instructions": self._translation_instructions(),
            },
        }))

    async def _watch_stop(self, ws) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(0.05)
        try:
            await ws.close()
        except Exception:
            pass

    async def _run_async(self) -> None:
        if pyaudio is None:
            raise RuntimeError("OpenAI Realtime Translate needs PyAudio. Run setup.bat.")
        if websockets is None:
            raise RuntimeError("OpenAI Realtime Translate needs websockets. Install: pip install websockets")
        pa = pyaudio.PyAudio()
        microphone = None
        speaker = None
        ws = None
        try:
            microphone = pa.open(
                format=pyaudio.paInt16,
                channels=_CHANNELS,
                rate=_SAMPLE_RATE,
                input=True,
                frames_per_buffer=_BLOCK_FRAMES,
                **(
                    {"input_device_index": int(self.config["device_index"])}
                    if self.config.get("device_index") not in (None, "", "none")
                    else {}
                ),
            )
            speaker = pa.open(
                format=pyaudio.paInt16,
                channels=_CHANNELS,
                rate=_SAMPLE_RATE,
                output=True,
                frames_per_buffer=_BLOCK_FRAMES,
            )
            self._status("Connecting...")
            ws = await self._connect()
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": self._translation_instructions(),
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": _SAMPLE_RATE},
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": int(self.config["silence_ms"]),
                                "create_response": False,
                                "interrupt_response": True,
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": _SAMPLE_RATE},
                            "voice": self.config["voice"],
                        },
                    },
                },
            }))
            self._status("Listening...")
            sender = asyncio.create_task(self._send_audio(ws, microphone))
            receiver = asyncio.create_task(self._receive_audio(ws, speaker))
            stopper = asyncio.create_task(self._watch_stop(ws))
            done, pending = await asyncio.wait(
                {sender, receiver, stopper}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if task is not stopper:
                    task.result()
        finally:
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
            for stream in (microphone, speaker):
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
            pa.terminate()

    def _translation_instructions(self) -> str:
        route_text = ""
        if self.config.get("bidirectional") == "1" and self.config.get("source_2") and self.config.get("target_2"):
            route_text = (
                f"There are exactly two language routes. Route 1: {self.config['source']} -> {self.config['target']}. "
                f"Route 2: {self.config['source_2']} -> {self.config['target_2']}. "
                f"Detect the language of each utterance independently. {self.config['source']} MUST produce only {self.config['target']}; "
                f"{self.config['source_2']} MUST produce only {self.config['target_2']}. "
                "Never use the target from the other route and never repeat the source language. "
            )
        else:
            route_text = (
                f"There is one language route: {self.config['source']} -> {self.config['target']}. "
                f"Translate only {self.config['source']} into {self.config['target']}. "
            )
        return (
            "You are an automatic simultaneous interpreter. Do not behave like an assistant. "
            + route_text
            + "Do not ask the user to speak, do not acknowledge them, and do not describe what you are doing. "
            "Speak only the translated words. Never repeat the source, answer questions, summarize, explain, "
            "add commentary, or use quotation marks. Never say 'Sure', 'Okay, let me translate', "
            "'please go ahead', 'I understand', or 'what was said'. If the audio is silence, noise, or unintelligible, produce no audio."
        )

    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            if not self.stop_event.is_set():
                self._status(f"Error: {exc}")
        finally:
            if self.stop_event.is_set():
                self._status("Stopped.")


class OpenAIRealtimeTranslateWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self.node_item = node_item
        self.worker = None
        self._text = ""
        self.setMinimumSize(OPENAI_REALTIME_TRANSLATE_BODY_W, OPENAI_REALTIME_TRANSLATE_BODY_H)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(5)

        self.api_key = _RevealOnFocusLineEdit()
        self.api_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key.setPlaceholderText("OpenAI API key")
        self.model = QtWidgets.QLineEdit()
        self.model.setPlaceholderText(_DEFAULT_MODEL)
        self.source = QtWidgets.QComboBox()
        self.target = QtWidgets.QComboBox()
        self.source_2 = QtWidgets.QComboBox()
        self.target_2 = QtWidgets.QComboBox()
        for label, value in _LANGUAGES:
            self.source.addItem(label, value)
            self.target.addItem(label, value)
            self.source_2.addItem(label, value)
            self.target_2.addItem(label, value)
        self.bidirectional = QtWidgets.QCheckBox("Bidirectional")
        self.bidirectional.setChecked(True)
        self.voice = QtWidgets.QComboBox()
        self.voice.addItems(["marin", "cedar"])
        self.silence = QtWidgets.QSpinBox()
        self.silence.setRange(200, 3000)
        self.silence.setSuffix(" ms")
        for label, widget in (("API key", self.api_key), ("Model", self.model), ("Voice", self.voice), ("Silence", self.silence)):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(label))
            row.addWidget(widget, 1)
            root.addLayout(row)
        language_row = QtWidgets.QHBoxLayout()
        language_row.addWidget(QtWidgets.QLabel("From"))
        language_row.addWidget(self.source, 1)
        language_row.addSpacing(8)
        language_row.addWidget(QtWidgets.QLabel("To"))
        language_row.addWidget(self.target, 1)
        root.addLayout(language_row)
        self.route2_row = QtWidgets.QWidget()
        language_row_2 = QtWidgets.QHBoxLayout(self.route2_row)
        language_row_2.setContentsMargins(0, 0, 0, 0)
        language_row_2.addWidget(QtWidgets.QLabel("From"))
        language_row_2.addWidget(self.source_2, 1)
        language_row_2.addSpacing(8)
        language_row_2.addWidget(QtWidgets.QLabel("To"))
        language_row_2.addWidget(self.target_2, 1)
        root.addWidget(self.route2_row)
        self.add_route_btn = QtWidgets.QPushButton("+")
        self.add_route_btn.setFixedWidth(34)
        self.add_route_btn.setToolTip("Add another language route.")
        root.addWidget(self.add_route_btn)
        self.action_btn = QtWidgets.QPushButton("Listen")
        self.action_btn.setIcon(_microphone_icon())
        self.action_btn.setIconSize(QtCore.QSize(16, 16))
        self.action_btn.setMinimumSize(220, 48)
        self.action_btn.setMaximumWidth(220)
        self.action_btn.setToolTip("Start live microphone translation.")
        self.action_btn.setStyleSheet(
            "QPushButton{background:#1d4ed8;color:#fff;border:1px solid #60a5fa;border-radius:8px;padding:8px 14px;}"
            "QPushButton:hover{background:#2563eb;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#475569;border-radius:8px;}"
        )
        root.addWidget(self.action_btn, 0, QtCore.Qt.AlignHCenter)
        self.status = QtWidgets.QLabel("Stopped.")
        self.status.setWordWrap(True)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Live translation transcript")
        self.output.setMaximumHeight(75)
        root.addWidget(self.status)
        root.addWidget(self.output, 1)

        self.action_btn.clicked.connect(self._toggle)
        self.add_route_btn.clicked.connect(self._show_second_route)
        self._load()

    def _load(self) -> None:
        model = self.node_item.model
        self.api_key.setText(_param_value(model, _PARAM_API_KEY, os.environ.get("OPENAI_API_KEY", "")))
        saved_model = _param_value(model, _PARAM_MODEL, _DEFAULT_MODEL)
        if saved_model.strip().lower() == _LEGACY_TRANSLATE_MODEL:
            saved_model = _DEFAULT_MODEL
        self.model.setText(saved_model)
        self._select(self.source, _param_value(model, _PARAM_SOURCE, "English"))
        self._select(self.target, _param_value(model, _PARAM_TARGET, "Mandarin Chinese"))
        source_2 = _param_value(model, _PARAM_SOURCE_2, "")
        target_2 = _param_value(model, _PARAM_TARGET_2, "")
        self._select(self.source_2, source_2)
        self._select(self.target_2, target_2)
        self.route2_row.setVisible(bool(source_2 and target_2))
        self.add_route_btn.setVisible(not bool(source_2 and target_2))
        self.bidirectional.setChecked(_param_value(model, _PARAM_BIDIRECTIONAL, "1").strip().lower() not in {"0", "false", "no", "off"})
        self._select(self.voice, _param_value(model, _PARAM_VOICE, "marin"))
        self.silence.setValue(int(float(_param_value(model, _PARAM_SILENCE, "500") or 500)))

    @staticmethod
    def _select(combo, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _show_second_route(self) -> None:
        self.route2_row.setVisible(True)
        self.add_route_btn.setVisible(False)

    def _config(self) -> dict[str, Any]:
        return {
            "api_key": self.api_key.text().strip(),
            "model": self.model.text().strip() or _DEFAULT_MODEL,
            "source": str(self.source.currentData()),
            "target": str(self.target.currentData()),
            "source_2": str(self.source_2.currentData()),
            "target_2": str(self.target_2.currentData()),
            "bidirectional": "1" if self.bidirectional.isChecked() else "0",
            "voice": str(self.voice.currentText()),
            "silence_ms": str(self.silence.value()),
            "device_index": _selected_mic_device_index(self.node_item),
        }

    def _save(self, config: dict[str, Any]) -> None:
        for name, value in ((_PARAM_API_KEY, config["api_key"]), (_PARAM_MODEL, config["model"]), (_PARAM_SOURCE, config["source"]), (_PARAM_TARGET, config["target"]), (_PARAM_SOURCE_2, config["source_2"]), (_PARAM_TARGET_2, config["target_2"]), (_PARAM_BIDIRECTIONAL, config["bidirectional"]), (_PARAM_VOICE, config["voice"]), (_PARAM_SILENCE, config["silence_ms"])):
            _set_param(self.node_item, name, value)

    def start(self) -> None:
        if self.worker is not None:
            return
        config = self._config()
        if not config["api_key"]:
            self.status.setText("Enter an OpenAI API key.")
            return
        self._save(config)
        self._text = ""
        self.output.clear()
        self.worker = RealtimeTranslateWorker(config)
        self.worker.statusChanged.connect(self.status.setText)
        self.worker.textChanged.connect(self._append_text)
        self.worker.finished.connect(self._finished)
        self.worker.start()
        self.action_btn.setText("Stop")
        self.action_btn.setIcon(_microphone_icon())
        self.action_btn.setToolTip("Stop live microphone translation.")
        self.action_btn.setStyleSheet(
            "QPushButton{background:#b91c1c;color:#fff;border:1px solid #f87171;border-radius:8px;padding:8px 14px;}"
            "QPushButton:hover{background:#dc2626;}"
            "QPushButton:disabled{background:#7f1d1d;color:#fecaca;border-color:#991b1b;border-radius:8px;}"
        )

    def stop(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.action_btn.setEnabled(False)
            self.status.setText("Stopping...")

    def _toggle(self) -> None:
        if self.worker is None:
            self.start()
        else:
            self.stop()

    def _finished(self) -> None:
        worker = self.worker
        self.worker = None
        self.action_btn.setEnabled(True)
        self.action_btn.setText("Listen")
        self.action_btn.setIcon(_microphone_icon())
        self.action_btn.setToolTip("Start live microphone translation.")
        self.action_btn.setStyleSheet(
            "QPushButton{background:#1d4ed8;color:#fff;border:1px solid #60a5fa;border-radius:8px;padding:8px 14px;}"
            "QPushButton:hover{background:#2563eb;}"
        )
        if worker is not None:
            worker.deleteLater()

    def _append_text(self, text: str) -> None:
        incoming = str(text or "")
        if not incoming:
            return
        # Delta events are followed by a completed event containing the full transcript.
        # Replace that full value instead of speaking/displaying it twice.
        if incoming == self._text:
            return
        if self._text and incoming.startswith(self._text):
            self._text = incoming
        else:
            self._text += incoming
        self.output.setPlainText(self._text[-4000:])
        _set_info(self.node_item, self._text)

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)


def build_ports(node_item) -> None:
    defaults = {
        _PARAM_API_KEY: "",
        _PARAM_MODEL: _DEFAULT_MODEL,
        _PARAM_SOURCE: "English",
        _PARAM_TARGET: "Mandarin Chinese",
        _PARAM_SOURCE_2: "",
        _PARAM_TARGET_2: "",
        _PARAM_BIDIRECTIONAL: "1",
        _PARAM_VOICE: "marin",
        _PARAM_SILENCE: "500",
        _PARAM_STATUS: "Stopped.",
        _PARAM_TEXT: "",
        _PARAM_TOKEN: "",
    }
    for name, value in defaults.items():
        _ensure_param(node_item, name, value)
    _ensure_hidden_params(getattr(node_item, "model", None), _HIDDEN_PARAMS)
    if hasattr(node_item, "ensure_output"):
        node_item.ensure_output("audio")
        node_item.ensure_output("text")


def render_node_body(node_item, y_cursor: int) -> int:
    build_ports(node_item)
    body = OpenAIRealtimeTranslateWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    width = max(OPENAI_REALTIME_TRANSLATE_BODY_W, int(hint.width()))
    height = max(OPENAI_REALTIME_TRANSLATE_BODY_H, int(hint.height()))
    proxy.resize(width, height)
    node_item.width = max(float(getattr(node_item, "width", 0) or 0), float(width))
    node_item.height = max(float(getattr(node_item, "height", 0) or 0), float(y_cursor + height + 8))
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + height


OPENAI_REALTIME_TRANSLATE_SPEC = Spec(
    stripe_color="#10b981",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
