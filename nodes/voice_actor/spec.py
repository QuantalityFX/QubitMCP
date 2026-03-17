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

    named_edges = [edge for edge in in_edges if _edge_matches_name(edge)]
    if not named_edges:
        return ""
    try:
        ordered = [edge for edge in scene._ordered_in_edges(node_item) if edge in named_edges]
        if ordered:
            named_edges = ordered
    except Exception:
        pass

    parts = []
    for edge in named_edges:
        try:
            text = scene.resolve_text_value(edge.src)
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
        self._mode = "voice_to_text"
        self._busy = False
        self._syncing_text = False
        self._listen_icon = _voice_action_icon("Mic_Icon.png")
        self._speak_icon = _voice_action_icon("Voice_Icon.png")

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

        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self._mode_btn, 1)
        top.addWidget(self._action_btn, 0)
        top.addWidget(self._copy_btn, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(top, 0)
        layout.addWidget(self._transcript, 1)
        layout.addWidget(self._status, 0)

        self._stt_done.connect(self._finish_stt)
        self._tts_done.connect(self._finish_tts)

        initial_text = (getattr(getattr(self._node_item, "model", None), "info", "") or "").strip()
        if initial_text:
            self._set_transcript(initial_text)
        self._apply_mode_ui()

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
            self._mode = "text_to_voice"
        else:
            self._mode = "voice_to_text"
        self._apply_mode_ui()

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
            self._set_status("No text to speak. Connect input 'text' or type transcript.", error=True)
            return
        if source == "input":
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
