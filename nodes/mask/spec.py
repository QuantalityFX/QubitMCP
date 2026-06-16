from __future__ import annotations

import os
import math
import re
import tempfile
from pathlib import Path
from typing import Optional

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant


SUPPORTED_MESH_EXTS = {".obj", ".fbx", ".gltf", ".glb"}
MASK_SIZE = 1024
MASK_PREVIEW_SIZE = 64
MASK_NODE_BODY_H = 176
MASK_HIDDEN_PARAMS = [
    "mesh",
    "source",
    "path",
    "mask_path",
    "foreground",
    "background",
    "brush_size",
    "falloff",
    "brush_mode",
]


def _param_value(model, name: str, default: str = "") -> str:
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value", default) or default)
    return default


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
        try:
            params = list(params)
        except Exception:
            params = []
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


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    hidden_entry = None
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == "__ui_hidden_params":
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(hidden_entry)
    hidden = {
        part.strip().lower()
        for part in str(hidden_entry.get("value") or "").split(",")
        if part.strip()
    }
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    hidden_entry["value"] = ",".join(sorted(hidden))
    try:
        setattr(model, "params", params)
    except Exception:
        pass


def _set_param(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    key = (name or "").strip().lower()
    try:
        for entry in (getattr(model, "params", None) or []):
            if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
                if str(entry.get("value", "") or "") == str(value):
                    return
                break
    except Exception:
        pass
    try:
        node_item._set_param_value(name, str(value), rebuild=False, notify_scene=notify_scene)
        return
    except Exception:
        pass
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value)
            break
    else:
        params.append({"name": name, "value": str(value)})
    try:
        setattr(model, "params", params)
    except Exception:
        pass


def _sanitize_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    return safe.strip("_") or "mask"


def _workflow_dir_for_node(node_item) -> Optional[Path]:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    workflow_path = getattr(scene, "_filename", None) if scene is not None else None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                workflow_path = getattr(views[0].window(), "_current_path", None) or workflow_path
        except Exception:
            pass
    if not workflow_path:
        return None
    try:
        return Path(workflow_path).parent
    except Exception:
        return None


def _mask_dir(node_item=None) -> Path:
    base = _workflow_dir_for_node(node_item) if node_item is not None else None
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    out_dir = base / "masks"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _mask_path(node_item) -> Path:
    model = getattr(node_item, "model", None)
    current = _param_value(model, "mask_path") if model is not None else ""
    if current:
        return Path(current)
    name = getattr(model, "name", "") or "mask"
    return _mask_dir(node_item) / f"{_sanitize_name(name)}_mask.png"


def _rgba_format():
    return QtGui.QImage.Format_RGBA8888 if hasattr(QtGui.QImage, "Format_RGBA8888") else QtGui.QImage.Format_ARGB32


def _color_from_text(text: str, default: str) -> QtGui.QColor:
    color = QtGui.QColor(str(text or "").strip() or default)
    if not color.isValid():
        color = QtGui.QColor(default)
    return color


def _color_text(color: QtGui.QColor) -> str:
    try:
        return color.name(QtGui.QColor.HexArgb)
    except Exception:
        return color.name()


def _mesh_path_from_item(item) -> str:
    model = getattr(item, "model", None)
    if model is None:
        return ""
    kind = (getattr(model, "kind", "") or "").strip().lower()
    if kind in {"normals", "normal", "smooth_normals", "smooth normals"}:
        try:
            from nodes.normals import spec as _normals_spec  # type: ignore

            build = getattr(_normals_spec, "build_normals_obj", None)
            if callable(build):
                path, _err = build(item, force=False, notify_scene=False)
                if path:
                    return str(path)
        except Exception:
            pass
    return (
        _param_value(model, "path")
        or _param_value(model, "mesh")
        or _param_value(model, "source")
        or _param_value(model, "texture")
    )


def _resolve_input_item(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    def _trace(item, depth=0, visited=None):
        if item is None or depth > 8:
            return None, "", ""
        if visited is None:
            visited = set()
        if item in visited:
            return None, "", ""
        visited.add(item)
        model = getattr(item, "model", None)
        if model is None:
            return None, "", ""
        kind = (getattr(model, "kind", "") or "").strip().lower()
        if kind == "switch" and scene is not None:
            try:
                edges = list(scene._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(scene._in_edges(item))
                except Exception:
                    edges = []
            if edges:
                return _trace(getattr(edges[0], "src", None), depth + 1, visited)
        return item, kind, _mesh_path_from_item(item)

    if scene is not None:
        try:
            in_edges = list(scene._ordered_in_edges(node_item))
        except Exception:
            try:
                in_edges = list(scene._in_edges(node_item))
            except Exception:
                in_edges = []
        chosen = None
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in {"mesh", "path", "source"}:
                chosen = edge
                break
        if chosen is None and in_edges:
            chosen = in_edges[0]
        if chosen is not None:
            return _trace(getattr(chosen, "src", None), 0, set())

    model = getattr(node_item, "model", None)
    if model is not None:
        return None, "", _param_value(model, "source") or _param_value(model, "path")
    return None, "", ""


def _resolve_input_path(node_item) -> str:
    _item, _kind, path = _resolve_input_item(node_item)
    return str(path or "")


def _resolve_window(node_item):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            views = scene.views()
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
        aw = QtWidgets.QApplication.activeWindow()
        if aw is not None and aw.isWindow():
            return aw
    except Exception:
        pass
    return None


def _icon(name: str) -> QtGui.QIcon:
    try:
        path = Path(__file__).resolve().parents[2] / "icons" / name
        if path.exists():
            return QtGui.QIcon(str(path))
    except Exception:
        pass
    return QtGui.QIcon()


class MaskPaintProvider:
    def __init__(self, node_item):
        self._node_item = node_item
        self._image: Optional[QtGui.QImage] = None
        self._revision = 0
        self._preview_revision = 0
        self._save_pending = False
        self._last_saved_revision = -1

    @property
    def revision(self) -> int:
        return int(self._revision)

    @property
    def preview_revision(self) -> int:
        return int(self._preview_revision)

    def brush_size(self) -> int:
        model = getattr(self._node_item, "model", None)
        try:
            return max(2, min(512, int(float(_param_value(model, "brush_size", "48")))))
        except Exception:
            return 48

    def falloff(self) -> float:
        model = getattr(self._node_item, "model", None)
        try:
            return max(0.0, min(1.0, float(_param_value(model, "falloff", "0.35"))))
        except Exception:
            return 0.35

    def brush_mode(self) -> str:
        model = getattr(self._node_item, "model", None)
        mode = str(_param_value(model, "brush_mode", "add") or "add").strip().lower()
        return "smooth" if mode == "smooth" else "add"

    def foreground(self) -> QtGui.QColor:
        model = getattr(self._node_item, "model", None)
        return _color_from_text(_param_value(model, "foreground", "#ffffffff"), "#ffffffff")

    def background(self) -> QtGui.QColor:
        model = getattr(self._node_item, "model", None)
        return _color_from_text(_param_value(model, "background", "#ff000000"), "#ff000000")

    def mask_path(self) -> Path:
        path = _mask_path(self._node_item)
        try:
            _set_param(self._node_item, "mask_path", str(path), notify_scene=False)
        except Exception:
            pass
        return path

    def _new_image(self) -> QtGui.QImage:
        img = QtGui.QImage(MASK_SIZE, MASK_SIZE, _rgba_format())
        img.fill(self.background())
        return img

    def ensure_image(self) -> QtGui.QImage:
        if self._image is not None and not self._image.isNull():
            return self._image
        path = self.mask_path()
        img = None
        if path.exists():
            loaded = QtGui.QImage(str(path))
            if not loaded.isNull():
                img = loaded.convertToFormat(_rgba_format())
        if img is None or img.isNull():
            img = self._new_image()
            self._image = img
            self._revision += 1
            self._preview_revision += 1
            self.schedule_save()
        self._image = img
        return self._image

    def image(self) -> QtGui.QImage:
        return self.ensure_image()

    def preview_image(self, size: int = MASK_PREVIEW_SIZE) -> QtGui.QImage:
        img = self.ensure_image()
        size = max(16, int(size))
        return img.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

    def reset(self) -> None:
        self._image = self._new_image()
        self._revision += 1
        self._preview_revision += 1
        self.schedule_save()

    def set_foreground(self, color: QtGui.QColor) -> None:
        if color.isValid():
            _set_param(self._node_item, "foreground", _color_text(color), notify_scene=True)
            self._preview_revision += 1

    def set_background(self, color: QtGui.QColor) -> None:
        if color.isValid():
            _set_param(self._node_item, "background", _color_text(color), notify_scene=True)
            self._preview_revision += 1

    def _sync_bound_widget(self) -> None:
        widget = getattr(self, "_widget", None)
        sync = getattr(widget, "_sync_controls_from_provider", None)
        if callable(sync):
            try:
                sync()
            except Exception:
                pass
        refresh = getattr(widget, "_refresh_preview", None)
        if callable(refresh):
            try:
                refresh(force=True)
            except Exception:
                pass

    def set_brush_size(self, size: int) -> None:
        try:
            size = max(2, min(512, int(round(float(size)))))
        except Exception:
            size = 48
        _set_param(self._node_item, "brush_size", str(size), notify_scene=True)
        self._sync_bound_widget()

    def set_brush_mode(self, mode: str) -> None:
        mode = str(mode or "add").strip().lower()
        if mode not in {"add", "smooth"}:
            mode = "add"
        _set_param(self._node_item, "brush_mode", mode, notify_scene=True)
        self._sync_bound_widget()

    def bind_widget(self, widget) -> None:
        try:
            self._widget = widget
        except Exception:
            pass

    def notify_tool_enabled(self, enabled: bool) -> None:
        widget = getattr(self, "_widget", None)
        sync = getattr(widget, "_set_brush_checked_from_viewport", None)
        if callable(sync):
            try:
                sync(bool(enabled))
            except Exception:
                pass

    def swap_colors(self) -> None:
        fg = self.foreground()
        bg = self.background()
        if not fg.isValid() or not bg.isValid():
            return
        _set_param(self._node_item, "foreground", _color_text(bg), notify_scene=True)
        _set_param(self._node_item, "background", _color_text(fg), notify_scene=True)
        self._preview_revision += 1
        self._sync_bound_widget()

    def paint_uv(
        self,
        u: float,
        v: float,
        radius_px: Optional[float] = None,
        falloff: Optional[float] = None,
        color: Optional[QtGui.QColor] = None,
    ) -> bool:
        img = self.ensure_image()
        if img.isNull():
            return False
        try:
            w = int(img.width())
            h = int(img.height())
            if w <= 0 or h <= 0:
                return False
            u = float(u) % 1.0
            v = float(v) % 1.0
        except Exception:
            return False

        radius = float(radius_px if radius_px is not None else (self.brush_size() * 0.5))
        radius = max(1.0, min(float(max(w, h)), radius))
        softness = self.falloff() if falloff is None else max(0.0, min(1.0, float(falloff)))
        x = float(u) * float(max(1, w - 1))
        y = (1.0 - float(v)) * float(max(1, h - 1))

        color = QtGui.QColor(color) if isinstance(color, QtGui.QColor) and color.isValid() else self.foreground()
        painter = QtGui.QPainter(img)
        if not painter.isActive():
            return False
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
        rect = QtCore.QRectF(x - radius, y - radius, radius * 2.0, radius * 2.0)
        painter.setPen(QtCore.Qt.NoPen)
        if softness <= 0.001:
            painter.setBrush(QtGui.QBrush(color))
        else:
            grad = QtGui.QRadialGradient(QtCore.QPointF(x, y), radius)
            inner = max(0.0, min(1.0, 1.0 - softness))
            edge = QtGui.QColor(color)
            edge.setAlpha(0)
            grad.setColorAt(0.0, color)
            grad.setColorAt(inner, color)
            grad.setColorAt(1.0, edge)
            painter.setBrush(QtGui.QBrush(grad))
        painter.drawEllipse(rect)
        painter.end()
        self._revision += 1
        self._preview_revision += 1
        self.schedule_save()
        return True

    def smooth_uv(self, u: float, v: float, radius_px: Optional[float] = None, falloff: Optional[float] = None) -> bool:
        img = self.ensure_image()
        if img.isNull():
            return False
        try:
            w = int(img.width())
            h = int(img.height())
            if w <= 0 or h <= 0:
                return False
            u = float(u) % 1.0
            v = float(v) % 1.0
        except Exception:
            return False

        radius = float(radius_px if radius_px is not None else (self.brush_size() * 0.5))
        radius = max(1.0, min(float(max(w, h)), radius))
        softness = self.falloff() if falloff is None else max(0.0, min(1.0, float(falloff)))
        x = float(u) * float(max(1, w - 1))
        y = (1.0 - float(v)) * float(max(1, h - 1))
        rect = QtCore.QRectF(x - radius, y - radius, radius * 2.0, radius * 2.0).toAlignedRect()
        rect = rect.intersected(QtCore.QRect(0, 0, w, h))
        if rect.isEmpty():
            return False
        pad = 2
        sample_rect = rect.adjusted(-pad, -pad, pad, pad).intersected(QtCore.QRect(0, 0, w, h))
        source = img.copy(sample_rect)
        if source.isNull():
            return False

        kernel = ((1, 4, 6, 4, 1), (4, 16, 24, 16, 4), (6, 24, 36, 24, 6), (4, 16, 24, 16, 4), (1, 4, 6, 4, 1))
        kernel_sum = 256.0
        inner_radius = radius * max(0.0, min(1.0, 1.0 - softness))
        feather = max(1.0, radius - inner_radius)
        strength_base = 0.74
        src_w = int(source.width())
        src_h = int(source.height())
        changed = False
        for iy in range(int(rect.top()), int(rect.bottom()) + 1):
            dy = float(iy) - y
            for ix in range(int(rect.left()), int(rect.right()) + 1):
                dx = float(ix) - x
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > radius:
                    continue
                if dist <= inner_radius:
                    mask = 1.0
                else:
                    t = max(0.0, min(1.0, (dist - inner_radius) / feather))
                    mask = 1.0 - (t * t * (3.0 - (2.0 * t)))
                if mask <= 0.0:
                    continue
                sx = int(ix - sample_rect.left())
                sy = int(iy - sample_rect.top())
                ar = ag = ab = aa = 0.0
                for ky in range(5):
                    yy = max(0, min(src_h - 1, sy + ky - 2))
                    row = kernel[ky]
                    for kx in range(5):
                        xx = max(0, min(src_w - 1, sx + kx - 2))
                        weight = float(row[kx])
                        color = source.pixelColor(xx, yy)
                        ar += float(color.red()) * weight
                        ag += float(color.green()) * weight
                        ab += float(color.blue()) * weight
                        aa += float(color.alpha()) * weight
                blurred = QtGui.QColor(
                    int(round(ar / kernel_sum)),
                    int(round(ag / kernel_sum)),
                    int(round(ab / kernel_sum)),
                    int(round(aa / kernel_sum)),
                )
                original = source.pixelColor(sx, sy)
                blend = max(0.0, min(1.0, strength_base * mask))
                out = QtGui.QColor(
                    int(round((float(original.red()) * (1.0 - blend)) + (float(blurred.red()) * blend))),
                    int(round((float(original.green()) * (1.0 - blend)) + (float(blurred.green()) * blend))),
                    int(round((float(original.blue()) * (1.0 - blend)) + (float(blurred.blue()) * blend))),
                    int(round((float(original.alpha()) * (1.0 - blend)) + (float(blurred.alpha()) * blend))),
                )
                img.setPixelColor(ix, iy, out)
                changed = True
        if not changed:
            return False
        self._revision += 1
        self._preview_revision += 1
        self.schedule_save()
        return True

    def schedule_save(self) -> None:
        if self._save_pending:
            return
        self._save_pending = True
        try:
            QtCore.QTimer.singleShot(300, self.save)
        except Exception:
            self.save()

    def save(self) -> None:
        self._save_pending = False
        if self._last_saved_revision == self._revision:
            return
        img = self.ensure_image()
        path = self.mask_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if img.save(str(path)):
                self._last_saved_revision = int(self._revision)
                _set_param(self._node_item, "mask_path", str(path), notify_scene=False)
        except Exception:
            pass

    def advance(self, _dt: float, _frame_id=None) -> bool:
        return False


def _get_provider(node_item) -> MaskPaintProvider:
    model = getattr(node_item, "model", None)
    provider = getattr(model, "_mask_paint_provider", None) if model is not None else None
    if not isinstance(provider, MaskPaintProvider):
        provider = MaskPaintProvider(node_item)
        if model is not None:
            try:
                setattr(model, "_mask_paint_provider", provider)
            except Exception:
                pass
    return provider


class _ColorButton(QtWidgets.QToolButton):
    colorChanged = QtCore.Signal(QtGui.QColor)

    def __init__(self, color: QtGui.QColor, tooltip: str, parent=None):
        super().__init__(parent)
        self._color = QtGui.QColor(color)
        self.setToolTip(tooltip)
        self.setFixedSize(24, 22)
        self.clicked.connect(self._pick)
        self._sync_style()

    def color(self) -> QtGui.QColor:
        return QtGui.QColor(self._color)

    def setColor(self, color: QtGui.QColor) -> None:
        if color.isValid():
            self._color = QtGui.QColor(color)
            self._sync_style()
            self.colorChanged.emit(QtGui.QColor(self._color))

    def _sync_style(self) -> None:
        self.setStyleSheet(
            "QToolButton{"
            f"background:{self._color.name()};"
            "border:1px solid #64748b;"
            "border-radius:4px;"
            "}"
            "QToolButton:hover{border-color:#e2e8f0;}"
        )

    def _pick(self) -> None:
        color = QtWidgets.QColorDialog.getColor(self._color, self, self.toolTip(), QtWidgets.QColorDialog.ShowAlphaChannel)
        if color.isValid():
            self.setColor(color)


class MaskWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._provider = _get_provider(node_item)
        try:
            self._provider.bind_widget(self)
        except Exception:
            pass
        self._scene = None
        self._scene_connected = False
        self._pending_update = False
        self._input_item = None
        self._input_kind = ""
        self._scene_input = False
        self._last_preview_rev = -1

        self._ensure_defaults()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        tool_row = QtWidgets.QHBoxLayout()
        tool_row.setContentsMargins(0, 0, 0, 0)
        tool_row.setSpacing(7)
        tool_row.setAlignment(QtCore.Qt.AlignVCenter)

        self._brush_btn = QtWidgets.QToolButton()
        self._brush_btn.setCheckable(True)
        self._brush_btn.setToolTip("Paint mask in the 3D viewport")
        self._brush_icon_off = _icon("PaintBrush_Off_Icon.png")
        self._brush_icon_on = _icon("PaintBrush_On_Icon.png")
        self._brush_btn.setIcon(self._brush_icon_off)
        self._brush_btn.setIconSize(QtCore.QSize(20, 20))
        self._brush_btn.setFixedSize(28, 28)
        self._brush_btn.toggled.connect(self._on_brush_toggled)
        tool_row.addWidget(self._brush_btn, 0, QtCore.Qt.AlignVCenter)

        color_box = QtWidgets.QVBoxLayout()
        color_box.setContentsMargins(0, 0, 0, 0)
        color_box.setSpacing(1)
        color_row = QtWidgets.QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(3)
        self._fg_btn = _ColorButton(self._provider.foreground(), "Foreground color")
        self._fg_btn.colorChanged.connect(self._on_foreground_changed)
        color_row.addWidget(self._fg_btn, 0)

        self._bg_btn = _ColorButton(self._provider.background(), "Background color")
        self._bg_btn.colorChanged.connect(self._on_background_changed)
        color_row.addWidget(self._bg_btn, 0)
        color_box.addLayout(color_row)

        self._invert_btn = QtWidgets.QToolButton()
        self._invert_btn.setToolTip("Invert foreground and background colors")
        self._invert_btn.setIcon(_icon("InvertArrows_Icon_s.png"))
        self._invert_btn.setIconSize(QtCore.QSize(16, 16))
        self._invert_btn.setFixedSize(28, 16)
        self._invert_btn.setStyleSheet(
            "QToolButton{background:transparent;border:0px;padding:0px;}"
            "QToolButton:hover{background:transparent;border:0px;}"
            "QToolButton:pressed{background:transparent;border:0px;}"
        )
        self._invert_btn.clicked.connect(self._on_invert_clicked)
        color_box.addWidget(self._invert_btn, 0, QtCore.Qt.AlignHCenter)
        tool_row.addLayout(color_box, 0)

        self._mode_combo = QtWidgets.QComboBox()
        self._mode_combo.addItem("Add", "add")
        self._mode_combo.addItem("Smooth", "smooth")
        self._mode_combo.setFixedSize(72, 24)
        self._mode_combo.setToolTip("Brush mode")
        self._mode_combo.setStyleSheet(
            "QComboBox{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:4px;padding-left:5px;}"
            "QComboBox:hover{border-color:#64748b;}"
            "QComboBox::drop-down{border:0px;width:14px;}"
        )
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        tool_row.addWidget(self._mode_combo, 0, QtCore.Qt.AlignVCenter)
        tool_row.addStretch(1)
        layout.addLayout(tool_row)

        preview_row = QtWidgets.QHBoxLayout()
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(6)
        preview_row.setAlignment(QtCore.Qt.AlignVCenter)

        self._clear_btn = QtWidgets.QToolButton()
        self._clear_btn.setText("Clear")
        self._clear_btn.setToolTip("Clear mask to background color")
        self._clear_btn.setFixedSize(54, 24)
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        preview_row.addWidget(self._clear_btn, 0, QtCore.Qt.AlignVCenter)

        self._preview = QtWidgets.QLabel()
        self._preview.setFixedSize(30, 30)
        self._preview.setScaledContents(True)
        self._preview.setStyleSheet("QLabel{background:#0f1216;border:1px solid #334155;border-radius:4px;}")
        preview_row.addWidget(self._preview, 0, QtCore.Qt.AlignVCenter)
        preview_row.addStretch(1)
        layout.addLayout(preview_row)

        self._size_slider, self._size_value = self._make_slider(4, 256, self._provider.brush_size())
        layout.addLayout(self._slider_row("Size", self._size_slider, self._size_value))
        self._falloff_slider, self._falloff_value = self._make_slider(0, 100, int(round(self._provider.falloff() * 100.0)))
        self._falloff_value.setText(f"{int(self._falloff_slider.value())}%")
        layout.addLayout(self._slider_row("Falloff", self._falloff_slider, self._falloff_value))

        action_row = QtWidgets.QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(4)
        action_row.addStretch(1)
        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(74)
        self._view_btn.clicked.connect(self._on_view_clicked)
        action_row.addWidget(self._view_btn, 0)
        layout.addLayout(action_row)

        self._size_slider.valueChanged.connect(self._on_size_changed)
        self._falloff_slider.valueChanged.connect(self._on_falloff_changed)

        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._update_inputs)
        self._refresh_preview(force=True)

    def sizeHint(self):
        return QtCore.QSize(260, MASK_NODE_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(220, MASK_NODE_BODY_H)

    def _ensure_defaults(self) -> None:
        for name, default in (
            ("mesh", ""),
            ("source", ""),
            ("path", ""),
            ("mask_path", ""),
            ("foreground", "#ffffffff"),
            ("background", "#ff000000"),
            ("brush_size", "48"),
            ("falloff", "0.35"),
            ("brush_mode", "add"),
        ):
            _ensure_param(self._node_item, name, default)
        _ensure_hidden_params(getattr(self._node_item, "model", None), MASK_HIDDEN_PARAMS)

    def _make_slider(self, minimum: int, maximum: int, value: int):
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(int(minimum), int(maximum))
        slider.setValue(int(max(minimum, min(maximum, value))))
        slider.setMinimumWidth(94)
        value_label = QtWidgets.QLabel(str(slider.value()))
        value_label.setFixedWidth(36)
        value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        return slider, value_label

    def _slider_row(self, label: str, slider: QtWidgets.QSlider, value_label: QtWidgets.QLabel):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        text = QtWidgets.QLabel(label)
        text.setFixedWidth(46)
        row.addWidget(text, 0)
        row.addWidget(slider, 1)
        row.addWidget(value_label, 0)
        return row

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._schedule_update)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(self._on_scene_param_changed)
            except Exception:
                pass
        self._scene_connected = True

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._schedule_update()

    def _schedule_update(self):
        if self._pending_update:
            return
        self._pending_update = True
        QtCore.QTimer.singleShot(50, self._update_inputs)

    def _get_gl_view(self):
        win = _resolve_window(self._node_item)
        return getattr(win, "gl_view", None) if win is not None else None

    def _sync_brush_tool(self):
        glv = self._get_gl_view()
        if glv is None or not hasattr(glv, "set_mask_paint_tool"):
            return
        try:
            glv.set_mask_paint_tool(self._provider, bool(self._brush_btn.isChecked()), label="Mask")
        except Exception:
            pass

    def _set_brush_checked_from_viewport(self, checked: bool) -> None:
        was_blocked = self._brush_btn.blockSignals(True)
        try:
            self._brush_btn.setChecked(bool(checked))
            self._brush_btn.setIcon(self._brush_icon_on if checked else self._brush_icon_off)
        finally:
            try:
                self._brush_btn.blockSignals(was_blocked)
            except Exception:
                pass

    def _update_inputs(self):
        self._pending_update = False
        self._ensure_scene()
        src_item, src_kind, src_path = _resolve_input_item(self._node_item)
        self._input_item = src_item
        self._input_kind = (src_kind or "").strip().lower()
        self._scene_input = self._input_kind in {"scene", "scene_assembly", "scene_outliner"}
        src_path = (src_path or "").strip()
        _set_param(self._node_item, "source", src_path, notify_scene=False)
        _set_param(self._node_item, "path", src_path, notify_scene=True)

        has_link = False
        try:
            sc = self._node_item.scene()
            if sc is not None:
                try:
                    in_edges = list(sc._ordered_in_edges(self._node_item))
                except Exception:
                    in_edges = list(sc._in_edges(self._node_item))
                has_link = bool(in_edges)
        except Exception:
            has_link = False
        valid_mesh = bool(src_path) and os.path.exists(src_path) and Path(src_path).suffix.lower() in SUPPORTED_MESH_EXTS
        enabled = bool(valid_mesh or has_link or self._scene_input)
        self._view_btn.setEnabled(enabled)
        self._view_btn.setToolTip("View mask on connected model" if enabled else "Connect a mesh node")
        self._sync_controls_from_provider()
        self._refresh_preview()

    def _sync_controls_from_provider(self) -> None:
        try:
            for button, color in (
                (self._fg_btn, self._provider.foreground()),
                (self._bg_btn, self._provider.background()),
            ):
                was_blocked = button.blockSignals(True)
                try:
                    button.setColor(color)
                finally:
                    button.blockSignals(was_blocked)
        except Exception:
            pass
        try:
            size = int(self._provider.brush_size())
            was_blocked = self._size_slider.blockSignals(True)
            try:
                self._size_slider.setValue(size)
                self._size_value.setText(str(size))
            finally:
                self._size_slider.blockSignals(was_blocked)
        except Exception:
            pass
        try:
            falloff = int(round(float(self._provider.falloff()) * 100.0))
            was_blocked = self._falloff_slider.blockSignals(True)
            try:
                self._falloff_slider.setValue(falloff)
                self._falloff_value.setText(f"{falloff}%")
            finally:
                self._falloff_slider.blockSignals(was_blocked)
        except Exception:
            pass
        try:
            mode = self._provider.brush_mode()
            index = self._mode_combo.findData(mode)
            if index < 0:
                index = 0
            was_blocked = self._mode_combo.blockSignals(True)
            try:
                self._mode_combo.setCurrentIndex(index)
            finally:
                self._mode_combo.blockSignals(was_blocked)
        except Exception:
            pass

    def _refresh_preview(self, force: bool = False):
        try:
            rev = int(self._provider.preview_revision)
        except Exception:
            rev = -1
        if not force and rev == self._last_preview_rev:
            return
        self._last_preview_rev = rev
        try:
            img = self._provider.preview_image(MASK_PREVIEW_SIZE)
            self._preview.setPixmap(QtGui.QPixmap.fromImage(img))
        except Exception:
            pass

    def _on_foreground_changed(self, color: QtGui.QColor):
        self._provider.set_foreground(color)
        self._sync_brush_tool()
        self._refresh_preview(force=True)

    def _on_background_changed(self, color: QtGui.QColor):
        self._provider.set_background(color)
        self._sync_brush_tool()

    def _on_size_changed(self, value: int):
        self._size_value.setText(str(int(value)))
        _set_param(self._node_item, "brush_size", str(int(value)), notify_scene=True)
        self._sync_brush_tool()
        glv = self._get_gl_view()
        if glv is not None:
            try:
                glv.update()
            except Exception:
                pass

    def _on_falloff_changed(self, value: int):
        self._falloff_value.setText(f"{int(value)}%")
        _set_param(self._node_item, "falloff", f"{float(value) / 100.0:.3f}", notify_scene=True)
        self._sync_brush_tool()
        glv = self._get_gl_view()
        if glv is not None:
            try:
                glv.update()
            except Exception:
                pass

    def _on_mode_changed(self, _index: int):
        try:
            mode = self._mode_combo.currentData()
        except Exception:
            mode = "add"
        self._provider.set_brush_mode(str(mode or "add"))
        self._sync_brush_tool()

    def _on_brush_toggled(self, checked: bool):
        self._brush_btn.setIcon(self._brush_icon_on if checked else self._brush_icon_off)
        self._sync_brush_tool()

    def _on_invert_clicked(self):
        self._provider.swap_colors()
        self._sync_controls_from_provider()
        self._sync_brush_tool()
        self._refresh_preview(force=True)

    def _on_clear_clicked(self):
        self._provider.reset()
        self._refresh_preview(force=True)
        glv = self._get_gl_view()
        if glv is not None:
            try:
                glv.update()
            except Exception:
                pass

    def _open_connected_mesh(self, src_item, src_kind: str, src_path: str) -> bool:
        win = _resolve_window(self._node_item)
        if win is None:
            return False
        if src_kind == "modeler" and src_item is not None:
            return self._open_modeler_mesh(src_item, win)
        opened = False
        ext = Path(src_path).suffix.lower()
        if src_kind == "import" and src_item is not None:
            open_import_model = getattr(src_item, "_open_import_model", None)
            if callable(open_import_model):
                try:
                    opened = bool(open_import_model(src_path, ext))
                except Exception:
                    opened = False
        if not opened:
            handler = getattr(win, "open_3d_model", None)
            if callable(handler):
                try:
                    handler(src_path, None, frame=False)
                    opened = True
                except TypeError:
                    try:
                        handler(src_path, None)
                        opened = True
                    except Exception:
                        opened = False
                except Exception:
                    opened = False
        if opened:
            glv = getattr(win, "gl_view", None)
            if glv is not None and hasattr(glv, "set_procedural_texture_provider"):
                try:
                    glv.set_procedural_texture_provider(self._provider, "Mask")
                except Exception:
                    pass
        return opened

    def _open_modeler_mesh(self, src_item, win) -> bool:
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            return False
        scene = None
        try:
            scene = src_item.scene()
        except Exception:
            scene = None
        try:
            from nodes.modeler import spec as _modeler_spec  # type: ignore

            assets_fn = getattr(_modeler_spec, "_modeler_scene_assets", None)
            assets = list(assets_fn(scene, src_item)) if callable(assets_fn) else []
        except Exception:
            assets = []
        if not assets:
            return False
        for entry in assets:
            if isinstance(entry, dict):
                entry["texture_provider"] = self._provider
                entry["texture"] = ""
        try:
            setattr(win, "_active_scene_node", getattr(src_item, "model", None))
            setattr(win, "_active_scene_preview_context", None)
            setattr(win, "_opening_scene_assets_from_scene_node", True)
        except Exception:
            pass
        try:
            try:
                result = handler(assets, frame=False)
            except TypeError:
                result = handler(assets)
            return True if result is None else bool(result)
        except Exception:
            return False
        finally:
            try:
                setattr(win, "_opening_scene_assets_from_scene_node", False)
            except Exception:
                pass

    def _on_view_clicked(self):
        if self._scene_input and self._input_item is not None:
            assets = []
            try:
                if hasattr(self._input_item, "_collect_scene_assets"):
                    assets = list(self._input_item._collect_scene_assets())
            except Exception:
                assets = []
            if not assets:
                QtWidgets.QMessageBox.warning(_resolve_window(self._node_item) or self, "Mask", "No valid scene assets connected.")
                return
            for entry in assets:
                if isinstance(entry, dict):
                    entry["texture_provider"] = self._provider
                    entry["texture"] = ""
            win = _resolve_window(self._node_item)
            handler = getattr(win, "open_scene_assets", None) if win is not None else None
            if callable(handler):
                try:
                    handler(assets, frame=False)
                except TypeError:
                    try:
                        handler(assets)
                    except Exception:
                        pass
                except Exception:
                    pass
            self._sync_brush_tool()
            return

        src_item, src_kind, src_path = _resolve_input_item(self._node_item)
        src_path = (src_path or _resolve_input_path(self._node_item) or "").strip()
        if not src_path or not os.path.exists(src_path):
            QtWidgets.QMessageBox.warning(_resolve_window(self._node_item) or self, "Mask", "No valid input mesh connected.")
            return
        if self._open_connected_mesh(src_item, (src_kind or "").strip().lower(), src_path):
            self._sync_brush_tool()


def build_ports(node_item) -> None:
    for name, default in (
        ("mesh", ""),
        ("source", ""),
        ("path", ""),
        ("mask_path", ""),
        ("foreground", "#ffffffff"),
        ("background", "#ff000000"),
        ("brush_size", "48"),
        ("falloff", "0.35"),
        ("brush_mode", "add"),
    ):
        _ensure_param(node_item, name, default)
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")
    _ensure_hidden_params(getattr(node_item, "model", None), MASK_HIDDEN_PARAMS)


def render_node_body(node_item, y_cursor: int) -> int:
    body = MaskWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    h = max(int(body.sizeHint().height()), int(body.minimumSizeHint().height()))
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


MASK_SPEC = Spec(
    stripe_color="#f43f5e",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
