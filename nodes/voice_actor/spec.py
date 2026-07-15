from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import re
import threading
import tempfile
import time
import uuid
import wave
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
VOICE_ACTOR_BODY_H = 330
VOICE_ACTOR_ICON_BTN_SIDE = 56
VOICE_ACTOR_SELECTED_PARAM_KEY = "__voice_actor_selected_param"
VOICE_ACTOR_MODE_KEY = "__voice_actor_mode"
VOICE_ACTOR_VOICE_KEY = "__voice_actor_voice"
VOICE_ACTOR_VOICE_GENDER_KEY = "__voice_actor_voice_gender"
VOICE_ACTOR_STT_METHOD_KEY = "__voice_actor_stt_method"
VOICE_ACTOR_STT_LANGUAGE_KEY = "__voice_actor_stt_language"
VOICE_ACTOR_SEND_MODE_KEY = "__voice_actor_send_mode"
VOICE_ACTOR_SEND_TOKEN_KEY = "__voice_actor_send_token"
VOICE_TANYA_GOOGLE = "__google_tanya__"
VOICE_KOKORO_82M = "__kokoro_82m__"
VOICE_LOCAL_DESKTOP = "__local_desktop__"
VOICE_AUTO_FEMALE = "__auto_female__"
STT_METHOD_LOCAL_WHISPER = "local_whisper"
STT_METHOD_GOOGLE = "google"
STT_SEND_MODE_AUTO_RESPOND = "auto_respond"
STT_SEND_MODE_ASK_FIRST = "ask_first"
STT_SEND_MODE_MANUAL = "manual"
VOICE_GENDER_FEMALE = "female"
VOICE_GENDER_MALE = "male"
LOCAL_WHISPER_MODEL_NAME = "base"
LOCAL_WHISPER_LANGUAGE = "en"
KOKORO_DEFAULT_VOICE = "af_heart"
KOKORO_DEFAULT_VOICES_BY_LANGUAGE = {
    "en": {
        VOICE_GENDER_FEMALE: "af_heart",
        VOICE_GENDER_MALE: "am_adam",
    },
    "es": {
        VOICE_GENDER_FEMALE: "ef_dora",
        VOICE_GENDER_MALE: "em_alex",
    },
    "ja": {
        VOICE_GENDER_FEMALE: "jf_alpha",
        VOICE_GENDER_MALE: "jm_kumo",
    },
    "zh": {
        VOICE_GENDER_FEMALE: "zf_xiaoxiao",
        VOICE_GENDER_MALE: "zm_yunjian",
    },
}
KOKORO_LANG_CODES = {
    "en": "a",
    "es": "e",
    "ja": "j",
    "ko": "k",
    "zh": "z",
}
STT_LANGUAGE_OPTIONS = (
    {"id": "en", "name": "English", "whisper": "en", "google": "en-US", "tts": "en"},
    {"id": "es", "name": "Spanish", "whisper": "es", "google": "es-ES", "tts": "es"},
    {"id": "ja", "name": "Japanese", "whisper": "ja", "google": "ja-JP", "tts": "ja"},
    {"id": "ko", "name": "Korean", "whisper": "ko", "google": "ko-KR", "tts": "ko"},
    {"id": "zh", "name": "Chinese", "whisper": "zh", "google": "zh-CN", "tts": "zh-CN"},
)
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
# Only treat narrow relay/proxy nodes as auto speech sources.
# This keeps auto speech working through common pass-through nodes
# without matching unrelated agent chains.
CHATBOT_PROXY_NODE_KINDS = {"python", "output", "wire", "switch"}
MEDIGATOR_NODE_KINDS = {
    "medigator_agent",
    "mediator_agent",
    "medigator agent",
    "mediator agent",
    "medigator",
    "mediator",
}
MEDIGATOR_OUTPUT_TOKEN_KEY = "__medigator_output_token"
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
    "haruka",
    "ayumi",
    "sayaka",
    "sabina",
    "helena",
    "huihui",
    "yaoyao",
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
    "ichiro",
    "pablo",
    "raul",
    "kangkang",
)

_WHISPER_MODEL = None
_WHISPER_MODEL_LOCK = threading.Lock()
_KOKORO_PIPELINES = {}
_KOKORO_PIPELINE_LOCK = threading.Lock()
_TTS_PLAYBACK_LOCK = threading.Lock()
_USER_FEEDBACK_TAG_RE = re.compile(
    r"<\s*(?:user[\s_-]*feedback|user[\s_-]*feed[\s_-]*back|feedback)\s*>"
    r"(.*?)"
    r"<\s*/\s*(?:user[\s_-]*feedback|user[\s_-]*feed[\s_-]*back|feedback)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_SECURITY_REQUEST_TAG_RE = re.compile(
    r"<\s*security_request\b[^>]*>.*?<\s*/\s*security_request\s*>",
    re.IGNORECASE | re.DOTALL,
)
_VOICE_CONTROL_TAG_RE = re.compile(
    r"<\s*/?\s*(?:security_request|security_approval)\b[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _new_gtts(text: str, language: str = LOCAL_WHISPER_LANGUAGE):
    if gTTS is None:
        return None
    lang = _tts_google_language(language)
    # Newer gTTS builds support timeout; older builds may not.
    try:
        return gTTS(text=text, lang=lang, timeout=8)
    except TypeError:
        return gTTS(text=text, lang=lang)


def _save_gtts_mp3(
    text: str,
    target_path: Path,
    *,
    timeout_seconds: float = 12.0,
    language: str = LOCAL_WHISPER_LANGUAGE,
) -> str:
    done = threading.Event()
    errors = []

    def _worker():
        try:
            tts = _new_gtts(text, language=language)
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


def _kokoro_available() -> bool:
    try:
        return importlib.util.find_spec("kokoro") is not None
    except Exception:
        return False


def _kokoro_lang_code(language: str) -> str:
    normalized = _normalize_stt_language(language)
    return str(KOKORO_LANG_CODES.get(normalized, "a") or "a")


def _normalize_voice_gender(value: str) -> str:
    key = str(value or "").strip().lower()
    if key in {"m", "male", "man", "masculine"}:
        return VOICE_GENDER_MALE
    return VOICE_GENDER_FEMALE


def _kokoro_voice_for_language(language: str, gender: str = VOICE_GENDER_FEMALE) -> str:
    normalized = _normalize_stt_language(language)
    voices = KOKORO_DEFAULT_VOICES_BY_LANGUAGE.get(normalized)
    if isinstance(voices, dict):
        selected_gender = _normalize_voice_gender(gender)
        voice = voices.get(selected_gender) or voices.get(VOICE_GENDER_FEMALE) or KOKORO_DEFAULT_VOICE
    else:
        voice = KOKORO_DEFAULT_VOICE
    return str(voice or KOKORO_DEFAULT_VOICE)


def _mecab_dictionary_ready(dicdir) -> bool:
    if not dicdir:
        return False
    try:
        return (Path(str(dicdir)) / "mecabrc").exists()
    except Exception:
        return False


def _prepare_kokoro_japanese_backend() -> str:
    try:
        import unidic  # type: ignore
    except Exception:
        unidic = None

    if unidic is not None and _mecab_dictionary_ready(getattr(unidic, "DICDIR", "")):
        return ""

    try:
        import unidic_lite  # type: ignore
    except Exception:
        return (
            "Kokoro Japanese voice needs a MeCab dictionary. "
            "Install the compact bundled dictionary: pip install unidic-lite. "
            "Full UniDic also works after: python -m unidic download."
        )

    lite_dicdir = getattr(unidic_lite, "DICDIR", "")
    if not _mecab_dictionary_ready(lite_dicdir):
        return (
            "Kokoro Japanese voice found unidic-lite, but its MeCab dictionary is incomplete. "
            "Reinstall it with: pip install --force-reinstall unidic-lite."
        )

    if unidic is not None:
        try:
            unidic.DICDIR = lite_dicdir
            unidic.VERSION = getattr(unidic_lite, "VERSION", getattr(unidic, "VERSION", ""))
        except Exception:
            pass
    return ""


def _kokoro_init_error(lang_code: str, exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    if lang_code == "j":
        return (
            "Kokoro Japanese voice dependencies are incomplete. "
            "Run setup.bat or install: pip install \"misaki[ja]\" unidic-lite. "
            f"({detail})"
        )
    if lang_code == "z":
        return (
            "Kokoro Chinese voice dependencies are missing. "
            "Run setup.bat or install: pip install \"misaki[zh]\". "
            f"({detail})"
        )
    return f"Kokoro init failed for language '{lang_code}': {detail}"


def _kokoro_pipeline(language: str):
    lang_code = _kokoro_lang_code(language)
    with _KOKORO_PIPELINE_LOCK:
        cached = _KOKORO_PIPELINES.get(lang_code)
        if cached is not None:
            return cached, ""
        try:
            from kokoro import KPipeline  # type: ignore
        except Exception as exc:
            return None, f"Missing dependency: kokoro. Run setup.bat or pip install kokoro. ({exc})"
        if lang_code == "j":
            japanese_err = _prepare_kokoro_japanese_backend()
            if japanese_err:
                return None, japanese_err
        try:
            pipeline = KPipeline(lang_code=lang_code)
        except TypeError:
            try:
                pipeline = KPipeline(lang_code)
            except Exception as exc:
                return None, _kokoro_init_error(lang_code, exc)
        except Exception as exc:
            return None, _kokoro_init_error(lang_code, exc)
        _KOKORO_PIPELINES[lang_code] = pipeline
        return pipeline, ""


def _audio_to_pcm16_bytes(audio) -> bytes:
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"Missing dependency: numpy. ({exc})") from exc
    if audio is None:
        raise RuntimeError("Kokoro produced empty audio.")
    if hasattr(audio, "detach"):
        audio = audio.detach()
    if hasattr(audio, "cpu"):
        audio = audio.cpu()
    if hasattr(audio, "numpy"):
        audio = audio.numpy()
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size <= 0:
        raise RuntimeError("Kokoro produced empty audio.")
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()


def _kokoro_result_audio(item):
    audio = getattr(item, "audio", None)
    if audio is not None:
        return audio
    output = getattr(item, "output", None)
    audio = getattr(output, "audio", None)
    if audio is not None:
        return audio
    if isinstance(item, (tuple, list)) and item:
        return item[-1]
    return item


def _save_kokoro_wav(
    text: str,
    target_path: Path,
    *,
    language: str = LOCAL_WHISPER_LANGUAGE,
    voice: str = KOKORO_DEFAULT_VOICE,
    sample_rate: int = 24000,
) -> str:
    pipeline, err = _kokoro_pipeline(language)
    if err or pipeline is None:
        return err or "Kokoro is unavailable."
    try:
        generator = pipeline(text, voice=voice, speed=1)
        chunks = []
        for item in generator:
            chunks.append(_audio_to_pcm16_bytes(_kokoro_result_audio(item)))
        if not chunks:
            return "Kokoro produced no audio."
        with wave.open(str(target_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate))
            for chunk in chunks:
                wav.writeframes(chunk)
        return ""
    except Exception as exc:
        lang_name = _stt_language_name(language)
        return f"Kokoro-82M failed for {lang_name}: {exc}"


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


def _publish_voice_command(node_item, text: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    value = text or ""
    model.info = value
    params = list(getattr(model, "params", None) or [])
    token_value = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"
    token_key = VOICE_ACTOR_SEND_TOKEN_KEY.strip().lower()
    found = False
    for entry in params:
        if (entry.get("name") or "").strip().lower() == token_key:
            entry["value"] = token_value
            found = True
            break
    if not found:
        params.append({"name": VOICE_ACTOR_SEND_TOKEN_KEY, "value": token_value})
    model.params = params
    _ensure_hidden_params(model, [VOICE_ACTOR_SEND_TOKEN_KEY])
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


def _auto_speech_source_kind(node_item) -> str:
    kind = _kind_of_item(node_item)
    if kind in CHATBOT_NODE_KINDS:
        return "chatbot"
    if kind in MEDIGATOR_NODE_KINDS:
        return "mediator"
    return ""


def _auto_speech_source_label(node_item) -> str:
    kind = _auto_speech_source_kind(node_item)
    if kind == "mediator":
        return "Mediator"
    if kind == "chatbot":
        return "Chatbot"
    return "Auto source"


def _find_upstream_auto_speech_source(scene, node_item, max_depth: int = 6, _visited=None):
    if not scene or node_item is None:
        return None
    if _auto_speech_source_kind(node_item):
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
        found = _find_upstream_auto_speech_source(scene, src, max_depth=max_depth - 1, _visited=_visited)
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


def _latest_mediator_response(_scene, mediator_item) -> tuple[str, str, str]:
    if mediator_item is None:
        return "", "", "Mediator input is unavailable."
    model = getattr(mediator_item, "model", None)
    if model is None:
        return "", "", "Mediator input is unavailable."
    text = str(getattr(model, "info", "") or "").strip()
    token = _param_value(model, MEDIGATOR_OUTPUT_TOKEN_KEY, "").strip()
    if not token and text:
        token = f"info:{hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()}"
    if not text:
        return "", token, "No Mediator output available yet."
    return text, token, ""


def _latest_auto_speech_response(scene, source_item) -> tuple[str, str, str]:
    kind = _auto_speech_source_kind(source_item)
    if kind == "mediator":
        return _latest_mediator_response(scene, source_item)
    return _latest_chatbot_response(scene, source_item)


def _extract_user_feedback_text(text: str) -> str:
    matches = []
    for match in _USER_FEEDBACK_TAG_RE.finditer(str(text or "")):
        clean = str(match.group(1) or "").strip()
        if clean:
            matches.append(clean)
    return matches[-1] if matches else ""


def _clean_voice_text(text: str) -> str:
    cleaned = str(text or "")
    cleaned = _SECURITY_REQUEST_TAG_RE.sub("", cleaned)
    cleaned = _VOICE_CONTROL_TAG_RE.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = _MULTI_BLANK_RE.sub("\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _proxy_auto_speech_text(source_item, text: str, *, fallback_text: str = "") -> str:
    clean = str(text or "").strip()
    if _auto_speech_source_kind(source_item) != "mediator":
        return _clean_voice_text(clean)
    feedback = _extract_user_feedback_text(clean)
    if feedback:
        return feedback
    stripped = _clean_voice_text(clean)
    if stripped and stripped != clean:
        return stripped
    if not clean:
        fallback_feedback = _extract_user_feedback_text(fallback_text)
        if fallback_feedback:
            return fallback_feedback
        fallback_stripped = _clean_voice_text(fallback_text)
        if fallback_stripped:
            return fallback_stripped
    return stripped or clean


def _pick_desktop_voice_id(engine, gender: str = VOICE_GENDER_FEMALE) -> str:
    if engine is None:
        return ""
    selected_gender = _normalize_voice_gender(gender)
    positive_hints = MALE_VOICE_HINTS if selected_gender == VOICE_GENDER_MALE else FEMALE_VOICE_HINTS
    negative_hints = FEMALE_VOICE_HINTS if selected_gender == VOICE_GENDER_MALE else MALE_VOICE_HINTS
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
        for hint in positive_hints:
            if hint in blob:
                score += 2.0
        for hint in negative_hints:
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


def _pick_female_voice_id(engine) -> str:
    return _pick_desktop_voice_id(engine, VOICE_GENDER_FEMALE)


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


def _normalize_stt_language(value: str) -> str:
    key = str(value or "").strip().lower().replace("_", "-")
    if not key:
        return LOCAL_WHISPER_LANGUAGE
    aliases = {
        "english": "en",
        "en-us": "en",
        "en-gb": "en",
        "spanish": "es",
        "es-es": "es",
        "es-us": "es",
        "es-mx": "es",
        "japanese": "ja",
        "jp": "ja",
        "ja-jp": "ja",
        "korean": "ko",
        "kr": "ko",
        "ko-kr": "ko",
        "chinese": "zh",
        "mandarin": "zh",
        "zh-cn": "zh",
        "zh-hans": "zh",
        "zh-hans-cn": "zh",
        "cmn-hans-cn": "zh",
    }
    key = aliases.get(key, key)
    for option in STT_LANGUAGE_OPTIONS:
        if key == str(option.get("id", "")).lower():
            return str(option.get("id", "") or LOCAL_WHISPER_LANGUAGE)
        if key == str(option.get("whisper", "")).lower():
            return str(option.get("id", "") or LOCAL_WHISPER_LANGUAGE)
        if key == str(option.get("google", "")).lower():
            return str(option.get("id", "") or LOCAL_WHISPER_LANGUAGE)
    return LOCAL_WHISPER_LANGUAGE


def _stt_language_option(value: str) -> dict:
    key = _normalize_stt_language(value)
    for option in STT_LANGUAGE_OPTIONS:
        if str(option.get("id", "") or "") == key:
            return option
    return STT_LANGUAGE_OPTIONS[0]


def _stt_language_name(value: str) -> str:
    return str(_stt_language_option(value).get("name", "") or "English")


def _stt_whisper_language(value: str) -> str:
    return str(_stt_language_option(value).get("whisper", "") or LOCAL_WHISPER_LANGUAGE)


def _stt_google_language(value: str) -> str:
    return str(_stt_language_option(value).get("google", "") or "en-US")


def _tts_google_language(value: str) -> str:
    return str(_stt_language_option(value).get("tts", "") or LOCAL_WHISPER_LANGUAGE)


_KO_BASE_KEY_TO_JAMO = {
    "r": "ㄱ",
    "s": "ㄴ",
    "e": "ㄷ",
    "f": "ㄹ",
    "a": "ㅁ",
    "q": "ㅂ",
    "t": "ㅅ",
    "d": "ㅇ",
    "w": "ㅈ",
    "c": "ㅊ",
    "z": "ㅋ",
    "x": "ㅌ",
    "v": "ㅍ",
    "g": "ㅎ",
    "k": "ㅏ",
    "o": "ㅐ",
    "i": "ㅑ",
    "j": "ㅓ",
    "p": "ㅔ",
    "u": "ㅕ",
    "h": "ㅗ",
    "y": "ㅛ",
    "n": "ㅜ",
    "b": "ㅠ",
    "m": "ㅡ",
    "l": "ㅣ",
}
_KO_SHIFT_KEY_TO_JAMO = {
    "R": "ㄲ",
    "E": "ㄸ",
    "Q": "ㅃ",
    "T": "ㅆ",
    "W": "ㅉ",
    "O": "ㅒ",
    "P": "ㅖ",
}
_KO_KEY_TO_JAMO = dict(_KO_BASE_KEY_TO_JAMO)
for _ko_key, _ko_jamo in list(_KO_BASE_KEY_TO_JAMO.items()):
    _KO_KEY_TO_JAMO.setdefault(_ko_key.upper(), _ko_jamo)
_KO_KEY_TO_JAMO.update(_KO_SHIFT_KEY_TO_JAMO)

_KO_INITIALS = (
    "ㄱ",
    "ㄲ",
    "ㄴ",
    "ㄷ",
    "ㄸ",
    "ㄹ",
    "ㅁ",
    "ㅂ",
    "ㅃ",
    "ㅅ",
    "ㅆ",
    "ㅇ",
    "ㅈ",
    "ㅉ",
    "ㅊ",
    "ㅋ",
    "ㅌ",
    "ㅍ",
    "ㅎ",
)
_KO_VOWELS = (
    "ㅏ",
    "ㅐ",
    "ㅑ",
    "ㅒ",
    "ㅓ",
    "ㅔ",
    "ㅕ",
    "ㅖ",
    "ㅗ",
    "ㅘ",
    "ㅙ",
    "ㅚ",
    "ㅛ",
    "ㅜ",
    "ㅝ",
    "ㅞ",
    "ㅟ",
    "ㅠ",
    "ㅡ",
    "ㅢ",
    "ㅣ",
)
_KO_FINALS = (
    "",
    "ㄱ",
    "ㄲ",
    "ㄳ",
    "ㄴ",
    "ㄵ",
    "ㄶ",
    "ㄷ",
    "ㄹ",
    "ㄺ",
    "ㄻ",
    "ㄼ",
    "ㄽ",
    "ㄾ",
    "ㄿ",
    "ㅀ",
    "ㅁ",
    "ㅂ",
    "ㅄ",
    "ㅅ",
    "ㅆ",
    "ㅇ",
    "ㅈ",
    "ㅊ",
    "ㅋ",
    "ㅌ",
    "ㅍ",
    "ㅎ",
)
_KO_INITIAL_INDEX = {value: idx for idx, value in enumerate(_KO_INITIALS)}
_KO_VOWEL_INDEX = {value: idx for idx, value in enumerate(_KO_VOWELS)}
_KO_FINAL_INDEX = {value: idx for idx, value in enumerate(_KO_FINALS)}
_KO_CONSONANTS = set(_KO_INITIALS)
_KO_VOWEL_SET = set(_KO_VOWELS)
_KO_VOWEL_COMBOS = {
    ("ㅗ", "ㅏ"): "ㅘ",
    ("ㅗ", "ㅐ"): "ㅙ",
    ("ㅗ", "ㅣ"): "ㅚ",
    ("ㅜ", "ㅓ"): "ㅝ",
    ("ㅜ", "ㅔ"): "ㅞ",
    ("ㅜ", "ㅣ"): "ㅟ",
    ("ㅡ", "ㅣ"): "ㅢ",
}
_KO_FINAL_COMBOS = {
    ("ㄱ", "ㅅ"): "ㄳ",
    ("ㄴ", "ㅈ"): "ㄵ",
    ("ㄴ", "ㅎ"): "ㄶ",
    ("ㄹ", "ㄱ"): "ㄺ",
    ("ㄹ", "ㅁ"): "ㄻ",
    ("ㄹ", "ㅂ"): "ㄼ",
    ("ㄹ", "ㅅ"): "ㄽ",
    ("ㄹ", "ㅌ"): "ㄾ",
    ("ㄹ", "ㅍ"): "ㄿ",
    ("ㄹ", "ㅎ"): "ㅀ",
    ("ㅂ", "ㅅ"): "ㅄ",
}
_KO_JAMO_TO_KEYS = {
    "ㄱ": "r",
    "ㄲ": "R",
    "ㄳ": "rt",
    "ㄴ": "s",
    "ㄵ": "sw",
    "ㄶ": "sg",
    "ㄷ": "e",
    "ㄸ": "E",
    "ㄹ": "f",
    "ㄺ": "fr",
    "ㄻ": "fa",
    "ㄼ": "fq",
    "ㄽ": "ft",
    "ㄾ": "fx",
    "ㄿ": "fv",
    "ㅀ": "fg",
    "ㅁ": "a",
    "ㅂ": "q",
    "ㅃ": "Q",
    "ㅄ": "qt",
    "ㅅ": "t",
    "ㅆ": "T",
    "ㅇ": "d",
    "ㅈ": "w",
    "ㅉ": "W",
    "ㅊ": "c",
    "ㅋ": "z",
    "ㅌ": "x",
    "ㅍ": "v",
    "ㅎ": "g",
    "ㅏ": "k",
    "ㅐ": "o",
    "ㅑ": "i",
    "ㅒ": "O",
    "ㅓ": "j",
    "ㅔ": "p",
    "ㅕ": "u",
    "ㅖ": "P",
    "ㅗ": "h",
    "ㅘ": "hk",
    "ㅙ": "ho",
    "ㅚ": "hl",
    "ㅛ": "y",
    "ㅜ": "n",
    "ㅝ": "nj",
    "ㅞ": "np",
    "ㅟ": "nl",
    "ㅠ": "b",
    "ㅡ": "m",
    "ㅢ": "ml",
    "ㅣ": "l",
}


def _is_hangul_syllable(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    return 0xAC00 <= code <= 0xD7A3


def _is_korean_keyboard_segment_char(ch: str) -> bool:
    return bool(
        ch in _KO_KEY_TO_JAMO
        or ch in _KO_JAMO_TO_KEYS
        or _is_hangul_syllable(ch)
    )


def _compose_korean_syllable(initial: str, vowel: str, final: str = "") -> str:
    if initial not in _KO_INITIAL_INDEX or vowel not in _KO_VOWEL_INDEX:
        return f"{initial}{vowel}{final}"
    final_idx = _KO_FINAL_INDEX.get(final, 0)
    code = 0xAC00 + (
        (_KO_INITIAL_INDEX[initial] * len(_KO_VOWELS) + _KO_VOWEL_INDEX[vowel])
        * len(_KO_FINALS)
    ) + final_idx
    return chr(code)


def _hangul_syllable_to_keys(ch: str) -> str:
    if not _is_hangul_syllable(ch):
        return ""
    syllable = ord(ch) - 0xAC00
    final_idx = syllable % len(_KO_FINALS)
    vowel_idx = (syllable // len(_KO_FINALS)) % len(_KO_VOWELS)
    initial_idx = syllable // (len(_KO_FINALS) * len(_KO_VOWELS))
    return (
        _KO_JAMO_TO_KEYS.get(_KO_INITIALS[initial_idx], "")
        + _KO_JAMO_TO_KEYS.get(_KO_VOWELS[vowel_idx], "")
        + _KO_JAMO_TO_KEYS.get(_KO_FINALS[final_idx], "")
    )


def _korean_text_to_keyboard_sequence(text: str) -> str:
    parts = []
    for ch in str(text or ""):
        if ch in _KO_KEY_TO_JAMO:
            parts.append(ch)
        elif _is_hangul_syllable(ch):
            parts.append(_hangul_syllable_to_keys(ch))
        elif ch in _KO_JAMO_TO_KEYS:
            parts.append(_KO_JAMO_TO_KEYS.get(ch, ""))
    return "".join(parts)


def _consume_korean_vowel(tokens: list[str], index: int) -> tuple[str, int]:
    if index >= len(tokens) or tokens[index] not in _KO_VOWEL_SET:
        return "", index
    vowel = tokens[index]
    if index + 1 < len(tokens):
        combined = _KO_VOWEL_COMBOS.get((vowel, tokens[index + 1]))
        if combined:
            return combined, index + 2
    return vowel, index + 1


def _consume_korean_final(tokens: list[str], index: int) -> tuple[str, int]:
    if index >= len(tokens) or tokens[index] not in _KO_CONSONANTS:
        return "", index
    first = tokens[index]
    if index + 1 < len(tokens) and tokens[index + 1] in _KO_VOWEL_SET:
        return "", index
    if index + 1 < len(tokens) and tokens[index + 1] in _KO_CONSONANTS:
        combined = _KO_FINAL_COMBOS.get((first, tokens[index + 1]))
        if combined:
            if index + 2 < len(tokens) and tokens[index + 2] in _KO_VOWEL_SET:
                if first in _KO_FINAL_INDEX:
                    return first, index + 1
                return "", index
            return combined, index + 2
    if first in _KO_FINAL_INDEX:
        return first, index + 1
    return "", index


def _korean_keyboard_to_hangul(keys: str) -> str:
    tokens = [_KO_KEY_TO_JAMO[ch] for ch in str(keys or "") if ch in _KO_KEY_TO_JAMO]
    out = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _KO_CONSONANTS:
            if index + 1 < len(tokens) and tokens[index + 1] in _KO_VOWEL_SET:
                vowel, after_vowel = _consume_korean_vowel(tokens, index + 1)
                final, after_final = _consume_korean_final(tokens, after_vowel)
                out.append(_compose_korean_syllable(token, vowel, final))
                index = after_final
            else:
                out.append(token)
                index += 1
            continue
        if token in _KO_VOWEL_SET:
            vowel, after_vowel = _consume_korean_vowel(tokens, index)
            out.append(vowel)
            index = after_vowel
            continue
        out.append(token)
        index += 1
    return "".join(out)


def _convert_korean_keyboard_runs(text: str) -> str:
    source = str(text or "")
    out = []
    run = []
    for ch in source:
        if ch in _KO_KEY_TO_JAMO:
            run.append(ch)
            continue
        if run:
            out.append(_korean_keyboard_to_hangul("".join(run)))
            run = []
        out.append(ch)
    if run:
        out.append(_korean_keyboard_to_hangul("".join(run)))
    return "".join(out)


def _normalize_stt_send_mode(value: str) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in {"ask", "ask_first", "ask_for_approval", "approval"}:
        return STT_SEND_MODE_ASK_FIRST
    if key in {"manual", "push_to_talk", "push_to_send", "stop_to_send"}:
        return STT_SEND_MODE_MANUAL
    return STT_SEND_MODE_AUTO_RESPOND


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


def _transcribe_local_whisper(audio_wav: bytes, language: str = LOCAL_WHISPER_LANGUAGE) -> tuple[str, str]:
    model, err = _whisper_model()
    if err or model is None:
        return "", err or "Local Whisper model is unavailable."
    wav_path = Path(tempfile.gettempdir()) / f"voice_actor_stt_{uuid.uuid4().hex}.wav"
    try:
        wav_path.write_bytes(audio_wav)
        segments, _info = model.transcribe(
            str(wav_path),
            language=_stt_whisper_language(language),
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


class VoiceActorTranscriptEdit(QtWidgets.QPlainTextEdit):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def _language(self) -> str:
        try:
            return _normalize_stt_language(getattr(self._owner, "_selected_stt_language", ""))
        except Exception:
            return LOCAL_WHISPER_LANGUAGE

    def _korean_keyboard_enabled(self) -> bool:
        return self._language() == "ko"

    def _qt_enum_value(self, group_name: str, member_name: str):
        group = getattr(QtCore.Qt, group_name, None)
        if group is not None:
            value = getattr(group, member_name, None)
            if value is not None:
                return value
        return getattr(QtCore.Qt, member_name, None)

    def _event_key_is(self, event, member_name: str) -> bool:
        expected = self._qt_enum_value("Key", member_name)
        if expected is None:
            return False
        try:
            return int(event.key()) == int(expected)
        except Exception:
            try:
                return int(event.key()) == int(expected.value)
            except Exception:
                return event.key() == expected

    def _has_shortcut_modifier(self, event) -> bool:
        modifiers = event.modifiers()
        for member_name in ("ControlModifier", "AltModifier", "MetaModifier"):
            flag = self._qt_enum_value("KeyboardModifier", member_name)
            if flag is None:
                continue
            try:
                if modifiers & flag:
                    return True
            except Exception:
                try:
                    if int(modifiers) & int(flag):
                        return True
                except Exception:
                    pass
        return False

    def _keep_anchor(self):
        value = getattr(QtGui.QTextCursor, "KeepAnchor", None)
        if value is not None:
            return value
        move_mode = getattr(QtGui.QTextCursor, "MoveMode", None)
        return getattr(move_mode, "KeepAnchor", None)

    def _replace_range(self, start: int, end: int, text: str) -> None:
        cursor = self.textCursor()
        cursor.setPosition(max(0, int(start)))
        keep_anchor = self._keep_anchor()
        if keep_anchor is not None:
            cursor.setPosition(max(0, int(end)), keep_anchor)
        else:
            cursor.setPosition(max(0, int(end)), QtGui.QTextCursor.KeepAnchor)
        cursor.insertText(text or "")
        self.setTextCursor(cursor)

    def _korean_segment_start(self, text: str, pos: int) -> int:
        start = max(0, min(int(pos), len(text)))
        while start > 0 and _is_korean_keyboard_segment_char(text[start - 1]):
            start -= 1
        return start

    def _insert_korean_key(self, key_text: str) -> bool:
        if key_text not in _KO_KEY_TO_JAMO:
            return False
        cursor = self.textCursor()
        replacement = _korean_keyboard_to_hangul(key_text)
        if cursor.hasSelection():
            self._replace_range(cursor.selectionStart(), cursor.selectionEnd(), replacement)
            return True
        text = self.toPlainText()
        pos = cursor.position()
        start = self._korean_segment_start(text, pos)
        keys = _korean_text_to_keyboard_sequence(text[start:pos]) + key_text
        self._replace_range(start, pos, _korean_keyboard_to_hangul(keys))
        return True

    def _backspace_korean_key(self) -> bool:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        text = self.toPlainText()
        pos = cursor.position()
        start = self._korean_segment_start(text, pos)
        if start >= pos:
            return False
        keys = _korean_text_to_keyboard_sequence(text[start:pos])
        if not keys:
            return False
        self._replace_range(start, pos, _korean_keyboard_to_hangul(keys[:-1]))
        return True

    def keyPressEvent(self, event) -> None:
        if self._korean_keyboard_enabled() and not self._has_shortcut_modifier(event):
            if self._event_key_is(event, "Key_Backspace") and self._backspace_korean_key():
                return
            text = event.text()
            if len(text or "") == 1 and self._insert_korean_key(text):
                return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source) -> None:
        try:
            if self._korean_keyboard_enabled() and source is not None and source.hasText():
                self.textCursor().insertText(_convert_korean_keyboard_runs(source.text()))
                return
        except Exception:
            pass
        super().insertFromMimeData(source)


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
        self._send_icon = _voice_action_icon("Send_Icon.png")
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
        self._syncing_voice_gender_combo = False
        self._syncing_stt_combo = False
        self._syncing_stt_language_combo = False
        self._syncing_send_mode_combo = False
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
        self._pause_button_role = "pause"
        self._replay_role = "replay"
        self._stop_role = "erase"
        self._transcript_undo_stack = []
        model = getattr(self._node_item, "model", None)
        saved_mode = _param_value(model, VOICE_ACTOR_MODE_KEY, "").strip().lower()
        self._mode = "text_to_voice" if saved_mode == "text_to_voice" else "voice_to_text"
        self._selected_param_key = _param_value(model, VOICE_ACTOR_SELECTED_PARAM_KEY, "")
        saved_voice_key = _param_value(model, VOICE_ACTOR_VOICE_KEY, "").strip()
        self._selected_voice_key = saved_voice_key or VOICE_TANYA_GOOGLE
        saved_voice_gender = _param_value(model, VOICE_ACTOR_VOICE_GENDER_KEY, "").strip()
        self._selected_voice_gender = _normalize_voice_gender(saved_voice_gender)
        saved_stt_method = _param_value(model, VOICE_ACTOR_STT_METHOD_KEY, "").strip()
        self._selected_stt_method = _normalize_stt_method(saved_stt_method or STT_METHOD_LOCAL_WHISPER)
        saved_stt_language = _param_value(model, VOICE_ACTOR_STT_LANGUAGE_KEY, "").strip()
        self._selected_stt_language = _normalize_stt_language(saved_stt_language or LOCAL_WHISPER_LANGUAGE)
        saved_send_mode = _param_value(model, VOICE_ACTOR_SEND_MODE_KEY, "").strip()
        self._selected_send_mode = _normalize_stt_send_mode(saved_send_mode or STT_SEND_MODE_AUTO_RESPOND)
        self._had_note_input = False
        if self._selected_param_key:
            _ensure_hidden_params(model, [VOICE_ACTOR_SELECTED_PARAM_KEY])
        if saved_mode:
            _ensure_hidden_params(model, [VOICE_ACTOR_MODE_KEY])
        if saved_voice_key:
            _ensure_hidden_params(model, [VOICE_ACTOR_VOICE_KEY])
        if saved_voice_gender:
            _ensure_hidden_params(model, [VOICE_ACTOR_VOICE_GENDER_KEY])
        if saved_stt_method:
            _ensure_hidden_params(model, [VOICE_ACTOR_STT_METHOD_KEY])
        if saved_stt_language:
            _ensure_hidden_params(model, [VOICE_ACTOR_STT_LANGUAGE_KEY])
        if saved_send_mode:
            _ensure_hidden_params(model, [VOICE_ACTOR_SEND_MODE_KEY])

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
        self._pause_btn.clicked.connect(self._on_pause_or_send_clicked)

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

        self._transcript = VoiceActorTranscriptEdit(self, self)
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

        self._voice_gender_label = QtWidgets.QLabel("M/F:")
        self._voice_gender_label.setStyleSheet("QLabel{color:#94a3b8;}")
        self._voice_gender_combo = QtWidgets.QComboBox()
        self._voice_gender_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:3px 8px;}"
            "QComboBox:disabled{background:#1f2937;color:#94a3b8;border-color:#334155;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        self._voice_gender_combo.setToolTip("Select male or female local voice.")
        self._voice_gender_combo.setMinimumWidth(78)
        self._voice_gender_combo.setMaximumWidth(92)
        self._voice_gender_combo.currentIndexChanged.connect(self._on_voice_gender_changed)

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

        self._stt_language_label = QtWidgets.QLabel("Lang:")
        self._stt_language_label.setStyleSheet("QLabel{color:#94a3b8;}")
        self._stt_language_combo = QtWidgets.QComboBox()
        self._stt_language_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:3px 8px;}"
            "QComboBox:disabled{background:#1f2937;color:#94a3b8;border-color:#334155;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        self._stt_language_combo.setToolTip("Select recognition and Google voice language.")
        self._stt_language_combo.currentIndexChanged.connect(self._on_stt_language_changed)

        self._send_mode_label = QtWidgets.QLabel("Response:")
        self._send_mode_label.setStyleSheet("QLabel{color:#94a3b8;}")
        self._send_mode_combo = QtWidgets.QComboBox()
        self._send_mode_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:3px 8px;}"
            "QComboBox:disabled{background:#1f2937;color:#94a3b8;border-color:#334155;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        self._send_mode_combo.setToolTip("Choose how Voice -> Text sends the finished command.")
        self._send_mode_combo.currentIndexChanged.connect(self._on_send_mode_changed)

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

        response_row = QtWidgets.QHBoxLayout()
        response_row.setContentsMargins(0, 0, 0, 0)
        response_row.setSpacing(6)
        response_row.addWidget(self._send_mode_label, 0)
        response_row.addWidget(self._send_mode_combo, 1)
        response_row.addWidget(self._stt_language_label, 0)
        response_row.addWidget(self._stt_language_combo, 1)
        response_row.addWidget(self._voice_gender_label, 0)
        response_row.addWidget(self._voice_gender_combo, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(top, 0)
        layout.addLayout(selector, 0)
        layout.addLayout(response_row, 0)
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
        self._refresh_stt_language_options()
        self._refresh_voice_gender_options()
        self._refresh_send_mode_options()
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
            if listening_active and _normalize_stt_send_mode(self._selected_send_mode) == STT_SEND_MODE_MANUAL:
                self._action_btn.setToolTip("Stop listening and send the transcript.")
            else:
                self._action_btn.setToolTip("Stop speech or listening immediately.")
        else:
            self._action_role = "action"
            if self._chatbot_connected:
                self._action_btn.setText("Auto")
                self._action_btn.setStyleSheet(_auto_button_style())
                self._action_btn.setIcon(self._chatbot_icon)
                self._action_btn.setToolTip("Replay the latest auto response.")
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
            self._mode_btn.setToolTip("Connected auto input forces Text -> Voice mode.")
            self._action_btn.setText("Auto")
            self._action_btn.setStyleSheet(_auto_button_style())
            self._action_btn.setIcon(self._chatbot_icon)
            self._action_btn.setToolTip("Replay the latest auto response.")
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
        listening, paused, stop_requested = self._stt_state()
        if listening:
            self._pause_button_role = "pause"
            if not self._pause_icon.isNull():
                self._pause_btn.setIcon(self._pause_icon)
            self._pause_btn.setText("Paused" if paused and not stop_requested else "Pause")
            self._pause_btn.setToolTip("Pause/resume active listening.")
            flashing = bool(paused and not stop_requested and self._pause_flash_state)
            self._pause_btn.setStyleSheet(_pause_button_style(paused=bool(paused and not stop_requested), flash=flashing))
            if paused and not stop_requested:
                if not self._pause_flash_timer.isActive():
                    self._pause_flash_timer.start()
            else:
                if self._pause_flash_timer.isActive():
                    self._pause_flash_timer.stop()
                self._pause_flash_state = False
            return

        if self._mode == "voice_to_text" and not self._chatbot_connected:
            self._pause_button_role = "send"
            self._pause_btn.setText("Send")
            if not self._send_icon.isNull():
                self._pause_btn.setIcon(self._send_icon)
            self._pause_btn.setStyleSheet(_tool_button_style("#0f766e", "#14b8a6", "#0d9488", text="#ecfeff"))
            self._pause_btn.setToolTip("Send the transcript text.")
        else:
            self._pause_button_role = "pause"
            self._pause_btn.setText("Pause")
            if not self._pause_icon.isNull():
                self._pause_btn.setIcon(self._pause_icon)
            self._pause_btn.setStyleSheet(_pause_button_style(paused=False, flash=False))
            self._pause_btn.setToolTip("Pause is available in Voice -> Text listen mode.")
        if self._pause_flash_timer.isActive():
            self._pause_flash_timer.stop()
        self._pause_flash_state = False

    def _update_control_states(self) -> None:
        self._update_contextual_action_roles()
        self._update_pause_button_ui()
        listening, _paused, stop_requested = self._stt_state()
        can_control_listening = bool(listening and not stop_requested)
        can_stop_action = bool(self._tts_playing or can_control_listening)
        can_backspace = self._can_backspace_transcript()
        can_erase = self._can_erase_last_spoken()
        can_send = bool(
            getattr(self, "_pause_button_role", "") == "send"
            and not self._busy
            and (self._transcript.toPlainText() or "").strip()
        )
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
        if getattr(self, "_pause_button_role", "") == "send":
            self._pause_btn.setEnabled(can_send)
        else:
            self._pause_btn.setEnabled(bool(self._busy and can_control_listening))
        self._copy_btn.setEnabled(not self._busy)
        self._param_combo.setEnabled(bool(self._source_param_options) and not self._busy and not self._chatbot_connected)
        self._voice_combo.setEnabled(bool(self._tts_voice_options) and not self._busy)
        self._voice_gender_combo.setEnabled(self._voice_gender_control_available() and not self._busy)
        self._stt_combo.setEnabled(not self._busy)
        self._stt_language_combo.setEnabled(not self._busy)
        self._send_mode_combo.setEnabled(not self._busy)

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
            if kind in CHATBOT_NODE_KINDS or kind in MEDIGATOR_NODE_KINDS:
                if chatbot_input_item is None:
                    chatbot_input_item = src_item
                continue
            if kind in CHATBOT_PROXY_NODE_KINDS:
                upstream_chatbot = _find_upstream_auto_speech_source(scene, src_item, max_depth=6)
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
            _text, token, _err = _latest_auto_speech_response(self._scene, chatbot_item)
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
                source_label = _auto_speech_source_label(self._chatbot_input_item)
                if self._chatbot_via_proxy:
                    self._param_combo.addItem(f"{source_label} -> Python output (Auto)", "")
                else:
                    self._param_combo.addItem(f"{source_label} output (Auto)", "")
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
            source_label = _auto_speech_source_label(self._chatbot_input_item)
            if self._chatbot_via_proxy:
                self._param_combo.setToolTip(f"{source_label} input through Python uses automatic speech.")
            else:
                self._param_combo.setToolTip(f"{source_label} input uses automatic speech.")
        elif options:
            self._param_combo.setEnabled(not self._busy)
            self._param_combo.setToolTip("Pick which connected parameter to speak in Text -> Voice mode.")
        else:
            self._param_combo.setEnabled(False)
            self._param_combo.setToolTip("Connect a node with parameters to choose a value for speech.")

        self._apply_mode_ui()

    def _refresh_voice_options(self) -> None:
        google_ready = gTTS is not None and pygame is not None
        kokoro_ready = _kokoro_available() and pygame is not None
        desktop_ready = pyttsx3 is not None
        google_label = "Google (Tanya)" if google_ready else "Google (Tanya) - install gTTS + pygame"
        kokoro_label = "Kokoro-82M (Local)" if kokoro_ready else "Kokoro-82M - install kokoro + pygame"
        desktop_label = "Desktop Voice (Local)" if desktop_ready else "Desktop Voice - install pyttsx3"
        options = [
            {"id": VOICE_TANYA_GOOGLE, "name": google_label},
            {"id": VOICE_KOKORO_82M, "name": kokoro_label},
            {"id": VOICE_LOCAL_DESKTOP, "name": desktop_label},
        ]

        selected_key = str(self._selected_voice_key or "").strip() or VOICE_TANYA_GOOGLE
        if selected_key == VOICE_AUTO_FEMALE:
            selected_key = VOICE_LOCAL_DESKTOP
            self._selected_voice_key = selected_key
            _set_node_param(self._node_item, VOICE_ACTOR_VOICE_KEY, selected_key)
        if not any(str(opt.get("id", "")) == selected_key for opt in options):
            selected_key = VOICE_LOCAL_DESKTOP if desktop_ready else VOICE_TANYA_GOOGLE
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
        elif not google_ready and not kokoro_ready and pyttsx3 is None:
            self._voice_combo.setEnabled(True)
            self._voice_combo.setToolTip(
                "Install dependencies for speech: pip install gTTS pygame pyttsx3 kokoro"
            )
        elif not google_ready:
            self._voice_combo.setEnabled(True)
            self._voice_combo.setToolTip(
                "Google (Tanya) needs gTTS + pygame. Kokoro and local voices may still be available."
            )
        elif not kokoro_ready:
            self._voice_combo.setEnabled(True)
            self._voice_combo.setToolTip(
                "Kokoro-82M needs kokoro + pygame. Google and local voices are still available."
            )
        else:
            self._voice_combo.setEnabled(True)
            self._voice_combo.setToolTip(
                "Google and Kokoro use the Lang setting. Kokoro and Desktop Voice use the M/F setting."
            )
        self._refresh_voice_gender_options()

    def _voice_gender_control_available(self) -> bool:
        selected_voice = str(self._selected_voice_key or "").strip()
        return selected_voice in {VOICE_KOKORO_82M, VOICE_LOCAL_DESKTOP, VOICE_AUTO_FEMALE}

    def _refresh_voice_gender_options(self) -> None:
        selected_key = _normalize_voice_gender(self._selected_voice_gender)
        if selected_key != str(self._selected_voice_gender or "").strip():
            self._selected_voice_gender = selected_key
            _set_node_param(self._node_item, VOICE_ACTOR_VOICE_GENDER_KEY, selected_key)

        options = [
            {"id": VOICE_GENDER_FEMALE, "name": "Female"},
            {"id": VOICE_GENDER_MALE, "name": "Male"},
        ]

        self._syncing_voice_gender_combo = True
        try:
            self._voice_gender_combo.blockSignals(True)
            self._voice_gender_combo.clear()
            selected_index = 0
            for idx, option in enumerate(options):
                label = str(option.get("name", "") or "")
                value = str(option.get("id", "") or "")
                self._voice_gender_combo.addItem(label, value)
                if value == selected_key:
                    selected_index = idx
            self._voice_gender_combo.setCurrentIndex(selected_index)
        finally:
            self._voice_gender_combo.blockSignals(False)
            self._syncing_voice_gender_combo = False

        available = self._voice_gender_control_available()
        self._voice_gender_label.setVisible(available)
        self._voice_gender_combo.setVisible(available)
        self._voice_gender_combo.setEnabled(available and not self._busy)
        selected_voice = str(self._selected_voice_key or "").strip()
        if selected_voice == VOICE_KOKORO_82M:
            voice_id = _kokoro_voice_for_language(self._selected_stt_language, selected_key)
            self._voice_gender_combo.setToolTip(
                f"Select local Kokoro voice gender. Current {selected_key}: {voice_id}."
            )
        elif selected_voice in {VOICE_LOCAL_DESKTOP, VOICE_AUTO_FEMALE}:
            self._voice_gender_combo.setToolTip("Select the installed desktop voice gender automatically.")
        else:
            self._voice_gender_combo.setToolTip("Available for Kokoro-82M and Desktop Voice.")

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

    def _refresh_stt_language_options(self) -> None:
        selected_key = _normalize_stt_language(self._selected_stt_language)
        if selected_key != str(self._selected_stt_language or "").strip():
            self._selected_stt_language = selected_key
            _set_node_param(self._node_item, VOICE_ACTOR_STT_LANGUAGE_KEY, selected_key)

        self._syncing_stt_language_combo = True
        try:
            self._stt_language_combo.blockSignals(True)
            self._stt_language_combo.clear()
            selected_index = 0
            for idx, option in enumerate(STT_LANGUAGE_OPTIONS):
                label = str(option.get("name", "") or "")
                value = str(option.get("id", "") or "")
                self._stt_language_combo.addItem(label, value)
                if value == selected_key:
                    selected_index = idx
            self._stt_language_combo.setCurrentIndex(selected_index)
        finally:
            self._stt_language_combo.blockSignals(False)
            self._syncing_stt_language_combo = False

        if self._busy:
            self._stt_language_combo.setEnabled(False)
            self._stt_language_combo.setToolTip("Language is disabled while the node is busy.")
        else:
            self._stt_language_combo.setEnabled(True)
            self._stt_language_combo.setToolTip(
                "Language used by recognition and Google voice output."
            )

    def _refresh_send_mode_options(self) -> None:
        options = [
            {"id": STT_SEND_MODE_AUTO_RESPOND, "name": "Auto Respond"},
            {"id": STT_SEND_MODE_ASK_FIRST, "name": "Ask First"},
            {"id": STT_SEND_MODE_MANUAL, "name": "Manual"},
        ]
        selected_key = _normalize_stt_send_mode(self._selected_send_mode)
        if selected_key not in {STT_SEND_MODE_AUTO_RESPOND, STT_SEND_MODE_ASK_FIRST, STT_SEND_MODE_MANUAL}:
            selected_key = STT_SEND_MODE_AUTO_RESPOND
            self._selected_send_mode = selected_key
            _set_node_param(self._node_item, VOICE_ACTOR_SEND_MODE_KEY, selected_key)

        self._syncing_send_mode_combo = True
        try:
            self._send_mode_combo.blockSignals(True)
            self._send_mode_combo.clear()
            selected_index = 0
            for idx, option in enumerate(options):
                label = str(option.get("name", "") or "")
                value = str(option.get("id", "") or "")
                self._send_mode_combo.addItem(label, value)
                if value == selected_key:
                    selected_index = idx
            self._send_mode_combo.setCurrentIndex(selected_index)
        finally:
            self._send_mode_combo.blockSignals(False)
            self._syncing_send_mode_combo = False

        if self._busy:
            self._send_mode_combo.setEnabled(False)
        else:
            self._send_mode_combo.setEnabled(True)
        if selected_key == STT_SEND_MODE_MANUAL:
            self._send_mode_combo.setToolTip("Manual sends only when Stop is pressed.")
        elif selected_key == STT_SEND_MODE_ASK_FIRST:
            self._send_mode_combo.setToolTip("Ask First confirms before sending.")
        else:
            self._send_mode_combo.setToolTip("Auto Respond sends after speech pauses.")

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
        self._refresh_voice_gender_options()
        self._update_control_states()

    def _on_voice_gender_changed(self, _index: int) -> None:
        if self._syncing_voice_gender_combo:
            return
        selected_key = _normalize_voice_gender(self._voice_gender_combo.currentData() or "")
        if selected_key == str(self._selected_voice_gender or "").strip():
            return
        self._selected_voice_gender = selected_key
        _set_node_param(self._node_item, VOICE_ACTOR_VOICE_GENDER_KEY, selected_key)
        self._refresh_voice_gender_options()

    def _on_stt_method_changed(self, _index: int) -> None:
        if self._syncing_stt_combo:
            return
        selected_key = _normalize_stt_method(self._stt_combo.currentData() or "")
        if selected_key == str(self._selected_stt_method or "").strip():
            return
        self._selected_stt_method = selected_key
        _set_node_param(self._node_item, VOICE_ACTOR_STT_METHOD_KEY, selected_key)

    def _on_stt_language_changed(self, _index: int) -> None:
        if self._syncing_stt_language_combo:
            return
        selected_key = _normalize_stt_language(self._stt_language_combo.currentData() or "")
        if selected_key == str(self._selected_stt_language or "").strip():
            return
        self._selected_stt_language = selected_key
        _set_node_param(self._node_item, VOICE_ACTOR_STT_LANGUAGE_KEY, selected_key)
        self._refresh_stt_language_options()
        self._refresh_voice_gender_options()

    def _on_send_mode_changed(self, _index: int) -> None:
        if self._syncing_send_mode_combo:
            return
        selected_key = _normalize_stt_send_mode(self._send_mode_combo.currentData() or "")
        if selected_key == str(self._selected_send_mode or "").strip():
            return
        self._selected_send_mode = selected_key
        _set_node_param(self._node_item, VOICE_ACTOR_SEND_MODE_KEY, selected_key)
        self._refresh_send_mode_options()
        self._update_control_states()

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
            self._set_status("Auto mode enabled by connected input.")
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

    def _on_pause_or_send_clicked(self) -> None:
        listening, _paused, stop_requested = self._stt_state()
        if listening and not stop_requested:
            self._toggle_pause_listening()
            return
        if getattr(self, "_pause_button_role", "") == "send":
            self._send_manual_transcript()
            return
        self._set_status("Pause is available only while listening.")

    def _send_manual_transcript(self) -> None:
        if self._busy:
            self._set_status("Cannot send while the node is busy.", error=True)
            return
        if self._mode != "voice_to_text":
            self._set_status("Switch to Voice -> Text mode before sending transcript text.", error=True)
            return
        text = (self._transcript.toPlainText() or "").strip()
        if not text:
            self._set_status("Transcript is empty.", error=True)
            self._update_control_states()
            return
        _publish_voice_command(self._node_item, text)
        self._stt_session_has_new_text = False
        self._transcript_undo_stack.clear()
        self._set_status("Message sent.")
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
            if _normalize_stt_send_mode(self._selected_send_mode) == STT_SEND_MODE_MANUAL and self._stt_session_has_new_text:
                self._set_status("Stopping listening and sending...")
            else:
                self._set_status("Stopping listening...")
            return
        if not self._tts_playing:
            if self._busy:
                self._pending_chatbot_text = ""
                self._set_busy(False, "")
                self._resume_turn_taking_paused_actors()
                self._set_status("Speech playback ended.")
                return
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
        stt_language = _normalize_stt_language(self._selected_stt_language)
        send_mode = _normalize_stt_send_mode(self._selected_send_mode)
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
        if send_mode == STT_SEND_MODE_MANUAL:
            send_hint = "Press Stop to send."
        elif send_mode == STT_SEND_MODE_ASK_FIRST:
            send_hint = "Ask First confirms before sending."
        else:
            send_hint = "Auto Respond sends after a pause."
        language_label = _stt_language_name(stt_language)
        if stt_method == STT_METHOD_LOCAL_WHISPER:
            self._set_status(f"Listening... (Local Whisper, {language_label}). {send_hint}")
        else:
            self._set_status(f"Listening... (Google, {language_label}). {send_hint}")
        threading.Thread(target=self._stt_worker, args=(stt_method, send_mode, stt_language), daemon=True).start()

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
        selected_voice_gender = _normalize_voice_gender(self._selected_voice_gender)
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
                if selected_voice_key and selected_voice_key not in {VOICE_TANYA_GOOGLE, VOICE_LOCAL_DESKTOP, VOICE_AUTO_FEMALE}:
                    try:
                        engine.setProperty("voice", selected_voice_key)
                        voice_applied = True
                    except Exception:
                        voice_applied = False
                if not voice_applied:
                    desktop_voice_id = _pick_desktop_voice_id(engine, selected_voice_gender)
                    if desktop_voice_id:
                        try:
                            engine.setProperty("voice", desktop_voice_id)
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

    def _stt_worker(self, stt_method: str, send_mode: str, stt_language: str = LOCAL_WHISPER_LANGUAGE) -> None:
        transcript_parts = []
        error = ""
        selected_method = _normalize_stt_method(stt_method)
        selected_send_mode = _normalize_stt_send_mode(send_mode)
        selected_language = _normalize_stt_language(stt_language)
        wait_timeout = getattr(sr, "WaitTimeoutError", None) if sr is not None else None
        unknown_value = getattr(sr, "UnknownValueError", None) if sr is not None else None
        request_error = getattr(sr, "RequestError", None) if sr is not None else None
        awaiting_send_confirmation = False
        awaiting_started_at = 0.0
        prompt_response_until = 0.0
        last_voice_activity = time.monotonic()
        send_confirmed = False

        def _maybe_handle_idle_send(now: float) -> bool:
            nonlocal awaiting_send_confirmation
            nonlocal awaiting_started_at
            nonlocal prompt_response_until
            nonlocal last_voice_activity
            nonlocal send_confirmed

            should_send = (
                bool(transcript_parts)
                and not awaiting_send_confirmation
                and (now - last_voice_activity) >= STT_IDLE_CONFIRM_SECONDS
            )
            if not should_send:
                return False
            if selected_send_mode == STT_SEND_MODE_MANUAL:
                return False
            if selected_send_mode == STT_SEND_MODE_AUTO_RESPOND:
                send_confirmed = True
                last_voice_activity = now
                self._stt_status.emit("Sending message...")
                self._set_stt_state(stop_requested=True, paused=False)
                return True
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
                                if _maybe_handle_idle_send(time.monotonic()):
                                    break
                                continue
                            error = _format_stt_error(exc)
                            break
                        chunk = ""
                        if selected_method == STT_METHOD_LOCAL_WHISPER:
                            wav_data = audio.get_wav_data(convert_rate=16000, convert_width=2)
                            chunk, chunk_error = _transcribe_local_whisper(wav_data, selected_language)
                            if chunk_error:
                                if chunk_error == "Speech detected but transcript was empty.":
                                    if _maybe_handle_idle_send(time.monotonic()):
                                        break
                                    continue
                                error = chunk_error
                                break
                        else:
                            try:
                                chunk = (
                                    recognizer.recognize_google(
                                        audio,
                                        language=_stt_google_language(selected_language),
                                    )
                                    or ""
                                ).strip()
                            except Exception as exc:
                                if unknown_value and isinstance(exc, unknown_value):
                                    if _maybe_handle_idle_send(time.monotonic()):
                                        break
                                    continue
                                if request_error and isinstance(exc, request_error):
                                    error = f"Speech recognition service failed: {exc}"
                                else:
                                    error = _format_stt_error(exc)
                                break
                        clean_chunk = str(chunk or "").strip()
                        if not clean_chunk:
                            if _maybe_handle_idle_send(time.monotonic()):
                                break
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
            if send_confirmed:
                error = "__sent__"
            elif selected_send_mode == STT_SEND_MODE_MANUAL and transcript_parts:
                error = "__manual_sent__"
            else:
                error = "__stopped__"
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
        clean_error = str(error or "").strip()
        send_completed = clean_error in {"__sent__", "__manual_sent__"}
        if had_new_text:
            text = self._transcript.toPlainText() or ""
            if send_completed:
                _publish_voice_command(self._node_item, text)
            else:
                _set_node_info(self._node_item, text)
        if error:
            if clean_error == "__sent__":
                if had_new_text:
                    self._set_status("Message sent. Listening for commands...")
                else:
                    self._set_status("Listening for commands...")
                QtCore.QTimer.singleShot(120, self._resume_stt_after_send)
                return
            if clean_error == "__manual_sent__":
                if had_new_text:
                    self._set_status("Message sent.")
                else:
                    self._set_status("Listening stopped.")
                return
            if clean_error == "__stopped__":
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
            source_text = ""
            py_error = ""
            if self._chatbot_via_proxy:
                source_text, token, _err = _latest_auto_speech_response(self._scene, source_item)
                proxy_item = self._chatbot_proxy_item
                if proxy_item is not None and _kind_of_item(proxy_item) == "python":
                    text, py_error = _run_python_transform(self._scene, proxy_item)
                    if py_error and py_error != "Python transform produced empty output.":
                        self._set_status(f"{py_error} Falling back to auto response.", error=True)
                if not text:
                    text = _text_from_input(self._scene, self._node_item, "text").strip()
                if not text:
                    text = str(source_text or "").strip()
                text = _proxy_auto_speech_text(source_item, text, fallback_text=source_text)
                if not text:
                    self._set_status("Waiting for Python output from auto response...")
                    return
            else:
                text, token, _err = _latest_auto_speech_response(self._scene, source_item)
                if not text:
                    return
                text = _proxy_auto_speech_text(source_item, text, fallback_text=text).strip()
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
        tts_language = _normalize_stt_language(self._selected_stt_language)
        voice_gender = _normalize_voice_gender(self._selected_voice_gender)
        if voice_key == VOICE_TANYA_GOOGLE:
            if gTTS is None or pygame is None:
                self._set_status(
                    "Google voice missing dependencies. Install: pip install gTTS pygame",
                    error=True,
                )
                return False
        elif voice_key == VOICE_KOKORO_82M:
            if pygame is None:
                self._set_status(
                    "Kokoro voice needs pygame for playback. Install: pip install pygame",
                    error=True,
                )
                return False
            if not _kokoro_available():
                self._set_status(
                    "Missing dependency: kokoro. Run setup.bat or pip install kokoro.",
                    error=True,
                )
                return False
        elif pyttsx3 is None:
            self._set_status(
                "Missing dependency: pyttsx3. Install: pip install pyttsx3",
                error=True,
            )
            return False
        clean = _clean_voice_text(text)
        if not clean:
            if source == "selected_param":
                self._set_status(
                    "Selected parameter is empty. Choose another parameter or fill its value.",
                    error=True,
                )
            elif source in ("chatbot_auto", "chatbot_latest"):
                self._set_status("No auto response available to speak yet.", error=True)
            else:
                self._set_status("No text to speak. Connect input 'text' or type transcript.", error=True)
            return False
        language_suffix = ""
        if voice_key in {VOICE_TANYA_GOOGLE, VOICE_KOKORO_82M}:
            language_suffix = f" ({_stt_language_name(tts_language)})"
        if source == "selected_param":
            self._set_status(f"Speaking selected parameter value{language_suffix}...")
        elif source in ("chatbot_auto", "chatbot_latest"):
            self._set_status(f"Auto-speaking latest response{language_suffix}...")
        elif source == "replay":
            self._set_status(f"Replaying last output{language_suffix}...")
        elif source == "input":
            self._set_status(f"Speaking text from wired input{language_suffix}...")
        else:
            self._set_status(f"Speaking transcript{language_suffix}...")
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
        threading.Thread(target=self._tts_worker, args=(clean, voice_key, tts_language, voice_gender), daemon=True).start()
        return True

    def _resolve_tts_text(self) -> tuple[str, str]:
        self._refresh_source_param_options()
        if self._chatbot_connected and self._chatbot_input_item is not None:
            if self._chatbot_via_proxy:
                scene = self._node_item.scene()
                source_text, token, _err = _latest_auto_speech_response(self._scene, self._chatbot_input_item)
                if token:
                    self._last_chatbot_token = token
                proxy_item = self._chatbot_proxy_item
                if proxy_item is not None and _kind_of_item(proxy_item) == "python":
                    text, _py_error = _run_python_transform(scene, proxy_item)
                    spoken = _proxy_auto_speech_text(
                        self._chatbot_input_item,
                        text,
                        fallback_text=source_text,
                    )
                    if spoken:
                        return spoken, "chatbot_latest"
                wired = _text_from_input(scene, self._node_item, "text")
                spoken = _proxy_auto_speech_text(
                    self._chatbot_input_item,
                    wired,
                    fallback_text=source_text,
                )
                if spoken:
                    return spoken, "chatbot_latest"
                spoken = _proxy_auto_speech_text(
                    self._chatbot_input_item,
                    source_text,
                    fallback_text=source_text,
                )
                if spoken:
                    return spoken, "chatbot_latest"
                local = (self._transcript.toPlainText() or "").strip()
                if local:
                    return local, "chatbot_latest"
                return "", "chatbot_latest"

            text, token, _err = _latest_auto_speech_response(self._scene, self._chatbot_input_item)
            spoken = _proxy_auto_speech_text(self._chatbot_input_item, text, fallback_text=text)
            if spoken.strip():
                if token:
                    self._last_chatbot_token = token
                return spoken.strip(), "chatbot_latest"
            return "", "chatbot_latest"

        selected_key = str(self._selected_param_key or "").strip()
        if selected_key:
            value, _label = self._selected_source_param_value()
            spoken = _clean_voice_text(value)
            if spoken.strip():
                return spoken.strip(), "selected_param"
            return "", "selected_param"

        scene = self._node_item.scene()
        wired = _text_from_input(scene, self._node_item, "text")
        spoken = _clean_voice_text(wired)
        if spoken.strip():
            return spoken.strip(), "input"
        local = _clean_voice_text(self._transcript.toPlainText())
        return local, "transcript"

    def _start_tts(self) -> None:
        text, source = self._resolve_tts_text()
        self._speak_text(text, source=source)

    def _tts_worker(
        self,
        text: str,
        voice_key: str,
        tts_language: str = LOCAL_WHISPER_LANGUAGE,
        voice_gender: str = VOICE_GENDER_FEMALE,
    ) -> None:
        error = ""
        user_stopped = False
        engine = None
        temp_file = None
        used_pygame = False
        playback_slot = False
        selected_language = _normalize_stt_language(tts_language)
        selected_gender = _normalize_voice_gender(voice_gender)

        def _speak_with_local(local_voice_key: str, local_voice_gender: str) -> None:
            nonlocal engine
            nonlocal user_stopped
            nonlocal error
            if pyttsx3 is None:
                raise RuntimeError("Missing dependency: pyttsx3. Install: pip install pyttsx3")
            engine = pyttsx3.init()
            voice_applied = False
            if local_voice_key not in {VOICE_LOCAL_DESKTOP, VOICE_AUTO_FEMALE}:
                try:
                    engine.setProperty("voice", local_voice_key)
                    voice_applied = True
                except Exception:
                    voice_applied = False
            if not voice_applied:
                desktop_voice_id = _pick_desktop_voice_id(engine, local_voice_gender)
                if desktop_voice_id:
                    try:
                        engine.setProperty("voice", desktop_voice_id)
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

        def _play_with_pygame(audio_path: Path) -> None:
            nonlocal user_stopped
            nonlocal used_pygame
            if pygame is None:
                raise RuntimeError("Missing dependency: pygame. Install: pip install pygame")
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            with self._tts_lock:
                self._tts_using_pygame = True
            used_pygame = True
            pygame.mixer.music.load(str(audio_path))
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
                pass
            elif selected_voice_key == VOICE_TANYA_GOOGLE:
                google_error = ""
                if gTTS is not None and pygame is not None:
                    try:
                        temp_file = Path(tempfile.gettempdir()) / f"voice_actor_{uuid.uuid4().hex}.mp3"
                        save_err = _save_gtts_mp3(
                            text,
                            temp_file,
                            timeout_seconds=12.0,
                            language=selected_language,
                        )
                        if save_err:
                            raise RuntimeError(save_err)
                        _play_with_pygame(temp_file)
                    except Exception as exc:
                        google_error = str(exc)
                else:
                    google_error = "Google voice dependencies are missing."
                if google_error and not user_stopped:
                    error = f"Tanya (Google) voice failed: {google_error}"
            elif selected_voice_key == VOICE_KOKORO_82M:
                kokoro_error = ""
                if pygame is not None:
                    try:
                        temp_file = Path(tempfile.gettempdir()) / f"voice_actor_kokoro_{uuid.uuid4().hex}.wav"
                        save_err = _save_kokoro_wav(
                            text,
                            temp_file,
                            language=selected_language,
                            voice=_kokoro_voice_for_language(selected_language, selected_gender),
                        )
                        if save_err:
                            raise RuntimeError(save_err)
                        with self._tts_lock:
                            user_stopped = bool(self._tts_user_stopped)
                        if user_stopped:
                            error = "__stopped__"
                        else:
                            _play_with_pygame(temp_file)
                    except Exception as exc:
                        kokoro_error = str(exc)
                else:
                    kokoro_error = "Kokoro playback dependency pygame is missing."
                if kokoro_error and not user_stopped:
                    error = f"Kokoro-82M voice failed: {kokoro_error}"
            else:
                _speak_with_local(selected_voice_key, selected_gender)
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

    size_hint = body.sizeHint()
    minimum_hint = body.minimumSizeHint()
    h = max(int(size_hint.height()), int(minimum_hint.height()), VOICE_ACTOR_BODY_H)
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


VOICE_ACTOR_SPEC = Spec(
    stripe_color="#0ea5e9",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
