from __future__ import annotations

import audioop
import io
import math
import threading
import time
import wave
from dataclasses import dataclass
from typing import Any, Callable

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

try:
    import pyaudio  # type: ignore
except Exception:
    pyaudio = None  # type: ignore

try:
    import soundcard as sc  # type: ignore
except Exception:
    sc = None  # type: ignore

try:
    import numpy as np  # type: ignore
except Exception:
    np = None  # type: ignore

from nodes.core import Spec
from .windows_loopback import is_supported as _process_loopback_supported


AUDIO_CAPTURE_NODE_KIND = "audio_capture"
AUDIO_CAPTURE_NODE_ALIASES = ["audio capture", "audiocapture"]
AUDIO_CAPTURE_NODE_KINDS = {AUDIO_CAPTURE_NODE_KIND, *AUDIO_CAPTURE_NODE_ALIASES}

AUDIO_CAPTURE_BODY_W = 420
AUDIO_CAPTURE_BODY_H = 196

SOURCE_OUTPUT_DEVICE = "output_device"
SOURCE_INPUT_DEVICE = "input_device"
SOURCE_APPLICATION = "application"

_PARAM_SOURCE_MODE = "source_mode"
_PARAM_DEVICE_ID = "device_id"
_PARAM_DEVICE_NAME = "device_name"
_PARAM_APP_NAME = "application"
_PARAM_SAMPLE_RATE = "sample_rate"
_PARAM_THRESHOLD = "threshold"
_PARAM_SILENCE_MS = "silence_ms"
_PARAM_PHRASE_MS = "phrase_ms"
_PARAM_BACKEND = "backend"
_PARAM_LIVE_MODE = "live_mode"
_PARAM_EXCLUDE_APP_AUDIO = "exclude_app_audio"
_HIDDEN_PARAM = "__ui_hidden_params"

_DEFAULT_SAMPLE_RATE = 16000
_DEFAULT_THRESHOLD = 0.012
_DEFAULT_SILENCE_MS = 850
_DEFAULT_PHRASE_MS = 20000
_DEFAULT_LIVE_MODE = True
_DEFAULT_EXCLUDE_APP_AUDIO = _process_loopback_supported()
_LIVE_PHRASE_MS = 2500
_LISTEN_WAIT_TIMEOUT_SECONDS = 1.2
_BLOCK_MS = 50

_PARAM_DEFAULTS = {
    _PARAM_SOURCE_MODE: SOURCE_OUTPUT_DEVICE,
    _PARAM_DEVICE_ID: "",
    _PARAM_DEVICE_NAME: "",
    _PARAM_APP_NAME: "",
    _PARAM_SAMPLE_RATE: str(_DEFAULT_SAMPLE_RATE),
    _PARAM_THRESHOLD: str(_DEFAULT_THRESHOLD),
    _PARAM_SILENCE_MS: str(_DEFAULT_SILENCE_MS),
    _PARAM_PHRASE_MS: str(_DEFAULT_PHRASE_MS),
    _PARAM_BACKEND: "auto",
    _PARAM_LIVE_MODE: "1",
    _PARAM_EXCLUDE_APP_AUDIO: "1" if _DEFAULT_EXCLUDE_APP_AUDIO else "0",
}


@dataclass
class CapturedAudioChunk:
    pcm: bytes
    sample_rate: int
    sample_width: int = 2
    channels: int = 1
    source_label: str = ""

    def wav_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(max(1, int(self.channels or 1)))
            wav.setsampwidth(max(1, int(self.sample_width or 2)))
            wav.setframerate(max(1, int(self.sample_rate or _DEFAULT_SAMPLE_RATE)))
            wav.writeframes(self.pcm or b"")
        return buffer.getvalue()


def _coerce_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        out = int(float(str(value or "").strip()))
    except Exception:
        out = int(default)
    if minimum is not None:
        out = max(int(minimum), out)
    if maximum is not None:
        out = min(int(maximum), out)
    return out


def _coerce_float(value: Any, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        out = float(str(value or "").strip())
    except Exception:
        out = float(default)
    if minimum is not None:
        out = max(float(minimum), out)
    if maximum is not None:
        out = min(float(maximum), out)
    return out


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "y", "on", "enabled", "live"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return bool(default)


def _normalize_source_mode(value: str) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in {"input", "input_device", "mic", "microphone"}:
        return SOURCE_INPUT_DEVICE
    if key in {"app", "application", "process", "application_audio"}:
        return SOURCE_APPLICATION
    return SOURCE_OUTPUT_DEVICE


def _param_value(model, name: str, default: str = "") -> str:
    if model is None:
        return default
    key = str(name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if str(entry.get("name", "") or "").strip().lower() == key:
            return str(entry.get("value", "") or "")
    return default


def _set_node_param(node_item, name: str, value: str, *, emit_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = str(name or "").strip().lower()
    text = str(value or "")
    found = False
    changed = False
    for entry in params:
        if str(entry.get("name", "") or "").strip().lower() == key:
            found = True
            if str(entry.get("value", "") or "") != text:
                entry["value"] = text
                changed = True
            break
    if not found:
        params.append({"name": name, "value": text})
        changed = True
    model.params = params
    _ensure_hidden_params(model, _PARAM_DEFAULTS.keys())
    if not changed or not emit_scene:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return
    if hasattr(scene, "set_node_params"):
        try:
            scene.set_node_params(model.name, params, rebuild=False, emit=True)
            return
        except TypeError:
            try:
                scene.set_node_params(model.name, params)
                return
            except Exception:
                pass
        except Exception:
            pass
    if hasattr(scene, "paramChanged"):
        try:
            scene.paramChanged.emit(model.name, list(params))
        except Exception:
            pass


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = str(name or "").strip().lower()
    for entry in params:
        if str(entry.get("name", "") or "").strip().lower() == key:
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
        if str(entry.get("name", "") or "").strip().lower() == _HIDDEN_PARAM:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": _HIDDEN_PARAM, "value": ""}
        params.append(hidden_entry)
    hidden = {
        part.strip().lower()
        for part in str(hidden_entry.get("value", "") or "").split(",")
        if part.strip()
    }
    for name in names or []:
        key = str(name or "").strip().lower()
        if key:
            hidden.add(key)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _device_label(entry: dict) -> str:
    name = str(entry.get("name", "") or "").strip() or "Default"
    backend = str(entry.get("backend", "") or "").strip()
    if backend:
        return f"{name} ({backend})"
    return name


def _soundcard_speakers() -> list[dict]:
    if sc is None:
        return []
    try:
        default = sc.default_speaker()
    except Exception:
        default = None
    default_id = str(getattr(default, "id", "") or "")
    out = []
    try:
        speakers = list(sc.all_speakers() or [])
    except Exception:
        speakers = []
    for idx, speaker in enumerate(speakers):
        name = str(getattr(speaker, "name", "") or "").strip() or f"Output {idx + 1}"
        speaker_id = str(getattr(speaker, "id", "") or "").strip() or name
        label = name
        if speaker_id and speaker_id == default_id:
            label = f"{label} - Default"
        out.append(
            {
                "id": speaker_id,
                "name": name,
                "label": label,
                "backend": "soundcard",
                "mode": SOURCE_OUTPUT_DEVICE,
                "available": True,
            }
        )
    return out


def _pyaudio_devices(*, input_only: bool = False, output_only: bool = False) -> list[dict]:
    if pyaudio is None:
        return []
    pa = None
    out = []
    try:
        pa = pyaudio.PyAudio()
        host_names = {}
        try:
            for host_idx in range(pa.get_host_api_count()):
                info = pa.get_host_api_info_by_index(host_idx) or {}
                host_names[int(info.get("index", host_idx))] = str(info.get("name", "") or "")
        except Exception:
            host_names = {}
        for idx in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(idx) or {}
            in_ch = int(info.get("maxInputChannels", 0) or 0)
            out_ch = int(info.get("maxOutputChannels", 0) or 0)
            if input_only and in_ch <= 0:
                continue
            if output_only and out_ch <= 0:
                continue
            if not input_only and not output_only and in_ch <= 0 and out_ch <= 0:
                continue
            name = str(info.get("name", "") or "").strip() or f"Device {idx}"
            host_name = host_names.get(int(info.get("hostApi", -1) or -1), "")
            label = name
            if host_name:
                label = f"{label} - {host_name}"
            out.append(
                {
                    "id": str(idx),
                    "index": idx,
                    "name": name,
                    "label": label,
                    "backend": "pyaudio",
                    "mode": SOURCE_INPUT_DEVICE if input_only else SOURCE_OUTPUT_DEVICE,
                    "input_channels": in_ch,
                    "output_channels": out_ch,
                    "sample_rate": int(float(info.get("defaultSampleRate", _DEFAULT_SAMPLE_RATE) or _DEFAULT_SAMPLE_RATE)),
                    "available": bool(input_only and in_ch > 0),
                }
            )
    except Exception:
        out = []
    finally:
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass
    return out


def _stereo_mix_devices() -> list[dict]:
    stereo = []
    for entry in _pyaudio_devices(input_only=True):
        name = str(entry.get("name", "") or "").strip().lower()
        if "stereo mix" in name or "what u hear" in name or "what you hear" in name:
            stereo.append(entry)
    return stereo


def available_targets(source_mode: str = SOURCE_OUTPUT_DEVICE) -> list[dict]:
    mode = _normalize_source_mode(source_mode)
    if mode == SOURCE_INPUT_DEVICE:
        return _pyaudio_devices(input_only=True)
    if mode == SOURCE_APPLICATION:
        return [
            {
                "id": "",
                "name": "Application capture backend unavailable",
                "label": "Application capture backend unavailable",
                "backend": "",
                "mode": SOURCE_APPLICATION,
                "available": False,
            }
        ]

    speakers = _soundcard_speakers()
    if speakers:
        return speakers

    stereo = _stereo_mix_devices()
    outputs = _pyaudio_devices(output_only=True)
    if stereo:
        for entry in stereo:
            entry["label"] = f"{entry.get('label') or entry.get('name')} - fallback"
            entry["mode"] = SOURCE_INPUT_DEVICE
            entry["available"] = True
        for entry in outputs:
            entry["available"] = False
            entry["label"] = f"{entry.get('label') or entry.get('name')} - needs soundcard"
        return stereo + outputs
    if outputs:
        for entry in outputs:
            entry["available"] = False
            entry["label"] = f"{entry.get('label') or entry.get('name')} - needs soundcard"
        return outputs
    return []


def backend_status() -> str:
    if sc is not None and np is not None:
        return "Output-device capture ready."
    if _stereo_mix_devices():
        return "Using Stereo Mix fallback. Install soundcard for output-device loopback."
    if pyaudio is None:
        return "Missing audio backend. Run setup.bat."
    return "Install soundcard for computer-output capture."


def config_from_node_item(node_item) -> dict:
    model = getattr(node_item, "model", None)
    mode = _normalize_source_mode(_param_value(model, _PARAM_SOURCE_MODE, SOURCE_OUTPUT_DEVICE))
    return {
        "source_mode": mode,
        "device_id": _param_value(model, _PARAM_DEVICE_ID, ""),
        "device_name": _param_value(model, _PARAM_DEVICE_NAME, ""),
        "application": _param_value(model, _PARAM_APP_NAME, ""),
        "sample_rate": _coerce_int(_param_value(model, _PARAM_SAMPLE_RATE, _DEFAULT_SAMPLE_RATE), _DEFAULT_SAMPLE_RATE, minimum=8000, maximum=96000),
        "threshold": _coerce_float(_param_value(model, _PARAM_THRESHOLD, _DEFAULT_THRESHOLD), _DEFAULT_THRESHOLD, minimum=0.001, maximum=0.5),
        "silence_ms": _coerce_int(_param_value(model, _PARAM_SILENCE_MS, _DEFAULT_SILENCE_MS), _DEFAULT_SILENCE_MS, minimum=150, maximum=5000),
        "phrase_ms": _coerce_int(_param_value(model, _PARAM_PHRASE_MS, _DEFAULT_PHRASE_MS), _DEFAULT_PHRASE_MS, minimum=1000, maximum=120000),
        "backend": _param_value(model, _PARAM_BACKEND, "auto") or "auto",
        "live_mode": _coerce_bool(_param_value(model, _PARAM_LIVE_MODE, "1"), _DEFAULT_LIVE_MODE),
        "exclude_app_audio": _coerce_bool(_param_value(model, _PARAM_EXCLUDE_APP_AUDIO, _PARAM_DEFAULTS[_PARAM_EXCLUDE_APP_AUDIO]), _DEFAULT_EXCLUDE_APP_AUDIO),
    }


def _soundcard_speaker_for_config(config: dict):
    if sc is None:
        return None
    target_id = str(config.get("device_id", "") or "").strip()
    target_name = str(config.get("device_name", "") or "").strip().lower()
    try:
        speakers = list(sc.all_speakers() or [])
    except Exception:
        speakers = []
    for speaker in speakers:
        speaker_id = str(getattr(speaker, "id", "") or "").strip()
        speaker_name = str(getattr(speaker, "name", "") or "").strip()
        if target_id and target_id in {speaker_id, speaker_name}:
            return speaker
        if target_name and speaker_name.lower() == target_name:
            return speaker
    try:
        return sc.default_speaker()
    except Exception:
        return speakers[0] if speakers else None


def _soundcard_loopback_for_speaker(speaker):
    if sc is None or speaker is None:
        return None
    target_id = str(getattr(speaker, "id", "") or "").strip()
    target_name = str(getattr(speaker, "name", "") or "").strip()
    try:
        microphones = list(sc.all_microphones(include_loopback=True) or [])
    except Exception:
        microphones = []
    loopbacks = [
        mic
        for mic in microphones
        if bool(getattr(mic, "isloopback", False))
    ]
    for mic in loopbacks:
        mic_id = str(getattr(mic, "id", "") or "").strip()
        mic_name = str(getattr(mic, "name", "") or "").strip()
        if target_id and mic_id == target_id:
            return mic
        if target_name and mic_name == target_name:
            return mic
    try:
        return sc.get_microphone(id=target_id or target_name, include_loopback=True)
    except Exception:
        return loopbacks[0] if loopbacks else None


def _pyaudio_input_for_config(config: dict, *, allow_stereo_mix_fallback: bool = False) -> dict | None:
    target_id = str(config.get("device_id", "") or "").strip()
    target_name = str(config.get("device_name", "") or "").strip().lower()
    inputs = _pyaudio_devices(input_only=True)
    if target_id:
        for entry in inputs:
            if target_id in {str(entry.get("id", "")), str(entry.get("index", "")), str(entry.get("name", ""))}:
                return entry
    if target_name:
        for entry in inputs:
            if str(entry.get("name", "") or "").strip().lower() == target_name:
                return entry
    if allow_stereo_mix_fallback:
        stereo = _stereo_mix_devices()
        if stereo:
            return stereo[0]
    return inputs[0] if inputs else None


def _pcm_level(pcm: bytes, sample_width: int = 2) -> float:
    if not pcm:
        return 0.0
    try:
        return min(1.0, float(audioop.rms(pcm, int(sample_width or 2))) / 32768.0)
    except Exception:
        return 0.0


def _float_frames_to_pcm_mono(frames) -> bytes:
    if np is None:
        raise RuntimeError("Missing dependency: numpy.")
    arr = np.asarray(frames, dtype=np.float32)
    if arr.size <= 0:
        return b""
    if len(arr.shape) > 1:
        arr = arr.mean(axis=1)
    arr = np.clip(arr.reshape(-1), -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2")
    return pcm.tobytes()


def _pyaudio_to_mono(pcm: bytes, channels: int) -> bytes:
    if int(channels or 1) <= 1:
        return pcm or b""
    try:
        return audioop.tomono(pcm or b"", 2, 0.5, 0.5)
    except Exception:
        return pcm or b""


def _live_mode_enabled(config: dict) -> bool:
    return _coerce_bool((config or {}).get("live_mode"), _DEFAULT_LIVE_MODE)


def _exclude_app_audio_enabled(config: dict) -> bool:
    mode = _normalize_source_mode(config.get("source_mode", SOURCE_OUTPUT_DEVICE))
    return mode == SOURCE_OUTPUT_DEVICE and _coerce_bool(config.get("exclude_app_audio"), _DEFAULT_EXCLUDE_APP_AUDIO)


def _phrase_ms_for_config(config: dict) -> int:
    phrase_ms = _coerce_int((config or {}).get("phrase_ms"), _DEFAULT_PHRASE_MS, minimum=1000, maximum=120000)
    if _live_mode_enabled(config):
        return min(phrase_ms, _LIVE_PHRASE_MS)
    return phrase_ms


def _wait_timeout_for_config(config: dict) -> float | None:
    return None if _live_mode_enabled(config) else _LISTEN_WAIT_TIMEOUT_SECONDS


def _record_phrase(
    read_block: Callable[[], bytes],
    *,
    sample_rate: int,
    threshold: float,
    silence_ms: int,
    phrase_ms: int,
    wait_timeout_seconds: float | None = 1.2,
    should_stop: Callable[[], bool] | None = None,
    is_paused: Callable[[], bool] | None = None,
    source_label: str = "",
) -> tuple[CapturedAudioChunk | None, str]:
    frames: list[bytes] = []
    started = False
    wait_started_at = time.monotonic()
    voice_seen_at = wait_started_at
    phrase_started_at = wait_started_at
    silence_seconds = max(0.15, float(silence_ms or _DEFAULT_SILENCE_MS) / 1000.0)
    phrase_seconds = max(1.0, float(phrase_ms or _DEFAULT_PHRASE_MS) / 1000.0)
    wait_timeout = None if wait_timeout_seconds is None else max(0.1, float(wait_timeout_seconds or 0.0))

    while True:
        if should_stop and should_stop():
            return None, ""
        if is_paused and is_paused():
            return None, ""
        now = time.monotonic()
        if not started and wait_timeout is not None and (now - wait_started_at) >= wait_timeout:
            return None, ""
        if started and (now - phrase_started_at) >= phrase_seconds:
            break

        try:
            block = read_block()
        except Exception as exc:
            return None, f"Audio Capture failed: {exc}"
        level = _pcm_level(block, 2)
        if level >= threshold:
            if not started:
                started = True
                phrase_started_at = now
            voice_seen_at = now
        if started:
            frames.append(block)
            if (now - voice_seen_at) >= silence_seconds:
                break

    pcm = b"".join(frames)
    if not pcm:
        return None, ""
    return CapturedAudioChunk(pcm=pcm, sample_rate=int(sample_rate), sample_width=2, channels=1, source_label=source_label), ""


def _capture_next_phrase_soundcard(
    config: dict,
    *,
    should_stop: Callable[[], bool] | None = None,
    is_paused: Callable[[], bool] | None = None,
) -> tuple[CapturedAudioChunk | None, str]:
    if sc is None or np is None:
        return None, "Output-device capture needs soundcard and numpy. Run setup.bat."
    speaker = _soundcard_speaker_for_config(config)
    if speaker is None:
        return None, "No output device found for Audio Capture."
    loopback = _soundcard_loopback_for_speaker(speaker)
    if loopback is None:
        return None, "No loopback recorder found for the selected output device."
    sample_rate = _coerce_int(config.get("sample_rate"), _DEFAULT_SAMPLE_RATE, minimum=8000, maximum=96000)
    block_frames = max(160, int(sample_rate * (_BLOCK_MS / 1000.0)))
    source_label = str(getattr(speaker, "name", "") or "Output device")

    try:
        with loopback.recorder(samplerate=sample_rate) as recorder:
            def _read_block() -> bytes:
                return _float_frames_to_pcm_mono(recorder.record(numframes=block_frames))

            return _record_phrase(
                _read_block,
                sample_rate=sample_rate,
                threshold=_coerce_float(config.get("threshold"), _DEFAULT_THRESHOLD, minimum=0.001, maximum=0.5),
                silence_ms=_coerce_int(config.get("silence_ms"), _DEFAULT_SILENCE_MS, minimum=150, maximum=5000),
                phrase_ms=_phrase_ms_for_config(config),
                wait_timeout_seconds=_wait_timeout_for_config(config),
                should_stop=should_stop,
                is_paused=is_paused,
                source_label=source_label,
            )
    except Exception as exc:
        return None, f"Output-device capture failed: {exc}"


def _capture_next_phrase_pyaudio(
    config: dict,
    *,
    allow_stereo_mix_fallback: bool = False,
    should_stop: Callable[[], bool] | None = None,
    is_paused: Callable[[], bool] | None = None,
) -> tuple[CapturedAudioChunk | None, str]:
    if pyaudio is None:
        return None, "Audio Capture needs PyAudio. Run setup.bat."
    entry = _pyaudio_input_for_config(config, allow_stereo_mix_fallback=allow_stereo_mix_fallback)
    if not entry:
        if allow_stereo_mix_fallback:
            return None, "Output capture needs soundcard, or enable Stereo Mix as a recording device."
        return None, "No input device found for Audio Capture."
    pa = None
    stream = None
    try:
        pa = pyaudio.PyAudio()
        device_index = int(entry.get("index", entry.get("id", 0)) or 0)
        sample_rate = int(entry.get("sample_rate", 0) or config.get("sample_rate") or _DEFAULT_SAMPLE_RATE)
        channels = min(2, max(1, int(entry.get("input_channels", 1) or 1)))
        block_frames = max(160, int(sample_rate * (_BLOCK_MS / 1000.0)))
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=block_frames,
        )

        def _read_block() -> bytes:
            data = stream.read(block_frames, exception_on_overflow=False)
            return _pyaudio_to_mono(data, channels)

        return _record_phrase(
            _read_block,
            sample_rate=sample_rate,
            threshold=_coerce_float(config.get("threshold"), _DEFAULT_THRESHOLD, minimum=0.001, maximum=0.5),
            silence_ms=_coerce_int(config.get("silence_ms"), _DEFAULT_SILENCE_MS, minimum=150, maximum=5000),
            phrase_ms=_phrase_ms_for_config(config),
            wait_timeout_seconds=_wait_timeout_for_config(config),
            should_stop=should_stop,
            is_paused=is_paused,
            source_label=str(entry.get("name", "") or "Input device"),
        )
    except Exception as exc:
        return None, f"Input-device capture failed: {exc}"
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass


def capture_next_phrase(
    config: dict,
    *,
    should_stop: Callable[[], bool] | None = None,
    is_paused: Callable[[], bool] | None = None,
) -> tuple[CapturedAudioChunk | None, str]:
    if _exclude_app_audio_enabled(config):
        from .stream import open_audio_reader
        try:
            with open_audio_reader(config) as (read, rate, label):
                return _record_phrase(
                    read, sample_rate=rate,
                    threshold=_coerce_float(config.get("threshold"), _DEFAULT_THRESHOLD, minimum=0.001, maximum=0.5),
                    silence_ms=_coerce_int(config.get("silence_ms"), _DEFAULT_SILENCE_MS, minimum=150, maximum=5000),
                    phrase_ms=_phrase_ms_for_config(config), wait_timeout_seconds=_wait_timeout_for_config(config),
                    should_stop=should_stop, is_paused=is_paused, source_label=label,
                )
        except Exception as exc:
            return None, f"Capture excluding QubitMCP failed: {exc}"
    mode = _normalize_source_mode(str(config.get("source_mode", "") or SOURCE_OUTPUT_DEVICE))
    if mode == SOURCE_APPLICATION:
        return None, "Application-specific capture is not available yet. Use Output Device capture."
    if mode == SOURCE_INPUT_DEVICE:
        return _capture_next_phrase_pyaudio(config, should_stop=should_stop, is_paused=is_paused)
    if sc is not None and np is not None:
        return _capture_next_phrase_soundcard(config, should_stop=should_stop, is_paused=is_paused)
    return _capture_next_phrase_pyaudio(
        config,
        allow_stereo_mix_fallback=True,
        should_stop=should_stop,
        is_paused=is_paused,
    )


def capture_level(config: dict, duration_seconds: float = 0.25) -> tuple[float, str]:
    from .stream import record_sample
    try:
        chunk = record_sample(config, seconds=duration_seconds)
        return _pcm_level(chunk.pcm, chunk.sample_width), ""
    except Exception as exc:
        return 0.0, str(exc)


def build_ports(node_item) -> None:
    for name, default in _PARAM_DEFAULTS.items():
        _ensure_param(node_item, name, default)
    _ensure_hidden_params(getattr(node_item, "model", None), _PARAM_DEFAULTS.keys())
    if hasattr(node_item, "ensure_output"):
        node_item.ensure_output("audio")


class AudioCaptureWidget(QtWidgets.QWidget):
    _sample_level = QtCore.Signal(float)
    _sample_done = QtCore.Signal(object, str)

    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._syncing = False
        self._testing = False
        self._test_stop = threading.Event()
        self.destroyed.connect(self._test_stop.set)
        self._test_player = None
        self._test_file = None
        self._test_peak = 0.0
        self._test_summary = ""
        self._test_play_started = False
        self.setMinimumSize(AUDIO_CAPTURE_BODY_W, AUDIO_CAPTURE_BODY_H)
        try:
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass

        self._mode_combo = QtWidgets.QComboBox()
        self._mode_combo.addItem("Output Device", SOURCE_OUTPUT_DEVICE)
        self._mode_combo.addItem("Input Device", SOURCE_INPUT_DEVICE)
        self._mode_combo.addItem("Application", SOURCE_APPLICATION)

        self._device_combo = QtWidgets.QComboBox()
        self._device_combo.setMinimumWidth(220)
        size_policy = getattr(QtWidgets.QComboBox, "AdjustToMinimumContentsLengthWithIcon", None)
        if size_policy is None:
            enum_group = getattr(QtWidgets.QComboBox, "SizeAdjustPolicy", None)
            size_policy = getattr(enum_group, "AdjustToMinimumContentsLengthWithIcon", None) if enum_group is not None else None
        if size_policy is not None:
            self._device_combo.setSizeAdjustPolicy(size_policy)

        self._refresh_btn = QtWidgets.QPushButton("Refresh")
        self._test_btn = QtWidgets.QPushButton("Test & Listen")
        self._test_btn.setToolTip("Record three seconds from the selected source, show its level, then play the sample. Playback starts after recording stops.")
        self._live_check = QtWidgets.QCheckBox("Live")
        self._live_check.setToolTip("Keep the capture stream waiting for random speech and split long speech into short live chunks.")
        self._exclude_app_check = QtWidgets.QCheckBox("Exclude QubitMCP audio (all outputs)")
        self._exclude_app_check.setToolTip(
            "Capture other apps, including YouTube, while excluding Voice Actor speech and test playback. "
            "Windows applies this to all output devices, so the device selector is unused. "
            "Restart listening after changing this setting. Requires Windows build 20348 or newer."
        )
        self._exclude_app_check.setChecked(_coerce_bool(
            _param_value(getattr(node_item, "model", None), _PARAM_EXCLUDE_APP_AUDIO, _PARAM_DEFAULTS[_PARAM_EXCLUDE_APP_AUDIO]),
            _DEFAULT_EXCLUDE_APP_AUDIO,
        ))
        self._exclude_app_check.toggled.connect(self._on_exclude_app_changed)
        self._level = QtWidgets.QProgressBar()
        self._level.setRange(0, 100)
        self._level.setTextVisible(True)
        self._level.setFormat("No signal")
        self._level.setFixedHeight(22)
        self._level.setToolTip("Recorded level in dBFS. 0 dBFS is full scale; more negative values are quieter.")
        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("QLabel{color:#94a3b8;font-size:10px;}")

        combo_style = (
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:3px 8px;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        self._mode_combo.setStyleSheet(combo_style)
        self._device_combo.setStyleSheet(combo_style)
        button_style = (
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:4px 8px;}"
            "QPushButton:hover{background:#273449;}"
            "QPushButton:disabled{background:#111827;color:#64748b;border-color:#334155;}"
        )
        self._refresh_btn.setStyleSheet(button_style)
        self._test_btn.setStyleSheet(button_style)
        self._live_check.setStyleSheet("QCheckBox{color:#cbd5e1;font-size:11px;}")
        self._exclude_app_check.setStyleSheet("QCheckBox{color:#cbd5e1;font-size:11px;}")
        self._level.setStyleSheet(
            "QProgressBar{background:#0f1216;border:1px solid #334155;border-radius:4px;}"
            "QProgressBar::chunk{background:#14b8a6;border-radius:3px;}"
        )

        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(QtWidgets.QLabel("Source"), 0)
        top.addWidget(self._mode_combo, 0)
        top.addWidget(self._refresh_btn, 0)
        top.addWidget(self._live_check, 0)

        device_row = QtWidgets.QHBoxLayout()
        device_row.setContentsMargins(0, 0, 0, 0)
        device_row.setSpacing(6)
        device_row.addWidget(QtWidgets.QLabel("Device"), 0)
        device_row.addWidget(self._device_combo, 1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(top, 0)
        layout.addLayout(device_row, 0)
        layout.addWidget(self._exclude_app_check, 0)
        test_row = QtWidgets.QHBoxLayout()
        test_row.addWidget(self._test_btn, 0)
        test_row.addWidget(self._level, 1)
        layout.addLayout(test_row, 0)
        layout.addWidget(self._status, 0)

        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        self._refresh_btn.clicked.connect(self._refresh_targets)
        self._test_btn.clicked.connect(self._test_level)
        self._sample_level.connect(self._on_sample_level)
        self._sample_done.connect(self._on_sample_done)

        mode = _normalize_source_mode(_param_value(getattr(node_item, "model", None), _PARAM_SOURCE_MODE, SOURCE_OUTPUT_DEVICE))
        self._live_check.blockSignals(True)
        self._live_check.setChecked(
            _coerce_bool(
                _param_value(getattr(node_item, "model", None), _PARAM_LIVE_MODE, "1"),
                _DEFAULT_LIVE_MODE,
            )
        )
        self._live_check.blockSignals(False)
        self._live_check.toggled.connect(self._on_live_changed)
        for idx in range(self._mode_combo.count()):
            if str(self._mode_combo.itemData(idx) or "") == mode:
                self._mode_combo.setCurrentIndex(idx)
                break
        self._refresh_targets()

    def sizeHint(self):
        return QtCore.QSize(AUDIO_CAPTURE_BODY_W, AUDIO_CAPTURE_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(AUDIO_CAPTURE_BODY_W, AUDIO_CAPTURE_BODY_H)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status.setText(str(text or ""))
        self._status.setStyleSheet("QLabel{color:#fca5a5;font-size:10px;}" if error else "QLabel{color:#94a3b8;font-size:10px;}")

    def _config(self) -> dict:
        return config_from_node_item(self._node_item)

    def _current_mode(self) -> str:
        return _normalize_source_mode(str(self._mode_combo.currentData() or SOURCE_OUTPUT_DEVICE))

    def _refresh_targets(self) -> None:
        selected_id = _param_value(getattr(self._node_item, "model", None), _PARAM_DEVICE_ID, "")
        mode = self._current_mode()
        targets = available_targets(mode)
        self._syncing = True
        try:
            self._device_combo.blockSignals(True)
            self._device_combo.clear()
            selected_index = 0
            if not targets:
                self._device_combo.addItem("No devices found", {})
            for idx, target in enumerate(targets):
                label = str(target.get("label", "") or _device_label(target))
                self._device_combo.addItem(label, dict(target))
                if selected_id and selected_id == str(target.get("id", "") or ""):
                    selected_index = idx
            self._device_combo.setCurrentIndex(selected_index)
        finally:
            self._device_combo.blockSignals(False)
            self._syncing = False
        self._persist_selected_target()
        self._update_capture_source_ui()

    def _update_capture_source_ui(self) -> None:
        output = self._current_mode() == SOURCE_OUTPUT_DEVICE
        protected = output and self._exclude_app_check.isChecked()
        self._exclude_app_check.setEnabled(output and _process_loopback_supported() and not self._testing)
        self._device_combo.setEnabled(not self._testing and not protected)
        self._set_status("Captures other apps on all outputs. QubitMCP audio is excluded." if protected else backend_status())

    def _on_exclude_app_changed(self, checked: bool) -> None:
        _set_node_param(self._node_item, _PARAM_EXCLUDE_APP_AUDIO, "1" if checked else "0")
        self._update_capture_source_ui()

    def _persist_selected_target(self) -> None:
        data = self._device_combo.currentData()
        if not isinstance(data, dict):
            data = {}
        _set_node_param(self._node_item, _PARAM_SOURCE_MODE, self._current_mode())
        _set_node_param(self._node_item, _PARAM_DEVICE_ID, str(data.get("id", "") or ""))
        _set_node_param(self._node_item, _PARAM_DEVICE_NAME, str(data.get("name", "") or ""))

    def _on_mode_changed(self, _idx: int) -> None:
        if self._syncing:
            return
        _set_node_param(self._node_item, _PARAM_SOURCE_MODE, self._current_mode())
        _set_node_param(self._node_item, _PARAM_DEVICE_ID, "")
        _set_node_param(self._node_item, _PARAM_DEVICE_NAME, "")
        self._refresh_targets()

    def _on_device_changed(self, _idx: int) -> None:
        if self._syncing:
            return
        self._persist_selected_target()

    def _on_live_changed(self, checked: bool) -> None:
        if self._syncing:
            return
        _set_node_param(self._node_item, _PARAM_LIVE_MODE, "1" if checked else "0")
        if checked:
            self._set_status("Live capture enabled. Long speech is split into short chunks.")
        else:
            self._set_status("Live capture disabled. Capture waits for phrase endings.")

    def _test_level(self) -> None:
        if self._testing:
            if self._test_player is not None:
                self._finish_test()
            return
        config = self._config()
        self._testing = True
        self._test_stop.clear()
        self._test_peak = 0.0
        self._test_summary = ""
        self._level.setValue(0)
        self._level.setFormat("Recording...")
        self._test_btn.setEnabled(False)
        self._mode_combo.setEnabled(False)
        self._device_combo.setEnabled(False)
        self._exclude_app_check.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._set_status("Recording 3 seconds. Speak or play audio on the selected device...")

        def _worker():
            from .stream import record_sample
            sample, err = None, ""
            try:
                sample = record_sample(config, seconds=3.0, on_level=self._sample_level.emit,
                                       should_stop=self._test_stop.is_set)
            except Exception as exc:
                err = str(exc)
            if not self._test_stop.is_set():
                try:
                    self._sample_done.emit(sample, err)
                except RuntimeError:
                    pass  # The node was removed while capture was finishing.

        threading.Thread(target=_worker, daemon=True).start()

    @QtCore.Slot(float)
    def _on_sample_level(self, level: float) -> None:
        self._test_peak = max(self._test_peak, level)
        db = 20.0 * math.log10(max(level, 0.000001))
        self._level.setValue(max(0, min(100, round((db + 60.0) * 100.0 / 60.0))))
        self._level.setFormat(f"{db:.0f} dBFS" if level > 0 else "No signal")

    @QtCore.Slot(object, str)
    def _on_sample_done(self, sample, err: str) -> None:
        if err or sample is None:
            self._finish_test(err or "No audio captured.", error=True)
            return
        peak_db = 20.0 * math.log10(max(self._test_peak, 0.000001))
        if self._test_peak <= 0:
            self._test_summary = "No signal detected. Check the selected device and source volume."
        else:
            self._test_summary = f"Loudest recorded level: {peak_db:.0f} dBFS."
            if peak_db < -45:
                self._test_summary += " Signal is very quiet."
        self._on_sample_level(self._test_peak)
        self._play_test_sample(sample.wav_bytes())

    def _play_test_sample(self, wav_bytes: bytes) -> None:
        try:
            try:
                from PySide6.QtMultimedia import QSoundEffect
            except ImportError:
                from PySide2.QtMultimedia import QSoundEffect
            sample_file = QtCore.QTemporaryFile(QtCore.QDir.tempPath() + "/qubit_audio_test_XXXXXX.wav", self)
            self._test_file = sample_file
            if not sample_file.open():
                raise RuntimeError(sample_file.errorString())
            if sample_file.write(wav_bytes) != len(wav_bytes):
                raise RuntimeError("Could not write the audio test sample.")
            sample_file.close()
            player = QSoundEffect(self)
            self._test_player = player
            self._test_play_started = False
            player.setVolume(1.0)
            player.playingChanged.connect(self._on_test_playing_changed)
            player.statusChanged.connect(self._on_test_playback_status)
            self._test_btn.setText("Stop playback")
            self._test_btn.setEnabled(True)
            self._set_status(f"{self._test_summary} Playing recorded sample...")
            player.setSource(QtCore.QUrl.fromLocalFile(sample_file.fileName()))
            if self._test_player is player:
                player.play()
        except Exception as exc:
            self._finish_test(f"{self._test_summary} Playback unavailable: {exc}", error=True)

    def _on_test_playing_changed(self) -> None:
        player = self._test_player
        if player is None:
            return
        if player.isPlaying():
            self._test_play_started = True
        elif self._test_play_started:
            self._finish_test()

    def _on_test_playback_status(self) -> None:
        player = self._test_player
        if player is not None and player.status() == player.Error:
            self._finish_test(f"{self._test_summary} Could not play the recorded sample.", error=True)

    def _finish_test(self, message: str = "", *, error: bool = False) -> None:
        player, self._test_player = self._test_player, None
        if player is not None:
            player.stop()
            player.deleteLater()
        if self._test_file is not None:
            self._test_file.remove()
            self._test_file.deleteLater()
            self._test_file = None
        self._testing = False
        self._test_btn.setText("Test & Listen")
        self._test_btn.setEnabled(True)
        self._mode_combo.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._update_capture_source_ui()
        self._set_status(message or self._test_summary, error=error)


def render_node_body(node_item, y_cursor: int) -> int:
    try:
        body = AudioCaptureWidget(node_item, None)
    except Exception as exc:
        print("[EchoGraph] Audio Capture UI init failed:", exc)
        body = QtWidgets.QFrame()
        body.setStyleSheet("QFrame{background:#0f1216;color:#e5e7eb;border:1px solid #334;border-radius:6px;}")
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(10, 8, 10, 8)
        msg = QtWidgets.QLabel("Audio Capture UI failed to load.")
        msg.setWordWrap(True)
        msg.setStyleSheet("QLabel{color:#e5e7eb;}")
        lay.addWidget(msg, 1)

    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    size_hint = body.sizeHint()
    minimum_hint = body.minimumSizeHint()
    h = max(int(size_hint.height()), int(minimum_hint.height()), AUDIO_CAPTURE_BODY_H)
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


AUDIO_CAPTURE_SPEC = Spec(
    stripe_color="#14b8a6",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
