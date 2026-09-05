from __future__ import annotations

import re
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

try:
    from PySide6 import QtMultimedia
except Exception:
    try:
        from PySide2 import QtMultimedia  # type: ignore
    except Exception:
        QtMultimedia = None  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".exr"}
_VIDEO_EXTS = {".mp4"}
_DEFAULT_FPS = 24.0


def _load_icon(name: str) -> QtGui.QIcon:
    try:
        path = Path(__file__).resolve().parents[2] / "icons" / name
        if path.is_file():
            return QtGui.QIcon(str(path))
    except Exception:
        pass
    return QtGui.QIcon()


class _JumpScrubSlider(QtWidgets.QSlider):
    jumpDragStarted = QtCore.Signal()
    jumpDragFinished = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self._jump_drag_active = False
        self.setMouseTracking(True)

    @staticmethod
    def _event_pos(ev) -> QtCore.QPoint:
        try:
            if hasattr(ev, "position"):
                pos = ev.position()
                if pos is not None:
                    return pos.toPoint()
        except Exception:
            pass
        try:
            if hasattr(ev, "pos"):
                pos = ev.pos()
                if pos is not None:
                    return pos
        except Exception:
            pass
        return QtCore.QPoint(0, 0)

    def _pixel_x_to_value(self, x_pos: int) -> int | None:
        try:
            opt = QtWidgets.QStyleOptionSlider()
            self.initStyleOption(opt)
            style = self.style()
            groove = style.subControlRect(
                QtWidgets.QStyle.CC_Slider,
                opt,
                QtWidgets.QStyle.SC_SliderGroove,
                self,
            )
            span = int(
                max(
                    1,
                    style.pixelMetric(
                        QtWidgets.QStyle.PM_SliderSpaceAvailable,
                        opt,
                        self,
                    ),
                )
            )
            slider_len = int(
                max(
                    1,
                    style.pixelMetric(
                        QtWidgets.QStyle.PM_SliderLength,
                        opt,
                        self,
                    ),
                )
            )
            pos = int(round(float(x_pos) - float(groove.left()) - (float(slider_len) * 0.5)))
            pos = max(0, min(span, pos))
            val = int(
                QtWidgets.QStyle.sliderValueFromPosition(
                    int(self.minimum()),
                    int(self.maximum()),
                    int(pos),
                    int(span),
                    bool(getattr(opt, "upsideDown", False)),
                )
            )
            return max(int(self.minimum()), min(int(self.maximum()), int(val)))
        except Exception:
            return None

    def mousePressEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(ev)
        pt = self._event_pos(ev)
        try:
            opt = QtWidgets.QStyleOptionSlider()
            self.initStyleOption(opt)
            style = self.style()
            handle = style.subControlRect(
                QtWidgets.QStyle.CC_Slider,
                opt,
                QtWidgets.QStyle.SC_SliderHandle,
                self,
            )
            if handle is not None and handle.contains(pt):
                self._jump_drag_active = False
                return super().mousePressEvent(ev)
        except Exception:
            pass
        target = self._pixel_x_to_value(int(pt.x()))
        if target is None:
            self._jump_drag_active = False
            return super().mousePressEvent(ev)
        try:
            self.setSliderPosition(int(target))
        except Exception:
            pass
        try:
            self.setValue(int(target))
        except Exception:
            pass
        self._jump_drag_active = True
        try:
            self.setSliderDown(True)
        except Exception:
            pass
        try:
            self.jumpDragStarted.emit()
        except Exception:
            pass
        try:
            ev.accept()
        except Exception:
            pass
        return

    def mouseMoveEvent(self, ev):
        if not bool(getattr(self, "_jump_drag_active", False)):
            return super().mouseMoveEvent(ev)
        try:
            if not bool(ev.buttons() & QtCore.Qt.LeftButton):
                self._jump_drag_active = False
                try:
                    self.setSliderDown(False)
                except Exception:
                    pass
                return super().mouseMoveEvent(ev)
        except Exception:
            pass
        pt = self._event_pos(ev)
        target = self._pixel_x_to_value(int(pt.x()))
        if target is not None:
            try:
                self.setSliderPosition(int(target))
            except Exception:
                pass
            try:
                self.setValue(int(target))
            except Exception:
                pass
        try:
            ev.accept()
        except Exception:
            pass
        return

    def mouseReleaseEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton:
            return super().mouseReleaseEvent(ev)
        if not bool(getattr(self, "_jump_drag_active", False)):
            return super().mouseReleaseEvent(ev)
        pt = self._event_pos(ev)
        target = self._pixel_x_to_value(int(pt.x()))
        if target is not None:
            try:
                self.setSliderPosition(int(target))
            except Exception:
                pass
            try:
                self.setValue(int(target))
            except Exception:
                pass
        self._jump_drag_active = False
        try:
            self.setSliderDown(False)
        except Exception:
            pass
        try:
            self.jumpDragFinished.emit()
        except Exception:
            pass
        try:
            ev.accept()
        except Exception:
            pass
        return


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if (p.get("name") or "").strip().lower() == key:
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
        if (p.get("name") or "").strip().lower() == "__ui_hidden_params":
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
    try:
        node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
    except Exception:
        pass


def _workflow_dir_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    workflow_path = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                workflow_path = getattr(win, "_current_path", None)
        except Exception:
            pass
    if not workflow_path:
        workflow_path = getattr(scene, "_filename", None) if scene is not None else None
    if not workflow_path:
        return None
    try:
        p = Path(workflow_path)
        return p.parent if p.suffix else p
    except Exception:
        return None


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


def _edge_port(edge) -> str:
    name = (
        getattr(edge, "dst_port_name", None)
        or getattr(edge, "dst_label", None)
        or getattr(edge, "dst_name", None)
        or ""
    )
    return str(name or "").strip().lower()


def _ordered_in_edges(node_item):
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


def _looks_like_media_text(text: str) -> bool:
    raw = str(text or "").strip().strip('"').strip("'")
    if not raw:
        return False
    suffix = Path(raw).suffix.lower()
    if suffix in (_VIDEO_EXTS | _IMAGE_EXTS):
        return True
    if any(token in raw for token in ("*", "####", "{frame}", "%0")):
        return True
    try:
        path = Path(raw).expanduser()
        return path.exists()
    except Exception:
        return False


def _connected_path_from_graph(node_item) -> str:
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return ""
    edges = _ordered_in_edges(node_item)
    named = [edge for edge in edges if _edge_port(edge) == "path"]
    if named:
        edges = named
    path_param_names = ("mp4_path", "video_path", "path", "output", "output_path")
    for edge in edges:
        src = getattr(edge, "src", None)
        model = getattr(src, "model", None)
        if model is None:
            continue
        for param_name in path_param_names:
            value = (_param_value(model, param_name) or "").strip()
            if value:
                return value
        info = str(getattr(model, "info", "") or "").strip()
        if _looks_like_media_text(info):
            return info
        try:
            text = str(scene.resolve_text_value(src) or "").strip()
        except Exception:
            text = ""
        for line in text.splitlines():
            candidate = line.strip().strip('"').strip("'")
            if _looks_like_media_text(candidate):
                return candidate
    return ""


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


def _sequence_from_numeric_file(path: Path) -> list[Path]:
    m = re.match(r"^(.*?)(\d+)(\.[^.]+)$", path.name, flags=re.IGNORECASE)
    if not m:
        return [path]
    prefix = m.group(1)
    ext = m.group(3)
    rx = re.compile(rf"^{re.escape(prefix)}(\d+){re.escape(ext)}$", flags=re.IGNORECASE)
    out = []
    try:
        for child in path.parent.iterdir():
            if not child.is_file():
                continue
            if not _is_image_path(child):
                continue
            if rx.match(child.name):
                out.append(child)
    except Exception:
        return [path]
    if not out:
        return [path]
    return sorted(out, key=_natural_sort_key)


def _sequence_from_pattern(parent: Path, pattern_name: str) -> list[Path]:
    if not parent.exists() or not parent.is_dir():
        return []
    regex_src = None
    if any(ch in pattern_name for ch in "*?[]"):
        try:
            matches = [
                p
                for p in parent.glob(pattern_name)
                if p.is_file() and _is_image_path(p)
            ]
            return sorted(matches, key=_natural_sort_key)
        except Exception:
            return []
    if "####" in pattern_name:
        regex_src = re.escape(pattern_name).replace(r"\#\#\#\#", r"(\d+)")
    elif "{frame}" in pattern_name:
        regex_src = re.escape(pattern_name).replace(r"\{frame\}", r"(\d+)")
    else:
        m = re.search(r"%0(\d+)d", pattern_name)
        if m:
            token = re.escape(m.group(0))
            width = max(1, int(m.group(1)))
            regex_src = re.escape(pattern_name).replace(token, rf"(\d{{{width}}})")
    if not regex_src:
        return []
    rx = re.compile(rf"^{regex_src}$", flags=re.IGNORECASE)
    out = []
    try:
        for child in parent.iterdir():
            if not child.is_file():
                continue
            if not _is_image_path(child):
                continue
            if rx.match(child.name):
                out.append(child)
    except Exception:
        return []
    return sorted(out, key=_natural_sort_key)


def _resolve_source_path(node_item, text: str) -> Path:
    path = Path(text).expanduser()
    if not path.is_absolute():
        base = _workflow_dir_for_node(node_item)
        if base is not None:
            path = base / path
    return path


def _resolve_image_sequence(node_item, text: str) -> list[Path]:
    raw = (text or "").strip()
    if not raw:
        return []
    path = _resolve_source_path(node_item, raw)
    try:
        if path.is_dir():
            files = [p for p in path.iterdir() if p.is_file() and _is_image_path(p)]
            return sorted(files, key=_natural_sort_key)
    except Exception:
        pass
    if path.exists() and path.is_file() and _is_image_path(path):
        return _sequence_from_numeric_file(path)
    return _sequence_from_pattern(path.parent, path.name)


class VideoPlayerWidget(QtWidgets.QWidget):
    _PREVIEW_H = 116

    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._loaded_path = None
        self._connected_path = ""

        self._mode = "none"
        self._seq_paths: list[Path] = []
        self._seq_index = 0

        self._preview_image = QtGui.QImage()

        self._seq_timer = QtCore.QTimer(self)
        self._seq_timer.timeout.connect(self._on_sequence_tick)

        self._player = None
        self._video_sink = None
        self._audio_output = None
        self._video_loop_set = False
        self._video_playing = False
        self._audio_muted = False
        self._scrub_dragging = False
        self._scrub_updating = False
        self._resume_after_scrub = False
        self._play_icon = _load_icon("PlayButton_icon.png")
        self._stop_icon = _load_icon("StopButton_icon.png")
        self._sound_on_icon = _load_icon("Sound_On_Icon.png")
        self._sound_off_icon = _load_icon("Sound_Off_Icon.png")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        layout.addWidget(self._status, 0)

        row1 = QtWidgets.QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        self._path_edit = QtWidgets.QLineEdit()
        self._path_edit.setPlaceholderText("Image sequence or .mp4 path")
        self._path_edit.editingFinished.connect(self._on_path_committed)
        row1.addWidget(self._path_edit, 1)
        self._browse_btn = QtWidgets.QPushButton("Browse")
        self._browse_btn.setFixedWidth(70)
        self._browse_btn.clicked.connect(self._on_browse_clicked)
        row1.addWidget(self._browse_btn, 0)
        layout.addLayout(row1, 0)

        self._preview = QtWidgets.QLabel("No media loaded")
        self._preview.setAlignment(QtCore.Qt.AlignCenter)
        self._preview.setMinimumHeight(72)
        self._preview.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._preview.setStyleSheet(
            "QLabel{background:#0b1220;color:#94a3b8;border:1px solid #334155;border-radius:4px;}"
        )
        layout.addWidget(self._preview, 1)

        row2 = QtWidgets.QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        self._play_btn = QtWidgets.QToolButton()
        self._play_btn.setFixedSize(28, 28)
        self._play_btn.setIconSize(QtCore.QSize(18, 18))
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._on_play_clicked)
        row2.addWidget(self._play_btn, 0)
        self._sound_btn = QtWidgets.QToolButton()
        self._sound_btn.setFixedSize(28, 28)
        self._sound_btn.setIconSize(QtCore.QSize(18, 18))
        self._sound_btn.setEnabled(False)
        self._sound_btn.clicked.connect(self._on_sound_clicked)
        row2.addWidget(self._sound_btn, 0)
        self._scrub_slider = _JumpScrubSlider(self)
        self._scrub_slider.setRange(0, 0)
        self._scrub_slider.setEnabled(False)
        self._scrub_slider.setTracking(True)
        self._scrub_slider.sliderPressed.connect(self._on_scrub_pressed)
        self._scrub_slider.sliderMoved.connect(self._on_scrub_moved)
        self._scrub_slider.sliderReleased.connect(self._on_scrub_released)
        self._scrub_slider.valueChanged.connect(self._on_scrub_value_changed)
        if hasattr(self._scrub_slider, "jumpDragStarted"):
            self._scrub_slider.jumpDragStarted.connect(self._on_scrub_pressed)
        if hasattr(self._scrub_slider, "jumpDragFinished"):
            self._scrub_slider.jumpDragFinished.connect(self._on_scrub_released)
        self._apply_scrub_slider_style()
        row2.addWidget(self._scrub_slider, 1)
        layout.addLayout(row2, 0)

        self._set_play_button(False)
        self._set_sound_button(self._audio_muted)
        self._ensure_scene()
        self._sync_from_params(force=True)

    def sizeHint(self):
        return QtCore.QSize(240, 188)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_preview_pixmap()

    def _ensure_scene(self):
        if self._scene is None:
            self._scene = self._node_item.scene()
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

    def _on_scene_links_changed(self, *_args):
        self._sync_from_params(force=True)

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._sync_from_params(force=False)

    def _sync_from_params(self, force: bool = False):
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        connected_path = _connected_path_from_graph(self._node_item)
        self._connected_path = connected_path
        path_text = connected_path or (_param_value(model, "path") or "").strip()
        if path_text != (self._path_edit.text() or "").strip():
            try:
                self._path_edit.blockSignals(True)
                self._path_edit.setText(path_text)
            finally:
                self._path_edit.blockSignals(False)
        self._path_edit.setEnabled(not bool(connected_path))
        self._browse_btn.setEnabled(not bool(connected_path))
        self._path_edit.setToolTip("Driven by connected path input." if connected_path else "")
        self._load_media(path_text, force=force)

    def _set_status(self, text: str):
        self._status.setText(str(text or ""))

    def _on_path_committed(self):
        if self._connected_path:
            return
        text = (self._path_edit.text() or "").strip()
        _set_param_value(self._node_item, "path", text, notify_scene=True)
        self._load_media(text, force=True)

    def _on_browse_clicked(self):
        if self._connected_path:
            return
        current = (self._path_edit.text() or "").strip()
        if current:
            start_path = _resolve_source_path(self._node_item, current)
        else:
            start_path = _workflow_dir_for_node(self._node_item) or Path.home()
        start_dir = start_path
        try:
            if start_path.is_file():
                start_dir = start_path.parent
        except Exception:
            pass
        parent = _dialog_parent(self._node_item) or self
        chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            "Select Video or Sequence Frame",
            str(start_dir),
            "Video/Image (*.mp4 *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.exr);;All Files (*.*)",
        )
        if not chosen:
            return
        self._path_edit.setText(chosen)
        self._on_path_committed()

    def _load_media(self, text: str, force: bool = False):
        key = (text or "").strip()
        if not force and key == self._loaded_path:
            return
        self._loaded_path = key
        self._stop_playback()
        self._mode = "none"
        self._seq_paths = []
        self._seq_index = 0
        self._play_btn.setEnabled(False)
        self._sound_btn.setEnabled(False)
        self._scrub_slider.setEnabled(False)
        self._set_scrub_range(0, 0)
        self._set_scrub_value(0)
        self._set_play_button(False)
        self._set_sound_button(self._audio_muted)
        self._preview_image = QtGui.QImage()
        self._preview.setPixmap(QtGui.QPixmap())
        if not key:
            self._preview.setText("No media loaded")
            self._set_status("Paste a path or browse to a .mp4 or image sequence.")
            return

        source = _resolve_source_path(self._node_item, key)
        try:
            is_video = source.is_file() and source.suffix.lower() in _VIDEO_EXTS
        except Exception:
            is_video = False
        if is_video:
            if self._load_video_source(source):
                self._mode = "video"
                self._play_btn.setEnabled(True)
                self._sound_btn.setEnabled(True)
                self._scrub_slider.setEnabled(True)
                self._preview.setText("Press Play")
                self._set_status(f"Video loaded: {source.name}")
                self._sync_video_scrub_range()
            else:
                self._preview.setText("MP4 preview unavailable")
                self._set_status("This Qt build cannot preview .mp4 files in-node.")
            return

        seq = _resolve_image_sequence(self._node_item, key)
        if not seq:
            self._preview.setText("Path not found")
            self._set_status("No readable image sequence or .mp4 found.")
            return

        self._mode = "sequence"
        self._seq_paths = seq
        self._seq_index = 0
        self._set_scrub_range(0, max(0, len(seq) - 1))
        self._set_scrub_value(0)
        self._scrub_slider.setEnabled(True)
        self._show_sequence_frame(0)
        self._play_btn.setEnabled(True)
        self._set_status(f"Sequence loaded: {len(seq)} frame(s)")

    def _set_play_button(self, playing: bool):
        self._video_playing = bool(playing)
        icon = self._stop_icon if self._video_playing else self._play_icon
        if not icon.isNull():
            self._play_btn.setIcon(icon)
            self._play_btn.setText("")
        else:
            self._play_btn.setText("Pause" if self._video_playing else "Play")
        self._play_btn.setToolTip("Pause" if self._video_playing else "Play")

    def _set_sound_button(self, muted: bool):
        self._audio_muted = bool(muted)
        icon = self._sound_off_icon if self._audio_muted else self._sound_on_icon
        if not icon.isNull():
            self._sound_btn.setIcon(icon)
            self._sound_btn.setText("")
        else:
            self._sound_btn.setText("Off" if self._audio_muted else "On")
        self._sound_btn.setToolTip("Sound Off" if self._audio_muted else "Sound On")

    def _apply_scrub_slider_style(self):
        try:
            self._scrub_slider.setMinimumHeight(20)
        except Exception:
            pass
        self._scrub_slider.setStyleSheet(
            "QSlider{min-height:20px;}"
            "QSlider:focus{outline:none;}"
            "QSlider::groove:horizontal{"
            "height:4px;border-radius:2px;background:#3f4752;}"
            "QSlider::sub-page:horizontal{"
            "background:#b8c0cc;border-radius:2px;}"
            "QSlider::add-page:horizontal{"
            "background:#4b5563;border-radius:2px;}"
            "QSlider::handle:horizontal{"
            "background:#d8e0eb;border:1px solid #9ba4b3;border-radius:1px;"
            "width:4px;height:16px;margin:-6px 0;}"
            "QSlider::handle:horizontal:hover{background:#e2e8f0;}"
            "QSlider::handle:horizontal:pressed{background:#f1f5f9;}"
        )

    def _set_scrub_value(self, value: int):
        try:
            self._scrub_updating = True
            self._scrub_slider.setValue(int(value))
        except Exception:
            pass
        finally:
            self._scrub_updating = False

    def _set_scrub_range(self, minimum: int, maximum: int):
        mn = int(min(minimum, maximum))
        mx = int(max(minimum, maximum))
        try:
            self._scrub_updating = True
            self._scrub_slider.setRange(mn, mx)
        except Exception:
            pass
        finally:
            self._scrub_updating = False

    def _format_time(self, ms: int) -> str:
        total_sec = max(0, int(ms) // 1000)
        hh = total_sec // 3600
        mm = (total_sec % 3600) // 60
        ss = total_sec % 60
        if hh > 0:
            return f"{hh}:{mm:02d}:{ss:02d}"
        return f"{mm}:{ss:02d}"

    def _apply_audio_mute(self):
        if self._audio_output is not None:
            try:
                self._audio_output.setMuted(bool(self._audio_muted))
            except Exception:
                pass
            try:
                if hasattr(self._audio_output, "setVolume"):
                    self._audio_output.setVolume(1.0)
            except Exception:
                pass
        if self._player is not None:
            try:
                if hasattr(self._player, "setMuted"):
                    self._player.setMuted(bool(self._audio_muted))
            except Exception:
                pass
            try:
                if hasattr(self._player, "setVolume"):
                    self._player.setVolume(0 if self._audio_muted else 100)
            except Exception:
                pass
        self._set_sound_button(self._audio_muted)

    def _stop_playback(self):
        try:
            self._seq_timer.stop()
        except Exception:
            pass
        self._video_playing = False
        if self._player is not None:
            try:
                self._player.pause()
            except Exception:
                pass

    def _frame_interval_ms(self) -> int:
        try:
            fps = float(_DEFAULT_FPS)
        except Exception:
            fps = 24.0
        fps = max(1.0, min(240.0, fps))
        return max(1, int(round(1000.0 / fps)))

    def _on_play_clicked(self):
        if self._mode == "sequence":
            if not self._seq_paths:
                return
            if self._seq_timer.isActive():
                self._seq_timer.stop()
                self._set_play_button(False)
            else:
                self._seq_timer.start(self._frame_interval_ms())
                self._set_play_button(True)
            return
        if self._mode == "video":
            self._toggle_video_playback()

    def _on_sound_clicked(self):
        self._audio_muted = not bool(self._audio_muted)
        self._apply_audio_mute()

    def _sync_video_scrub_range(self):
        if self._player is None:
            self._set_scrub_range(0, 0)
            self._set_scrub_value(0)
            return
        try:
            duration = int(self._player.duration() or 0)
        except Exception:
            duration = 0
        try:
            position = int(self._player.position() or 0)
        except Exception:
            position = 0
        self._set_scrub_range(0, max(0, duration))
        if not self._scrub_dragging:
            self._set_scrub_value(max(0, min(position, max(0, duration))))

    def _on_scrub_pressed(self):
        if self._scrub_dragging:
            return
        self._scrub_dragging = True
        self._resume_after_scrub = False
        if self._mode == "sequence" and self._seq_timer.isActive():
            try:
                self._seq_timer.stop()
                self._resume_after_scrub = True
                self._set_play_button(False)
            except Exception:
                self._resume_after_scrub = False
        elif self._mode == "video" and self._video_playing and self._player is not None:
            try:
                self._player.pause()
                self._resume_after_scrub = True
                self._set_play_button(False)
            except Exception:
                self._resume_after_scrub = False

    def _seek_to_scrub(self, value: int):
        if self._mode == "sequence":
            if not self._seq_paths:
                return
            idx = max(0, min(int(value), len(self._seq_paths) - 1))
            self._seq_index = idx
            self._show_sequence_frame(idx)
            return
        if self._mode == "video" and self._player is not None:
            pos = max(0, int(value))
            try:
                self._player.setPosition(pos)
            except Exception:
                pass

    def _on_scrub_moved(self, value: int):
        self._seek_to_scrub(int(value))

    def _on_scrub_value_changed(self, value: int):
        if self._scrub_updating:
            return
        if self._scrub_dragging:
            return
        self._seek_to_scrub(int(value))

    def _on_scrub_released(self):
        if not self._scrub_dragging:
            return
        value = int(self._scrub_slider.value() or 0)
        self._seek_to_scrub(value)
        resume = bool(self._resume_after_scrub)
        self._scrub_dragging = False
        self._resume_after_scrub = False
        if not resume:
            return
        if self._mode == "sequence":
            try:
                self._seq_timer.start(self._frame_interval_ms())
                self._set_play_button(True)
            except Exception:
                pass
            return
        if self._mode == "video" and self._player is not None:
            try:
                self._player.play()
                self._set_play_button(True)
            except Exception:
                self._set_play_button(False)

    def _on_sequence_tick(self):
        if not self._seq_paths:
            self._seq_timer.stop()
            self._set_play_button(False)
            return
        self._seq_index = (self._seq_index + 1) % len(self._seq_paths)
        self._show_sequence_frame(self._seq_index)

    def _show_sequence_frame(self, index: int):
        if not self._seq_paths:
            return
        idx = max(0, min(int(index), len(self._seq_paths) - 1))
        self._seq_index = idx
        frame_path = self._seq_paths[idx]
        image = QtGui.QImage(str(frame_path))
        if image.isNull():
            self._set_status(f"Failed to read frame: {frame_path.name}")
            return
        self._preview_image = image
        if not self._scrub_dragging:
            self._set_scrub_value(idx)
        self._refresh_preview_pixmap()
        self._set_status(f"Frame {idx + 1}/{len(self._seq_paths)}: {frame_path.name}")

    def _refresh_preview_pixmap(self):
        if self._preview_image.isNull():
            return
        pm = QtGui.QPixmap.fromImage(self._preview_image)
        target = self._preview.contentsRect().size()
        if target.width() <= 0 or target.height() <= 0:
            return
        scaled = pm.scaled(target, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self._preview.setText("")
        self._preview.setPixmap(scaled)

    def _init_video_player(self) -> bool:
        if self._player is not None and self._video_sink is not None:
            return True
        if QtMultimedia is None:
            return False
        if not hasattr(QtMultimedia, "QMediaPlayer") or not hasattr(QtMultimedia, "QVideoSink"):
            return False
        try:
            self._player = QtMultimedia.QMediaPlayer(self)
            if hasattr(QtMultimedia, "QAudioOutput"):
                self._audio_output = QtMultimedia.QAudioOutput(self)
                try:
                    self._player.setAudioOutput(self._audio_output)
                except Exception:
                    pass
            self._video_sink = QtMultimedia.QVideoSink(self)
            self._video_sink.videoFrameChanged.connect(self._on_video_frame_changed)
            self._player.setVideoSink(self._video_sink)
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
            if hasattr(self._player, "durationChanged"):
                self._player.durationChanged.connect(self._on_video_duration_changed)
            if hasattr(self._player, "positionChanged"):
                self._player.positionChanged.connect(self._on_video_position_changed)
            self._apply_audio_mute()
            return True
        except Exception:
            self._player = None
            self._video_sink = None
            self._audio_output = None
            return False

    def _load_video_source(self, path: Path) -> bool:
        if not self._init_video_player():
            return False
        if self._player is None:
            return False
        url = QtCore.QUrl.fromLocalFile(str(path))
        loaded = False
        try:
            self._player.stop()
        except Exception:
            pass
        try:
            self._player.setSource(url)
            loaded = True
        except Exception:
            try:
                media_content = QtMultimedia.QMediaContent(url)
                self._player.setMedia(media_content)
                loaded = True
            except Exception:
                loaded = False
        if not loaded:
            return False
        self._video_loop_set = False
        try:
            if hasattr(self._player, "setLoops"):
                infinite = getattr(QtMultimedia.QMediaPlayer, "Infinite", -1)
                self._player.setLoops(int(infinite))
                self._video_loop_set = True
        except Exception:
            self._video_loop_set = False
        try:
            self._player.setPosition(0)
        except Exception:
            pass
        try:
            self._player.pause()
        except Exception:
            pass
        self._apply_audio_mute()
        self._sync_video_scrub_range()
        return True

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

    def _player_is_playing(self) -> bool:
        if self._player is None:
            return False
        try:
            state = self._player.playbackState()
        except Exception:
            try:
                state = self._player.state()
            except Exception:
                return False
        return self._state_is_playing(state)

    def _toggle_video_playback(self):
        if self._player is None:
            return
        if self._video_playing:
            try:
                self._player.pause()
            except Exception:
                pass
            self._set_play_button(False)
            return
        try:
            self._player.play()
            self._set_play_button(True)
        except Exception:
            self._set_play_button(False)

    def _on_player_state_changed(self, state=None, *_args):
        if state is None:
            self._set_play_button(self._player_is_playing())
            return
        self._set_play_button(self._state_is_playing(state))

    def _on_video_duration_changed(self, duration):
        try:
            dur = max(0, int(duration))
        except Exception:
            dur = 0
        self._set_scrub_range(0, dur)
        if dur <= 0:
            self._set_scrub_value(0)
            return
        if self._player is not None and (not self._scrub_dragging):
            try:
                pos = int(self._player.position() or 0)
            except Exception:
                pos = 0
            self._set_scrub_value(max(0, min(pos, dur)))

    def _on_video_position_changed(self, position):
        try:
            pos = max(0, int(position))
        except Exception:
            pos = 0
        if not self._scrub_dragging:
            self._set_scrub_value(pos)
        if self._mode == "video":
            try:
                dur = int(self._player.duration() or 0) if self._player is not None else 0
            except Exception:
                dur = 0
            if dur > 0:
                self._set_status(f"{self._format_time(pos)} / {self._format_time(dur)}")

    def _on_media_status_changed(self, status):
        if self._player is None or self._video_loop_set:
            return
        try:
            if int(status) == int(QtMultimedia.QMediaPlayer.EndOfMedia):
                self._player.setPosition(0)
                self._player.play()
                self._set_play_button(True)
        except Exception:
            pass

    def _on_player_error(self, *_args):
        msg = ""
        if self._player is not None:
            try:
                msg = str(self._player.errorString() or "")
            except Exception:
                msg = ""
        self._set_status(msg or "Failed to play .mp4 file.")
        self._set_play_button(False)

    def _on_video_frame_changed(self, frame):
        if frame is None:
            return
        try:
            image = frame.toImage()
        except Exception:
            image = QtGui.QImage()
        if image.isNull():
            return
        self._preview_image = image
        self._refresh_preview_pixmap()


def build_ports(node_item) -> None:
    _ensure_param(node_item, "path", "")
    _ensure_hidden_params(getattr(node_item, "model", None), ["path"])
    try:
        setattr(node_item, "_default_named_input", "path")
        setattr(node_item, "_show_default_input_with_named", True)
        node_item.ensure_input("path")
    except Exception:
        pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = VideoPlayerWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    w = int(getattr(node_item, "width", 220))
    base_h = int(body.sizeHint().height())
    try:
        node_h = int(getattr(node_item, "height", 0) or 0)
    except Exception:
        node_h = 0
    try:
        pad = int(getattr(node_item, "_PADDING", 8))
    except Exception:
        pad = 8
    available_h = node_h - int(y_cursor) - max(0, pad)
    h = max(base_h, int(available_h)) if available_h > 0 else base_h
    try:
        body.setMinimumWidth(w)
        body.setMaximumWidth(w)
        proxy.setMinimumWidth(w)
        proxy.setMaximumWidth(w)
    except Exception:
        pass
    proxy.resize(w, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


VIDEO_PLAYER_SPEC = Spec(
    stripe_color="#0ea5e9",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
