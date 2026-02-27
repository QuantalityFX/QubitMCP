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
        browse_btn = QtWidgets.QPushButton("Browse")
        browse_btn.setFixedWidth(70)
        browse_btn.clicked.connect(self._on_browse_clicked)
        row1.addWidget(browse_btn, 0)
        layout.addLayout(row1, 0)

        self._preview = QtWidgets.QLabel("No media loaded")
        self._preview.setAlignment(QtCore.Qt.AlignCenter)
        self._preview.setMinimumHeight(self._PREVIEW_H)
        self._preview.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._preview.setStyleSheet(
            "QLabel{background:#0b1220;color:#94a3b8;border:1px solid #334155;border-radius:4px;}"
        )
        layout.addWidget(self._preview, 0)

        row2 = QtWidgets.QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        self._play_btn = QtWidgets.QPushButton("Play")
        self._play_btn.setFixedWidth(70)
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._on_play_clicked)
        row2.addWidget(self._play_btn, 0)
        row2.addStretch(1)
        layout.addLayout(row2, 0)

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
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(self._on_scene_param_changed)
            except Exception:
                pass
        self._scene_connected = True

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._sync_from_params(force=False)

    def _sync_from_params(self, force: bool = False):
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        path_text = (_param_value(model, "path") or "").strip()
        if path_text != (self._path_edit.text() or "").strip():
            try:
                self._path_edit.blockSignals(True)
                self._path_edit.setText(path_text)
            finally:
                self._path_edit.blockSignals(False)
        self._load_media(path_text, force=force)

    def _set_status(self, text: str):
        self._status.setText(str(text or ""))

    def _on_path_committed(self):
        text = (self._path_edit.text() or "").strip()
        _set_param_value(self._node_item, "path", text, notify_scene=True)
        self._load_media(text, force=True)

    def _on_browse_clicked(self):
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
        self._set_play_button(False)
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
                self._preview.setText("Press Play")
                self._set_status(f"Video loaded: {source.name}")
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
        self._show_sequence_frame(0)
        self._play_btn.setEnabled(True)
        self._set_status(f"Sequence loaded: {len(seq)} frame(s)")

    def _set_play_button(self, playing: bool):
        self._play_btn.setText("Pause" if playing else "Play")

    def _stop_playback(self):
        try:
            self._seq_timer.stop()
        except Exception:
            pass
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
        frame_path = self._seq_paths[idx]
        image = QtGui.QImage(str(frame_path))
        if image.isNull():
            self._set_status(f"Failed to read frame: {frame_path.name}")
            return
        self._preview_image = image
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
                    self._audio_output.setMuted(True)
                except Exception:
                    pass
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
        return True

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
        try:
            return int(state) == int(QtMultimedia.QMediaPlayer.PlayingState)
        except Exception:
            return False

    def _toggle_video_playback(self):
        if self._player is None:
            return
        if self._player_is_playing():
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

    def _on_player_state_changed(self, *_args):
        self._set_play_button(self._player_is_playing())

    def _on_media_status_changed(self, status):
        if self._player is None or self._video_loop_set:
            return
        try:
            if int(status) == int(QtMultimedia.QMediaPlayer.EndOfMedia):
                self._player.setPosition(0)
                self._player.play()
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


def render_node_body(node_item, y_cursor: int) -> int:
    body = VideoPlayerWidget(node_item)
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
    return y_cursor + h


VIDEO_PLAYER_SPEC = Spec(
    stripe_color="#0ea5e9",
    render_node_body=render_node_body,
    build_ports=build_ports,
)

