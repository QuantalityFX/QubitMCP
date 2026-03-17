from __future__ import annotations

import contextlib
import io
import threading
import tempfile
import time
import uuid
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

try:
    from gtts import gTTS  # type: ignore
except Exception:
    gTTS = None

try:
    import pygame  # type: ignore
except Exception:
    pygame = None

try:
    from faster_whisper import WhisperModel  # type: ignore
except Exception:
    WhisperModel = None

try:
    from pymongo import MongoClient  # type: ignore
except Exception:
    MongoClient = None


VOICE_ACTOR_NODE_KIND = "voice_actor"
VOICE_ACTOR_NODE_ALIASES = [
    "voice actor",
    "voiceactor",
]

VOICE_ACTOR_BODY_W = 460
VOICE_ACTOR_BODY_H = 300
VOICE_ACTOR_SELECTED_PARAM_KEY = "__voice_actor_selected_param"
VOICE_ACTOR_MODE_KEY = "__voice_actor_mode"
VOICE_ACTOR_VOICE_KEY = "__voice_actor_voice"
VOICE_ACTOR_STT_METHOD_KEY = "__voice_actor_stt_method"
VOICE_TANYA_GOOGLE = "__google_tanya__"
VOICE_AUTO_FEMALE = "__auto_female__"
STT_METHOD_LOCAL_WHISPER = "local_whisper"
STT_METHOD_GOOGLE = "google"
LOCAL_WHISPER_MODEL_NAME = "base"
LOCAL_WHISPER_LANGUAGE = "en"
CHATBOT_NODE_KINDS = {"chatbot", "chat bot", "chat_bot"}
DATABASE_NODE_KINDS = {"database"}
CHATBOT_DB_NAME = "my_database"
CHATBOT_DEFAULT_COLLECTION = "EchoGragh"
FEMALE_VOICE_HINTS = (
    "female",
    "woman",
    "zira",
    "hazel",
    "aria",
    "sarah",
    "susan",
    "allison",
    "ava",
    "sonia",
    "jenny",
    "emma",
)
MALE_VOICE_HINTS = (
    "male",
    "man",
    "david",
    "mark",
    "george",
    "james",
    "richard",
    "guy",
)

_WHISPER_MODEL = None
_WHISPER_MODEL_LOCK = threading.Lock()


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


def _kind_of_item(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "kind", "") or "").strip().lower()


def _node_name(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "name", "") or "").strip().lower()


def _find_upstream_chatbot(scene, node_item, max_depth: int = 6, _visited=None):
    if not scene or node_item is None:
        return None
    if _kind_of_item(node_item) in CHATBOT_NODE_KINDS:
        return node_item
    if max_depth <= 0:
        return None
    if _visited is None:
        _visited = set()
    marker = id(node_item)
    if marker in _visited:
        return None
    _visited.add(marker)
    for edge in _ordered_in_edges(scene, node_item):
        src = getattr(edge, "src", None)
        if src is None:
            continue
        found = _find_upstream_chatbot(scene, src, max_depth=max_depth - 1, _visited=_visited)
        if found is not None:
            return found
    return None


def _python_inputs(scene, python_item) -> tuple[dict, str]:
    inputs = {}
    primary_input = ""
    for idx, edge in enumerate(_ordered_in_edges(scene, python_item), start=1):
        src = getattr(edge, "src", None)
        if src is None:
            continue
        try:
            txt = scene.resolve_text_value(src)
        except Exception:
            txt = ""
        text = str(txt or "").strip()
        if not text:
            continue
        if not primary_input:
            primary_input = text
        src_name = _node_name(src) or f"in{idx}"
        inputs.setdefault(src_name, text)
        inputs.setdefault(f"in{idx}", text)
    return inputs, primary_input


def _run_python_transform(scene, python_item) -> tuple[str, str]:
    model = getattr(python_item, "model", None)
    if model is None:
        return "", "Python transform node is unavailable."
    src = str(getattr(model, "code", "") or "").strip()
    if not src:
        return "", "Python transform node has no code."

    params_map = {}
    raw_params = list(getattr(model, "params", None) or [])
    for idx, p in enumerate(raw_params):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or f"param{idx+1}")
        params_map[name] = p.get("value", "")

    inputs, primary_input = _python_inputs(scene, python_item)
    ns = {
        "__name__": "__echograph_exec__",
        "node": model,
        "params": params_map,
        "raw_params": raw_params,
        "inputs": inputs,
        "primary_input": primary_input,
        "graph_scene": scene,
    }
    try:
        ns["QtCore"] = QtCore
        ns["QtGui"] = QtGui
        ns["QtWidgets"] = QtWidgets
    except Exception:
        pass

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            exec(src, ns, ns)
    except Exception as exc:
        return "", f"Python transform execution failed: {exc}"

    stderr_text = err_buf.getvalue().strip()
    if stderr_text:
        return "", f"Python transform error: {stderr_text}"

    out_text = out_buf.getvalue().strip()
    output_text = ns.get("output_text", None)
    if output_text is None:
        output_text = ns.get("result", None)
    if output_text is None:
        output_text = getattr(model, "info", "")
    if output_text is None:
        output_text = out_text
    clean = str(output_text or "").strip()

    changed = False
    if clean and str(getattr(model, "info", "") or "") != clean:
        try:
            model.info = clean
            changed = True
        except Exception:
            pass

    if changed and scene is not None and hasattr(scene, "paramChanged"):
        try:
            scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
        except Exception:
            pass

    if not clean:
        return "", "Python transform produced empty output."
    return clean, ""


def _param_from_item(node_item, name: str, default: str = "") -> str:
    model = getattr(node_item, "model", None)
    return _param_value(model, name, default)


def _find_input_node(scene, node_item, port_names: set[str], kind_set: set[str] | None = None):
    if not scene or not node_item:
        return None
    try:
        in_edges = list(scene._in_edges(node_item))
    except Exception:
        in_edges = []
    wanted_ports = {str(p or "").strip().lower() for p in (port_names or set()) if str(p or "").strip()}
    for edge in in_edges:
        dst_port = _edge_port_name(edge).strip().lower()
        if wanted_ports and dst_port not in wanted_ports:
            continue
        src = getattr(edge, "src", None)
        if src is None:
            continue
        if kind_set is None:
            return src
        if _kind_of_item(src) in kind_set:
            return src
    if kind_set:
        for edge in in_edges:
            src = getattr(edge, "src", None)
            if src is None:
                continue
            if _kind_of_item(src) in kind_set:
                return src
    return None


def _chatbot_db_config(scene, chatbot_item):
    db_item = _find_input_node(scene, chatbot_item, {"database", "db"}, DATABASE_NODE_KINDS)
    if db_item is None:
        return None
    return {
        "mongo_uri": _param_from_item(db_item, "mongo_uri", "mongodb://localhost:27017") or "mongodb://localhost:27017",
        "project": _param_from_item(db_item, "project", ""),
        "collection": _param_from_item(db_item, "collection", CHATBOT_DEFAULT_COLLECTION) or CHATBOT_DEFAULT_COLLECTION,
    }


def _latest_chatbot_response(scene, chatbot_item) -> tuple[str, str, str]:
    if scene is None or chatbot_item is None:
        return "", "", "Chatbot input is unavailable."
    if MongoClient is None:
        return "", "", "Missing dependency: pymongo. Install: pip install pymongo"
    cfg = _chatbot_db_config(scene, chatbot_item)
    if not cfg:
        return "", "", "Connected chatbot has no database input."
    project = str(cfg.get("project", "") or "").strip()
    if not project:
        return "", "", "Connected chatbot database has no selected project."
    uri = str(cfg.get("mongo_uri", "") or "").strip() or "mongodb://localhost:27017"
    collection_name = str(cfg.get("collection", "") or "").strip() or CHATBOT_DEFAULT_COLLECTION

    client = None
    try:
        client = MongoClient(uri)
        coll = client[CHATBOT_DB_NAME][collection_name]
        doc = coll.find_one({"$or": [{"name": project}, {"project": project}]}) or {}
        history = doc.get("history") or []
        if not isinstance(history, list) or not history:
            return "", "", "No chatbot history found in database."
        for idx in range(len(history) - 1, -1, -1):
            entry = history[idx]
            if not isinstance(entry, dict):
                continue
            response = str(entry.get("response", "") or "").strip()
            if not response:
                continue
            stamp = str(entry.get("timestamp", "") or f"{idx}")
            token = f"{stamp}|{idx}|{len(history)}"
            return response, token, ""
        return "", "", "No assistant response found in chatbot history."
    except Exception as exc:
        return "", "", f"Failed to read chatbot history: {exc}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _pick_female_voice_id(engine) -> str:
    if engine is None:
        return ""
    try:
        voices = list(engine.getProperty("voices") or [])
    except Exception:
        voices = []
    best_id = ""
    best_score = -999.0
    for idx, voice in enumerate(voices):
        name = str(getattr(voice, "name", "") or "")
        voice_id = str(getattr(voice, "id", "") or "")
        langs = getattr(voice, "languages", None) or []
        lang_text = " ".join([str(x or "") for x in langs])
        blob = f"{name} {voice_id} {lang_text}".strip().lower()
        if not blob:
            continue
        score = 0.0
        for hint in FEMALE_VOICE_HINTS:
            if hint in blob:
                score += 2.0
        for hint in MALE_VOICE_HINTS:
            if hint in blob:
                score -= 2.0
        if "english" in blob or " en" in blob:
            score += 0.25
        score -= float(idx) * 0.0001
        if score > best_score:
            best_score = score
            best_id = voice_id
    if best_score <= 0.0:
        return ""
    return best_id


def _available_tts_voices() -> list[dict]:
    if pyttsx3 is None:
        return []
    engine = None
    voices = []
    try:
        engine = pyttsx3.init()
        raw = list(engine.getProperty("voices") or [])
        for voice in raw:
            voice_id = str(getattr(voice, "id", "") or "").strip()
            if not voice_id:
                continue
            name = str(getattr(voice, "name", "") or "").strip() or voice_id
            voices.append({"id": voice_id, "name": name})
    except Exception:
        voices = []
    finally:
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
    return voices


def _normalize_stt_method(value: str) -> str:
    key = str(value or "").strip().lower()
    if key == STT_METHOD_GOOGLE:
        return STT_METHOD_GOOGLE
    return STT_METHOD_LOCAL_WHISPER


def _whisper_model():
    if WhisperModel is None:
        return None, "Missing dependency: faster-whisper. Run setup.bat or pip install faster-whisper."
    global _WHISPER_MODEL
    with _WHISPER_MODEL_LOCK:
        if _WHISPER_MODEL is not None:
            return _WHISPER_MODEL, ""
        try:
            _WHISPER_MODEL = WhisperModel(
                LOCAL_WHISPER_MODEL_NAME,
                device="cpu",
                compute_type="int8",
            )
        except Exception as exc:
            return None, f"Local Whisper init failed: {exc}"
        return _WHISPER_MODEL, ""


def _transcribe_local_whisper(audio_wav: bytes) -> tuple[str, str]:
    model, err = _whisper_model()
    if err or model is None:
        return "", err or "Local Whisper model is unavailable."
    wav_path = Path(tempfile.gettempdir()) / f"voice_actor_stt_{uuid.uuid4().hex}.wav"
    try:
        wav_path.write_bytes(audio_wav)
        segments, _info = model.transcribe(
            str(wav_path),
            language=LOCAL_WHISPER_LANGUAGE,
            vad_filter=True,
        )
        parts = []
        for segment in segments:
            text = str(getattr(segment, "text", "") or "").strip()
            if text:
                parts.append(text)
        transcript = " ".join(parts).strip()
        if not transcript:
            return "", "Speech detected but transcript was empty."
        return transcript, ""
    except Exception as exc:
        return "", f"Local Whisper transcription failed: {exc}"
    finally:
        try:
            wav_path.unlink()
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


def _auto_button_style() -> str:
    return (
        "QPushButton{background:#ca8a04;color:#111827;border:1px solid #facc15;"
        "border-radius:4px;padding:4px 10px;}"
        "QPushButton:hover{background:#d97706;}"
        "QPushButton:disabled{background:#a16207;color:#fde68a;border-color:#d97706;}"
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
        self._chatbot_icon = _voice_action_icon("Chatbot_Icon.png")
        self._replay_icon = _voice_action_icon("PlayButton_icon.png")
        self._stop_icon = _voice_action_icon("StopButton_icon.png")
        self._scene = None
        self._scene_connected = False
        self._param_refresh_pending = False
        self._syncing_param_combo = False
        self._source_param_options = []
        self._chatbot_connected = False
        self._chatbot_input_item = None
        self._chatbot_proxy_item = None
        self._chatbot_via_proxy = False
        self._last_chatbot_token = ""
        self._pending_chatbot_text = ""
        self._last_tts_text = ""
        self._processing_chatbot_auto = False
        self._tts_lock = threading.Lock()
        self._tts_engine = None
        self._tts_playing = False
        self._tts_user_stopped = False
        self._tts_voice_options = []
        self._syncing_voice_combo = False
        self._syncing_stt_combo = False
        model = getattr(self._node_item, "model", None)
        saved_mode = _param_value(model, VOICE_ACTOR_MODE_KEY, "").strip().lower()
        self._mode = "text_to_voice" if saved_mode == "text_to_voice" else "voice_to_text"
        self._selected_param_key = _param_value(model, VOICE_ACTOR_SELECTED_PARAM_KEY, "")
        saved_voice_key = _param_value(model, VOICE_ACTOR_VOICE_KEY, "").strip()
        self._selected_voice_key = saved_voice_key or VOICE_TANYA_GOOGLE
        saved_stt_method = _param_value(model, VOICE_ACTOR_STT_METHOD_KEY, "").strip()
        self._selected_stt_method = _normalize_stt_method(saved_stt_method or STT_METHOD_LOCAL_WHISPER)
        self._had_note_input = False
        if self._selected_param_key:
            _ensure_hidden_params(model, [VOICE_ACTOR_SELECTED_PARAM_KEY])
        if saved_mode:
            _ensure_hidden_params(model, [VOICE_ACTOR_MODE_KEY])
        if saved_voice_key:
            _ensure_hidden_params(model, [VOICE_ACTOR_VOICE_KEY])
        if saved_stt_method:
            _ensure_hidden_params(model, [VOICE_ACTOR_STT_METHOD_KEY])

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

        self._replay_btn = QtWidgets.QPushButton("Replay")
        self._replay_btn.setStyleSheet(
            "QPushButton{background:#334155;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:4px 8px;}"
            "QPushButton:hover{background:#3f4d62;}"
            "QPushButton:disabled{background:#1f2937;color:#64748b;border-color:#334155;}"
        )
        if not self._replay_icon.isNull():
            self._replay_btn.setIcon(self._replay_icon)
            self._replay_btn.setIconSize(QtCore.QSize(14, 14))
        self._replay_btn.clicked.connect(self._replay_last)

        self._stop_btn = QtWidgets.QPushButton("Stop")
        self._stop_btn.setStyleSheet(
            "QPushButton{background:#7f1d1d;color:#f8fafc;border:1px solid #b91c1c;"
            "border-radius:4px;padding:4px 8px;}"
            "QPushButton:hover{background:#991b1b;}"
            "QPushButton:disabled{background:#1f2937;color:#64748b;border-color:#334155;}"
        )
        if not self._stop_icon.isNull():
            self._stop_btn.setIcon(self._stop_icon)
            self._stop_btn.setIconSize(QtCore.QSize(14, 14))
        self._stop_btn.clicked.connect(self._stop_audio)

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

        self._param_label = QtWidgets.QLabel("Read:")
        self._param_label.setStyleSheet("QLabel{color:#94a3b8;}")
        self._param_combo = QtWidgets.QComboBox()
        self._param_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:3px 8px;}"
            "QComboBox:disabled{background:#1f2937;color:#94a3b8;border-color:#334155;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        self._param_combo.setToolTip("Pick which connected parameter to speak in Text -> Voice mode.")
        self._param_combo.currentIndexChanged.connect(self._on_param_selection_changed)

        self._voice_label = QtWidgets.QLabel("Voice:")
        self._voice_label.setStyleSheet("QLabel{color:#94a3b8;}")
        self._voice_combo = QtWidgets.QComboBox()
        self._voice_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:3px 8px;}"
            "QComboBox:disabled{background:#1f2937;color:#94a3b8;border-color:#334155;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        self._voice_combo.setToolTip("Select which installed speech voice to use.")
        self._voice_combo.currentIndexChanged.connect(self._on_voice_selection_changed)

        self._stt_label = QtWidgets.QLabel("STT:")
        self._stt_label.setStyleSheet("QLabel{color:#94a3b8;}")
        self._stt_combo = QtWidgets.QComboBox()
        self._stt_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:3px 8px;}"
            "QComboBox:disabled{background:#1f2937;color:#94a3b8;border-color:#334155;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        self._stt_combo.setToolTip("Select speech-to-text engine for Listen mode.")
        self._stt_combo.currentIndexChanged.connect(self._on_stt_method_changed)

        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self._mode_btn, 1)
        top.addWidget(self._action_btn, 0)
        top.addWidget(self._replay_btn, 0)
        top.addWidget(self._stop_btn, 0)
        top.addWidget(self._copy_btn, 0)

        selector = QtWidgets.QHBoxLayout()
        selector.setContentsMargins(0, 0, 0, 0)
        selector.setSpacing(6)
        selector.addWidget(self._param_label, 0)
        selector.addWidget(self._param_combo, 1)
        selector.addWidget(self._voice_label, 0)
        selector.addWidget(self._voice_combo, 1)
        selector.addWidget(self._stt_label, 0)
        selector.addWidget(self._stt_combo, 1)

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
        self._refresh_voice_options()
        self._refresh_stt_method_options()
        self._refresh_source_param_options()

    def sizeHint(self):
        return QtCore.QSize(VOICE_ACTOR_BODY_W, VOICE_ACTOR_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(VOICE_ACTOR_BODY_W, VOICE_ACTOR_BODY_H)

    def _apply_mode_ui(self) -> None:
        if self._chatbot_connected:
            self._mode_btn.setText("Text -> Voice")
            self._mode_btn.setStyleSheet(_button_style("#1e3a8a", "#3b5bb0", "#23459f"))
            self._mode_btn.setToolTip("Chatbot input forces Text -> Voice auto mode.")
            self._action_btn.setText("Auto")
            self._action_btn.setStyleSheet(_auto_button_style())
            self._action_btn.setIcon(self._chatbot_icon)
            self._action_btn.setToolTip("Replay the latest chatbot response.")
            self._replay_btn.setToolTip("Replay the last spoken output.")
            self._stop_btn.setToolTip("Stop speech immediately.")
            return
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
        self._replay_btn.setToolTip("Replay the last spoken output.")
        self._stop_btn.setToolTip("Stop speech immediately.")

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

    def _on_scene_param_changed(self, name=None, _params=None) -> None:
        self._schedule_param_refresh()
        self._maybe_trigger_chatbot_auto(name)

    def _schedule_param_refresh(self) -> None:
        self._ensure_scene_connections()
        if self._param_refresh_pending:
            return
        self._param_refresh_pending = True
        QtCore.QTimer.singleShot(0, self._refresh_source_param_options)

    def _collect_source_param_options(self) -> tuple[list, bool, object, object]:
        self._ensure_scene_connections()
        scene = self._scene
        if scene is None:
            return [], False, None, None
        edges = _input_edges(scene, self._node_item, "text")
        if not edges:
            return [], False, None, None
        multiple_sources = len(edges) > 1
        options = []
        note_input_connected = False
        chatbot_input_item = None
        chatbot_proxy_item = None
        for edge_index, edge in enumerate(edges, start=1):
            src_item = getattr(edge, "src", None)
            src_model = getattr(src_item, "model", None)
            if src_model is None:
                continue
            kind = str(getattr(src_model, "kind", "") or "").strip().lower()
            if kind == "note":
                note_input_connected = True
            if kind in CHATBOT_NODE_KINDS:
                if chatbot_input_item is None:
                    chatbot_input_item = src_item
                continue
            upstream_chatbot = _find_upstream_chatbot(scene, src_item, max_depth=6)
            if upstream_chatbot is not None:
                if chatbot_input_item is None:
                    chatbot_input_item = upstream_chatbot
                if chatbot_proxy_item is None:
                    chatbot_proxy_item = src_item
                continue
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
        return options, note_input_connected, chatbot_input_item, chatbot_proxy_item

    def _refresh_source_param_options(self) -> None:
        self._param_refresh_pending = False
        options, note_connected, chatbot_item, chatbot_proxy = self._collect_source_param_options()
        chatbot_connected = chatbot_item is not None
        if note_connected and not chatbot_connected and not self._had_note_input and self._mode != "text_to_voice":
            self._set_mode("text_to_voice")
        if chatbot_connected and self._mode != "text_to_voice":
            self._set_mode("text_to_voice")
        self._had_note_input = note_connected
        if chatbot_connected and not self._chatbot_connected:
            _text, token, _err = _latest_chatbot_response(self._scene, chatbot_item)
            self._last_chatbot_token = token
        if not chatbot_connected:
            self._last_chatbot_token = ""
            self._pending_chatbot_text = ""
        self._chatbot_connected = chatbot_connected
        self._chatbot_input_item = chatbot_item
        self._chatbot_proxy_item = chatbot_proxy
        self._chatbot_via_proxy = bool(chatbot_connected and chatbot_proxy is not None and chatbot_proxy is not chatbot_item)
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
            if self._chatbot_connected:
                if self._chatbot_via_proxy:
                    self._param_combo.addItem("Chatbot -> Python output (Auto)", "")
                else:
                    self._param_combo.addItem("Chatbot latest response (Auto)", "")
                self._param_combo.setCurrentIndex(0)
            else:
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

        if self._chatbot_connected:
            self._param_combo.setEnabled(False)
            if self._chatbot_via_proxy:
                self._param_combo.setToolTip("Chatbot input through Python uses automatic speech.")
            else:
                self._param_combo.setToolTip("Chatbot input uses automatic latest-response speech.")
        elif options:
            self._param_combo.setEnabled(not self._busy)
            self._param_combo.setToolTip("Pick which connected parameter to speak in Text -> Voice mode.")
        else:
            self._param_combo.setEnabled(False)
            self._param_combo.setToolTip("Connect a node with parameters to choose a value for speech.")

        self._apply_mode_ui()
        self._mode_btn.setEnabled(not self._busy and not self._chatbot_connected)
        self._action_btn.setEnabled(not self._busy)
        self._replay_btn.setEnabled(not self._busy)
        self._stop_btn.setEnabled(bool(self._busy and self._tts_playing))
        self._voice_combo.setEnabled(bool(self._tts_voice_options) and not self._busy)
        self._stt_combo.setEnabled(not self._busy)

    def _refresh_voice_options(self) -> None:
        google_ready = gTTS is not None and pygame is not None
        google_label = "Google (Tanya)" if google_ready else "Google (Tanya) - install gTTS + pygame"
        options = [
            {"id": VOICE_TANYA_GOOGLE, "name": google_label},
        ]
        seen_ids = {VOICE_TANYA_GOOGLE}
        for voice in _available_tts_voices():
            voice_id = str(voice.get("id", "") or "").strip()
            if not voice_id or voice_id in seen_ids:
                continue
            seen_ids.add(voice_id)
            voice_name = str(voice.get("name", "") or "").strip() or voice_id
            options.append({"id": voice_id, "name": voice_name})

        selected_key = str(self._selected_voice_key or "").strip() or VOICE_TANYA_GOOGLE
        if not any(str(opt.get("id", "")) == selected_key for opt in options):
            selected_key = VOICE_TANYA_GOOGLE
            self._selected_voice_key = selected_key
            _set_node_param(self._node_item, VOICE_ACTOR_VOICE_KEY, selected_key)

        self._tts_voice_options = options
        self._syncing_voice_combo = True
        try:
            self._voice_combo.blockSignals(True)
            self._voice_combo.clear()
            selected_index = 0
            for idx, option in enumerate(options):
                label = str(option.get("name", "") or "")
                value = str(option.get("id", "") or "")
                self._voice_combo.addItem(label, value)
                if value == selected_key:
                    selected_index = idx
            self._voice_combo.setCurrentIndex(selected_index)
        finally:
            self._voice_combo.blockSignals(False)
            self._syncing_voice_combo = False

        if self._busy:
            self._voice_combo.setEnabled(False)
            self._voice_combo.setToolTip("Voice selection is disabled while audio is active.")
        elif not google_ready and pyttsx3 is None:
            self._voice_combo.setEnabled(True)
            self._voice_combo.setToolTip(
                "Install dependencies for speech: pip install gTTS pygame pyttsx3"
            )
        elif not google_ready:
            self._voice_combo.setEnabled(True)
            self._voice_combo.setToolTip(
                "Google (Tanya) needs gTTS + pygame. Local voices are still available."
            )
        else:
            self._voice_combo.setEnabled(True)
            self._voice_combo.setToolTip(
                "Google (Tanya) matches Tanya project voice. Local voices use system TTS."
            )

    def _refresh_stt_method_options(self) -> None:
        whisper_ready = WhisperModel is not None
        local_label = "Local Whisper (Default)" if whisper_ready else "Local Whisper - install faster-whisper"
        options = [
            {"id": STT_METHOD_LOCAL_WHISPER, "name": local_label},
            {"id": STT_METHOD_GOOGLE, "name": "Google (Cloud)"},
        ]
        selected_key = _normalize_stt_method(self._selected_stt_method)
        if selected_key not in {STT_METHOD_LOCAL_WHISPER, STT_METHOD_GOOGLE}:
            selected_key = STT_METHOD_LOCAL_WHISPER
            self._selected_stt_method = selected_key
            _set_node_param(self._node_item, VOICE_ACTOR_STT_METHOD_KEY, selected_key)

        self._syncing_stt_combo = True
        try:
            self._stt_combo.blockSignals(True)
            self._stt_combo.clear()
            selected_index = 0
            for idx, option in enumerate(options):
                label = str(option.get("name", "") or "")
                value = str(option.get("id", "") or "")
                self._stt_combo.addItem(label, value)
                if value == selected_key:
                    selected_index = idx
            self._stt_combo.setCurrentIndex(selected_index)
        finally:
            self._stt_combo.blockSignals(False)
            self._syncing_stt_combo = False

        if self._busy:
            self._stt_combo.setEnabled(False)
            self._stt_combo.setToolTip("STT method is disabled while the node is busy.")
        elif not whisper_ready:
            self._stt_combo.setEnabled(True)
            self._stt_combo.setToolTip("Install faster-whisper for local transcription; Google remains available.")
        else:
            self._stt_combo.setEnabled(True)
            self._stt_combo.setToolTip("Local Whisper is the default transcription method.")

    def _on_param_selection_changed(self, _index: int) -> None:
        if self._syncing_param_combo:
            return
        selected_key = str(self._param_combo.currentData() or "").strip()
        if selected_key == str(self._selected_param_key or "").strip():
            return
        self._selected_param_key = selected_key
        _set_node_param(self._node_item, VOICE_ACTOR_SELECTED_PARAM_KEY, selected_key)

    def _on_voice_selection_changed(self, _index: int) -> None:
        if self._syncing_voice_combo:
            return
        selected_key = str(self._voice_combo.currentData() or "").strip() or VOICE_TANYA_GOOGLE
        if selected_key == str(self._selected_voice_key or "").strip():
            return
        self._selected_voice_key = selected_key
        _set_node_param(self._node_item, VOICE_ACTOR_VOICE_KEY, selected_key)

    def _on_stt_method_changed(self, _index: int) -> None:
        if self._syncing_stt_combo:
            return
        selected_key = _normalize_stt_method(self._stt_combo.currentData() or "")
        if selected_key == str(self._selected_stt_method or "").strip():
            return
        self._selected_stt_method = selected_key
        _set_node_param(self._node_item, VOICE_ACTOR_STT_METHOD_KEY, selected_key)

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
        self._mode_btn.setEnabled(not self._busy and not self._chatbot_connected)
        self._action_btn.setEnabled(not self._busy)
        self._replay_btn.setEnabled(not self._busy)
        self._stop_btn.setEnabled(bool(self._busy and self._tts_playing))
        self._copy_btn.setEnabled(not self._busy)
        self._param_combo.setEnabled(bool(self._source_param_options) and not self._busy and not self._chatbot_connected)
        self._voice_combo.setEnabled(bool(self._tts_voice_options) and not self._busy)
        self._stt_combo.setEnabled(not self._busy)
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
        if self._chatbot_connected:
            self._set_status("Auto mode enabled by connected chatbot input.")
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

    def _replay_last(self) -> None:
        if self._busy:
            return
        text = (self._last_tts_text or "").strip()
        if text:
            self._speak_text(text, source="replay")
            return
        text, source = self._resolve_tts_text()
        self._speak_text(text, source=source if source else "replay")

    def _stop_audio(self) -> None:
        if not self._tts_playing:
            self._set_status("Nothing is playing.")
            return
        self._pending_chatbot_text = ""
        engine = None
        with self._tts_lock:
            self._tts_user_stopped = True
            engine = self._tts_engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
        if pygame is not None:
            try:
                if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                    pygame.mixer.music.stop()
            except Exception:
                pass
        self._stop_btn.setEnabled(False)
        self._set_status("Stopping speech...")

    def _on_action_clicked(self) -> None:
        if self._busy:
            return
        self._refresh_source_param_options()
        if self._chatbot_connected:
            text, source = self._resolve_tts_text()
            self._speak_text(text, source=source)
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
        stt_method = _normalize_stt_method(self._selected_stt_method)
        if stt_method == STT_METHOD_LOCAL_WHISPER and WhisperModel is None:
            self._set_status(
                "Missing dependency: faster-whisper. Run setup.bat to enable Local Whisper.",
                error=True,
            )
            return
        self._set_busy(True, "listening")
        if stt_method == STT_METHOD_LOCAL_WHISPER:
            self._set_status("Listening... (Local Whisper)")
        else:
            self._set_status("Listening... (Google)")
        threading.Thread(target=self._stt_worker, args=(stt_method,), daemon=True).start()

    def _stt_worker(self, stt_method: str) -> None:
        transcript = ""
        error = ""
        selected_method = _normalize_stt_method(stt_method)
        try:
            recognizer = sr.Recognizer()
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.4)
                audio = recognizer.listen(source, timeout=8, phrase_time_limit=20)
            if selected_method == STT_METHOD_LOCAL_WHISPER:
                wav_data = audio.get_wav_data(convert_rate=16000, convert_width=2)
                transcript, error = _transcribe_local_whisper(wav_data)
            else:
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

    def _maybe_trigger_chatbot_auto(self, changed_name=None) -> None:
        if self._processing_chatbot_auto:
            return
        if not self._chatbot_connected:
            self._refresh_source_param_options()
            if not self._chatbot_connected:
                return
        source_item = self._chatbot_input_item
        if source_item is None:
            return
        self._processing_chatbot_auto = True
        try:
            source_name = _node_name(source_item)
            proxy_name = _node_name(self._chatbot_proxy_item)
            changed_key = str(changed_name or "").strip().lower()
            if self._chatbot_via_proxy:
                allowed = {k for k in (source_name, proxy_name) if k}
                if changed_key and allowed and changed_key not in allowed:
                    return
            else:
                if changed_key and source_name and changed_key != source_name:
                    return
            if self._mode != "text_to_voice":
                self._set_mode("text_to_voice")

            text = ""
            token = ""
            if self._chatbot_via_proxy:
                _db_text, token, _err = _latest_chatbot_response(self._scene, source_item)
                if token and token == self._last_chatbot_token:
                    return
                proxy_item = self._chatbot_proxy_item
                if proxy_item is not None and _kind_of_item(proxy_item) == "python":
                    text, py_error = _run_python_transform(self._scene, proxy_item)
                    if py_error and py_error != "Python transform produced empty output.":
                        self._set_status(py_error, error=True)
                        return
                if not text:
                    text = _text_from_input(self._scene, self._node_item, "text").strip()
                if not text:
                    self._set_status("Waiting for Python output from chatbot response...")
                    return
            else:
                text, token, _err = _latest_chatbot_response(self._scene, source_item)
                if not text:
                    return
                text = text.strip()
                if not text:
                    return

            if token and token == self._last_chatbot_token:
                return
            if token:
                self._last_chatbot_token = token
            self._set_transcript(text)
            if self._busy:
                self._pending_chatbot_text = text
                return
            self._speak_text(text, source="chatbot_auto")
        finally:
            self._processing_chatbot_auto = False

    def _speak_text(self, text: str, *, source: str) -> bool:
        voice_key = str(self._selected_voice_key or "").strip() or VOICE_TANYA_GOOGLE
        if voice_key == VOICE_TANYA_GOOGLE:
            if gTTS is None or pygame is None:
                self._set_status(
                    "Google voice missing dependencies. Install: pip install gTTS pygame",
                    error=True,
                )
                return False
        elif pyttsx3 is None:
            self._set_status(
                "Missing dependency: pyttsx3. Install: pip install pyttsx3",
                error=True,
            )
            return False
        clean = (text or "").strip()
        if not clean:
            if source == "selected_param":
                self._set_status(
                    "Selected parameter is empty. Choose another parameter or fill its value.",
                    error=True,
                )
            elif source in ("chatbot_auto", "chatbot_latest"):
                self._set_status("No chatbot response available to speak yet.", error=True)
            else:
                self._set_status("No text to speak. Connect input 'text' or type transcript.", error=True)
            return False
        if source == "selected_param":
            self._set_status("Speaking selected parameter value...")
        elif source in ("chatbot_auto", "chatbot_latest"):
            self._set_status("Auto-speaking latest chatbot response...")
        elif source == "replay":
            self._set_status("Replaying last output...")
        elif source == "input":
            self._set_status("Speaking text from wired input...")
        else:
            self._set_status("Speaking transcript...")
        self._last_tts_text = clean
        with self._tts_lock:
            self._tts_user_stopped = False
            self._tts_playing = True
            self._tts_engine = None
        self._set_busy(True, "speaking")
        threading.Thread(target=self._tts_worker, args=(clean, voice_key), daemon=True).start()
        return True

    def _resolve_tts_text(self) -> tuple[str, str]:
        self._refresh_source_param_options()
        if self._chatbot_connected and self._chatbot_input_item is not None:
            if self._chatbot_via_proxy:
                scene = self._node_item.scene()
                proxy_item = self._chatbot_proxy_item
                if proxy_item is not None and _kind_of_item(proxy_item) == "python":
                    text, _py_error = _run_python_transform(scene, proxy_item)
                    if text.strip():
                        return text.strip(), "chatbot_latest"
                wired = _text_from_input(scene, self._node_item, "text")
                if wired.strip():
                    return wired.strip(), "chatbot_latest"
                local = (self._transcript.toPlainText() or "").strip()
                if local:
                    return local, "chatbot_latest"
                return "", "chatbot_latest"

            text, token, _err = _latest_chatbot_response(self._scene, self._chatbot_input_item)
            if text.strip():
                if token:
                    self._last_chatbot_token = token
                return text.strip(), "chatbot_latest"
            return "", "chatbot_latest"

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
        text, source = self._resolve_tts_text()
        self._speak_text(text, source=source)

    def _tts_worker(self, text: str, voice_key: str) -> None:
        error = ""
        user_stopped = False
        engine = None
        temp_file = None
        try:
            selected_voice_key = str(voice_key or "").strip() or VOICE_TANYA_GOOGLE
            if selected_voice_key == VOICE_TANYA_GOOGLE:
                if gTTS is None or pygame is None:
                    raise RuntimeError("Google voice dependencies are missing. Install: pip install gTTS pygame")
                temp_file = Path(tempfile.gettempdir()) / f"voice_actor_{uuid.uuid4().hex}.mp3"
                tts = gTTS(text=text, lang="en")
                tts.save(str(temp_file))
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                pygame.mixer.music.load(str(temp_file))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    with self._tts_lock:
                        user_stopped = bool(self._tts_user_stopped)
                    if user_stopped:
                        try:
                            pygame.mixer.music.stop()
                        except Exception:
                            pass
                        break
                    time.sleep(0.05)
            else:
                engine = pyttsx3.init()
                voice_applied = False
                if selected_voice_key != VOICE_AUTO_FEMALE:
                    try:
                        engine.setProperty("voice", selected_voice_key)
                        voice_applied = True
                    except Exception:
                        voice_applied = False
                if not voice_applied:
                    female_voice_id = _pick_female_voice_id(engine)
                    if female_voice_id:
                        try:
                            engine.setProperty("voice", female_voice_id)
                        except Exception:
                            pass
                with self._tts_lock:
                    self._tts_engine = engine
                    user_stopped = bool(self._tts_user_stopped)
                if user_stopped:
                    error = "__stopped__"
                else:
                    engine.say(text)
                    engine.runAndWait()
        except Exception as exc:
            error = f"Text-to-speech failed: {exc}"
        finally:
            if pygame is not None:
                try:
                    if pygame.mixer.get_init():
                        try:
                            pygame.mixer.music.unload()
                        except Exception:
                            pass
                except Exception:
                    pass
            if temp_file is not None:
                try:
                    temp_file.unlink()
                except Exception:
                    pass
            if engine is not None:
                try:
                    engine.stop()
                except Exception:
                    pass
            with self._tts_lock:
                if self._tts_engine is engine:
                    self._tts_engine = None
                user_stopped = bool(self._tts_user_stopped)
                self._tts_playing = False
        if user_stopped and not error:
            error = "__stopped__"
        self._tts_done.emit(error)

    @QtCore.Slot(str)
    def _finish_tts(self, error: str) -> None:
        self._set_busy(False, "")
        if error:
            if str(error).strip() == "__stopped__":
                self._set_status("Speech stopped.")
                return
            self._set_status(error, error=True)
            return
        self._set_status("Speech playback complete.")
        pending = (self._pending_chatbot_text or "").strip()
        self._pending_chatbot_text = ""
        if pending:
            self._speak_text(pending, source="chatbot_auto")


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
