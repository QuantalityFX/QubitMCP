from __future__ import annotations

from pathlib import Path
from typing import Dict, List

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    from PySide2 import QtWidgets, QtCore  # type: ignore

from nodes.core import Spec


SUPPORTED_EXTS = {".fbx", ".obj", ".gltf", ".glb", ".ply", ".stl", ".off", ".om"}


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return entry.get("value") or ""
    return ""


def _collect_assets(node_item) -> List[Dict[str, str]]:
    scene = node_item.scene()
    if scene is None:
        return []
    try:
        in_edges = list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            in_edges = list(scene._in_edges(node_item))
        except Exception:
            in_edges = []

    assets: List[Dict[str, str]] = []
    seen = set()
    for edge in in_edges:
        src_item = getattr(edge, "src", None)
        model = getattr(src_item, "model", None)
        if model is None:
            continue
        path = _param_value(model, "path")
        if not path:
            continue
        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED_EXTS:
            continue
        key = path.strip()
        if key in seen:
            continue
        seen.add(key)
        texture = _param_value(model, "texture") if ext == ".obj" else ""
        assets.append(
            {
                "path": path,
                "texture": texture,
                "node": getattr(model, "name", "") or "",
                "ext": ext,
            }
        )
    return assets


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


class SceneAssemblyWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")

        view_btn = QtWidgets.QPushButton("View Scene")
        view_btn.clicked.connect(self._on_view_clicked)
        view_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:6px 10px;}"
            "QPushButton:hover{background:#273449;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(view_btn, 0)
        layout.addWidget(self._status, 0)

        self._ensure_scene()
        self._update_status()

    def sizeHint(self):
        return QtCore.QSize(200, 54)

    def _ensure_scene(self):
        if self._scene is None:
            self._scene = self._node_item.scene()
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._update_status)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(lambda *_: self._update_status())
            except Exception:
                pass
        self._scene_connected = True

    def _update_status(self, *_):
        assets = _collect_assets(self._node_item)
        if not assets:
            self._status.setText("No 3D assets connected.")
            return
        mesh_count = sum(1 for a in assets if a.get("ext") != ".ply")
        splat_count = len(assets) - mesh_count
        label = f"{len(assets)} connected (mesh {mesh_count}"
        if splat_count:
            label += f", splat {splat_count}"
        label += ")"
        self._status.setText(label)

    def _on_view_clicked(self):
        assets = _collect_assets(self._node_item)
        if not assets:
            QtWidgets.QMessageBox.information(
                self,
                "Scene",
                "Connect one or more 3D import nodes first.",
            )
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            QtWidgets.QMessageBox.warning(
                self,
                "Scene",
                "3D view is not available.",
            )
            return
        handler(assets)


def augment_infocard_footer(card, footer_layout) -> bool:
    node = getattr(card, "_node_ref", None)
    if not node:
        return False

    container = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    title = QtWidgets.QLabel("Scene Outliner")
    title.setStyleSheet("color:#94a3b8;font-size:11px;")
    layout.addWidget(title, 0)

    outliner = QtWidgets.QListWidget()
    outliner.setStyleSheet(
        "QListWidget{background:#0f1216;color:#e2e8f0;border:1px solid #3c4450;border-radius:6px;}"
        "QListWidget::item{padding:2px 6px;}"
    )
    outliner.setMinimumHeight(70)
    outliner.setMaximumHeight(140)
    layout.addWidget(outliner, 0)

    def _refresh(scene_override=None):
        outliner.clear()
        scene = scene_override if scene_override is not None else getattr(card, "_graph_scene", None)
        if scene is None:
            empty = QtWidgets.QListWidgetItem("(scene not attached)")
            empty.setFlags(QtCore.Qt.NoItemFlags)
            outliner.addItem(empty)
            return
        try:
            item = getattr(scene, "_node_items", {}).get(node.name)
        except Exception:
            item = None
        if item is None:
            empty = QtWidgets.QListWidgetItem("(scene node not found)")
            empty.setFlags(QtCore.Qt.NoItemFlags)
            outliner.addItem(empty)
            return
        try:
            in_edges = list(scene._ordered_in_edges(item))
        except Exception:
            try:
                in_edges = list(scene._in_edges(item))
            except Exception:
                in_edges = []

        rows = []
        seen = set()
        for edge in in_edges:
            src_item = getattr(edge, "src", None)
            model = getattr(src_item, "model", None)
            if model is None:
                continue
            if (model.kind or "").strip().lower() != "import":
                continue
            path = _param_value(model, "path")
            if not path:
                continue
            ext = Path(path).suffix.lower()
            if ext not in SUPPORTED_EXTS:
                continue
            name = (getattr(model, "name", "") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            rows.append({"name": name, "path": path})

        if not rows:
            empty = QtWidgets.QListWidgetItem("(no connected imports)")
            empty.setFlags(QtCore.Qt.NoItemFlags)
            outliner.addItem(empty)
            return

        for idx, entry in enumerate(rows, start=1):
            label = f"{idx}. {entry['name']}"
            row = QtWidgets.QListWidgetItem(label)
            if entry.get("path"):
                row.setToolTip(entry["path"])
            outliner.addItem(row)

    def _connect(scene):
        if scene is None:
            return
        if getattr(card, "_scene_outliner_connected", False):
            return
        try:
            if hasattr(scene, "linksChanged"):
                scene.linksChanged.connect(lambda *_: _refresh(scene))
            if hasattr(scene, "paramChanged"):
                scene.paramChanged.connect(lambda *_: _refresh(scene))
            card._scene_outliner_connected = True
        except Exception:
            pass

    card._scene_outliner_refresh = _refresh
    card._scene_outliner_connect = _connect

    sc = getattr(card, "_graph_scene", None)
    if sc is not None:
        _connect(sc)
        _refresh(sc)
    else:
        _refresh()
    footer_layout.addWidget(container)
    return True


def render_node_body(node_item, y_cursor: int) -> int:
    body = SceneAssemblyWidget(node_item, node_item)
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


SCENE_SPEC = Spec(
    stripe_color="#38bdf8",
    render_node_body=render_node_body,
    augment_infocard_footer=augment_infocard_footer,
)
