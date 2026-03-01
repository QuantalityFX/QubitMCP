from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant


_FORMAT_EXT = {"png": ".png", "jpg": ".jpg", "tiff": ".tiff", "exr": ".exr"}
_FORMAT_QT = {"png": "PNG", "jpg": "JPEG", "tiff": "TIFF", "exr": "EXR"}
_FORMAT_LABEL = {"png": "PNG", "jpg": "JPG", "tiff": "TIFF", "exr": "EXR"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr"}
_RENDER_SUPERSAMPLE = 2.0
_RENDER_MAX_DIM = 8192


def _norm_fmt(fmt: str) -> str:
    key = (fmt or "").strip().lower()
    if key == "jpeg":
        return "jpg"
    if key == "tif":
        return "tiff"
    return key if key in _FORMAT_EXT else "png"


def _fmt_from_suffix(suffix: str) -> str | None:
    sfx = (suffix or "").strip().lower()
    if sfx == ".jpeg":
        sfx = ".jpg"
    if sfx == ".tif":
        sfx = ".tiff"
    for fmt, ext in _FORMAT_EXT.items():
        if sfx == ext:
            return fmt
    return None


def _qt_supported_formats() -> set[str]:
    out = set()
    try:
        for item in (QtGui.QImageWriter.supportedImageFormats() or []):
            try:
                out.add(bytes(item).decode("ascii", errors="ignore").strip().lower())
            except Exception:
                out.add(str(item).strip().lower())
    except Exception:
        pass
    return out


def _qt_supports_format(fmt: str, supported: set[str]) -> bool:
    key = _norm_fmt(fmt)
    if key == "jpg":
        return "jpg" in supported or "jpeg" in supported
    if key == "tiff":
        return "tif" in supported or "tiff" in supported
    return key in supported


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


def _ordered_in_edges(scene, item):
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _resolve_scene_input_item(node_item):
    scene = node_item.scene()
    if scene is None:
        return None
    edges = _ordered_in_edges(scene, node_item)
    if not edges:
        return None
    picked = None
    for edge in edges:
        name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
        if (name or "").strip().lower() == "scene":
            picked = edge
            break
    if picked is None:
        picked = edges[0]
    src = getattr(picked, "src", None)
    visited = set()
    for _ in range(8):
        if src is None or src in visited:
            return None
        visited.add(src)
        model = getattr(src, "model", None)
        if model is None:
            return None
        kind = (getattr(model, "kind", "") or "").strip().lower()
        if kind in {"scene", "scene_assembly", "scene_outliner"}:
            return src
        if kind != "switch":
            return None
        upstream = _ordered_in_edges(scene, src)
        src = getattr(upstream[0], "src", None) if upstream else None
    return None


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


def _render_dir(node_item) -> Path:
    base = _workflow_dir_for_node(node_item) or (Path(tempfile.gettempdir()) / "EchoGraph")
    out = base / "renders"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _default_output_path(node_item, fmt: str = "png") -> Path:
    scene_item = _resolve_scene_input_item(node_item)
    scene_name = "scene"
    if scene_item is not None and getattr(scene_item, "model", None) is not None:
        scene_name = str(getattr(scene_item.model, "name", "") or "scene").strip() or "scene"
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", scene_name).strip("_") or "scene"
    return _render_dir(node_item) / f"{safe}_render{_FORMAT_EXT.get(_norm_fmt(fmt), '.png')}"


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


def _collect_scene_assets(node_item):
    scene_item = _resolve_scene_input_item(node_item)
    if scene_item is None:
        return [], "Connect a Scene node to this Render node first."
    try:
        from nodes.scene import spec as scene_spec
    except Exception:
        return [], "Scene module is unavailable."
    collector = getattr(scene_spec, "_collect_assets", None)
    if not callable(collector):
        return [], "Scene collector is unavailable."
    try:
        assets = list(collector(scene_item) or [])
    except Exception:
        return [], "Failed to read assets from the connected Scene node."
    if not assets:
        return [], "Connected Scene has no assets."
    return assets, ""


def _collect_scene_cameras(node_item):
    assets, err = _collect_scene_assets(node_item)
    if err:
        return [], err
    out = []
    seen = set()
    for entry in assets:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "")).strip().lower()
        ext = str(entry.get("ext", "")).strip().lower()
        if kind != "camera" and ext != ".camera":
            continue
        owner = str(entry.get("node") or "").strip() or "camera"
        key = owner.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "owner": owner,
                "aspect_width": entry.get("aspect_width", 1920),
                "aspect_height": entry.get("aspect_height", 1080),
            }
        )
    if not out:
        return [], "Connected Scene has no camera nodes."
    return out, ""


def _sequence_frame_path(template_path: Path, frame: int) -> Path:
    idx = max(0, int(frame))
    name = template_path.name
    if "####" in name:
        rendered = name.replace("####", f"{idx:04d}")
    elif "{frame}" in name:
        rendered = name.replace("{frame}", str(idx))
    else:
        rendered = f"{template_path.stem}_{idx:04d}{template_path.suffix}"
    return template_path.with_name(rendered)


def _read_timeline_max_frame(path: Path) -> int | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    max_frame = -1
    for row in (raw.get("keys", []) or []):
        if not isinstance(row, dict):
            continue
        try:
            frame = int(row.get("frame", -1))
        except Exception:
            continue
        max_frame = max(max_frame, frame)
    return max_frame if max_frame >= 0 else None


class _RefreshOnPopupComboBox(QtWidgets.QComboBox):
    def __init__(self, parent=None, on_popup=None):
        super().__init__(parent)
        self._on_popup = on_popup

    def showPopup(self):
        cb = getattr(self, "_on_popup", None)
        if callable(cb):
            try:
                cb()
            except Exception:
                pass
        super().showPopup()


def build_ports(node_item) -> None:
    _ensure_param(node_item, "output", "")
    _ensure_param(node_item, "camera", "")
    _ensure_param(node_item, "frame_rate", "30")
    _ensure_param(node_item, "format", "png")
    _ensure_param(node_item, "start_frame", "0")
    _ensure_param(node_item, "end_frame", "-1")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        ["output", "camera", "frame_rate", "format", "start_frame", "end_frame"],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("scene")


class RenderNodeWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._busy = False
        self._camera_meta: Dict[str, Dict[str, object]] = {}
        self._camera_preferred = ""

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        layout.addWidget(self._status, 0)

        row1 = QtWidgets.QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.addWidget(QtWidgets.QLabel("Camera"), 0)
        self._camera_combo = _RefreshOnPopupComboBox(on_popup=self._on_camera_combo_popup)
        self._camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        row1.addWidget(self._camera_combo, 1)
        layout.addLayout(row1, 0)

        row2 = QtWidgets.QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.addWidget(QtWidgets.QLabel("FPS"), 0)
        self._fps_spin = QtWidgets.QDoubleSpinBox()
        self._fps_spin.setRange(1.0, 240.0)
        self._fps_spin.setValue(30.0)
        self._fps_spin.valueChanged.connect(self._on_fps_changed)
        row2.addWidget(self._fps_spin, 0)
        row2.addWidget(QtWidgets.QLabel("Format"), 0)
        self._format_combo = QtWidgets.QComboBox()
        for fmt in ("png", "jpg", "tiff", "exr"):
            self._format_combo.addItem(_FORMAT_LABEL[fmt], fmt)
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        row2.addWidget(self._format_combo, 0)
        row2.addStretch(1)
        layout.addLayout(row2, 0)

        row2b = QtWidgets.QHBoxLayout()
        row2b.setContentsMargins(0, 0, 0, 0)
        row2b.addWidget(QtWidgets.QLabel("Start"), 0)
        self._start_spin = QtWidgets.QSpinBox()
        self._start_spin.setRange(0, 1_000_000)
        self._start_spin.setValue(0)
        self._start_spin.valueChanged.connect(self._on_start_frame_changed)
        row2b.addWidget(self._start_spin, 0)
        row2b.addWidget(QtWidgets.QLabel("End"), 0)
        self._end_spin = QtWidgets.QSpinBox()
        self._end_spin.setRange(-1, 1_000_000)
        self._end_spin.setSpecialValueText("Auto")
        self._end_spin.setValue(-1)
        self._end_spin.setToolTip("End frame (-1/Auto uses timeline max frame).")
        self._end_spin.valueChanged.connect(self._on_end_frame_changed)
        row2b.addWidget(self._end_spin, 0)
        row2b.addStretch(1)
        layout.addLayout(row2b, 0)

        row3 = QtWidgets.QHBoxLayout()
        row3.setContentsMargins(0, 0, 0, 0)
        self._output_edit = QtWidgets.QLineEdit()
        self._output_edit.setPlaceholderText("Output path, e.g. C:/renders/shot.exr")
        self._output_edit.editingFinished.connect(self._on_output_edit_committed)
        row3.addWidget(self._output_edit, 1)
        browse_btn = QtWidgets.QPushButton("Browse")
        browse_btn.setFixedWidth(70)
        browse_btn.clicked.connect(self._on_browse_clicked)
        row3.addWidget(browse_btn, 0)
        layout.addLayout(row3, 0)

        self._render_btn = QtWidgets.QPushButton("Render Sequence")
        self._render_btn.clicked.connect(self._on_render_clicked)
        layout.addWidget(self._render_btn, 0)

        self._ensure_scene()
        self._sync_controls_from_params()
        self._refresh_status()
        try:
            QtCore.QTimer.singleShot(0, self._deferred_refresh_from_scene)
        except Exception:
            pass

    def sizeHint(self):
        return QtCore.QSize(240, 198)

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

    def _saved_camera_value(self) -> str:
        model = getattr(self._node_item, "model", None)
        raw = (_param_value(model, "camera") if model is not None else "") or ""
        val = str(raw).strip()
        if val:
            return val
        return str(getattr(self, "_camera_preferred", "") or "").strip()

    def _on_scene_links_changed(self, *_args):
        preferred = self._saved_camera_value()
        self._refresh_camera_combo(preferred=preferred)
        self._refresh_status()

    def _deferred_refresh_from_scene(self):
        try:
            self._ensure_scene()
        except Exception:
            pass
        preferred = self._saved_camera_value()
        self._refresh_camera_combo(preferred=preferred)
        self._refresh_status()

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._sync_controls_from_params()
            self._refresh_status()

    def _set_busy(self, busy: bool):
        self._busy = bool(busy)
        self._render_btn.setEnabled(not self._busy)
        self._render_btn.setText("Rendering..." if self._busy else "Render Sequence")

    def _show_popup(self, icon, text: str, details: str = "") -> None:
        parent = _dialog_parent(self._node_item) or self
        box = QtWidgets.QMessageBox(parent)
        box.setWindowTitle("Render Sequence")
        box.setIcon(icon)
        box.setText(str(text or ""))
        if details:
            try:
                box.setDetailedText(str(details))
            except Exception:
                pass
        try:
            box.exec()
        except Exception:
            box.exec_()

    def _sync_controls_from_params(self):
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        raw_output = (_param_value(model, "output") or "").strip()
        fmt = _norm_fmt(_param_value(model, "format") or "png")
        if not raw_output:
            raw_output = str(_default_output_path(self._node_item, fmt))
        fmt_from_path = _fmt_from_suffix(Path(raw_output).suffix)
        if fmt_from_path:
            fmt = fmt_from_path
        self._output_edit.setText(raw_output)
        try:
            fps = float(_param_value(model, "frame_rate") or "30")
        except Exception:
            fps = 30.0
        if fps <= 0.0:
            fps = 30.0
        self._fps_spin.setValue(fps)
        self._set_fmt_combo(fmt)
        try:
            start_frame = int(float(_param_value(model, "start_frame") or "0"))
        except Exception:
            start_frame = 0
        if start_frame < 0:
            start_frame = 0
        try:
            end_frame = int(float(_param_value(model, "end_frame") or "-1"))
        except Exception:
            end_frame = -1
        if end_frame < -1:
            end_frame = -1
        try:
            self._start_spin.blockSignals(True)
            self._start_spin.setValue(int(start_frame))
        except Exception:
            pass
        finally:
            try:
                self._start_spin.blockSignals(False)
            except Exception:
                pass
        try:
            self._end_spin.blockSignals(True)
            self._end_spin.setValue(int(end_frame))
        except Exception:
            pass
        finally:
            try:
                self._end_spin.blockSignals(False)
            except Exception:
                pass
        saved_camera = (_param_value(model, "camera") or "").strip()
        if saved_camera:
            self._camera_preferred = str(saved_camera)
        self._refresh_camera_combo(preferred=saved_camera)

    def _refresh_camera_combo(self, preferred: str = ""):
        cams, _err = _collect_scene_cameras(self._node_item)
        self._camera_meta = {}
        self._camera_combo.blockSignals(True)
        self._camera_combo.clear()
        if not cams:
            self._camera_combo.addItem("(No scene cameras)", "")
            self._camera_combo.blockSignals(False)
            return
        for cam in cams:
            owner = str(cam.get("owner") or "").strip()
            self._camera_combo.addItem(owner, owner)
            self._camera_meta[owner.lower()] = dict(cam)
        idx = 0
        saved = self._saved_camera_value()
        want = preferred.strip().lower() or saved.lower()
        if want:
            for i in range(self._camera_combo.count()):
                if str(self._camera_combo.itemData(i) or "").strip().lower() == want:
                    idx = i
                    break
        self._camera_combo.setCurrentIndex(idx)
        try:
            selected = str(self._camera_combo.itemData(idx) or "").strip()
            if selected:
                self._camera_preferred = selected
        except Exception:
            pass
        self._camera_combo.blockSignals(False)

    def _on_camera_combo_popup(self):
        self._ensure_scene()
        preferred = ""
        try:
            preferred = str(self._camera_combo.currentData() or "").strip()
        except Exception:
            preferred = ""
        if not preferred:
            preferred = self._saved_camera_value()
        self._refresh_camera_combo(preferred=preferred)
        self._refresh_status()

    def _set_fmt_combo(self, fmt: str):
        target = _norm_fmt(fmt)
        for i in range(self._format_combo.count()):
            if _norm_fmt(str(self._format_combo.itemData(i) or "")) == target:
                self._format_combo.setCurrentIndex(i)
                return
        self._format_combo.setCurrentIndex(0)

    def _selected_fmt(self) -> str:
        return _norm_fmt(str(self._format_combo.currentData() or "png"))

    def _refresh_status(self):
        assets, err = _collect_scene_assets(self._node_item)
        if err:
            self._status.setText(err)
            if not self._busy:
                self._render_btn.setEnabled(True)
            return
        cams, cam_err = _collect_scene_cameras(self._node_item)
        if cam_err:
            self._status.setText(cam_err)
            if not self._busy:
                self._render_btn.setEnabled(True)
            return
        self._status.setText(f"Connected: {len(assets)} asset(s), {len(cams)} camera(s).")
        if not self._busy:
            self._render_btn.setEnabled(True)

    def _resolved_template(self) -> Tuple[Path, str]:
        fmt = self._selected_fmt()
        raw = (self._output_edit.text() or "").strip()
        if not raw:
            out = _default_output_path(self._node_item, fmt)
        else:
            out = Path(raw).expanduser()
            if not out.is_absolute():
                out = (_workflow_dir_for_node(self._node_item) or _render_dir(self._node_item)) / out
            out = out.resolve()
        fmt_from_path = _fmt_from_suffix(out.suffix)
        if fmt_from_path:
            fmt = fmt_from_path
        elif out.suffix.lower() not in _IMAGE_EXTS:
            out = out.with_suffix(_FORMAT_EXT[fmt])
        return out, fmt

    def _on_camera_changed(self, _index: int):
        selected = str(self._camera_combo.currentData() or "").strip()
        if selected:
            self._camera_preferred = selected
            _set_param_value(self._node_item, "camera", selected, notify_scene=True)
            return
        # Keep previously saved camera when UI is in temporary "(No scene cameras)" state.
        if self._saved_camera_value():
            return
        _set_param_value(self._node_item, "camera", "", notify_scene=True)

    def _on_fps_changed(self, value: float):
        fps = max(1.0, float(value))
        _set_param_value(self._node_item, "frame_rate", f"{fps:.3f}".rstrip("0").rstrip("."), notify_scene=True)

    def _on_format_changed(self, _index: int):
        fmt = self._selected_fmt()
        _set_param_value(self._node_item, "format", fmt, notify_scene=True)

    def _on_start_frame_changed(self, value: int):
        start = max(0, int(value))
        _set_param_value(self._node_item, "start_frame", str(start), notify_scene=True)

    def _on_end_frame_changed(self, value: int):
        end_frame = int(value)
        if end_frame < -1:
            end_frame = -1
        _set_param_value(self._node_item, "end_frame", str(end_frame), notify_scene=True)

    def _on_output_edit_committed(self):
        text = (self._output_edit.text() or "").strip()
        _set_param_value(self._node_item, "output", text, notify_scene=True)
        fmt = _fmt_from_suffix(Path(text).suffix) if text else None
        if fmt:
            self._set_fmt_combo(fmt)
            _set_param_value(self._node_item, "format", fmt, notify_scene=True)

    def _on_browse_clicked(self):
        start, fmt = self._resolved_template()
        try:
            start.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        parent = _dialog_parent(self._node_item) or self
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent,
            "Render Output Path",
            str(start),
            "Image Sequence (*.png *.jpg *.jpeg *.tif *.tiff *.exr)",
        )
        if not path:
            return
        out = Path(path)
        if _fmt_from_suffix(out.suffix) is None:
            out = out.with_suffix(_FORMAT_EXT[fmt])
        self._output_edit.setText(str(out))
        self._on_output_edit_committed()

    def _camera_resolution(self, owner: str) -> Tuple[int, int]:
        meta = self._camera_meta.get(str(owner or "").strip().lower(), {})
        w = 1920
        h = 1080
        try:
            w = int(float(meta.get("aspect_width", 1920)))
        except Exception:
            w = 1920
        try:
            h = int(float(meta.get("aspect_height", 1080)))
        except Exception:
            h = 1080
        return (max(1, w), max(1, h))

    def _resolved_frame_range(self, timeline_end: int) -> Tuple[int, int]:
        try:
            start_frame = max(0, int(self._start_spin.value()))
        except Exception:
            start_frame = 0
        try:
            raw_end = int(self._end_spin.value())
        except Exception:
            raw_end = -1
        if raw_end < 0:
            end_frame = max(0, int(timeline_end))
        else:
            end_frame = max(0, int(raw_end))
        if end_frame < start_frame:
            end_frame = int(start_frame)
        return int(start_frame), int(end_frame)

    def _fit_image(self, image: QtGui.QImage, target_w: int, target_h: int) -> QtGui.QImage:
        src_w = max(1, int(image.width()))
        src_h = max(1, int(image.height()))
        dst_w = max(1, int(target_w))
        dst_h = max(1, int(target_h))
        src_aspect = float(src_w) / float(src_h)
        dst_aspect = float(dst_w) / float(dst_h)
        out = image
        if abs(src_aspect - dst_aspect) > 1e-6:
            if src_aspect > dst_aspect:
                crop_w = max(1, min(src_w, int(round(float(src_h) * dst_aspect))))
                x0 = max(0, (src_w - crop_w) // 2)
                out = out.copy(x0, 0, crop_w, src_h)
            else:
                crop_h = max(1, min(src_h, int(round(float(src_w) / dst_aspect))))
                y0 = max(0, (src_h - crop_h) // 2)
                out = out.copy(0, y0, src_w, crop_h)
        if out.width() != dst_w or out.height() != dst_h:
            out = out.scaled(dst_w, dst_h, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        return out

    def _process_ui_events(self, max_ms: int = 40) -> None:
        try:
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, max(1, int(max_ms)))
            QtWidgets.QApplication.sendPostedEvents(None, 0)
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, max(1, int(max_ms)))
        except Exception:
            pass

    def _grab_frame(self, glv):
        image = None
        try:
            self._process_ui_events(8)
            try:
                glv.repaint()
            except Exception:
                try:
                    glv.update()
                except Exception:
                    pass
            self._process_ui_events(16)
            if hasattr(glv, "makeCurrent"):
                glv.makeCurrent()
            try:
                ctx = glv.context()
                if ctx is not None:
                    funcs = ctx.functions()
                    if funcs is not None and hasattr(funcs, "glFinish"):
                        funcs.glFinish()
            except Exception:
                pass
            image = glv.grabFramebuffer()
        except Exception:
            image = None
        finally:
            try:
                if hasattr(glv, "doneCurrent"):
                    glv.doneCurrent()
            except Exception:
                pass
        if image is None or image.isNull():
            return None
        return image

    def _image_is_invalid_capture(self, image: QtGui.QImage, threshold: int = 2) -> bool:
        if image is None or image.isNull():
            return True
        w = max(1, int(image.width()))
        h = max(1, int(image.height()))
        step_x = max(1, w // 8)
        step_y = max(1, h // 8)
        min_r = 255
        min_g = 255
        min_b = 255
        min_a = 255
        max_r = 0
        max_g = 0
        max_b = 0
        max_a = 0
        samples = 0
        for y in range(0, h, step_y):
            for x in range(0, w, step_x):
                try:
                    c = image.pixelColor(int(x), int(y))
                    samples += 1
                    rr = int(c.red())
                    gg = int(c.green())
                    bb = int(c.blue())
                    aa = int(c.alpha())
                    min_r = min(min_r, rr)
                    min_g = min(min_g, gg)
                    min_b = min(min_b, bb)
                    min_a = min(min_a, aa)
                    max_r = max(max_r, rr)
                    max_g = max(max_g, gg)
                    max_b = max(max_b, bb)
                    max_a = max(max_a, aa)
                except Exception:
                    continue
        if samples <= 0:
            return True
        # Uniform flat-color captures are usually failed offscreen renders.
        if (
            abs(max_r - min_r) <= 1
            and abs(max_g - min_g) <= 1
            and abs(max_b - min_b) <= 1
            and abs(max_a - min_a) <= 1
        ):
            return True
        # Explicit black-clear capture.
        if max_r <= int(threshold) and max_g <= int(threshold) and max_b <= int(threshold):
            return True
        return False

    def _grab_frame_supersampled(self, glv, target_w: int, target_h: int):
        w = max(1, int(target_w))
        h = max(1, int(target_h))
        render_offscreen = getattr(glv, "_mgl_render_to_image", None)
        if callable(render_offscreen):
            factors = []
            try:
                factors.append(max(1.0, float(_RENDER_SUPERSAMPLE)))
            except Exception:
                factors.append(2.0)
            factors.extend([1.5, 1.0])
            seen = set()
            factors = [ff for ff in factors if not (ff in seen or seen.add(ff))]
            for factor in factors:
                src_w = max(w, int(round(float(w) * float(factor))))
                src_h = max(h, int(round(float(h) * float(factor))))
                src_w = max(2, min(int(_RENDER_MAX_DIM), int(src_w)))
                src_h = max(2, min(int(_RENDER_MAX_DIM), int(src_h)))
                for _attempt in range(4):
                    try:
                        self._process_ui_events(8)
                        glv.update()
                        glv.repaint()
                    except Exception:
                        pass
                    self._process_ui_events(12)
                    try:
                        image = render_offscreen(src_w, src_h)
                    except Exception:
                        image = None
                    if image is not None and (not image.isNull()):
                        if not self._image_is_invalid_capture(image):
                            return image
                    try:
                        time.sleep(0.012)
                    except Exception:
                        pass
            image = self._grab_frame(glv)
            if image is not None and (not image.isNull()) and (not self._image_is_invalid_capture(image)):
                return image
            return None
        return self._grab_frame(glv)

    def _timeline_max_frame(self, glv, scene_name: str, project_path: str | None) -> int:
        paths = []
        seen = set()
        get_anim = getattr(glv, "_timeline_anim_file_path", None)
        if callable(get_anim):
            try:
                p = get_anim(scene_name, project_path=project_path, owner_name=None)
                if isinstance(p, Path) and p.exists():
                    paths.append(p)
                    seen.add(str(p))
            except Exception:
                pass
        get_owner_paths = getattr(glv, "_timeline_owner_file_paths", None)
        if callable(get_owner_paths):
            try:
                for p in list(get_owner_paths() or []):
                    if not isinstance(p, Path) or not p.exists():
                        continue
                    if str(p) in seen:
                        continue
                    paths.append(p)
                    seen.add(str(p))
            except Exception:
                pass
        max_frame = 0
        any_keys = False
        for p in paths:
            mx = _read_timeline_max_frame(p)
            if mx is None:
                continue
            any_keys = True
            max_frame = max(max_frame, int(mx))
        return max_frame if any_keys else 0

    def _on_render_clicked(self):
        cams, cam_err = _collect_scene_cameras(self._node_item)
        if cam_err:
            self._show_popup(QtWidgets.QMessageBox.Warning, cam_err)
            return
        owner = str(self._camera_combo.currentData() or "").strip()
        if not owner:
            owner = str((cams[0] if cams else {}).get("owner") or "").strip()
        if not owner:
            self._show_popup(QtWidgets.QMessageBox.Warning, "Select a camera to render.")
            return

        out_template, fmt = self._resolved_template()
        try:
            out_template.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            self._show_popup(QtWidgets.QMessageBox.Critical, "Failed to create output directory.")
            return

        supported = _qt_supported_formats()
        if not _qt_supports_format(fmt, supported):
            self._show_popup(QtWidgets.QMessageBox.Warning, f"Format '{fmt}' is not supported by this Qt build.")
            return

        parent = _dialog_parent(self._node_item) or self
        win = parent if hasattr(parent, "gl_view") else None
        glv = getattr(win, "gl_view", None) if win is not None else None
        if glv is None or not hasattr(glv, "grabFramebuffer"):
            self._show_popup(QtWidgets.QMessageBox.Warning, "3D viewport is not available.")
            return

        assets, err = _collect_scene_assets(self._node_item)
        if err:
            self._show_popup(QtWidgets.QMessageBox.Warning, err)
            return
        open_scene = getattr(win, "open_scene_assets", None) if win is not None else None
        if callable(open_scene):
            try:
                open_scene(assets, frame=False)
            except Exception:
                pass

        scene_item = _resolve_scene_input_item(self._node_item)
        scene_name = str(getattr(getattr(scene_item, "model", None), "name", "") or "scene").strip() or "scene"
        project_path = str(getattr(win, "_current_path", "") or "").strip() if win is not None else None

        old_view_mode = str(getattr(win, "_view_mode", "") or "").strip().lower() if win is not None else ""
        forced_view_restore = False
        if win is not None and hasattr(win, "_set_view_mode"):
            try:
                if old_view_mode != "3d":
                    win._set_view_mode("3d")
                    forced_view_restore = True
            except Exception:
                forced_view_restore = False

        old_frame = 0
        try:
            old_frame = int(glv._timeline_current_frame())
        except Exception:
            old_frame = 0
        old_mode = str(getattr(glv, "_camera_select_mode", "default") or "default").strip() or "default"
        old_fly_mode_enabled = bool(getattr(glv, "_fly_mode_enabled", False))
        old_orbit_locked = bool(getattr(glv, "_mgl_orbit_locked", True))
        old_lock_enabled = bool(getattr(glv, "_camera_select_lock_enabled", False))
        old_material_live_mode = bool(getattr(glv, "_timeline_material_live_mode", True))
        try:
            old_material_fps_override = float(getattr(glv, "_timeline_material_fps_override", 0.0) or 0.0)
        except Exception:
            old_material_fps_override = 0.0
        set_material_live_mode = getattr(glv, "_timeline_set_material_live_mode", None)
        set_material_fps_override = getattr(glv, "_timeline_set_material_fps_override", None)
        old_viewport_bg = getattr(glv, "_viewport_bg", None)
        old_mgl_bg_color = getattr(glv, "_mgl_bg_color", None)
        old_gizmo_visible = bool(getattr(glv, "_mgl_gizmo_visible", True))
        set_gizmo_visible = getattr(glv, "set_gizmo_visible", None)
        try:
            render_fps = max(1.0, float(self._fps_spin.value()))
        except Exception:
            render_fps = 30.0

        self._set_busy(True)
        failures = []
        written = 0
        render_start = 0
        render_end = 0
        try:
            try:
                glv._timeline_on_play_toggled(False)
            except Exception:
                pass
            try:
                glv.set_timeline_scene_context(scene_name=scene_name, project_path=project_path, owner_name=owner)
            except Exception:
                pass
            try:
                glv._select_scene_camera(owner)
            except Exception:
                pass
            try:
                glv._camera_select_lock_enabled = True
                upd_lock_btn = getattr(glv, "_update_camera_selector_lock_button", None)
                if callable(upd_lock_btn):
                    upd_lock_btn()
            except Exception:
                pass
            # Render with explicit camera-navigation state:
            # fly mode enabled + free-roll orbit.
            try:
                if hasattr(glv, "_on_camera_orbit_toggled"):
                    glv._on_camera_orbit_toggled(False)
                else:
                    glv._mgl_orbit_locked = False
            except Exception:
                pass
            try:
                if hasattr(glv, "_on_fly_mode_toggled"):
                    glv._on_fly_mode_toggled(True)
                else:
                    glv._fly_mode_enabled = True
                    glv._fps_camera_active = True
            except Exception:
                pass
            try:
                sync_view = getattr(glv, "_sync_selected_scene_camera_view", None)
                if callable(sync_view):
                    sync_view(owner)
            except Exception:
                pass
            # Keep viewport gizmo hidden while rendering to avoid accidental capture.
            try:
                if callable(set_gizmo_visible):
                    set_gizmo_visible(False)
                else:
                    glv._mgl_gizmo_visible = False
                    gizmo_toggle = getattr(glv, "_mgl_gizmo_toggle", None)
                    if gizmo_toggle is not None:
                        try:
                            gizmo_toggle.blockSignals(True)
                            gizmo_toggle.setChecked(False)
                        except Exception:
                            pass
                        finally:
                            try:
                                gizmo_toggle.blockSignals(False)
                            except Exception:
                                pass
                    try:
                        glv.update()
                    except Exception:
                        pass
            except Exception:
                pass
            # Sequence renders must be timeline-driven for deterministic procedural materials.
            try:
                if callable(set_material_fps_override):
                    set_material_fps_override(float(render_fps))
                else:
                    glv._timeline_material_fps_override = float(render_fps)
            except Exception:
                pass
            try:
                if callable(set_material_live_mode):
                    set_material_live_mode(False)
                else:
                    glv._timeline_material_live_mode = False
                    upd = getattr(glv, "_update_timeline_material_live_button", None)
                    if callable(upd):
                        upd()
            except Exception:
                pass
            # Keep intermediate viewport flashes darker while rendering.
            try:
                glv._viewport_bg = QtGui.QColor("#000000")
            except Exception:
                pass
            try:
                glv._mgl_bg_color = (0.0, 0.0, 0.0, 1.0)
            except Exception:
                pass
            timeline_end = self._timeline_max_frame(glv, scene_name, project_path)
            render_start, render_end = self._resolved_frame_range(timeline_end)
            total_frames = max(1, int(render_end) - int(render_start) + 1)
            out_fmt_qt = _FORMAT_QT[_norm_fmt(fmt)]
            target_w, target_h = self._camera_resolution(owner)
            for idx, frame in enumerate(range(int(render_start), int(render_end) + 1), start=1):
                self._status.setText(
                    f"Rendering frame {int(frame)} ({int(idx)}/{int(total_frames)})..."
                )
                try:
                    glv._timeline_set_frame_widgets(frame)
                    glv._timeline_apply_frame_if_keyed(frame, force=True)
                    glv._timeline_apply_other_owner_frames(frame)
                    sync_view = getattr(glv, "_sync_selected_scene_camera_view", None)
                    if callable(sync_view):
                        sync_view(owner)
                except Exception:
                    pass
                image = None
                for _capture_attempt in range(3):
                    image = self._grab_frame_supersampled(glv, target_w, target_h)
                    if image is not None and (not image.isNull()) and (not self._image_is_invalid_capture(image)):
                        break
                    image = None
                    try:
                        self._process_ui_events(12)
                    except Exception:
                        pass
                if image is None:
                    failures.append(f"Frame {frame}: capture failed.")
                    continue
                image = self._fit_image(image, target_w, target_h)
                out_path = _sequence_frame_path(out_template, frame)
                ok = False
                try:
                    ok = image.save(str(out_path), out_fmt_qt)
                except Exception:
                    ok = False
                if not ok:
                    failures.append(f"Frame {frame}: save failed ({out_path.name}).")
                    continue
                written += 1
        finally:
            try:
                glv._timeline_set_frame_widgets(old_frame)
                glv._timeline_apply_frame_if_keyed(old_frame, force=True)
                glv._timeline_apply_other_owner_frames(old_frame)
            except Exception:
                pass
            try:
                if old_mode.lower() == "default":
                    glv._select_default_camera()
                else:
                    glv._select_scene_camera(old_mode)
            except Exception:
                pass
            try:
                if hasattr(glv, "_on_fly_mode_toggled"):
                    glv._on_fly_mode_toggled(bool(old_fly_mode_enabled))
                else:
                    glv._fly_mode_enabled = bool(old_fly_mode_enabled)
            except Exception:
                pass
            try:
                if hasattr(glv, "_on_camera_orbit_toggled"):
                    glv._on_camera_orbit_toggled(bool(old_orbit_locked))
                else:
                    glv._mgl_orbit_locked = bool(old_orbit_locked)
            except Exception:
                pass
            try:
                if callable(set_material_fps_override):
                    if old_material_fps_override > 1.0:
                        set_material_fps_override(float(old_material_fps_override))
                    else:
                        set_material_fps_override(None)
                else:
                    glv._timeline_material_fps_override = float(old_material_fps_override)
            except Exception:
                pass
            try:
                if callable(set_material_live_mode):
                    set_material_live_mode(bool(old_material_live_mode))
                else:
                    glv._timeline_material_live_mode = bool(old_material_live_mode)
                    upd = getattr(glv, "_update_timeline_material_live_button", None)
                    if callable(upd):
                        upd()
            except Exception:
                pass
            try:
                glv._camera_select_lock_enabled = bool(old_lock_enabled)
                upd_lock_btn = getattr(glv, "_update_camera_selector_lock_button", None)
                if callable(upd_lock_btn):
                    upd_lock_btn()
            except Exception:
                pass
            try:
                if old_viewport_bg is not None:
                    glv._viewport_bg = old_viewport_bg
            except Exception:
                pass
            try:
                if old_mgl_bg_color is not None:
                    glv._mgl_bg_color = old_mgl_bg_color
            except Exception:
                pass
            try:
                if callable(set_gizmo_visible):
                    set_gizmo_visible(bool(old_gizmo_visible))
                else:
                    glv._mgl_gizmo_visible = bool(old_gizmo_visible)
                    gizmo_toggle = getattr(glv, "_mgl_gizmo_toggle", None)
                    if gizmo_toggle is not None:
                        try:
                            gizmo_toggle.blockSignals(True)
                            gizmo_toggle.setChecked(bool(old_gizmo_visible))
                        except Exception:
                            pass
                        finally:
                            try:
                                gizmo_toggle.blockSignals(False)
                            except Exception:
                                pass
                    try:
                        glv.update()
                    except Exception:
                        pass
            except Exception:
                pass
            if forced_view_restore and win is not None:
                try:
                    if hasattr(win, "_set_view_mode"):
                        restore = old_view_mode if old_view_mode in {"2d", "3d", "split"} else "2d"
                        win._set_view_mode(restore)
                except Exception:
                    pass
            self._set_busy(False)
            self._refresh_status()

        _set_param_value(self._node_item, "output", str(out_template), notify_scene=True)
        _set_param_value(self._node_item, "camera", owner, notify_scene=True)
        _set_param_value(self._node_item, "format", _norm_fmt(fmt), notify_scene=True)
        _set_param_value(
            self._node_item,
            "frame_rate",
            f"{float(render_fps):.3f}".rstrip("0").rstrip("."),
            notify_scene=True,
        )
        _set_param_value(
            self._node_item,
            "start_frame",
            str(max(0, int(self._start_spin.value()))),
            notify_scene=True,
        )
        _set_param_value(
            self._node_item,
            "end_frame",
            str(int(self._end_spin.value())),
            notify_scene=True,
        )

        if failures:
            self._show_popup(
                QtWidgets.QMessageBox.Warning,
                f"Rendered {written} frame(s) [{int(render_start)}-{int(render_end)}]. {len(failures)} frame(s) failed.",
                "\n".join(failures[:120]),
            )
            return
        self._show_popup(
            QtWidgets.QMessageBox.Information,
            f"Rendered {written} frame(s) [{int(render_start)}-{int(render_end)}] from camera '{owner}' to:\n{out_template.parent}",
        )


def render_node_body(node_item, y_cursor: int) -> int:
    body = RenderNodeWidget(node_item)
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


RENDER_SPEC = Spec(
    stripe_color="#14b8a6",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
