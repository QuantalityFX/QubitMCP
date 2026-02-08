from __future__ import annotations

import math
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
    return safe.strip("_") or "transforms"


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


def _transform_dir(node_item) -> Path:
    base = _workflow_dir_for_node(node_item)
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    out = base / "transforms"
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


def build_ports(node_item) -> None:
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "pos", "0,0,0")
    _ensure_param(node_item, "rot", "0,0,0")
    _ensure_param(node_item, "scl", "1,1,1")
    _ensure_hidden_params(getattr(node_item, "model", None), ["source", "path", "pos", "rot", "scl"])
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


def _resolve_input_label(node_item) -> str:
    sc = node_item.scene()
    pass_kinds = {"switch", "uv_unwrap", "texture", "texture_pro", "texture_layer"}

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
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in {"mesh", "path"}:
                chosen = edge
                break
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
                    path = _param_value(model, "path") or _param_value(model, "source")
                    if path:
                        return Path(path).name
                except Exception:
                    pass
    return ""


def _output_path(node_item, src_path: str) -> Path:
    node_name = _sanitize_name(getattr(getattr(node_item, "model", None), "name", "") or "transforms")
    src_stem = _sanitize_name(Path(src_path).stem) if src_path else "mesh"
    return _transform_dir(node_item) / f"{node_name}_{src_stem}_xform.obj"


def _parse_vec3(value: str, default: Tuple[float, float, float]) -> Tuple[float, float, float]:
    try:
        parts = [p.strip() for p in str(value or "").split(",")]
        if len(parts) >= 3:
            return (float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:
        pass
    return default


def _format_vec3(val: Tuple[float, float, float]) -> str:
    return f"{val[0]:.6f},{val[1]:.6f},{val[2]:.6f}"


def _param_vec3(model, name: str, default: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return _parse_vec3(_param_value(model, name), default)


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
    return np.zeros((points.shape[0], 2), dtype="f4")


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


def _write_obj(path: Path, points, normals, uvs) -> Optional[str]:
    if points is None or not getattr(points, "size", 0):
        return "No output mesh."
    lines = ["# EchoGraph Transform"]
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


class TransformWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending = False
        self._last_stamp = None
        self._syncing_view = False
        self._defer_bake = False
        self._drag_pending_xform = None
        self._auto_bake = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setMinimumWidth(0)
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(self._status, 0)

        def _mk_spin():
            sb = QtWidgets.QDoubleSpinBox()
            sb.setDecimals(3)
            sb.setRange(-1e9, 1e9)
            sb.setSingleStep(0.01)
            sb.setKeyboardTracking(False)
            sb.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            sb.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            sb.setMinimumHeight(20)
            sb.setFixedWidth(48)
            sb.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            sb.setStyleSheet(
                "QDoubleSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
                "border-radius:4px;padding:1px 4px;}"
            )
            return sb

        def _xyz_row(default=(0.0, 0.0, 0.0)):
            w = QtWidgets.QWidget()
            w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            l = QtWidgets.QHBoxLayout(w)
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(4)
            a = _mk_spin()
            b = _mk_spin()
            c = _mk_spin()
            a.setValue(float(default[0]))
            b.setValue(float(default[1]))
            c.setValue(float(default[2]))
            l.addWidget(a)
            l.addWidget(b)
            l.addWidget(c)
            l.addStretch(1)
            return w, (a, b, c)

        self._xform_panel = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self._xform_panel)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(4)
        form.setHorizontalSpacing(4)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldsStayAtSizeHint)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.DontWrapRows)

        pos_w, pos_xyz = _xyz_row((0.0, 0.0, 0.0))
        rot_w, rot_xyz = _xyz_row((0.0, 0.0, 0.0))
        scl_w, scl_xyz = _xyz_row((1.0, 1.0, 1.0))

        label_pos = QtWidgets.QLabel("Pos")
        label_rot = QtWidgets.QLabel("Rot")
        label_scl = QtWidgets.QLabel("Scale")
        for lb in (label_pos, label_rot, label_scl):
            lb.setStyleSheet("color:#94a3b8;font-size:10px;")
            lb.setFixedWidth(52)
            lb.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        form.addRow(label_pos, pos_w)
        form.addRow(label_rot, rot_w)
        form.addRow(label_scl, scl_w)

        self._pos_spins = pos_xyz
        self._rot_spins = rot_xyz
        self._scl_spins = scl_xyz
        self._xform_updating = False

        for sb in self._pos_spins:
            sb.editingFinished.connect(lambda k="pos": self._on_xform_edit(k))
        for sb in self._rot_spins:
            sb.editingFinished.connect(lambda k="rot": self._on_xform_edit(k))
        for sb in self._scl_spins:
            sb.editingFinished.connect(lambda k="scl": self._on_xform_edit(k))

        layout.addWidget(self._xform_panel, 0)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(64)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        btn_row.addWidget(self._view_btn, 0, QtCore.Qt.AlignLeft)

        self._bake_btn = QtWidgets.QPushButton("Bake")
        self._bake_btn.setFixedWidth(64)
        self._bake_btn.setStyleSheet(
            "QPushButton{background:#0f172a;color:#e2e8f0;border-radius:4px;padding:2px 8px;"
            "border:1px solid #334155;}"
            "QPushButton:hover{background:#1f2937;}"
            "QPushButton:disabled{background:#0b1220;color:#475569;border:1px solid #1f2937;}"
        )
        self._bake_btn.clicked.connect(self._on_bake_clicked)
        btn_row.addWidget(self._bake_btn, 0, QtCore.Qt.AlignLeft)
        btn_row.addStretch(1)

        layout.addLayout(btn_row)

        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._update_transform)

        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(120)
        self._poll_timer.timeout.connect(self._poll_view_xform)
        self._poll_timer.start()

    def sizeHint(self):
        try:
            lay = self.layout()
            if lay is not None:
                hint = lay.sizeHint()
                if hint is not None:
                    return QtCore.QSize(220, max(96, int(hint.height())))
        except Exception:
            pass
        return QtCore.QSize(220, 120)

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

    def _is_dragging(self) -> bool:
        try:
            win = _resolve_window(self._node_item)
            glv = getattr(win, "gl_view", None) if win is not None else None
            if glv is None:
                return False
            if not bool(getattr(glv, "_xform_dragging", False)):
                return False
            owner = getattr(glv, "_xform_drag_owner", None) or getattr(glv, "_xform_gizmo_owner", None)
            if owner:
                node_name = (getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
                if node_name and str(owner) != str(node_name):
                    return False
            return True
        except Exception:
            return False

    def _has_downstream(self) -> bool:
        sc = None
        try:
            sc = self._node_item.scene()
        except Exception:
            sc = None
        if sc is None:
            return False
        try:
            for edge in list(getattr(sc, "_edges", []) or []):
                if getattr(edge, "src", None) is self._node_item:
                    return True
        except Exception:
            return False
        return False

    def _should_auto_bake(self) -> bool:
        # Auto-bake only when explicitly enabled or when there is a downstream consumer.
        return bool(self._auto_bake or self._has_downstream())

    def _owner_in_scene(self, renderer, owner: str) -> bool:
        if not owner or renderer is None:
            return False
        try:
            splats = getattr(renderer, "_mgl_scene_splats", None)
            if isinstance(splats, dict) and owner in splats:
                return True
        except Exception:
            pass
        try:
            bounds = (
                getattr(renderer, "_mgl_scene_mesh_bounds_by_owner", None)
                or getattr(renderer, "_mgl_scene_bounds_by_owner", None)
            )
            if isinstance(bounds, dict) and owner in bounds:
                return True
        except Exception:
            pass
        return False

    def _schedule_update(self):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(80, self._update_transform)

    def _set_param(self, name: str, value: str, notify_scene: bool = True):
        try:
            current = ""
            for p in (getattr(self._node_item.model, "params", None) or []):
                if (p.get("name") or "").strip().lower() == (name or "").strip().lower():
                    current = p.get("value", "") or ""
                    break
            if current == value:
                return False
        except Exception:
            pass
        try:
            self._node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
            return True
        except Exception:
            return False

    def _emit_param_changed(self):
        try:
            emit_later = getattr(self._node_item, "_schedule_param_emit", None)
            if callable(emit_later):
                emit_later()
                return
        except Exception:
            pass
        try:
            sc = self._node_item.scene()
            if sc is not None and hasattr(sc, "paramChanged"):
                model = getattr(self._node_item, "model", None)
                name = getattr(model, "name", "") if model is not None else ""
                params = list(getattr(model, "params", None) or []) if model is not None else []
                sc.paramChanged.emit(name, params)
        except Exception:
            pass

    def _poll_view_xform(self):
        if self._syncing_view:
            return
        owner = (getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
        if not owner:
            return
        win = _resolve_window(self._node_item)
        glv = getattr(win, "gl_view", None) if win is not None else None
        if glv is None:
            return
        renderer = getattr(glv, "_mgl_renderer", None) or glv
        if not self._owner_in_scene(renderer, owner):
            return
        get_xf = getattr(renderer, "_mgl_get_scene_asset_xform", None)
        if not callable(get_xf):
            return
        xf = get_xf(owner)
        if not isinstance(xf, dict):
            return
        pos = tuple(xf.get("pos", (0.0, 0.0, 0.0)))
        rot = tuple(xf.get("rot", (0.0, 0.0, 0.0)))
        scl = tuple(xf.get("scl", (1.0, 1.0, 1.0)))

        model = getattr(self._node_item, "model", None)
        cur_pos = _param_vec3(model, "pos", (0.0, 0.0, 0.0))
        cur_rot = _param_vec3(model, "rot", (0.0, 0.0, 0.0))
        cur_scl = _param_vec3(model, "scl", (1.0, 1.0, 1.0))

        def _diff(a, b):
            return any(abs(float(a[i]) - float(b[i])) > 1e-4 for i in range(3))

        dragging = self._is_dragging()
        if dragging:
            if _diff(pos, cur_pos) or _diff(rot, cur_rot) or _diff(scl, cur_scl):
                self._drag_pending_xform = (pos, rot, scl)
                self._defer_bake = True
            return

        if self._drag_pending_xform is not None:
            try:
                pos, rot, scl = self._drag_pending_xform
            except Exception:
                pass
            self._drag_pending_xform = None

        if not (_diff(pos, cur_pos) or _diff(rot, cur_rot) or _diff(scl, cur_scl)):
            if self._defer_bake:
                self._defer_bake = False
                if self._should_auto_bake():
                    self._schedule_update()
            return
        self._syncing_view = True
        try:
            changed = False
            changed = self._set_param("pos", _format_vec3(pos), notify_scene=False) or changed
            changed = self._set_param("rot", _format_vec3(rot), notify_scene=False) or changed
            changed = self._set_param("scl", _format_vec3(scl), notify_scene=False) or changed
            self._set_xform_controls(pos, rot, scl)
            if changed:
                self._emit_param_changed()
            if self._should_auto_bake():
                self._schedule_update()
        finally:
            self._syncing_view = False

    def _update_transform(self, force: bool = False):
        self._pending = False
        src_path = (_resolve_input_path(self._node_item) or "").strip()

        if not src_path:
            self._status.setText("No mesh input.")
            self._view_btn.setEnabled(False)
            self._set_param("source", "", notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return
        if not os.path.exists(src_path):
            self._status.setText("Mesh not found.")
            self._view_btn.setEnabled(False)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return

        label = _resolve_input_label(self._node_item) or Path(src_path).name

        model = getattr(self._node_item, "model", None)
        pos = _param_vec3(model, "pos", (0.0, 0.0, 0.0))
        rot = _param_vec3(model, "rot", (0.0, 0.0, 0.0))
        scl = _param_vec3(model, "scl", (1.0, 1.0, 1.0))
        self._set_xform_controls(pos, rot, scl)

        identity = (
            all(abs(v) < 1e-6 for v in pos)
            and all(abs(v) < 1e-6 for v in rot)
            and all(abs(v - 1.0) < 1e-6 for v in scl)
        )

        mesh_ext = Path(src_path).suffix.lower()
        if mesh_ext not in SUPPORTED_MESH_EXTS:
            self._status.setText("Unsupported mesh.")
            self._view_btn.setEnabled(False)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return
        if (not force) and (not self._should_auto_bake()):
            self._status.setText(label)
            self._view_btn.setEnabled(True)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", src_path, notify_scene=False)
            return

        if identity:
            self._status.setText(label)
            self._view_btn.setEnabled(True)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", src_path, notify_scene=True)
            return

        if (not force) and self._is_dragging():
            self._defer_bake = True
            self._status.setText(label)
            self._view_btn.setEnabled(True)
            self._set_param("source", src_path, notify_scene=False)
            cur_path = _param_value(model, "path")
            if not cur_path:
                self._set_param("path", src_path, notify_scene=True)
            return

        try:
            st_mesh = os.stat(src_path)
            stamp = (
                src_path,
                int(st_mesh.st_mtime),
                int(st_mesh.st_size),
                float(pos[0]), float(pos[1]), float(pos[2]),
                float(rot[0]), float(rot[1]), float(rot[2]),
                float(scl[0]), float(scl[1]), float(scl[2]),
            )
        except Exception:
            stamp = (src_path, None, None, pos, rot, scl)

        out_path = _output_path(self._node_item, src_path)
        if not force and self._last_stamp == stamp and out_path.exists():
            self._status.setText(label)
            self._view_btn.setEnabled(True)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", str(out_path), notify_scene=True)
            return

        pts, norms, uvs = _load_mesh_arrays(Path(src_path))
        if pts is None or not getattr(pts, "size", 0):
            self._status.setText("Mesh load failed.")
            self._view_btn.setEnabled(False)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            return

        norms = _ensure_normals(pts, norms)
        uvs = _ensure_uvs(pts, uvs)

        pts_t, norms_t = _apply_transform(pts, norms, pos, rot, scl)
        err = _write_obj(out_path, pts_t, norms_t, uvs)
        if err:
            self._status.setText(err)
            self._view_btn.setEnabled(False)
            self._set_param("path", "", notify_scene=True)
            return

        self._last_stamp = stamp
        self._status.setText(label)
        self._view_btn.setEnabled(True)
        self._set_param("source", src_path, notify_scene=False)
        self._set_param("path", str(out_path), notify_scene=True)

    def _on_view_clicked(self):
        try:
            model = getattr(self._node_item, "model", None)
            src_path = (_param_value(model, "source") or "").strip()
            if not src_path:
                return
            owner = (getattr(model, "name", "") or "").strip()
            pos = _param_vec3(model, "pos", (0.0, 0.0, 0.0))
            rot = _param_vec3(model, "rot", (0.0, 0.0, 0.0))
            scl = _param_vec3(model, "scl", (1.0, 1.0, 1.0))
            win = _resolve_window(self._node_item)
            handler = getattr(win, "open_scene_assets", None) if win is not None else None
            assets = [{
                "path": src_path,
                "node": owner,
                "xform": {"pos": list(pos), "rot": list(rot), "scl": list(scl)},
            }]
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
            try:
                glv = getattr(win, "gl_view", None) if win is not None else None
                if glv is not None:
                    glv._xform_gizmo_owner = owner
                    glv._xform_gizmo_owner_kind = "mesh"
            except Exception:
                pass
        except Exception:
            pass

    def _on_bake_clicked(self):
        try:
            self._update_transform(force=True)
        except Exception:
            pass

    def _set_xform_controls(self, pos, rot, scl):
        if self._xform_updating:
            return
        try:
            self._xform_updating = True
            for sb, v in zip(self._pos_spins, pos):
                sb.blockSignals(True)
                sb.setValue(float(v))
                sb.blockSignals(False)
            for sb, v in zip(self._rot_spins, rot):
                sb.blockSignals(True)
                sb.setValue(float(v))
                sb.blockSignals(False)
            for sb, v in zip(self._scl_spins, scl):
                sb.blockSignals(True)
                sb.setValue(float(v))
                sb.blockSignals(False)
        finally:
            self._xform_updating = False

    def _push_view_xform(self, pos, rot, scl):
        try:
            model = getattr(self._node_item, "model", None)
            owner = (getattr(model, "name", "") or "").strip()
            if not owner:
                return
            win = _resolve_window(self._node_item)
            glv = getattr(win, "gl_view", None) if win is not None else None
            if glv is None:
                return
            renderer = getattr(glv, "_mgl_renderer", None) or glv
            if not self._owner_in_scene(renderer, owner):
                return
            set_xf = getattr(renderer, "_mgl_set_scene_asset_xform", None)
            if callable(set_xf):
                set_xf(owner, pos=pos, rot=rot, scl=scl, apply_to_scene_models=True, use_splat_xform=False)
            if hasattr(win, "update_scene_asset_xform"):
                try:
                    win.update_scene_asset_xform(owner)
                except Exception:
                    pass
            try:
                glv._xform_gizmo_owner = owner
                glv._xform_gizmo_owner_kind = "mesh"
                glv._xform_gizmo_pos = tuple(pos)
            except Exception:
                pass
        except Exception:
            pass

    def _on_xform_edit(self, kind: str):
        if self._xform_updating:
            return
        pos = tuple(sb.value() for sb in self._pos_spins)
        rot = tuple(sb.value() for sb in self._rot_spins)
        scl = tuple(sb.value() for sb in self._scl_spins)
        changed = False
        if kind == "pos":
            changed = self._set_param("pos", _format_vec3(pos), notify_scene=False) or changed
        elif kind == "rot":
            changed = self._set_param("rot", _format_vec3(rot), notify_scene=False) or changed
        elif kind == "scl":
            changed = self._set_param("scl", _format_vec3(scl), notify_scene=False) or changed
        else:
            changed = self._set_param("pos", _format_vec3(pos), notify_scene=False) or changed
            changed = self._set_param("rot", _format_vec3(rot), notify_scene=False) or changed
            changed = self._set_param("scl", _format_vec3(scl), notify_scene=False) or changed
        if changed:
            self._push_view_xform(pos, rot, scl)
            self._emit_param_changed()
            if self._should_auto_bake():
                self._schedule_update()


def render_node_body(node_item, y_cursor: int) -> int:
    body = TransformWidget(node_item)
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


TRANSFORM_SPEC = Spec(
    stripe_color="#f97316",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("transforms", TRANSFORM_SPEC)


