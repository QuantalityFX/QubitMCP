from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".exr"}
_CODEC_LABELS = {
    "h264": "H.264",
    "h265": "H.265",
}
_CODEC_LIBS = {
    "h264": "libx264",
    "h265": "libx265",
}


@dataclass
class SequenceInfo:
    input_pattern: Path
    start_number: int
    frame_count: int
    padding: int
    example_path: Path | None = None


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if isinstance(p, dict) and (p.get("name") or "").strip().lower() == key:
            return p.get("value", "") or ""
    return ""


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
    key = name.strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    entry = None
    for p in params:
        if isinstance(p, dict) and (p.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = p
            break
    if entry is None:
        entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(entry)
    raw = str(entry.get("value") or "")
    hidden = {tok.strip().lower() for tok in raw.split(",") if tok.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_param_value(node_item, name: str, value: str, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is not None and _param_value(model, name) == str(value or ""):
        return
    try:
        node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
    except Exception:
        pass


def _workflow_dir_from_ref(raw) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        p = Path(text).expanduser()
        return p.parent if p.suffix else p
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
    try:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            candidates.extend(_workflow_refs_from_widget(widget))
    except Exception:
        pass

    for raw in candidates:
        workflow_dir = _workflow_dir_from_ref(raw)
        if workflow_dir is not None:
            return workflow_dir
    return None


def _temp_video_dir() -> Path:
    return Path(tempfile.gettempdir()) / "EchoGraph" / "videos"


def _is_temp_video_path(raw: str) -> bool:
    text = _clean_path_text(raw)
    if not text:
        return False
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            return False
        resolved_path = path.resolve()
        resolved_temp = _temp_video_dir().resolve()
        return resolved_path == resolved_temp or resolved_temp in resolved_path.parents
    except Exception:
        return False


def _is_generated_output_path(node_item, raw: str) -> bool:
    text = _clean_path_text(raw)
    if not text:
        return True
    if _is_temp_video_path(text):
        return True
    try:
        output = _resolve_path(node_item, text)
        default = _default_output_path(node_item)
        return output.resolve() == default.resolve()
    except Exception:
        return False


def _video_dir(node_item) -> Path:
    workflow_dir = _workflow_dir_for_node(node_item)
    out = (workflow_dir / "videos") if workflow_dir is not None else _temp_video_dir()
    out.mkdir(parents=True, exist_ok=True)
    return out


def _default_output_path(node_item) -> Path:
    model = getattr(node_item, "model", None)
    name = str(getattr(model, "name", "") or "sequence").strip() or "sequence"
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "sequence"
    return _video_dir(node_item) / f"{safe}.mp4"


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
    try:
        aw = QtWidgets.QApplication.activeWindow()
        if aw is not None and aw.isWindow():
            return aw
    except Exception:
        pass
    return None


def _clean_path_text(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _resolve_path(node_item, raw: str) -> Path:
    text = _clean_path_text(raw)
    p = Path(text).expanduser()
    if p.is_absolute():
        return p
    base = _workflow_dir_for_node(node_item)
    if base is not None:
        return (base / p).resolve()
    return p


def _is_image_path(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXTS


def _natural_sort_key(path: Path):
    parts = re.split(r"(\d+)", path.name.lower())
    key = []
    for token in parts:
        if token.isdigit():
            key.append((1, int(token)))
        else:
            key.append((0, token))
    return key


def _pattern_to_regex(pattern_name: str):
    match = re.search(r"%0?(\d*)d", pattern_name)
    if match:
        width = int(match.group(1) or "1")
        regex_src = re.escape(pattern_name)
        regex_src = regex_src.replace(re.escape(match.group(0)), rf"(\d{{{width},}})")
        return re.compile(rf"^{regex_src}$", flags=re.IGNORECASE), width
    if "####" in pattern_name:
        regex_src = re.escape(pattern_name).replace(r"\#\#\#\#", r"(\d{4,})")
        return re.compile(rf"^{regex_src}$", flags=re.IGNORECASE), 4
    if "{frame}" in pattern_name:
        regex_src = re.escape(pattern_name).replace(r"\{frame\}", r"(\d+)")
        return re.compile(rf"^{regex_src}$", flags=re.IGNORECASE), 1
    return None, 0


def _sequence_from_pattern(path: Path) -> SequenceInfo | None:
    rx, padding = _pattern_to_regex(path.name)
    if rx is None:
        return None
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        return None
    matches = []
    try:
        for child in parent.iterdir():
            if not child.is_file() or not _is_image_path(child):
                continue
            m = rx.match(child.name)
            if m:
                matches.append((int(m.group(1)), child))
    except Exception:
        return None
    if not matches:
        return None
    matches.sort(key=lambda row: row[0])
    start_number = int(matches[0][0])
    ffmpeg_name = path.name
    if "####" in ffmpeg_name:
        ffmpeg_name = ffmpeg_name.replace("####", "%04d")
    elif "{frame}" in ffmpeg_name:
        ffmpeg_name = ffmpeg_name.replace("{frame}", "%d")
    return SequenceInfo(
        input_pattern=parent / ffmpeg_name,
        start_number=start_number,
        frame_count=len(matches),
        padding=padding,
        example_path=matches[0][1],
    )


def _sequence_from_numeric_file(path: Path) -> SequenceInfo | None:
    match = re.match(r"^(.*?)(\d+)(\.[^.]+)$", path.name, flags=re.IGNORECASE)
    if not match:
        return None
    prefix, digits, ext = match.groups()
    rx = re.compile(rf"^{re.escape(prefix)}(\d+){re.escape(ext)}$", flags=re.IGNORECASE)
    matches = []
    try:
        for child in path.parent.iterdir():
            if not child.is_file() or not _is_image_path(child):
                continue
            m = rx.match(child.name)
            if m:
                matches.append((int(m.group(1)), child))
    except Exception:
        return None
    if not matches:
        return None
    matches.sort(key=lambda row: row[0])
    padding = len(digits)
    return SequenceInfo(
        input_pattern=path.parent / f"{prefix}%0{padding}d{ext}",
        start_number=int(matches[0][0]),
        frame_count=len(matches),
        padding=padding,
        example_path=matches[0][1],
    )


def _sequence_from_directory(path: Path) -> SequenceInfo | None:
    try:
        files = sorted(
            [p for p in path.iterdir() if p.is_file() and _is_image_path(p)],
            key=_natural_sort_key,
        )
    except Exception:
        return None
    for child in files:
        info = _sequence_from_numeric_file(child)
        if info is not None:
            return info
    return None


def _resolve_sequence(node_item, raw: str) -> SequenceInfo:
    text = _clean_path_text(raw)
    if not text:
        raise ValueError("Choose a source image sequence first.")
    path = _resolve_path(node_item, text)
    if path.exists() and path.is_dir():
        info = _sequence_from_directory(path)
    elif path.exists() and path.is_file():
        info = _sequence_from_numeric_file(path)
    else:
        info = _sequence_from_pattern(path)
    if info is None:
        raise ValueError(
            "Could not resolve a numbered image sequence. Use a folder, first frame, "
            "or pattern such as frame_%05d.png, frame_####.png, or frame_{frame}.png."
        )
    if info.frame_count < 1:
        raise ValueError("No sequence frames were found.")
    return info


def _connected_sequence_source(node_item, *, apply_postprocess: bool = False):
    try:
        from nodes.post_process.spec import find_sequence_source  # type: ignore

        return find_sequence_source(
            node_item,
            apply_postprocess=apply_postprocess,
            require_connection=True,
        )
    except Exception:
        return None


def _source_input_connected(node_item) -> bool:
    try:
        from nodes.post_process.spec import source_input_connected  # type: ignore

        return bool(source_input_connected(node_item))
    except Exception:
        return _connected_sequence_source(node_item, apply_postprocess=False) is not None


def _effective_sequence_info(node_item, fallback_source: str, *, apply_postprocess: bool = False) -> tuple[SequenceInfo, str, bool]:
    source = _connected_sequence_source(node_item, apply_postprocess=apply_postprocess)
    if source is not None and (source.text or "").strip():
        info = _resolve_sequence(source.owner_item, source.text)
        return info, str(source.label or "connected source"), True
    return _resolve_sequence(node_item, fallback_source), "", False


def _normalize_codec(raw: str) -> str:
    key = (raw or "").strip().lower().replace(".", "").replace("-", "")
    if key in {"h265", "hevc", "libx265"}:
        return "h265"
    return "h264"


def _parse_fps(raw: str) -> float:
    try:
        fps = float(str(raw or "").strip())
    except Exception:
        fps = 24.0
    if fps <= 0.0:
        fps = 24.0
    return max(1.0, min(240.0, fps))


def _normalize_bitrate(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if not re.match(r"^\d+(?:\.\d+)?[kKmM]?$", text):
        raise ValueError("Bitrate must look like 8000k, 12M, or be left blank.")
    return text


def _output_path(node_item, raw: str) -> Path:
    text = _clean_path_text(raw)
    if text and not (_is_temp_video_path(text) and _workflow_dir_for_node(node_item) is not None):
        out = _resolve_path(node_item, text)
    else:
        out = _default_output_path(node_item)
    if out.suffix.lower() != ".mp4":
        out = out.with_suffix(".mp4")
    return out


def _find_ffmpeg_exe() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg  # type: ignore

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).exists():
            return str(bundled)
    except Exception:
        pass
    return "ffmpeg"


def _build_ffmpeg_command(info: SequenceInfo, output: Path, fps: float, codec: str, bitrate: str) -> list[str]:
    ffmpeg = _find_ffmpeg_exe()
    codec_key = _normalize_codec(codec)
    cmd = [
        ffmpeg,
        "-y",
        "-framerate",
        f"{fps:g}",
        "-start_number",
        str(int(info.start_number)),
        "-i",
        str(info.input_pattern),
        "-c:v",
        _CODEC_LIBS[codec_key],
        "-pix_fmt",
        "yuv420p",
    ]
    if codec_key == "h265":
        cmd.extend(["-tag:v", "hvc1"])
    if bitrate:
        cmd.extend(["-b:v", bitrate])
    else:
        cmd.extend(["-crf", "20" if codec_key == "h264" else "26"])
    cmd.extend(["-movflags", "+faststart", str(output)])
    return cmd


def _run_sequence_to_mp4(info: SequenceInfo, output: Path, fps: float, codec: str, bitrate: str) -> subprocess.CompletedProcess:
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = _build_ffmpeg_command(info, output, fps, codec, bitrate)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def build_ports(node_item) -> None:
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "output", "")
    _ensure_param(node_item, "codec", "h264")
    _ensure_param(node_item, "fps", "24")
    _ensure_param(node_item, "bitrate", "12M")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        ["source", "output", "codec", "fps", "bitrate"],
    )
    try:
        node_item.ensure_input("source")
    except Exception:
        pass


class SequenceToMP4Widget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._busy = False
        self._last_status_source = None
        self._status_refresh_timer = QtCore.QTimer(self)
        self._status_refresh_timer.setSingleShot(True)
        self._status_refresh_timer.timeout.connect(self._refresh_status)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status, 0)

        self._source_edit = self._line_edit("Folder, first frame, or frame_%05d.png")
        source_row = QtWidgets.QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(4)
        source_row.addWidget(self._source_edit, 1)
        self._source_file_btn = self._button("File", 46)
        self._source_dir_btn = self._button("Dir", 42)
        self._source_file_btn.clicked.connect(self._on_source_file)
        self._source_dir_btn.clicked.connect(self._on_source_dir)
        source_row.addWidget(self._source_file_btn, 0)
        source_row.addWidget(self._source_dir_btn, 0)
        layout.addLayout(source_row, 0)

        self._output_edit = self._line_edit("Output .mp4 path")
        output_row = QtWidgets.QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(4)
        output_row.addWidget(self._output_edit, 1)
        self._output_btn = self._button("...", 34)
        self._output_btn.setToolTip("Choose output MP4 path")
        self._output_btn.clicked.connect(self._on_output_browse)
        output_row.addWidget(self._output_btn, 0)
        layout.addLayout(output_row, 0)

        settings_row = QtWidgets.QHBoxLayout()
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.setSpacing(4)
        self._codec_combo = QtWidgets.QComboBox()
        self._codec_combo.addItem(_CODEC_LABELS["h264"], "h264")
        self._codec_combo.addItem(_CODEC_LABELS["h265"], "h265")
        self._codec_combo.setMinimumHeight(24)
        self._codec_combo.setStyleSheet(
            "QComboBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
        )
        settings_row.addWidget(self._codec_combo, 1)

        self._fps_spin = QtWidgets.QDoubleSpinBox()
        self._fps_spin.setRange(1.0, 240.0)
        self._fps_spin.setDecimals(0)
        self._fps_spin.setSingleStep(1.0)
        self._fps_spin.setSuffix(" fps")
        self._fps_spin.setMinimumHeight(24)
        self._fps_spin.setStyleSheet(
            "QDoubleSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
        )
        settings_row.addWidget(self._fps_spin, 1)
        layout.addLayout(settings_row, 0)

        bitrate_row = QtWidgets.QHBoxLayout()
        bitrate_row.setContentsMargins(0, 0, 0, 0)
        bitrate_row.setSpacing(4)
        label = QtWidgets.QLabel("Bitrate")
        label.setStyleSheet("color:#94a3b8;font-size:10px;")
        bitrate_row.addWidget(label, 0)
        self._bitrate_edit = self._line_edit("12M")
        bitrate_row.addWidget(self._bitrate_edit, 1)
        layout.addLayout(bitrate_row, 0)

        self._convert_btn = self._button("Convert to MP4", 0)
        self._convert_btn.setStyleSheet(
            "QPushButton{background:#0f766e;color:#f8fafc;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#0d9488;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._convert_btn.clicked.connect(self._on_convert)
        layout.addWidget(self._convert_btn, 0)

        self._source_edit.editingFinished.connect(self._commit_controls)
        self._output_edit.editingFinished.connect(self._commit_controls)
        self._bitrate_edit.editingFinished.connect(self._commit_controls)
        self._codec_combo.currentIndexChanged.connect(self._on_settings_changed)
        self._fps_spin.valueChanged.connect(self._on_settings_changed)

        self._ensure_scene()
        self._sync_controls_from_params()
        self._schedule_status_refresh(0)

    def sizeHint(self):
        return QtCore.QSize(240, 176)

    def _line_edit(self, placeholder: str) -> QtWidgets.QLineEdit:
        edit = QtWidgets.QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMinimumWidth(0)
        edit.setMinimumHeight(24)
        edit.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        edit.setStyleSheet(
            "QLineEdit{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
            "QLineEdit:disabled{background:#111827;color:#64748b;border-color:#334155;}"
        )
        return edit

    def _button(self, text: str, width: int) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(text)
        if width > 0:
            btn.setFixedWidth(width)
        btn.setMinimumHeight(24)
        btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:2px 6px;}"
            "QPushButton:hover{background:#273449;}"
        )
        return btn

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(self._on_scene_param_changed)
            except Exception:
                pass
        self._scene_connected = True

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            before_source = (self._source_edit.text() or "").strip()
            self._sync_controls_from_params()
            after_source = (self._source_edit.text() or "").strip()
            if after_source != before_source or after_source != self._last_status_source:
                self._schedule_status_refresh()

    def _sync_controls_from_params(self):
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        source = (_param_value(model, "source") or "").strip()
        output = (_param_value(model, "output") or "").strip()
        codec = _normalize_codec(_param_value(model, "codec"))
        fps = _parse_fps(_param_value(model, "fps"))
        bitrate = (_param_value(model, "bitrate") or "").strip()
        connected_source = _connected_sequence_source(self._node_item, apply_postprocess=False)
        if output and _is_temp_video_path(output) and _workflow_dir_for_node(self._node_item) is not None:
            output = ""
            _set_param_value(self._node_item, "output", "", notify_scene=False)

        try:
            self._source_edit.blockSignals(True)
            self._source_edit.setText(
                f"Connected: {connected_source.label}" if connected_source is not None else source
            )
            self._source_edit.setEnabled(connected_source is None)
            self._source_file_btn.setEnabled(connected_source is None)
            self._source_dir_btn.setEnabled(connected_source is None)
        finally:
            self._source_edit.blockSignals(False)
        try:
            self._output_edit.blockSignals(True)
            self._output_edit.setText(output or str(_default_output_path(self._node_item)))
        finally:
            self._output_edit.blockSignals(False)
        try:
            self._bitrate_edit.blockSignals(True)
            self._bitrate_edit.setText(bitrate)
        finally:
            self._bitrate_edit.blockSignals(False)
        try:
            self._fps_spin.blockSignals(True)
            self._fps_spin.setValue(fps)
        finally:
            self._fps_spin.blockSignals(False)
        try:
            self._codec_combo.blockSignals(True)
            idx = self._codec_combo.findData(codec)
            self._codec_combo.setCurrentIndex(max(0, idx))
        finally:
            self._codec_combo.blockSignals(False)

    def _on_settings_changed(self, *_args):
        self._commit_controls()

    def _commit_controls(self):
        model = getattr(self._node_item, "model", None)
        old_source = (_param_value(model, "source") or "").strip() if model is not None else ""
        source = old_source if _source_input_connected(self._node_item) else (self._source_edit.text() or "").strip()
        codec = str(self._codec_combo.currentData() or "h264")
        fps = f"{float(self._fps_spin.value()):g}"
        output = (self._output_edit.text() or "").strip()
        if _is_generated_output_path(self._node_item, output):
            output = ""
        _set_param_value(self._node_item, "source", source, notify_scene=False)
        _set_param_value(self._node_item, "output", output, notify_scene=False)
        _set_param_value(self._node_item, "codec", codec, notify_scene=False)
        _set_param_value(self._node_item, "fps", fps, notify_scene=False)
        _set_param_value(self._node_item, "bitrate", (self._bitrate_edit.text() or "").strip(), notify_scene=False)
        if source != old_source:
            self._schedule_status_refresh()

    def _schedule_status_refresh(self, delay_ms: int = 250):
        try:
            self._status_refresh_timer.start(max(0, int(delay_ms)))
        except Exception:
            self._refresh_status()

    def _refresh_status(self):
        connected_source = _connected_sequence_source(self._node_item, apply_postprocess=False)
        source = (self._source_edit.text() or "").strip()
        status_key = f"connected:{connected_source.label}" if connected_source is not None else source
        self._last_status_source = status_key
        if connected_source is None and not source:
            self._status.setText("Choose a numbered image sequence.")
            return
        try:
            info, label, connected = _effective_sequence_info(self._node_item, source, apply_postprocess=False)
            prefix = f"Connected from {label}: " if connected else ""
            self._status.setText(f"{prefix}{info.frame_count} frame(s), start {info.start_number}, pattern {info.input_pattern.name}")
        except Exception as exc:
            self._status.setText(str(exc))

    def _on_source_file(self):
        if _source_input_connected(self._node_item):
            return
        parent = _dialog_parent(self._node_item) or self
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            "Select Sequence Frame",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.exr);;All Files (*.*)",
        )
        if not path:
            return
        self._source_edit.setText(path)
        self._commit_controls()

    def _on_source_dir(self):
        if _source_input_connected(self._node_item):
            return
        parent = _dialog_parent(self._node_item) or self
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Select Sequence Folder", "")
        if not path:
            return
        self._source_edit.setText(path)
        self._commit_controls()

    def _on_output_browse(self):
        parent = _dialog_parent(self._node_item) or self
        start = _output_path(self._node_item, (self._output_edit.text() or "").strip())
        try:
            start.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent,
            "Choose MP4 Output Path",
            str(start),
            "MP4 Video (*.mp4)",
        )
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path = f"{path}.mp4"
        self._output_edit.setText(path)
        self._commit_controls()

    def _show_popup(self, icon, text: str, details: str = "") -> None:
        parent = _dialog_parent(self._node_item) or self
        box = QtWidgets.QMessageBox(parent)
        box.setWindowTitle("Sequence to MP4")
        box.setIcon(icon)
        box.setText(str(text or ""))
        try:
            box.setTextFormat(QtCore.Qt.PlainText)
        except Exception:
            pass
        if details:
            try:
                box.setDetailedText(str(details))
            except Exception:
                pass
        box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        box.setStyleSheet(
            "QMessageBox{background:#0f1216;color:#e6edf3;}"
            "QLabel{color:#e6edf3;}"
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:4px 12px;min-width:72px;}"
            "QPushButton:hover{background:#273449;}"
        )
        try:
            box.exec()
        except Exception:
            box.exec_()

    def _set_busy(self, busy: bool):
        self._busy = bool(busy)
        self._convert_btn.setEnabled(not self._busy)
        self._convert_btn.setText("Converting..." if self._busy else "Convert to MP4")

    def _on_convert(self):
        self._commit_controls()
        source = (self._source_edit.text() or "").strip()
        codec = str(self._codec_combo.currentData() or "h264")
        fps = float(self._fps_spin.value())
        try:
            bitrate = _normalize_bitrate((self._bitrate_edit.text() or "").strip())
            info, _label, _connected = _effective_sequence_info(
                self._node_item,
                source,
                apply_postprocess=True,
            )
            output = _output_path(self._node_item, (self._output_edit.text() or "").strip())
        except Exception as exc:
            self._show_popup(QtWidgets.QMessageBox.Warning, str(exc))
            self._refresh_status()
            return

        self._set_busy(True)
        try:
            result = _run_sequence_to_mp4(info, output, fps, codec, bitrate)
            details = "\n".join(
                [
                    "Command:",
                    subprocess.list2cmdline(_build_ffmpeg_command(info, output, fps, codec, bitrate)),
                    "",
                    "stdout:",
                    result.stdout or "",
                    "",
                    "stderr:",
                    result.stderr or "",
                ]
            )
            if result.returncode != 0:
                self._show_popup(QtWidgets.QMessageBox.Critical, "ffmpeg failed to create the MP4.", details)
                return
            if not output.exists():
                self._show_popup(QtWidgets.QMessageBox.Critical, "ffmpeg completed but the MP4 was not found.", details)
                return
            _set_param_value(self._node_item, "output", str(output), notify_scene=True)
            self._output_edit.setText(str(output))
            label = _CODEC_LABELS.get(_normalize_codec(codec), "H.264")
            self._status.setText(f"Created {output.name} ({label}, {fps:g} fps).")
            self._show_popup(QtWidgets.QMessageBox.Information, f"Created MP4:\n{output}", details)
        except FileNotFoundError:
            self._show_popup(
                QtWidgets.QMessageBox.Critical,
                "ffmpeg was not found. Run setup.bat to install the bundled project ffmpeg runtime.",
            )
        except Exception as exc:
            self._show_popup(QtWidgets.QMessageBox.Critical, "Conversion failed.", str(exc))
        finally:
            self._set_busy(False)
            self._refresh_status()


def _ensure_body_space(node_item, body_h: int) -> None:
    try:
        old_h = float(getattr(node_item, "height", 0.0) or 0.0)
        min_h = float(body_h) + 10.0
        if old_h < min_h:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = min_h
    except Exception:
        pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = SequenceToMP4Widget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    w = int(getattr(node_item, "width", 220))
    try:
        body.setMinimumWidth(w)
        body.setMaximumWidth(w)
        proxy.setMinimumWidth(w)
        proxy.setMaximumWidth(w)
    except Exception:
        pass
    h = body.sizeHint().height()
    proxy.resize(w, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    _ensure_body_space(node_item, int(y_cursor) + h)
    return y_cursor + h


SEQUENCE_TO_MP4_SPEC = Spec(
    stripe_color="#2563eb",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
