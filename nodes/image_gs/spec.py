from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec
from echograph.util.image_gs_runtime import (
    app_home_dir,
    image_gs_dependency_status,
    image_gs_python,
    image_gs_root,
    image_gs_setup_script,
    image_gs_status,
)


KIND_ALIASES = {
    "image_gs_splat",
    "image gs splat",
    "imagegssplat",
    "image_to_splat",
    "image to splat",
    "image_splat",
}

_DEFAULT_NUM_GAUSSIANS = 2000
_DEFAULT_MAX_STEPS = 800
_DEFAULT_RENDER_HEIGHT = 768
_DEFAULT_SHEET_SCALE = 2.0
_DEFAULT_RADIUS_SCALE = 1.0
_DEFAULT_ALPHA = 0.92
_DEFAULT_DEVICE = "cuda:0"
_NODE_BODY_MIN_W = 378
_NODE_BODY_MIN_H = 520
_NODE_PREVIEW_H = 216
_CONTROL_W = 92
_STATUS_MIN_H = 64
_STATUS_MAX_H = 112
_STATUS_TEXT_PAD_H = 16
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".bmp", ".webp"}
_NODE_SCHEMA = "qubit.image_gs_splat.v1"
_SETUP_PROMPT_SHOWN = False
_DEBUG_CAPTURE_TAIL_CHARS = 50000


@dataclass(frozen=True)
class ImageGsSplatOutcome:
    asset: Optional[Dict[str, Any]]
    status: str
    detail: str
    generated: bool = False


@dataclass(frozen=True)
class ImageGsRunPlan:
    image_path: Path
    image_rel: str
    output_dir: Path
    exp_name: str
    asset_stem: str
    ply_path: Path
    manifest_path: Path
    width: int
    height: int


def _repo_root() -> Path:
    try:
        return Path(__file__).resolve().parents[2]
    except Exception:
        return Path.cwd()


def _logs_dir() -> Path:
    out = _repo_root() / "logs" / "image_gs_splat"
    try:
        out.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return out


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value") or "")
    return ""


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = _param_value(model, name).strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _param_int(model, name: str, default: int, *, min_value: int, max_value: int) -> int:
    try:
        value = int(round(float(_param_value(model, name).strip())))
    except Exception:
        value = int(default)
    return max(int(min_value), min(int(max_value), int(value)))


def _param_float(model, name: str, default: float, *, min_value: float, max_value: float) -> float:
    try:
        value = float(_param_value(model, name).strip())
    except Exception:
        value = float(default)
    return max(float(min_value), min(float(max_value), float(value)))


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        params = list(params)
        try:
            setattr(model, "params", params)
        except Exception:
            return
    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _set_param(node_item, name: str, value: str, *, notify_scene: bool = True) -> bool:
    try:
        setter = getattr(node_item, "_set_param_value", None)
        if callable(setter):
            setter(name, str(value), rebuild=False, notify_scene=bool(notify_scene))
            return True
    except Exception:
        pass
    model = getattr(node_item, "model", None)
    if model is None:
        return False
    _ensure_param(node_item, name, str(value))
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value)
            return True
    return False


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    entry = None
    for param in params:
        if isinstance(param, dict) and (param.get("name") or "").strip().lower() == store_key:
            entry = param
            break
    if entry is None:
        entry = {"name": store_key, "value": ""}
        params.append(entry)
    hidden = {
        part.strip().lower()
        for part in str(entry.get("value") or "").split(",")
        if part.strip()
    }
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    try:
        model.params = params
    except Exception:
        pass


def _safe_stem(value: str, fallback: str = "image_gs_splat") -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    clean = clean.strip("._-")
    if not clean:
        clean = fallback
    return clean[:72]


def _node_name(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "name", "") or "image_gs_splat").strip() or "image_gs_splat"


def _source_key(model) -> str:
    return (
        _param_value(model, "image_url").strip()
        or _param_value(model, "source").strip()
        or _param_value(model, "path").strip()
    )


def _hash_settings(model) -> str:
    payload = {
        "source": _source_key(model),
        "num": _param_int(model, "num_gaussians", _DEFAULT_NUM_GAUSSIANS, min_value=100, max_value=2_000_000),
        "steps": _param_int(model, "max_steps", _DEFAULT_MAX_STEPS, min_value=1, max_value=2_000_000),
        "sheet": _param_float(model, "sheet_scale", _DEFAULT_SHEET_SCALE, min_value=0.01, max_value=100.0),
        "radius": _param_float(model, "radius_scale", _DEFAULT_RADIUS_SCALE, min_value=0.01, max_value=100.0),
        "alpha": _param_float(model, "alpha", _DEFAULT_ALPHA, min_value=0.001, max_value=0.999),
        "init": _param_value(model, "init_mode").strip() or "gradient",
        "quantize": _param_bool(model, "quantize", False),
    }
    blob = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _output_dir(node_item) -> Path:
    model = getattr(node_item, "model", None)
    raw = _param_value(model, "output_dir").strip()
    if raw:
        out = Path(raw)
    else:
        out = _logs_dir() / _safe_stem(_node_name(node_item))
    out.mkdir(parents=True, exist_ok=True)
    return out


def _debug_enabled(model) -> bool:
    return _param_bool(model, "debug_log", False)


def _debug_log(model, event: str, **fields) -> None:
    if not _debug_enabled(model):
        return
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": str(event or ""),
    }
    record.update(fields or {})
    try:
        with (_logs_dir() / "debug.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")
    except Exception:
        pass


def _model_from_node(obj):
    return getattr(obj, "model", obj)


def _debug_capture_path(obj) -> Path:
    model = _model_from_node(obj)
    name = str(getattr(model, "name", "") or "image_gs_splat").strip() or "image_gs_splat"
    return _logs_dir() / f"{_safe_stem(name)}_latest_run.log"


def _set_model_param_value(model, name: str, value: str) -> None:
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        params = list(params)
        try:
            setattr(model, "params", params)
        except Exception:
            return
    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value)
            return
    params.append({"name": name, "value": str(value)})


def _begin_debug_capture(obj, action: str, *, command=None, cwd=None) -> Path:
    model = _model_from_node(obj)
    path = _debug_capture_path(obj)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    command_text = ""
    if command is not None:
        if isinstance(command, (list, tuple)):
            command_text = " ".join(str(part) for part in command)
        else:
            command_text = str(command)
    lines = [
        "Image-GS Run Log",
        f"started: {started}",
        f"node: {getattr(model, 'name', 'image_gs_splat')}",
        f"action: {action}",
    ]
    if cwd:
        lines.append(f"cwd: {cwd}")
    if command_text:
        lines.append(f"command: {command_text}")
    lines.append("")
    header = "\n".join(lines)
    try:
        setattr(model, "_image_gs_last_action", str(action or ""))
        setattr(model, "_image_gs_last_started", started)
        setattr(model, "_image_gs_last_finished", "")
        setattr(model, "_image_gs_last_exit_code", "")
        setattr(model, "_image_gs_last_status", "running")
        setattr(model, "_image_gs_last_command", command_text)
        setattr(model, "_image_gs_last_log_path", str(path))
        setattr(model, "_image_gs_last_output", header)
    except Exception:
        pass
    try:
        path.write_text(header, encoding="utf-8")
    except Exception:
        pass
    return path


def _capture_debug_output(obj, text: str) -> None:
    if not text:
        return
    model = _model_from_node(obj)
    try:
        current = str(getattr(model, "_image_gs_last_output", "") or "")
        setattr(model, "_image_gs_last_output", (current + str(text))[-_DEBUG_CAPTURE_TAIL_CHARS:])
    except Exception:
        pass
    try:
        raw_path = str(getattr(model, "_image_gs_last_log_path", "") or "").strip()
        path = Path(raw_path) if raw_path else _debug_capture_path(obj)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(str(text))
    except Exception:
        pass


def _finish_debug_capture(obj, exit_code=None, status_text: str = "") -> None:
    model = _model_from_node(obj)
    finished = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        setattr(model, "_image_gs_last_finished", finished)
        setattr(model, "_image_gs_last_exit_code", "" if exit_code is None else str(exit_code))
        setattr(model, "_image_gs_last_status", str(status_text or "finished"))
    except Exception:
        pass
    trailer = [
        "",
        "Image-GS Run Finished",
        f"finished: {finished}",
    ]
    if exit_code is not None:
        trailer.append(f"exit_code: {exit_code}")
    if status_text:
        trailer.append(f"status: {status_text}")
    trailer.append("")
    _capture_debug_output(obj, "\n".join(trailer))


def _path_state(path_text: str) -> str:
    raw = str(path_text or "").strip()
    if not raw:
        return "Not set"
    try:
        return f"{raw} ({'exists' if Path(raw).exists() else 'missing'})"
    except Exception:
        return raw


def image_gs_debug_report_text(node_item) -> str:
    model = _model_from_node(node_item)
    try:
        runtime = image_gs_status()
    except Exception as exc:
        runtime = None
        runtime_detail = f"Runtime check failed: {exc}"
    else:
        runtime_detail = str(getattr(runtime, "detail", "") or "")
    try:
        deps = image_gs_dependency_status(timeout=20)
    except Exception as exc:
        deps = None
        deps_detail = f"Dependency check failed: {exc}"
    else:
        deps_detail = str(getattr(deps, "detail", "") or "")

    log_path = str(getattr(model, "_image_gs_last_log_path", "") or _debug_capture_path(node_item))
    output_text = str(getattr(model, "_image_gs_last_output", "") or "")
    if not output_text:
        try:
            path = Path(log_path)
            if path.exists():
                output_text = path.read_text(encoding="utf-8", errors="replace")[-_DEBUG_CAPTURE_TAIL_CHARS:]
        except Exception:
            output_text = ""

    source = _source_key(model)
    output_dir = _param_value(model, "output_dir").strip()
    if not output_dir:
        output_dir = str(_logs_dir() / _safe_stem(str(getattr(model, "name", "") or "image_gs_splat")))

    lines = [
        "Image-GS Debug Report",
        f"generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"node: {getattr(model, 'name', 'image_gs_splat')}",
        f"kind: {getattr(model, 'kind', 'image_gs_splat')}",
        f"debug_log: {'on' if _debug_enabled(model) else 'off'}",
        "",
        "Runtime",
        f"app_home: {app_home_dir()}",
        f"repo: {getattr(runtime, 'root', image_gs_root()) if runtime is not None else image_gs_root()}",
        f"python: {getattr(runtime, 'python', image_gs_python()) if runtime is not None else image_gs_python()}",
        f"runtime_ready: {bool(getattr(runtime, 'ready', False)) if runtime is not None else False}",
        f"runtime_detail: {runtime_detail}",
        f"deps_ready: {bool(getattr(deps, 'ready', False)) if deps is not None else False}",
        f"deps_detail: {deps_detail}",
        "",
        "Inputs And Settings",
        f"source: {source or 'Not set'}",
        f"num_gaussians: {_param_int(model, 'num_gaussians', _DEFAULT_NUM_GAUSSIANS, min_value=100, max_value=2_000_000)}",
        f"max_steps: {_param_int(model, 'max_steps', _DEFAULT_MAX_STEPS, min_value=1, max_value=2_000_000)}",
        f"render_height: {_param_int(model, 'render_height', _DEFAULT_RENDER_HEIGHT, min_value=64, max_value=12000)}",
        f"init_mode: {_param_value(model, 'init_mode').strip() or 'gradient'}",
        f"device: {_param_value(model, 'device').strip() or _DEFAULT_DEVICE}",
        "",
        "Generated Files",
        f"output_dir: {_path_state(output_dir)}",
        f"splat_ply: {_path_state(_param_value(model, 'splat_ply'))}",
        f"path: {_path_state(_param_value(model, 'path'))}",
        f"checkpoint: {_path_state(_param_value(model, 'checkpoint'))}",
        f"preview_image: {_path_state(_param_value(model, 'preview_image'))}",
        f"manifest: {_path_state(_param_value(model, 'manifest'))}",
        "",
        "Last Run",
        f"action: {getattr(model, '_image_gs_last_action', '') or 'None captured'}",
        f"started: {getattr(model, '_image_gs_last_started', '') or 'Not captured'}",
        f"finished: {getattr(model, '_image_gs_last_finished', '') or 'Not captured'}",
        f"exit_code: {getattr(model, '_image_gs_last_exit_code', '') or 'Not captured'}",
        f"status: {getattr(model, '_image_gs_last_status', '') or 'Not captured'}",
        f"command: {getattr(model, '_image_gs_last_command', '') or 'Not captured'}",
        f"log_path: {log_path}",
        "",
        "Captured Output",
        output_text.strip() or "No Image-GS process output has been captured yet. Click Generate, then open this report again.",
    ]
    return "\n".join(lines)


def write_image_gs_debug_report(node_item, report: str) -> Path:
    model = _model_from_node(node_item)
    name = str(getattr(model, "name", "") or "image_gs_splat").strip() or "image_gs_splat"
    path = _logs_dir() / f"{_safe_stem(name)}_debug_report_latest.txt"
    path.write_text(str(report or ""), encoding="utf-8")
    return path


def show_image_gs_debug_report(node_item, parent=None) -> bool:
    model = _model_from_node(node_item)
    report_holder = {"text": image_gs_debug_report_text(node_item), "path": None}
    try:
        report_holder["path"] = write_image_gs_debug_report(node_item, report_holder["text"])
    except Exception:
        report_holder["path"] = None

    dialog = QtWidgets.QDialog(parent or _resolve_window(node_item))
    dialog.setWindowTitle("Image-GS Debug")
    dialog.resize(820, 560)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.setSpacing(8)

    saved_label = QtWidgets.QLabel()
    saved_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard)
    layout.addWidget(saved_label)

    debug_check = QtWidgets.QCheckBox("Verbose debug logging")
    debug_check.setToolTip("Write extra Image-GS JSONL diagnostics to logs/image_gs_splat/debug.log.")
    debug_check.setChecked(_debug_enabled(model))
    debug_check.setStyleSheet("QCheckBox{color:#cbd5e1;}")
    layout.addWidget(debug_check)

    text = QtWidgets.QPlainTextEdit()
    text.setReadOnly(True)
    text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
    text.setPlainText(report_holder["text"])
    text.moveCursor(QtGui.QTextCursor.End)
    text.setStyleSheet(
        "QPlainTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
        "border-radius:6px;padding:6px;font-family:Consolas,monospace;font-size:11px;}"
    )
    layout.addWidget(text, 1)

    def _update_saved_label() -> None:
        path = report_holder.get("path")
        saved_label.setText(f"Saved to: {path}" if path else "Could not write debug report to the logs folder.")

    def _refresh_report() -> None:
        report_holder["text"] = image_gs_debug_report_text(node_item)
        try:
            report_holder["path"] = write_image_gs_debug_report(node_item, report_holder["text"])
        except Exception:
            report_holder["path"] = None
        text.setPlainText(report_holder["text"])
        text.moveCursor(QtGui.QTextCursor.End)
        _update_saved_label()

    def _copy_report() -> None:
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(str(report_holder.get("text") or text.toPlainText() or ""))
        copy_btn.setText("Copied")

    def _open_logs() -> None:
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(_logs_dir())))
        except Exception:
            pass

    def _toggle_verbose(state: int) -> None:
        value = "1" if state == QtCore.Qt.Checked or bool(debug_check.isChecked()) else "0"
        setter = getattr(node_item, "_set_param_value", None)
        if callable(setter):
            try:
                setter("debug_log", value, rebuild=False, notify_scene=True)
            except Exception:
                _set_model_param_value(model, "debug_log", value)
        else:
            _set_model_param_value(model, "debug_log", value)
        _refresh_report()

    debug_check.stateChanged.connect(_toggle_verbose)

    row = QtWidgets.QHBoxLayout()
    row.addStretch(1)
    refresh_btn = QtWidgets.QPushButton("Refresh")
    copy_btn = QtWidgets.QPushButton("Copy")
    open_btn = QtWidgets.QPushButton("Open Logs")
    close_btn = QtWidgets.QPushButton("Close")
    refresh_btn.clicked.connect(_refresh_report)
    copy_btn.clicked.connect(_copy_report)
    open_btn.clicked.connect(_open_logs)
    close_btn.clicked.connect(dialog.accept)
    row.addWidget(refresh_btn)
    row.addWidget(copy_btn)
    row.addWidget(open_btn)
    row.addWidget(close_btn)
    layout.addLayout(row)

    _update_saved_label()
    dialog.exec()
    return True


def _qt_image_size(path: Path) -> tuple[int, int]:
    try:
        image = QtGui.QImage(str(path))
        if not image.isNull() and image.width() > 0 and image.height() > 0:
            return int(image.width()), int(image.height())
    except Exception:
        pass
    return 1024, 1024


def _url_filename(raw: str, fallback: str) -> str:
    parsed = url_parse.urlparse(raw)
    name = Path(url_parse.unquote(parsed.path or "")).name
    if not name:
        name = fallback
    stem = _safe_stem(Path(name).stem, fallback)
    ext = Path(name).suffix.lower()
    if ext not in _IMAGE_EXTS:
        ext = ".png"
    return f"{stem}{ext}"


def _normalize_image_for_image_gs(source_path: Path, input_dir: Path, asset_stem: str) -> Path:
    target = input_dir / f"{asset_stem}.png"
    image = QtGui.QImage(str(source_path))
    if image.isNull():
        raise RuntimeError(f"Could not read image for Image-GS staging: {source_path}")
    try:
        srgb = QtGui.QColorSpace(QtGui.QColorSpace.SRgb)
        converted = image.convertedToColorSpace(srgb) if hasattr(image, "convertedToColorSpace") else None
        if converted is not None and not converted.isNull():
            image = converted
        else:
            image.convertToColorSpace(srgb)
    except Exception:
        pass
    try:
        rgb = image.convertToFormat(QtGui.QImage.Format_RGB888)
    except Exception:
        rgb = image.convertToFormat(QtGui.QImage.Format.Format_RGB888)
    if rgb.isNull():
        raise RuntimeError(f"Could not convert image to 8-bit RGB PNG: {source_path}")
    if not rgb.save(str(target), "PNG"):
        raise RuntimeError(f"Could not write staged Image-GS PNG: {target}")
    return target


def _copy_or_download_image(source: str, output_dir: Path, asset_stem: str) -> Path:
    source = str(source or "").strip()
    if not source:
        raise ValueError("Enter an image URL or local image path.")

    input_dir = output_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    parsed = url_parse.urlparse(source)
    is_url = parsed.scheme.lower() in {"http", "https"}
    if is_url:
        filename = _url_filename(source, f"{asset_stem}.png")
        raw_target = input_dir / f"{asset_stem}_source{Path(filename).suffix.lower() or '.png'}"
        try:
            request = url_request.Request(source, headers={"User-Agent": "QubitMCP Image-GS node"})
            with url_request.urlopen(request, timeout=45) as response:
                data = response.read()
            if not data:
                raise ValueError("download returned no data")
            raw_target.write_bytes(data)
        except (url_error.URLError, OSError, ValueError) as exc:
            raise RuntimeError(f"Image download failed: {exc}") from exc
        return _normalize_image_for_image_gs(raw_target, input_dir, asset_stem)

    local = Path(source).expanduser()
    if not local.exists() or not local.is_file():
        raise FileNotFoundError(f"Image path does not exist: {source}")
    ext = local.suffix.lower()
    if ext not in _IMAGE_EXTS:
        raise ValueError(f"Unsupported image extension '{ext}'.")
    return _normalize_image_for_image_gs(local, input_dir, asset_stem)


def prepare_image_gs_run(node_item) -> ImageGsRunPlan:
    model = getattr(node_item, "model", None)
    status = image_gs_status()
    if not status.ready:
        raise RuntimeError(status.detail)
    source = _source_key(model)
    digest = _hash_settings(model)
    asset_stem = f"{_safe_stem(_node_name(node_item))}_{digest}"
    out_dir = _output_dir(node_item)
    local_image = _copy_or_download_image(source, out_dir, asset_stem)
    width, height = _qt_image_size(local_image)

    media_dir = status.root / "media" / "qubitmcp"
    media_dir.mkdir(parents=True, exist_ok=True)
    media_image = media_dir / f"{asset_stem}{local_image.suffix.lower() or '.png'}"
    if local_image.resolve() != media_image.resolve():
        shutil.copy2(local_image, media_image)

    exp_name = f"qubitmcp/{asset_stem}"
    ply_path = out_dir / f"{asset_stem}.ply"
    manifest_path = out_dir / f"{asset_stem}.image_gs_splat.json"
    rel = f"qubitmcp/{media_image.name}"
    _set_param(node_item, "source_image", str(local_image), notify_scene=False)
    _set_param(node_item, "image_gs_input", rel, notify_scene=False)
    _set_param(node_item, "image_gs_exp_name", exp_name, notify_scene=False)
    return ImageGsRunPlan(
        image_path=local_image,
        image_rel=rel,
        output_dir=out_dir,
        exp_name=exp_name,
        asset_stem=asset_stem,
        ply_path=ply_path,
        manifest_path=manifest_path,
        width=width,
        height=height,
    )


def image_gs_command_args(node_item, plan: ImageGsRunPlan) -> list[str]:
    model = getattr(node_item, "model", None)
    num = _param_int(model, "num_gaussians", _DEFAULT_NUM_GAUSSIANS, min_value=100, max_value=2_000_000)
    steps = _param_int(model, "max_steps", _DEFAULT_MAX_STEPS, min_value=1, max_value=2_000_000)
    render_h = _param_int(model, "render_height", _DEFAULT_RENDER_HEIGHT, min_value=64, max_value=12000)
    eval_steps = max(1, min(100, steps))
    init_mode = (_param_value(model, "init_mode").strip() or "gradient").lower()
    if init_mode not in {"gradient", "saliency", "random"}:
        init_mode = "gradient"
    args = [
        "main.py",
        "--data_root",
        "media",
        "--input_path",
        plan.image_rel.replace("\\", "/"),
        "--log_root",
        "results",
        "--exp_name",
        plan.exp_name.replace("\\", "/"),
        "--device",
        _param_value(model, "device").strip() or _DEFAULT_DEVICE,
        "--num_gaussians",
        str(num),
        "--max_steps",
        str(steps),
        "--save_ckpt_steps",
        str(steps),
        "--save_image_steps",
        str(steps),
        "--eval_steps",
        str(eval_steps),
        "--render_height",
        str(render_h),
        "--save_image_format",
        "png",
        "--save_plot_format",
        "jpg",
        "--init_mode",
        init_mode,
        "--disable_prog_optim",
    ]
    if _param_bool(model, "quantize", False):
        args.append("--quantize")
    if _param_bool(model, "vis_gaussians", True):
        args.append("--vis_gaussians")
    return args


def _results_dir(runtime_root: Path, exp_name: str) -> Path:
    return runtime_root / "results" / Path(exp_name)


def _latest_file(base: Path, patterns: list[str]) -> Optional[Path]:
    if not base.exists():
        return None
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path for path in base.rglob(pattern) if path.is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def find_latest_checkpoint(runtime_root: Path, exp_name: str) -> Optional[Path]:
    return _latest_file(_results_dir(runtime_root, exp_name), ["ckpt_step-*.pt"])


def find_latest_preview(runtime_root: Path, exp_name: str) -> Optional[Path]:
    base = _results_dir(runtime_root, exp_name)
    preferred = _latest_file(
        base,
        [
            "gaussian-position_step-*.jpg",
            "gaussian-position_step-*.jpeg",
            "gaussian-position_step-*.png",
            "render_step-*.png",
            "render_res-*.png",
        ],
    )
    if preferred is not None:
        return preferred
    return _latest_file(base, ["*.png", "*.jpg", "*.jpeg"])


def convert_checkpoint_to_ply(node_item, plan: ImageGsRunPlan, ckpt_path: Path) -> str:
    status = image_gs_status()
    model = getattr(node_item, "model", None)
    script = Path(__file__).resolve().with_name("convert_checkpoint_to_ply.py")
    args = [
        str(script),
        "--ckpt",
        str(ckpt_path),
        "--out",
        str(plan.ply_path),
        "--width",
        str(int(plan.width)),
        "--height",
        str(int(plan.height)),
        "--sheet-scale",
        f"{_param_float(model, 'sheet_scale', _DEFAULT_SHEET_SCALE, min_value=0.01, max_value=100.0):.6g}",
        "--radius-scale",
        f"{_param_float(model, 'radius_scale', _DEFAULT_RADIUS_SCALE, min_value=0.01, max_value=100.0):.6g}",
        "--alpha",
        f"{_param_float(model, 'alpha', _DEFAULT_ALPHA, min_value=0.001, max_value=0.999):.6g}",
        "--inverse-scale",
        "0" if _param_bool(model, "disable_inverse_scale", False) else "1",
        "--max-splats",
        str(_param_int(model, "max_splats", 200000, min_value=1, max_value=5_000_000)),
    ]
    env = os.environ.copy()
    repo = str(_repo_root())
    env["PYTHONPATH"] = repo + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    completed = subprocess.run(
        [str(status.python), *args],
        cwd=str(_repo_root()),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Image-GS checkpoint conversion failed: {detail or completed.returncode}")
    return (completed.stdout or "").strip()


def write_manifest(node_item, plan: ImageGsRunPlan, ckpt_path: Path, preview_path: Optional[Path]) -> None:
    model = getattr(node_item, "model", None)
    payload = {
        "schema": _NODE_SCHEMA,
        "node": _node_name(node_item),
        "source": _source_key(model),
        "source_image": str(plan.image_path),
        "image_gs_checkpoint": str(ckpt_path),
        "splat_ply": str(plan.ply_path),
        "preview_image": str(preview_path or ""),
        "image_width": int(plan.width),
        "image_height": int(plan.height),
        "num_gaussians": _param_int(model, "num_gaussians", _DEFAULT_NUM_GAUSSIANS, min_value=100, max_value=2_000_000),
        "max_steps": _param_int(model, "max_steps", _DEFAULT_MAX_STEPS, min_value=1, max_value=2_000_000),
        "sheet_scale": _param_float(model, "sheet_scale", _DEFAULT_SHEET_SCALE, min_value=0.01, max_value=100.0),
        "radius_scale": _param_float(model, "radius_scale", _DEFAULT_RADIUS_SCALE, min_value=0.01, max_value=100.0),
        "alpha": _param_float(model, "alpha", _DEFAULT_ALPHA, min_value=0.001, max_value=0.999),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    plan.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    plan.manifest_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _existing_ply(model) -> str:
    for key in ("splat_ply", "path"):
        raw = _param_value(model, key).strip()
        if raw and Path(raw).exists() and Path(raw).suffix.lower() == ".ply":
            return raw
    return ""


def build_image_gs_splat_scene_asset(node_item, *, generate: bool = False) -> ImageGsSplatOutcome:
    model = getattr(node_item, "model", None)
    ply = _existing_ply(model)
    if not ply:
        return ImageGsSplatOutcome(None, "warning", "Image-GS splat has not been generated yet.")

    node_name = _node_name(node_item)
    source_image = _param_value(model, "source_image").strip()
    preview = _param_value(model, "preview_image").strip()
    asset = {
        "path": ply,
        "texture": "",
        "node": node_name,
        "kind": "image_gs_splat",
        "source_kind": "image_gs_splat",
        "ext": ".ply",
        "visible": True,
        "image_gs_splat": {
            "schema": _NODE_SCHEMA,
            "source": _source_key(model),
            "source_image": source_image,
            "preview_image": preview,
            "checkpoint": _param_value(model, "checkpoint").strip(),
            "manifest": _param_value(model, "manifest").strip(),
        },
    }
    return ImageGsSplatOutcome(asset, "ok", "Image-GS splat ready.", generated=False)


def build_ports(node_item) -> None:
    _ensure_param(node_item, "image_url", "")
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "source_image", "")
    _ensure_param(node_item, "image_gs_input", "")
    _ensure_param(node_item, "image_gs_exp_name", "")
    _ensure_param(node_item, "output_dir", "")
    _ensure_param(node_item, "splat_ply", "")
    _ensure_param(node_item, "checkpoint", "")
    _ensure_param(node_item, "preview_image", "")
    _ensure_param(node_item, "manifest", "")
    _ensure_param(node_item, "num_gaussians", str(_DEFAULT_NUM_GAUSSIANS))
    _ensure_param(node_item, "max_steps", str(_DEFAULT_MAX_STEPS))
    _ensure_param(node_item, "render_height", str(_DEFAULT_RENDER_HEIGHT))
    _ensure_param(node_item, "sheet_scale", str(_DEFAULT_SHEET_SCALE))
    _ensure_param(node_item, "radius_scale", str(_DEFAULT_RADIUS_SCALE))
    _ensure_param(node_item, "alpha", str(_DEFAULT_ALPHA))
    _ensure_param(node_item, "device", _DEFAULT_DEVICE)
    _ensure_param(node_item, "init_mode", "gradient")
    _ensure_param(node_item, "quantize", "0")
    _ensure_param(node_item, "vis_gaussians", "1")
    _ensure_param(node_item, "disable_inverse_scale", "0")
    _ensure_param(node_item, "max_splats", "200000")
    _ensure_param(node_item, "debug_log", "0")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [
            "image_url",
            "source",
            "path",
            "source_image",
            "image_gs_input",
            "image_gs_exp_name",
            "output_dir",
            "splat_ply",
            "checkpoint",
            "preview_image",
            "manifest",
            "num_gaussians",
            "max_steps",
            "render_height",
            "sheet_scale",
            "radius_scale",
            "alpha",
            "device",
            "init_mode",
            "quantize",
            "vis_gaussians",
            "disable_inverse_scale",
            "max_splats",
            "debug_log",
        ],
    )


def _button_style() -> str:
    return (
        "QPushButton{background:#16202f;color:#e2e8f0;border:1px solid #3b4b63;"
        "border-radius:4px;padding:5px 8px;font-size:11px;}"
        "QPushButton:hover{background:#1e2b3d;}"
        "QPushButton:disabled{color:#64748b;background:#111827;border-color:#243247;}"
    )


def _stop_button_style() -> str:
    return (
        "QPushButton{background:#3a1620;color:#fee2e2;border:1px solid #9f3346;"
        "border-radius:4px;padding:5px 8px;font-size:11px;}"
        "QPushButton:hover{background:#4a1d2a;border-color:#f87171;}"
        "QPushButton:disabled{color:#7f4b55;background:#1f1218;border-color:#4f2530;}"
    )


def _field_style() -> str:
    return (
        "QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox{background:#0f172a;color:#e2e8f0;"
        "border:1px solid #334155;border-radius:4px;padding:3px 5px;font-size:11px;}"
        "QSpinBox,QDoubleSpinBox{padding-right:18px;}"
        "QSpinBox::up-button,QDoubleSpinBox::up-button{"
        "subcontrol-origin:border;subcontrol-position:top right;width:16px;"
        "background:#2b5f8f;border-left:1px solid #7dd3fc;border-bottom:1px solid #5ea5d7;"
        "border-top-right-radius:4px;}"
        "QSpinBox::down-button,QDoubleSpinBox::down-button{"
        "subcontrol-origin:border;subcontrol-position:bottom right;width:16px;"
        "background:#2b5f8f;border-left:1px solid #7dd3fc;border-top:1px solid #1f4f78;"
        "border-bottom-right-radius:4px;}"
        "QSpinBox::up-button:hover,QDoubleSpinBox::up-button:hover,"
        "QSpinBox::down-button:hover,QDoubleSpinBox::down-button:hover{background:#4aa3df;}"
        "QSpinBox::up-arrow,QDoubleSpinBox::up-arrow{image:none;width:0;height:0;}"
        "QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{image:none;width:0;height:0;}"
        "QLabel{color:#cbd5e1;font-size:11px;}"
        "QCheckBox{color:#cbd5e1;font-size:11px;}"
    )


def _resolve_window(node_item):
    try:
        scene = node_item.scene()
        views = scene.views() if scene is not None else []
        if views:
            return views[0].window()
    except Exception:
        pass
    try:
        win = node_item.window()
        if win is not None:
            return win
    except Exception:
        pass
    try:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            active = app.activeWindow()
            if active is not None and active.isWindow():
                return active
            for widget in app.topLevelWidgets():
                try:
                    if widget.isWindow() and widget.isVisible():
                        return widget
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _process_running(proc) -> bool:
    try:
        return proc is not None and proc.state() != QtCore.QProcess.NotRunning
    except Exception:
        return False


def _node_setup_process(node) -> object:
    try:
        return getattr(node, "_image_gs_setup_process", None)
    except Exception:
        return None


def _start_image_gs_setup(parent, node, button, status_label) -> None:
    existing = _node_setup_process(node)
    if _process_running(existing):
        return
    script = image_gs_setup_script()
    if not script.exists():
        status_label.setStyleSheet("color:#ef4444;font-size:11px;")
        status_label.setText(f"Setup script not found: {script}")
        return
    root_python = Path(sys.executable)
    if not root_python.exists():
        status_label.setStyleSheet("color:#ef4444;font-size:11px;")
        status_label.setText("Current app Python was not found for setup.")
        return

    proc = QtCore.QProcess()
    command = ["cmd.exe", "/c", str(script), str(app_home_dir()), str(root_python)]
    _begin_debug_capture(node, "manual setup", command=command, cwd=_repo_root())
    setattr(node, "_image_gs_setup_process", proc)
    setattr(node, "_image_gs_setup_tail", "")
    button.setEnabled(False)
    status_label.setStyleSheet("color:#38bdf8;font-size:11px;")
    status_label.setText("Running Image-GS setup...")

    def _append_output() -> None:
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
        raw_text = "".join(chunks)
        _capture_debug_output(node, raw_text)
        tail = (str(getattr(node, "_image_gs_setup_tail", "") or "") + "\n" + raw_text.strip())[-12000:]
        setattr(node, "_image_gs_setup_tail", tail)
        lines = [line for line in tail.splitlines() if line.strip()]
        if lines:
            status_label.setStyleSheet("color:#38bdf8;font-size:11px;")
            status_label.setText(lines[-1][-180:])

    def _finished(exit_code: int, _exit_status) -> None:
        _append_output()
        button.setEnabled(True)
        setattr(node, "_image_gs_setup_process", None)
        if int(exit_code) != 0:
            tail = str(getattr(node, "_image_gs_setup_tail", "") or "").strip()
            detail = tail.splitlines()[-1] if tail else f"exit code {exit_code}"
            status_label.setStyleSheet("color:#ef4444;font-size:11px;")
            status_label.setText(f"Image-GS setup failed: {detail}")
            _finish_debug_capture(node, exit_code=exit_code, status_text=f"Image-GS setup failed: {detail}")
            return
        deps = image_gs_dependency_status(timeout=60)
        if deps.ready:
            status_label.setStyleSheet("color:#22c55e;font-size:11px;")
            status_label.setText("Image-GS dependencies are ready.")
            _finish_debug_capture(node, exit_code=exit_code, status_text="Image-GS dependencies are ready.")
        else:
            status_label.setStyleSheet("color:#ef4444;font-size:11px;")
            status_label.setText(deps.detail)
            _finish_debug_capture(node, exit_code=exit_code, status_text=deps.detail)
        try:
            proc.deleteLater()
        except Exception:
            pass

    proc.setWorkingDirectory(str(_repo_root()))
    env = QtCore.QProcessEnvironment.systemEnvironment()
    env.insert("QUBITMCP_HOME", str(app_home_dir()))
    proc.setProcessEnvironment(env)
    proc.readyReadStandardOutput.connect(_append_output)
    proc.readyReadStandardError.connect(_append_output)
    proc.finished.connect(_finished)
    proc.start(command[0], command[1:])
    if not proc.waitForStarted(5000):
        setattr(node, "_image_gs_setup_process", None)
        button.setEnabled(True)
        status_label.setStyleSheet("color:#ef4444;font-size:11px;")
        status_label.setText("Could not start Image-GS setup process.")
        _finish_debug_capture(node, exit_code=None, status_text="Could not start Image-GS setup process.")


def _infocard_locations(node, status) -> list[tuple[str, str]]:
    model = node
    name = str(getattr(node, "name", "") or "image_gs_splat").strip() or "image_gs_splat"
    output_dir = _param_value(model, "output_dir").strip() or str(_logs_dir() / _safe_stem(name))
    return [
        ("App home", str(app_home_dir())),
        ("Image-GS repo", str(getattr(status, "root", "") or image_gs_root())),
        ("Python venv", str(getattr(status, "python", "") or image_gs_python())),
        ("Setup script", str(image_gs_setup_script())),
        ("Output folder", output_dir),
        ("Splat PLY", _param_value(model, "splat_ply").strip() or "Not generated"),
        ("Checkpoint", _param_value(model, "checkpoint").strip() or "Not generated"),
        ("Manifest", _param_value(model, "manifest").strip() or "Not generated"),
    ]


def _make_infocard_value(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(str(text or ""))
    label.setWordWrap(True)
    label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    label.setMinimumWidth(320)
    try:
        label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    except Exception:
        pass
    label.setStyleSheet(
        "QLabel{color:#94a3b8;font-size:10px;background:#0f172a;"
        "border:1px solid #1e293b;border-radius:4px;padding:3px 5px;}"
    )
    return label


def _make_infocard_location_cell(title: str, value: str, parent=None) -> QtWidgets.QWidget:
    cell = QtWidgets.QWidget(parent)
    try:
        cell.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    except Exception:
        pass
    layout = QtWidgets.QVBoxLayout(cell)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    title_label = QtWidgets.QLabel(str(title or ""))
    title_label.setStyleSheet("QLabel{color:#cbd5e1;font-size:10px;font-weight:600;}")
    layout.addWidget(title_label)
    layout.addWidget(_make_infocard_value(value))
    return cell


def augment_infocard_footer(card, footer_layout) -> bool:
    node = getattr(card, "_node_ref", None)
    if node is None or str(getattr(node, "kind", "") or "").strip().lower() not in KIND_ALIASES:
        return False

    container = QtWidgets.QWidget(card)
    try:
        container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    except Exception:
        pass
    stack = QtWidgets.QVBoxLayout(container)
    stack.setContentsMargins(0, 0, 0, 0)
    stack.setSpacing(6)

    setup_btn = QtWidgets.QPushButton("Setup Image-GS")
    setup_btn.setToolTip("Install or repair the Image-GS runtime used by this node.")
    setup_btn.setStyleSheet(_button_style())
    setup_btn.setMinimumWidth(128)
    setup_btn.setMaximumWidth(156)

    button_row = QtWidgets.QHBoxLayout()
    button_row.setContentsMargins(0, 0, 0, 0)
    button_row.setSpacing(6)
    button_row.addWidget(setup_btn, 0, QtCore.Qt.AlignLeft)
    button_row.addStretch(1)

    status_label = QtWidgets.QLabel()
    status_label.setWordWrap(True)
    status_label.setStyleSheet("color:#94a3b8;font-size:11px;")
    runtime = image_gs_status()
    status_label.setText(
        "Status: Image-GS runtime is ready. Generate will verify dependencies."
        if runtime.ready else f"Status: {runtime.detail}"
    )
    if runtime.ready:
        status_label.setStyleSheet("color:#22c55e;font-size:11px;")
    setup_btn.clicked.connect(lambda: _start_image_gs_setup(card, node, setup_btn, status_label))

    existing = getattr(node, "_image_gs_setup_process", None)
    if _process_running(existing):
        setup_btn.setEnabled(False)
        status_label.setStyleSheet("color:#38bdf8;font-size:11px;")
        status_label.setText("Status: Image-GS setup is running...")

    locations = QtWidgets.QWidget(container)
    try:
        locations.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    except Exception:
        pass
    locations_layout = QtWidgets.QVBoxLayout(locations)
    locations_layout.setContentsMargins(0, 0, 0, 0)
    locations_layout.setSpacing(6)
    for title, value in _infocard_locations(node, runtime):
        locations_layout.addWidget(_make_infocard_location_cell(title, value, locations))
    locations_layout.addStretch(1)

    stack.addLayout(button_row)
    stack.addWidget(status_label)
    stack.addWidget(locations)
    footer_layout.addWidget(container, 1)
    return True


class _SpinArrowOverlay(QtWidgets.QWidget):
    def __init__(self, spinbox: QtWidgets.QAbstractSpinBox):
        super().__init__(spinbox)
        self._spinbox = spinbox
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.resize(16, max(20, spinbox.height()))
        self.show()

    def update_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = 16
        self.setGeometry(max(0, parent.width() - width - 1), 1, width, max(1, parent.height() - 2))
        self.raise_()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pen = QtGui.QPen(QtGui.QColor("#eef8ff"))
        pen.setWidth(2)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        painter.setPen(pen)
        w = max(1, self.width())
        h = max(1, self.height())
        mid_x = w // 2
        upper_y = max(5, h // 4 + 1)
        lower_y = min(h - 5, (h * 3) // 4 - 1)
        painter.drawLine(mid_x - 4, upper_y + 2, mid_x, upper_y - 2)
        painter.drawLine(mid_x, upper_y - 2, mid_x + 4, upper_y + 2)
        painter.drawLine(mid_x - 4, lower_y - 2, mid_x, lower_y + 2)
        painter.drawLine(mid_x, lower_y + 2, mid_x + 4, lower_y - 2)
        painter.end()


class _ArrowSpinBox(QtWidgets.QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._arrow_overlay = _SpinArrowOverlay(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrow_overlay.update_geometry()


class _ArrowDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._arrow_overlay = _SpinArrowOverlay(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrow_overlay.update_geometry()


class ImageGsSplatWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._proc: Optional[QtCore.QProcess] = None
        self._setup_proc: Optional[QtCore.QProcess] = None
        self._run_plan: Optional[ImageGsRunPlan] = None
        self._browse_dialog: Optional[QtWidgets.QFileDialog] = None
        self._browse_pending = False
        self._stop_requested = False
        self._tail = ""
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet(_field_style())

        self._url_edit = QtWidgets.QLineEdit()
        self._url_edit.setPlaceholderText("https://.../image.png or local image path")
        self._url_edit.setMinimumHeight(24)

        self._browse_btn = QtWidgets.QToolButton()
        self._browse_btn.setAutoRaise(True)
        self._browse_btn.setToolTip("Browse image")
        self._browse_btn.setFixedSize(26, 24)
        icon_path = _repo_root() / "icons" / "explorer_button_icon.png"
        if icon_path.exists():
            self._browse_btn.setIcon(QtGui.QIcon(str(icon_path)))
            self._browse_btn.setIconSize(QtCore.QSize(16, 16))
        else:
            self._browse_btn.setText("...")
        self._browse_btn.setStyleSheet(
            "QToolButton{background:#16202f;border:1px solid #3b4b63;border-radius:4px;}"
            "QToolButton:hover{background:#1e2b3d;border-color:#7dd3fc;}"
        )

        image_row = QtWidgets.QHBoxLayout()
        image_row.setContentsMargins(0, 0, 0, 0)
        image_row.setSpacing(4)
        image_row.addWidget(self._url_edit, 1)
        image_row.addWidget(self._browse_btn, 0)

        self._gauss_spin = _ArrowSpinBox()
        self._gauss_spin.setRange(100, 2_000_000)
        self._gauss_spin.setSingleStep(500)

        self._steps_spin = _ArrowSpinBox()
        self._steps_spin.setRange(1, 2_000_000)
        self._steps_spin.setSingleStep(100)

        self._height_spin = _ArrowSpinBox()
        self._height_spin.setRange(64, 12000)
        self._height_spin.setSingleStep(64)

        self._sheet_spin = _ArrowDoubleSpinBox()
        self._sheet_spin.setRange(0.01, 100.0)
        self._sheet_spin.setDecimals(2)
        self._sheet_spin.setSingleStep(0.1)

        self._radius_spin = _ArrowDoubleSpinBox()
        self._radius_spin.setRange(0.01, 100.0)
        self._radius_spin.setDecimals(2)
        self._radius_spin.setSingleStep(0.1)

        self._alpha_spin = _ArrowDoubleSpinBox()
        self._alpha_spin.setRange(0.001, 0.999)
        self._alpha_spin.setDecimals(3)
        self._alpha_spin.setSingleStep(0.025)

        self._init_combo = QtWidgets.QComboBox()
        self._init_combo.addItems(["gradient", "saliency", "random"])

        self._quantize_check = QtWidgets.QCheckBox("Quantize")
        self._vis_check = QtWidgets.QCheckBox("Points")

        for field in (
            self._gauss_spin,
            self._steps_spin,
            self._height_spin,
            self._sheet_spin,
            self._radius_spin,
            self._alpha_spin,
            self._init_combo,
        ):
            field.setFixedWidth(_CONTROL_W)
            if isinstance(field, QtWidgets.QAbstractSpinBox):
                field.setButtonSymbols(QtWidgets.QAbstractSpinBox.UpDownArrows)

        self._generate_btn = QtWidgets.QPushButton("Generate")
        self._view_btn = QtWidgets.QPushButton("View")
        self._folder_btn = QtWidgets.QPushButton("Folder")
        self._stop_btn = QtWidgets.QPushButton("Stop")
        for btn in (self._generate_btn, self._view_btn, self._folder_btn):
            btn.setStyleSheet(_button_style())
            btn.setMinimumHeight(25)
            btn.setMinimumWidth(72)
        self._stop_btn.setStyleSheet(_stop_button_style())
        self._stop_btn.setMinimumHeight(25)
        self._stop_btn.setMinimumWidth(72)
        self._stop_btn.setVisible(False)
        self._stop_btn.setEnabled(False)

        self._preview = QtWidgets.QLabel()
        self._preview.setMinimumHeight(_NODE_PREVIEW_H)
        self._preview.setMaximumHeight(_NODE_PREVIEW_H)
        self._preview.setAlignment(QtCore.Qt.AlignCenter)
        self._preview.setStyleSheet(
            "QLabel{background:#0b1120;border:1px solid #273449;border-radius:4px;color:#64748b;font-size:11px;}"
        )

        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(_STATUS_MIN_H)
        self._status.setMaximumHeight(_STATUS_MAX_H)
        self._status.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._status.setStyleSheet(
            "QLabel{background:#09111f;border:1px solid #1e293b;border-radius:4px;"
            "padding:4px 6px;color:#94a3b8;font-size:11px;}"
        )

        def _label(text: str) -> QtWidgets.QLabel:
            label = QtWidgets.QLabel(text)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            label.setMinimumWidth(42)
            return label

        form = QtWidgets.QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(4)
        form.setVerticalSpacing(4)
        form.addWidget(_label("Image"), 0, 0)
        form.addLayout(image_row, 0, 1, 1, 3)
        form.addWidget(_label("Splats"), 1, 0)
        form.addWidget(self._gauss_spin, 1, 1, QtCore.Qt.AlignLeft)
        form.addWidget(_label("Steps"), 1, 2)
        form.addWidget(self._steps_spin, 1, 3, QtCore.Qt.AlignLeft)
        form.addWidget(_label("Height"), 2, 0)
        form.addWidget(self._height_spin, 2, 1, QtCore.Qt.AlignLeft)
        form.addWidget(_label("Init"), 2, 2)
        form.addWidget(self._init_combo, 2, 3, QtCore.Qt.AlignLeft)
        form.addWidget(_label("Sheet"), 3, 0)
        form.addWidget(self._sheet_spin, 3, 1, QtCore.Qt.AlignLeft)
        form.addWidget(_label("Radius"), 3, 2)
        form.addWidget(self._radius_spin, 3, 3, QtCore.Qt.AlignLeft)
        form.addWidget(_label("Alpha"), 4, 0)
        form.addWidget(self._alpha_spin, 4, 1, QtCore.Qt.AlignLeft)
        form.addWidget(self._quantize_check, 5, 0, 1, 2)
        form.addWidget(self._vis_check, 5, 2, 1, 2)
        form.setColumnMinimumWidth(0, 42)
        form.setColumnMinimumWidth(1, _CONTROL_W)
        form.setColumnMinimumWidth(2, 42)
        form.setColumnMinimumWidth(3, _CONTROL_W)
        form.setColumnStretch(0, 0)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(2, 0)
        form.setColumnStretch(3, 1)

        actions = QtWidgets.QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        actions.addWidget(self._generate_btn)
        actions.addWidget(self._view_btn)
        actions.addWidget(self._folder_btn)
        actions.addWidget(self._stop_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self._preview)
        layout.addWidget(self._status)

        self._url_edit.editingFinished.connect(self._on_url_changed)
        self._browse_btn.clicked.connect(self._on_browse_clicked)
        self._gauss_spin.valueChanged.connect(self._on_gauss_changed)
        self._steps_spin.valueChanged.connect(self._on_steps_changed)
        self._height_spin.valueChanged.connect(self._on_height_changed)
        self._sheet_spin.valueChanged.connect(self._on_sheet_changed)
        self._radius_spin.valueChanged.connect(self._on_radius_changed)
        self._alpha_spin.valueChanged.connect(self._on_alpha_changed)
        self._init_combo.currentTextChanged.connect(self._on_init_changed)
        self._quantize_check.stateChanged.connect(self._on_quantize_changed)
        self._vis_check.stateChanged.connect(self._on_vis_changed)
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        self._view_btn.clicked.connect(self._on_view_clicked)
        self._folder_btn.clicked.connect(self._on_folder_clicked)
        self._stop_btn.clicked.connect(self._on_stop_clicked)

        self._sync_from_params()
        self._refresh_status()
        self._refresh_preview()
        QtCore.QTimer.singleShot(250, self._maybe_prompt_setup)

    def sizeHint(self):
        return QtCore.QSize(_NODE_BODY_MIN_W, _NODE_BODY_MIN_H)

    def minimumSizeHint(self):
        return QtCore.QSize(_NODE_BODY_MIN_W, _NODE_BODY_MIN_H)

    def _sync_from_params(self) -> None:
        model = getattr(self._node_item, "model", None)
        controls = [
            self._url_edit,
            self._gauss_spin,
            self._steps_spin,
            self._height_spin,
            self._sheet_spin,
            self._radius_spin,
            self._alpha_spin,
            self._init_combo,
            self._quantize_check,
            self._vis_check,
        ]
        for widget in controls:
            widget.blockSignals(True)
        try:
            self._url_edit.setText(_source_key(model))
            self._gauss_spin.setValue(_param_int(model, "num_gaussians", _DEFAULT_NUM_GAUSSIANS, min_value=100, max_value=2_000_000))
            self._steps_spin.setValue(_param_int(model, "max_steps", _DEFAULT_MAX_STEPS, min_value=1, max_value=2_000_000))
            self._height_spin.setValue(_param_int(model, "render_height", _DEFAULT_RENDER_HEIGHT, min_value=64, max_value=12000))
            self._sheet_spin.setValue(_param_float(model, "sheet_scale", _DEFAULT_SHEET_SCALE, min_value=0.01, max_value=100.0))
            self._radius_spin.setValue(_param_float(model, "radius_scale", _DEFAULT_RADIUS_SCALE, min_value=0.01, max_value=100.0))
            self._alpha_spin.setValue(_param_float(model, "alpha", _DEFAULT_ALPHA, min_value=0.001, max_value=0.999))
            init = _param_value(model, "init_mode").strip() or "gradient"
            idx = self._init_combo.findText(init)
            self._init_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self._quantize_check.setChecked(_param_bool(model, "quantize", False))
            self._vis_check.setChecked(_param_bool(model, "vis_gaussians", True))
        finally:
            for widget in controls:
                widget.blockSignals(False)

    def _set_status(self, text: str, tone: str = "muted") -> None:
        colors = {
            "ok": "#22c55e",
            "warn": "#f59e0b",
            "error": "#ef4444",
            "busy": "#38bdf8",
            "muted": "#94a3b8",
        }
        self._status.setStyleSheet(
            "QLabel{background:#09111f;border:1px solid #1e293b;border-radius:4px;"
            "padding:4px 6px;color:"
            + colors.get(tone, colors["muted"])
            + ";font-size:11px;}"
        )
        self._status.setText(text)
        self._resize_status_for_text()

    def _resize_status_for_text(self) -> None:
        text = self._status.text() or " "
        width = int(self._status.width())
        if width <= 24:
            width = max(120, int(self.width()) - 12)
        text_width = max(80, width - 14)
        try:
            rect = self._status.fontMetrics().boundingRect(
                QtCore.QRect(0, 0, text_width, 2000),
                QtCore.Qt.TextWordWrap | QtCore.Qt.AlignLeft,
                text,
            )
        except Exception:
            rect = self._status.fontMetrics().boundingRect(text)
        wanted = int(rect.height()) + _STATUS_TEXT_PAD_H
        wanted = max(_STATUS_MIN_H, min(_STATUS_MAX_H, wanted))
        if abs(int(self._status.height()) - wanted) > 1:
            self._status.setMinimumHeight(wanted)
            self._status.setMaximumHeight(wanted)
            self._status.updateGeometry()
            self._resize_node_body_to_layout()

    def _resize_node_body_to_layout(self) -> None:
        layout = self.layout()
        if layout is not None:
            try:
                layout.activate()
            except Exception:
                pass
            hint = layout.sizeHint()
        else:
            hint = self.minimumSizeHint()
        body_height = max(_NODE_BODY_MIN_H, int(hint.height()))
        try:
            proxy = self.graphicsProxyWidget()
        except Exception:
            proxy = None
        if proxy is None:
            self.setMinimumHeight(body_height)
            return
        body_width = max(_NODE_BODY_MIN_W, int(proxy.size().width()) if proxy.size().width() else int(self.width()))
        current_h = float(proxy.size().height())
        if float(body_height) > current_h + 0.5:
            proxy.resize(body_width, body_height)
            self.resize(body_width, body_height)
            node_item = self._node_item
            required_height = float(proxy.pos().y()) + float(body_height) + 10.0
            try:
                if float(getattr(node_item, "height", 0.0) or 0.0) < required_height:
                    try:
                        node_item.prepareGeometryChange()
                    except Exception:
                        pass
                    node_item.height = required_height
            except Exception:
                pass

    def _refresh_status(self) -> None:
        status = image_gs_status()
        outcome = build_image_gs_splat_scene_asset(self._node_item, generate=False)
        self._view_btn.setEnabled(bool(outcome.asset))
        self._folder_btn.setEnabled(bool(_param_value(getattr(self._node_item, "model", None), "output_dir").strip() or _output_dir(self._node_item).exists()))
        if not status.ready:
            self._set_status(status.detail, "warn")
            return
        if outcome.status == "ok":
            self._set_status(outcome.detail, "ok")
        elif outcome.status == "warning":
            self._set_status(outcome.detail, "warn")
        else:
            self._set_status(outcome.detail, "error")

    def _prompt_for_setup(self, status, *, force: bool = False) -> bool:
        global _SETUP_PROMPT_SHOWN
        model = getattr(self._node_item, "model", None)
        if self._setup_proc is not None or self._proc is not None or _process_running(_node_setup_process(model)):
            return False
        if self._browse_dialog_open():
            QtCore.QTimer.singleShot(500, self._maybe_prompt_setup)
            return False
        if not force and _SETUP_PROMPT_SHOWN:
            return False
        if not force and bool(getattr(model, "_image_gs_setup_prompted", False)):
            return False
        try:
            setattr(model, "_image_gs_setup_prompted", True)
        except Exception:
            pass
        _SETUP_PROMPT_SHOWN = True
        detail = str(getattr(status, "detail", "") or "Image-GS is not ready.")
        parent = _resolve_window(self._node_item) or self.window() or self
        result = QtWidgets.QMessageBox.question(
            parent,
            "Image-GS setup required",
            detail + "\n\nRun Image-GS setup now? This can take a while and needs network access.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if result != QtWidgets.QMessageBox.Yes:
            self._set_status(detail, "warn")
            return False
        self._on_setup_clicked()
        return True

    def _maybe_prompt_setup(self) -> None:
        if self._browse_dialog_open():
            QtCore.QTimer.singleShot(500, self._maybe_prompt_setup)
            return
        status = image_gs_status()
        if status.ready:
            return
        self._prompt_for_setup(status, force=False)

    def _browse_dialog_open(self) -> bool:
        dlg = self._browse_dialog
        if dlg is None:
            return False
        try:
            return bool(dlg.isVisible())
        except RuntimeError:
            self._browse_dialog = None
            return False
        except Exception:
            return False

    def _refresh_preview(self) -> None:
        model = getattr(self._node_item, "model", None)
        raw = _param_value(model, "preview_image").strip() or _param_value(model, "source_image").strip()
        if not raw or not Path(raw).exists():
            self._preview.setText("No preview")
            self._preview.setPixmap(QtGui.QPixmap())
            return
        pixmap = QtGui.QPixmap(raw)
        if pixmap.isNull():
            self._preview.setText("Preview unavailable")
            self._preview.setPixmap(QtGui.QPixmap())
            return
        size = self._preview.size()
        if size.width() <= 8 or size.height() <= 8:
            size = QtCore.QSize(280, _NODE_PREVIEW_H)
        scaled = pixmap.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self._preview.setText("")
        self._preview.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_preview()
        self._resize_status_for_text()

    def _mark_dirty(self) -> None:
        self._set_status("Generation settings changed. Generate again to refresh the splat.", "warn")
        self._view_btn.setEnabled(bool(build_image_gs_splat_scene_asset(self._node_item, generate=False).asset))

    def _on_url_changed(self):
        value = self._url_edit.text().strip()
        _set_param(self._node_item, "image_url", value, notify_scene=False)
        _set_param(self._node_item, "source", value, notify_scene=False)
        self._mark_dirty()

    def _on_browse_clicked(self, *_args):
        if self._browse_dialog_open():
            try:
                self._browse_dialog.raise_()
                self._browse_dialog.activateWindow()
            except Exception:
                pass
            return
        if self._browse_pending:
            return
        self._browse_pending = True
        QtCore.QTimer.singleShot(0, self._open_browse_dialog)

    def _open_browse_dialog(self):
        self._browse_pending = False
        start = ""
        current = self._url_edit.text().strip()
        if current and not url_parse.urlparse(current).scheme:
            try:
                current_path = Path(current).expanduser()
                start = str(current_path.parent if current_path.parent.exists() else current_path)
            except Exception:
                start = ""
        parent = _resolve_window(self._node_item) or self.window() or self
        dlg = QtWidgets.QFileDialog(
            parent,
            "Select source image",
            start,
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.exr *.bmp *.webp);;All Files (*.*)",
        )
        dlg.setAcceptMode(QtWidgets.QFileDialog.AcceptOpen)
        dlg.setFileMode(QtWidgets.QFileDialog.ExistingFile)
        self._browse_dialog = dlg
        path = ""
        try:
            if dlg.exec() == QtWidgets.QDialog.Accepted:
                files = dlg.selectedFiles()
                if files:
                    path = files[0]
        finally:
            self._browse_dialog = None
            try:
                dlg.deleteLater()
            except Exception:
                pass
        if not path:
            return
        self._url_edit.setText(path)
        _set_param(self._node_item, "image_url", path, notify_scene=False)
        _set_param(self._node_item, "source", path, notify_scene=False)
        _set_param(self._node_item, "source_image", path, notify_scene=False)
        _set_param(self._node_item, "preview_image", path, notify_scene=False)
        self._refresh_preview()
        self._mark_dirty()

    def _on_gauss_changed(self, value: int):
        _set_param(self._node_item, "num_gaussians", str(int(value)), notify_scene=False)
        self._mark_dirty()

    def _on_steps_changed(self, value: int):
        _set_param(self._node_item, "max_steps", str(int(value)), notify_scene=False)
        self._mark_dirty()

    def _on_height_changed(self, value: int):
        _set_param(self._node_item, "render_height", str(int(value)), notify_scene=False)
        self._mark_dirty()

    def _on_sheet_changed(self, value: float):
        _set_param(self._node_item, "sheet_scale", f"{float(value):.3f}", notify_scene=False)
        self._mark_dirty()

    def _on_radius_changed(self, value: float):
        _set_param(self._node_item, "radius_scale", f"{float(value):.3f}", notify_scene=False)
        self._mark_dirty()

    def _on_alpha_changed(self, value: float):
        _set_param(self._node_item, "alpha", f"{float(value):.3f}", notify_scene=False)
        self._mark_dirty()

    def _on_init_changed(self, text: str):
        _set_param(self._node_item, "init_mode", str(text or "gradient"), notify_scene=False)
        self._mark_dirty()

    def _on_quantize_changed(self, _state: int):
        _set_param(self._node_item, "quantize", "1" if self._quantize_check.isChecked() else "0", notify_scene=False)
        self._mark_dirty()

    def _on_vis_changed(self, _state: int):
        _set_param(self._node_item, "vis_gaussians", "1" if self._vis_check.isChecked() else "0", notify_scene=False)
        self._mark_dirty()

    def _notify_scene_params_changed(self) -> None:
        try:
            scene = self._node_item.scene()
            signal = getattr(scene, "paramChanged", None)
            model = getattr(self._node_item, "model", None)
            if signal is not None and model is not None:
                signal.emit(model.name, list(getattr(model, "params", []) or []))
        except Exception:
            pass

    def _set_busy_border(self, active: bool) -> None:
        setter = getattr(self._node_item, "setBusyState", None)
        if callable(setter):
            try:
                setter(bool(active), "image-gs" if active else "")
            except Exception:
                pass

    def _append_process_output(self) -> None:
        proc = self._proc
        if proc is None:
            return
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
        raw_text = "".join(chunks)
        _capture_debug_output(self._node_item, raw_text)
        text = raw_text.strip()
        if text:
            self._tail = (self._tail + "\n" + text)[-12000:]
            last = [line for line in self._tail.splitlines() if line.strip()]
            if last:
                self._set_status(last[-1][-180:], "busy")

    def _append_setup_output(self) -> None:
        proc = self._setup_proc
        if proc is None:
            return
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
        raw_text = "".join(chunks)
        _capture_debug_output(self._node_item, raw_text)
        text = raw_text.strip()
        if text:
            self._tail = (self._tail + "\n" + text)[-12000:]
            last = [line for line in self._tail.splitlines() if line.strip()]
            if last:
                self._set_status(last[-1][-180:], "busy")

    def _set_setup_controls_busy(self, busy: bool) -> None:
        self._generate_btn.setEnabled(not busy)
        self._browse_btn.setEnabled(not busy)
        self._set_busy_border(bool(busy))

    def _on_setup_clicked(self):
        model = getattr(self._node_item, "model", None)
        if self._setup_proc is not None or self._proc is not None or _process_running(_node_setup_process(model)):
            return
        script = image_gs_setup_script()
        if not script.exists():
            self._set_status(f"Image-GS setup script not found: {script}", "error")
            return
        root_python = Path(sys.executable)
        if not root_python.exists():
            self._set_status("Current app Python was not found for setup.", "error")
            return
        self._tail = ""
        command = ["cmd.exe", "/c", str(script), str(app_home_dir()), str(root_python)]
        _begin_debug_capture(self._node_item, "setup", command=command, cwd=_repo_root())
        self._set_setup_controls_busy(True)
        self._set_status("Running Image-GS setup...", "busy")

        proc = QtCore.QProcess()
        try:
            setattr(model, "_image_gs_setup_process", proc)
        except Exception:
            pass
        proc.setWorkingDirectory(str(_repo_root()))
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("QUBITMCP_HOME", str(app_home_dir()))
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(self._append_setup_output)
        proc.readyReadStandardError.connect(self._append_setup_output)
        proc.finished.connect(self._on_setup_finished)
        self._setup_proc = proc
        proc.start(command[0], command[1:])
        if not proc.waitForStarted(5000):
            self._setup_proc = None
            try:
                setattr(model, "_image_gs_setup_process", None)
            except Exception:
                pass
            self._set_setup_controls_busy(False)
            self._set_status("Could not start Image-GS setup process.", "error")
            _finish_debug_capture(self._node_item, exit_code=None, status_text="Could not start Image-GS setup process.")

    def _on_setup_finished(self, exit_code: int, _exit_status):
        self._append_setup_output()
        proc = self._setup_proc
        self._setup_proc = None
        model = getattr(self._node_item, "model", None)
        try:
            setattr(model, "_image_gs_setup_process", None)
        except Exception:
            pass
        if proc is not None:
            proc.deleteLater()
        self._set_setup_controls_busy(False)
        if int(exit_code) != 0:
            detail = self._tail.strip().splitlines()[-1] if self._tail.strip() else f"exit code {exit_code}"
            self._set_status(f"Image-GS setup failed: {detail}", "error")
            _finish_debug_capture(self._node_item, exit_code=exit_code, status_text=f"Image-GS setup failed: {detail}")
            return
        deps = image_gs_dependency_status(timeout=60)
        if deps.ready:
            self._set_status("Image-GS dependencies are ready.", "ok")
            _finish_debug_capture(self._node_item, exit_code=exit_code, status_text="Image-GS dependencies are ready.")
        else:
            self._set_status(deps.detail, "error")
            _finish_debug_capture(self._node_item, exit_code=exit_code, status_text=deps.detail)

    def _on_generate_clicked(self):
        model = getattr(self._node_item, "model", None)
        if self._proc is not None or self._setup_proc is not None or _process_running(_node_setup_process(model)):
            if _process_running(_node_setup_process(model)):
                self._set_status("Image-GS setup is still running.", "busy")
            return
        status = image_gs_status()
        if not status.ready:
            self._set_status(status.detail, "error")
            return
        self._set_status("Checking Image-GS dependencies...", "busy")
        try:
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass
        deps = image_gs_dependency_status(timeout=60)
        if not deps.ready:
            self._prompt_for_setup(deps, force=True)
            return
        try:
            plan = prepare_image_gs_run(self._node_item)
            args = image_gs_command_args(self._node_item, plan)
        except Exception as exc:
            self._set_status(str(exc), "error")
            return
        self._run_plan = plan
        self._tail = ""
        command = [str(status.python), *args]
        _begin_debug_capture(self._node_item, "generate", command=command, cwd=status.root)
        self._stop_requested = False
        self._generate_btn.setEnabled(False)
        self._browse_btn.setEnabled(False)
        self._view_btn.setEnabled(False)
        self._stop_btn.setVisible(True)
        self._stop_btn.setEnabled(True)
        self._set_busy_border(True)
        self._set_status("Starting Image-GS...", "busy")
        _debug_log(getattr(self._node_item, "model", None), "start", args=args, plan=plan)

        proc = QtCore.QProcess(self)
        proc.setWorkingDirectory(str(status.root))
        env = QtCore.QProcessEnvironment.systemEnvironment()
        repo = str(_repo_root())
        bundled_gsplat = str(status.root / "gsplat")
        old_py_path = env.value("PYTHONPATH", "")
        py_parts = [bundled_gsplat, repo]
        if old_py_path:
            py_parts.append(old_py_path)
        env.insert("PYTHONPATH", os.pathsep.join(py_parts))
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(self._append_process_output)
        proc.readyReadStandardError.connect(self._append_process_output)
        proc.finished.connect(self._on_process_finished)
        self._proc = proc
        proc.start(str(status.python), args)
        if not proc.waitForStarted(5000):
            self._proc = None
            self._stop_requested = False
            self._generate_btn.setEnabled(True)
            self._browse_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._stop_btn.setVisible(False)
            self._set_busy_border(False)
            self._set_status("Could not start Image-GS Python process.", "error")
            _finish_debug_capture(self._node_item, exit_code=None, status_text="Could not start Image-GS Python process.")

    def _on_stop_clicked(self):
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.state() == QtCore.QProcess.NotRunning:
                return
        except RuntimeError:
            return
        self._stop_requested = True
        self._stop_btn.setEnabled(False)
        self._set_status("Stopping Image-GS...", "warn")
        _capture_debug_output(self._node_item, "\nImage-GS stop requested by user.\n")
        try:
            proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            return
        QtCore.QTimer.singleShot(2500, lambda p=proc: self._force_stop_process(p))

    def _force_stop_process(self, proc: QtCore.QProcess) -> None:
        if self._proc is not proc:
            return
        try:
            if proc.state() == QtCore.QProcess.NotRunning:
                return
            _capture_debug_output(self._node_item, "\nImage-GS process did not stop; killing it.\n")
            self._set_status("Force stopping Image-GS...", "warn")
            proc.kill()
        except RuntimeError:
            return
        except Exception:
            return

    def _on_process_finished(self, exit_code: int, _exit_status):
        self._append_process_output()
        proc = self._proc
        self._proc = None
        was_stopped = self._stop_requested
        self._stop_requested = False
        self._generate_btn.setEnabled(True)
        self._browse_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setVisible(False)
        self._set_busy_border(False)
        status = image_gs_status()
        plan = self._run_plan
        if proc is not None:
            proc.deleteLater()
        if was_stopped:
            detail = "Image-GS generation stopped."
            self._set_status(detail, "warn")
            self._view_btn.setEnabled(bool(build_image_gs_splat_scene_asset(self._node_item, generate=False).asset))
            self._folder_btn.setEnabled(bool(_param_value(getattr(self._node_item, "model", None), "output_dir").strip() or _output_dir(self._node_item).exists()))
            _debug_log(getattr(self._node_item, "model", None), "stopped", exit_code=int(exit_code), tail=self._tail)
            _finish_debug_capture(self._node_item, exit_code=exit_code, status_text=detail)
            return
        if plan is None:
            self._set_status("Image-GS process finished without a run plan.", "error")
            _finish_debug_capture(self._node_item, exit_code=exit_code, status_text="Image-GS process finished without a run plan.")
            return
        if int(exit_code) != 0:
            detail = self._tail.strip().splitlines()[-1] if self._tail.strip() else f"exit code {exit_code}"
            self._set_status(f"Image-GS failed: {detail}", "error")
            _debug_log(getattr(self._node_item, "model", None), "failed", exit_code=int(exit_code), tail=self._tail)
            _finish_debug_capture(self._node_item, exit_code=exit_code, status_text=f"Image-GS failed: {detail}")
            self._refresh_status()
            return
        try:
            ckpt = find_latest_checkpoint(status.root, plan.exp_name)
            if ckpt is None:
                raise RuntimeError("Image-GS finished but no checkpoint was found.")
            preview = find_latest_preview(status.root, plan.exp_name) or plan.image_path
            conversion = convert_checkpoint_to_ply(self._node_item, plan, ckpt)
            write_manifest(self._node_item, plan, ckpt, preview)
            _set_param(self._node_item, "checkpoint", str(ckpt), notify_scene=False)
            _set_param(self._node_item, "preview_image", str(preview), notify_scene=False)
            _set_param(self._node_item, "splat_ply", str(plan.ply_path), notify_scene=False)
            _set_param(self._node_item, "manifest", str(plan.manifest_path), notify_scene=False)
            _set_param(self._node_item, "path", str(plan.ply_path), notify_scene=True)
            self._notify_scene_params_changed()
            self._refresh_preview()
            self._set_status(conversion or "Image-GS splat generated.", "ok")
            _finish_debug_capture(self._node_item, exit_code=exit_code, status_text=conversion or "Image-GS splat generated.")
        except Exception as exc:
            self._set_status(str(exc), "error")
            _debug_log(getattr(self._node_item, "model", None), "convert_failed", error=repr(exc), tail=self._tail)
            _finish_debug_capture(self._node_item, exit_code=exit_code, status_text=f"Image-GS conversion failed: {exc}")
        self._refresh_status()

    def _on_view_clicked(self):
        outcome = build_image_gs_splat_scene_asset(self._node_item, generate=False)
        if not isinstance(outcome.asset, dict):
            self._refresh_status()
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            self._set_status("Scene view is not available.", "error")
            return
        try:
            handler([dict(outcome.asset)], frame=True)
        except TypeError:
            handler([dict(outcome.asset)])

    def _on_folder_clicked(self):
        out = _output_dir(self._node_item)
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(out)))
        except Exception:
            self._set_status(str(out), "muted")


def render_node_body(node_item, y_cursor: int) -> int:
    inset_x = 6
    inset_top = 2
    inset_bottom = 10
    body = ImageGsSplatWidget(node_item)
    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    required_width = float(hint.width() + (inset_x * 2))
    try:
        if float(getattr(node_item, "width", 0.0) or 0.0) < required_width:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.width = required_width
    except Exception:
        pass
    body.setMinimumSize(hint)

    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(inset_x, y_cursor + inset_top)
    body_height = max(_NODE_BODY_MIN_H, int(hint.height()))
    proxy.resize(max(int(hint.width()), int(node_item.width) - (inset_x * 2)), body_height)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    required_height = float(y_cursor + inset_top + body_height + inset_bottom)
    try:
        if float(getattr(node_item, "height", 0.0) or 0.0) < required_height:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = required_height
    except Exception:
        pass
    return y_cursor + inset_top + body_height + inset_bottom


IMAGE_GS_SPLAT_SPEC = Spec(
    stripe_color="#67e8f9",
    augment_infocard_footer=augment_infocard_footer,
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "KIND_ALIASES",
    "ImageGsSplatOutcome",
    "IMAGE_GS_SPLAT_SPEC",
    "augment_infocard_footer",
    "build_image_gs_splat_scene_asset",
    "build_ports",
    "image_gs_debug_report_text",
    "render_node_body",
    "show_image_gs_debug_report",
    "write_image_gs_debug_report",
]
