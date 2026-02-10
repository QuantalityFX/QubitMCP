from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

SUPPORTED_TEX_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".tif", ".tiff"}
SUPPORTED_MESH_EXTS = {".obj", ".fbx", ".gltf", ".glb"}
PREVIEW_SIZE = 72


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
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        try:
            params = list(params)
        except Exception:
            params = []
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
    store_key = "__ui_hidden_params"
    existing = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            existing = entry
            break
    if existing is None:
        existing = {"name": store_key, "value": ""}
        params.append(existing)

    raw = existing.get("value", "")
    cur = set()
    for part in str(raw).split(","):
        t = part.strip().lower()
        if t:
            cur.add(t)
    for name in names or []:
        if name:
            cur.add(str(name).strip().lower())
    existing["value"] = ",".join(sorted(cur))
    model.params = params


def _ensure_visible_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    existing = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            existing = entry
            break
    if existing is None:
        return
    raw = existing.get("value", "")
    cur = {t.strip().lower() for t in str(raw).split(",") if t.strip()}
    for name in names or []:
        if name:
            cur.discard(str(name).strip().lower())
    existing["value"] = ",".join(sorted(cur))
    model.params = params


def build_ports(node_item) -> None:
    _ensure_param(node_item, "mesh", "")
    _ensure_param(node_item, "base", "")
    _ensure_param(node_item, "overlay", "")
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_hidden_params(getattr(node_item, "model", None), ["source", "path"])
    _ensure_visible_params(getattr(node_item, "model", None), ["mesh", "base", "overlay"])
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")
        node_item.ensure_input("base")
        node_item.ensure_input("overlay")


def _resolve_input_path(node_item) -> str:
    model = getattr(node_item, "model", None)
    sc = node_item.scene()

    def _path_from_item(item, depth=0, visited=None) -> str:
        if item is None or depth > 8:
            return ""
        if visited is None:
            visited = set()
        if item in visited:
            return ""
        visited.add(item)

        m = getattr(item, "model", None)
        if m is None:
            return ""
        kind = (getattr(m, "kind", "") or "").strip().lower()
        if kind == "switch" and sc is not None:
            try:
                edges = list(sc._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(sc._in_edges(item))
                except Exception:
                    edges = []
            if edges:
                return _path_from_item(getattr(edges[0], "src", None), depth + 1, visited)
        return _param_value(m, "path")

    if sc is not None:
        try:
            in_edges = list(sc._ordered_in_edges(node_item))
        except Exception:
            try:
                in_edges = list(sc._in_edges(node_item))
            except Exception:
                in_edges = []
        chosen = None
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in {"mesh", "path"}:
                chosen = edge
                break
        if chosen is None and in_edges:
            chosen = in_edges[0]
        if chosen is not None:
            src_item = getattr(chosen, "src", None)
            path = _path_from_item(src_item, 0, set())
            if path:
                return path

    if model is not None:
        return _param_value(model, "source") or _param_value(model, "path") or _param_value(model, "mesh")
    return ""


def _resolve_named_input_item(node_item, names):
    model = getattr(node_item, "model", None)
    sc = node_item.scene()
    name_set = {str(n).strip().lower() for n in (names or []) if n}

    def _trace(item, depth=0, visited=None):
        if item is None or depth > 8:
            return None, "", ""
        if visited is None:
            visited = set()
        if item in visited:
            return None, "", ""
        visited.add(item)

        m = getattr(item, "model", None)
        if m is None:
            return None, "", ""
        kind = (getattr(m, "kind", "") or "").strip().lower()
        if kind == "switch" and sc is not None:
            try:
                edges = list(sc._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(sc._in_edges(item))
                except Exception:
                    edges = []
            if edges:
                return _trace(getattr(edges[0], "src", None), depth + 1, visited)
        path = _param_value(m, "path")
        return item, kind, path

    if sc is not None:
        try:
            in_edges = list(sc._ordered_in_edges(node_item))
        except Exception:
            try:
                in_edges = list(sc._in_edges(node_item))
            except Exception:
                in_edges = []
        chosen = None
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in name_set:
                chosen = edge
                break
        if chosen is not None:
            src_item = getattr(chosen, "src", None)
            return _trace(src_item, 0, set())

    if model is not None:
        return None, "", _param_value(model, "source") or _param_value(model, "path") or _param_value(model, "mesh")
    return None, "", ""


def _resolve_window(node_item):
    scene = node_item.scene()
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


class TextureLayerProvider:
    def __init__(self, node_item):
        self._node_item = node_item
        self._revision = 0
        self._image: Optional[QtGui.QImage] = None
        self._base_item = None
        self._overlay_item = None
        self._base_path = ""
        self._overlay_path = ""
        self._base_img = None
        self._overlay_img = None
        self._last_base_sig = None
        self._last_overlay_sig = None
        self._last_base_rev = None
        self._last_overlay_rev = None

    def set_sources(self, base_item, overlay_item) -> None:
        if base_item is self._base_item and overlay_item is self._overlay_item:
            return
        self._base_item = base_item
        self._overlay_item = overlay_item
        self._base_path = ""
        self._overlay_path = ""
        self._base_img = None
        self._overlay_img = None
        self._image = None
        self._last_base_sig = None
        self._last_overlay_sig = None
        self._last_base_rev = None
        self._last_overlay_rev = None
        self._revision += 1

    def _source_info(self, item, tag: str):
        if item is None:
            model = getattr(self._node_item, "model", None)
            if model is None:
                return None, None, None
            path = (_param_value(model, tag) or "").strip()
            if not path:
                return None, None, None
            ext = Path(path).suffix.lower()
            if ext not in SUPPORTED_TEX_EXTS:
                return None, None, None
            if tag == "base":
                if path != self._base_path:
                    self._base_path = path
                    self._base_img = None
                img = self._base_img
                if img is None or img.isNull():
                    img = QtGui.QImage(path)
                    if not img.isNull():
                        img = img.convertToFormat(
                            QtGui.QImage.Format_RGBA8888
                            if hasattr(QtGui.QImage, "Format_RGBA8888")
                            else QtGui.QImage.Format_ARGB32
                        )
                        self._base_img = img
                return img, ("path", path), None
            if tag == "overlay":
                if path != self._overlay_path:
                    self._overlay_path = path
                    self._overlay_img = None
                img = self._overlay_img
                if img is None or img.isNull():
                    img = QtGui.QImage(path)
                    if not img.isNull():
                        img = img.convertToFormat(
                            QtGui.QImage.Format_RGBA8888
                            if hasattr(QtGui.QImage, "Format_RGBA8888")
                            else QtGui.QImage.Format_ARGB32
                        )
                        self._overlay_img = img
                return img, ("path", path), None
            return None, None, None
        model = getattr(item, "model", None)
        if model is None:
            return None, None, None
        kind = (getattr(model, "kind", "") or "").strip().lower()
        provider = None
        if kind == "texture_pro":
            provider = getattr(model, "_texture_pro_provider", None)
        elif kind == "texture_layer":
            provider = getattr(model, "_texture_layer_provider", None)
        if provider is not None:
            rev = None
            try:
                rev = int(getattr(provider, "revision", 0))
            except Exception:
                rev = None
            img = None
            try:
                img = provider.image()
            except Exception:
                img = None
            if isinstance(img, QtGui.QImage) and not img.isNull():
                try:
                    fmt = (
                        QtGui.QImage.Format_RGBA8888
                        if hasattr(QtGui.QImage, "Format_RGBA8888")
                        else QtGui.QImage.Format_ARGB32
                    )
                    if img.format() != fmt:
                        img = img.convertToFormat(fmt)
                except Exception:
                    pass
            return img, ("provider", id(provider)), rev
        if kind == "texture":
            path = (_param_value(model, "texture") or "").strip()
            if not path:
                return None, None, None
            ext = Path(path).suffix.lower()
            if ext not in SUPPORTED_TEX_EXTS:
                return None, None, None
            img = None
            if tag == "base":
                if path != self._base_path:
                    self._base_path = path
                    self._base_img = None
                img = self._base_img
                if img is None or img.isNull():
                    img = QtGui.QImage(path)
                    if not img.isNull():
                        img = img.convertToFormat(
                            QtGui.QImage.Format_RGBA8888
                            if hasattr(QtGui.QImage, "Format_RGBA8888")
                            else QtGui.QImage.Format_ARGB32
                        )
                        self._base_img = img
            else:
                if path != self._overlay_path:
                    self._overlay_path = path
                    self._overlay_img = None
                img = self._overlay_img
                if img is None or img.isNull():
                    img = QtGui.QImage(path)
                    if not img.isNull():
                        img = img.convertToFormat(
                            QtGui.QImage.Format_RGBA8888
                            if hasattr(QtGui.QImage, "Format_RGBA8888")
                            else QtGui.QImage.Format_ARGB32
                        )
                        self._overlay_img = img
            return img, ("path", path), None
        return None, None, None

    def advance(self, dt: float, frame_id: Optional[int] = None) -> bool:
        changed = False
        for item in (self._base_item, self._overlay_item):
            if item is None:
                continue
            model = getattr(item, "model", None)
            if model is None:
                continue
            provider = None
            kind = (getattr(model, "kind", "") or "").strip().lower()
            if kind == "texture_pro":
                provider = getattr(model, "_texture_pro_provider", None)
            elif kind == "texture_layer":
                provider = getattr(model, "_texture_layer_provider", None)
            if provider is None:
                continue
            fn = getattr(provider, "advance", None)
            if callable(fn):
                try:
                    if fn(dt, frame_id):
                        changed = True
                except TypeError:
                    try:
                        if fn(dt):
                            changed = True
                    except Exception:
                        pass
                except Exception:
                    pass
        if changed:
            self._revision += 1
        return changed

    def _composite(self, base_img: Optional[QtGui.QImage], overlay_img: Optional[QtGui.QImage]) -> Optional[QtGui.QImage]:
        if base_img is None or base_img.isNull():
            if overlay_img is None or overlay_img.isNull():
                return None
            return overlay_img
        if overlay_img is None or overlay_img.isNull():
            return base_img
        fmt = (
            QtGui.QImage.Format_RGBA8888
            if hasattr(QtGui.QImage, "Format_RGBA8888")
            else QtGui.QImage.Format_ARGB32
        )
        w = int(base_img.width())
        h = int(base_img.height())
        if w <= 0 or h <= 0:
            return overlay_img
        canvas = QtGui.QImage(w, h, fmt)
        canvas.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(canvas)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
        painter.drawImage(0, 0, base_img)
        if overlay_img.width() != w or overlay_img.height() != h:
            overlay_img = overlay_img.scaled(w, h, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        painter.drawImage(0, 0, overlay_img)
        painter.end()
        return canvas

    def image(self) -> Optional[QtGui.QImage]:
        base_img, base_sig, base_rev = self._source_info(self._base_item, "base")
        overlay_img, overlay_sig, overlay_rev = self._source_info(self._overlay_item, "overlay")

        if base_sig != self._last_base_sig or base_rev != self._last_base_rev:
            self._image = None
            self._last_base_sig = base_sig
            self._last_base_rev = base_rev
        if overlay_sig != self._last_overlay_sig or overlay_rev != self._last_overlay_rev:
            self._image = None
            self._last_overlay_sig = overlay_sig
            self._last_overlay_rev = overlay_rev

        if self._image is None:
            self._image = self._composite(base_img, overlay_img)
            if self._image is not None:
                self._revision += 1
        return self._image

    def _source_gpu_state(self, item, tag: str):
        has_source = False
        model = getattr(self._node_item, "model", None)
        if item is None:
            if model is not None:
                try:
                    path = (_param_value(model, tag) or "").strip()
                except Exception:
                    path = ""
                if path:
                    has_source = True
            return None, has_source

        has_source = True
        m = getattr(item, "model", None)
        if m is None:
            return None, has_source
        kind = (getattr(m, "kind", "") or "").strip().lower()
        provider = None
        if kind == "texture_pro":
            provider = getattr(m, "_texture_pro_provider", None)
            if provider is None:
                try:
                    from nodes.texture_pro.spec import _get_provider as _get_texture_pro_provider  # type: ignore
                    provider = _get_texture_pro_provider(item)
                except Exception:
                    provider = None
        elif kind == "texture_layer":
            provider = getattr(m, "_texture_layer_provider", None)
            if provider is None:
                try:
                    provider = _get_provider(item)
                except Exception:
                    provider = None
        if provider is None:
            return None, has_source
        fn = getattr(provider, "gpu_state", None)
        if not callable(fn):
            return None, has_source
        try:
            state = fn()
        except Exception:
            state = None
        if not isinstance(state, dict) or not state:
            return None, has_source
        if state.get("layer"):
            return None, has_source
        return state, has_source

    def gpu_state(self) -> Optional[dict]:
        base_state, base_has = self._source_gpu_state(self._base_item, "base")
        overlay_state, overlay_has = self._source_gpu_state(self._overlay_item, "overlay")

        if base_has and base_state is None:
            return None
        if overlay_has and overlay_state is None:
            return None

        if base_state is None and overlay_state is None:
            return None
        if overlay_state is None:
            return base_state
        if base_state is None:
            return overlay_state

        return {
            "layer": True,
            "base": base_state,
            "overlay": overlay_state,
        }

    @property
    def revision(self) -> int:
        return int(self._revision)


def _get_provider(node_item) -> TextureLayerProvider:
    model = getattr(node_item, "model", None)
    provider = getattr(model, "_texture_layer_provider", None) if model is not None else None
    if not isinstance(provider, TextureLayerProvider):
        provider = TextureLayerProvider(node_item)
        if model is not None:
            try:
                setattr(model, "_texture_layer_provider", provider)
            except Exception:
                pass
    return provider


class TextureLayerWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending = False
        self._provider = _get_provider(node_item)
        self._last_rev = -1
        self._preview_last_ts = 0.0
        self._preview_min_interval = 0.12
        self._advance_last_ts = 0.0
        self._input_item = None
        self._input_kind = ""
        self._scene_input = False
        self._base_item = None
        self._overlay_item = None

        _ensure_param(node_item, "mesh", "")
        _ensure_param(node_item, "base", "")
        _ensure_param(node_item, "overlay", "")
        _ensure_param(node_item, "source", "")
        _ensure_param(node_item, "path", "")
        _ensure_hidden_params(getattr(node_item, "model", None), ["source", "path"])
        _ensure_visible_params(getattr(node_item, "model", None), ["mesh", "base", "overlay"])
        if hasattr(node_item, "ensure_input"):
            try:
                node_item.ensure_input("mesh")
                node_item.ensure_input("base")
                node_item.ensure_input("overlay")
            except Exception:
                pass

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        left = QtWidgets.QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(4)

        self._preview = QtWidgets.QLabel()
        self._preview.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        self._preview.setAlignment(QtCore.Qt.AlignCenter)
        self._preview.setStyleSheet(
            "QLabel{background:#0f172a;border:1px solid #334155;border-radius:4px;}"
        )
        left.addWidget(self._preview, 0, QtCore.Qt.AlignLeft)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(PREVIEW_SIZE)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        left.addWidget(self._view_btn, 0, QtCore.Qt.AlignLeft)
        left.addStretch(1)

        layout.addLayout(left, 0)

        right = QtWidgets.QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)

        info = QtWidgets.QLabel("Layer base + overlay")
        info.setStyleSheet("color:#94a3b8;font-size:10px;")
        right.addWidget(info, 0)
        right.addStretch(1)

        layout.addLayout(right, 1)

        self._ensure_scene()
        self._refresh_preview()

        self._frame_timer = QtCore.QTimer(self)
        self._frame_timer.setInterval(60)
        self._frame_timer.timeout.connect(self._on_timer_tick)
        self._frame_timer.start()

        QtCore.QTimer.singleShot(0, self._update_inputs)

    def sizeHint(self):
        w = 250
        h = PREVIEW_SIZE + 40
        try:
            lay = self.layout()
            if lay is not None:
                hint = lay.sizeHint()
                if hint is not None:
                    w = max(w, int(hint.width()) + 12)
                    h = max(h, int(hint.height()) + 8)
        except Exception:
            pass
        return QtCore.QSize(w, h)

    def _is_selected(self) -> bool:
        sc = None
        try:
            sc = self._node_item.scene()
        except Exception:
            sc = None
        if sc is not None and hasattr(sc, "_active_node_item"):
            try:
                return getattr(sc, "_active_node_item", None) is self._node_item
            except Exception:
                return False
        try:
            return bool(self._node_item.isSelected())
        except Exception:
            return False

    def _ensure_scene(self):
        if self._scene is None:
            self._scene = self._node_item.scene()
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
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(60, self._update_inputs)

    def _refresh_preview(self, force: bool = False):
        if not force:
            try:
                min_interval = float(getattr(self, "_preview_min_interval", 0.12) or 0.0)
            except Exception:
                min_interval = 0.12
            if min_interval > 0.0:
                now = time.perf_counter()
                last = float(getattr(self, "_preview_last_ts", 0.0) or 0.0)
                if last > 0.0 and (now - last) < min_interval:
                    return
        rev_before = None
        try:
            rev_before = int(getattr(self._provider, "revision", 0))
        except Exception:
            rev_before = None
        if not force and rev_before is not None and rev_before == self._last_rev:
            return
        try:
            img = self._provider.image()
        except Exception:
            img = None
        if img is None or img.isNull():
            return
        pix = QtGui.QPixmap.fromImage(img)
        pix = pix.scaled(
            self._preview.width(),
            self._preview.height(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self._preview.setPixmap(pix)
        try:
            self._preview_last_ts = time.perf_counter()
        except Exception:
            pass
        rev_after = rev_before
        try:
            rev_after = int(getattr(self._provider, "revision", rev_before or 0))
        except Exception:
            rev_after = rev_before
        if rev_after is not None:
            self._last_rev = rev_after

    def _update_inputs(self):
        self._pending = False
        self._ensure_scene()

        src_item, src_kind, src_path = _resolve_named_input_item(self._node_item, {"mesh", "path"})
        self._input_item = src_item
        self._input_kind = (src_kind or "").strip().lower()
        self._scene_input = self._input_kind in {"scene", "scene_assembly", "scene_outliner"}

        base_item, _, _ = _resolve_named_input_item(self._node_item, {"base"})
        overlay_item, _, _ = _resolve_named_input_item(self._node_item, {"overlay"})
        self._base_item = base_item
        self._overlay_item = overlay_item
        try:
            self._provider.set_sources(base_item, overlay_item)
        except Exception:
            pass

        src_path = (src_path or "").strip()
        if not src_path:
            try:
                src_path = _param_value(self._node_item.model, "path").strip()
            except Exception:
                src_path = ""
        if not src_path:
            try:
                src_path = _param_value(self._node_item.model, "mesh").strip()
            except Exception:
                src_path = ""
        if src_path:
            self._set_param("mesh", src_path, notify_scene=False)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", src_path, notify_scene=True)
        else:
            self._set_param("mesh", "", notify_scene=False)
            self._set_param("source", "", notify_scene=False)
            self._set_param("path", "", notify_scene=True)

        valid_mesh = bool(src_path) and os.path.exists(src_path) and Path(src_path).suffix.lower() in SUPPORTED_MESH_EXTS
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

        enabled = bool(valid_mesh or has_link)
        self._view_btn.setEnabled(enabled)
        if not enabled:
            self._view_btn.setToolTip("Connect a mesh node.")
        elif not valid_mesh:
            self._view_btn.setToolTip("Waiting for mesh path.")
        else:
            if self._scene_input:
                self._view_btn.setToolTip("View textured scene")
            else:
                self._view_btn.setToolTip("View textured model")

        self._refresh_preview(force=True)

    def _set_param(self, name: str, value: str, notify_scene: bool = True):
        try:
            current = ""
            for p in (getattr(self._node_item.model, "params", None) or []):
                if (p.get("name") or "").strip().lower() == (name or "").strip().lower():
                    current = p.get("value", "") or ""
                    break
            if current == value:
                return
        except Exception:
            pass
        try:
            self._node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
        except Exception:
            pass

    def _on_timer_tick(self):
        if self._is_selected():
            now = time.perf_counter()
            try:
                min_interval = float(getattr(self, "_preview_min_interval", 0.12) or 0.0)
            except Exception:
                min_interval = 0.12
            last = float(getattr(self, "_advance_last_ts", 0.0) or 0.0)
            if min_interval <= 0.0 or (now - last) >= min_interval:
                try:
                    self._advance_last_ts = now
                except Exception:
                    pass
                dt = (now - last) if last > 0.0 else (1.0 / 60.0)
                try:
                    self._provider.advance(dt)
                except Exception:
                    pass
        self._refresh_preview()

    def _on_view_clicked(self):
        if self._scene_input and self._input_item is not None:
            assets = []
            try:
                if hasattr(self._input_item, "_collect_scene_assets"):
                    assets = list(self._input_item._collect_scene_assets())
            except Exception:
                assets = []
            if not assets:
                QtWidgets.QMessageBox.warning(
                    _resolve_window(self._node_item) or self,
                    "Texture Layer",
                    "No valid scene assets connected.",
                )
                return
            for entry in assets:
                if not isinstance(entry, dict):
                    continue
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
            return

        src_path = (_resolve_input_path(self._node_item) or "").strip()
        if not src_path or not os.path.exists(src_path):
            QtWidgets.QMessageBox.warning(
                _resolve_window(self._node_item) or self,
                "Texture Layer",
                "No valid input mesh connected.",
            )
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_3d_model", None) if win is not None else None
        if callable(handler):
            try:
                handler(src_path, None, frame=False)
            except TypeError:
                try:
                    handler(src_path, None)
                except Exception:
                    pass
            except Exception:
                pass
        glv = getattr(win, "gl_view", None) if win is not None else None
        if glv is not None and hasattr(glv, "set_procedural_texture_provider"):
            try:
                glv.set_procedural_texture_provider(self._provider, "Layer")
            except Exception:
                pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = TextureLayerWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    h = max(
        int(body.sizeHint().height()),
        int(body.minimumSizeHint().height()),
        int(body.minimumHeight() or 0),
    )
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


TEXTURE_LAYER_SPEC = Spec(
    stripe_color="#38bdf8",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
