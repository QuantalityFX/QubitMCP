from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec


MINIMAX_H3_VIDEO_NODE_KIND = "minimax_h3_video"
MINIMAX_H3_VIDEO_NODE_ALIASES = [
    "minimax h3 video",
    "minimax_h3",
    "minimax h3",
    "h3_video",
    "h3 video",
    "text_to_video",
    "text to video",
    "image_to_video",
    "image to video",
]
MINIMAX_H3_VIDEO_NODE_KINDS = {MINIMAX_H3_VIDEO_NODE_KIND, *MINIMAX_H3_VIDEO_NODE_ALIASES}
MINIMAX_H3_VIDEO_BODY_W = 520
MINIMAX_H3_VIDEO_BODY_H = 454

MP4_OUTPUT_PARAM = "mp4_path"
TASK_OUTPUT_PARAM = "task_id"
STATUS_OUTPUT_PARAM = "h3_status"
OUTPUT_PARAMS = (MP4_OUTPUT_PARAM, TASK_OUTPUT_PARAM, STATUS_OUTPUT_PARAM)

_PARAM_PROMPT = "__minimax_h3_prompt"
_PARAM_FIRST_IMAGE = "__minimax_h3_first_image"
_PARAM_LAST_IMAGE = "__minimax_h3_last_image"
_PARAM_MODEL_PATH = "__minimax_h3_model_path"
_PARAM_VARIANT = "__minimax_h3_variant"
_PARAM_LOCAL_URL = "__minimax_h3_local_url"
_PARAM_CREATE_PATH = "__minimax_h3_create_path"
_PARAM_QUERY_PATH = "__minimax_h3_query_path"
_PARAM_RETRIEVE_PATH = "__minimax_h3_retrieve_path"
_PARAM_DURATION = "__minimax_h3_duration"
_PARAM_RATIO = "__minimax_h3_ratio"
_PARAM_RESOLUTION = "__minimax_h3_resolution"
_PARAM_OUTPUT_DIR = "__minimax_h3_output_dir"
_PARAM_POLL_INTERVAL = "__minimax_h3_poll_interval"
_PARAM_TIMEOUT = "__minimax_h3_timeout"
_HIDDEN_PARAM = "__ui_hidden_params"

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".exr"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}
_MANIFEST_NAME = "qubitmcp_minimax_h3_manifest.json"
_EXPECTED_COMPONENTS = ("processor", "tokenizer", "text_encoder", "transformer", "audio_vae")
_VIDEO_VAE_COMPONENTS = ("visual_vae", "video_vae")


def _app_home_dir() -> Path:
    raw = (os.environ.get("QUBITMCP_HOME") or os.environ.get("QUBITFIELD_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    try:
        repo = Path(__file__).resolve().parents[2]
        if (repo / "models" / "MiniMax-H3").exists():
            return repo
    except Exception:
        pass
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return Path(local).expanduser() / "QubitMCP"
    return Path.home() / ".qubitmcp"


def minimax_h3_model_dir(home: Path | None = None) -> Path:
    return (home or _app_home_dir()) / "models" / "MiniMax-H3"


def minimax_h3_setup_script() -> Path:
    return Path(__file__).resolve().parents[2] / "setup.bat"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_local_url() -> str:
    return (
        os.environ.get("QUBITMCP_MINIMAX_H3_LOCAL_URL")
        or os.environ.get("SGLANG_DEPLOYMENT_URL")
        or "http://127.0.0.1:30010"
    ).strip()


def _default_output_dir(node_item) -> Path:
    workflow_dir = _workflow_dir_for_node(node_item)
    if workflow_dir is not None:
        return workflow_dir / "videos" / "minimax_h3"
    return Path(tempfile.gettempdir()) / "EchoGraph" / "minimax_h3"


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _clean_path_text(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _safe_stem(raw: str, fallback: str = "minimax_h3") -> str:
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


def _resolve_path(node_item, raw: str) -> Path:
    text = _clean_path_text(raw)
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    base = _workflow_dir_for_node(node_item) or Path.cwd()
    return base / path


def _output_dir(node_item, raw: str) -> Path:
    text = _clean_path_text(raw)
    if not text:
        return _default_output_dir(node_item)
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    base = _workflow_dir_for_node(node_item) or Path.cwd()
    return base / path


def _normalize_variant(raw: str) -> str:
    text = str(raw or "").strip().lower().replace("-", "_")
    if text in {"ref2va", "ref", "r2va"}:
        return "ref2va"
    return "fl2va"


def _variant_dir(variant: str) -> str:
    return "Ref2VA" if _normalize_variant(variant) == "ref2va" else "FL2VA"


def _model_root_and_family(path: Path, variant: str) -> tuple[Path, Path, str]:
    family = _variant_dir(variant)
    if path.name.lower() == family.lower():
        return path.parent, path, family
    if path.name.lower() in {"fl2va", "ref2va"}:
        actual_family = "Ref2VA" if path.name.lower() == "ref2va" else "FL2VA"
        return path.parent, path, actual_family
    return path, path / family, family


def _has_incomplete_download(root: Path) -> bool:
    cache = root / ".cache" / "huggingface" / "download"
    if not cache.exists():
        return False
    try:
        for current_root, _dirs, files in os.walk(cache):
            for name in files:
                if str(name).lower().endswith(".incomplete"):
                    return True
    except Exception:
        return False
    return False


@dataclass(frozen=True)
class MiniMaxH3ModelStatus:
    root: Path
    variant: str
    ready: bool
    detail: str


def minimax_h3_model_status(model_path: str | Path, variant: str = "fl2va") -> MiniMaxH3ModelStatus:
    requested = Path(model_path).expanduser() if str(model_path or "").strip() else minimax_h3_model_dir()
    root, family_dir, family = _model_root_and_family(requested, variant)
    status_root = root if root.exists() else requested
    if not requested.exists():
        return MiniMaxH3ModelStatus(status_root, family, False, "MiniMax H3 model is not downloaded.")
    if not requested.is_dir():
        return MiniMaxH3ModelStatus(requested, family, False, "MiniMax H3 model path is not a folder.")
    if _has_incomplete_download(root):
        return MiniMaxH3ModelStatus(root, family, False, "MiniMax H3 download is still running; incomplete files remain.")
    if not family_dir.exists():
        return MiniMaxH3ModelStatus(root, family, False, f"{family} checkpoint folder is missing.")
    if not (family_dir / "model_index.json").exists():
        return MiniMaxH3ModelStatus(root, family, False, f"{family}/model_index.json is missing.")
    missing = [name for name in _EXPECTED_COMPONENTS if not (family_dir / name).exists()]
    if not any((family_dir / name).exists() for name in _VIDEO_VAE_COMPONENTS):
        missing.append("visual_vae or video_vae")
    if missing:
        names = ", ".join(missing[:3])
        suffix = "..." if len(missing) > 3 else ""
        return MiniMaxH3ModelStatus(root, family, False, f"MiniMax H3 {family} download is incomplete; missing {names}{suffix}.")
    if not (root / "model_index.json").exists():
        return MiniMaxH3ModelStatus(root, family, True, f"MiniMax H3 {family} checkpoint is ready; using {family}/model_index.json.")
    return MiniMaxH3ModelStatus(root, family, True, f"MiniMax H3 {family} checkpoint is ready.")


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


def _connected_value(node_item, ports: set[str], param_names: tuple[str, ...] = ()) -> str:
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return ""
    ports = {str(port or "").strip().lower() for port in ports if str(port or "").strip()}
    edges = _ordered_in_edges(node_item)
    named = [edge for edge in edges if _edge_port(edge) in ports]
    if ports and named:
        edges = named
    elif ports:
        return ""
    for edge in edges:
        src = getattr(edge, "src", None)
        model = getattr(src, "model", None)
        for param_name in param_names:
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


def _host_is_local_or_private(host: str) -> bool:
    clean = str(host or "").strip().strip("[]").lower()
    if not clean:
        return False
    if clean in _LOCAL_HOSTS or clean.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(clean)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except Exception:
        return False


def _is_local_runtime_url(raw: str) -> bool:
    parsed = url_parse.urlparse(str(raw or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    return _host_is_local_or_private(parsed.hostname or "")


def _join_runtime_url(base: str, path: str) -> str:
    path = str(path or "").strip()
    if path and url_parse.urlparse(path).scheme:
        if not _is_local_runtime_url(path):
            raise RuntimeError("MiniMax H3 local node only accepts localhost or private-network runtime URLs.")
        return path
    clean_base = str(base or "").strip()
    if not clean_base:
        raise RuntimeError("Enter a local MiniMax H3 runtime URL.")
    if not url_parse.urlparse(clean_base).scheme:
        clean_base = "http://" + clean_base
    if not _is_local_runtime_url(clean_base):
        raise RuntimeError("MiniMax H3 local node only accepts localhost or private-network runtime URLs.")
    parsed = url_parse.urlparse(clean_base)
    if parsed.path and parsed.path != "/" and path in {"", "/generate"}:
        return clean_base
    if parsed.path and parsed.path != "/" and not path:
        return clean_base
    suffix = path or "/generate"
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return clean_base.rstrip("/") + suffix


def _path_with_task(path: str, task_id: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    if "{task_id}" in text:
        return text.replace("{task_id}", url_parse.quote(str(task_id), safe=""))
    return text.rstrip("/") + "/" + url_parse.quote(str(task_id), safe="")


def _http_json(url: str, payload: dict[str, Any] | None = None, *, timeout: int = 60) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = url_request.Request(url, data=data, headers=headers, method=method)
    try:
        with url_request.urlopen(req, timeout=max(5, int(timeout))) as response:
            raw = response.read()
    except url_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"Local H3 runtime returned HTTP {exc.code}: {detail[:600]}") from exc
    except url_error.URLError as exc:
        raise RuntimeError(f"Could not reach local H3 runtime at {url}: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        preview = raw[:400].decode("utf-8", errors="replace")
        raise RuntimeError(f"Local H3 runtime did not return JSON: {preview}") from exc


def _data_url_for_file(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    data = path.read_bytes()
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _payload(settings: "MiniMaxH3JobSettings") -> dict[str, Any]:
    model_root, family_dir, family = _model_root_and_family(Path(settings.model_path).expanduser(), settings.variant)
    payload: dict[str, Any] = {
        "model": "MiniMax-H3",
        "model_path": str(model_root),
        "checkpoint_path": str(family_dir),
        "checkpoint_family": family,
        "model_variant": _normalize_variant(settings.variant),
        "task": "fl2va" if settings.first_image or settings.last_image else "t2va",
        "prompt": settings.prompt,
        "duration": int(settings.duration),
        "ratio": settings.ratio,
        "resolution": "768p",
        "fps": 24,
    }
    if settings.first_image:
        first = Path(settings.first_image).expanduser()
        payload["first_image_path"] = str(first)
        payload["first_frame_image"] = _data_url_for_file(first)
    if settings.last_image:
        last = Path(settings.last_image).expanduser()
        payload["last_image_path"] = str(last)
        payload["last_frame_image"] = _data_url_for_file(last)
    return payload


def _walk_preferred(obj: Any):
    preferred = (
        "mp4_path",
        "video_path",
        "output_path",
        "local_path",
        "download_url",
        "file_url",
        "video_url",
        "url",
        "path",
        "video",
        "output",
        "result",
        "data",
        "content",
        "task",
    )
    if isinstance(obj, dict):
        used = set()
        for key in preferred:
            if key in obj:
                used.add(key)
                yield key, obj[key]
        for key, value in obj.items():
            if key not in used:
                yield key, value
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            yield str(idx), value


def _looks_like_video_ref(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lower = text.lower()
    if lower.startswith("data:video/"):
        return True
    if lower.startswith(("http://", "https://")):
        path = url_parse.urlparse(text).path.lower()
        return Path(path).suffix in _VIDEO_EXTS or "video" in path or "download" in path
    path = Path(_clean_path_text(text))
    if path.suffix.lower() in _VIDEO_EXTS:
        return True
    try:
        return path.exists() and path.is_file()
    except Exception:
        return False


def _extract_video_ref(obj: Any) -> Any:
    if isinstance(obj, str):
        return obj if _looks_like_video_ref(obj) else None
    if isinstance(obj, dict):
        for key, value in _walk_preferred(obj):
            if str(key).lower() in {"task_id", "id", "file_id", "status"}:
                continue
            found = _extract_video_ref(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for _key, value in _walk_preferred(obj):
            found = _extract_video_ref(value)
            if found is not None:
                return found
    return None


def _extract_first_string(obj: Any, keys) -> str:
    ordered_keys = tuple(keys)
    if isinstance(obj, dict):
        for key in ordered_keys:
            value = obj.get(key)
            if isinstance(value, (str, int)):
                text = str(value).strip()
                if text:
                    return text
        for _key, value in _walk_preferred(obj):
            text = _extract_first_string(value, ordered_keys)
            if text:
                return text
    elif isinstance(obj, list):
        for _key, value in _walk_preferred(obj):
            text = _extract_first_string(value, ordered_keys)
            if text:
                return text
    return ""


def _extract_status(obj: Any) -> str:
    return _extract_first_string(obj, ("status", "state", "message", "detail"))


def _write_data_url(ref: str, target: Path) -> Path:
    _header, _sep, encoded = ref.partition(",")
    if not encoded:
        raise RuntimeError("Video data URL did not contain base64 data.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(encoded, validate=False))
    return target


def _write_base64_video(ref: str, target: Path) -> Path | None:
    text = re.sub(r"\s+", "", str(ref or ""))
    if len(text) < 256:
        return None
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _copy_local_video(ref: str, target: Path, output_dir: Path) -> Path | None:
    text = _clean_path_text(ref)
    candidates = [Path(text).expanduser()]
    if not candidates[0].is_absolute():
        candidates.append(output_dir / text)
    for candidate in candidates:
        try:
            if not candidate.exists() or not candidate.is_file():
                continue
            suffix = candidate.suffix.lower() if candidate.suffix.lower() in _VIDEO_EXTS else ".mp4"
            actual_target = target.with_suffix(suffix)
            if candidate.resolve() == actual_target.resolve():
                return candidate
            actual_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, actual_target)
            return actual_target
        except Exception:
            continue
    return None


def _download_local_video(ref: str, target: Path) -> Path:
    if not _is_local_runtime_url(ref):
        raise RuntimeError("Refusing to download generated video from a non-local URL.")
    req = url_request.Request(ref, headers={"User-Agent": "QubitMCP MiniMax H3 local node"})
    target.parent.mkdir(parents=True, exist_ok=True)
    with url_request.urlopen(req, timeout=600) as response:
        with target.open("wb") as fh:
            shutil.copyfileobj(response, fh)
    return target


def _materialize_video(ref: Any, output_dir: Path, stem: str) -> Path:
    if isinstance(ref, dict):
        nested = _extract_video_ref(ref)
        if nested is None:
            raise RuntimeError("Local H3 runtime response did not include a video result.")
        ref = nested
    if not isinstance(ref, str):
        raise RuntimeError("Local H3 runtime returned an unsupported video result.")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = output_dir / f"{_safe_stem(stem)}_{stamp}.mp4"
    text = ref.strip()
    if text.lower().startswith("data:video/"):
        return _write_data_url(text, target)
    if text.lower().startswith(("http://", "https://")):
        return _download_local_video(text, target)
    copied = _copy_local_video(text, target, output_dir)
    if copied is not None:
        return copied
    decoded = _write_base64_video(text, target)
    if decoded is not None:
        return decoded
    raise RuntimeError("Local H3 runtime returned a video reference that could not be resolved.")


@dataclass
class MiniMaxH3JobSettings:
    prompt: str
    first_image: str
    last_image: str
    model_path: Path
    variant: str
    local_url: str
    create_path: str
    query_path: str
    retrieve_path: str
    duration: int
    ratio: str
    output_dir: Path
    poll_interval: float
    timeout: int
    node_name: str = "minimax_h3"


@dataclass
class MiniMaxH3JobResult:
    ok: bool
    mp4_path: str = ""
    task_id: str = ""
    status: str = ""
    error: str = ""
    details: str = ""


def _validate_image_path(raw: str, node_item=None) -> str:
    text = _clean_path_text(raw)
    if not text:
        return ""
    path = _resolve_path(node_item, text) if node_item is not None else Path(text).expanduser()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Image path does not exist: {path}")
    if path.suffix.lower() not in _IMAGE_EXTS:
        raise ValueError(f"Unsupported image extension '{path.suffix}'.")
    return str(path)


def run_minimax_h3_job(settings: MiniMaxH3JobSettings, progress=None) -> MiniMaxH3JobResult:
    def emit(text: str) -> None:
        if progress is not None:
            try:
                progress(str(text))
            except Exception:
                pass

    prompt = settings.prompt.strip()
    if not prompt:
        raise ValueError("Enter a prompt for MiniMax H3.")
    status = minimax_h3_model_status(settings.model_path, settings.variant)
    if not status.ready:
        raise RuntimeError(f"{status.detail} Run setup.bat minimax_h3 to download the local Hugging Face checkpoint.")

    emit("Preparing local H3 request...")
    payload = _payload(settings)
    create_url = _join_runtime_url(settings.local_url, settings.create_path)
    response = _http_json(create_url, payload, timeout=min(max(15, settings.timeout), 300))
    task_id = _extract_first_string(response, ("task_id", "id"))
    video_ref = _extract_video_ref(response)
    if video_ref is not None:
        path = _materialize_video(video_ref, settings.output_dir, settings.node_name)
        return MiniMaxH3JobResult(True, str(path), task_id, f"Generated {path.name}.")

    if not task_id:
        status_text = _extract_status(response)
        raise RuntimeError(status_text or "Local H3 runtime accepted the request but did not return a task id or video.")

    emit(f"Local H3 task {task_id} is running...")
    deadline = time.monotonic() + max(30, int(settings.timeout))
    last_response: dict[str, Any] = response
    while time.monotonic() < deadline:
        time.sleep(max(0.5, float(settings.poll_interval)))
        query_path = _path_with_task(settings.query_path, task_id)
        if not query_path:
            break
        query_url = _join_runtime_url(settings.local_url, query_path)
        try:
            last_response = _http_json(query_url, None, timeout=60)
        except RuntimeError:
            last_response = _http_json(query_url, {"task_id": task_id}, timeout=60)
        status_text = _extract_status(last_response) or "running"
        emit(f"Local H3 task {task_id}: {status_text}")
        video_ref = _extract_video_ref(last_response)
        if video_ref is not None:
            path = _materialize_video(video_ref, settings.output_dir, settings.node_name)
            return MiniMaxH3JobResult(True, str(path), task_id, f"Generated {path.name}.")
        if status_text.lower() in {"failed", "error", "cancelled", "canceled"}:
            raise RuntimeError(f"Local H3 task failed: {json.dumps(last_response, ensure_ascii=True)[:1000]}")

    file_id = _extract_first_string(last_response, ("file_id", "video_file_id"))
    if file_id and settings.retrieve_path:
        retrieve_path = _path_with_task(settings.retrieve_path.replace("{file_id}", url_parse.quote(file_id, safe="")), task_id)
        retrieve_url = _join_runtime_url(settings.local_url, retrieve_path)
        retrieved = _http_json(retrieve_url, None, timeout=120)
        video_ref = _extract_video_ref(retrieved)
        if video_ref is not None:
            path = _materialize_video(video_ref, settings.output_dir, settings.node_name)
            return MiniMaxH3JobResult(True, str(path), task_id, f"Generated {path.name}.")

    raise RuntimeError(f"Local H3 task timed out before returning a video. Task id: {task_id}")


class MiniMaxH3GenerationThread(QtCore.QThread):
    progressChanged = QtCore.Signal(str)
    resultReady = QtCore.Signal(object)

    def __init__(self, settings: MiniMaxH3JobSettings, parent=None):
        super().__init__(parent)
        self._settings = settings

    def run(self):
        try:
            result = run_minimax_h3_job(self._settings, self.progressChanged.emit)
        except Exception as exc:
            result = MiniMaxH3JobResult(False, task_id="", status="MiniMax H3 generation failed.", error=str(exc))
        self.resultReady.emit(result)


def _button_style(primary: bool = False) -> str:
    if primary:
        return (
            "QPushButton{background:#2563eb;color:#f8fafc;border:1px solid #60a5fa;"
            "border-radius:4px;padding:5px 10px;font-size:11px;font-weight:600;}"
            "QPushButton:hover{background:#3b82f6;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#475569;}"
        )
    return (
        "QPushButton{background:#16202f;color:#e2e8f0;border:1px solid #3b4b63;"
        "border-radius:4px;padding:5px 8px;font-size:11px;}"
        "QPushButton:hover{background:#1e2b3d;}"
        "QPushButton:disabled{color:#64748b;background:#111827;border-color:#243247;}"
    )


def _field_style() -> str:
    return (
        "QWidget#MiniMaxH3VideoWidget{background:#0f1216;border:1px solid #334155;border-radius:0px;}"
        "QLabel{color:#cbd5e1;font-size:11px;}"
        "QLineEdit,QComboBox,QSpinBox{background:#0f172a;color:#e2e8f0;"
        "border:1px solid #334155;border-radius:4px;padding:3px 5px;font-size:11px;}"
        "QPlainTextEdit{background:#0f172a;color:#e2e8f0;border:1px solid #334155;"
        "border-radius:4px;padding:4px 6px;font-size:11px;}"
        "QToolButton{background:#16202f;color:#e2e8f0;border:1px solid #3b4b63;border-radius:4px;}"
        "QToolButton:hover{background:#1e2b3d;border-color:#7dd3fc;}"
        "QLineEdit:disabled,QPlainTextEdit:disabled{background:#111827;color:#64748b;border-color:#334155;}"
    )


class MiniMaxH3VideoWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._thread: MiniMaxH3GenerationThread | None = None
        self._setup_proc: QtCore.QProcess | None = None
        self._setup_tail = ""
        self._syncing = False
        self._scene_connected = False
        self._connected_prompt = ""
        self._connected_first = ""
        self._connected_last = ""

        build_ports(node_item)
        self.setObjectName("MiniMaxH3VideoWidget")
        self.setMinimumSize(MINIMAX_H3_VIDEO_BODY_W, MINIMAX_H3_VIDEO_BODY_H)
        self.setStyleSheet(_field_style())

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._prompt_edit = QtWidgets.QPlainTextEdit()
        self._prompt_edit.setPlaceholderText("Prompt")
        self._prompt_edit.setFixedHeight(78)
        self._prompt_edit.textChanged.connect(self._commit_controls)
        root.addLayout(self._row("Prompt", self._prompt_edit), 0)

        self._first_edit = QtWidgets.QLineEdit()
        self._first_edit.setPlaceholderText("Optional first frame")
        self._first_edit.editingFinished.connect(self._commit_controls)
        self._first_btn = self._tool_button("Browse first frame")
        self._first_btn.clicked.connect(lambda: self._browse_image(self._first_edit, "Choose First Frame"))
        first_row = self._row("First", self._first_edit)
        first_row.addWidget(self._first_btn, 0)
        root.addLayout(first_row, 0)

        self._last_edit = QtWidgets.QLineEdit()
        self._last_edit.setPlaceholderText("Optional last frame")
        self._last_edit.editingFinished.connect(self._commit_controls)
        self._last_btn = self._tool_button("Browse last frame")
        self._last_btn.clicked.connect(lambda: self._browse_image(self._last_edit, "Choose Last Frame"))
        last_row = self._row("Last", self._last_edit)
        last_row.addWidget(self._last_btn, 0)
        root.addLayout(last_row, 0)

        self._model_path_edit = QtWidgets.QLineEdit()
        self._model_path_edit.editingFinished.connect(self._commit_controls)
        self._model_btn = self._tool_button("Choose model folder")
        self._model_btn.clicked.connect(self._browse_model_dir)
        self._setup_btn = QtWidgets.QPushButton("Setup Model")
        self._setup_btn.setStyleSheet(_button_style())
        self._setup_btn.clicked.connect(self._on_setup_model)
        model_row = self._row("Model", self._model_path_edit)
        model_row.addWidget(self._model_btn, 0)
        model_row.addWidget(self._setup_btn, 0)
        root.addLayout(model_row, 0)

        self._runtime_edit = QtWidgets.QLineEdit()
        self._runtime_edit.setPlaceholderText("http://127.0.0.1:30010")
        self._runtime_edit.editingFinished.connect(self._commit_controls)
        root.addLayout(self._row("Runtime", self._runtime_edit), 0)

        settings_row = QtWidgets.QHBoxLayout()
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.setSpacing(6)
        settings_row.addWidget(self._small_label("Variant"), 0)
        self._variant_combo = QtWidgets.QComboBox()
        self._variant_combo.addItem("FL2VA", "fl2va")
        self._variant_combo.addItem("Ref2VA", "ref2va")
        self._variant_combo.currentIndexChanged.connect(self._commit_controls)
        settings_row.addWidget(self._variant_combo, 1)
        settings_row.addWidget(self._small_label("Duration"), 0)
        self._duration_spin = QtWidgets.QSpinBox()
        self._duration_spin.setRange(4, 15)
        self._duration_spin.setSuffix(" s")
        self._duration_spin.valueChanged.connect(self._commit_controls)
        settings_row.addWidget(self._duration_spin, 0)
        settings_row.addWidget(self._small_label("Ratio"), 0)
        self._ratio_combo = QtWidgets.QComboBox()
        for label in ("adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"):
            self._ratio_combo.addItem(label, label)
        self._ratio_combo.currentIndexChanged.connect(self._commit_controls)
        settings_row.addWidget(self._ratio_combo, 1)
        root.addLayout(settings_row, 0)

        self._output_dir_edit = QtWidgets.QLineEdit()
        self._output_dir_edit.editingFinished.connect(self._commit_controls)
        self._output_btn = self._tool_button("Choose output folder")
        self._output_btn.clicked.connect(self._browse_output_dir)
        output_row = self._row("Output", self._output_dir_edit)
        output_row.addWidget(self._output_btn, 0)
        root.addLayout(output_row, 0)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)
        self._generate_btn = QtWidgets.QPushButton("Generate")
        self._generate_btn.setStyleSheet(_button_style(primary=True))
        self._generate_btn.clicked.connect(self._on_generate)
        self._open_btn = QtWidgets.QPushButton("Open")
        self._open_btn.setStyleSheet(_button_style())
        self._open_btn.clicked.connect(self._open_output)
        self._copy_btn = QtWidgets.QPushButton("Copy")
        self._copy_btn.setStyleSheet(_button_style())
        self._copy_btn.clicked.connect(self._copy_output_path)
        button_row.addWidget(self._generate_btn, 1)
        button_row.addWidget(self._open_btn, 0)
        button_row.addWidget(self._copy_btn, 0)
        root.addLayout(button_row, 0)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(42)
        self._status.setMaximumHeight(56)
        self._status.setStyleSheet(
            "QLabel{background:#09111f;border:1px solid #1e293b;border-radius:4px;"
            "padding:4px 6px;color:#94a3b8;font-size:11px;}"
        )
        root.addWidget(self._status, 0)

        self._mp4_out = QtWidgets.QLineEdit()
        self._mp4_out.setReadOnly(True)
        root.addLayout(self._row("MP4", self._mp4_out), 0)

        self._refresh_from_params()
        self._schedule_scene_sync()

    def sizeHint(self):
        return QtCore.QSize(MINIMAX_H3_VIDEO_BODY_W, MINIMAX_H3_VIDEO_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(MINIMAX_H3_VIDEO_BODY_W, MINIMAX_H3_VIDEO_BODY_H)

    def _model(self):
        return getattr(self._node_item, "model", None)

    def _row(self, label: str, widget: QtWidgets.QWidget) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        lab = QtWidgets.QLabel(label)
        lab.setMinimumWidth(50)
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
        prompt = _connected_value(self._node_item, {"prompt"}, ())
        first = _connected_value(
            self._node_item,
            {"first_image", "first frame", "first"},
            ("path", "source_image", "image_url", "output", "preview_image"),
        )
        last = _connected_value(
            self._node_item,
            {"last_image", "last frame", "last"},
            ("path", "source_image", "image_url", "output", "preview_image"),
        )
        changed = (prompt != self._connected_prompt) or (first != self._connected_first) or (last != self._connected_last)
        self._connected_prompt = prompt
        self._connected_first = first
        self._connected_last = last
        self._syncing = True
        try:
            self._prompt_edit.setEnabled(not bool(prompt))
            self._first_edit.setEnabled(not bool(first))
            self._first_btn.setEnabled(not bool(first))
            self._last_edit.setEnabled(not bool(last))
            self._last_btn.setEnabled(not bool(last))
            if prompt:
                self._prompt_edit.setPlainText(prompt)
            if first:
                self._first_edit.setText(first)
            if last:
                self._last_edit.setText(last)
        finally:
            self._syncing = False
        if changed:
            self._refresh_model_status()

    def _refresh_from_params(self) -> None:
        model = self._model()
        self._syncing = True
        try:
            self._prompt_edit.setPlainText(_param_value(model, _PARAM_PROMPT, ""))
            self._first_edit.setText(_param_value(model, _PARAM_FIRST_IMAGE, ""))
            self._last_edit.setText(_param_value(model, _PARAM_LAST_IMAGE, ""))
            self._model_path_edit.setText(_param_value(model, _PARAM_MODEL_PATH, str(minimax_h3_model_dir())))
            self._runtime_edit.setText(_param_value(model, _PARAM_LOCAL_URL, _default_local_url()))
            variant = _normalize_variant(_param_value(model, _PARAM_VARIANT, "fl2va"))
            idx = self._variant_combo.findData(variant)
            self._variant_combo.setCurrentIndex(max(0, idx))
            try:
                self._duration_spin.setValue(max(4, min(15, int(_param_value(model, _PARAM_DURATION, "8") or "8"))))
            except Exception:
                self._duration_spin.setValue(8)
            ratio = _param_value(model, _PARAM_RATIO, "adaptive") or "adaptive"
            ratio_idx = self._ratio_combo.findData(ratio)
            self._ratio_combo.setCurrentIndex(max(0, ratio_idx))
            output_dir = _param_value(model, _PARAM_OUTPUT_DIR, "")
            self._output_dir_edit.setText(output_dir or str(_default_output_dir(self._node_item)))
            self._mp4_out.setText(_param_value(model, MP4_OUTPUT_PARAM, ""))
            self._status.setText(_param_value(model, STATUS_OUTPUT_PARAM, "") or "Ready.")
        finally:
            self._syncing = False
        self._refresh_connected_inputs()
        self._refresh_model_status()

    def _commit_controls(self) -> None:
        if self._syncing:
            return
        if not self._connected_prompt:
            _set_param_value(self._node_item, _PARAM_PROMPT, self._prompt_edit.toPlainText().strip(), notify_scene=False)
        if not self._connected_first:
            _set_param_value(self._node_item, _PARAM_FIRST_IMAGE, self._first_edit.text().strip(), notify_scene=False)
        if not self._connected_last:
            _set_param_value(self._node_item, _PARAM_LAST_IMAGE, self._last_edit.text().strip(), notify_scene=False)
        default_model = str(minimax_h3_model_dir())
        model_path = self._model_path_edit.text().strip() or default_model
        _set_param_value(self._node_item, _PARAM_MODEL_PATH, "" if model_path == default_model else model_path, notify_scene=False)
        _set_param_value(self._node_item, _PARAM_VARIANT, str(self._variant_combo.currentData() or "fl2va"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_LOCAL_URL, self._runtime_edit.text().strip() or _default_local_url(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_DURATION, str(int(self._duration_spin.value())), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_RATIO, str(self._ratio_combo.currentData() or "adaptive"), notify_scene=False)
        default_output = str(_default_output_dir(self._node_item))
        output_dir = self._output_dir_edit.text().strip() or default_output
        _set_param_value(self._node_item, _PARAM_OUTPUT_DIR, "" if output_dir == default_output else output_dir, notify_scene=False)
        self._refresh_model_status()

    def _refresh_model_status(self) -> None:
        if self._thread is not None:
            return
        if self._setup_proc is not None:
            self._status.setStyleSheet(
                "QLabel{background:#081322;border:1px solid #1d4ed8;border-radius:4px;"
                "padding:4px 6px;color:#93c5fd;font-size:11px;}"
            )
            self._status.setText("MiniMax H3 model setup is still running. Wait for the download to finish.")
            self._generate_btn.setEnabled(False)
            self._setup_btn.setEnabled(False)
            return
        model_path = self._model_path_edit.text().strip() or str(minimax_h3_model_dir())
        variant = str(self._variant_combo.currentData() or "fl2va")
        status = minimax_h3_model_status(model_path, variant)
        if status.ready:
            self._generate_btn.setEnabled(True)
            self._setup_btn.setEnabled(True)
            self._status.setStyleSheet(
                "QLabel{background:#07140f;border:1px solid #14532d;border-radius:4px;"
                "padding:4px 6px;color:#86efac;font-size:11px;}"
            )
            self._status.setText(status.detail)
        else:
            self._generate_btn.setEnabled(True)
            self._setup_btn.setEnabled(True)
            self._status.setStyleSheet(
                "QLabel{background:#160f08;border:1px solid #7c2d12;border-radius:4px;"
                "padding:4px 6px;color:#fdba74;font-size:11px;}"
            )
            self._status.setText(status.detail)

    def _browse_image(self, edit: QtWidgets.QLineEdit, title: str) -> None:
        parent = _dialog_parent(self._node_item) or self
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            title,
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.exr);;All Files (*.*)",
        )
        if not path:
            return
        edit.setText(path)
        self._commit_controls()

    def _browse_model_dir(self) -> None:
        parent = _dialog_parent(self._node_item) or self
        start = self._model_path_edit.text().strip() or str(minimax_h3_model_dir())
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Choose MiniMax H3 Model Folder", start)
        if not path:
            return
        self._model_path_edit.setText(path)
        self._commit_controls()

    def _browse_output_dir(self) -> None:
        parent = _dialog_parent(self._node_item) or self
        start = self._output_dir_edit.text().strip() or str(_default_output_dir(self._node_item))
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Choose MiniMax H3 Output Folder", start)
        if not path:
            return
        self._output_dir_edit.setText(path)
        self._commit_controls()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        clean = str(text or "").strip()
        self._status.setStyleSheet(
            (
                "QLabel{background:#160b0b;border:1px solid #7f1d1d;border-radius:4px;"
                "padding:4px 6px;color:#fca5a5;font-size:11px;}"
            )
            if error
            else (
                "QLabel{background:#09111f;border:1px solid #1e293b;border-radius:4px;"
                "padding:4px 6px;color:#94a3b8;font-size:11px;}"
            )
        )
        self._status.setText(clean)
        _set_param_value(self._node_item, STATUS_OUTPUT_PARAM, clean, notify_scene=False)

    def _set_busy(self, busy: bool) -> None:
        self._generate_btn.setEnabled(not busy)
        self._setup_btn.setEnabled(not busy and self._setup_proc is None)
        self._model_path_edit.setEnabled(not busy)
        self._model_btn.setEnabled(not busy)
        self._runtime_edit.setEnabled(not busy)
        self._variant_combo.setEnabled(not busy)
        self._duration_spin.setEnabled(not busy)
        self._ratio_combo.setEnabled(not busy)
        self._output_dir_edit.setEnabled(not busy)
        self._output_btn.setEnabled(not busy)
        self._generate_btn.setText("Working..." if busy else "Generate")
        try:
            self._node_item.setBusyState(busy, "minimax_h3" if busy else "")
        except Exception:
            pass

    def _collect_settings(self) -> MiniMaxH3JobSettings:
        if self._setup_proc is not None:
            raise RuntimeError("MiniMax H3 model setup is still running. Wait for it to finish before generating.")
        self._commit_controls()
        prompt = self._connected_prompt or self._prompt_edit.toPlainText().strip()
        first = _validate_image_path(self._connected_first or self._first_edit.text().strip(), self._node_item)
        last = _validate_image_path(self._connected_last or self._last_edit.text().strip(), self._node_item)
        model_path = Path(self._model_path_edit.text().strip() or str(minimax_h3_model_dir())).expanduser()
        output_dir = _output_dir(self._node_item, self._output_dir_edit.text().strip())
        model = self._model()
        node_name = _safe_stem(str(getattr(model, "name", "") or "minimax_h3"))
        try:
            poll_interval = float(_param_value(model, _PARAM_POLL_INTERVAL, "5") or "5")
        except Exception:
            poll_interval = 5.0
        try:
            timeout = int(float(_param_value(model, _PARAM_TIMEOUT, "1800") or "1800"))
        except Exception:
            timeout = 1800
        model_status = minimax_h3_model_status(model_path, str(self._variant_combo.currentData() or "fl2va"))
        if not model_status.ready:
            raise RuntimeError(f"{model_status.detail} Use Setup Model or run setup.bat minimax_h3.")
        return MiniMaxH3JobSettings(
            prompt=prompt,
            first_image=first,
            last_image=last,
            model_path=model_path,
            variant=str(self._variant_combo.currentData() or "fl2va"),
            local_url=self._runtime_edit.text().strip() or _default_local_url(),
            create_path=_param_value(model, _PARAM_CREATE_PATH, "/generate") or "/generate",
            query_path=_param_value(model, _PARAM_QUERY_PATH, "/status/{task_id}") or "/status/{task_id}",
            retrieve_path=_param_value(model, _PARAM_RETRIEVE_PATH, "/files/{file_id}") or "/files/{file_id}",
            duration=int(self._duration_spin.value()),
            ratio=str(self._ratio_combo.currentData() or "adaptive"),
            output_dir=output_dir,
            poll_interval=poll_interval,
            timeout=timeout,
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
        self._set_busy(True)
        self._set_status("Starting local MiniMax H3 generation...")
        self._thread = MiniMaxH3GenerationThread(settings, self)
        self._thread.progressChanged.connect(lambda text: self._set_status(text))
        self._thread.resultReady.connect(self._on_generation_result)
        self._thread.finished.connect(self._on_generation_finished)
        self._thread.start()

    def _on_generation_finished(self) -> None:
        if self._thread is not None:
            try:
                self._thread.deleteLater()
            except Exception:
                pass
        self._thread = None
        self._set_busy(False)

    def _on_generation_result(self, result_obj) -> None:
        result = result_obj if isinstance(result_obj, MiniMaxH3JobResult) else MiniMaxH3JobResult(False, error=str(result_obj))
        _set_param_value(self._node_item, MP4_OUTPUT_PARAM, result.mp4_path, notify_scene=False)
        _set_param_value(self._node_item, TASK_OUTPUT_PARAM, result.task_id, notify_scene=False)
        final_status = result.status or ("Done." if result.ok else "MiniMax H3 generation failed.")
        if result.error and not result.ok:
            final_status = f"{final_status} {result.error}".strip()
        _set_param_value(self._node_item, STATUS_OUTPUT_PARAM, final_status, notify_scene=True)
        try:
            model = self._model()
            if model is not None:
                model.info = result.mp4_path or final_status
        except Exception:
            pass
        self._mp4_out.setText(result.mp4_path)
        self._set_status(final_status, error=not result.ok)

    def _on_setup_model(self) -> None:
        if self._setup_proc is not None:
            return
        script = minimax_h3_setup_script()
        if not script.exists():
            self._set_status(f"setup.bat was not found: {script}", error=True)
            return
        self._commit_controls()
        self._setup_tail = ""
        proc = QtCore.QProcess(self)
        self._setup_proc = proc
        self._setup_btn.setEnabled(False)
        self._generate_btn.setEnabled(False)
        self._set_status("Downloading MiniMax H3 with setup.bat...")

        def append_output() -> None:
            chunks = []
            try:
                out = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
                err = bytes(proc.readAllStandardError()).decode("utf-8", errors="replace")
                if out:
                    chunks.append(out)
                if err:
                    chunks.append(err)
            except Exception:
                return
            if not chunks:
                return
            self._setup_tail = (self._setup_tail + "\n" + "".join(chunks).strip())[-12000:]
            lines = [line.strip() for line in self._setup_tail.splitlines() if line.strip()]
            if lines:
                self._set_status(lines[-1][-220:])

        def finished(exit_code: int, _exit_status) -> None:
            append_output()
            self._setup_proc = None
            self._setup_btn.setEnabled(True)
            self._generate_btn.setEnabled(True)
            if int(exit_code) != 0:
                lines = [line.strip() for line in self._setup_tail.splitlines() if line.strip()]
                detail = lines[-1] if lines else f"exit code {exit_code}"
                self._set_status(f"MiniMax H3 setup failed: {detail}", error=True)
                try:
                    proc.deleteLater()
                except Exception:
                    pass
                return
            self._model_path_edit.setText(str(minimax_h3_model_dir()))
            self._commit_controls()
            self._refresh_model_status()
            try:
                proc.deleteLater()
            except Exception:
                pass

        proc.setWorkingDirectory(str(_repo_root()))
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("QUBITMCP_HOME", str(_app_home_dir()))
        env.insert("QUBITFIELD_HOME", str(_app_home_dir()))
        variant = str(self._variant_combo.currentData() or "fl2va")
        env.insert("QUBITMCP_MINIMAX_H3_VARIANT", "Ref2VA" if variant == "ref2va" else "FL2VA")
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(append_output)
        proc.readyReadStandardError.connect(append_output)
        proc.finished.connect(finished)
        proc.start("cmd.exe", ["/c", str(script), "minimax_h3"])
        if not proc.waitForStarted(5000):
            self._setup_proc = None
            self._setup_btn.setEnabled(True)
            self._generate_btn.setEnabled(True)
            self._set_status("Could not start setup.bat minimax_h3.", error=True)

    def _open_output(self) -> None:
        raw = self._mp4_out.text().strip() or _param_value(self._model(), MP4_OUTPUT_PARAM, "")
        try:
            if raw and Path(raw).expanduser().exists():
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(raw).expanduser())))
                return
            out = _output_dir(self._node_item, self._output_dir_edit.text().strip())
            out.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(out)))
        except Exception as exc:
            self._set_status(str(exc), error=True)

    def _copy_output_path(self) -> None:
        raw = self._mp4_out.text().strip() or _param_value(self._model(), MP4_OUTPUT_PARAM, "")
        try:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(raw)
            self._set_status("Copied MP4 path." if raw else "No MP4 path to copy.", error=not bool(raw))
        except Exception as exc:
            self._set_status(str(exc), error=True)


def _dialog_parent(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
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
        _PARAM_PROMPT: "",
        _PARAM_FIRST_IMAGE: "",
        _PARAM_LAST_IMAGE: "",
        _PARAM_MODEL_PATH: str(minimax_h3_model_dir()),
        _PARAM_VARIANT: "fl2va",
        _PARAM_LOCAL_URL: _default_local_url(),
        _PARAM_CREATE_PATH: "/generate",
        _PARAM_QUERY_PATH: "/status/{task_id}",
        _PARAM_RETRIEVE_PATH: "/files/{file_id}",
        _PARAM_DURATION: "8",
        _PARAM_RATIO: "adaptive",
        _PARAM_RESOLUTION: "768p",
        _PARAM_OUTPUT_DIR: "",
        _PARAM_POLL_INTERVAL: "5",
        _PARAM_TIMEOUT: "1800",
    }
    for name, default in defaults.items():
        _ensure_param(node_item, name, default)
    for name in OUTPUT_PARAMS:
        _ensure_param(node_item, name, "")
    _ensure_hidden_params(getattr(node_item, "model", None), [*defaults.keys(), *OUTPUT_PARAMS])
    try:
        setattr(node_item, "_default_named_input", "prompt")
        setattr(node_item, "_show_default_input_with_named", True)
        node_item.ensure_input("prompt")
        node_item.ensure_input("first_image")
        node_item.ensure_input("last_image")
    except Exception:
        pass
    try:
        for name in OUTPUT_PARAMS:
            node_item.ensure_output(name)
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
    inputs = {
        "prompt": 34,
        "first_image": 122,
        "last_image": 152,
    }
    for name, offset in inputs.items():
        try:
            node_item._input_port_pos[name.lower()] = (QtCore.QPointF(0.0, float(y_cursor + offset)), name)
        except Exception:
            pass
    outputs = {
        MP4_OUTPUT_PARAM: 418,
        TASK_OUTPUT_PARAM: 438,
        STATUS_OUTPUT_PARAM: 398,
    }
    for name, offset in outputs.items():
        try:
            node_item._output_port_pos[name.lower()] = (QtCore.QPointF(float(node_item.width), float(y_cursor + offset)), name)
        except Exception:
            pass


def render_node_body(node_item, y_cursor: int) -> int:
    build_ports(node_item)
    body = MiniMaxH3VideoWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    w = max(int(hint.width()), int(getattr(node_item, "width", MINIMAX_H3_VIDEO_BODY_W) or MINIMAX_H3_VIDEO_BODY_W))
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


MINIMAX_H3_VIDEO_SPEC = Spec(
    stripe_color="#4f46e5",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "MINIMAX_H3_VIDEO_NODE_ALIASES",
    "MINIMAX_H3_VIDEO_NODE_KIND",
    "MINIMAX_H3_VIDEO_NODE_KINDS",
    "MINIMAX_H3_VIDEO_BODY_H",
    "MINIMAX_H3_VIDEO_BODY_W",
    "MINIMAX_H3_VIDEO_SPEC",
    "build_ports",
    "minimax_h3_model_dir",
    "minimax_h3_model_status",
    "render_node_body",
    "run_minimax_h3_job",
]
