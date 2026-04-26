from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    from PySide2 import QtWidgets, QtCore  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

SUPPORTED_MESH_EXTS = {".obj", ".fbx", ".gltf", ".glb", ".stl", ".ply", ".off", ".om", ".bvh"}
RIG_PASSTHROUGH_EXTS = {".bvh"}
FBX_KIND_ALIASES = {"fbx_import", "fbx import", "fbximport"}
MOCAP_KIND_ALIASES = {"mocap_import", "mocap import", "mocapimport", "bvh_import", "bvh import", "bvhimport"}


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


def _repo_logs_dir() -> Path:
    try:
        root = Path(__file__).resolve().parents[2]
    except Exception:
        root = Path.cwd()
    out = root / "logs"
    try:
        out.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return out


def _debug_log(event: str, *, node_item=None, model=None, enabled: Optional[bool] = None, **fields) -> None:
    if enabled is None:
        if model is None and node_item is not None:
            try:
                model = getattr(node_item, "model", None)
            except Exception:
                model = None
        enabled = _param_bool(model, "debug", False) if model is not None else False
    if not bool(enabled):
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": str(event or ""),
    }
    record.update(fields or {})
    try:
        log_path = _repo_logs_dir() / "transforms_debug.jsonl"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


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


def _node_kind(item) -> str:
    model = getattr(item, "model", None)
    return str(getattr(model, "kind", "") or "").strip().lower()


def _edge_dst_name(edge) -> str:
    return str(
        getattr(edge, "dst_port_name", None)
        or getattr(edge, "dst_label", None)
        or getattr(edge, "dst_name", None)
        or ""
    ).strip()


def _ordered_in_edges(scene, item) -> list:
    if scene is None or item is None:
        return []
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _connected_input_item(node_item):
    try:
        sc = node_item.scene()
    except Exception:
        sc = None
    edges = _ordered_in_edges(sc, node_item)
    chosen = None
    for edge in edges:
        if _edge_dst_name(edge).lower() in {"mesh", "path", "source"}:
            chosen = edge
            break
    if chosen is None and edges:
        chosen = edges[0]
    return getattr(chosen, "src", None) if chosen is not None else None


def _fbx_import_resolved_path(source_item, *, persist: bool = False) -> str:
    model = getattr(source_item, "model", None)
    try:
        from nodes.fbx_import import spec as fbx_spec  # type: ignore

        result = fbx_spec.resolve_fbx_import_sources(
            source_item,
            persist=bool(persist),
            validate_bind_data=False,
            validate_animation_data=False,
        )
        path = str(getattr(result, "effective_sources", {}).get("rest_geometry", "") or "").strip()
        if path:
            return path
    except Exception:
        pass
    for raw in (
        getattr(model, "_fbx_resolved_rest_geometry", None),
        _param_value(model, "resolved_rest_geometry"),
        _param_value(model, "rest_geometry"),
        _param_value(model, "path"),
    ):
        text = str(raw or "").strip()
        if text:
            return text
    return ""


def _mocap_import_resolved_path(source_item, *, persist: bool = False) -> str:
    model = getattr(source_item, "model", None)
    try:
        from nodes.mocap_import import spec as mocap_spec  # type: ignore

        result = mocap_spec.resolve_mocap_import_source(
            source_item,
            persist=bool(persist),
            validate_animation=False,
        )
        path = str(getattr(result, "resolved_path", "") or "").strip()
        if path:
            return path
    except Exception:
        pass
    for raw in (
        getattr(model, "_mocap_resolved_path", None),
        _param_value(model, "resolved_path"),
        _param_value(model, "path"),
    ):
        text = str(raw or "").strip()
        if text:
            return text
    return ""


def _source_path_from_item(source_item) -> str:
    kind = _node_kind(source_item)
    if kind in FBX_KIND_ALIASES:
        return _fbx_import_resolved_path(source_item)
    if kind in MOCAP_KIND_ALIASES:
        return _mocap_import_resolved_path(source_item)
    model = getattr(source_item, "model", None)
    return _param_value(model, "path") or _param_value(model, "source") or _param_value(model, "mesh")


def _cache_rig_context_for_view(win, asset: dict) -> None:
    try:
        glv = getattr(win, "gl_view", None) if win is not None else None
        path_text = str(asset.get("path") or "").strip()
        context_obj = asset.get("fbx_rig_context")
        if glv is None or not path_text or not isinstance(context_obj, dict):
            return
        path_obj = Path(path_text)
        try:
            cache_key = str(path_obj.resolve())
        except Exception:
            cache_key = str(path_obj)
        try:
            mtime = float(path_obj.stat().st_mtime)
        except Exception:
            mtime = None
        cache = getattr(glv, "_mgl_fbx_rig_context_cache", None)
        if not isinstance(cache, dict):
            cache = {}
        cache_entry = {"mtime": mtime, "context": dict(context_obj)}
        cache[cache_key] = cache_entry
        cache[str(path_obj)] = cache_entry
        setattr(glv, "_mgl_fbx_rig_context_cache", cache)
    except Exception:
        pass


def _rig_preview_asset_from_item(source_item, *, owner: str, xform: dict) -> tuple[Optional[dict], str]:
    kind = _node_kind(source_item)
    model = getattr(source_item, "model", None)
    asset = None
    if kind in FBX_KIND_ALIASES:
        try:
            from nodes.fbx_import import spec as fbx_spec  # type: ignore

            result = fbx_spec.resolve_fbx_import_sources(
                source_item,
                persist=True,
                validate_bind_data=True,
                validate_animation_data=True,
            )
            if getattr(result, "status", "") == "error":
                return None, "\n".join(result.message_lines())
            build_preview = getattr(fbx_spec, "_build_preview_asset", None)
            if callable(build_preview):
                asset = build_preview(model, result)
        except Exception as exc:
            return None, f"FBX preview failed: {exc}"
    elif kind in MOCAP_KIND_ALIASES:
        try:
            from nodes.mocap_import import spec as mocap_spec  # type: ignore

            result = mocap_spec.resolve_mocap_import_source(
                source_item,
                persist=True,
                validate_animation=True,
            )
            if getattr(result, "status", "") == "error":
                return None, "\n".join(result.message_lines())
            build_preview = getattr(mocap_spec, "_build_preview_asset", None)
            if callable(build_preview):
                asset = build_preview(model, result)
        except Exception as exc:
            return None, f"Mocap preview failed: {exc}"

    if not isinstance(asset, dict):
        return None, ""
    asset = dict(asset)
    asset["node"] = owner
    asset["visible"] = True
    asset["xform"] = xform
    return asset, ""


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
    _ensure_param(node_item, "debug", "0")
    _ensure_param(node_item, "auto_bake", "0")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        ["source", "path", "pos", "rot", "scl", "debug", "auto_bake"],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


def _resolve_input_path(node_item) -> str:
    model = getattr(node_item, "model", None)
    source_item = _connected_input_item(node_item)
    if source_item is not None:
        path = _source_path_from_item(source_item)
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


def _cache_meta_path(path: Path) -> Path:
    return Path(str(path) + ".meta.json")


def _stamp_key(stamp) -> str:
    try:
        return json.dumps(stamp, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        try:
            return repr(stamp)
        except Exception:
            return ""


def _read_cached_stamp(path: Path) -> str:
    meta_path = _cache_meta_path(path)
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    value = data.get("stamp", "")
    return str(value or "")


def _write_cached_stamp(path: Path, stamp) -> None:
    meta_path = _cache_meta_path(path)
    payload = {"stamp": _stamp_key(stamp)}
    try:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _cache_matches(path: Path, stamp) -> bool:
    return _read_cached_stamp(path) == _stamp_key(stamp)


def _mtime_ns_from_stat(st) -> int:
    try:
        return int(getattr(st, "st_mtime_ns"))
    except Exception:
        try:
            return int(float(getattr(st, "st_mtime", 0.0)) * 1_000_000_000)
        except Exception:
            return 0


def _output_is_fresh(out_path: Path, inputs: list[str]) -> bool:
    try:
        st_out = os.stat(out_path)
    except Exception:
        return False
    out_ns = _mtime_ns_from_stat(st_out)
    if out_ns <= 0:
        return False
    for path in inputs or []:
        p = str(path or "").strip()
        if not p:
            return False
        try:
            st_in = os.stat(p)
        except Exception:
            return False
        if _mtime_ns_from_stat(st_in) > out_ns:
            return False
    return True


def _paths_equal(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.normpath(str(a or ""))) == os.path.normcase(os.path.normpath(str(b or "")))
    except Exception:
        return str(a or "") == str(b or "")


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

    # Match viewport transform semantics: move pivot to mesh center,
    # apply scale/rotation there, then place by explicit position only.
    centered = pts - c
    centered = centered * svec
    rotated = (R @ centered.T).T
    transformed = rotated + np.array(pos, dtype="f4")

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
        self._history_drag_before = None
        self._auto_bake = _param_bool(getattr(node_item, "model", None), "auto_bake", False)

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

        debug_row = QtWidgets.QHBoxLayout()
        debug_row.setContentsMargins(0, 0, 0, 0)
        debug_row.setSpacing(6)

        debug_label = QtWidgets.QLabel("Debug")
        debug_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        debug_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        debug_row.addWidget(debug_label, 0)

        self._debug = QtWidgets.QCheckBox()
        self._debug.setStyleSheet("QCheckBox{color:#e6edf3;}")
        self._debug.stateChanged.connect(self._on_debug_changed)
        debug_row.addWidget(self._debug, 0)
        debug_row.addStretch(1)

        layout.addLayout(debug_row)

        self._ensure_scene()
        try:
            self._debug.setChecked(_param_bool(getattr(self._node_item, "model", None), "debug", False))
        except Exception:
            pass
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
        # In deferred/manual mode, ignore this node's own paramChanged echo.
        # We already synced local params and viewport directly.
        node_name = (getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
        changed_name = (name or "").strip() if isinstance(name, str) else str(name or "").strip()
        if node_name and changed_name == node_name and (not self._should_auto_bake()):
            return
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

    def _should_auto_bake(self) -> bool:
        model = getattr(self._node_item, "model", None)
        self._auto_bake = _param_bool(model, "auto_bake", self._auto_bake)
        # Keep gizmo edits responsive by default; bake only when explicitly enabled.
        return bool(self._auto_bake)

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

    def _record_history(self, before_pos, before_rot, before_scl, after_pos, after_rot, after_scl):
        try:
            win = _resolve_window(self._node_item)
            if win is None:
                return
            if bool(getattr(win, "_xform_history_busy", False)):
                return
            model = getattr(self._node_item, "model", None)
            if model is None:
                return
            owner = (getattr(model, "name", "") or "").strip()
            if not owner:
                return
            try:
                from echograph.ui import actions
            except Exception:
                return
            fn = getattr(actions, "record_transforms_node_xform", None)
            if not callable(fn):
                return
            before = {
                "pos": [float(before_pos[0]), float(before_pos[1]), float(before_pos[2])],
                "rot": [float(before_rot[0]), float(before_rot[1]), float(before_rot[2])],
                "scl": [float(before_scl[0]), float(before_scl[1]), float(before_scl[2])],
            }
            after = {
                "pos": [float(after_pos[0]), float(after_pos[1]), float(after_pos[2])],
                "rot": [float(after_rot[0]), float(after_rot[1]), float(after_rot[2])],
                "scl": [float(after_scl[0]), float(after_scl[1]), float(after_scl[2])],
            }
            fn(win, model, owner, before, after)
        except Exception:
            pass

    def _poll_view_xform(self):
        if self._syncing_view:
            return
        owner = (getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
        if not owner:
            self._history_drag_before = None
            return
        win = _resolve_window(self._node_item)
        glv = getattr(win, "gl_view", None) if win is not None else None
        if glv is None:
            self._history_drag_before = None
            return
        renderer = getattr(glv, "_mgl_renderer", None) or glv
        if not self._owner_in_scene(renderer, owner):
            self._history_drag_before = None
            return
        get_xf = getattr(renderer, "_mgl_get_scene_asset_xform", None)
        if not callable(get_xf):
            self._history_drag_before = None
            return
        xf = get_xf(owner)
        if not isinstance(xf, dict):
            self._history_drag_before = None
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
                if self._history_drag_before is None:
                    self._history_drag_before = (cur_pos, cur_rot, cur_scl)
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
            self._history_drag_before = None
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
                _debug_log(
                    "viewport_xform_sync",
                    model=model,
                    node=(getattr(model, "name", "") or "").strip() if model is not None else "",
                    pos=[float(pos[0]), float(pos[1]), float(pos[2])],
                    rot=[float(rot[0]), float(rot[1]), float(rot[2])],
                    scl=[float(scl[0]), float(scl[1]), float(scl[2])],
                    source=(_param_value(model, "source") if model is not None else ""),
                    path=(_param_value(model, "path") if model is not None else ""),
                    auto_bake=bool(self._should_auto_bake()),
                )
                # In deferred/manual mode we keep edits local to the node model.
                # This avoids global paramChanged fan-out (scene outliner refresh, etc.)
                # while still persisting values on workflow save.
                if self._should_auto_bake():
                    self._emit_param_changed()
                if self._history_drag_before is not None:
                    try:
                        before_pos, before_rot, before_scl = self._history_drag_before
                        self._record_history(before_pos, before_rot, before_scl, pos, rot, scl)
                    except Exception:
                        pass
                    self._history_drag_before = None
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
        source_item = _connected_input_item(self._node_item)
        source_kind = _node_kind(source_item)
        pos = _param_vec3(model, "pos", (0.0, 0.0, 0.0))
        rot = _param_vec3(model, "rot", (0.0, 0.0, 0.0))
        scl = _param_vec3(model, "scl", (1.0, 1.0, 1.0))
        self._set_xform_controls(pos, rot, scl)
        _debug_log(
            "xform_update_start",
            model=model,
            node=(getattr(model, "name", "") or "").strip() if model is not None else "",
            source_path=src_path,
            pos=[float(pos[0]), float(pos[1]), float(pos[2])],
            rot=[float(rot[0]), float(rot[1]), float(rot[2])],
            scl=[float(scl[0]), float(scl[1]), float(scl[2])],
            force=bool(force),
            auto_bake=bool(self._should_auto_bake()),
        )

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
        if mesh_ext in RIG_PASSTHROUGH_EXTS or source_kind in FBX_KIND_ALIASES or source_kind in MOCAP_KIND_ALIASES:
            self._status.setText(label)
            self._view_btn.setEnabled(True)
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", src_path, notify_scene=True)
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
        cache_hit = False
        if (not force) and out_path.exists():
            if self._last_stamp == stamp:
                cache_hit = True
            elif _cache_matches(out_path, stamp):
                cache_hit = True
            elif self._last_stamp is None:
                cur_path = (_param_value(model, "path") if model is not None else "").strip()
                if _paths_equal(cur_path, str(out_path)) and _output_is_fresh(out_path, [src_path]):
                    cache_hit = True
                    _write_cached_stamp(out_path, stamp)
        if cache_hit:
            self._last_stamp = stamp
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

        _write_cached_stamp(out_path, stamp)
        self._last_stamp = stamp
        self._status.setText(label)
        self._view_btn.setEnabled(True)
        self._set_param("source", src_path, notify_scene=False)
        self._set_param("path", str(out_path), notify_scene=True)

    def _on_view_clicked(self):
        try:
            model = getattr(self._node_item, "model", None)
            source_item = _connected_input_item(self._node_item)
            src_path = (_resolve_input_path(self._node_item) or _param_value(model, "source") or "").strip()
            if not src_path:
                self._status.setText("No mesh input.")
                return
            owner = (getattr(model, "name", "") or "").strip()
            pos = _param_vec3(model, "pos", (0.0, 0.0, 0.0))
            rot = _param_vec3(model, "rot", (0.0, 0.0, 0.0))
            scl = _param_vec3(model, "scl", (1.0, 1.0, 1.0))
            xform = {"pos": list(pos), "rot": list(rot), "scl": list(scl)}
            win = _resolve_window(self._node_item)
            handler = getattr(win, "open_scene_assets", None) if win is not None else None
            asset, preview_error = _rig_preview_asset_from_item(source_item, owner=owner, xform=xform)
            if asset is None:
                if preview_error:
                    self._status.setText(preview_error.splitlines()[0][:80])
                asset = {
                    "path": src_path,
                    "node": owner,
                    "xform": xform,
                }
            assets = [asset]
            _cache_rig_context_for_view(win, asset)
            if callable(handler):
                try:
                    handler(assets, frame=True)
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

    def _on_debug_changed(self, state: int):
        val = "1" if bool(state) else "0"
        self._set_param("debug", val, notify_scene=True)

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
        model = getattr(self._node_item, "model", None)
        before_pos = _param_vec3(model, "pos", (0.0, 0.0, 0.0))
        before_rot = _param_vec3(model, "rot", (0.0, 0.0, 0.0))
        before_scl = _param_vec3(model, "scl", (1.0, 1.0, 1.0))
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
            self._record_history(before_pos, before_rot, before_scl, pos, rot, scl)
            try:
                model = getattr(self._node_item, "model", None)
            except Exception:
                model = None
            _debug_log(
                "xform_edit_commit",
                model=model,
                node=(getattr(model, "name", "") or "").strip() if model is not None else "",
                kind=str(kind or ""),
                pos=[float(pos[0]), float(pos[1]), float(pos[2])],
                rot=[float(rot[0]), float(rot[1]), float(rot[2])],
                scl=[float(scl[0]), float(scl[1]), float(scl[2])],
                source=(_param_value(model, "source") if model is not None else ""),
                path=(_param_value(model, "path") if model is not None else ""),
                auto_bake=bool(self._should_auto_bake()),
            )
            if self._should_auto_bake():
                self._emit_param_changed()
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


