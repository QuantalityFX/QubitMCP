from __future__ import annotations

import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    from PySide2 import QtWidgets, QtCore  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant


SUPPORTED_EXTS = {".obj", ".fbx", ".gltf", ".glb"}
_NORMALS_PROCESS_KINDS = {"normals", "normal", "smooth_normals", "smooth normals"}


@dataclass
class _UVMesh:
    positions: List[Tuple[float, float, float]]
    faces: List[List[int]]
    normals: List[Tuple[float, float, float]]
    face_normals: List[Optional[List[int]]]


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


def _mesh_path_from_item(item) -> str:
    model = getattr(item, "model", None)
    if model is None:
        return ""
    kind = (getattr(model, "kind", "") or "").strip().lower()
    path = _param_value(model, "path") or _param_value(model, "mesh") or _param_value(model, "source")
    if kind in _NORMALS_PROCESS_KINDS:
        try:
            from nodes.normals import spec as _normals_spec  # type: ignore

            build = getattr(_normals_spec, "build_normals_obj", None)
            if callable(build):
                built_path, _err = build(item, force=False, notify_scene=False)
                if built_path:
                    return str(built_path)
        except Exception:
            pass
    return str(path or "")


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
            path = _mesh_path_from_item(getattr(chosen, "src", None))
            if path:
                return path
    if model is not None:
        return _param_value(model, "source") or _param_value(model, "path")
    return ""


def _output_path(node_item, src_path: str) -> Path:
    node_name = _sanitize_name(getattr(getattr(node_item, "model", None), "name", "") or "uv_unwrap")
    src_stem = _sanitize_name(Path(src_path).stem) if src_path else "mesh"
    return _unwrap_dir(node_item) / f"{node_name}_{src_stem}_uv.obj"


def _resolve_obj_index(value: Optional[int], total: int) -> Optional[int]:
    if value is None or total <= 0:
        return None
    if value < 0:
        value = total + value + 1
    if value <= 0 or value > total:
        return None
    return value - 1


def _load_obj_faces(path: Path) -> _UVMesh:
    positions: List[Tuple[float, float, float]] = []
    normals: List[Tuple[float, float, float]] = []
    faces: List[List[int]] = []
    face_normals: List[Optional[List[int]]] = []
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
        elif head == "vn" and len(parts) >= 4:
            try:
                normals.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except Exception:
                continue
        elif head == "f" and len(parts) >= 4:
            face: List[int] = []
            normal_refs: List[Optional[int]] = []
            for token in parts[1:]:
                if not token:
                    continue
                vals = token.split("/")
                idx_str = vals[0] if vals else ""
                if not idx_str:
                    continue
                try:
                    idx = int(idx_str)
                except Exception:
                    continue
                vidx = _resolve_obj_index(idx, len(positions))
                if vidx is None:
                    continue
                nidx = None
                if len(vals) > 2 and vals[2]:
                    try:
                        nidx = _resolve_obj_index(int(vals[2]), len(normals))
                    except Exception:
                        nidx = None
                face.append(vidx)
                normal_refs.append(nidx)
            if len(face) >= 3:
                faces.append(face)
                if len(normal_refs) == len(face) and all(n is not None for n in normal_refs):
                    face_normals.append([int(n) for n in normal_refs if n is not None])
                else:
                    face_normals.append(None)
    return _UVMesh(positions=positions, faces=faces, normals=normals, face_normals=face_normals)


def _mesh_from_arrays(points, normals=None) -> _UVMesh:
    positions: List[Tuple[float, float, float]] = []
    normal_values: List[Tuple[float, float, float]] = []
    try:
        pts = points.reshape(-1, 3)
    except Exception:
        pts = []
    nrm = None
    if normals is not None:
        try:
            nrm = normals.reshape(-1, 3)
        except Exception:
            nrm = None
    for idx, row in enumerate(pts):
        try:
            positions.append((float(row[0]), float(row[1]), float(row[2])))
        except Exception:
            continue
        if nrm is not None and idx < len(nrm):
            try:
                n = nrm[idx]
                normal_values.append((float(n[0]), float(n[1]), float(n[2])))
            except Exception:
                normal_values.append((0.0, 0.0, 1.0))

    faces: List[List[int]] = []
    face_normals: List[Optional[List[int]]] = []
    use_normals = bool(normal_values) and len(normal_values) >= len(positions)
    for i in range(0, len(positions) - 2, 3):
        faces.append([i, i + 1, i + 2])
        face_normals.append([i, i + 1, i + 2] if use_normals else None)
    return _UVMesh(
        positions=positions,
        faces=faces,
        normals=normal_values if use_normals else [],
        face_normals=face_normals,
    )


def _load_tri_mesh(path: Path) -> _UVMesh:
    try:
        from echograph.ui import gl_loaders
    except Exception:
        return _UVMesh([], [], [], [])
    ext = path.suffix.lower()
    array_loader = None
    if ext in {".gltf", ".glb"}:
        array_loader = getattr(gl_loaders, "load_gltf_mesh_arrays", None)
    elif ext == ".fbx":
        array_loader = getattr(gl_loaders, "_load_fbx_mesh_arrays", None)
    if callable(array_loader):
        try:
            arrays = array_loader(path)
            points = getattr(arrays, "points", None)
            if points is not None and getattr(points, "size", 0):
                return _mesh_from_arrays(points, getattr(arrays, "normals", None))
        except Exception:
            pass

    load_model = getattr(gl_loaders, "load_model", None)
    if not callable(load_model):
        return _UVMesh([], [], [], [])
    model = load_model(path)
    if model is None or not model.vertices:
        return _UVMesh([], [], [], [])
    verts: List[Tuple[float, float, float]] = []
    vals = model.vertices
    for i in range(0, len(vals), 3):
        try:
            verts.append((float(vals[i]), float(vals[i + 1]), float(vals[i + 2])))
        except Exception:
            continue
    faces: List[List[int]] = []
    face_normals: List[Optional[List[int]]] = []
    for i in range(0, len(verts) - 2, 3):
        faces.append([i, i + 1, i + 2])
        face_normals.append(None)
    return _UVMesh(positions=verts, faces=faces, normals=[], face_normals=face_normals)


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
    mesh: _UVMesh,
    out_path: Path,
) -> Optional[str]:
    verts = mesh.positions
    faces = mesh.faces
    if not verts or not faces:
        return "No mesh data."
    bounds = _bounds(verts)
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    if not all(math.isfinite(v) for v in bounds):
        return "Invalid mesh bounds."

    uvs: List[Tuple[float, float]] = []
    face_uv_indices: List[Tuple[List[int], List[int], Optional[List[int]]]] = []
    uv_index = 1

    for face_idx, face in enumerate(faces):
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
            normal_refs = mesh.face_normals[face_idx] if face_idx < len(mesh.face_normals) else None
            if normal_refs is not None and len(normal_refs) != len(face):
                normal_refs = None
            face_uv_indices.append((face, face_uvs, normal_refs))

    if not face_uv_indices:
        return "No valid faces to unwrap."

    lines = ["# EchoGraph UV Unwrap"]
    for x, y, z in verts:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for u, v in uvs:
        lines.append(f"vt {u:.6f} {v:.6f}")
    has_normal_refs = any(nidxs for _face, _uvs, nidxs in face_uv_indices)
    if has_normal_refs:
        for nx, ny, nz in mesh.normals:
            lines.append(f"vn {nx:.6f} {ny:.6f} {nz:.6f}")
    for face, uv_idxs, normal_idxs in face_uv_indices:
        parts = []
        for idx, (vidx, vt_idx) in enumerate(zip(face, uv_idxs)):
            if has_normal_refs and normal_idxs is not None and idx < len(normal_idxs):
                parts.append(f"{vidx + 1}/{vt_idx}/{int(normal_idxs[idx]) + 1}")
            else:
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
            mesh = _load_obj_faces(Path(src_path))
        else:
            mesh = _load_tri_mesh(Path(src_path))

        err = _unwrap_to_obj(mesh, out_path)
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
