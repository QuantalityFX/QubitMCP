from __future__ import annotations

import math
import os
import re
import tempfile
from pathlib import Path
from typing import List, Tuple, Optional

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    from PySide2 import QtWidgets, QtCore  # type: ignore

from nodes.core import Spec


SUPPORTED_EXTS = {".obj", ".fbx", ".gltf", ".glb"}


def _sanitize_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    return safe.strip("_") or "uv_unwrap"


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


def _unwrap_dir(node_item) -> Path:
    base = _workflow_dir_for_node(node_item)
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    out = base / "uv_unwrap"
    out.mkdir(parents=True, exist_ok=True)
    return out


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


def _output_path(node_item, src_path: str) -> Path:
    node_name = _sanitize_name(getattr(getattr(node_item, "model", None), "name", "") or "uv_unwrap")
    src_stem = _sanitize_name(Path(src_path).stem) if src_path else "mesh"
    return _unwrap_dir(node_item) / f"{node_name}_{src_stem}_uv.obj"


def _load_obj_faces(path: Path) -> Tuple[List[Tuple[float, float, float]], List[List[int]]]:
    positions: List[Tuple[float, float, float]] = []
    faces: List[List[int]] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        raw = path.read_text(errors="ignore")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if not parts:
            continue
        head = parts[0].lower()
        if head == "v" and len(parts) >= 4:
            try:
                positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except Exception:
                continue
        elif head == "f" and len(parts) >= 4:
            face: List[int] = []
            for token in parts[1:]:
                if not token:
                    continue
                idx_str = token.split("/")[0]
                if not idx_str:
                    continue
                try:
                    idx = int(idx_str)
                except Exception:
                    continue
                if idx < 0:
                    idx = len(positions) + idx + 1
                if idx <= 0 or idx > len(positions):
                    continue
                face.append(idx - 1)
            if len(face) >= 3:
                faces.append(face)
    return positions, faces


def _load_tri_mesh(path: Path) -> Tuple[List[Tuple[float, float, float]], List[List[int]]]:
    try:
        from echograph.ui.gl_loaders import load_model
    except Exception:
        return [], []
    model = load_model(path)
    if model is None or not model.vertices:
        return [], []
    verts: List[Tuple[float, float, float]] = []
    vals = model.vertices
    for i in range(0, len(vals), 3):
        try:
            verts.append((float(vals[i]), float(vals[i + 1]), float(vals[i + 2])))
        except Exception:
            continue
    faces: List[List[int]] = []
    for i in range(0, len(verts) - 2, 3):
        faces.append([i, i + 1, i + 2])
    return verts, faces


def _bounds(verts: List[Tuple[float, float, float]]) -> Tuple[float, float, float, float, float, float]:
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def _face_normal(v0, v1, v2) -> Tuple[float, float, float]:
    ax = v1[0] - v0[0]
    ay = v1[1] - v0[1]
    az = v1[2] - v0[2]
    bx = v2[0] - v0[0]
    by = v2[1] - v0[1]
    bz = v2[2] - v0[2]
    nx = ay * bz - az * by
    ny = az * bx - ax * bz
    nz = ax * by - ay * bx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length > 1e-8:
        inv = 1.0 / length
        return nx * inv, ny * inv, nz * inv
    return 0.0, 0.0, 1.0


_TILES = {
    ("x", 1): (0.0, 0.0, 1.0 / 3.0, 0.5),
    ("x", -1): (1.0 / 3.0, 0.0, 2.0 / 3.0, 0.5),
    ("y", 1): (2.0 / 3.0, 0.0, 1.0, 0.5),
    ("y", -1): (0.0, 0.5, 1.0 / 3.0, 1.0),
    ("z", 1): (1.0 / 3.0, 0.5, 2.0 / 3.0, 1.0),
    ("z", -1): (2.0 / 3.0, 0.5, 1.0, 1.0),
}


def _project_uv(
    axis: str,
    sign: int,
    v: Tuple[float, float, float],
    bounds: Tuple[float, float, float, float, float, float],
) -> Tuple[float, float]:
    x, y, z = v
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    dx = xmax - xmin if (xmax - xmin) != 0.0 else 1.0
    dy = ymax - ymin if (ymax - ymin) != 0.0 else 1.0
    dz = zmax - zmin if (zmax - zmin) != 0.0 else 1.0

    if axis == "x":
        u = (z - zmin) / dz
        v = (y - ymin) / dy
        if sign < 0:
            u = 1.0 - u
    elif axis == "y":
        u = (x - xmin) / dx
        v = (z - zmin) / dz
        if sign > 0:
            v = 1.0 - v
    else:  # z
        u = (x - xmin) / dx
        v = (y - ymin) / dy
        if sign < 0:
            u = 1.0 - u

    return max(0.0, min(1.0, u)), max(0.0, min(1.0, v))


def _unwrap_to_obj(
    verts: List[Tuple[float, float, float]],
    faces: List[List[int]],
    out_path: Path,
) -> Optional[str]:
    if not verts or not faces:
        return "No mesh data."
    bounds = _bounds(verts)
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    if not all(math.isfinite(v) for v in bounds):
        return "Invalid mesh bounds."

    uvs: List[Tuple[float, float]] = []
    face_uv_indices: List[Tuple[List[int], List[int]]] = []
    uv_index = 1

    for face in faces:
        if len(face) < 3:
            continue
        try:
            v0 = verts[face[0]]
            v1 = verts[face[1]]
            v2 = verts[face[2]]
        except Exception:
            continue
        nx, ny, nz = _face_normal(v0, v1, v2)
        ax = abs(nx)
        ay = abs(ny)
        az = abs(nz)
        if ax >= ay and ax >= az:
            axis = "x"
            sign = 1 if nx >= 0.0 else -1
        elif ay >= ax and ay >= az:
            axis = "y"
            sign = 1 if ny >= 0.0 else -1
        else:
            axis = "z"
            sign = 1 if nz >= 0.0 else -1

        tile = _TILES.get((axis, sign), (0.0, 0.0, 1.0, 1.0))
        u0, v0t, u1, v1t = tile
        scale_u = u1 - u0
        scale_v = v1t - v0t

        face_uvs: List[int] = []
        for vidx in face:
            try:
                pos = verts[vidx]
            except Exception:
                continue
            u, v = _project_uv(axis, sign, pos, (xmin, xmax, ymin, ymax, zmin, zmax))
            u = u0 + u * scale_u
            v = v0t + v * scale_v
            uvs.append((u, v))
            face_uvs.append(uv_index)
            uv_index += 1

        if len(face_uvs) == len(face) and face_uvs:
            face_uv_indices.append((face, face_uvs))

    if not face_uv_indices:
        return "No valid faces to unwrap."

    lines = ["# EchoGraph UV Unwrap"]
    for x, y, z in verts:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for u, v in uvs:
        lines.append(f"vt {u:.6f} {v:.6f}")
    for face, uv_idxs in face_uv_indices:
        parts = []
        for vidx, vt_idx in zip(face, uv_idxs):
            parts.append(f"{vidx + 1}/{vt_idx}")
        lines.append("f " + " ".join(parts))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return None


class UVUnwrapWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending = False
        self._last_stamp = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setMinimumWidth(0)
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(self._status, 1)

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
        QtCore.QTimer.singleShot(0, self._update_uv)

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
        QtCore.QTimer.singleShot(60, self._update_uv)

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

    def _update_uv(self, force: bool = False):
        self._pending = False
        src_path = (_resolve_input_path(self._node_item) or "").strip()
        if not src_path:
            self._status.setText("No input mesh.")
            self._view_btn.setEnabled(False)
            self._set_param("source", "", notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return
        if not os.path.exists(src_path):
            self._status.setText("Input not found.")
            self._view_btn.setEnabled(False)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return

        ext = Path(src_path).suffix.lower()
        if ext not in SUPPORTED_EXTS:
            self._status.setText("Unsupported mesh.")
            self._view_btn.setEnabled(False)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return

        try:
            st = os.stat(src_path)
            stamp = (src_path, int(st.st_mtime), int(st.st_size))
        except Exception:
            stamp = (src_path, None, None)

        out_path = _output_path(self._node_item, src_path)
        if not force and self._last_stamp == stamp and out_path.exists():
            self._status.setText(Path(src_path).name)
            self._view_btn.setEnabled(True)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", str(out_path), notify_scene=True)
            return

        if ext == ".obj":
            verts, faces = _load_obj_faces(Path(src_path))
        else:
            verts, faces = _load_tri_mesh(Path(src_path))

        err = _unwrap_to_obj(verts, faces, out_path)
        if err:
            self._status.setText(err)
            self._view_btn.setEnabled(False)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return

        self._last_stamp = stamp
        self._status.setText(Path(src_path).name)
        self._view_btn.setEnabled(True)
        self._set_param("source", src_path, notify_scene=False)
        self._set_param("path", str(out_path), notify_scene=True)

    def _on_view_clicked(self):
        self._update_uv(force=True)
        try:
            path = _param_value(self._node_item.model, "path")
        except Exception:
            path = ""
        if not path:
            return
        try:
            self._node_item._open_import_preview(path)
        except Exception:
            pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = UVUnwrapWidget(node_item)
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


UV_UNWRAP_SPEC = Spec(
    stripe_color="#06b6d4",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
