from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    from PySide2 import QtWidgets, QtCore  # type: ignore

from nodes.core import Spec


SUPPORTED_TEX_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".tif", ".tiff"}
SUPPORTED_MESH_EXTS = {".obj", ".fbx", ".gltf", ".glb"}


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


def build_ports(node_item) -> None:
    _ensure_param(node_item, "texture", "")
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


def _resolve_input_path(node_item) -> str:
    model = getattr(node_item, "model", None)
    sc = node_item.scene()
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
            src_model = getattr(getattr(chosen, "src", None), "model", None)
            if src_model is not None:
                path = _param_value(src_model, "path")
                if path:
                    return path
    if model is not None:
        return _param_value(model, "source") or _param_value(model, "path")
    return ""


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


class TextureWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending = False

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._edit = QtWidgets.QLineEdit(_param_value(self._node_item.model, "texture"))
        self._edit.setPlaceholderText("Texture file")
        self._edit.setStyleSheet(
            "QLineEdit{background:#12151a;color:#e6edf3;"
            "border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
        )
        self._edit.editingFinished.connect(self._on_texture_changed)
        self._edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(self._edit, 1)

        browse_btn = QtWidgets.QToolButton()
        btn_style = QtWidgets.QApplication.style()
        if btn_style:
            browse_btn.setIcon(btn_style.standardIcon(QtWidgets.QStyle.SP_DialogOpenButton))
        browse_btn.setToolTip("Choose texture")
        browse_btn.setFixedSize(22, 22)
        browse_btn.clicked.connect(lambda _=False: self._browse_texture())
        layout.addWidget(browse_btn, 0)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(64)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        layout.addWidget(self._view_btn, 0)

        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._update_inputs)

    def sizeHint(self):
        return QtCore.QSize(220, 32)

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
                self._scene.paramChanged.connect(lambda *_: self._schedule_update())
            except Exception:
                pass
        self._scene_connected = True

    def _schedule_update(self):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(60, self._update_inputs)

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

    def _update_inputs(self):
        self._pending = False
        try:
            current_tex = _param_value(self._node_item.model, "texture")
            if current_tex != (self._edit.text() or ""):
                self._edit.setText(current_tex)
        except Exception:
            pass
        src_path = (_resolve_input_path(self._node_item) or "").strip()
        if src_path:
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", src_path, notify_scene=True)
        else:
            self._set_param("source", "", notify_scene=False)
            self._set_param("path", "", notify_scene=True)

        tex_path = (self._edit.text() or "").strip()
        valid_tex = bool(tex_path) and os.path.exists(tex_path) and Path(tex_path).suffix.lower() in SUPPORTED_TEX_EXTS
        valid_mesh = bool(src_path) and os.path.exists(src_path) and Path(src_path).suffix.lower() in SUPPORTED_MESH_EXTS
        self._view_btn.setEnabled(bool(valid_tex and valid_mesh))

    def _browse_texture(self):
        start = os.path.expanduser("~")
        current = (self._edit.text() or "").strip()
        if current:
            start = current
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Texture",
            start,
            "Images (*.png *.jpg *.jpeg *.bmp *.tga *.tif *.tiff);;All Files (*.*)",
        )
        if not file_path:
            return
        self._edit.setText(file_path)
        self._on_texture_changed()

    def _on_texture_changed(self):
        value = (self._edit.text() or "").strip()
        self._set_param("texture", value, notify_scene=True)
        self._update_inputs()

    def _on_view_clicked(self):
        src_path = (_resolve_input_path(self._node_item) or "").strip()
        if not src_path or not os.path.exists(src_path):
            return
        tex_path = (self._edit.text() or "").strip()
        if not tex_path or not os.path.exists(tex_path):
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_3d_model", None) if win is not None else None
        if callable(handler):
            try:
                handler(src_path, tex_path)
            except Exception:
                pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = TextureWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    h = body.sizeHint().height()
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


TEXTURE_SPEC = Spec(
    stripe_color="#f59e0b",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
