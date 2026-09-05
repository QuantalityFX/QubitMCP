from __future__ import annotations

import base64
import binascii
import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    try:
        from PySide6 import QtMultimedia
    except Exception:
        QtMultimedia = None  # type: ignore
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
    try:
        from PySide2 import QtMultimedia  # type: ignore
    except Exception:
        QtMultimedia = None  # type: ignore

from nodes.core import Spec


MINIMAX_TTS_API_NODE_KIND = "minimax_tts_api"
MINIMAX_TTS_API_NODE_ALIASES = [
    "minimax text to speech",
    "minimax speech api",
    "minimax tts",
    "minimax_speech",
    "minimax_text_to_speech",
]
MINIMAX_TTS_API_NODE_KINDS = {MINIMAX_TTS_API_NODE_KIND, *MINIMAX_TTS_API_NODE_ALIASES}
MINIMAX_TTS_API_BODY_W = 520
MINIMAX_TTS_API_BODY_H = 608

AUDIO_OUTPUT_PARAM = "audio_path"
PATH_OUTPUT_PARAM = "path"
STATUS_OUTPUT_PARAM = "tts_status"
OUTPUT_PARAMS = (AUDIO_OUTPUT_PARAM, PATH_OUTPUT_PARAM, STATUS_OUTPUT_PARAM)

_PARAM_TEXT = "__minimax_tts_text"
_PARAM_API_KEY = "__minimax_tts_api_key"
_PARAM_API_BASE_URL = "__minimax_tts_api_base_url"
_PARAM_API_PATH = "__minimax_tts_api_path"
_PARAM_MODEL = "__minimax_tts_model"
_PARAM_VOICE_ID = "__minimax_tts_voice_id"
_PARAM_SPEED = "__minimax_tts_speed"
_PARAM_VOLUME = "__minimax_tts_volume"
_PARAM_PITCH = "__minimax_tts_pitch"
_PARAM_FORMAT = "__minimax_tts_format"
_PARAM_SAMPLE_RATE = "__minimax_tts_sample_rate"
_PARAM_BITRATE = "__minimax_tts_bitrate"
_PARAM_CHANNEL = "__minimax_tts_channel"
_PARAM_OUTPUT_DIR = "__minimax_tts_output_dir"
_PARAM_TIMEOUT = "__minimax_tts_timeout"
_HIDDEN_PARAM = "__ui_hidden_params"

_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}

_SYSTEM_VOICE_IDS = [
    "English_Graceful_Lady",
    "English_Trustworthy_Man",
    "English_CalmWoman",
    "English_Deep_Voice_Man",
    "English_LadyNarrator",
    "English_RadiantGirl",
    "male-qn-qingse",
    "male-qn-jingying",
    "male-qn-badao",
    "male-qn-daxuesheng",
    "female-shaonv",
    "female-yujie",
    "female-chengshu",
    "female-tianmei",
    "presenter_male",
    "presenter_female",
    "audiobook_male_1",
    "audiobook_male_2",
    "audiobook_female_1",
    "audiobook_female_2",
    "clever_boy",
    "cute_boy",
    "lovely_girl",
    "cartoon_pig",
    "Santa_Claus",
]


def _default_api_base_url() -> str:
    return (
        os.environ.get("QUBITMCP_MINIMAX_API_BASE_URL")
        or os.environ.get("MINIMAX_API_BASE_URL")
        or "https://api.minimax.io"
    ).strip()


def _default_api_key() -> str:
    return (
        os.environ.get("QUBITMCP_MINIMAX_TTS_API_KEY")
        or os.environ.get("MINIMAX_TTS_API_KEY")
        or os.environ.get("QUBITMCP_MINIMAX_CREDITS_API_KEY")
        or os.environ.get("MINIMAX_CREDITS_API_KEY")
        or os.environ.get("QUBITMCP_MINIMAX_API_KEY")
        or os.environ.get("MINIMAX_API_KEY")
        or ""
    ).strip()


def _workflow_dir_from_ref(raw) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        path = Path(text).expanduser()
        return path.parent if path.suffix else path
    except Exception:
        return None


def _workflow_refs_from_widget(widget):
    seen = set()
    current = widget
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        try:
            path = getattr(current, "_current_path", None)
        except Exception:
            path = None
        if path:
            yield path
        try:
            current = current.parentWidget()
        except Exception:
            break


def _workflow_dir_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    candidates = []
    if scene is not None:
        try:
            for view in scene.views() or []:
                candidates.extend(_workflow_refs_from_widget(view))
                try:
                    candidates.extend(_workflow_refs_from_widget(view.window()))
                except Exception:
                    pass
        except Exception:
            pass
        try:
            candidates.append(getattr(scene, "_filename", None))
        except Exception:
            pass
    try:
        candidates.extend(_workflow_refs_from_widget(node_item.window()))
    except Exception:
        pass
    try:
        candidates.extend(_workflow_refs_from_widget(QtWidgets.QApplication.activeWindow()))
    except Exception:
        pass
    for raw in candidates:
        workflow_dir = _workflow_dir_from_ref(raw)
        if workflow_dir is not None:
            return workflow_dir
    return None


def _temp_audio_dir() -> Path:
    return Path(tempfile.gettempdir()) / "EchoGraph" / "minimax_tts_api"


def _default_output_dir(node_item) -> Path:
    workflow_dir = _workflow_dir_for_node(node_item)
    if workflow_dir is not None:
        return workflow_dir / "audio" / "minimax_tts"
    return _temp_audio_dir()


def _clean_path_text(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _output_dir(node_item, raw: str) -> Path:
    text = _clean_path_text(raw)
    if not text:
        return _default_output_dir(node_item)
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    base = _workflow_dir_for_node(node_item) or _temp_audio_dir()
    return base / path


def _safe_stem(raw: str, fallback: str = "minimax_tts") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw or "").strip()).strip("._-")
    return text[:80] or fallback


def _param_value(model, name: str, default: str = "") -> str:
    if model is None:
        return default
    key = str(name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "") or "").strip().lower() == key:
            return str(entry.get("value", "") or "")
    return default


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        setattr(model, "params", params)
    if not isinstance(params, list):
        params = list(params)
        setattr(model, "params", params)
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
    found = False
    changed = False
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
    if not changed:
        return
    if notify_scene:
        scene = node_item.scene() if hasattr(node_item, "scene") else None
        if scene is not None and hasattr(scene, "set_node_params"):
            try:
                scene.set_node_params(model.name, list(getattr(model, "params", None) or []), rebuild=False, emit=True)
                return
            except TypeError:
                try:
                    scene.set_node_params(model.name, list(getattr(model, "params", None) or []))
                    return
                except Exception:
                    pass
            except Exception:
                pass
        if scene is not None and hasattr(scene, "paramChanged"):
            try:
                scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
            except Exception:
                pass


def _edge_port(edge) -> str:
    name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None) or ""
    return str(name or "").strip().lower()


def _ordered_in_edges(node_item) -> list:
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return []
    try:
        return list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            return list(scene._in_edges(node_item))
        except Exception:
            return []


def _connected_text(node_item) -> str:
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return ""
    edges = _ordered_in_edges(node_item)
    named = [edge for edge in edges if _edge_port(edge) in {"text", "prompt", "input"}]
    if named:
        edges = named
    for edge in edges:
        src = getattr(edge, "src", None)
        model = getattr(src, "model", None)
        for param_name in ("text", "prompt", "output", "response", "message"):
            value = _param_value(model, param_name, "").strip()
            if value:
                return value
        try:
            text = str(scene.resolve_text_value(src) or "").strip()
        except Exception:
            text = ""
        if text:
            return text
    return ""


def _is_safe_api_url(raw: str) -> bool:
    parsed = url_parse.urlparse(str(raw or "").strip())
    scheme = parsed.scheme.lower()
    if scheme == "https":
        return True
    host = str(parsed.hostname or "").strip().strip("[]").lower()
    return scheme == "http" and host in {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}


def _join_api_url(base: str, path: str, default_path: str = "") -> str:
    text = str(path or "").strip() or str(default_path or "").strip()
    if text and url_parse.urlparse(text).scheme:
        if not _is_safe_api_url(text):
            raise RuntimeError("Use an HTTPS MiniMax API URL, or a local HTTP proxy for testing.")
        return text
    clean_base = str(base or "").strip() or _default_api_base_url()
    if not url_parse.urlparse(clean_base).scheme:
        clean_base = "https://" + clean_base
    if not _is_safe_api_url(clean_base):
        raise RuntimeError("Use an HTTPS MiniMax API base URL, or a local HTTP proxy for testing.")
    suffix = text or default_path or "/v1/t2a_v2"
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return clean_base.rstrip("/") + suffix


def _http_json(url: str, payload: dict[str, Any], *, timeout: int, headers: dict[str, str]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "QubitMCP MiniMax TTS API node",
    }
    request_headers.update(headers or {})
    req = url_request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with url_request.urlopen(req, timeout=max(10, int(timeout))) as resp:
            raw = resp.read()
    except url_error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise RuntimeError(f"MiniMax TTS API returned HTTP {exc.code}: {detail or str(exc)}") from exc
    except url_error.URLError as exc:
        raise RuntimeError(f"Could not reach MiniMax TTS API: {exc.reason}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        preview = raw[:500].decode("utf-8", errors="replace")
        raise RuntimeError(f"MiniMax TTS API response was not valid JSON: {preview}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("MiniMax TTS API response was not a JSON object.")
    base_resp = data.get("base_resp")
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code")
        status_msg = str(base_resp.get("status_msg") or "").strip()
        if status_code not in (None, 0, "0"):
            raise RuntimeError(status_msg or f"MiniMax TTS API failed with status_code {status_code}.")
    return data


def _decode_audio_payload(value: Any) -> bytes:
    text = str(value or "").strip()
    if not text:
        return b""
    if text.startswith("data:"):
        _prefix, _sep, text = text.partition(",")
    compact = re.sub(r"\s+", "", text)
    if re.fullmatch(r"[0-9A-Fa-f]+", compact or "") and len(compact) % 2 == 0:
        try:
            return bytes.fromhex(compact)
        except ValueError:
            pass
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("MiniMax TTS API returned audio data that was not hex or base64.") from exc


def _extract_audio_data(data: dict[str, Any]) -> Any:
    candidates = [
        data.get("audio"),
        data.get("audio_data"),
        (data.get("data") or {}).get("audio") if isinstance(data.get("data"), dict) else None,
        (data.get("data") or {}).get("audio_data") if isinstance(data.get("data"), dict) else None,
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    raise RuntimeError(f"MiniMax TTS API response did not include audio data: {json.dumps(data, ensure_ascii=True)[:700]}")


def _normalize_model(raw: str) -> str:
    text = str(raw or "").strip()
    if text == "speech-2.8-hd":
        return text
    return "speech-2.8-turbo"


def _normalize_format(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if text in {"wav", "pcm", "flac"}:
        return text
    return "mp3"


def _audio_extension(audio_format: str) -> str:
    fmt = _normalize_format(audio_format)
    if fmt in {"wav", "flac"}:
        return "." + fmt
    return ".mp3"


def _payload(settings: "MiniMaxTTSJobSettings") -> dict[str, Any]:
    voice_id = str(settings.voice_id or "").strip()
    if not voice_id:
        raise ValueError("Enter a MiniMax voice_id.")
    text = str(settings.text or "").strip()
    if not text:
        raise ValueError("Enter text for MiniMax speech.")
    sample_rate = int(settings.sample_rate)
    bitrate = int(settings.bitrate)
    channel = int(settings.channel)
    return {
        "model": _normalize_model(settings.model),
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": round(float(settings.speed), 3),
            "vol": round(float(settings.volume), 3),
            "pitch": int(settings.pitch),
        },
        "audio_setting": {
            "sample_rate": sample_rate,
            "bitrate": bitrate,
            "format": _normalize_format(settings.audio_format),
            "channel": channel,
        },
    }


@dataclass
class MiniMaxTTSJobSettings:
    text: str
    api_key: str
    api_base_url: str
    api_path: str
    model: str
    voice_id: str
    speed: float
    volume: float
    pitch: int
    audio_format: str
    sample_rate: int
    bitrate: int
    channel: int
    output_dir: Path
    timeout: int
    node_name: str = "minimax_tts"


@dataclass
class MiniMaxTTSJobResult:
    ok: bool
    audio_path: str = ""
    status: str = ""
    error: str = ""
    details: str = ""


def run_minimax_tts_job(settings: MiniMaxTTSJobSettings, progress=None) -> MiniMaxTTSJobResult:
    def emit(text: str) -> None:
        if progress is not None:
            try:
                progress(str(text))
            except Exception:
                pass

    api_key = (settings.api_key or _default_api_key()).strip()
    if not api_key:
        raise ValueError("Enter a MiniMax Speech API key or set MINIMAX_TTS_API_KEY / MINIMAX_CREDITS_API_KEY.")
    payload = _payload(settings)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    url = _join_api_url(settings.api_base_url, settings.api_path, "/v1/t2a_v2")
    headers = {"Authorization": f"Bearer {api_key}"}
    emit("Submitting MiniMax text-to-speech request...")
    data = _http_json(url, payload, timeout=settings.timeout, headers=headers)
    audio_bytes = _decode_audio_payload(_extract_audio_data(data))
    if not audio_bytes:
        raise RuntimeError("MiniMax TTS API returned empty audio.")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    ext = _audio_extension(settings.audio_format)
    target = settings.output_dir / f"{_safe_stem(settings.node_name)}_{stamp}_{suffix}{ext}"
    target.write_bytes(audio_bytes)
    return MiniMaxTTSJobResult(True, str(target), f"Generated {target.name}.")


class MiniMaxTTSThread(QtCore.QThread):
    progressChanged = QtCore.Signal(str)
    resultReady = QtCore.Signal(object)

    def __init__(self, settings: MiniMaxTTSJobSettings, parent=None):
        super().__init__(parent)
        self._settings = settings

    def run(self) -> None:
        try:
            result = run_minimax_tts_job(self._settings, progress=self.progressChanged.emit)
        except Exception as exc:
            result = MiniMaxTTSJobResult(False, status="MiniMax TTS generation failed.", error=str(exc))
        self.resultReady.emit(result)


def _style_sheet() -> str:
    return """
        QWidget#MiniMaxTTSApiWidget{background:#0f1216;border:1px solid #334155;border-radius:0px;}
        QLabel{color:#cbd5e1;font-size:11px;}
        QLineEdit,QTextEdit,QComboBox,QSpinBox,QDoubleSpinBox{
            background:#111827;color:#e5e7eb;border:1px solid #334155;border-radius:4px;
            padding:3px 5px;font-size:11px;
        }
        QLineEdit:disabled,QTextEdit:disabled,QComboBox:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled{
            color:#64748b;background:#0b1220;
        }
        QPushButton,QToolButton{
            background:#1f2937;color:#e5e7eb;border:1px solid #334155;border-radius:4px;
            padding:4px 8px;font-size:11px;
        }
        QPushButton:hover,QToolButton:hover{background:#263244;}
        QPushButton:disabled,QToolButton:disabled{color:#64748b;background:#111827;}
        QPushButton#PrimaryButton{background:#0f766e;border-color:#14b8a6;color:#ecfeff;font-weight:600;}
        QPushButton#PrimaryButton:hover{background:#0d9488;}
    """


class MiniMaxTTSApiWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._thread: MiniMaxTTSThread | None = None
        self._scene_connected = False
        self._connected_text = ""
        self._syncing = False
        self._player = None
        self._audio_output = None
        self._playing = False

        build_ports(node_item)
        self.setObjectName("MiniMaxTTSApiWidget")
        self.setStyleSheet(_style_sheet())
        self.setMinimumSize(MINIMAX_TTS_API_BODY_W, MINIMAX_TTS_API_BODY_H)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(5)

        self._text_edit = QtWidgets.QTextEdit()
        self._text_edit.setPlaceholderText("Text to speak")
        self._text_edit.setMinimumHeight(78)
        self._text_edit.setMaximumHeight(94)
        self._text_edit.setToolTip("Text sent to MiniMax. A connected text input overrides this field.")
        self._text_edit.textChanged.connect(self._commit_controls)
        root.addWidget(self._text_edit, 0)

        self._api_key_edit = QtWidgets.QLineEdit()
        self._api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self._api_key_edit.setPlaceholderText("sk-cp... or MINIMAX_TTS_API_KEY")
        self._api_key_edit.setToolTip("MiniMax Speech API key. Token Plan sk-cp keys can be used for speech when the plan covers speech.")
        self._api_key_edit.textChanged.connect(self._commit_controls)
        root.addLayout(self._row("API Key", self._api_key_edit), 0)

        self._api_base_edit = QtWidgets.QLineEdit()
        self._api_base_edit.setToolTip("MiniMax API base URL. The official global base is https://api.minimax.io.")
        self._api_base_edit.editingFinished.connect(self._commit_controls)
        root.addLayout(self._row("API Base", self._api_base_edit), 0)

        model_voice_row = QtWidgets.QHBoxLayout()
        model_voice_row.setContentsMargins(0, 0, 0, 0)
        model_voice_row.setSpacing(6)
        self._model_combo = QtWidgets.QComboBox()
        self._model_combo.setToolTip("MiniMax speech model. Turbo is faster; HD prioritizes quality when available.")
        self._model_combo.addItem("2.8 Turbo", "speech-2.8-turbo")
        self._model_combo.addItem("2.8 HD", "speech-2.8-hd")
        self._model_combo.currentIndexChanged.connect(self._commit_controls)
        model_voice_row.addWidget(self._small_label("Model"), 0)
        model_voice_row.addWidget(self._model_combo, 1)
        self._voice_combo = QtWidgets.QComboBox()
        self._voice_combo.setEditable(True)
        self._voice_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self._voice_combo.setToolTip("MiniMax voice_id inserted into voice_setting.voice_id. You can type cloned or custom voice IDs.")
        for voice_id in _SYSTEM_VOICE_IDS:
            self._voice_combo.addItem(voice_id, voice_id)
        self._voice_combo.currentTextChanged.connect(self._commit_controls)
        model_voice_row.addWidget(self._small_label("Voice"), 0)
        model_voice_row.addWidget(self._voice_combo, 1)
        root.addLayout(model_voice_row, 0)

        voice_row = QtWidgets.QHBoxLayout()
        voice_row.setContentsMargins(0, 0, 0, 0)
        voice_row.setSpacing(6)
        self._speed_spin = QtWidgets.QDoubleSpinBox()
        self._speed_spin.setRange(0.5, 2.0)
        self._speed_spin.setSingleStep(0.05)
        self._speed_spin.setDecimals(2)
        self._speed_spin.setToolTip("Speech speed sent as voice_setting.speed.")
        self._speed_spin.valueChanged.connect(self._commit_controls)
        voice_row.addWidget(self._small_label("Speed"), 0)
        voice_row.addWidget(self._speed_spin, 1)
        self._volume_spin = QtWidgets.QDoubleSpinBox()
        self._volume_spin.setRange(0.1, 10.0)
        self._volume_spin.setSingleStep(0.1)
        self._volume_spin.setDecimals(1)
        self._volume_spin.setToolTip("Speech volume sent as voice_setting.vol.")
        self._volume_spin.valueChanged.connect(self._commit_controls)
        voice_row.addWidget(self._small_label("Vol"), 0)
        voice_row.addWidget(self._volume_spin, 1)
        self._pitch_spin = QtWidgets.QSpinBox()
        self._pitch_spin.setRange(-12, 12)
        self._pitch_spin.setToolTip("Pitch shift sent as voice_setting.pitch.")
        self._pitch_spin.valueChanged.connect(self._commit_controls)
        voice_row.addWidget(self._small_label("Pitch"), 0)
        voice_row.addWidget(self._pitch_spin, 1)
        root.addLayout(voice_row, 0)

        audio_row = QtWidgets.QHBoxLayout()
        audio_row.setContentsMargins(0, 0, 0, 0)
        audio_row.setSpacing(6)
        self._format_combo = QtWidgets.QComboBox()
        self._format_combo.setToolTip("Output audio format requested from MiniMax.")
        for fmt in ("mp3", "wav", "flac"):
            self._format_combo.addItem(fmt.upper(), fmt)
        self._format_combo.currentIndexChanged.connect(self._commit_controls)
        audio_row.addWidget(self._small_label("Format"), 0)
        audio_row.addWidget(self._format_combo, 1)
        self._sample_rate_combo = QtWidgets.QComboBox()
        self._sample_rate_combo.setToolTip("Sample rate sent as audio_setting.sample_rate.")
        for rate in (32000, 44100, 24000, 16000):
            self._sample_rate_combo.addItem(str(rate), rate)
        self._sample_rate_combo.currentIndexChanged.connect(self._commit_controls)
        audio_row.addWidget(self._small_label("Rate"), 0)
        audio_row.addWidget(self._sample_rate_combo, 1)
        self._bitrate_combo = QtWidgets.QComboBox()
        self._bitrate_combo.setToolTip("Bitrate sent as audio_setting.bitrate.")
        for bitrate in (128000, 256000, 64000):
            self._bitrate_combo.addItem(str(bitrate), bitrate)
        self._bitrate_combo.currentIndexChanged.connect(self._commit_controls)
        audio_row.addWidget(self._small_label("Bitrate"), 0)
        audio_row.addWidget(self._bitrate_combo, 1)
        self._channel_combo = QtWidgets.QComboBox()
        self._channel_combo.setToolTip("Audio channels sent as audio_setting.channel.")
        self._channel_combo.addItem("Mono", 1)
        self._channel_combo.addItem("Stereo", 2)
        self._channel_combo.currentIndexChanged.connect(self._commit_controls)
        audio_row.addWidget(self._channel_combo, 0)
        root.addLayout(audio_row, 0)

        endpoint_row = QtWidgets.QHBoxLayout()
        endpoint_row.setContentsMargins(0, 0, 0, 0)
        endpoint_row.setSpacing(6)
        self._api_path_edit = QtWidgets.QLineEdit()
        self._api_path_edit.setToolTip("MiniMax text-to-audio endpoint path. The Speech 2.8 docs use /v1/t2a_v2.")
        self._api_path_edit.editingFinished.connect(self._commit_controls)
        endpoint_row.addWidget(self._small_label("Path"), 0)
        endpoint_row.addWidget(self._api_path_edit, 1)
        self._timeout_spin = QtWidgets.QSpinBox()
        self._timeout_spin.setRange(15, 600)
        self._timeout_spin.setSuffix(" s")
        self._timeout_spin.setToolTip("HTTP timeout for the MiniMax speech request.")
        self._timeout_spin.valueChanged.connect(self._commit_controls)
        endpoint_row.addWidget(self._small_label("Timeout"), 0)
        endpoint_row.addWidget(self._timeout_spin, 0)
        root.addLayout(endpoint_row, 0)

        self._output_dir_edit = QtWidgets.QLineEdit()
        self._output_dir_edit.setToolTip("Folder where generated audio files are saved. Relative paths resolve under the project folder or temp audio folder.")
        self._output_dir_edit.editingFinished.connect(self._on_output_dir_committed)
        self._output_btn = self._tool_button("Choose output folder")
        self._output_btn.clicked.connect(self._browse_output_dir)
        output_row = self._row("Output", self._output_dir_edit)
        output_row.addWidget(self._output_btn, 0)
        root.addLayout(output_row, 0)

        controls = QtWidgets.QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self._generate_btn = QtWidgets.QPushButton("Generate")
        self._generate_btn.setObjectName("PrimaryButton")
        self._generate_btn.clicked.connect(self._on_generate)
        self._play_btn = QtWidgets.QPushButton("Play")
        self._play_btn.clicked.connect(self._on_play_clicked)
        self._refresh_btn = QtWidgets.QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._refresh_history)
        self._open_btn = QtWidgets.QPushButton("Open")
        self._open_btn.clicked.connect(self._open_output_dir)
        controls.addWidget(self._generate_btn, 1)
        controls.addWidget(self._play_btn, 0)
        controls.addWidget(self._refresh_btn, 0)
        controls.addWidget(self._open_btn, 0)
        root.addLayout(controls, 0)

        self._history_combo = QtWidgets.QComboBox()
        self._history_combo.setToolTip("Generated audio files in the output folder. Select one and press Play.")
        self._history_combo.currentIndexChanged.connect(self._on_history_changed)
        root.addLayout(self._row("Audio", self._history_combo), 0)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(40)
        self._status.setMaximumHeight(56)
        self._status.setStyleSheet(
            "QLabel{background:#09111f;border:1px solid #1e293b;border-radius:4px;"
            "padding:4px 6px;color:#94a3b8;font-size:11px;}"
        )
        root.addWidget(self._status, 0)

        self._audio_out = QtWidgets.QLineEdit()
        self._audio_out.setReadOnly(True)
        root.addLayout(self._row("Path", self._audio_out), 0)

        self._refresh_from_params()
        self._schedule_scene_sync()

    def sizeHint(self):
        return QtCore.QSize(MINIMAX_TTS_API_BODY_W, MINIMAX_TTS_API_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(MINIMAX_TTS_API_BODY_W, MINIMAX_TTS_API_BODY_H)

    def _model(self):
        return getattr(self._node_item, "model", None)

    def _row(self, label: str, widget: QtWidgets.QWidget) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        lab = QtWidgets.QLabel(label)
        lab.setMinimumWidth(54)
        lab.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        row.addWidget(lab, 0)
        row.addWidget(widget, 1)
        return row

    def _small_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        return label

    def _tool_button(self, tooltip: str) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton()
        btn.setToolTip(tooltip)
        btn.setFixedSize(26, 24)
        try:
            icon = QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_DialogOpenButton)
            btn.setIcon(icon)
            btn.setIconSize(QtCore.QSize(16, 16))
        except Exception:
            btn.setText("...")
        return btn

    def _schedule_scene_sync(self) -> None:
        QtCore.QTimer.singleShot(0, self._sync_scene)
        QtCore.QTimer.singleShot(120, self._sync_scene)

    def _sync_scene(self) -> None:
        scene = self._node_item.scene() if hasattr(self._node_item, "scene") else None
        if scene is not None and not self._scene_connected:
            if hasattr(scene, "linksChanged"):
                try:
                    scene.linksChanged.connect(self._on_graph_changed)
                except Exception:
                    pass
            if hasattr(scene, "paramChanged"):
                try:
                    scene.paramChanged.connect(self._on_graph_changed)
                except Exception:
                    pass
            self._scene_connected = True
        self._refresh_connected_inputs()

    def _on_graph_changed(self, *_args) -> None:
        QtCore.QTimer.singleShot(0, self._refresh_connected_inputs)

    def _refresh_connected_inputs(self) -> None:
        text = _connected_text(self._node_item)
        changed = text != self._connected_text
        self._connected_text = text
        self._syncing = True
        try:
            self._text_edit.setEnabled(not bool(text))
            if text:
                self._text_edit.setPlainText(text)
        finally:
            self._syncing = False
        if changed:
            self._refresh_ready_status()

    def _refresh_from_params(self) -> None:
        model = self._model()
        self._syncing = True
        try:
            self._text_edit.setPlainText(_param_value(model, _PARAM_TEXT, ""))
            self._api_key_edit.setText(_param_value(model, _PARAM_API_KEY, ""))
            self._api_base_edit.setText(_param_value(model, _PARAM_API_BASE_URL, _default_api_base_url()))
            self._api_path_edit.setText(_param_value(model, _PARAM_API_PATH, "/v1/t2a_v2") or "/v1/t2a_v2")
            self._set_combo_data(self._model_combo, _normalize_model(_param_value(model, _PARAM_MODEL, "speech-2.8-turbo")))
            voice_id = _param_value(model, _PARAM_VOICE_ID, "English_Graceful_Lady") or "English_Graceful_Lady"
            self._set_voice_text(voice_id)
            self._speed_spin.setValue(_float_param(model, _PARAM_SPEED, 1.0, 0.5, 2.0))
            self._volume_spin.setValue(_float_param(model, _PARAM_VOLUME, 1.0, 0.1, 10.0))
            self._pitch_spin.setValue(_int_param(model, _PARAM_PITCH, 0, -12, 12))
            self._set_combo_data(self._format_combo, _normalize_format(_param_value(model, _PARAM_FORMAT, "mp3")))
            self._set_combo_data(self._sample_rate_combo, _int_param(model, _PARAM_SAMPLE_RATE, 32000, 8000, 96000))
            self._set_combo_data(self._bitrate_combo, _int_param(model, _PARAM_BITRATE, 128000, 16000, 320000))
            self._set_combo_data(self._channel_combo, _int_param(model, _PARAM_CHANNEL, 1, 1, 2))
            self._timeout_spin.setValue(_int_param(model, _PARAM_TIMEOUT, 120, 15, 600))
            output_dir = _param_value(model, _PARAM_OUTPUT_DIR, "")
            self._output_dir_edit.setText(output_dir or str(_default_output_dir(self._node_item)))
            audio_path = _param_value(model, AUDIO_OUTPUT_PARAM, "") or _param_value(model, PATH_OUTPUT_PARAM, "")
            self._audio_out.setText(audio_path)
            self._status.setText(_param_value(model, STATUS_OUTPUT_PARAM, "") or "Ready.")
        finally:
            self._syncing = False
        self._refresh_history(select_path=self._audio_out.text().strip())
        self._refresh_connected_inputs()
        self._refresh_ready_status()

    def _set_combo_data(self, combo: QtWidgets.QComboBox, value: Any) -> None:
        idx = combo.findData(value)
        if idx < 0:
            idx = combo.findText(str(value))
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _set_voice_text(self, voice_id: str) -> None:
        idx = self._voice_combo.findData(voice_id)
        if idx >= 0:
            self._voice_combo.setCurrentIndex(idx)
            return
        self._voice_combo.setEditText(voice_id)

    def _commit_controls(self) -> None:
        if self._syncing:
            return
        if not self._connected_text:
            _set_param_value(self._node_item, _PARAM_TEXT, self._text_edit.toPlainText().strip(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_API_KEY, self._api_key_edit.text().strip(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_API_BASE_URL, self._api_base_edit.text().strip() or _default_api_base_url(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_API_PATH, self._api_path_edit.text().strip() or "/v1/t2a_v2", notify_scene=False)
        _set_param_value(self._node_item, _PARAM_MODEL, str(self._model_combo.currentData() or "speech-2.8-turbo"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_VOICE_ID, self._voice_id(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_SPEED, f"{float(self._speed_spin.value()):.2f}", notify_scene=False)
        _set_param_value(self._node_item, _PARAM_VOLUME, f"{float(self._volume_spin.value()):.2f}", notify_scene=False)
        _set_param_value(self._node_item, _PARAM_PITCH, str(int(self._pitch_spin.value())), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_FORMAT, str(self._format_combo.currentData() or "mp3"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_SAMPLE_RATE, str(int(self._sample_rate_combo.currentData() or 32000)), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_BITRATE, str(int(self._bitrate_combo.currentData() or 128000)), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_CHANNEL, str(int(self._channel_combo.currentData() or 1)), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_TIMEOUT, str(int(self._timeout_spin.value())), notify_scene=False)
        default_output = str(_default_output_dir(self._node_item))
        output_dir = self._output_dir_edit.text().strip() or default_output
        _set_param_value(self._node_item, _PARAM_OUTPUT_DIR, "" if output_dir == default_output else output_dir, notify_scene=False)
        self._refresh_ready_status()

    def _on_output_dir_committed(self) -> None:
        self._commit_controls()
        self._refresh_history(select_path=self._audio_out.text().strip())

    def _voice_id(self) -> str:
        text = ""
        try:
            text = self._voice_combo.currentText()
        except Exception:
            text = ""
        return str(text or "").strip()

    def _effective_text(self) -> str:
        return (self._connected_text or self._text_edit.toPlainText()).strip()

    def _refresh_ready_status(self) -> None:
        if self._thread is not None:
            return
        if not (self._api_key_edit.text().strip() or _default_api_key()):
            self._generate_btn.setEnabled(False)
            self._set_status_text("Enter a MiniMax Speech API key or set MINIMAX_TTS_API_KEY.", warn=True)
            return
        if not self._voice_id():
            self._generate_btn.setEnabled(False)
            self._set_status_text("Enter a MiniMax voice_id.", warn=True)
            return
        if not self._effective_text():
            self._generate_btn.setEnabled(False)
            self._set_status_text("Enter text or connect a text input.", warn=True)
            return
        self._generate_btn.setEnabled(True)
        self._set_status_text("Ready for MiniMax speech generation.", ok=True)

    def _set_status_text(self, text: str, *, error: bool = False, warn: bool = False, ok: bool = False) -> None:
        clean = str(text or "").strip()
        if error:
            style = "QLabel{background:#160b0b;border:1px solid #7f1d1d;border-radius:4px;padding:4px 6px;color:#fca5a5;font-size:11px;}"
        elif warn:
            style = "QLabel{background:#160f08;border:1px solid #7c2d12;border-radius:4px;padding:4px 6px;color:#fdba74;font-size:11px;}"
        elif ok:
            style = "QLabel{background:#07140f;border:1px solid #14532d;border-radius:4px;padding:4px 6px;color:#86efac;font-size:11px;}"
        else:
            style = "QLabel{background:#09111f;border:1px solid #1e293b;border-radius:4px;padding:4px 6px;color:#94a3b8;font-size:11px;}"
        self._status.setStyleSheet(style)
        self._status.setText(clean)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._set_status_text(text, error=error)
        _set_param_value(self._node_item, STATUS_OUTPUT_PARAM, str(text or "").strip(), notify_scene=False)

    def _browse_output_dir(self) -> None:
        parent = _dialog_parent(self._node_item) or self
        start = self._output_dir_edit.text().strip() or str(_default_output_dir(self._node_item))
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Choose MiniMax TTS Output Folder", start)
        if not path:
            return
        self._output_dir_edit.setText(path)
        self._on_output_dir_committed()

    def _current_output_dir(self) -> Path:
        return _output_dir(self._node_item, self._output_dir_edit.text().strip())

    def _refresh_history(self, *args, select_path: str = "") -> None:
        current = str(select_path or self._selected_audio_path() or self._audio_out.text().strip())
        self._history_combo.blockSignals(True)
        try:
            self._history_combo.clear()
            output_dir = self._current_output_dir()
            paths = []
            if output_dir.exists():
                for path in output_dir.iterdir():
                    if path.is_file() and path.suffix.lower() in _AUDIO_EXTS:
                        paths.append(path)
            paths.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
            for path in paths[:60]:
                label = path.name
                try:
                    label = f"{path.name}  {time.strftime('%H:%M:%S', time.localtime(path.stat().st_mtime))}"
                except Exception:
                    pass
                self._history_combo.addItem(label, str(path))
            if current:
                idx = self._history_combo.findData(current)
                if idx >= 0:
                    self._history_combo.setCurrentIndex(idx)
            self._play_btn.setEnabled(self._history_combo.count() > 0)
        finally:
            self._history_combo.blockSignals(False)

    def _selected_audio_path(self) -> str:
        try:
            return str(self._history_combo.currentData() or "").strip()
        except Exception:
            return ""

    def _on_history_changed(self, *_args) -> None:
        path = self._selected_audio_path()
        if path:
            self._audio_out.setText(path)
            _set_param_value(self._node_item, AUDIO_OUTPUT_PARAM, path, notify_scene=False)
            _set_param_value(self._node_item, PATH_OUTPUT_PARAM, path, notify_scene=False)

    def _init_audio_player(self) -> bool:
        if self._player is not None:
            return True
        if QtMultimedia is None or not hasattr(QtMultimedia, "QMediaPlayer"):
            return False
        try:
            self._player = QtMultimedia.QMediaPlayer(self)
            if hasattr(QtMultimedia, "QAudioOutput"):
                self._audio_output = QtMultimedia.QAudioOutput(self)
                try:
                    self._audio_output.setVolume(1.0)
                    self._player.setAudioOutput(self._audio_output)
                except Exception:
                    pass
            if hasattr(self._player, "playbackStateChanged"):
                self._player.playbackStateChanged.connect(self._on_player_state_changed)
            elif hasattr(self._player, "stateChanged"):
                self._player.stateChanged.connect(self._on_player_state_changed)
            if hasattr(self._player, "mediaStatusChanged"):
                self._player.mediaStatusChanged.connect(self._on_media_status_changed)
            if hasattr(self._player, "errorOccurred"):
                self._player.errorOccurred.connect(self._on_player_error)
            elif hasattr(self._player, "error"):
                self._player.error.connect(self._on_player_error)
            return True
        except Exception:
            self._player = None
            self._audio_output = None
            return False

    def _load_audio_source(self, path: Path) -> bool:
        if not self._init_audio_player() or self._player is None:
            return False
        url = QtCore.QUrl.fromLocalFile(str(path))
        try:
            self._player.stop()
        except Exception:
            pass
        try:
            self._player.setSource(url)
            return True
        except Exception:
            try:
                media_content = QtMultimedia.QMediaContent(url)
                self._player.setMedia(media_content)
                return True
            except Exception:
                return False

    def _on_play_clicked(self) -> None:
        if self._playing:
            self._stop_playback()
            return
        path_text = self._selected_audio_path() or self._audio_out.text().strip()
        if not path_text:
            self._set_status("No generated audio selected.", error=True)
            return
        path = Path(path_text).expanduser()
        if not path.exists():
            self._set_status(f"Audio file does not exist: {path}", error=True)
            self._refresh_history()
            return
        if not self._load_audio_source(path):
            self._set_status("Qt audio playback is not available for this file.", error=True)
            return
        try:
            self._player.play()
            self._set_play_button(True)
            self._set_status(f"Playing {path.name}.")
        except Exception as exc:
            self._set_status(f"Could not play audio: {exc}", error=True)

    def _stop_playback(self) -> None:
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass
        self._set_play_button(False)

    def _set_play_button(self, playing: bool) -> None:
        self._playing = bool(playing)
        self._play_btn.setText("Stop" if self._playing else "Play")
        self._play_btn.setToolTip("Stop playback" if self._playing else "Play selected audio")

    def _state_is_playing(self, state) -> bool:
        if state is None:
            return False
        try:
            if state == getattr(QtMultimedia.QMediaPlayer, "PlayingState"):
                return True
        except Exception:
            pass
        try:
            if int(state) == int(getattr(QtMultimedia.QMediaPlayer, "PlayingState")):
                return True
        except Exception:
            pass
        name = ""
        try:
            name = str(getattr(state, "name", "") or "").strip().lower()
        except Exception:
            name = ""
        if not name:
            try:
                name = str(state).strip().lower()
            except Exception:
                name = ""
        return "playingstate" in name or name.endswith(".playing") or name == "playing"

    def _on_player_state_changed(self, state=None, *_args) -> None:
        if state is None and self._player is not None:
            try:
                state = self._player.playbackState()
            except Exception:
                try:
                    state = self._player.state()
                except Exception:
                    state = None
        self._set_play_button(self._state_is_playing(state))

    def _on_media_status_changed(self, status=None, *_args) -> None:
        try:
            if int(status) == int(QtMultimedia.QMediaPlayer.EndOfMedia):
                self._set_play_button(False)
        except Exception:
            pass

    def _on_player_error(self, *_args) -> None:
        msg = ""
        if self._player is not None:
            try:
                msg = str(self._player.errorString() or "")
            except Exception:
                msg = ""
        if msg:
            self._set_status(msg, error=True)
        self._set_play_button(False)

    def _open_output_dir(self) -> None:
        path = self._audio_out.text().strip() or self._selected_audio_path()
        target = Path(path).expanduser().parent if path else self._current_output_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target)))

    def _set_busy(self, busy: bool) -> None:
        self._generate_btn.setEnabled(not busy)
        self._api_key_edit.setEnabled(not busy)
        self._api_base_edit.setEnabled(not busy)
        self._api_path_edit.setEnabled(not busy)
        self._model_combo.setEnabled(not busy)
        self._voice_combo.setEnabled(not busy)
        self._speed_spin.setEnabled(not busy)
        self._volume_spin.setEnabled(not busy)
        self._pitch_spin.setEnabled(not busy)
        self._format_combo.setEnabled(not busy)
        self._sample_rate_combo.setEnabled(not busy)
        self._bitrate_combo.setEnabled(not busy)
        self._channel_combo.setEnabled(not busy)
        self._timeout_spin.setEnabled(not busy)
        self._output_dir_edit.setEnabled(not busy)
        self._output_btn.setEnabled(not busy)
        self._text_edit.setEnabled(not busy and not bool(self._connected_text))
        self._generate_btn.setText("Working..." if busy else "Generate")
        try:
            self._node_item.setBusyState(busy, "minimax_tts_api" if busy else "")
        except Exception:
            pass
        if not busy:
            self._generate_btn.setEnabled(bool(self._api_key_edit.text().strip() or _default_api_key()) and bool(self._voice_id()) and bool(self._effective_text()))

    def _collect_settings(self) -> MiniMaxTTSJobSettings:
        self._commit_controls()
        model = self._model()
        node_name = _safe_stem(str(getattr(model, "name", "") or "minimax_tts"))
        return MiniMaxTTSJobSettings(
            text=self._effective_text(),
            api_key=self._api_key_edit.text().strip() or _default_api_key(),
            api_base_url=self._api_base_edit.text().strip() or _default_api_base_url(),
            api_path=self._api_path_edit.text().strip() or "/v1/t2a_v2",
            model=str(self._model_combo.currentData() or "speech-2.8-turbo"),
            voice_id=self._voice_id(),
            speed=float(self._speed_spin.value()),
            volume=float(self._volume_spin.value()),
            pitch=int(self._pitch_spin.value()),
            audio_format=str(self._format_combo.currentData() or "mp3"),
            sample_rate=int(self._sample_rate_combo.currentData() or 32000),
            bitrate=int(self._bitrate_combo.currentData() or 128000),
            channel=int(self._channel_combo.currentData() or 1),
            output_dir=self._current_output_dir(),
            timeout=int(self._timeout_spin.value()),
            node_name=node_name,
        )

    def _on_generate(self) -> None:
        if self._thread is not None:
            return
        try:
            settings = self._collect_settings()
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return
        self._stop_playback()
        self._set_busy(True)
        self._set_status("Generating MiniMax speech...")
        self._thread = MiniMaxTTSThread(settings, self)
        self._thread.progressChanged.connect(lambda text: self._set_status(text))
        self._thread.resultReady.connect(self._on_generation_result)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.deleteLater()
        self._set_busy(False)

    def _on_generation_result(self, result_obj) -> None:
        result = result_obj if isinstance(result_obj, MiniMaxTTSJobResult) else MiniMaxTTSJobResult(False, error=str(result_obj))
        if result.ok:
            self._audio_out.setText(result.audio_path)
            _set_param_value(self._node_item, AUDIO_OUTPUT_PARAM, result.audio_path, notify_scene=False)
            _set_param_value(self._node_item, PATH_OUTPUT_PARAM, result.audio_path, notify_scene=False)
            _set_param_value(self._node_item, STATUS_OUTPUT_PARAM, result.status or "Generated audio.", notify_scene=False)
            self._refresh_history(select_path=result.audio_path)
            self._set_status(result.status or "Generated audio.")
        else:
            self._set_status(result.error or result.status or "MiniMax TTS generation failed.", error=True)


def _float_param(model, name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(_param_value(model, name, str(default)) or default)
    except Exception:
        value = default
    return max(float(minimum), min(float(maximum), value))


def _int_param(model, name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(float(_param_value(model, name, str(default)) or default))
    except Exception:
        value = default
    return max(int(minimum), min(int(maximum), value))


def _dialog_parent(node_item):
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                if win is not None:
                    return win
        except Exception:
            pass
    try:
        win = node_item.window()
        if win is not None:
            return win
    except Exception:
        pass
    return QtWidgets.QApplication.activeWindow()


def build_ports(node_item) -> None:
    defaults = {
        _PARAM_TEXT: "",
        _PARAM_API_KEY: "",
        _PARAM_API_BASE_URL: _default_api_base_url(),
        _PARAM_API_PATH: "/v1/t2a_v2",
        _PARAM_MODEL: "speech-2.8-turbo",
        _PARAM_VOICE_ID: "English_Graceful_Lady",
        _PARAM_SPEED: "1.00",
        _PARAM_VOLUME: "1.00",
        _PARAM_PITCH: "0",
        _PARAM_FORMAT: "mp3",
        _PARAM_SAMPLE_RATE: "32000",
        _PARAM_BITRATE: "128000",
        _PARAM_CHANNEL: "1",
        _PARAM_OUTPUT_DIR: "",
        _PARAM_TIMEOUT: "120",
    }
    for name, default in defaults.items():
        _ensure_param(node_item, name, default)
    for name in OUTPUT_PARAMS:
        _ensure_param(node_item, name, "")
    _ensure_hidden_params(getattr(node_item, "model", None), [*defaults.keys(), *OUTPUT_PARAMS])
    try:
        setattr(node_item, "_default_named_input", "text")
        setattr(node_item, "_show_default_input_with_named", True)
        node_item.ensure_input("text")
        node_item.ensure_output("audio")
        node_item.ensure_output("audio_path")
    except Exception:
        pass


def _ensure_body_space(node_item, bottom_y: int) -> None:
    try:
        pad = int(getattr(node_item, "_PADDING", 8))
        min_h = float(bottom_y + pad)
        if float(getattr(node_item, "height", 0.0) or 0.0) < min_h:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = min_h
    except Exception:
        pass


def _set_manual_ports(node_item, y_cursor: int) -> None:
    try:
        h = int(MINIMAX_TTS_API_BODY_H)
        node_item._input_port_pos["text"] = (0.0, float(y_cursor + max(48, h * 0.18)))
        node_item._output_port_pos["audio"] = (float(getattr(node_item, "width", MINIMAX_TTS_API_BODY_W)), float(y_cursor + h - 88))
        node_item._output_port_pos["audio_path"] = (float(getattr(node_item, "width", MINIMAX_TTS_API_BODY_W)), float(y_cursor + h - 44))
    except Exception:
        pass


def render_node_body(node_item, y_cursor: int) -> int:
    build_ports(node_item)
    body = MiniMaxTTSApiWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    w = max(int(hint.width()), int(getattr(node_item, "width", MINIMAX_TTS_API_BODY_W) or MINIMAX_TTS_API_BODY_W))
    h = max(int(hint.height()), int(body.minimumSizeHint().height()))
    try:
        body.setMinimumWidth(w)
        body.setMaximumWidth(w)
        proxy.setMinimumWidth(w)
        proxy.setMaximumWidth(w)
        proxy.setPreferredSize(w, h)
    except Exception:
        pass
    proxy.resize(w, h)
    try:
        if int(getattr(node_item, "width", 0) or 0) < w:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.width = w
    except Exception:
        pass
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    _set_manual_ports(node_item, int(y_cursor))
    bottom_y = int(y_cursor) + h
    _ensure_body_space(node_item, bottom_y)
    return bottom_y


MINIMAX_TTS_API_SPEC = Spec(
    stripe_color="#14b8a6",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "MINIMAX_TTS_API_NODE_ALIASES",
    "MINIMAX_TTS_API_NODE_KIND",
    "MINIMAX_TTS_API_NODE_KINDS",
    "MINIMAX_TTS_API_BODY_H",
    "MINIMAX_TTS_API_BODY_W",
    "MINIMAX_TTS_API_SPEC",
    "MiniMaxTTSJobSettings",
    "MiniMaxTTSJobResult",
    "build_ports",
    "render_node_body",
    "run_minimax_tts_job",
]
