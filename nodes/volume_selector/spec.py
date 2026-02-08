from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Optional, Tuple

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    from PySide2 import QtWidgets, QtCore  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

SUPPORTED_MESH_EXTS = {".obj", ".fbx", ".gltf", ".glb", ".stl", ".ply", ".off", ".om"}


def _sanitize_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    return safe.strip("_") or "volume_split"


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


def _split_dir(node_item) -> Path:
    base = _workflow_dir_for_node(node_item)
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    out = base / "volume_split"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _resolve_window(node_item):
    scene = None
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


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if (p.get("name") or "").strip().lower() == key:
            return p.get("value", "") or ""
    return ""


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = _param_value(model, name).strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "y")


def _parse_vec3(value: str, default: Tuple[float, float, float]) -> Tuple[float, float, float]:
    try:
        parts = [p.strip() for p in str(value or "").split(",")]
        if len(parts) >= 3:
            return (float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:
        pass
    return default


def _param_vec3(model, name: str, default: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return _parse_vec3(_param_value(model, name), default)


def _apply_transform(points, normals, pos, rot, scl):
    try:
        import numpy as np
    except Exception:
        return points, normals
    if points is None or not getattr(points, "size", 0):
        return points, normals
    pts = points.astype("f4").reshape(-1, 3)
    norms = normals.astype("f4").reshape(-1, 3)

    try:
        c = (pts.min(axis=0) + pts.max(axis=0)) * 0.5
    except Exception:
        c = np.zeros(3, dtype="f4")

    sx, sy, sz = scl
    rx, ry, rz = rot
    rx = -float(rx)
    ry = -float(ry)
    rz = -float(rz)

    def Rx(a):
        a = math.radians(a)
        c, s = math.cos(a), math.sin(a)
        return np.array([[1.0, 0.0, 0.0], [0.0, c, s], [0.0, -s, c]], dtype="f4")

    def Ry(a):
        a = math.radians(a)
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]], dtype="f4")

    def Rz(a):
        a = math.radians(a)
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]], dtype="f4")

    R = Rx(rx) @ Ry(ry) @ Rz(rz)
    svec = np.array([sx, sy, sz], dtype="f4")

    centered = pts - c
    centered = centered * svec
    rotated = (R @ centered.T).T
    transformed = rotated + c + np.array(pos, dtype="f4")

    inv = np.array(
        [
            1.0 / sx if abs(sx) > 1e-8 else 0.0,
            1.0 / sy if abs(sy) > 1e-8 else 0.0,
            1.0 / sz if abs(sz) > 1e-8 else 0.0,
        ],
        dtype="f4",
    )
    n = norms * inv
    n = (R @ n.T).T
    try:
        lengths = np.linalg.norm(n, axis=1)
        lengths[lengths < 1e-8] = 1.0
        n = n / lengths.reshape(-1, 1)
    except Exception:
        pass
    return transformed.astype("f4"), n.astype("f4")


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
    cur = set()
    for part in str(raw).split(","):
        t = part.strip().lower()
        if t:
            cur.add(t)
    for name in names or []:
        if name:
            cur.discard(str(name).strip().lower())
    existing["value"] = ",".join(sorted(cur))
    model.params = params


def build_ports(node_item) -> None:
    _ensure_param(node_item, "mesh", "")
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "volume", "")
    _ensure_param(node_item, "invert", "0")
    _ensure_param(node_item, "path", "")
    model = getattr(node_item, "model", None)
    _ensure_hidden_params(model, ["source", "path", "invert"])
    _ensure_visible_params(model, ["mesh", "volume"])
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")
        node_item.ensure_input("volume")


def _resolve_input_path(
    node_item,
    port_names=None,
    fallback_index: Optional[int] = None,
    allow_any: bool = True,
    allow_model_fallback: bool = True,
) -> str:
    model = getattr(node_item, "model", None)
    sc = node_item.scene()

    pass_kinds = {"switch", "uv_unwrap", "texture", "texture_pro", "texture_layer"}

    def _trace(item, depth=0, visited=None) -> str:
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
        if kind in pass_kinds and sc is not None:
            try:
                edges = list(sc._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(sc._in_edges(item))
                except Exception:
                    edges = []
            if edges:
                return _trace(getattr(edges[0], "src", None), depth + 1, visited)
        return _param_value(m, "path") or _param_value(m, "mesh") or _param_value(m, "source")

    if sc is not None:
        try:
            in_edges = list(sc._ordered_in_edges(node_item))
        except Exception:
            try:
                in_edges = list(sc._in_edges(node_item))
            except Exception:
                in_edges = []
        chosen = None
        if port_names:
            wanted = {str(n).strip().lower() for n in (port_names or []) if str(n).strip()}
            for edge in in_edges:
                name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
                if (name or "").strip().lower() in wanted:
                    chosen = edge
                    break
        if chosen is None and fallback_index is not None and len(in_edges) > fallback_index:
            chosen = in_edges[fallback_index]
        if chosen is None and in_edges and allow_any:
            chosen = in_edges[0]
        if chosen is not None:
            src_item = getattr(chosen, "src", None)
            path = _trace(src_item, 0, set())
            if path:
                return path

    if model is not None and allow_model_fallback:
        return _param_value(model, "mesh") or _param_value(model, "source") or _param_value(model, "path")
    return ""


def _pick_input_edge(node_item, port_names=None, fallback_index: Optional[int] = None, allow_any: bool = True):
    sc = node_item.scene()
    if sc is None:
        return None
    try:
        in_edges = list(sc._ordered_in_edges(node_item))
    except Exception:
        try:
            in_edges = list(sc._in_edges(node_item))
        except Exception:
            in_edges = []
    if not in_edges:
        return None
    chosen = None
    if port_names:
        wanted = {str(n).strip().lower() for n in (port_names or []) if str(n).strip()}
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in wanted:
                chosen = edge
                break
    if chosen is None and fallback_index is not None and len(in_edges) > fallback_index:
        chosen = in_edges[fallback_index]
    if chosen is None and allow_any:
        chosen = in_edges[0]
    return chosen


def _resolve_input_label(node_item, port_names=None, fallback_index: Optional[int] = None) -> str:
    sc = node_item.scene()
    pass_kinds = {"switch", "uv_unwrap", "texture", "texture_pro", "texture_layer", "transforms"}

    def _trace(item, depth=0, visited=None):
        if item is None or depth > 10:
            return None
        if visited is None:
            visited = set()
        if item in visited:
            return None
        visited.add(item)
        m = getattr(item, "model", None)
        if m is None:
            return None
        kind = (getattr(m, "kind", "") or "").strip().lower()
        if kind in pass_kinds and sc is not None:
            try:
                edges = list(sc._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(sc._in_edges(item))
                except Exception:
                    edges = []
            if edges:
                return _trace(getattr(edges[0], "src", None), depth + 1, visited)
        return item

    if sc is not None:
        try:
            in_edges = list(sc._ordered_in_edges(node_item))
        except Exception:
            try:
                in_edges = list(sc._in_edges(node_item))
            except Exception:
                in_edges = []
        chosen = None
        if port_names:
            wanted = {str(n).strip().lower() for n in (port_names or []) if str(n).strip()}
            for edge in in_edges:
                name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
                if (name or "").strip().lower() in wanted:
                    chosen = edge
                    break
        if chosen is None and fallback_index is not None and len(in_edges) > fallback_index:
            chosen = in_edges[fallback_index]
        if chosen is None and in_edges:
            chosen = in_edges[0]
        if chosen is not None:
            src_item = getattr(chosen, "src", None)
            item = _trace(src_item, 0, set())
            if item is not None:
                model = getattr(item, "model", None)
                if model is not None:
                    name = (getattr(model, "name", "") or "").strip()
                    if name:
                        return name
                try:
                    path = _param_value(model, "path") or _param_value(model, "mesh") or _param_value(model, "source")
                    if path:
                        return Path(path).name
                except Exception:
                    pass
    return ""


def _output_path(node_item, mesh_path: str, volume_path: str) -> Path:
    node_name = _sanitize_name(getattr(getattr(node_item, "model", None), "name", "") or "volume_split")
    mesh_stem = _sanitize_name(Path(mesh_path).stem) if mesh_path else "mesh"
    vol_stem = _sanitize_name(Path(volume_path).stem) if volume_path else "volume"
    return _split_dir(node_item) / f"{node_name}_{mesh_stem}_in_{vol_stem}.obj"


def _load_mesh_arrays(path: Path):
    try:
        import numpy as np
    except Exception:
        return None, None, None
    try:
        from echograph.ui import gl_loaders
    except Exception:
        return None, None, None
    ext = path.suffix.lower()
    try:
        if ext == ".obj":
            pts, norms, uvs = gl_loaders.load_obj_mesh_arrays(path)
            return pts, norms, uvs
        if ext == ".fbx":
            mesh_arrays = gl_loaders.load_fbx_mesh_arrays_pyassimp(path)
            return mesh_arrays.points, mesh_arrays.normals, mesh_arrays.uvs
        if ext in (".gltf", ".glb"):
            mesh_arrays = gl_loaders.load_gltf_mesh_arrays(path)
            return mesh_arrays.points, mesh_arrays.normals, mesh_arrays.uvs
    except Exception:
        return None, None, None

    try:
        model = gl_loaders.load_model(path)
    except Exception:
        model = None
    if model is None or not model.vertices:
        return None, None, None
    pts = np.array(model.vertices, dtype="f4").reshape(-1, 3)
    norms = np.zeros_like(pts)
    uvs = np.zeros((pts.shape[0], 2), dtype="f4")
    return pts, norms, uvs


def _volume_bounds(path: Path) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    try:
        import numpy as np
    except Exception:
        return None
    pts, _, _ = _load_mesh_arrays(path)
    if pts is None or not getattr(pts, "size", 0):
        return None
    try:
        bmin = pts.min(axis=0)
        bmax = pts.max(axis=0)
    except Exception:
        return None
    return (float(bmin[0]), float(bmin[1]), float(bmin[2])), (float(bmax[0]), float(bmax[1]), float(bmax[2]))


def _ensure_normals(points, normals):
    try:
        import numpy as np
    except Exception:
        return normals
    if normals is not None and getattr(normals, "size", 0):
        return normals
    normals = np.zeros_like(points)
    for i in range(0, points.shape[0], 3):
        tri = points[i:i + 3]
        if tri.shape[0] != 3:
            continue
        a, b, c = tri
        n = np.cross(b - a, c - a)
        length = float(np.linalg.norm(n))
        if length > 1e-6:
            n = n / length
        normals[i:i + 3] = n
    return normals


def _ensure_uvs(points, uvs):
    try:
        import numpy as np
    except Exception:
        return uvs
    if uvs is not None and getattr(uvs, "size", 0):
        return uvs
    try:
        bmin = points.min(axis=0)
        bmax = points.max(axis=0)
    except Exception:
        bmin = (0.0, 0.0, 0.0)
        bmax = (1.0, 1.0, 1.0)
    dx = float(bmax[0] - bmin[0]) if float(bmax[0] - bmin[0]) != 0.0 else 1.0
    dz = float(bmax[2] - bmin[2]) if float(bmax[2] - bmin[2]) != 0.0 else 1.0
    u = (points[:, 0] - float(bmin[0])) / dx
    v = (points[:, 2] - float(bmin[2])) / dz
    uvs = np.stack([u, v], axis=1).astype("f4")
    return uvs


def _split_mesh(mesh_path: Path, volume_path: Path, invert: bool = False, mesh_xform: Optional[dict] = None):
    try:
        import numpy as np
    except Exception:
        return None, None, None, "numpy unavailable"

    pts, norms, uvs = _load_mesh_arrays(mesh_path)
    if pts is None or not getattr(pts, "size", 0):
        return None, None, None, "Mesh load failed."
    if pts.shape[0] % 3 != 0:
        return None, None, None, "Mesh is not triangulated."

    bounds = _volume_bounds(volume_path)
    if bounds is None:
        return None, None, None, "Volume mesh missing or invalid."
    (minx, miny, minz), (maxx, maxy, maxz) = bounds
    bmin = np.array([minx, miny, minz], dtype="f4")
    bmax = np.array([maxx, maxy, maxz], dtype="f4")

    norms = _ensure_normals(pts, norms)
    uvs = _ensure_uvs(pts, uvs)
    if mesh_xform:
        try:
            pos = mesh_xform.get("pos", (0.0, 0.0, 0.0))
            rot = mesh_xform.get("rot", (0.0, 0.0, 0.0))
            scl = mesh_xform.get("scl", (1.0, 1.0, 1.0))
            pts, norms = _apply_transform(pts, norms, pos, rot, scl)
        except Exception:
            pass

    kept_pts = []
    kept_norms = []
    kept_uvs = []
    eps = 1e-6
    for i in range(0, pts.shape[0], 3):
        tri = pts[i:i + 3]
        if tri.shape[0] != 3:
            continue
        inside = True
        for v in tri:
            if (v < (bmin - eps)).any() or (v > (bmax + eps)).any():
                inside = False
                break
        keep = inside if not invert else not inside
        if not keep:
            continue
        kept_pts.extend(tri.tolist())
        kept_norms.extend(norms[i:i + 3].tolist())
        kept_uvs.extend(uvs[i:i + 3].tolist())

    if not kept_pts:
        return None, None, None, "No faces inside volume."

    pts_out = np.array(kept_pts, dtype="f4").reshape(-1, 3)
    norms_out = np.array(kept_norms, dtype="f4").reshape(-1, 3)
    uvs_out = np.array(kept_uvs, dtype="f4").reshape(-1, 2)
    return pts_out, norms_out, uvs_out, None


def _write_obj(path: Path, points, normals, uvs) -> Optional[str]:
    if points is None or not getattr(points, "size", 0):
        return "No output mesh."
    lines = ["# EchoGraph Volume Split"]
    for x, y, z in points:
        lines.append(f"v {float(x):.6f} {float(y):.6f} {float(z):.6f}")
    for u, v in uvs:
        lines.append(f"vt {float(u):.6f} {float(v):.6f}")
    for nx, ny, nz in normals:
        lines.append(f"vn {float(nx):.6f} {float(ny):.6f} {float(nz):.6f}")
    tri_count = int(points.shape[0] // 3)
    for i in range(tri_count):
        a = i * 3 + 1
        b = a + 1
        c = a + 2
        lines.append(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return None


class VolumeSplitWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending = False
        self._last_stamp = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setMinimumWidth(0)
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        top_row.addWidget(self._status, 1)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(64)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        top_row.addWidget(self._view_btn, 0)

        layout.addLayout(top_row, 0)

        invert_row = QtWidgets.QHBoxLayout()
        invert_row.setContentsMargins(0, 0, 0, 0)
        invert_row.setSpacing(6)

        invert_label = QtWidgets.QLabel("Invert")
        invert_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        invert_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        invert_row.addWidget(invert_label, 0)

        self._invert = QtWidgets.QCheckBox()
        self._invert.setStyleSheet("QCheckBox{color:#e6edf3;}")
        self._invert.stateChanged.connect(self._on_invert_changed)
        invert_row.addWidget(self._invert, 0)
        invert_row.addStretch(1)

        layout.addLayout(invert_row, 0)

        self._ensure_scene()
        try:
            self._invert.setChecked(_param_bool(getattr(self._node_item, "model", None), "invert", False))
        except Exception:
            pass

        QtCore.QTimer.singleShot(0, self._update_split)

    def sizeHint(self):
        return QtCore.QSize(220, 54)

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
        QtCore.QTimer.singleShot(80, self._update_split)

    def _on_invert_changed(self, state: int):
        val = "1" if bool(state) else "0"
        self._set_param("invert", val, notify_scene=True)
        self._schedule_update()

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

    def _update_split(self, force: bool = False):
        self._pending = False
        invert = False
        try:
            invert = _param_bool(getattr(self._node_item, "model", None), "invert", False)
        except Exception:
            invert = False
        mesh_path = (_resolve_input_path(
            self._node_item,
            {"mesh", "source", "path"},
            allow_any=False,
            allow_model_fallback=False,
        ) or "").strip()
        mesh_label = _resolve_input_label(
            self._node_item,
            {"mesh", "source", "path"},
        )
        volume_path = (_resolve_input_path(
            self._node_item,
            {"volume", "mask"},
            allow_any=False,
            allow_model_fallback=False,
        ) or "").strip()
        volume_label = _resolve_input_label(
            self._node_item,
            {"volume", "mask"},
        )
        edges = []
        try:
            sc = self._node_item.scene()
            if sc is not None:
                try:
                    edges = list(sc._ordered_in_edges(self._node_item))
                except Exception:
                    try:
                        edges = list(sc._in_edges(self._node_item))
                    except Exception:
                        edges = []
        except Exception:
            edges = []
        if not edges:
            self._status.setText("No inputs connected.")
            self._view_btn.setEnabled(False)
            self._set_param("mesh", "", notify_scene=False)
            self._set_param("source", "", notify_scene=False)
            self._set_param("volume", "", notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return
        if not mesh_path and not volume_path and edges:
            mesh_path = (_resolve_input_path(
                self._node_item,
                {"mesh", "source", "path"},
                fallback_index=0,
                allow_model_fallback=False,
            ) or "").strip()
            if len(edges) > 1:
                volume_path = (_resolve_input_path(
                    self._node_item,
                    {"volume", "mask"},
                    fallback_index=1,
                    allow_model_fallback=False,
                ) or "").strip()
        mesh_xform = None
        mesh_src_item = None
        mesh_edge = _pick_input_edge(self._node_item, {"mesh", "source", "path"}, fallback_index=0, allow_any=True)
        if mesh_edge is not None:
            mesh_src_item = getattr(mesh_edge, "src", None)
        if mesh_src_item is not None:
            mesh_model = getattr(mesh_src_item, "model", None)
            mesh_kind = (getattr(mesh_model, "kind", "") or "").strip().lower() if mesh_model is not None else ""
            if mesh_kind == "transforms" and mesh_model is not None:
                mesh_source = (_param_value(mesh_model, "source") or _param_value(mesh_model, "path") or "").strip()
                mesh_out = (_param_value(mesh_model, "path") or mesh_source or "").strip()
                if mesh_source and mesh_out and mesh_out != mesh_source:
                    # Baked output path already includes transform
                    mesh_path = mesh_out
                else:
                    if mesh_source:
                        mesh_path = mesh_source
                    mesh_xform = {
                        "pos": _param_vec3(mesh_model, "pos", (0.0, 0.0, 0.0)),
                        "rot": _param_vec3(mesh_model, "rot", (0.0, 0.0, 0.0)),
                        "scl": _param_vec3(mesh_model, "scl", (1.0, 1.0, 1.0)),
                    }

        if not mesh_path:
            if volume_path:
                self._status.setText(volume_label or "Volume only.")
                self._view_btn.setEnabled(True)
                self._set_param("mesh", "", notify_scene=False)
                self._set_param("source", "", notify_scene=False)
                self._set_param("volume", volume_path, notify_scene=False)
                self._set_param("path", volume_path, notify_scene=True)
            else:
                self._status.setText("No mesh input.")
                self._view_btn.setEnabled(False)
                self._set_param("mesh", "", notify_scene=False)
                self._set_param("source", "", notify_scene=False)
                self._set_param("volume", "", notify_scene=False)
                self._set_param("path", "", notify_scene=True)
            return
        if not volume_path:
            self._status.setText("No volume input.")
            self._view_btn.setEnabled(False)
            self._set_param("mesh", mesh_path, notify_scene=False)
            self._set_param("source", mesh_path, notify_scene=False)
            self._set_param("volume", "", notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return
        if not os.path.exists(mesh_path):
            self._status.setText("Mesh not found.")
            self._view_btn.setEnabled(False)
            self._set_param("mesh", mesh_path, notify_scene=False)
            self._set_param("source", mesh_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return
        if not os.path.exists(volume_path):
            self._status.setText("Volume not found.")
            self._view_btn.setEnabled(False)
            self._set_param("mesh", mesh_path, notify_scene=False)
            self._set_param("source", mesh_path, notify_scene=False)
            self._set_param("volume", volume_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return

        mesh_ext = Path(mesh_path).suffix.lower()
        vol_ext = Path(volume_path).suffix.lower()
        if mesh_ext not in SUPPORTED_MESH_EXTS:
            self._status.setText("Unsupported mesh.")
            self._view_btn.setEnabled(False)
            self._set_param("mesh", mesh_path, notify_scene=False)
            self._set_param("source", mesh_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return
        if vol_ext not in SUPPORTED_MESH_EXTS:
            self._status.setText("Unsupported volume.")
            self._view_btn.setEnabled(False)
            self._set_param("volume", volume_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return

        try:
            st_mesh = os.stat(mesh_path)
            st_vol = os.stat(volume_path)
            stamp = (
                mesh_path,
                int(st_mesh.st_mtime),
                int(st_mesh.st_size),
                volume_path,
                int(st_vol.st_mtime),
                int(st_vol.st_size),
                int(bool(invert)),
            )
            if mesh_xform:
                stamp = stamp + (
                    float(mesh_xform["pos"][0]), float(mesh_xform["pos"][1]), float(mesh_xform["pos"][2]),
                    float(mesh_xform["rot"][0]), float(mesh_xform["rot"][1]), float(mesh_xform["rot"][2]),
                    float(mesh_xform["scl"][0]), float(mesh_xform["scl"][1]), float(mesh_xform["scl"][2]),
                )
        except Exception:
            stamp = (mesh_path, None, None, volume_path, None, None, int(bool(invert)))
            if mesh_xform:
                stamp = stamp + (mesh_xform.get("pos"), mesh_xform.get("rot"), mesh_xform.get("scl"))

        out_path = _output_path(self._node_item, mesh_path, volume_path)
        if not force and self._last_stamp == stamp and out_path.exists():
            self._status.setText(mesh_label or Path(mesh_path).name)
            self._view_btn.setEnabled(True)
            self._set_param("mesh", mesh_path, notify_scene=False)
            self._set_param("source", mesh_path, notify_scene=False)
            self._set_param("volume", volume_path, notify_scene=False)
            self._set_param("path", str(out_path), notify_scene=True)
            return

        pts, norms, uvs, err = _split_mesh(Path(mesh_path), Path(volume_path), invert=invert, mesh_xform=mesh_xform)
        if err:
            self._status.setText(err)
            # Allow viewing inputs even when no faces are inside/outside the volume.
            if "no faces" in str(err).lower():
                self._view_btn.setEnabled(True)
                self._set_param("mesh", mesh_path, notify_scene=False)
                self._set_param("source", mesh_path, notify_scene=False)
                self._set_param("volume", volume_path, notify_scene=False)
                # Point path at the original mesh so View shows both meshes.
                self._set_param("path", mesh_path, notify_scene=True)
            else:
                self._view_btn.setEnabled(False)
                self._set_param("mesh", mesh_path, notify_scene=False)
                self._set_param("source", mesh_path, notify_scene=False)
                self._set_param("volume", volume_path, notify_scene=False)
                self._set_param("path", "", notify_scene=True)
            return

        err = _write_obj(out_path, pts, norms, uvs)
        if err:
            self._status.setText(err)
            self._view_btn.setEnabled(False)
            self._set_param("path", "", notify_scene=True)
            return

        self._last_stamp = stamp
        self._status.setText(mesh_label or Path(mesh_path).name)
        self._view_btn.setEnabled(True)
        self._set_param("mesh", mesh_path, notify_scene=False)
        self._set_param("source", mesh_path, notify_scene=False)
        self._set_param("volume", volume_path, notify_scene=False)
        self._set_param("path", str(out_path), notify_scene=True)

    def _on_view_clicked(self):
        try:
            path = ""
            mesh_val = ""
            volume_val = ""
            for entry in (getattr(self._node_item.model, "params", None) or []):
                key = (entry.get("name") or "").strip().lower()
                if key == "path":
                    path = (entry.get("value") or "").strip()
                elif key == "mesh":
                    mesh_val = (entry.get("value") or "").strip()
                elif key == "volume":
                    volume_val = (entry.get("value") or "").strip()
            win = _resolve_window(self._node_item)
            handler = getattr(win, "open_scene_assets", None) if win is not None else None
            node_name = (getattr(self._node_item.model, "name", "") or "").strip()
            if volume_val and mesh_val:
                assets = []
                if path:
                    assets.append({
                        "path": path,
                        "node": node_name or Path(path).name,
                    })
                assets.append({
                    "path": volume_val,
                    "node": (node_name + " Volume").strip() or Path(volume_val).name,
                    "wire_only": True,
                    "volume": True,
                })
                if callable(handler) and assets:
                    try:
                        handler(assets, frame=False)
                        return
                    except TypeError:
                        try:
                            handler(assets)
                            return
                        except Exception:
                            pass
                    except Exception:
                        pass
            if volume_val and not mesh_val:
                win = _resolve_window(self._node_item)
                handler = getattr(win, "open_scene_assets", None) if win is not None else None
                assets = [{
                    "path": volume_val,
                    "node": (getattr(self._node_item.model, "name", "") or "").strip(),
                    "wire_only": True,
                    "volume": True,
                }]
                if callable(handler):
                    try:
                        handler(assets, frame=False)
                        return
                    except TypeError:
                        try:
                            handler(assets)
                            return
                        except Exception:
                            pass
                    except Exception:
                        pass
            if not path:
                return
            self._node_item._open_import_preview(path)
        except Exception:
            pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = VolumeSplitWidget(node_item)
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


VOLUME_SPLIT_SPEC = Spec(
    stripe_color="#38bdf8",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("volume_selector", VOLUME_SPLIT_SPEC)
    _core.register_spec("split_volume", VOLUME_SPLIT_SPEC)
