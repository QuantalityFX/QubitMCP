from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant


SUPPORTED_EXTS = {".obj", ".fbx", ".gltf", ".glb"}
NORMALS_NODE_BODY_H = 64
_DISPLAY_PASS_KINDS = {
    "uv_unwrap",
    "texture",
    "texture_pro",
    "texture_layer",
    "normals",
    "normal",
    "smooth_normals",
    "smooth normals",
}


@dataclass
class _FaceVert:
    vi: int
    vti: Optional[int] = None


@dataclass
class _MeshData:
    positions: list[tuple[float, float, float]]
    texcoords: list[tuple[float, float]]
    faces: list[list[_FaceVert]]


def _sanitize_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    return safe.strip("_") or "normals"


def _workflow_dir_for_node(node_item) -> Path | None:
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


def _normals_dir(node_item) -> Path:
    base = _workflow_dir_for_node(node_item)
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    out = base / "normals"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in getattr(model, "params", None) or []:
        if isinstance(p, dict) and (p.get("name") or "").strip().lower() == key:
            return str(p.get("value") or "")
    return ""


def _set_node_param(node_item, name: str, value: str, notify_scene: bool = False) -> None:
    model = getattr(node_item, "model", None)
    current = _param_value(model, name)
    if current == value:
        return
    setter = getattr(node_item, "_set_param_value", None)
    if callable(setter):
        try:
            setter(name, value, rebuild=False, notify_scene=notify_scene)
            return
        except Exception:
            pass
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = value
            break
    else:
        params.append({"name": name, "value": value})
    try:
        model.params = params
    except Exception:
        pass


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


def _ensure_hidden_params(node_item, names: list[str]) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    entry = None
    for p in params:
        if isinstance(p, dict) and (p.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = p
            break
    if entry is None:
        entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(entry)
    hidden = {part.strip().lower() for part in str(entry.get("value") or "").split(",") if part.strip()}
    hidden.update(str(name).strip().lower() for name in names if str(name).strip())
    entry["value"] = ",".join(sorted(hidden))
    model.params = params


def build_ports(node_item) -> None:
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "angle", "180")
    _ensure_param(node_item, "weld_tolerance", "0.0001")
    _ensure_hidden_params(node_item, ["source", "path"])
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


def _resolve_input_path(node_item) -> str:
    model = getattr(node_item, "model", None)
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
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
            src_model = getattr(getattr(chosen, "src", None), "model", None)
            if src_model is not None:
                path = _param_value(src_model, "path") or _param_value(src_model, "mesh") or _param_value(src_model, "source")
                if path:
                    return path
    if model is not None:
        return _param_value(model, "source") or _param_value(model, "path")
    return ""


def _pick_input_source_item(scene, node_item):
    if scene is None or node_item is None:
        return None
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
    return getattr(chosen, "src", None) if chosen is not None else None


def _display_source_path(node_item) -> str:
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    item = _pick_input_source_item(scene, node_item)
    visited = set()
    fallback = ""
    depth = 0
    while item is not None and id(item) not in visited and depth < 12:
        visited.add(id(item))
        depth += 1
        model = getattr(item, "model", None)
        if model is None:
            break
        kind = (getattr(model, "kind", "") or "").strip().lower()
        path = _param_value(model, "source") or _param_value(model, "mesh") or _param_value(model, "path")
        if path:
            fallback = path
        if kind not in _DISPLAY_PASS_KINDS:
            return path or fallback
        next_item = _pick_input_source_item(scene, item)
        if next_item is None or next_item is item:
            break
        item = next_item
    if fallback:
        return fallback
    model = getattr(node_item, "model", None)
    return _param_value(model, "source") or _param_value(model, "path")


def _short_filename(path: str, max_chars: int = 32) -> str:
    name = Path(path).name if path else ""
    if len(name) <= max_chars:
        return name
    suffix = Path(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    keep = max(8, max_chars - len(suffix) - 3)
    left = max(4, keep // 2)
    right = max(4, keep - left)
    return f"{stem[:left]}...{stem[-right:]}{suffix}"


def _output_path(node_item, src_path: str) -> Path:
    node_name = _sanitize_name(getattr(getattr(node_item, "model", None), "name", "") or "normals")
    src_stem = _sanitize_name(Path(src_path).stem) if src_path else "mesh"
    return _normals_dir(node_item) / f"{node_name}_{src_stem}_normals.obj"


def _cache_meta_path(out_path: Path) -> Path:
    return out_path.with_suffix(out_path.suffix + ".meta.json")


def _build_stamp(src_path: str, angle: float, tolerance: float) -> dict:
    try:
        st = os.stat(src_path)
        mtime_ns = int(getattr(st, "st_mtime_ns", int(float(st.st_mtime) * 1_000_000_000)))
        size = int(st.st_size)
    except Exception:
        mtime_ns = 0
        size = 0
    return {
        "schema": 1,
        "source": os.path.normcase(os.path.abspath(src_path)),
        "mtime_ns": mtime_ns,
        "size": size,
        "angle": round(float(angle), 6),
        "weld_tolerance": round(float(tolerance), 10),
    }


def _cache_matches(out_path: Path, stamp: dict) -> bool:
    if not out_path.exists():
        return False
    try:
        current = json.loads(_cache_meta_path(out_path).read_text(encoding="utf-8"))
    except Exception:
        return False
    return current == stamp


def _write_cache_meta(out_path: Path, stamp: dict) -> None:
    try:
        _cache_meta_path(out_path).write_text(
            json.dumps(stamp, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _parse_float(text: str, default: float, min_value: float, max_value: float) -> float:
    try:
        value = float(str(text or "").strip())
    except Exception:
        value = default
    return max(min_value, min(max_value, float(value)))


def _resolve_obj_index(idx: Optional[int], count: int) -> Optional[int]:
    if idx is None or count <= 0:
        return None
    try:
        value = int(idx)
    except Exception:
        return None
    if value < 0:
        value = count + value + 1
    if value <= 0 or value > count:
        return None
    return value - 1


def _load_obj_mesh(path: Path) -> _MeshData:
    positions: list[tuple[float, float, float]] = []
    texcoords: list[tuple[float, float]] = []
    faces: list[list[_FaceVert]] = []
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
        elif head == "vt" and len(parts) >= 3:
            try:
                texcoords.append((float(parts[1]), float(parts[2])))
            except Exception:
                continue
        elif head == "f" and len(parts) >= 4:
            face: list[_FaceVert] = []
            for token in parts[1:]:
                vals = token.split("/")
                try:
                    raw_vi = int(vals[0]) if vals and vals[0] else None
                except Exception:
                    raw_vi = None
                try:
                    raw_vti = int(vals[1]) if len(vals) > 1 and vals[1] else None
                except Exception:
                    raw_vti = None
                vi = _resolve_obj_index(raw_vi, len(positions))
                if vi is None:
                    continue
                vti = _resolve_obj_index(raw_vti, len(texcoords))
                face.append(_FaceVert(vi=vi, vti=vti))
            if len(face) >= 3:
                faces.append(face)
    return _MeshData(positions=positions, texcoords=texcoords, faces=faces)


def _load_tri_mesh(path: Path) -> _MeshData:
    def _from_arrays(points, uvs=None) -> _MeshData:
        positions: list[tuple[float, float, float]] = []
        texcoords: list[tuple[float, float]] = []
        try:
            pts = points.reshape(-1, 3)
        except Exception:
            pts = []
        uv_arr = None
        if uvs is not None:
            try:
                uv_arr = uvs.reshape(-1, 2)
            except Exception:
                uv_arr = None

        for idx, row in enumerate(pts):
            try:
                positions.append((float(row[0]), float(row[1]), float(row[2])))
            except Exception:
                continue
            if uv_arr is not None and idx < len(uv_arr):
                try:
                    uv = uv_arr[idx]
                    texcoords.append((float(uv[0]), float(uv[1])))
                except Exception:
                    texcoords.append((0.0, 0.0))

        faces: list[list[_FaceVert]] = []
        use_uvs = bool(texcoords) and len(texcoords) >= len(positions)
        for i in range(0, len(positions) - 2, 3):
            if use_uvs:
                faces.append([_FaceVert(i, i), _FaceVert(i + 1, i + 1), _FaceVert(i + 2, i + 2)])
            else:
                faces.append([_FaceVert(i), _FaceVert(i + 1), _FaceVert(i + 2)])
        return _MeshData(positions=positions, texcoords=texcoords if use_uvs else [], faces=faces)

    try:
        from echograph.ui import gl_loaders
    except Exception:
        return _MeshData([], [], [])
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
                return _from_arrays(points, getattr(arrays, "uvs", None))
        except Exception:
            pass

    load_model = getattr(gl_loaders, "load_model", None)
    if not callable(load_model):
        return _MeshData([], [], [])
    model = load_model(path)
    if model is None or not model.vertices:
        return _MeshData([], [], [])
    verts: list[tuple[float, float, float]] = []
    vals = list(model.vertices)
    for i in range(0, len(vals) - 2, 3):
        try:
            verts.append((float(vals[i]), float(vals[i + 1]), float(vals[i + 2])))
        except Exception:
            continue
    faces = [[_FaceVert(i), _FaceVert(i + 1), _FaceVert(i + 2)] for i in range(0, len(verts) - 2, 3)]
    return _MeshData(positions=verts, texcoords=[], faces=faces)


def _position_key(pos: tuple[float, float, float], tolerance: float):
    if tolerance <= 0.0:
        return (round(pos[0], 9), round(pos[1], 9), round(pos[2], 9))
    return (
        int(round(pos[0] / tolerance)),
        int(round(pos[1] / tolerance)),
        int(round(pos[2] / tolerance)),
    )


def _face_weighted_normal(mesh: _MeshData, face: list[_FaceVert]) -> tuple[float, float, float]:
    if len(face) < 3:
        return 0.0, 0.0, 0.0
    try:
        origin = mesh.positions[face[0].vi]
    except Exception:
        return 0.0, 0.0, 0.0
    nx = ny = nz = 0.0
    for i in range(1, len(face) - 1):
        try:
            a = mesh.positions[face[i].vi]
            b = mesh.positions[face[i + 1].vi]
        except Exception:
            continue
        ax, ay, az = a[0] - origin[0], a[1] - origin[1], a[2] - origin[2]
        bx, by, bz = b[0] - origin[0], b[1] - origin[1], b[2] - origin[2]
        nx += ay * bz - az * by
        ny += az * bx - ax * bz
        nz += ax * by - ay * bx
    return nx, ny, nz


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length <= 1.0e-8 or not math.isfinite(length):
        return 0.0, 0.0, 1.0
    inv = 1.0 / length
    return v[0] * inv, v[1] * inv, v[2] * inv


def _smooth_to_obj(mesh: _MeshData, out_path: Path, *, angle_degrees: float, tolerance: float) -> Optional[str]:
    if not mesh.positions or not mesh.faces:
        return "No mesh data."

    weighted_normals = [_face_weighted_normal(mesh, face) for face in mesh.faces]
    unit_normals = [_normalize(n) for n in weighted_normals]
    adjacency: dict[object, list[int]] = {}
    for face_idx, face in enumerate(mesh.faces):
        for fv in face:
            try:
                key = _position_key(mesh.positions[fv.vi], tolerance)
            except Exception:
                continue
            adjacency.setdefault(key, []).append(face_idx)

    cos_limit = math.cos(math.radians(max(0.0, min(180.0, angle_degrees))))
    normals: list[tuple[float, float, float]] = []
    face_normal_indices: list[list[int]] = []

    for face_idx, face in enumerate(mesh.faces):
        current = unit_normals[face_idx]
        indices: list[int] = []
        for fv in face:
            try:
                key = _position_key(mesh.positions[fv.vi], tolerance)
            except Exception:
                key = None
            sx = sy = sz = 0.0
            for adj_idx in adjacency.get(key, [face_idx]):
                if angle_degrees < 179.999:
                    other = unit_normals[adj_idx]
                    dot = current[0] * other[0] + current[1] * other[1] + current[2] * other[2]
                    if dot < cos_limit:
                        continue
                wx, wy, wz = weighted_normals[adj_idx]
                sx += wx
                sy += wy
                sz += wz
            normal = _normalize((sx, sy, sz))
            normals.append(normal)
            indices.append(len(normals))
        face_normal_indices.append(indices)

    lines = ["# EchoGraph smooth normals"]
    for x, y, z in mesh.positions:
        lines.append(f"v {x:.9g} {y:.9g} {z:.9g}")
    for u, v in mesh.texcoords:
        lines.append(f"vt {u:.9g} {v:.9g}")
    for nx, ny, nz in normals:
        lines.append(f"vn {nx:.9g} {ny:.9g} {nz:.9g}")
    for face, nidxs in zip(mesh.faces, face_normal_indices):
        if len(face) < 3 or len(face) != len(nidxs):
            continue
        tokens: list[str] = []
        for fv, nidx in zip(face, nidxs):
            vi = int(fv.vi) + 1
            if fv.vti is not None:
                tokens.append(f"{vi}/{int(fv.vti) + 1}/{nidx}")
            else:
                tokens.append(f"{vi}//{nidx}")
        lines.append("f " + " ".join(tokens))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return None


def build_normals_obj(node_item, *, force: bool = False, notify_scene: bool = False) -> tuple[str, str]:
    model = getattr(node_item, "model", None)
    src_path = (_resolve_input_path(node_item) or "").strip()
    if not src_path:
        _set_node_param(node_item, "source", "", notify_scene=False)
        _set_node_param(node_item, "path", "", notify_scene=notify_scene)
        return "", "No input mesh."
    if not os.path.exists(src_path):
        _set_node_param(node_item, "source", src_path, notify_scene=False)
        _set_node_param(node_item, "path", "", notify_scene=notify_scene)
        return "", "Input not found."

    ext = Path(src_path).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        _set_node_param(node_item, "source", src_path, notify_scene=False)
        _set_node_param(node_item, "path", "", notify_scene=notify_scene)
        return "", "Unsupported mesh."

    angle = _parse_float(_param_value(model, "angle"), 180.0, 0.0, 180.0)
    tolerance = _parse_float(_param_value(model, "weld_tolerance"), 0.0001, 0.0, 1.0)
    out_path = _output_path(node_item, src_path)
    stamp = _build_stamp(src_path, angle, tolerance)
    if not force and _cache_matches(out_path, stamp):
        out_text = str(out_path)
        _set_node_param(node_item, "source", src_path, notify_scene=False)
        _set_node_param(node_item, "path", out_text, notify_scene=notify_scene)
        return out_text, ""

    mesh = _load_obj_mesh(Path(src_path)) if ext == ".obj" else _load_tri_mesh(Path(src_path))
    err = _smooth_to_obj(mesh, out_path, angle_degrees=angle, tolerance=tolerance)
    if err:
        _set_node_param(node_item, "source", src_path, notify_scene=False)
        _set_node_param(node_item, "path", "", notify_scene=notify_scene)
        return "", err

    _write_cache_meta(out_path, stamp)
    out_text = str(out_path)
    _set_node_param(node_item, "source", src_path, notify_scene=False)
    _set_node_param(node_item, "path", out_text, notify_scene=notify_scene)
    return out_text, ""


class NormalsWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending = False
        self._last_stamp = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setMinimumWidth(0)
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(self._status, 0)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._view_btn.setMinimumHeight(24)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        layout.addWidget(self._view_btn, 0)

        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._update_normals)

    def sizeHint(self):
        return QtCore.QSize(220, NORMALS_NODE_BODY_H)

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
        QtCore.QTimer.singleShot(60, self._update_normals)

    def _set_param(self, name: str, value: str, notify_scene: bool = True):
        _set_node_param(self._node_item, name, value, notify_scene=notify_scene)

    def _update_normals(self, force: bool = False):
        self._pending = False
        path, err = build_normals_obj(self._node_item, force=force, notify_scene=True)
        if err:
            self._status.setText(err)
            self._view_btn.setEnabled(False)
            return
        display_path = _display_source_path(self._node_item) or path
        display_name = _short_filename(display_path)
        self._status.setText(display_name)
        self._status.setToolTip(Path(display_path).name if display_path else "")
        self._view_btn.setEnabled(True)

    def _on_view_clicked(self):
        self._update_normals(force=True)
        path = _param_value(self._node_item.model, "path")
        if not path:
            return
        try:
            self._node_item._open_import_preview(path)
        except Exception:
            pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = NormalsWidget(node_item)
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


NORMALS_SPEC = Spec(
    stripe_color="#38bdf8",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
