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


MINIMAX_H3_API_VIDEO_NODE_KIND = "minimax_h3_api_video"
MINIMAX_H3_API_VIDEO_NODE_ALIASES = [
    "minimax h3 api video",
    "minimax h3 api",
    "minimax api video",
    "minimax api",
    "hailuo h3 api video",
    "hailuo api video",
]
MINIMAX_H3_API_VIDEO_NODE_KINDS = {MINIMAX_H3_API_VIDEO_NODE_KIND, *MINIMAX_H3_API_VIDEO_NODE_ALIASES}
MINIMAX_H3_API_VIDEO_BODY_W = 520
MINIMAX_H3_API_VIDEO_BODY_H = 614

MP4_OUTPUT_PARAM = "mp4_path"
TASK_OUTPUT_PARAM = "task_id"
STATUS_OUTPUT_PARAM = "h3_api_status"
OUTPUT_PARAMS = (MP4_OUTPUT_PARAM, TASK_OUTPUT_PARAM, STATUS_OUTPUT_PARAM)

_PARAM_PROMPT = "__minimax_h3_api_prompt"
_PARAM_MODE = "__minimax_h3_api_mode"
_PARAM_FIRST_IMAGE = "__minimax_h3_api_first_image"
_PARAM_LAST_IMAGE = "__minimax_h3_api_last_image"
_PARAM_REFERENCE_IMAGES = "__minimax_h3_api_reference_images"
_PARAM_API_KEY = "__minimax_h3_api_key"
_PARAM_API_BASE_URL = "__minimax_h3_api_base_url"
_PARAM_MODEL = "__minimax_h3_api_model"
_PARAM_CREATE_PATH = "__minimax_h3_api_create_path"
_PARAM_QUERY_PATH = "__minimax_h3_api_query_path"
_PARAM_FILE_PATH = "__minimax_h3_api_file_path"
_PARAM_DURATION = "__minimax_h3_api_duration"
_PARAM_RATIO = "__minimax_h3_api_ratio"
_PARAM_RESOLUTION = "__minimax_h3_api_resolution"
_PARAM_OUTPUT_DIR = "__minimax_h3_api_output_dir"
_PARAM_POLL_INTERVAL = "__minimax_h3_api_poll_interval"
_PARAM_TIMEOUT = "__minimax_h3_api_timeout"
_HIDDEN_PARAM = "__ui_hidden_params"

_API_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}
_MAX_REQUEST_BYTES = 64 * 1024 * 1024
_MAX_IMAGE_BYTES = 30 * 1024 * 1024
_SUCCEEDED_STATUSES = {"succeeded", "success", "done", "completed", "complete"}
_FAILED_STATUSES = {"failed", "error", "cancelled", "canceled", "rejected"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_api_base_url() -> str:
    return (
        os.environ.get("QUBITMCP_MINIMAX_API_BASE_URL")
        or os.environ.get("MINIMAX_API_BASE_URL")
        or "https://api.minimax.io"
    ).strip()


def _default_api_key() -> str:
    return (
        os.environ.get("QUBITMCP_MINIMAX_PAYG_API_KEY")
        or os.environ.get("MINIMAX_PAYG_API_KEY")
        or os.environ.get("QUBITMCP_MINIMAX_API_KEY")
        or os.environ.get("MINIMAX_API_KEY")
        or ""
    ).strip()


def _default_output_dir(node_item) -> Path:
    workflow_dir = _workflow_dir_for_node(node_item)
    if workflow_dir is not None:
        return workflow_dir / "videos" / "minimax_h3_api"
    return Path(tempfile.gettempdir()) / "EchoGraph" / "minimax_h3_api"


def _clean_path_text(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _safe_stem(raw: str, fallback: str = "minimax_h3_api") -> str:
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


def _model_image_collection_paths(model) -> list[str]:
    kind = str(getattr(model, "kind", "") or "").strip().lower()
    paths: list[str] = []
    if kind in {"image_collection", "imagecollection"}:
        try:
            state = getattr(model, "_image_collection_state", None) or {}
            paths.extend(_parse_image_ref_list(state.get("paths", [])))
        except Exception:
            pass
    for param_name in ("reference_images", "image_paths", "images", "paths", "output"):
        value = _param_value(model, param_name, "")
        if value:
            paths.extend(_parse_image_ref_list(value))
    return _dedupe_strings(paths)


def _connected_image_refs(node_item, ports: set[str]) -> str:
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
    refs: list[str] = []
    for edge in edges:
        src = getattr(edge, "src", None)
        model = getattr(src, "model", None)
        refs.extend(_model_image_collection_paths(model))
        try:
            text = str(scene.resolve_text_value(src) or "").strip()
        except Exception:
            text = ""
        if text:
            refs.extend(_parse_image_ref_list(text))
    return "\n".join(_dedupe_strings(refs))


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


def _is_safe_api_url(raw: str) -> bool:
    parsed = url_parse.urlparse(str(raw or "").strip())
    scheme = parsed.scheme.lower()
    if scheme == "https":
        return True
    if scheme == "http" and _host_is_local_or_private(parsed.hostname or ""):
        return True
    return False


def _join_api_url(base: str, path: str, default_path: str = "") -> str:
    text = str(path or "").strip() or str(default_path or "").strip()
    if text and url_parse.urlparse(text).scheme:
        if not _is_safe_api_url(text):
            raise RuntimeError("Use an HTTPS MiniMax API URL, or a local/private HTTP proxy for testing.")
        return text
    clean_base = str(base or "").strip() or _default_api_base_url()
    if not url_parse.urlparse(clean_base).scheme:
        clean_base = "https://" + clean_base
    if not _is_safe_api_url(clean_base):
        raise RuntimeError("Use an HTTPS MiniMax API base URL, or a local/private HTTP proxy for testing.")
    suffix = text or "/v2/video_generation"
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return clean_base.rstrip("/") + suffix


def _path_with_task(path: str, task_id: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    quoted = url_parse.quote(str(task_id), safe="")
    if "{task_id}" in text:
        return text.replace("{task_id}", quoted)
    return text.rstrip("/") + "/" + quoted


def _path_with_file(path: str, file_id: str, task_id: str = "") -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    text = text.replace("{file_id}", url_parse.quote(str(file_id), safe=""))
    if task_id:
        text = text.replace("{task_id}", url_parse.quote(str(task_id), safe=""))
    return text


def _error_detail(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace") if raw else ""
    if not text:
        return ""
    try:
        obj = json.loads(text)
    except Exception:
        return text[:600]
    if isinstance(obj, dict):
        err = obj.get("error")
        if isinstance(err, dict):
            msg = str(err.get("message", "") or "").strip()
            typ = str(err.get("type", "") or "").strip()
            if msg and typ:
                return f"{typ}: {msg}"
            if msg:
                return msg
        msg = str(obj.get("message", "") or obj.get("detail", "") or "").strip()
        if msg:
            return msg
    return text[:600]


def _http_json(
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = 60,
    headers: dict[str, str] | None = None,
    label: str = "MiniMax API",
) -> dict[str, Any]:
    data = None
    request_headers = {"Accept": "application/json", "User-Agent": "QubitMCP MiniMax H3 API node"}
    request_headers.update(headers or {})
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
        method = "POST"
    req = url_request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with url_request.urlopen(req, timeout=max(5, int(timeout))) as response:
            raw = response.read()
    except url_error.HTTPError as exc:
        detail = _error_detail(exc.read() if hasattr(exc, "read") else b"")
        raise RuntimeError(f"{label} returned HTTP {exc.code}: {detail or str(exc)}") from exc
    except url_error.URLError as exc:
        raise RuntimeError(f"Could not reach {label} at {url}: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        preview = raw[:400].decode("utf-8", errors="replace")
        raise RuntimeError(f"{label} did not return JSON: {preview}") from exc


def _download_bytes(url: str, target: Path, *, headers: dict[str, str] | None = None, timeout: int = 600) -> Path:
    request_headers = {"User-Agent": "QubitMCP MiniMax H3 API node"}
    request_headers.update(headers or {})
    req = url_request.Request(url, headers=request_headers)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with url_request.urlopen(req, timeout=max(30, int(timeout))) as response:
            with target.open("wb") as fh:
                shutil.copyfileobj(response, fh)
    except url_error.HTTPError as exc:
        detail = _error_detail(exc.read() if hasattr(exc, "read") else b"")
        raise RuntimeError(f"MiniMax video download returned HTTP {exc.code}: {detail or str(exc)}") from exc
    except url_error.URLError as exc:
        raise RuntimeError(f"Could not download MiniMax video from {url}: {exc.reason}") from exc
    return target


def _read_binary_or_json(url: str, target: Path, *, headers: dict[str, str], timeout: int = 600) -> Path | dict[str, Any]:
    request_headers = {"Accept": "application/json,video/mp4,*/*", "User-Agent": "QubitMCP MiniMax H3 API node"}
    request_headers.update(headers or {})
    req = url_request.Request(url, headers=request_headers)
    try:
        with url_request.urlopen(req, timeout=max(30, int(timeout))) as response:
            content_type = str(response.headers.get("Content-Type", "") or "").lower()
            raw = response.read()
    except url_error.HTTPError as exc:
        detail = _error_detail(exc.read() if hasattr(exc, "read") else b"")
        raise RuntimeError(f"MiniMax file download returned HTTP {exc.code}: {detail or str(exc)}") from exc
    if "json" in content_type or raw[:1] in {b"{", b"["}:
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            preview = raw[:400].decode("utf-8", errors="replace")
            raise RuntimeError(f"MiniMax file response was not valid JSON: {preview}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return target


def _data_url_for_file(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    data = path.read_bytes()
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _is_external_image_ref(text: str) -> bool:
    lower = str(text or "").strip().lower()
    return lower.startswith(("http://", "https://", "mm_file://", "data:image/"))


def _image_url_value(ref: str) -> str:
    text = _clean_path_text(ref)
    if _is_external_image_ref(text):
        return text
    return _data_url_for_file(Path(text).expanduser())


def _normalize_mode(raw: str) -> str:
    text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"reference", "reference_to_video", "r2v", "r2va", "ref"}:
        return "reference"
    return "image"


def _normalize_model(raw: str) -> str:
    text = str(raw or "").strip()
    if text == "MiniMax-H3-Max":
        return "MiniMax-H3-Max"
    return "MiniMax-H3"


def _normalize_resolution(raw: str) -> str:
    text = str(raw or "").strip().upper()
    if text == "2K":
        return "2K"
    if text == "480P":
        return "480P"
    return "768P"


def _normalize_ratio(raw: str) -> str:
    text = str(raw or "").strip()
    if text in {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
        return text
    return "adaptive"


def _parse_image_ref_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        values: list[str] = []
        for item in raw:
            values.extend(_parse_image_ref_list(item))
        return _dedupe_strings(values)
    if isinstance(raw, dict):
        for key in ("paths", "images", "image_paths", "reference_images", "refs"):
            if key in raw:
                return _parse_image_ref_list(raw.get(key))
        return []

    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("[") or text.startswith("{"):
        try:
            parsed = json.loads(text)
            values = _parse_image_ref_list(parsed)
            if values:
                return values
        except Exception:
            pass

    lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        if ";" in line:
            lines.extend(part.strip() for part in line.split(";") if part.strip())
        else:
            lines.append(line)
    return _dedupe_strings(lines)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = _clean_path_text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _api_payload(settings: "MiniMaxH3ApiJobSettings") -> dict[str, Any]:
    prompt = settings.prompt.strip()
    mode = _normalize_mode(settings.mode)
    model = _normalize_model(settings.model)
    resolution = _normalize_resolution(settings.resolution)
    duration = int(settings.duration)
    reference_images = _dedupe_strings(list(settings.reference_images or []))
    has_frame_image = bool(settings.first_image or settings.last_image)
    has_reference = mode == "reference" and bool(reference_images)
    if model == "MiniMax-H3-Max" and resolution == "2K":
        raise ValueError("MiniMax-H3-Max does not support 2K. Use 480P or 768P.")
    if model == "MiniMax-H3-Max" and duration < 5:
        raise ValueError("MiniMax-H3-Max supports 5-15 second generations.")
    if mode == "reference" and model != "MiniMax-H3":
        raise ValueError("MiniMax-H3-Max does not support reference-to-video. Use MiniMax-H3.")
    if mode == "reference" and not reference_images:
        raise ValueError("Reference-to-video mode needs at least one reference image.")
    if len(reference_images) > 9:
        raise ValueError("MiniMax H3 supports up to 9 reference images per request.")
    if has_reference and has_frame_image:
        raise ValueError("MiniMax H3 cannot mix reference images with first/last-frame image-to-video.")

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if mode == "image" and settings.first_image:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_url_value(settings.first_image)},
                "role": "first_frame",
            }
        )
    if mode == "image" and settings.last_image:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_url_value(settings.last_image)},
                "role": "last_frame",
            }
        )
    if mode == "reference":
        for ref in reference_images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_url_value(ref)},
                    "role": "reference_image",
                }
            )

    ratio = "adaptive" if has_frame_image else _normalize_ratio(settings.ratio)
    if ratio == "adaptive" and mode == "image" and not has_frame_image:
        ratio = "16:9"
    payload: dict[str, Any] = {
        "model": model,
        "content": content,
        "resolution": resolution,
        "duration": duration,
        "ratio": ratio,
    }
    body_size = len(json.dumps(payload, ensure_ascii=True).encode("utf-8"))
    if body_size > _MAX_REQUEST_BYTES:
        raise ValueError("MiniMax request exceeds the 64 MB body limit. Use public image URLs or mm_file:// references.")
    return payload


def _walk_preferred(obj: Any):
    preferred = (
        "mp4_path",
        "video_path",
        "output_path",
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
    if lower.startswith("mm_file://"):
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


def _materialize_video(ref: Any, output_dir: Path, stem: str) -> Path:
    if isinstance(ref, dict):
        nested = _extract_video_ref(ref)
        if nested is None:
            raise RuntimeError("MiniMax API response did not include a video result.")
        ref = nested
    if not isinstance(ref, str):
        raise RuntimeError("MiniMax API returned an unsupported video result.")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = output_dir / f"{_safe_stem(stem)}_{stamp}.mp4"
    text = ref.strip()
    if text.lower().startswith("data:video/"):
        return _write_data_url(text, target)
    if text.lower().startswith("mm_file://"):
        raise RuntimeError("MiniMax returned an mm_file URI. Configure the File Path endpoint or use a query response that returns content.url.")
    if text.lower().startswith(("http://", "https://")):
        return _download_bytes(text, target)
    copied = _copy_local_video(text, target, output_dir)
    if copied is not None:
        return copied
    decoded = _write_base64_video(text, target)
    if decoded is not None:
        return decoded
    raise RuntimeError("MiniMax API returned a video reference that could not be resolved.")


def _validate_image_ref(raw: str, node_item=None) -> str:
    text = _clean_path_text(raw)
    if not text:
        return ""
    if _is_external_image_ref(text):
        return text
    path = _resolve_path(node_item, text) if node_item is not None else Path(text).expanduser()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Image path does not exist: {path}")
    if path.suffix.lower() not in _API_IMAGE_EXTS:
        raise ValueError("MiniMax API image input supports JPG, PNG, WEBP, HEIC, or HEIF.")
    try:
        if path.stat().st_size > _MAX_IMAGE_BYTES:
            raise ValueError("MiniMax API image input must be 30 MB or smaller. Use a public URL for larger files.")
    except ValueError:
        raise
    except Exception:
        pass
    try:
        size = QtGui.QImageReader(str(path)).size()
        if size.isValid():
            w = int(size.width())
            h = int(size.height())
            if w < 256 or h < 256 or w > 5760 or h > 5760:
                raise ValueError("MiniMax API image dimensions must be between 256 and 5760 pixels.")
            ratio = float(w) / float(max(1, h))
            if ratio < 0.4 or ratio > 2.5:
                raise ValueError("MiniMax API image aspect ratio must be between 0.4 and 2.5.")
    except ValueError:
        raise
    except Exception:
        pass
    return str(path)


def _validate_image_refs(raw: Any, node_item=None) -> list[str]:
    refs = _parse_image_ref_list(raw)
    if len(refs) > 9:
        raise ValueError("MiniMax H3 supports up to 9 reference images per request.")
    return [validated for ref in refs if (validated := _validate_image_ref(ref, node_item))]


@dataclass
class MiniMaxH3ApiJobSettings:
    prompt: str
    mode: str
    first_image: str
    last_image: str
    reference_images: list[str]
    api_key: str
    api_base_url: str
    model: str
    create_path: str
    query_path: str
    file_path: str
    duration: int
    ratio: str
    resolution: str
    output_dir: Path
    poll_interval: float
    timeout: int
    node_name: str = "minimax_h3_api"


@dataclass
class MiniMaxH3ApiJobResult:
    ok: bool
    mp4_path: str = ""
    task_id: str = ""
    status: str = ""
    error: str = ""
    details: str = ""


def run_minimax_h3_api_job(settings: MiniMaxH3ApiJobSettings, progress=None) -> MiniMaxH3ApiJobResult:
    def emit(text: str) -> None:
        if progress is not None:
            try:
                progress(str(text))
            except Exception:
                pass

    prompt = settings.prompt.strip()
    if not prompt:
        raise ValueError("Enter a prompt for MiniMax H3 API.")
    api_key = (settings.api_key or _default_api_key()).strip()
    if not api_key:
        raise ValueError("Enter a MiniMax Pay-as-you-go API key or set QUBITMCP_MINIMAX_PAYG_API_KEY / MINIMAX_PAYG_API_KEY.")

    headers = {"Authorization": f"Bearer {api_key}"}
    emit("Preparing MiniMax H3 API request...")
    payload = _api_payload(settings)
    create_url = _join_api_url(settings.api_base_url, settings.create_path, "/v2/video_generation")
    response = _http_json(create_url, payload, timeout=min(max(15, settings.timeout), 300), headers=headers)

    task_id = _extract_first_string(response, ("task_id", "id"))
    video_ref = _extract_video_ref(response)
    if video_ref is not None:
        path = _materialize_video(video_ref, settings.output_dir, settings.node_name)
        return MiniMaxH3ApiJobResult(True, str(path), task_id, f"Generated {path.name}.")

    if not task_id:
        status_text = _extract_status(response)
        raise RuntimeError(status_text or "MiniMax API accepted the request but did not return a task id or video.")

    emit(f"MiniMax H3 API task {task_id} is running...")
    deadline = time.monotonic() + max(30, int(settings.timeout))
    last_response: dict[str, Any] = response
    while time.monotonic() < deadline:
        time.sleep(max(0.5, float(settings.poll_interval)))
        query_path = _path_with_task(settings.query_path or "/v2/video_generation/{task_id}", task_id)
        query_url = _join_api_url(settings.api_base_url, query_path, "/v2/video_generation/{task_id}")
        try:
            last_response = _http_json(query_url, None, timeout=60, headers=headers)
        except RuntimeError:
            last_response = _http_json(query_url, {"task_id": task_id}, timeout=60, headers=headers)

        status_text = _extract_status(last_response) or "running"
        emit(f"MiniMax H3 API task {task_id}: {status_text}")
        video_ref = _extract_video_ref(last_response)
        if video_ref is not None:
            path = _materialize_video(video_ref, settings.output_dir, settings.node_name)
            return MiniMaxH3ApiJobResult(True, str(path), task_id, f"Generated {path.name}.")

        file_id = _extract_first_string(last_response, ("file_id", "video_file_id", "output_file_id"))
        if file_id and settings.file_path:
            file_path = _path_with_file(settings.file_path, file_id, task_id)
            file_url = _join_api_url(settings.api_base_url, file_path)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = settings.output_dir / f"{_safe_stem(settings.node_name)}_{stamp}.mp4"
            retrieved = _read_binary_or_json(file_url, target, headers=headers, timeout=600)
            if isinstance(retrieved, Path):
                return MiniMaxH3ApiJobResult(True, str(retrieved), task_id, f"Generated {retrieved.name}.")
            video_ref = _extract_video_ref(retrieved)
            if video_ref is not None:
                path = _materialize_video(video_ref, settings.output_dir, settings.node_name)
                return MiniMaxH3ApiJobResult(True, str(path), task_id, f"Generated {path.name}.")

        status_lower = status_text.strip().lower()
        if status_lower in _FAILED_STATUSES:
            raise RuntimeError(f"MiniMax H3 API task failed: {json.dumps(last_response, ensure_ascii=True)[:1000]}")
        if status_lower in _SUCCEEDED_STATUSES and not settings.file_path:
            raise RuntimeError(
                "MiniMax H3 API task succeeded but no video URL was found. Add the File Path endpoint if the query returns only a file_id."
            )

    raise RuntimeError(f"MiniMax H3 API task timed out before returning a video. Task id: {task_id}")


class MiniMaxH3ApiGenerationThread(QtCore.QThread):
    progressChanged = QtCore.Signal(str)
    resultReady = QtCore.Signal(object)

    def __init__(self, settings: MiniMaxH3ApiJobSettings, parent=None):
        super().__init__(parent)
        self._settings = settings

    def run(self):
        try:
            result = run_minimax_h3_api_job(self._settings, self.progressChanged.emit)
        except Exception as exc:
            result = MiniMaxH3ApiJobResult(False, task_id="", status="MiniMax H3 API generation failed.", error=str(exc))
        self.resultReady.emit(result)


def _button_style(primary: bool = False) -> str:
    if primary:
        return (
            "QPushButton{background:#0e7490;color:#f8fafc;border:1px solid #67e8f9;"
            "border-radius:4px;padding:5px 10px;font-size:11px;font-weight:600;}"
            "QPushButton:hover{background:#0891b2;}"
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
        "QWidget#MiniMaxH3ApiVideoWidget{background:#0f1216;border:1px solid #334155;border-radius:0px;}"
        "QLabel{color:#cbd5e1;font-size:11px;}"
        "QLineEdit,QComboBox,QSpinBox{background:#0f172a;color:#e2e8f0;"
        "border:1px solid #334155;border-radius:4px;padding:3px 5px;font-size:11px;}"
        "QPlainTextEdit{background:#0f172a;color:#e2e8f0;border:1px solid #334155;"
        "border-radius:4px;padding:4px 6px;font-size:11px;}"
        "QToolButton{background:#16202f;color:#e2e8f0;border:1px solid #3b4b63;border-radius:4px;}"
        "QToolButton:hover{background:#1e2b3d;border-color:#7dd3fc;}"
        "QLineEdit:disabled,QPlainTextEdit:disabled,QComboBox:disabled,QSpinBox:disabled{"
        "background:#111827;color:#64748b;border-color:#334155;}"
    )


class MiniMaxH3ApiVideoWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._thread: MiniMaxH3ApiGenerationThread | None = None
        self._syncing = False
        self._scene_connected = False
        self._connected_prompt = ""
        self._connected_first = ""
        self._connected_last = ""
        self._connected_refs = ""
        self._busy = False

        build_ports(node_item)
        self.setObjectName("MiniMaxH3ApiVideoWidget")
        self.setMinimumSize(MINIMAX_H3_API_VIDEO_BODY_W, MINIMAX_H3_API_VIDEO_BODY_H)
        self.setStyleSheet(_field_style())

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._prompt_edit = QtWidgets.QPlainTextEdit()
        self._prompt_edit.setPlaceholderText("Prompt")
        self._prompt_edit.setToolTip("Required text prompt sent as the MiniMax H3 text content item.")
        self._prompt_edit.setFixedHeight(78)
        self._prompt_edit.textChanged.connect(self._commit_controls)
        root.addLayout(self._row("Prompt", self._prompt_edit), 0)

        self._mode_combo = QtWidgets.QComboBox()
        self._mode_combo.setToolTip("Image-to-video uses first/last frame inputs. Reference-to-video uses reference images and cannot be mixed with first/last frames.")
        self._mode_combo.addItem("Image-to-video", "image")
        self._mode_combo.addItem("Reference-to-video", "reference")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        root.addLayout(self._row("Mode", self._mode_combo), 0)

        self._first_edit = QtWidgets.QLineEdit()
        self._first_edit.setPlaceholderText("Optional first frame")
        self._first_edit.setToolTip("Optional first frame. Accepts a local JPG/PNG/WEBP/HEIC/HEIF, public URL, data:image URI, or mm_file:// reference.")
        self._first_edit.editingFinished.connect(self._commit_controls)
        self._first_btn = self._tool_button("Browse first frame")
        self._first_btn.clicked.connect(lambda: self._browse_image(self._first_edit, "Choose First Frame"))
        first_row = self._row("First", self._first_edit)
        first_row.addWidget(self._first_btn, 0)
        self._first_row_widget = QtWidgets.QWidget()
        self._first_row_widget.setLayout(first_row)
        root.addWidget(self._first_row_widget, 0)

        self._last_edit = QtWidgets.QLineEdit()
        self._last_edit.setPlaceholderText("Optional last frame")
        self._last_edit.setToolTip("Optional last frame. When set with First, MiniMax uses first/last-frame image-to-video.")
        self._last_edit.editingFinished.connect(self._commit_controls)
        self._last_btn = self._tool_button("Browse last frame")
        self._last_btn.clicked.connect(lambda: self._browse_image(self._last_edit, "Choose Last Frame"))
        last_row = self._row("Last", self._last_edit)
        last_row.addWidget(self._last_btn, 0)
        self._last_row_widget = QtWidgets.QWidget()
        self._last_row_widget.setLayout(last_row)
        root.addWidget(self._last_row_widget, 0)

        self._refs_edit = QtWidgets.QPlainTextEdit()
        self._refs_edit.setPlaceholderText("Reference images, one per line")
        self._refs_edit.setToolTip("Reference-to-video images. MiniMax H3 allows up to 9 reference images. You can also connect an Image Collection to the reference_images input.")
        self._refs_edit.setFixedHeight(58)
        self._refs_edit.textChanged.connect(self._commit_controls)
        self._refs_btn = self._tool_button("Browse reference images")
        self._refs_btn.clicked.connect(self._browse_reference_images)
        refs_row = self._row("Refs", self._refs_edit)
        refs_row.addWidget(self._refs_btn, 0)
        self._refs_row_widget = QtWidgets.QWidget()
        self._refs_row_widget.setLayout(refs_row)
        root.addWidget(self._refs_row_widget, 0)

        self._api_key_edit = QtWidgets.QLineEdit()
        self._api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self._api_key_edit.setPlaceholderText("sk-api... or MINIMAX_PAYG_API_KEY")
        self._api_key_edit.setToolTip("MiniMax Pay-as-you-go API key for H3 video generation.")
        self._api_key_edit.textChanged.connect(self._commit_controls)
        root.addLayout(self._row("API Key", self._api_key_edit), 0)

        self._api_base_edit = QtWidgets.QLineEdit()
        self._api_base_edit.setPlaceholderText("https://api.minimax.io")
        self._api_base_edit.setToolTip("MiniMax API base URL. The official global base is https://api.minimax.io.")
        self._api_base_edit.editingFinished.connect(self._commit_controls)
        root.addLayout(self._row("API Base", self._api_base_edit), 0)

        settings_row = QtWidgets.QHBoxLayout()
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.setSpacing(6)
        settings_row.addWidget(self._small_label("Model"), 0)
        self._model_combo = QtWidgets.QComboBox()
        self._model_combo.setToolTip("MiniMax-H3 supports 768P/2K and 4-15 seconds. H3-Max is faster but has no 2K and starts at 5 seconds.")
        self._model_combo.addItem("H3", "MiniMax-H3")
        self._model_combo.addItem("H3 Max", "MiniMax-H3-Max")
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        settings_row.addWidget(self._model_combo, 1)
        settings_row.addWidget(self._small_label("Res"), 0)
        self._resolution_combo = QtWidgets.QComboBox()
        self._resolution_combo.setToolTip("Output resolution. H3 supports 768P and 2K; H3-Max supports 480P and 768P.")
        for label in ("768P", "2K", "480P"):
            self._resolution_combo.addItem(label, label)
        self._resolution_combo.currentIndexChanged.connect(self._commit_controls)
        settings_row.addWidget(self._resolution_combo, 1)
        settings_row.addWidget(self._small_label("Duration"), 0)
        self._duration_spin = QtWidgets.QSpinBox()
        self._duration_spin.setRange(4, 15)
        self._duration_spin.setSuffix(" s")
        self._duration_spin.setToolTip("Duration in seconds. H3 supports 4-15; H3-Max supports 5-15.")
        self._duration_spin.valueChanged.connect(self._commit_controls)
        settings_row.addWidget(self._duration_spin, 0)
        settings_row.addWidget(self._small_label("Ratio"), 0)
        self._ratio_combo = QtWidgets.QComboBox()
        self._ratio_combo.setToolTip("Text-to-video uses this ratio. Image-to-video is treated as adaptive by MiniMax.")
        for label in ("adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"):
            self._ratio_combo.addItem(label, label)
        self._ratio_combo.currentIndexChanged.connect(self._commit_controls)
        settings_row.addWidget(self._ratio_combo, 1)
        root.addLayout(settings_row, 0)

        endpoints_row = QtWidgets.QHBoxLayout()
        endpoints_row.setContentsMargins(0, 0, 0, 0)
        endpoints_row.setSpacing(6)
        endpoints_row.addWidget(self._small_label("Create"), 0)
        self._create_path_edit = QtWidgets.QLineEdit()
        self._create_path_edit.setToolTip("Create-task endpoint path. The MiniMax H3 V2 docs use /v2/video_generation.")
        self._create_path_edit.editingFinished.connect(self._commit_controls)
        endpoints_row.addWidget(self._create_path_edit, 1)
        endpoints_row.addWidget(self._small_label("Query"), 0)
        self._query_path_edit = QtWidgets.QLineEdit()
        self._query_path_edit.setToolTip("Polling endpoint path. Use {task_id} where the MiniMax query docs require the task id.")
        self._query_path_edit.editingFinished.connect(self._commit_controls)
        endpoints_row.addWidget(self._query_path_edit, 1)
        root.addLayout(endpoints_row, 0)

        self._file_path_edit = QtWidgets.QLineEdit()
        self._file_path_edit.setToolTip("Optional file retrieval endpoint used only when the query returns file_id without a video URL.")
        self._file_path_edit.editingFinished.connect(self._commit_controls)
        root.addLayout(self._row("File", self._file_path_edit), 0)

        self._output_dir_edit = QtWidgets.QLineEdit()
        self._output_dir_edit.setToolTip("Folder where the downloaded MP4 will be saved.")
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
        return QtCore.QSize(MINIMAX_H3_API_VIDEO_BODY_W, MINIMAX_H3_API_VIDEO_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(MINIMAX_H3_API_VIDEO_BODY_W, MINIMAX_H3_API_VIDEO_BODY_H)

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
        refs = _connected_image_refs(
            self._node_item,
            {"reference_images", "reference images", "references", "refs"},
        )
        changed = (
            (prompt != self._connected_prompt)
            or (first != self._connected_first)
            or (last != self._connected_last)
            or (refs != self._connected_refs)
        )
        self._connected_prompt = prompt
        self._connected_first = first
        self._connected_last = last
        self._connected_refs = refs
        self._syncing = True
        try:
            self._prompt_edit.setEnabled(not bool(prompt))
            self._first_edit.setEnabled(not bool(first))
            self._first_btn.setEnabled(not bool(first))
            self._last_edit.setEnabled(not bool(last))
            self._last_btn.setEnabled(not bool(last))
            self._refs_edit.setEnabled(not bool(refs))
            self._refs_btn.setEnabled(not bool(refs))
            if prompt:
                self._prompt_edit.setPlainText(prompt)
            if first:
                self._first_edit.setText(first)
            if last:
                self._last_edit.setText(last)
            if refs:
                self._refs_edit.setPlainText(refs)
        finally:
            self._syncing = False
        if changed:
            self._refresh_mode_fields()
            self._refresh_ready_status()

    def _refresh_from_params(self) -> None:
        model = self._model()
        self._syncing = True
        try:
            self._prompt_edit.setPlainText(_param_value(model, _PARAM_PROMPT, ""))
            mode = _normalize_mode(_param_value(model, _PARAM_MODE, "image"))
            mode_idx = self._mode_combo.findData(mode)
            self._mode_combo.setCurrentIndex(max(0, mode_idx))
            self._first_edit.setText(_param_value(model, _PARAM_FIRST_IMAGE, ""))
            self._last_edit.setText(_param_value(model, _PARAM_LAST_IMAGE, ""))
            self._refs_edit.setPlainText(_param_value(model, _PARAM_REFERENCE_IMAGES, ""))
            legacy_key = _param_value(model, _PARAM_API_KEY, "")
            temporary_payg_key = _param_value(model, "__minimax_h3_api_payg_key", "")
            key = temporary_payg_key or legacy_key
            self._api_key_edit.setText("" if key.startswith("sk-cp") else key)
            self._api_base_edit.setText(_param_value(model, _PARAM_API_BASE_URL, _default_api_base_url()))
            model_name = _normalize_model(_param_value(model, _PARAM_MODEL, "MiniMax-H3"))
            idx = self._model_combo.findData(model_name)
            self._model_combo.setCurrentIndex(max(0, idx))
            resolution = _normalize_resolution(_param_value(model, _PARAM_RESOLUTION, "768P"))
            res_idx = self._resolution_combo.findData(resolution)
            self._resolution_combo.setCurrentIndex(max(0, res_idx))
            try:
                self._duration_spin.setValue(max(4, min(15, int(_param_value(model, _PARAM_DURATION, "5") or "5"))))
            except Exception:
                self._duration_spin.setValue(5)
            ratio = _normalize_ratio(_param_value(model, _PARAM_RATIO, "adaptive") or "adaptive")
            ratio_idx = self._ratio_combo.findData(ratio)
            self._ratio_combo.setCurrentIndex(max(0, ratio_idx))
            self._create_path_edit.setText(_param_value(model, _PARAM_CREATE_PATH, "/v2/video_generation") or "/v2/video_generation")
            self._query_path_edit.setText(_param_value(model, _PARAM_QUERY_PATH, "/v2/video_generation/{task_id}") or "/v2/video_generation/{task_id}")
            self._file_path_edit.setText(_param_value(model, _PARAM_FILE_PATH, "/v1/files/retrieve?file_id={file_id}") or "")
            output_dir = _param_value(model, _PARAM_OUTPUT_DIR, "")
            self._output_dir_edit.setText(output_dir or str(_default_output_dir(self._node_item)))
            self._mp4_out.setText(_param_value(model, MP4_OUTPUT_PARAM, ""))
            self._status.setText(_param_value(model, STATUS_OUTPUT_PARAM, "") or "Ready.")
        finally:
            self._syncing = False
        self._refresh_model_limits()
        self._refresh_mode_fields()
        self._refresh_connected_inputs()
        self._refresh_ready_status()

    def _commit_controls(self) -> None:
        if self._syncing:
            return
        if not self._connected_prompt:
            _set_param_value(self._node_item, _PARAM_PROMPT, self._prompt_edit.toPlainText().strip(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_MODE, str(self._mode_combo.currentData() or "image"), notify_scene=False)
        if not self._connected_first:
            _set_param_value(self._node_item, _PARAM_FIRST_IMAGE, self._first_edit.text().strip(), notify_scene=False)
        if not self._connected_last:
            _set_param_value(self._node_item, _PARAM_LAST_IMAGE, self._last_edit.text().strip(), notify_scene=False)
        if not self._connected_refs:
            _set_param_value(self._node_item, _PARAM_REFERENCE_IMAGES, self._refs_edit.toPlainText().strip(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_API_KEY, self._api_key_edit.text().strip(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_API_BASE_URL, self._api_base_edit.text().strip() or _default_api_base_url(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_MODEL, str(self._model_combo.currentData() or "MiniMax-H3"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_RESOLUTION, str(self._resolution_combo.currentData() or "768P"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_DURATION, str(int(self._duration_spin.value())), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_RATIO, str(self._ratio_combo.currentData() or "adaptive"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_CREATE_PATH, self._create_path_edit.text().strip() or "/v2/video_generation", notify_scene=False)
        _set_param_value(
            self._node_item,
            _PARAM_QUERY_PATH,
            self._query_path_edit.text().strip() or "/v2/video_generation/{task_id}",
            notify_scene=False,
        )
        _set_param_value(self._node_item, _PARAM_FILE_PATH, self._file_path_edit.text().strip(), notify_scene=False)
        default_output = str(_default_output_dir(self._node_item))
        output_dir = self._output_dir_edit.text().strip() or default_output
        _set_param_value(self._node_item, _PARAM_OUTPUT_DIR, "" if output_dir == default_output else output_dir, notify_scene=False)
        self._refresh_ready_status()

    def _refresh_model_limits(self) -> None:
        model_name = str(self._model_combo.currentData() or "MiniMax-H3")
        current = int(self._duration_spin.value())
        self._duration_spin.setRange(5 if model_name == "MiniMax-H3-Max" else 4, 15)
        if current < int(self._duration_spin.minimum()):
            self._duration_spin.setValue(int(self._duration_spin.minimum()))
        if model_name == "MiniMax-H3-Max" and str(self._resolution_combo.currentData() or "") == "2K":
            idx = self._resolution_combo.findData("768P")
            self._resolution_combo.setCurrentIndex(max(0, idx))

    def _refresh_mode_fields(self) -> None:
        mode = _normalize_mode(str(self._mode_combo.currentData() or "image"))
        reference_mode = mode == "reference"
        if reference_mode and str(self._model_combo.currentData() or "") != "MiniMax-H3":
            idx = self._model_combo.findData("MiniMax-H3")
            self._model_combo.setCurrentIndex(max(0, idx))
        self._first_row_widget.setVisible(not reference_mode)
        self._last_row_widget.setVisible(not reference_mode)
        self._refs_row_widget.setVisible(reference_mode)
        enabled = not bool(self._busy)
        self._mode_combo.setEnabled(enabled)
        self._model_combo.setEnabled(enabled and not reference_mode)
        self._first_edit.setEnabled(enabled and not reference_mode and not bool(self._connected_first))
        self._first_btn.setEnabled(enabled and not reference_mode and not bool(self._connected_first))
        self._last_edit.setEnabled(enabled and not reference_mode and not bool(self._connected_last))
        self._last_btn.setEnabled(enabled and not reference_mode and not bool(self._connected_last))
        self._refs_edit.setEnabled(enabled and reference_mode and not bool(self._connected_refs))
        self._refs_btn.setEnabled(enabled and reference_mode and not bool(self._connected_refs))
        if reference_mode:
            self._ratio_combo.setToolTip("Reference-to-video ratio. Adaptive is allowed, or choose a fixed output ratio.")
        else:
            self._ratio_combo.setToolTip("Text-to-video uses this ratio. Image-to-video is treated as adaptive by MiniMax.")

    def _on_mode_changed(self) -> None:
        self._refresh_mode_fields()
        self._refresh_model_limits()
        self._commit_controls()

    def _on_model_changed(self) -> None:
        self._refresh_model_limits()
        self._refresh_mode_fields()
        self._commit_controls()

    def _refresh_ready_status(self) -> None:
        if self._thread is not None:
            return
        api_key = self._api_key_edit.text().strip() or _default_api_key()
        if not api_key:
            self._generate_btn.setEnabled(False)
            self._status.setStyleSheet(
                "QLabel{background:#160f08;border:1px solid #7c2d12;border-radius:4px;"
                "padding:4px 6px;color:#fdba74;font-size:11px;}"
            )
            self._status.setText("Enter a MiniMax Pay-as-you-go API key or set MINIMAX_PAYG_API_KEY.")
            return
        mode = _normalize_mode(str(self._mode_combo.currentData() or "image"))
        if mode == "reference" and not _parse_image_ref_list(self._connected_refs or self._refs_edit.toPlainText()):
            self._generate_btn.setEnabled(False)
            self._status.setStyleSheet(
                "QLabel{background:#160f08;border:1px solid #7c2d12;border-radius:4px;"
                "padding:4px 6px;color:#fdba74;font-size:11px;}"
            )
            self._status.setText("Reference mode needs 1-9 reference images.")
            return
        self._generate_btn.setEnabled(True)
        self._status.setStyleSheet(
            "QLabel{background:#07140f;border:1px solid #14532d;border-radius:4px;"
            "padding:4px 6px;color:#86efac;font-size:11px;}"
        )
        video_mode = "reference" if mode == "reference" else "image"
        self._status.setText(f"Ready for MiniMax H3 API {video_mode} mode.")

    def _browse_image(self, edit: QtWidgets.QLineEdit, title: str) -> None:
        parent = _dialog_parent(self._node_item) or self
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            title,
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.heic *.heif);;All Files (*.*)",
        )
        if not path:
            return
        edit.setText(path)
        self._commit_controls()

    def _browse_reference_images(self) -> None:
        parent = _dialog_parent(self._node_item) or self
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            parent,
            "Choose Reference Images",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.heic *.heif);;All Files (*.*)",
        )
        if not paths:
            return
        refs = _parse_image_ref_list(self._refs_edit.toPlainText())
        refs.extend(paths)
        self._refs_edit.setPlainText("\n".join(_dedupe_strings(refs)))
        self._commit_controls()

    def _browse_output_dir(self) -> None:
        parent = _dialog_parent(self._node_item) or self
        start = self._output_dir_edit.text().strip() or str(_default_output_dir(self._node_item))
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Choose MiniMax H3 API Output Folder", start)
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
        self._busy = bool(busy)
        self._generate_btn.setEnabled(not busy)
        self._api_key_edit.setEnabled(not busy)
        self._api_base_edit.setEnabled(not busy)
        self._resolution_combo.setEnabled(not busy)
        self._duration_spin.setEnabled(not busy)
        self._ratio_combo.setEnabled(not busy)
        self._create_path_edit.setEnabled(not busy)
        self._query_path_edit.setEnabled(not busy)
        self._file_path_edit.setEnabled(not busy)
        self._output_dir_edit.setEnabled(not busy)
        self._output_btn.setEnabled(not busy)
        self._refresh_mode_fields()
        self._generate_btn.setText("Working..." if busy else "Generate")
        try:
            self._node_item.setBusyState(busy, "minimax_h3_api" if busy else "")
        except Exception:
            pass

    def _collect_settings(self) -> MiniMaxH3ApiJobSettings:
        self._commit_controls()
        mode = _normalize_mode(str(self._mode_combo.currentData() or "image"))
        prompt = self._connected_prompt or self._prompt_edit.toPlainText().strip()
        if mode == "reference":
            first = ""
            last = ""
            reference_images = _validate_image_refs(self._connected_refs or self._refs_edit.toPlainText(), self._node_item)
        else:
            first = _validate_image_ref(self._connected_first or self._first_edit.text().strip(), self._node_item)
            last = _validate_image_ref(self._connected_last or self._last_edit.text().strip(), self._node_item)
            reference_images = []
        output_dir = _output_dir(self._node_item, self._output_dir_edit.text().strip())
        model = self._model()
        node_name = _safe_stem(str(getattr(model, "name", "") or "minimax_h3_api"))
        try:
            poll_interval = float(_param_value(model, _PARAM_POLL_INTERVAL, "5") or "5")
        except Exception:
            poll_interval = 5.0
        try:
            timeout = int(float(_param_value(model, _PARAM_TIMEOUT, "1800") or "1800"))
        except Exception:
            timeout = 1800
        return MiniMaxH3ApiJobSettings(
            prompt=prompt,
            mode=mode,
            first_image=first,
            last_image=last,
            reference_images=reference_images,
            api_key=self._api_key_edit.text().strip() or _default_api_key(),
            api_base_url=self._api_base_edit.text().strip() or _default_api_base_url(),
            model=str(self._model_combo.currentData() or "MiniMax-H3"),
            create_path=self._create_path_edit.text().strip() or "/v2/video_generation",
            query_path=self._query_path_edit.text().strip() or "/v2/video_generation/{task_id}",
            file_path=self._file_path_edit.text().strip(),
            duration=int(self._duration_spin.value()),
            ratio=str(self._ratio_combo.currentData() or "adaptive"),
            resolution=str(self._resolution_combo.currentData() or "768P"),
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
        mode_label = "reference-to-video" if settings.mode == "reference" else "image-to-video"
        self._set_status(f"Submitting MiniMax H3 API {mode_label} generation...")
        self._thread = MiniMaxH3ApiGenerationThread(settings, self)
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
        result = result_obj if isinstance(result_obj, MiniMaxH3ApiJobResult) else MiniMaxH3ApiJobResult(False, error=str(result_obj))
        _set_param_value(self._node_item, MP4_OUTPUT_PARAM, result.mp4_path, notify_scene=False)
        _set_param_value(self._node_item, TASK_OUTPUT_PARAM, result.task_id, notify_scene=False)
        final_status = result.status or ("Done." if result.ok else "MiniMax H3 API generation failed.")
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
        _PARAM_MODE: "image",
        _PARAM_FIRST_IMAGE: "",
        _PARAM_LAST_IMAGE: "",
        _PARAM_REFERENCE_IMAGES: "",
        _PARAM_API_KEY: "",
        _PARAM_API_BASE_URL: _default_api_base_url(),
        _PARAM_MODEL: "MiniMax-H3",
        _PARAM_CREATE_PATH: "/v2/video_generation",
        _PARAM_QUERY_PATH: "/v2/video_generation/{task_id}",
        _PARAM_FILE_PATH: "/v1/files/retrieve?file_id={file_id}",
        _PARAM_DURATION: "5",
        _PARAM_RATIO: "adaptive",
        _PARAM_RESOLUTION: "768P",
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
        node_item.ensure_input("reference_images")
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
        "first_image": 152,
        "last_image": 182,
        "reference_images": 182,
    }
    for name, offset in inputs.items():
        try:
            node_item._input_port_pos[name.lower()] = (QtCore.QPointF(0.0, float(y_cursor + offset)), name)
        except Exception:
            pass
    outputs = {
        STATUS_OUTPUT_PARAM: MINIMAX_H3_API_VIDEO_BODY_H - 68,
        TASK_OUTPUT_PARAM: MINIMAX_H3_API_VIDEO_BODY_H - 48,
        MP4_OUTPUT_PARAM: MINIMAX_H3_API_VIDEO_BODY_H - 28,
    }
    for name, offset in outputs.items():
        try:
            node_item._output_port_pos[name.lower()] = (QtCore.QPointF(float(node_item.width), float(y_cursor + offset)), name)
        except Exception:
            pass


def render_node_body(node_item, y_cursor: int) -> int:
    build_ports(node_item)
    body = MiniMaxH3ApiVideoWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    w = max(int(hint.width()), int(getattr(node_item, "width", MINIMAX_H3_API_VIDEO_BODY_W) or MINIMAX_H3_API_VIDEO_BODY_W))
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


MINIMAX_H3_API_VIDEO_SPEC = Spec(
    stripe_color="#06b6d4",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "MINIMAX_H3_API_VIDEO_NODE_ALIASES",
    "MINIMAX_H3_API_VIDEO_NODE_KIND",
    "MINIMAX_H3_API_VIDEO_NODE_KINDS",
    "MINIMAX_H3_API_VIDEO_BODY_H",
    "MINIMAX_H3_API_VIDEO_BODY_W",
    "MINIMAX_H3_API_VIDEO_SPEC",
    "MiniMaxH3ApiJobSettings",
    "MiniMaxH3ApiJobResult",
    "build_ports",
    "render_node_body",
    "run_minimax_h3_api_job",
]
