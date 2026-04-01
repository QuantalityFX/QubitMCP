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
VOICE_ACTOR_NODE_KINDS = {VOICE_ACTOR_NODE_KIND, *VOICE_ACTOR_NODE_ALIASES}

VOICE_ACTOR_BODY_W = 460
VOICE_ACTOR_BODY_H = 300
VOICE_ACTOR_ICON_BTN_SIDE = 56
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
STT_IDLE_CONFIRM_SECONDS = 2.2
STT_IDLE_CONFIRM_RESPONSE_SECONDS = 10.0
STT_SEND_CONFIRM_WORDS = {"yes", "yeah", "yep", "yup", "done", "send", "okay", "ok"}
STT_CONTINUE_LISTENING_WORDS = {"no", "nope", "wait", "continue", "hold"}
STT_STOP_SPEAKING_PHRASES = (
    "stop speaking",
    "stop talking",
    "stop the voice",
    "stop playback",
)
CHATBOT_NODE_KINDS = {"chatbot", "chat bot", "chat_bot"}
# Only treat narrow relay/proxy nodes as chatbot-auto sources.
# This keeps auto speech working through common pass-through nodes
# without matching unrelated agent chains (for example Medigator).
CHATBOT_PROXY_NODE_KINDS = {"python", "output", "wire", "switch"}
DATABASE_NODE_KINDS = {"database"}
CHATBOT_DB_NAME = "my_database"
CHATBOT_DEFAULT_COLLECTION = "EchoGragh"
VOICE_AUDIO_MODE_BILATERAL = "bilateral"
VOICE_AUDIO_MODE_TURN_TAKING = "turn_taking"
VOICE_MIC_DEVICE_DEFAULT = None
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
_TTS_PLAYBACK_LOCK = threading.Lock()


def _new_gtts(text: str):
    if gTTS is None:
        return None
    # Newer gTTS builds support timeout; older builds may not.
    try:
        return gTTS(text=text, lang="en", timeout=8)
    except TypeError:
        return gTTS(text=text, lang="en")


def _save_gtts_mp3(text: str, target_path: Path, *, timeout_seconds: float = 12.0) -> str:
    done = threading.Event()
    errors = []

    def _worker():
        try:
            tts = _new_gtts(text)
            if tts is None:
                raise RuntimeError("Google voice dependency is unavailable.")
            tts.save(str(target_path))
        except Exception as exc:
            errors.append(str(exc))
        finally:
            done.set()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    done.wait(max(1.0, float(timeout_seconds)))
    if not done.is_set():
        return f"Google TTS timed out after {timeout_seconds:.0f}s."
    if errors:
        return errors[0]
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
    if output_text is None or not str(output_text).strip():
        output_text = ns.get("result", None)
    if output_text is None or not str(output_text).strip():
        output_text = out_text
    if output_text is None or not str(output_text).strip():
        output_text = getattr(model, "info", "")
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


def _normalize_voice_audio_mode(value: str) -> str:
    key = str(value or "").strip().lower()
    if key in {"turn_taking", "turn-taking", "turntaking", "single", "single_talk"}:
        return VOICE_AUDIO_MODE_TURN_TAKING
    return VOICE_AUDIO_MODE_BILATERAL


def _normalize_voice_mic_device_index(value):
    if value is None:
        return VOICE_MIC_DEVICE_DEFAULT
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, (int, float)):
        idx = int(value)
        return idx if idx >= 0 else VOICE_MIC_DEVICE_DEFAULT
    text = str(value or "").strip()
    if not text:
        return VOICE_MIC_DEVICE_DEFAULT
    low = text.lower()
    if low in {"default", "system", "auto", "none", "-1"}:
        return VOICE_MIC_DEVICE_DEFAULT
    try:
        idx = int(text)
        return idx if idx >= 0 else VOICE_MIC_DEVICE_DEFAULT
    except Exception:
        return VOICE_MIC_DEVICE_DEFAULT


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
        "border-radius:4px;padding:2px 4px;font-size:10px;}"
        f"QPushButton:hover{{background:{hover};}}"
        "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#334155;}"
        f"QToolButton{{background:{background};color:#f8fafc;border:1px solid {border};"
        "border-radius:4px;"
        "padding-top:8px;padding-bottom:2px;padding-left:4px;padding-right:4px;font-size:9px;}"
        f"QToolButton:hover{{background:{hover};}}"
        "QToolButton:disabled{background:#334155;color:#94a3b8;border-color:#334155;}"
    )


def _auto_button_style() -> str:
    return (
        "QPushButton{background:#ca8a04;color:#111827;border:1px solid #facc15;"
        "border-radius:4px;padding:2px 4px;font-size:10px;}"
        "QPushButton:hover{background:#d97706;}"
        "QPushButton:disabled{background:#a16207;color:#fde68a;border-color:#d97706;}"
        "QToolButton{background:#ca8a04;color:#111827;border:1px solid #facc15;"
        "border-radius:4px;"
        "padding-top:8px;padding-bottom:2px;padding-left:4px;padding-right:4px;font-size:9px;}"
        "QToolButton:hover{background:#d97706;}"
        "QToolButton:disabled{background:#a16207;color:#fde68a;border-color:#d97706;}"
    )


def _tool_button_style(
    background: str,
    border: str,
    hover: str,
    *,
    text: str = "#f8fafc",
    disabled_background: str = "#1f2937",
    disabled_text: str = "#64748b",
    disabled_border: str = "#334155",
) -> str:
    return (
        f"QToolButton{{background:{background};color:{text};border:1px solid {border};"
        "border-radius:4px;"
        "padding-top:8px;padding-bottom:2px;padding-left:4px;padding-right:4px;font-size:9px;}"
        f"QToolButton:hover{{background:{hover};}}"
        f"QToolButton:disabled{{background:{disabled_background};color:{disabled_text};border-color:{disabled_border};}}"
    )


def _pause_button_style(*, paused: bool = False, flash: bool = False) -> str:
    if paused:
        if flash:
            background = "#92400e"
            border = "#f59e0b"
            hover = "#b45309"
        else:
            background = "#78350f"
            border = "#d97706"
            hover = "#92400e"
        text = "#fffbeb"
    else:
        background = "#334155"
        border = "#475569"
        hover = "#3f4d62"
        text = "#e2e8f0"
    return _tool_button_style(
        background,
        border,
        hover,
        text=text,
        disabled_background="#1f2937",
        disabled_text="#64748b",
        disabled_border="#334155",
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
    _stt_chunk = QtCore.Signal(str)
    _stt_status = QtCore.Signal(str)
    _stt_command = QtCore.Signal(str)
    _tts_done = QtCore.Signal(str)

    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._busy = False
        self._syncing_text = False
        self._listen_icon = _voice_action_icon("Mic_Icon.png")
        self._speak_icon = _voice_action_icon("Voice_Icon.png")
        self._chatbot_icon = _voice_action_icon("Chatbot_Icon.png")
        self._backspace_icon = _voice_action_icon("StreightArrow_Left_Icon.png")
        self._replay_icon = _voice_action_icon("PlayButton_icon.png")
        self._eraser_icon = _voice_action_icon("Eraser_Icon.png")
        self._pause_icon = _voice_action_icon("Pause_Icon.png")
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
        self._turn_taking_paused_actors = []
        self._last_tts_text = ""
        self._processing_chatbot_auto = False
        self._tts_lock = threading.Lock()
        self._tts_engine = None
        self._tts_playing = False
        self._tts_user_stopped = False
        self._tts_using_pygame = False
        self._tts_voice_options = []
        self._syncing_voice_combo = False
        self._syncing_stt_combo = False
        self._stt_state_lock = threading.Lock()
        self._stt_listening = False
        self._stt_paused = False
        self._stt_stop_requested = False
        self._stt_session_has_new_text = False
        self._stt_session_base_text = ""
        self._stt_last_pause_snapshot = ""
        self._stt_prompt_lock = threading.Lock()
        self._stt_prompt_stop_event = threading.Event()
        self._stt_prompt_thread = None
        self._stt_prompt_engine = None
        self._stt_prompt_playing = False
        self._stt_prompt_uses_pygame = False
        self._pause_flash_state = False
        self._pause_flash_timer = QtCore.QTimer(self)
        self._pause_flash_timer.setInterval(420)
        self._pause_flash_timer.timeout.connect(self._on_pause_flash_tick)
        self._action_role = "action"
        self._replay_role = "replay"
        self._stop_role = "erase"
        self._transcript_undo_stack = []
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
        self._mode_btn.setFixedHeight(VOICE_ACTOR_ICON_BTN_SIDE)
        try:
            self._mode_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass
        self._mode_btn.clicked.connect(self._toggle_mode)

        self._action_btn = QtWidgets.QToolButton()
        self._configure_icon_button(self._action_btn, icon_size=18)
        self._action_btn.clicked.connect(self._on_action_clicked)

        self._replay_btn = QtWidgets.QToolButton()
        self._configure_icon_button(self._replay_btn)
        self._replay_btn.setText("Replay")
        self._replay_btn.setStyleSheet(_tool_button_style("#334155", "#475569", "#3f4d62", text="#e2e8f0"))
        if not self._replay_icon.isNull():
            self._replay_btn.setIcon(self._replay_icon)
        self._replay_btn.clicked.connect(self._on_replay_or_erase_clicked)

        self._pause_btn = QtWidgets.QToolButton()
        self._configure_icon_button(self._pause_btn)
        self._pause_btn.setText("Pause")
        self._pause_btn.setStyleSheet(_pause_button_style(paused=False, flash=False))
        if not self._pause_icon.isNull():
            self._pause_btn.setIcon(self._pause_icon)
        self._pause_btn.clicked.connect(self._toggle_pause_listening)

        self._stop_btn = QtWidgets.QToolButton()
        self._configure_icon_button(self._stop_btn)
        self._stop_btn.setText("Erase")
        self._stop_btn.setStyleSheet(_tool_button_style("#334155", "#475569", "#3f4d62", text="#e2e8f0"))
        if not self._eraser_icon.isNull():
            self._stop_btn.setIcon(self._eraser_icon)
        self._stop_btn.clicked.connect(self._on_backspace_or_erase_clicked)

        self._copy_btn = QtWidgets.QToolButton()
        self._configure_icon_button(self._copy_btn)
        self._copy_btn.setText("Copy")
        self._copy_btn.setStyleSheet(_tool_button_style("#1f2937", "#475569", "#273449", text="#e2e8f0"))
        copy_icon = _copy_icon()
        if not copy_icon.isNull():
            self._copy_btn.setIcon(copy_icon)
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
        top.addWidget(self._pause_btn, 0)
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
        self._stt_chunk.connect(self._on_stt_chunk)
        self._stt_status.connect(self._on_stt_status)
        self._stt_command.connect(self._on_stt_command)
        self._tts_done.connect(self._finish_tts)

        initial_text = (getattr(getattr(self._node_item, "model", None), "info", "") or "").strip()
        if initial_text:
            self._set_transcript(initial_text)
        self._apply_mode_ui()
        self._ensure_scene_connections()
        self._refresh_voice_options()
        self._refresh_stt_method_options()
        self._refresh_source_param_options()
        self._queue_deferred_scene_bootstrap()

    def sizeHint(self):
        return QtCore.QSize(VOICE_ACTOR_BODY_W, VOICE_ACTOR_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(VOICE_ACTOR_BODY_W, VOICE_ACTOR_BODY_H)

    def _configure_icon_button(self, button, *, icon_size: int = 16) -> None:
        try:
            button.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        except Exception:
            pass
        icon_px = max(12, int(icon_size or 16))
        button.setIconSize(QtCore.QSize(icon_px, icon_px))
        button.setMinimumSize(VOICE_ACTOR_ICON_BTN_SIDE, VOICE_ACTOR_ICON_BTN_SIDE)
        button.setMaximumSize(VOICE_ACTOR_ICON_BTN_SIDE, VOICE_ACTOR_ICON_BTN_SIDE)
        try:
            button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass

    def _erase_target_text(self) -> str:
        target = str(self._stt_last_pause_snapshot or "")
        if target:
            return target
        return str(self._stt_session_base_text or "")

    def _can_backspace_transcript(self) -> bool:
        return bool(self._transcript_undo_stack)

    def _can_erase_last_spoken(self) -> bool:
        current = str(self._transcript.toPlainText() or "")
        return bool(current.strip())

    def _update_contextual_action_roles(self) -> None:
        listening, _paused, _stop_requested = self._stt_state()
        listening_active = bool(listening)
        speaking_active = bool(self._tts_playing)
        active_processing = bool(listening_active or speaking_active)

        if active_processing:
            self._action_role = "stop"
            self._action_btn.setText("Stop")
            self._action_btn.setStyleSheet(_tool_button_style("#7f1d1d", "#b91c1c", "#991b1b", text="#f8fafc"))
            if not self._stop_icon.isNull():
                self._action_btn.setIcon(self._stop_icon)
            self._action_btn.setToolTip("Stop speech or listening immediately.")
        else:
            self._action_role = "action"
            if self._chatbot_connected:
                self._action_btn.setText("Auto")
                self._action_btn.setStyleSheet(_auto_button_style())
                self._action_btn.setIcon(self._chatbot_icon)
                self._action_btn.setToolTip("Replay the latest chatbot response.")
            elif self._mode == "voice_to_text":
                self._action_btn.setText("Listen")
                self._action_btn.setStyleSheet(_button_style("#6b1f1f", "#8b2b2b", "#7a2323"))
                self._action_btn.setIcon(self._listen_icon)
                self._action_btn.setToolTip("Listen continuously and transcribe speech.")
            else:
                self._action_btn.setText("Speak")
                self._action_btn.setStyleSheet(_button_style("#1d4ed8", "#60a5fa", "#2563eb"))
                self._action_btn.setIcon(self._speak_icon)
                self._action_btn.setToolTip("Speak text from input or transcript box.")

        if listening_active:
            self._replay_role = "backspace"
            self._replay_btn.setText("Back")
            self._replay_btn.setStyleSheet(_tool_button_style("#334155", "#475569", "#3f4d62", text="#e2e8f0"))
            if not self._backspace_icon.isNull():
                self._replay_btn.setIcon(self._backspace_icon)
            self._replay_btn.setToolTip("Undo the last transcript chunk.")
        else:
            self._replay_role = "replay"
            self._replay_btn.setText("Replay")
            self._replay_btn.setStyleSheet(_tool_button_style("#334155", "#475569", "#3f4d62", text="#e2e8f0"))
            if not self._replay_icon.isNull():
                self._replay_btn.setIcon(self._replay_icon)
            self._replay_btn.setToolTip("Replay the last spoken output.")

        self._stop_role = "erase"
        self._stop_btn.setText("Erase")
        self._stop_btn.setStyleSheet(_tool_button_style("#334155", "#475569", "#3f4d62", text="#e2e8f0"))
        if not self._eraser_icon.isNull():
            self._stop_btn.setIcon(self._eraser_icon)
        self._stop_btn.setToolTip("Clear transcript.")

    def _apply_mode_ui(self) -> None:
        if self._chatbot_connected:
            self._mode_btn.setText("Text --> Voice")
            self._mode_btn.setStyleSheet(_button_style("#1e3a8a", "#3b5bb0", "#23459f"))
            self._mode_btn.setToolTip("Chatbot input forces Text -> Voice auto mode.")
            self._action_btn.setText("Auto")
            self._action_btn.setStyleSheet(_auto_button_style())
            self._action_btn.setIcon(self._chatbot_icon)
            self._action_btn.setToolTip("Replay the latest chatbot response.")
            self._replay_btn.setToolTip("Replay the last spoken output.")
            self._pause_btn.setToolTip("Pause is available in Voice -> Text listen mode.")
            self._stop_btn.setToolTip("Stop speech immediately.")
            self._update_pause_button_ui()
            self._update_control_states()
            return
        if self._mode == "voice_to_text":
            self._mode_btn.setText("Voice --> Text")
            self._mode_btn.setStyleSheet(_button_style("#4d1616", "#6a2222", "#5a1b1b"))
            self._action_btn.setText("Listen")
            self._action_btn.setStyleSheet(_button_style("#6b1f1f", "#8b2b2b", "#7a2323"))
            self._action_btn.setIcon(self._listen_icon)
            self._action_btn.setToolTip("Listen continuously and transcribe speech.")
            self._pause_btn.setToolTip("Pause/resume active listening.")
        else:
            self._mode_btn.setText("Text --> Voice")
            self._mode_btn.setStyleSheet(_button_style("#1e3a8a", "#3b5bb0", "#23459f"))
            self._action_btn.setText("Speak")
            self._action_btn.setStyleSheet(_button_style("#1d4ed8", "#60a5fa", "#2563eb"))
            self._action_btn.setIcon(self._speak_icon)
            self._action_btn.setToolTip("Speak text from input or transcript box.")
            self._pause_btn.setToolTip("Pause is available in Voice -> Text listen mode.")
        self._replay_btn.setToolTip("Replay the last spoken output.")
        self._stop_btn.setToolTip("Stop speech or listening immediately.")
        self._update_pause_button_ui()
        self._update_control_states()

    def _stt_state(self) -> tuple[bool, bool, bool]:
        with self._stt_state_lock:
            return bool(self._stt_listening), bool(self._stt_paused), bool(self._stt_stop_requested)

    def _set_stt_state(
        self,
        *,
        listening: bool | None = None,
        paused: bool | None = None,
        stop_requested: bool | None = None,
    ) -> None:
        with self._stt_state_lock:
            if listening is not None:
                self._stt_listening = bool(listening)
            if paused is not None:
                self._stt_paused = bool(paused)
            if stop_requested is not None:
                self._stt_stop_requested = bool(stop_requested)

    def _update_pause_button_ui(self) -> None:
        listening, paused, _stop_requested = self._stt_state()
        flashing = bool(paused and listening and self._pause_flash_state)
        self._pause_btn.setText("Paused" if paused and listening else "Pause")
        self._pause_btn.setStyleSheet(_pause_button_style(paused=bool(paused and listening), flash=flashing))
        if paused and listening:
            if not self._pause_flash_timer.isActive():
                self._pause_flash_timer.start()
        else:
            if self._pause_flash_timer.isActive():
                self._pause_flash_timer.stop()
            self._pause_flash_state = False

    def _update_control_states(self) -> None:
        self._update_contextual_action_roles()
        listening, _paused, stop_requested = self._stt_state()
        can_control_listening = bool(listening and not stop_requested)
        can_stop_action = bool(self._tts_playing or can_control_listening)
        can_backspace = self._can_backspace_transcript()
        can_erase = self._can_erase_last_spoken()
        self._mode_btn.setEnabled(not self._busy and not self._chatbot_connected)
        if self._action_role == "stop":
            self._action_btn.setEnabled(bool(self._busy and can_stop_action))
        else:
            self._action_btn.setEnabled(not self._busy)
        if self._replay_role == "backspace":
            self._replay_btn.setEnabled(bool(self._busy and can_backspace))
        else:
            self._replay_btn.setEnabled(not self._busy)
        if self._stop_role == "erase":
            self._stop_btn.setEnabled(bool(can_erase))
        self._pause_btn.setEnabled(bool(self._busy and can_control_listening))
        self._copy_btn.setEnabled(not self._busy)
        self._param_combo.setEnabled(bool(self._source_param_options) and not self._busy and not self._chatbot_connected)
        self._voice_combo.setEnabled(bool(self._tts_voice_options) and not self._busy)
        self._stt_combo.setEnabled(not self._busy)

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

    def _queue_deferred_scene_bootstrap(self) -> None:
        QtCore.QTimer.singleShot(0, self._bootstrap_scene_bindings)
        QtCore.QTimer.singleShot(120, self._bootstrap_scene_bindings)

    def _bootstrap_scene_bindings(self) -> None:
        self._ensure_scene_connections()
        self._schedule_param_refresh()

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
            if kind in CHATBOT_PROXY_NODE_KINDS:
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
        self._update_pause_button_ui()
        self._update_control_states()
        try:
            self._node_item.setBusyState(bool(active), label if active else "")
        except Exception:
            pass

    def _set_transcript(self, text: str, *, publish: bool = True) -> None:
        value = text or ""
        try:
            self._syncing_text = True
            self._transcript.blockSignals(True)
            if self._transcript.toPlainText() != value:
                self._transcript.setPlainText(value)
        finally:
            self._transcript.blockSignals(False)
            self._syncing_text = False
        if publish:
            _set_node_info(self._node_item, value)

    def _append_transcript(self, text: str, *, publish: bool = False) -> None:
        chunk = (text or "").strip()
        if not chunk:
            return
        current = self._transcript.toPlainText() or ""
        if current and not current.endswith((" ", "\n", "\t")):
            merged = f"{current} {chunk}"
        else:
            merged = f"{current}{chunk}"
        if merged != current:
            self._transcript_undo_stack.append(current)
            if len(self._transcript_undo_stack) > 200:
                self._transcript_undo_stack = self._transcript_undo_stack[-200:]
        self._set_transcript(merged, publish=publish)

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
        if not self._busy:
            self._transcript_undo_stack.clear()
        _set_node_info(self._node_item, self._transcript.toPlainText())
        self._update_control_states()

    def _toggle_pause_listening(self) -> None:
        listening, paused, stop_requested = self._stt_state()
        if not listening or stop_requested:
            self._set_status("Pause is available only while listening.")
            return
        if not paused:
            self._stt_last_pause_snapshot = str(self._transcript.toPlainText() or "")
            self._set_stt_state(paused=True)
        else:
            self._set_stt_state(paused=False)
        self._pause_flash_state = False
        self._update_pause_button_ui()
        self._update_control_states()
        if not paused:
            self._set_status("Listening paused.")
        else:
            stt_method = _normalize_stt_method(self._selected_stt_method)
            if stt_method == STT_METHOD_LOCAL_WHISPER:
                self._set_status("Listening resumed... (Local Whisper)")
            else:
                self._set_status("Listening resumed... (Google)")

    def _on_pause_flash_tick(self) -> None:
        listening, paused, _stop_requested = self._stt_state()
        if not listening or not paused:
            self._pause_flash_state = False
            self._update_pause_button_ui()
            return
        self._pause_flash_state = not self._pause_flash_state
        self._update_pause_button_ui()

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

    def _erase_last_spoken_segment(self) -> None:
        current = str(self._transcript.toPlainText() or "")
        if not current.strip():
            self._set_status("Transcript is already empty.")
            return
        self._set_transcript("", publish=not self._busy)
        self._transcript_undo_stack.clear()
        self._stt_session_has_new_text = False
        self._stt_session_base_text = ""
        self._stt_last_pause_snapshot = ""
        self._set_status("Transcript cleared.")
        self._update_control_states()

    def _undo_last_transcript_chunk(self) -> None:
        if not self._transcript_undo_stack:
            self._set_status("Nothing to undo.")
            self._update_control_states()
            return
        previous_text = str(self._transcript_undo_stack.pop() or "")
        listening, _paused, _stop_requested = self._stt_state()
        self._set_transcript(previous_text, publish=not listening)
        if listening:
            self._stt_session_has_new_text = bool(previous_text.strip())
        self._set_status("Undid last transcript chunk.")
        self._update_control_states()

    def _on_replay_or_erase_clicked(self) -> None:
        if self._replay_role == "backspace":
            self._undo_last_transcript_chunk()
            return
        self._replay_last()

    def _on_backspace_or_erase_clicked(self) -> None:
        if self._stop_role == "erase":
            self._erase_last_spoken_segment()
            return

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
        listening, _paused, stop_requested = self._stt_state()
        if listening and not stop_requested:
            self._set_stt_state(stop_requested=True, paused=False)
            self._stop_stt_send_confirmation_prompt()
            self._pause_flash_state = False
            self._update_pause_button_ui()
            self._update_control_states()
            self._set_status("Stopping listening...")
            return
        if not self._tts_playing:
            self._set_status("Nothing is playing.")
            return
        self._pending_chatbot_text = ""
        engine = None
        using_pygame = False
        with self._tts_lock:
            self._tts_user_stopped = True
            engine = self._tts_engine
            using_pygame = bool(self._tts_using_pygame)
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
        if using_pygame and pygame is not None:
            try:
                if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                    pygame.mixer.music.stop()
            except Exception:
                pass
        self._update_control_states()
        self._set_status("Stopping speech...")

    def _on_action_clicked(self) -> None:
        if self._busy:
            if self._action_role == "stop":
                self._stop_audio()
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
        self._stop_stt_send_confirmation_prompt()
        # Start each listen session as a fresh message transcription.
        self._set_transcript("", publish=True)
        self._transcript_undo_stack.clear()
        self._stt_session_base_text = ""
        self._stt_last_pause_snapshot = ""
        self._set_stt_state(listening=True, paused=False, stop_requested=False)
        self._stt_session_has_new_text = False
        self._pause_flash_state = False
        self._update_pause_button_ui()
        self._set_busy(True, "listening")
        if stt_method == STT_METHOD_LOCAL_WHISPER:
            self._set_status("Listening... (Local Whisper). Use Pause and Stop to control capture.")
        else:
            self._set_status("Listening... (Google). Use Pause and Stop to control capture.")
        threading.Thread(target=self._stt_worker, args=(stt_method,), daemon=True).start()

    @QtCore.Slot(str)
    def _on_stt_status(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        self._set_status(text)

    @QtCore.Slot(str)
    def _on_stt_command(self, command: str) -> None:
        cmd = str(command or "").strip().lower()
        if cmd != "stop_speaking":
            return
        stopped_any = self._interrupt_other_voice_actors()
        if stopped_any:
            self._set_status("Stopped speech. Listening...")
        else:
            self._set_status("No active speech found. Listening...")

    def _resume_stt_after_send(self) -> None:
        listening, _paused, stop_requested = self._stt_state()
        if listening or stop_requested or self._busy:
            return
        if self._mode != "voice_to_text":
            return
        if self._scene_voice_audio_mode() == VOICE_AUDIO_MODE_TURN_TAKING and self._other_voice_actor_is_speaking():
            try:
                QtCore.QTimer.singleShot(160, self._resume_stt_after_send)
            except Exception:
                pass
            return
        self._start_stt()

    def _other_voice_actor_is_speaking(self) -> bool:
        scene = self._scene
        if scene is None:
            try:
                scene = self._node_item.scene()
            except Exception:
                scene = None
        if scene is None:
            return False
        for item in list(getattr(scene, "_node_items", {}).values() or []):
            if item is None or item is self._node_item:
                continue
            if _kind_of_item(item) not in VOICE_ACTOR_NODE_KINDS:
                continue
            for proxy in list(getattr(item, "_plugin_proxies", []) or []):
                if proxy is None or not hasattr(proxy, "widget"):
                    continue
                widget = proxy.widget()
                if widget is None or widget is self:
                    continue
                try:
                    if bool(getattr(widget, "_tts_playing", False)):
                        return True
                except Exception:
                    pass
                try:
                    if bool(getattr(widget, "_stt_prompt_playing", False)):
                        return True
                except Exception:
                    pass
        return False

    def _is_stop_speaking_command(self, text: str) -> bool:
        cleaned = str(text or "").strip().lower()
        if not cleaned:
            return False
        compact = " ".join(cleaned.split())
        if compact.startswith("please "):
            compact = compact[len("please "):].strip()
        for phrase in STT_STOP_SPEAKING_PHRASES:
            if phrase and compact == phrase:
                return True
        return False

    def _stop_tts_only(self, *, reason: str = "") -> bool:
        if not self._tts_playing:
            self._pending_chatbot_text = ""
            return False
        self._pending_chatbot_text = ""
        engine = None
        using_pygame = False
        with self._tts_lock:
            self._tts_user_stopped = True
            engine = self._tts_engine
            using_pygame = bool(self._tts_using_pygame)
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
        if using_pygame and pygame is not None:
            try:
                if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                    pygame.mixer.music.stop()
            except Exception:
                pass
        if reason:
            self._set_status(reason)
        self._update_control_states()
        return True

    def _scene_voice_audio_mode(self) -> str:
        scene = self._scene
        if scene is None:
            try:
                scene = self._node_item.scene()
            except Exception:
                scene = None
        if scene is None:
            return VOICE_AUDIO_MODE_BILATERAL
        raw = ""
        try:
            raw = str(getattr(scene, "_voice_actor_audio_mode", "") or "").strip()
        except Exception:
            raw = ""
        if not raw:
            try:
                settings = getattr(scene, "_view_settings", None)
                if isinstance(settings, dict):
                    raw = str(settings.get("voice_audio_mode", "") or "").strip()
            except Exception:
                raw = ""
        return _normalize_voice_audio_mode(raw)

    def _scene_voice_mic_device_index(self):
        scene = self._scene
        if scene is None:
            try:
                scene = self._node_item.scene()
            except Exception:
                scene = None
        if scene is None:
            return VOICE_MIC_DEVICE_DEFAULT
        raw = None
        try:
            raw = getattr(scene, "_voice_actor_mic_device_index", VOICE_MIC_DEVICE_DEFAULT)
        except Exception:
            raw = VOICE_MIC_DEVICE_DEFAULT
        if raw in (None, "", "none", "default", "system", "auto", -1, "-1"):
            try:
                settings = getattr(scene, "_view_settings", None)
                if isinstance(settings, dict):
                    raw = settings.get("voice_mic_device_index", VOICE_MIC_DEVICE_DEFAULT)
            except Exception:
                raw = VOICE_MIC_DEVICE_DEFAULT
        return _normalize_voice_mic_device_index(raw)

    def _pause_listening_for_turn_taking(self) -> bool:
        listening, _paused, stop_requested = self._stt_state()
        if not listening or stop_requested:
            return False
        self._set_stt_state(stop_requested=True, paused=False)
        self._stop_stt_send_confirmation_prompt()
        self._pause_flash_state = False
        self._update_pause_button_ui()
        self._update_control_states()
        self._set_status("Listening paused for turn-taking output.")
        return True

    def _wait_until_listening_stopped_for_turn_taking(self, timeout_seconds: float = 2.2) -> bool:
        end_at = time.monotonic() + max(0.2, float(timeout_seconds or 0.0))
        while time.monotonic() < end_at:
            listening, _paused, _stop_requested = self._stt_state()
            if not listening:
                return True
            time.sleep(0.05)
        listening, _paused, _stop_requested = self._stt_state()
        return not listening

    def _resume_listening_after_turn_taking(self) -> bool:
        listening, _paused, stop_requested = self._stt_state()
        if listening:
            return False
        if stop_requested or self._busy:
            try:
                QtCore.QTimer.singleShot(140, self._resume_listening_after_turn_taking)
            except Exception:
                pass
            return False
        if self._mode != "voice_to_text":
            return False
        self._start_stt()
        return True

    def _pause_other_voice_actors_for_turn_taking(self) -> list:
        if self._scene_voice_audio_mode() != VOICE_AUDIO_MODE_TURN_TAKING:
            return []
        scene = self._scene
        if scene is None:
            try:
                scene = self._node_item.scene()
            except Exception:
                scene = None
        if scene is None:
            return []
        paused_widgets = []
        node_items = list(getattr(scene, "_node_items", {}).values() or [])
        for item in node_items:
            if item is None or item is self._node_item:
                continue
            if _kind_of_item(item) not in VOICE_ACTOR_NODE_KINDS:
                continue
            for proxy in list(getattr(item, "_plugin_proxies", []) or []):
                if proxy is None or not hasattr(proxy, "widget"):
                    continue
                widget = proxy.widget()
                if widget is None or widget is self:
                    continue
                pause_fn = getattr(widget, "_pause_listening_for_turn_taking", None)
                if not callable(pause_fn):
                    continue
                try:
                    if pause_fn():
                        paused_widgets.append(widget)
                except Exception:
                    pass
        for widget in paused_widgets:
            wait_fn = getattr(widget, "_wait_until_listening_stopped_for_turn_taking", None)
            if callable(wait_fn):
                try:
                    wait_fn(timeout_seconds=2.2)
                except Exception:
                    pass
        return paused_widgets

    def _resume_turn_taking_paused_actors(self) -> None:
        paused = list(getattr(self, "_turn_taking_paused_actors", []) or [])
        self._turn_taking_paused_actors = []
        if not paused:
            return
        for widget in paused:
            if widget is None or widget is self:
                continue
            resume_fn = getattr(widget, "_resume_listening_after_turn_taking", None)
            if not callable(resume_fn):
                continue
            try:
                resume_fn()
            except Exception:
                pass

    def _interrupt_other_voice_actors(self) -> bool:
        scene = self._scene
        if scene is None:
            try:
                scene = self._node_item.scene()
            except Exception:
                scene = None
        if scene is None:
            return False
        node_items = list(getattr(scene, "_node_items", {}).values() or [])
        stopped_any = False
        for item in node_items:
            if item is None or item is self._node_item:
                continue
            if _kind_of_item(item) not in VOICE_ACTOR_NODE_KINDS:
                continue
            for proxy in list(getattr(item, "_plugin_proxies", []) or []):
                if proxy is None or not hasattr(proxy, "widget"):
                    continue
                widget = proxy.widget()
                if widget is None or widget is self:
                    continue
                stop_fn = getattr(widget, "_stop_tts_only", None)
                if callable(stop_fn):
                    try:
                        if stop_fn(reason="Speech stopped by voice command."):
                            stopped_any = True
                    except Exception:
                        pass
        return stopped_any

    def _stt_confirmation_action(self, text: str) -> str:
        cleaned = str(text or "").strip().lower()
        if not cleaned:
            return ""
        compact = " ".join(cleaned.split())
        if "not yet" in cleaned or "keep listening" in cleaned:
            return "continue"
        if "send it" in cleaned:
            return "send"
        if compact in {"go ahead", "go ahead send", "please send"}:
            return "send"
        tokens = {
            part.strip(".,!?;:")
            for part in cleaned.split()
            if part.strip(".,!?;:")
        }
        if tokens & STT_CONTINUE_LISTENING_WORDS:
            return "continue"
        if tokens & STT_SEND_CONFIRM_WORDS:
            return "send"
        return ""

    def _start_stt_send_confirmation_prompt(self) -> None:
        self._stop_stt_send_confirmation_prompt()
        self._stt_prompt_stop_event.clear()
        worker = threading.Thread(target=self._speak_stt_send_confirmation_prompt, daemon=True)
        with self._stt_prompt_lock:
            self._stt_prompt_thread = worker
        worker.start()

    def _stop_stt_send_confirmation_prompt(self) -> None:
        self._stt_prompt_stop_event.set()
        engine = None
        prompt_playing = False
        prompt_uses_pygame = False
        with self._stt_prompt_lock:
            engine = self._stt_prompt_engine
            prompt_playing = bool(self._stt_prompt_playing)
            prompt_uses_pygame = bool(self._stt_prompt_uses_pygame)
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
        if prompt_playing and prompt_uses_pygame and pygame is not None:
            try:
                if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                    pygame.mixer.music.stop()
            except Exception:
                pass

    def _speak_stt_send_confirmation_prompt(self) -> None:
        prompt = "Are you ready to send? Say yes, done, or send."
        selected_voice_key = str(self._selected_voice_key or "").strip() or VOICE_TANYA_GOOGLE
        stop_event = self._stt_prompt_stop_event
        prompt_slot = False
        with self._stt_prompt_lock:
            self._stt_prompt_playing = True
            self._stt_prompt_engine = None
            self._stt_prompt_uses_pygame = False

        try:
            try:
                prompt_slot = _TTS_PLAYBACK_LOCK.acquire(blocking=False)
            except Exception:
                prompt_slot = False
            if not prompt_slot:
                return
            if stop_event.is_set():
                return

            # Prefer Tanya (Google TTS) for the listen confirmation prompt when available.
            if selected_voice_key == VOICE_TANYA_GOOGLE and gTTS is not None and pygame is not None:
                temp_file = None
                loaded = False
                try:
                    temp_file = Path(tempfile.gettempdir()) / f"voice_actor_prompt_{uuid.uuid4().hex}.mp3"
                    save_err = _save_gtts_mp3(prompt, temp_file, timeout_seconds=8.0)
                    if save_err:
                        raise RuntimeError(save_err)
                    if stop_event.is_set():
                        return
                    if not pygame.mixer.get_init():
                        pygame.mixer.init()
                    if pygame.mixer.music.get_busy():
                        raise RuntimeError("pygame mixer busy")
                    pygame.mixer.music.load(str(temp_file))
                    loaded = True
                    with self._stt_prompt_lock:
                        self._stt_prompt_uses_pygame = True
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        if stop_event.is_set():
                            try:
                                pygame.mixer.music.stop()
                            except Exception:
                                pass
                            break
                        time.sleep(0.03)
                    return
                except Exception:
                    pass
                finally:
                    if loaded and pygame is not None:
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

            if pyttsx3 is None or stop_event.is_set():
                return
            engine = None
            try:
                engine = pyttsx3.init()
                with self._stt_prompt_lock:
                    self._stt_prompt_engine = engine
                voice_applied = False
                if selected_voice_key and selected_voice_key not in {VOICE_TANYA_GOOGLE, VOICE_AUTO_FEMALE}:
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
                if stop_event.is_set():
                    return
                engine.say(prompt)
                engine.runAndWait()
            except Exception:
                pass
            finally:
                if engine is not None:
                    try:
                        engine.stop()
                    except Exception:
                        pass
        finally:
            with self._stt_prompt_lock:
                self._stt_prompt_engine = None
                self._stt_prompt_playing = False
                self._stt_prompt_uses_pygame = False
                self._stt_prompt_thread = None
            if prompt_slot:
                try:
                    _TTS_PLAYBACK_LOCK.release()
                except Exception:
                    pass

    def _stt_worker(self, stt_method: str) -> None:
        transcript_parts = []
        error = ""
        selected_method = _normalize_stt_method(stt_method)
        wait_timeout = getattr(sr, "WaitTimeoutError", None) if sr is not None else None
        unknown_value = getattr(sr, "UnknownValueError", None) if sr is not None else None
        request_error = getattr(sr, "RequestError", None) if sr is not None else None
        awaiting_send_confirmation = False
        awaiting_started_at = 0.0
        prompt_response_until = 0.0
        last_voice_activity = time.monotonic()
        send_confirmed = False

        def _maybe_prompt_send_confirmation(now: float) -> bool:
            nonlocal awaiting_send_confirmation
            nonlocal awaiting_started_at
            nonlocal prompt_response_until
            nonlocal last_voice_activity

            should_prompt = (
                bool(transcript_parts)
                and not awaiting_send_confirmation
                and (now - last_voice_activity) >= STT_IDLE_CONFIRM_SECONDS
            )
            if not should_prompt:
                return False
            awaiting_send_confirmation = True
            self._stt_status.emit("Are you ready to send? Say yes, done, or send.")
            self._start_stt_send_confirmation_prompt()
            awaiting_started_at = time.monotonic()
            prompt_response_until = awaiting_started_at + STT_IDLE_CONFIRM_RESPONSE_SECONDS
            last_voice_activity = awaiting_started_at
            return True

        try:
            recognizer = sr.Recognizer()
            mic_index = _normalize_voice_mic_device_index(self._scene_voice_mic_device_index())
            mic_kwargs = {}
            if mic_index is not None:
                mic_kwargs["device_index"] = int(mic_index)
            ambient_calibrated = False
            while True:
                listening, paused, stop_requested = self._stt_state()
                if not listening or stop_requested:
                    break
                if paused:
                    if awaiting_send_confirmation:
                        awaiting_send_confirmation = False
                        prompt_response_until = 0.0
                        self._stop_stt_send_confirmation_prompt()
                    last_voice_activity = time.monotonic()
                    time.sleep(0.08)
                    continue
                with sr.Microphone(**mic_kwargs) as source:
                    if not ambient_calibrated:
                        recognizer.adjust_for_ambient_noise(source, duration=0.4)
                        ambient_calibrated = True
                    while True:
                        listening, paused, stop_requested = self._stt_state()
                        if not listening or stop_requested or paused:
                            break
                        now = time.monotonic()
                        if awaiting_send_confirmation and (now - awaiting_started_at) >= STT_IDLE_CONFIRM_RESPONSE_SECONDS:
                            awaiting_send_confirmation = False
                            prompt_response_until = 0.0
                            last_voice_activity = now
                            self._stt_status.emit("Continuing to listen...")
                        try:
                            audio = recognizer.listen(source, timeout=1.2, phrase_time_limit=20)
                        except Exception as exc:
                            if wait_timeout and isinstance(exc, wait_timeout):
                                _maybe_prompt_send_confirmation(time.monotonic())
                                continue
                            error = _format_stt_error(exc)
                            break
                        chunk = ""
                        if selected_method == STT_METHOD_LOCAL_WHISPER:
                            wav_data = audio.get_wav_data(convert_rate=16000, convert_width=2)
                            chunk, chunk_error = _transcribe_local_whisper(wav_data)
                            if chunk_error:
                                if chunk_error == "Speech detected but transcript was empty.":
                                    _maybe_prompt_send_confirmation(time.monotonic())
                                    continue
                                error = chunk_error
                                break
                        else:
                            try:
                                chunk = (recognizer.recognize_google(audio) or "").strip()
                            except Exception as exc:
                                if unknown_value and isinstance(exc, unknown_value):
                                    _maybe_prompt_send_confirmation(time.monotonic())
                                    continue
                                if request_error and isinstance(exc, request_error):
                                    error = f"Speech recognition service failed: {exc}"
                                else:
                                    error = _format_stt_error(exc)
                                break
                        clean_chunk = str(chunk or "").strip()
                        if not clean_chunk:
                            _maybe_prompt_send_confirmation(time.monotonic())
                            continue
                        if self._is_stop_speaking_command(clean_chunk):
                            awaiting_send_confirmation = False
                            prompt_response_until = 0.0
                            self._stop_stt_send_confirmation_prompt()
                            last_voice_activity = time.monotonic()
                            self._stt_status.emit("Stopping speech playback...")
                            self._stt_command.emit("stop_speaking")
                            continue
                        confirmation_window_active = awaiting_send_confirmation or (time.monotonic() <= prompt_response_until)
                        if confirmation_window_active:
                            lowered = clean_chunk.lower()
                            prompt_phrase_heard = "are you ready to send" in lowered
                            command_text = lowered.replace("are you ready to send", " ").strip() if prompt_phrase_heard else clean_chunk
                            action = self._stt_confirmation_action(command_text)
                            if action == "send":
                                send_confirmed = True
                                awaiting_send_confirmation = False
                                prompt_response_until = 0.0
                                self._stop_stt_send_confirmation_prompt()
                                self._stt_status.emit("Sending message...")
                                self._set_stt_state(stop_requested=True, paused=False)
                                break
                            if action == "continue":
                                awaiting_send_confirmation = False
                                prompt_response_until = 0.0
                                self._stop_stt_send_confirmation_prompt()
                                last_voice_activity = time.monotonic()
                                self._stt_status.emit("Continuing to listen...")
                                continue
                            if prompt_phrase_heard:
                                continue
                            # User continued talking; treat it as transcript and leave confirmation mode.
                            awaiting_send_confirmation = False
                            prompt_response_until = 0.0
                            self._stop_stt_send_confirmation_prompt()
                            transcript_parts.append(clean_chunk)
                            last_voice_activity = time.monotonic()
                            self._stt_status.emit("Continuing to listen...")
                            self._stt_chunk.emit(clean_chunk)
                            continue
                        self._stop_stt_send_confirmation_prompt()
                        transcript_parts.append(clean_chunk)
                        last_voice_activity = time.monotonic()
                        self._stt_chunk.emit(clean_chunk)
                if error:
                    break
        except Exception as exc:
            error = _format_stt_error(exc)
        self._stop_stt_send_confirmation_prompt()
        _listening, _paused, stop_requested = self._stt_state()
        if stop_requested and not error:
            error = "__sent__" if send_confirmed else "__stopped__"
        transcript = " ".join(transcript_parts).strip()
        self._stt_done.emit(transcript, error)

    @QtCore.Slot(str)
    def _on_stt_chunk(self, chunk: str) -> None:
        clean = (chunk or "").strip()
        if not clean:
            return
        self._stt_session_has_new_text = True
        self._append_transcript(clean, publish=False)
        self._update_control_states()

    @QtCore.Slot(str, str)
    def _finish_stt(self, transcript: str, error: str) -> None:
        had_new_text = bool(self._stt_session_has_new_text)
        self._stt_session_has_new_text = False
        # Chatbot handoff is done on stop; clear undo cache so it does not accumulate forever.
        self._transcript_undo_stack.clear()
        self._set_stt_state(listening=False, paused=False, stop_requested=False)
        self._pause_flash_state = False
        self._update_pause_button_ui()
        self._set_busy(False, "")
        if transcript and not had_new_text:
            self._append_transcript(transcript, publish=False)
            had_new_text = True
        if had_new_text:
            _set_node_info(self._node_item, self._transcript.toPlainText() or "")
        if error:
            if str(error).strip() == "__sent__":
                if had_new_text:
                    self._set_status("Message sent. Listening for commands...")
                else:
                    self._set_status("Listening for commands...")
                QtCore.QTimer.singleShot(120, self._resume_stt_after_send)
                return
            if str(error).strip() == "__stopped__":
                if had_new_text:
                    self._set_status("Listening stopped. Transcript updated.")
                else:
                    self._set_status("Listening stopped.")
                return
            if had_new_text:
                self._set_status(f"{error} Transcript kept from captured speech.", error=True)
                return
            self._set_status(error, error=True)
            return
        if had_new_text:
            self._set_status("Transcript updated.")
        else:
            self._set_status("No speech captured.")

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
            db_text = ""
            py_error = ""
            if self._chatbot_via_proxy:
                db_text, token, _err = _latest_chatbot_response(self._scene, source_item)
                proxy_item = self._chatbot_proxy_item
                if proxy_item is not None and _kind_of_item(proxy_item) == "python":
                    text, py_error = _run_python_transform(self._scene, proxy_item)
                    if py_error and py_error != "Python transform produced empty output.":
                        self._set_status(f"{py_error} Falling back to chatbot response.", error=True)
                if not text:
                    text = _text_from_input(self._scene, self._node_item, "text").strip()
                if not text:
                    text = str(db_text or "").strip()
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

            same_token = bool(token and token == self._last_chatbot_token)
            if same_token:
                current_text = str(self._transcript.toPlainText() or "").strip()
                if current_text == text.strip():
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
        # Clear any stale turn-taking pause list from an interrupted prior playback.
        self._resume_turn_taking_paused_actors()
        self._turn_taking_paused_actors = self._pause_other_voice_actors_for_turn_taking()
        with self._tts_lock:
            self._tts_user_stopped = False
            self._tts_playing = True
            self._tts_engine = None
            self._tts_using_pygame = False
        self._set_busy(True, "speaking")
        threading.Thread(target=self._tts_worker, args=(clean, voice_key), daemon=True).start()
        return True

    def _resolve_tts_text(self) -> tuple[str, str]:
        self._refresh_source_param_options()
        if self._chatbot_connected and self._chatbot_input_item is not None:
            if self._chatbot_via_proxy:
                scene = self._node_item.scene()
                db_text, token, _err = _latest_chatbot_response(self._scene, self._chatbot_input_item)
                if token:
                    self._last_chatbot_token = token
                proxy_item = self._chatbot_proxy_item
                if proxy_item is not None and _kind_of_item(proxy_item) == "python":
                    text, _py_error = _run_python_transform(scene, proxy_item)
                    if text.strip():
                        return text.strip(), "chatbot_latest"
                wired = _text_from_input(scene, self._node_item, "text")
                if wired.strip():
                    return wired.strip(), "chatbot_latest"
                if db_text.strip():
                    return db_text.strip(), "chatbot_latest"
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
        used_pygame = False
        playback_slot = False

        def _speak_with_local(local_voice_key: str) -> None:
            nonlocal engine
            nonlocal user_stopped
            nonlocal error
            if pyttsx3 is None:
                raise RuntimeError("Missing dependency: pyttsx3. Install: pip install pyttsx3")
            engine = pyttsx3.init()
            voice_applied = False
            if local_voice_key != VOICE_AUTO_FEMALE:
                try:
                    engine.setProperty("voice", local_voice_key)
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
                return
            engine.say(text)
            engine.runAndWait()

        try:
            selected_voice_key = str(voice_key or "").strip() or VOICE_TANYA_GOOGLE
            while not playback_slot:
                playback_slot = _TTS_PLAYBACK_LOCK.acquire(timeout=0.1)
                if playback_slot:
                    break
                with self._tts_lock:
                    user_stopped = bool(self._tts_user_stopped)
                if user_stopped:
                    error = "__stopped__"
                    break
            if error:
                return

            if selected_voice_key == VOICE_TANYA_GOOGLE:
                google_error = ""
                if gTTS is not None and pygame is not None:
                    try:
                        temp_file = Path(tempfile.gettempdir()) / f"voice_actor_{uuid.uuid4().hex}.mp3"
                        save_err = _save_gtts_mp3(text, temp_file, timeout_seconds=12.0)
                        if save_err:
                            raise RuntimeError(save_err)
                        if not pygame.mixer.get_init():
                            pygame.mixer.init()
                        with self._tts_lock:
                            self._tts_using_pygame = True
                        used_pygame = True
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
                    except Exception as exc:
                        google_error = str(exc)
                else:
                    google_error = "Google voice dependencies are missing."
                if google_error and not user_stopped:
                    error = f"Tanya (Google) voice failed: {google_error}"
                    return
            else:
                _speak_with_local(selected_voice_key)
        except Exception as exc:
            error = f"Text-to-speech failed: {exc}"
        finally:
            if used_pygame and pygame is not None:
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
            if playback_slot:
                try:
                    _TTS_PLAYBACK_LOCK.release()
                except Exception:
                    pass
            with self._tts_lock:
                if self._tts_engine is engine:
                    self._tts_engine = None
                self._tts_using_pygame = False
                user_stopped = bool(self._tts_user_stopped)
                self._tts_playing = False
        if user_stopped and not error:
            error = "__stopped__"
        self._tts_done.emit(error)

    @QtCore.Slot(str)
    def _finish_tts(self, error: str) -> None:
        self._set_busy(False, "")
        self._resume_turn_taking_paused_actors()
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
