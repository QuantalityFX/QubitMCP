from __future__ import annotations

import re
import tempfile
from pathlib import Path

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    from PySide2 import QtWidgets, QtCore  # type: ignore

from nodes.core import Spec


def _sanitize_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    return safe.strip("_") or "volume"


def _workflow_dir_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    scene_path = getattr(scene, "_filename", None) if scene is not None else None
    workflow_path = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                workflow_path = getattr(win, "_current_path", None)
        except Exception:
            pass

    workflow_path = workflow_path or scene_path
    if not workflow_path:
        return None
    try:
        return Path(workflow_path).parent
    except Exception:
        return None


def _volume_dir(node_item=None) -> Path:
    base = _workflow_dir_for_node(node_item) if node_item is not None else None
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    vol_dir = base / "volumes"
    vol_dir.mkdir(parents=True, exist_ok=True)
    return vol_dir


def _volume_path(node_item, node_name: str) -> Path:
    safe = _sanitize_name(node_name)
    return _volume_dir(node_item) / f"{safe}_volume_cube.obj"


def _write_obj(
    path: Path,
    verts: list[tuple[float, float, float]],
    faces: list[list[int]],
    uvs: list[tuple[float, float]] | None = None,
) -> None:
    lines = ["# EchoGraph volume cube"]
    for x, y, z in verts:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    if uvs:
        for u, v in uvs:
            lines.append(f"vt {u:.6f} {v:.6f}")
    for face in faces:
        if not face or len(face) < 3:
            continue
        if uvs:
            idxs = " ".join(f"{i + 1}/{i + 1}" for i in face)
        else:
            idxs = " ".join(str(i + 1) for i in face)
        lines.append(f"f {idxs}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cube(size: float = 1.0):
    s = size * 0.5
    face_verts = [
        [(-s, -s, -s), (s, -s, -s), (s, s, -s), (-s, s, -s)],  # back (-Z)
        [(-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)],      # front (+Z)
        [(-s, -s, -s), (s, -s, -s), (s, -s, s), (-s, -s, s)],  # bottom (-Y)
        [(-s, s, -s), (s, s, -s), (s, s, s), (-s, s, s)],      # top (+Y)
        [(s, -s, -s), (s, s, -s), (s, s, s), (s, -s, s)],      # right (+X)
        [(-s, -s, -s), (-s, s, -s), (-s, s, s), (-s, -s, s)],  # left (-X)
    ]
    uv_face = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    verts: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    faces: list[list[int]] = []
    for face in face_verts:
        base = len(verts)
        verts.extend(face)
        uvs.extend(uv_face)
        faces.append([base, base + 1, base + 2, base + 3])
    return verts, faces, uvs


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


def _ensure_hidden_params(model, names: list[str]) -> None:
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
    for name in names:
        if name:
            cur.add(str(name).strip().lower())
    existing["value"] = ",".join(sorted(cur))
    model.params = params


def build_ports(node_item) -> None:
    _ensure_param(node_item, "mesh", "")
    _ensure_param(node_item, "texture", "")
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "volume", "1")
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")
        node_item.ensure_input("texture")


class VolumeSelectorWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        label = QtWidgets.QLabel("Volume Selector")
        label.setStyleSheet("color:#e2e8f0;")
        layout.addWidget(label, 1)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(64)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        layout.addWidget(self._view_btn, 0)

        self._ensure_volume_path(notify_scene=False)

    def sizeHint(self):
        return QtCore.QSize(220, 32)

    def _ensure_volume_path(self, notify_scene: bool = False) -> None:
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        path = _volume_path(self._node_item, getattr(model, "name", "volume"))
        try:
            verts, faces, uvs = _cube()
            _write_obj(path, verts, faces, uvs=uvs)
        except Exception:
            pass
        try:
            self._node_item._set_param_value("path", str(path), rebuild=False, notify_scene=notify_scene)
            self._node_item._set_param_value("volume", "1", rebuild=False, notify_scene=notify_scene)
        except Exception:
            pass
        try:
            self._view_btn.setEnabled(path.exists())
        except Exception:
            pass
        try:
            _ensure_hidden_params(model, ["path", "volume"])
        except Exception:
            pass

    def _on_view_clicked(self):
        try:
            path = ""
            for entry in (getattr(self._node_item.model, "params", None) or []):
                if (entry.get("name") or "").strip().lower() == "path":
                    path = (entry.get("value") or "").strip()
                    break
            if not path:
                return
            self._node_item._open_import_preview(path)
        except Exception:
            pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = VolumeSelectorWidget(node_item)
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


VOLUME_SPEC = Spec(
    stripe_color="#38bdf8",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("volume_selector", VOLUME_SPEC)
