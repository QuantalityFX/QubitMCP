from __future__ import annotations

import json
import os
import re
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
        log_path = _repo_logs_dir() / "split_volume_transform_debug.jsonl"
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
    _ensure_param(node_item, "debug", "0")
    model = getattr(node_item, "model", None)
    _ensure_hidden_params(model, ["source", "path", "invert", "debug"])
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


def _ordered_in_edges(item) -> list:
    sc = None
    try:
        sc = item.scene()
    except Exception:
        sc = None
    if sc is None:
        return []
    try:
        return list(sc._ordered_in_edges(item))
    except Exception:
        try:
            return list(sc._in_edges(item))
        except Exception:
            return []


def _resolve_input_asset(
    node_item,
    port_names=None,
    fallback_index: Optional[int] = None,
    allow_any: bool = True,
) -> Dict[str, object]:
    edge = _pick_input_edge(
        node_item,
        port_names=port_names,
        fallback_index=fallback_index,
        allow_any=allow_any,
    )
    src_item = getattr(edge, "src", None) if edge is not None else None
    pass_kinds = {"switch", "uv_unwrap", "texture", "texture_pro", "texture_layer"}

    item = src_item
    visited = set()
    transform_model = None
    while item is not None and item not in visited:
        visited.add(item)
        model = getattr(item, "model", None)
        if model is None:
            break
        kind = (getattr(model, "kind", "") or "").strip().lower()
        if kind == "transforms":
            transform_model = model
            up_edges = _ordered_in_edges(item)
            item = getattr(up_edges[0], "src", None) if up_edges else None
            if item is None:
                break
            continue
        if kind in pass_kinds:
            up_edges = _ordered_in_edges(item)
            item = getattr(up_edges[0], "src", None) if up_edges else None
            if item is None:
                break
            continue
        break

    asset_path = ""
    owner = ""
    xform = None

    if transform_model is not None:
        owner = (getattr(transform_model, "name", "") or "").strip()
        src_path = (_param_value(transform_model, "source") or "").strip()
        out_path = (_param_value(transform_model, "path") or "").strip()
        asset_path = src_path or out_path
        xform = {
            "pos": list(_param_vec3(transform_model, "pos", (0.0, 0.0, 0.0))),
            "rot": list(_param_vec3(transform_model, "rot", (0.0, 0.0, 0.0))),
            "scl": list(_param_vec3(transform_model, "scl", (1.0, 1.0, 1.0))),
        }

    if not asset_path:
        asset_path = (
            _resolve_input_path(
                node_item,
                port_names=port_names,
                fallback_index=fallback_index,
                allow_any=allow_any,
                allow_model_fallback=False,
            )
            or ""
        ).strip()

    if not owner:
        owner_item = item if item is not None else src_item
        owner_model = getattr(owner_item, "model", None) if owner_item is not None else None
        owner = (getattr(owner_model, "name", "") or "").strip()

    if not owner and asset_path:
        owner = Path(asset_path).name

    return {
        "path": asset_path,
        "owner": owner,
        "xform": xform,
        "has_transform": bool(transform_model is not None),
    }


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


def _xform_input_path(node_item, mesh_path: str) -> Path:
    node_name = _sanitize_name(getattr(getattr(node_item, "model", None), "name", "") or "volume_split")
    mesh_stem = _sanitize_name(Path(mesh_path).stem) if mesh_path else "mesh"
    return _split_dir(node_item) / f"{node_name}_{mesh_stem}_xform_input.obj"


def _mtime_ns_from_stat(st) -> int:
    try:
        return int(getattr(st, "st_mtime_ns"))
    except Exception:
        try:
            return int(float(getattr(st, "st_mtime", 0.0)) * 1_000_000_000)
        except Exception:
            return 0


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


def _resolve_obj_index(value: Optional[int], total: int) -> Optional[int]:
    if value is None:
        return None
    if value < 0:
        value = total + value + 1
    if value <= 0 or value > total:
        return None
    return int(value - 1)


def _load_obj_polygon_mesh(path: Path):
    try:
        import numpy as np
    except Exception:
        return None, None, None, None
    positions: List[Tuple[float, float, float]] = []
    texcoords: List[Tuple[float, float]] = []
    normals_src: List[Tuple[float, float, float]] = []
    out_pos: List[List[float]] = []
    out_uv: List[List[float]] = []
    out_norm: List[List[float]] = []
    faces_out: List[List[int]] = []
    has_authored_uv = False
    # Canonicalize by OBJ corner tuple so shared topology/UV seams are preserved.
    vertex_lookup: Dict[Tuple[int, Optional[int], Optional[int]], int] = {}
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        try:
            raw = path.read_text(errors="ignore")
        except Exception:
            return None, None, None, None

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
        elif head == "vn" and len(parts) >= 4:
            try:
                normals_src.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except Exception:
                continue
        elif head == "f" and len(parts) >= 4:
            face_idx: List[int] = []
            for tok in parts[1:]:
                if not tok:
                    continue
                vals = tok.split("/")
                v_idx_raw = None
                vt_idx_raw = None
                vn_idx_raw = None
                try:
                    if len(vals) >= 1 and vals[0]:
                        v_idx_raw = int(vals[0])
                except Exception:
                    v_idx_raw = None
                try:
                    if len(vals) >= 2 and vals[1]:
                        vt_idx_raw = int(vals[1])
                except Exception:
                    vt_idx_raw = None
                try:
                    if len(vals) >= 3 and vals[2]:
                        vn_idx_raw = int(vals[2])
                except Exception:
                    vn_idx_raw = None

                v_idx = _resolve_obj_index(v_idx_raw, len(positions))
                if v_idx is None:
                    continue
                vt_idx = _resolve_obj_index(vt_idx_raw, len(texcoords))
                vn_idx = _resolve_obj_index(vn_idx_raw, len(normals_src))
                key = (int(v_idx), int(vt_idx) if vt_idx is not None else None, int(vn_idx) if vn_idx is not None else None)
                mapped = vertex_lookup.get(key)
                if mapped is None:
                    vx, vy, vz = positions[v_idx]
                    if vt_idx is not None:
                        u, v = texcoords[vt_idx]
                        has_authored_uv = True
                    else:
                        u, v = 0.0, 0.0
                    if vn_idx is not None:
                        nx, ny, nz = normals_src[vn_idx]
                    else:
                        nx, ny, nz = 0.0, 0.0, 0.0
                    out_pos.append([float(vx), float(vy), float(vz)])
                    out_uv.append([float(u), float(v)])
                    out_norm.append([float(nx), float(ny), float(nz)])
                    mapped = len(out_pos) - 1
                    vertex_lookup[key] = mapped
                face_idx.append(int(mapped))
            if len(face_idx) >= 2 and face_idx[0] == face_idx[-1]:
                face_idx.pop()
            if len(face_idx) >= 3:
                faces_out.append(face_idx)

    if not out_pos or not faces_out:
        return None, None, None, None
    pts = np.asarray(out_pos, dtype="f4").reshape(-1, 3)
    norms = np.asarray(out_norm, dtype="f4").reshape(-1, 3)
    uvs = np.asarray(out_uv, dtype="f4").reshape(-1, 2) if has_authored_uv else None
    return pts, norms, uvs, faces_out


def _load_fbx_ascii_polygon_mesh(path: Path):
    try:
        import numpy as np
        from echograph.ui import gl_loaders
    except Exception:
        return None, None, None, None
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None, None, None, None
    geo_pattern = re.compile(r'Geometry:\s*[^\n]*"Mesh"[^\n]*\{', re.IGNORECASE)
    out_pos: List[List[float]] = []
    out_uv: List[List[float]] = []
    out_norm: List[List[float]] = []
    faces_out: List[List[int]] = []
    has_authored_uv = False

    for match in geo_pattern.finditer(raw):
        brace_start = raw.find("{", match.end() - 1)
        if brace_start < 0:
            continue
        brace_end = gl_loaders._find_matching_brace(raw, brace_start)  # type: ignore[attr-defined]
        if brace_end < 0:
            continue
        block = raw[brace_start + 1 : brace_end]
        verts = gl_loaders._fbx_extract_array(block, "Vertices", as_int=False)  # type: ignore[attr-defined]
        poly_idx = gl_loaders._fbx_extract_array(block, "PolygonVertexIndex", as_int=True)  # type: ignore[attr-defined]
        if not verts or not poly_idx:
            continue
        if len(verts) % 3 != 0:
            verts = verts[: (len(verts) // 3) * 3]
        vertices = np.asarray(verts, dtype="f4").reshape(-1, 3)
        if vertices.size == 0:
            continue
        max_idx = int(vertices.shape[0] - 1)

        uv_layer = None
        try:
            uv_layer = gl_loaders._fbx_extract_uv_layer(block)  # type: ignore[attr-defined]
        except Exception:
            uv_layer = None

        mapping = ""
        reference = ""
        uv_direct = None
        uv_index: List[int] = []
        if uv_layer is not None:
            try:
                mapping, reference, uv_list, uv_index = uv_layer
            except Exception:
                mapping, reference, uv_list, uv_index = "", "", [], []
            if mapping == "byvertice":
                mapping = "byvertex"
            if len(uv_list) % 2 != 0:
                uv_list = uv_list[: (len(uv_list) // 2) * 2]
            if uv_list:
                try:
                    uv_direct = np.asarray(uv_list, dtype="f4").reshape(-1, 2)
                except Exception:
                    uv_direct = None

        poly_vert_cursor = 0
        poly_index = 0
        polygon: List[int] = []
        polygon_uv: List[Optional[int]] = []
        vertex_lookup: Dict[Tuple[int, Optional[int]], int] = {}

        for idx_raw in poly_idx:
            end_poly = False
            idx = int(idx_raw)
            if idx < 0:
                idx = -idx - 1
                end_poly = True

            uv_idx_val: Optional[int] = None
            if uv_direct is not None:
                if mapping == "bypolygonvertex":
                    if reference == "indextodirect":
                        if poly_vert_cursor < len(uv_index):
                            try:
                                uv_idx_val = int(uv_index[poly_vert_cursor])
                            except Exception:
                                uv_idx_val = None
                    else:
                        uv_idx_val = int(poly_vert_cursor)
                elif mapping == "byvertex":
                    if reference == "indextodirect":
                        if 0 <= idx < len(uv_index):
                            try:
                                uv_idx_val = int(uv_index[idx])
                            except Exception:
                                uv_idx_val = None
                    else:
                        uv_idx_val = int(idx)
                elif mapping == "bypolygon":
                    if reference == "indextodirect":
                        if poly_index < len(uv_index):
                            try:
                                uv_idx_val = int(uv_index[poly_index])
                            except Exception:
                                uv_idx_val = None
                    else:
                        uv_idx_val = int(poly_index)
                elif mapping == "allsame":
                    uv_idx_val = 0

            if idx < 0 or idx > max_idx:
                polygon = []
                polygon_uv = []
                poly_vert_cursor += 1
                if end_poly:
                    poly_index += 1
                continue

            polygon.append(int(idx))
            polygon_uv.append(uv_idx_val)
            poly_vert_cursor += 1

            if end_poly:
                if len(polygon) >= 3:
                    face_idx: List[int] = []
                    for vid, uv_idx_cur in zip(polygon, polygon_uv):
                        key = (
                            int(vid),
                            int(uv_idx_cur) if uv_idx_cur is not None else None,
                        )
                        mapped = vertex_lookup.get(key)
                        if mapped is None:
                            vx, vy, vz = vertices[vid]
                            u = 0.0
                            v = 0.0
                            if uv_direct is not None and uv_idx_cur is not None:
                                if 0 <= int(uv_idx_cur) < int(uv_direct.shape[0]):
                                    u = float(uv_direct[int(uv_idx_cur)][0])
                                    v = float(uv_direct[int(uv_idx_cur)][1])
                                    has_authored_uv = True
                            out_pos.append([float(vx), float(vy), float(vz)])
                            out_uv.append([float(u), float(v)])
                            out_norm.append([0.0, 0.0, 0.0])
                            mapped = len(out_pos) - 1
                            vertex_lookup[key] = mapped
                        face_idx.append(int(mapped))
                    if len(face_idx) >= 2 and face_idx[0] == face_idx[-1]:
                        face_idx.pop()
                    if len(face_idx) >= 3:
                        faces_out.append(face_idx)
                polygon = []
                polygon_uv = []
                poly_index += 1

    if not out_pos or not faces_out:
        return None, None, None, None
    pts = np.asarray(out_pos, dtype="f4").reshape(-1, 3)
    norms = np.asarray(out_norm, dtype="f4").reshape(-1, 3)
    uvs = np.asarray(out_uv, dtype="f4").reshape(-1, 2) if has_authored_uv else None
    return pts, norms, uvs, faces_out


def _load_fbx_polygon_mesh(path: Path):
    try:
        import numpy as np
        from echograph.ui import gl_loaders
    except Exception:
        return None, None, None, None

    # FBX ASCII can preserve polygon lists directly.
    try:
        if gl_loaders._is_ascii_fbx(path):  # type: ignore[attr-defined]
            pts, norms, uvs, faces = _load_fbx_ascii_polygon_mesh(path)
            if pts is not None and faces:
                return pts, norms, uvs, faces
    except Exception:
        pass

    # FBX binary via pyassimp without triangulate flag.
    try:
        gl_loaders.ensure_assimp_dll()
        import pyassimp
        from pyassimp import postprocess as ai_post
    except Exception:
        return None, None, None, None

    try:
        processing = ai_post.aiProcess_PreTransformVertices | ai_post.aiProcess_JoinIdenticalVertices
        out_pos: List[List[float]] = []
        out_uv: List[List[float]] = []
        out_norm: List[List[float]] = []
        faces_out: List[List[int]] = []
        has_authored_uv = False
        with pyassimp.load(str(path), file_type="fbx", processing=processing) as scene:
            for mesh in scene.meshes or []:
                vertices = np.asarray(getattr(mesh, "vertices", []), dtype="f4")
                if vertices.size == 0:
                    continue
                normals_raw = None
                try:
                    nraw = getattr(mesh, "normals", None)
                    if nraw is not None and len(nraw) == len(vertices):
                        normals_raw = np.asarray(nraw, dtype="f4").reshape(-1, 3)
                except Exception:
                    normals_raw = None
                uv_raw = None
                try:
                    tcoords = getattr(mesh, "texturecoords", None)
                    if tcoords is not None and len(tcoords) > 0:
                        uv0 = np.asarray(tcoords[0], dtype="f4")
                        if uv0.shape[0] == vertices.shape[0] and uv0.shape[1] >= 2:
                            uv_raw = uv0[:, :2]
                except Exception:
                    uv_raw = None
                faces = getattr(mesh, "faces", None)
                if faces is None:
                    continue
                vertex_lookup: Dict[int, int] = {}
                for face in faces:
                    try:
                        idxs = [int(i) for i in face]
                    except Exception:
                        continue
                    if len(idxs) < 3:
                        continue
                    face_idx: List[int] = []
                    for idx in idxs:
                        if idx < 0 or idx >= len(vertices):
                            continue
                        mapped = vertex_lookup.get(int(idx))
                        if mapped is None:
                            vx, vy, vz = vertices[idx]
                            if normals_raw is not None:
                                nx, ny, nz = normals_raw[idx]
                            else:
                                nx, ny, nz = 0.0, 0.0, 0.0
                            if uv_raw is not None:
                                u, v = uv_raw[idx]
                                has_authored_uv = True
                            else:
                                u, v = 0.0, 0.0
                            out_pos.append([float(vx), float(vy), float(vz)])
                            out_uv.append([float(u), float(v)])
                            out_norm.append([float(nx), float(ny), float(nz)])
                            mapped = len(out_pos) - 1
                            vertex_lookup[int(idx)] = mapped
                        face_idx.append(int(mapped))
                    if len(face_idx) >= 2 and face_idx[0] == face_idx[-1]:
                        face_idx.pop()
                    if len(face_idx) >= 3:
                        faces_out.append(face_idx)
    except Exception:
        return None, None, None, None

    if not out_pos or not faces_out:
        return None, None, None, None
    pts = np.asarray(out_pos, dtype="f4").reshape(-1, 3)
    norms = np.asarray(out_norm, dtype="f4").reshape(-1, 3)
    uvs = np.asarray(out_uv, dtype="f4").reshape(-1, 2) if has_authored_uv else None
    return pts, norms, uvs, faces_out


def _load_mesh_polygon_data(path: Path):
    ext = path.suffix.lower()
    if ext == ".obj":
        return _load_obj_polygon_mesh(path)
    if ext == ".fbx":
        return _load_fbx_polygon_mesh(path)

    pts, norms, uvs = _load_mesh_arrays(path)
    if pts is None or not getattr(pts, "size", 0):
        return None, None, None, None
    try:
        face_count = int(pts.shape[0] // 3)
    except Exception:
        face_count = 0
    faces: List[List[int]] = []
    for i in range(face_count):
        a = i * 3
        faces.append([a, a + 1, a + 2])
    if not faces:
        return None, None, None, None
    return pts, norms, uvs, faces


def _materialize_transformed_input(
    mesh_path: Path,
    out_path: Path,
    mesh_xform: dict,
    debug: bool = False,
) -> Optional[str]:
    pts, norms, uvs, faces = _load_mesh_polygon_data(mesh_path)
    if pts is None or not getattr(pts, "size", 0):
        _debug_log(
            "materialize_transformed_input",
            enabled=debug,
            mesh_path=str(mesh_path),
            out_path=str(out_path),
            error="Mesh load failed.",
        )
        return "Mesh load failed."
    if not faces:
        _debug_log(
            "materialize_transformed_input",
            enabled=debug,
            mesh_path=str(mesh_path),
            out_path=str(out_path),
            error="Mesh has no polygon faces.",
        )
        return "Mesh has no polygon faces."
    norms = _ensure_normals(pts, norms, faces=faces)
    uvs = _ensure_uvs(pts, uvs)
    pos = mesh_xform.get("pos", (0.0, 0.0, 0.0))
    rot = mesh_xform.get("rot", (0.0, 0.0, 0.0))
    scl = mesh_xform.get("scl", (1.0, 1.0, 1.0))
    in_bounds = _points_bounds(pts)
    pts_t, norms_t = _apply_transform(pts, norms, pos, rot, scl)
    out_bounds = _points_bounds(pts_t)
    err = _write_obj(out_path, pts_t, norms_t, uvs, faces=faces)
    tri_equiv = 0
    try:
        tri_equiv = int(sum(max(0, len(face) - 2) for face in (faces or [])))
    except Exception:
        tri_equiv = 0
    _debug_log(
        "materialize_transformed_input",
        enabled=debug,
        mesh_path=str(mesh_path),
        out_path=str(out_path),
        pos=[float(pos[0]), float(pos[1]), float(pos[2])],
        rot=[float(rot[0]), float(rot[1]), float(rot[2])],
        scl=[float(scl[0]), float(scl[1]), float(scl[2])],
        input_bounds=in_bounds,
        output_bounds=out_bounds,
        point_count=int(pts.shape[0]),
        face_count=int(len(faces or [])),
        tri_equiv=int(tri_equiv),
        error=(err or ""),
    )
    return err


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
    pts, _, _, _ = _load_mesh_polygon_data(path)
    if pts is None or not getattr(pts, "size", 0):
        return None
    try:
        bmin = pts.min(axis=0)
        bmax = pts.max(axis=0)
    except Exception:
        return None
    return (float(bmin[0]), float(bmin[1]), float(bmin[2])), (float(bmax[0]), float(bmax[1]), float(bmax[2]))


def _points_bounds(points):
    if points is None or not getattr(points, "size", 0):
        return None
    try:
        bmin = points.min(axis=0)
        bmax = points.max(axis=0)
        return {
            "min": [float(bmin[0]), float(bmin[1]), float(bmin[2])],
            "max": [float(bmax[0]), float(bmax[1]), float(bmax[2])],
        }
    except Exception:
        return None


def _ensure_normals(points, normals, faces: Optional[List[List[int]]] = None):
    try:
        import numpy as np
    except Exception:
        return normals
    pts = np.asarray(points, dtype="f4").reshape(-1, 3)
    if pts.size == 0:
        return np.zeros((0, 3), dtype="f4")

    # Start from provided normals when possible, but do not trust zero/invalid data.
    normals_arr = None
    if normals is not None and getattr(normals, "size", 0):
        try:
            cand = np.asarray(normals, dtype="f4").reshape(-1, 3)
            if cand.shape[0] == pts.shape[0]:
                normals_arr = cand.copy()
        except Exception:
            normals_arr = None

    def _compute_face_normal(face_idx: List[int]):
        if not face_idx or len(face_idx) < 3:
            return None
        clean: List[int] = []
        for raw in face_idx:
            try:
                vi = int(raw)
            except Exception:
                continue
            if vi < 0 or vi >= pts.shape[0]:
                continue
            if clean and clean[-1] == vi:
                continue
            clean.append(vi)
        if len(clean) >= 2 and clean[0] == clean[-1]:
            clean.pop()
        if len(clean) < 3:
            return None

        # Some polygon data can start with collinear/duplicated verts.
        # Scan for any non-degenerate triplet instead of trusting the first 3.
        for i in range(len(clean) - 2):
            a = pts[clean[i]]
            for j in range(i + 1, len(clean) - 1):
                b = pts[clean[j]]
                ab = b - a
                if float(np.linalg.norm(ab)) <= 1e-8:
                    continue
                for k in range(j + 1, len(clean)):
                    c = pts[clean[k]]
                    n = np.cross(ab, c - a)
                    ln = float(np.linalg.norm(n))
                    if ln > 1e-8:
                        return (n / ln).astype("f4")
        return None

    def _rebuild_from_faces():
        rebuilt = np.zeros_like(pts, dtype="f4")
        if faces:
            accum = np.zeros_like(pts, dtype="f4")
            counts = np.zeros((pts.shape[0],), dtype="i4")
            for face in faces:
                if not face or len(face) < 3:
                    continue
                n = _compute_face_normal(face)
                if n is None:
                    continue
                for idx in face:
                    try:
                        vi = int(idx)
                    except Exception:
                        continue
                    if vi < 0 or vi >= pts.shape[0]:
                        continue
                    accum[vi] += n
                    counts[vi] += 1
            valid = counts > 0
            if valid.any():
                rebuilt[valid] = accum[valid]
                lens = np.linalg.norm(rebuilt[valid], axis=1)
                lens[lens < 1e-8] = 1.0
                rebuilt[valid] = rebuilt[valid] / lens.reshape(-1, 1)
            missing = ~valid
            if missing.any():
                rebuilt[missing] = np.array([0.0, 0.0, 1.0], dtype="f4")
            return rebuilt

        for i in range(0, pts.shape[0], 3):
            tri = pts[i:i + 3]
            if tri.shape[0] != 3:
                continue
            a, b, c = tri
            n = np.cross(b - a, c - a)
            ln = float(np.linalg.norm(n))
            if ln > 1e-8:
                n = n / ln
            rebuilt[i:i + 3] = n
        lens = np.linalg.norm(rebuilt, axis=1)
        zero = lens <= 1e-8
        if zero.any():
            rebuilt[zero] = np.array([0.0, 0.0, 1.0], dtype="f4")
        return rebuilt

    if normals_arr is None:
        return _rebuild_from_faces().astype("f4")

    finite = np.isfinite(normals_arr).all(axis=1)
    lengths = np.linalg.norm(normals_arr, axis=1)
    valid = finite & (lengths > 1e-6)
    if not valid.any():
        return _rebuild_from_faces().astype("f4")

    normals_arr[valid] = normals_arr[valid] / lengths[valid].reshape(-1, 1)
    missing = ~valid
    if missing.any():
        if faces:
            for face in faces:
                if not face or len(face) < 3:
                    continue
                n = _compute_face_normal(face)
                if n is None:
                    continue
                for idx in face:
                    try:
                        vi = int(idx)
                    except Exception:
                        continue
                    if vi < 0 or vi >= normals_arr.shape[0]:
                        continue
                    if missing[vi]:
                        normals_arr[vi] = n
            lengths = np.linalg.norm(normals_arr, axis=1)
            missing = ~(np.isfinite(normals_arr).all(axis=1) & (lengths > 1e-6))

        if missing.any():
            normals_arr[missing] = np.array([0.0, 0.0, 1.0], dtype="f4")
            lengths = np.linalg.norm(normals_arr, axis=1)
        good = lengths > 1e-8
        normals_arr[good] = normals_arr[good] / lengths[good].reshape(-1, 1)
    return normals_arr.astype("f4")


def _ensure_uvs(points, uvs):
    try:
        import numpy as np
    except Exception:
        return uvs
    pts = np.asarray(points, dtype="f4").reshape(-1, 3)
    if pts.size == 0:
        return np.zeros((0, 2), dtype="f4")

    def _project_uvs_from_bounds():
        try:
            bmin = pts.min(axis=0)
            bmax = pts.max(axis=0)
        except Exception:
            bmin = np.array([0.0, 0.0, 0.0], dtype="f4")
            bmax = np.array([1.0, 1.0, 1.0], dtype="f4")
        ext = np.abs(bmax - bmin)
        pairs = [(0, 2), (0, 1), (1, 2)]
        pair = max(pairs, key=lambda p: float(ext[p[0]] * ext[p[1]]))
        iu, iv = int(pair[0]), int(pair[1])
        du = float(ext[iu])
        dv = float(ext[iv])
        if du <= 1e-8:
            du = 1.0
        if dv <= 1e-8:
            dv = 1.0
        u = (pts[:, iu] - float(bmin[iu])) / du
        v = (pts[:, iv] - float(bmin[iv])) / dv
        return np.stack([u, v], axis=1).astype("f4")

    if uvs is not None and getattr(uvs, "size", 0):
        try:
            arr = np.asarray(uvs, dtype="f4").reshape(-1, 2)
        except Exception:
            return _project_uvs_from_bounds()
        if arr.shape[0] != pts.shape[0]:
            return _project_uvs_from_bounds()
        finite = np.isfinite(arr).all(axis=1)
        if not finite.all():
            return _project_uvs_from_bounds()
        # Preserve provided UVs exactly, including flat/all-zero layouts.
        return arr.astype("f4")
    return _project_uvs_from_bounds()


def _split_mesh(
    mesh_path: Path,
    volume_path: Path,
    invert: bool = False,
    mesh_xform: Optional[dict] = None,
    debug: bool = False,
):
    try:
        import numpy as np
    except Exception:
        _debug_log(
            "split_mesh",
            enabled=debug,
            mesh_path=str(mesh_path),
            volume_path=str(volume_path),
            invert=bool(invert),
            error="numpy unavailable",
        )
        return None, None, None, None, "numpy unavailable"

    pts, norms, uvs, faces = _load_mesh_polygon_data(mesh_path)
    if pts is None or not getattr(pts, "size", 0):
        _debug_log(
            "split_mesh",
            enabled=debug,
            mesh_path=str(mesh_path),
            volume_path=str(volume_path),
            invert=bool(invert),
            error="Mesh load failed.",
        )
        return None, None, None, None, "Mesh load failed."
    if not faces:
        _debug_log(
            "split_mesh",
            enabled=debug,
            mesh_path=str(mesh_path),
            volume_path=str(volume_path),
            invert=bool(invert),
            error="Mesh has no polygon faces.",
        )
        return None, None, None, None, "Mesh has no polygon faces."

    bounds = _volume_bounds(volume_path)
    if bounds is None:
        _debug_log(
            "split_mesh",
            enabled=debug,
            mesh_path=str(mesh_path),
            volume_path=str(volume_path),
            invert=bool(invert),
            mesh_bounds=_points_bounds(pts),
            error="Volume mesh missing or invalid.",
        )
        return None, None, None, None, "Volume mesh missing or invalid."
    (minx, miny, minz), (maxx, maxy, maxz) = bounds
    bmin = np.array([minx, miny, minz], dtype="f4")
    bmax = np.array([maxx, maxy, maxz], dtype="f4")

    norms = _ensure_normals(pts, norms, faces=faces)
    uvs = _ensure_uvs(pts, uvs)
    if mesh_xform:
        try:
            pos = mesh_xform.get("pos", (0.0, 0.0, 0.0))
            rot = mesh_xform.get("rot", (0.0, 0.0, 0.0))
            scl = mesh_xform.get("scl", (1.0, 1.0, 1.0))
            pts, norms = _apply_transform(pts, norms, pos, rot, scl)
        except Exception:
            pass
    input_bounds = _points_bounds(pts)
    kept_source_faces: List[List[int]] = []
    eps = 1e-6

    def _clean_face(face_raw):
        clean: List[int] = []
        for raw_idx in face_raw:
            try:
                vi = int(raw_idx)
            except Exception:
                continue
            if vi < 0 or vi >= pts.shape[0]:
                continue
            if clean and clean[-1] == vi:
                continue
            clean.append(vi)
        if len(clean) >= 2 and clean[0] == clean[-1]:
            clean.pop()
        return clean

    for face in faces:
        clean_face = _clean_face(face)
        if len(clean_face) < 3:
            continue
        inside = True
        for vidx in clean_face:
            try:
                v = pts[vidx]
            except Exception:
                inside = False
                break
            if (v < (bmin - eps)).any() or (v > (bmax + eps)).any():
                inside = False
                break
        keep = inside if not invert else not inside
        if not keep:
            continue
        kept_source_faces.append(clean_face)

    if not kept_source_faces:
        _debug_log(
            "split_mesh",
            enabled=debug,
            mesh_path=str(mesh_path),
            volume_path=str(volume_path),
            invert=bool(invert),
            mesh_bounds=input_bounds,
            volume_bounds={
                "min": [float(minx), float(miny), float(minz)],
                "max": [float(maxx), float(maxy), float(maxz)],
            },
            input_face_count=int(len(faces)),
            kept_face_count=0,
            error="No faces inside volume.",
        )
        return None, None, None, None, "No faces inside volume."

    # Reindex only vertices/UVs/normals referenced by kept polygons.
    # This preserves authored UVs while removing data for discarded polygons.
    kept_pts: List[List[float]] = []
    kept_norms: List[List[float]] = []
    kept_uvs: List[List[float]] = []
    kept_faces: List[List[int]] = []
    remap: Dict[int, int] = {}
    for face in kept_source_faces:
        new_face: List[int] = []
        for vidx in face:
            mapped = remap.get(vidx)
            if mapped is None:
                try:
                    v = pts[vidx]
                    n = norms[vidx]
                    uv = uvs[vidx]
                except Exception:
                    continue
                kept_pts.append([float(v[0]), float(v[1]), float(v[2])])
                kept_norms.append([float(n[0]), float(n[1]), float(n[2])])
                kept_uvs.append([float(uv[0]), float(uv[1])])
                mapped = len(kept_pts) - 1
                remap[vidx] = mapped
            new_face.append(int(mapped))
        if len(new_face) >= 3:
            kept_faces.append(new_face)

    if not kept_faces or not kept_pts:
        return None, None, None, None, "No faces inside volume."

    pts_out = np.array(kept_pts, dtype="f4").reshape(-1, 3)
    norms_out = np.array(kept_norms, dtype="f4").reshape(-1, 3)
    uvs_out = np.array(kept_uvs, dtype="f4").reshape(-1, 2)
    norms_out = _ensure_normals(pts_out, norms_out, faces=kept_faces)
    input_tri_equiv = 0
    kept_tri_equiv = 0
    try:
        input_tri_equiv = int(sum(max(0, len(face) - 2) for face in faces))
        kept_tri_equiv = int(sum(max(0, len(face) - 2) for face in kept_faces))
    except Exception:
        pass
    _debug_log(
        "split_mesh",
        enabled=debug,
        mesh_path=str(mesh_path),
        volume_path=str(volume_path),
        invert=bool(invert),
        mesh_bounds=input_bounds,
        output_bounds=_points_bounds(pts_out),
        volume_bounds={
            "min": [float(minx), float(miny), float(minz)],
            "max": [float(maxx), float(maxy), float(maxz)],
        },
        input_face_count=int(len(faces)),
        kept_face_count=int(len(kept_faces)),
        input_tri_equiv=int(input_tri_equiv),
        kept_tri_equiv=int(kept_tri_equiv),
        error="",
    )
    return pts_out, norms_out, uvs_out, kept_faces, None


def _write_obj(path: Path, points, normals, uvs, faces: Optional[List[List[int]]] = None) -> Optional[str]:
    if points is None or not getattr(points, "size", 0):
        return "No output mesh."
    normals = _ensure_normals(points, normals, faces=faces)
    lines = ["# EchoGraph Volume Split"]
    for x, y, z in points:
        lines.append(f"v {float(x):.6f} {float(y):.6f} {float(z):.6f}")
    for u, v in uvs:
        lines.append(f"vt {float(u):.6f} {float(v):.6f}")
    for nx, ny, nz in normals:
        lines.append(f"vn {float(nx):.6f} {float(ny):.6f} {float(nz):.6f}")
    if faces:
        for face in faces:
            if not face or len(face) < 3:
                continue
            parts = []
            for idx in face:
                i = int(idx) + 1
                parts.append(f"{i}/{i}/{i}")
            lines.append("f " + " ".join(parts))
    else:
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
        self._last_input_xform_stamp = None
        self._last_applied_output_path = ""

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

        layout.addLayout(debug_row, 0)

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
        btn_row.addWidget(self._view_btn, 0)

        self._split_btn = QtWidgets.QPushButton("Split")
        self._split_btn.setFixedWidth(64)
        self._split_btn.setStyleSheet(
            "QPushButton{background:#0f766e;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#0d9488;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._split_btn.clicked.connect(self._on_split_clicked)
        self._split_btn.setEnabled(False)
        btn_row.addWidget(self._split_btn, 0)
        btn_row.addStretch(1)

        layout.addLayout(btn_row, 0)

        self._ensure_scene()
        try:
            self._invert.setChecked(_param_bool(getattr(self._node_item, "model", None), "invert", False))
        except Exception:
            pass
        try:
            self._debug.setChecked(_param_bool(getattr(self._node_item, "model", None), "debug", False))
        except Exception:
            pass

        QtCore.QTimer.singleShot(0, self._update_split)

    def sizeHint(self):
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

    def _schedule_update(self):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(80, self._update_split)

    def _on_invert_changed(self, state: int):
        val = "1" if bool(state) else "0"
        self._set_param("invert", val, notify_scene=True)
        self._schedule_update()

    def _on_debug_changed(self, state: int):
        val = "1" if bool(state) else "0"
        self._set_param("debug", val, notify_scene=True)

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

    def _update_split(self, force: bool = False, apply_split: bool = False):
        self._pending = False
        if apply_split:
            self._last_applied_output_path = ""
        model = getattr(self._node_item, "model", None)
        invert = False
        try:
            invert = _param_bool(model, "invert", False)
        except Exception:
            invert = False
        try:
            debug = _param_bool(model, "debug", False)
        except Exception:
            debug = False
        try:
            self._split_btn.setEnabled(False)
        except Exception:
            pass
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
        _debug_log(
            "update_split_start",
            enabled=debug,
            node=(getattr(getattr(self._node_item, "model", None), "name", "") or "").strip(),
            force=bool(force),
            apply_split=bool(apply_split),
            invert=bool(invert),
            mesh_path=mesh_path,
            volume_path=volume_path,
            mesh_label=mesh_label,
            volume_label=volume_label,
            edge_count=int(len(edges)),
        )
        if not edges:
            self._status.setText("No inputs connected.")
            self._view_btn.setEnabled(False)
            try:
                self._split_btn.setEnabled(False)
            except Exception:
                pass
            self._set_param("mesh", "", notify_scene=False)
            self._set_param("source", "", notify_scene=False)
            self._set_param("volume", "", notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            _debug_log("update_split_abort", enabled=debug, reason="no_inputs_connected")
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
        mesh_preview_path = mesh_path
        mesh_edge = _pick_input_edge(self._node_item, {"mesh", "source", "path"}, fallback_index=0, allow_any=True)
        mesh_src_item = getattr(mesh_edge, "src", None) if mesh_edge is not None else None
        if mesh_src_item is not None:
            mesh_model = getattr(mesh_src_item, "model", None)
            mesh_kind = (getattr(mesh_model, "kind", "") or "").strip().lower() if mesh_model is not None else ""
            if mesh_kind == "transforms" and mesh_model is not None:
                mesh_source = (_param_value(mesh_model, "source") or "").strip()
                mesh_out = (_param_value(mesh_model, "path") or "").strip()
                if mesh_source:
                    mesh_path = mesh_source
                    mesh_preview_path = mesh_out or mesh_source
                    mesh_xform = {
                        "pos": _param_vec3(mesh_model, "pos", (0.0, 0.0, 0.0)),
                        "rot": _param_vec3(mesh_model, "rot", (0.0, 0.0, 0.0)),
                        "scl": _param_vec3(mesh_model, "scl", (1.0, 1.0, 1.0)),
                    }
                elif mesh_out:
                    # Fallback to baked output when source is unavailable.
                    mesh_path = mesh_out
                    mesh_preview_path = mesh_out
        _debug_log(
            "update_split_inputs_resolved",
            enabled=debug,
            mesh_path=mesh_path,
            mesh_preview_path=mesh_preview_path,
            volume_path=volume_path,
            mesh_xform=mesh_xform,
        )
        if not mesh_path:
            if volume_path:
                self._status.setText(volume_label or "Volume only.")
                self._view_btn.setEnabled(True)
                try:
                    self._split_btn.setEnabled(False)
                except Exception:
                    pass
                self._set_param("mesh", "", notify_scene=False)
                self._set_param("source", "", notify_scene=False)
                self._set_param("volume", volume_path, notify_scene=False)
                self._set_param("path", volume_path, notify_scene=True)
            else:
                self._status.setText("No mesh input.")
                self._view_btn.setEnabled(False)
                try:
                    self._split_btn.setEnabled(False)
                except Exception:
                    pass
                self._set_param("mesh", "", notify_scene=False)
                self._set_param("source", "", notify_scene=False)
                self._set_param("volume", "", notify_scene=False)
                self._set_param("path", "", notify_scene=True)
            _debug_log(
                "update_split_abort",
                enabled=debug,
                reason="no_mesh_input",
                volume_path=volume_path,
            )
            return
        if not volume_path:
            preview_path = (mesh_preview_path or mesh_path or "").strip()
            if (not preview_path) or (not os.path.exists(preview_path)):
                preview_path = (mesh_path or "").strip()
            if (not preview_path) or (not os.path.exists(preview_path)):
                self._status.setText("Mesh not found.")
                self._view_btn.setEnabled(False)
                try:
                    self._split_btn.setEnabled(False)
                except Exception:
                    pass
                self._set_param("mesh", mesh_path, notify_scene=False)
                self._set_param("source", mesh_path, notify_scene=False)
                self._set_param("volume", "", notify_scene=False)
                self._set_param("path", "", notify_scene=True)
                _debug_log(
                    "update_split_abort",
                    enabled=debug,
                    reason="no_volume_preview_missing_mesh",
                    mesh_path=mesh_path,
                    preview_path=preview_path,
                    mesh_xform=mesh_xform,
                )
                return
            self._status.setText(mesh_label or Path(preview_path).name)
            self._view_btn.setEnabled(True)
            try:
                self._split_btn.setEnabled(False)
            except Exception:
                pass
            self._set_param("mesh", mesh_path, notify_scene=False)
            self._set_param("source", mesh_path, notify_scene=False)
            self._set_param("volume", "", notify_scene=False)
            self._set_param("path", preview_path, notify_scene=True)
            _debug_log(
                "update_split_mesh_preview_only",
                enabled=debug,
                mesh_path=mesh_path,
                preview_path=preview_path,
                mesh_xform=mesh_xform,
            )
            return
        if not os.path.exists(mesh_path):
            self._status.setText("Mesh not found.")
            self._view_btn.setEnabled(False)
            try:
                self._split_btn.setEnabled(False)
            except Exception:
                pass
            self._set_param("mesh", mesh_path, notify_scene=False)
            self._set_param("source", mesh_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            _debug_log("update_split_abort", enabled=debug, reason="mesh_not_found", mesh_path=mesh_path)
            return
        if not os.path.exists(volume_path):
            self._status.setText("Volume not found.")
            self._view_btn.setEnabled(False)
            try:
                self._split_btn.setEnabled(False)
            except Exception:
                pass
            self._set_param("mesh", mesh_path, notify_scene=False)
            self._set_param("source", mesh_path, notify_scene=False)
            self._set_param("volume", volume_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            _debug_log(
                "update_split_abort",
                enabled=debug,
                reason="volume_not_found",
                mesh_path=mesh_path,
                volume_path=volume_path,
            )
            return

        mesh_ext = Path(mesh_path).suffix.lower()
        vol_ext = Path(volume_path).suffix.lower()
        if mesh_ext not in SUPPORTED_MESH_EXTS:
            self._status.setText("Unsupported mesh.")
            self._view_btn.setEnabled(False)
            try:
                self._split_btn.setEnabled(False)
            except Exception:
                pass
            self._set_param("mesh", mesh_path, notify_scene=False)
            self._set_param("source", mesh_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            _debug_log(
                "update_split_abort",
                enabled=debug,
                reason="unsupported_mesh",
                mesh_path=mesh_path,
                mesh_ext=mesh_ext,
            )
            return
        if vol_ext not in SUPPORTED_MESH_EXTS:
            self._status.setText("Unsupported volume.")
            self._view_btn.setEnabled(False)
            try:
                self._split_btn.setEnabled(False)
            except Exception:
                pass
            self._set_param("volume", volume_path, notify_scene=False)
            self._set_param("path", "", notify_scene=True)
            _debug_log(
                "update_split_abort",
                enabled=debug,
                reason="unsupported_volume",
                volume_path=volume_path,
                volume_ext=vol_ext,
            )
            return

        self._view_btn.setEnabled(True)
        try:
            self._split_btn.setEnabled(True)
        except Exception:
            pass
        self._set_param("mesh", mesh_path, notify_scene=False)
        self._set_param("source", mesh_path, notify_scene=False)
        self._set_param("volume", volume_path, notify_scene=False)

        if not apply_split:
            self._status.setText((mesh_label or Path(mesh_path).name) + " (ready)")
            _debug_log(
                "update_split_ready",
                enabled=debug,
                mesh_path=mesh_path,
                volume_path=volume_path,
                mesh_xform=mesh_xform,
                apply_split=False,
            )
            return

        split_mesh_path = mesh_path
        view_mesh_path = mesh_path
        if mesh_xform:
            xform_path = _xform_input_path(self._node_item, mesh_path)
            try:
                st_src = os.stat(mesh_path)
                xstamp = (
                    mesh_path,
                    _mtime_ns_from_stat(st_src),
                    int(st_src.st_size),
                    float(mesh_xform["pos"][0]), float(mesh_xform["pos"][1]), float(mesh_xform["pos"][2]),
                    float(mesh_xform["rot"][0]), float(mesh_xform["rot"][1]), float(mesh_xform["rot"][2]),
                    float(mesh_xform["scl"][0]), float(mesh_xform["scl"][1]), float(mesh_xform["scl"][2]),
                )
            except Exception:
                xstamp = (
                    mesh_path,
                    None,
                    mesh_xform.get("pos"),
                    mesh_xform.get("rot"),
                    mesh_xform.get("scl"),
                )
            xform_cache_hit = False
            if (not force) and xform_path.exists():
                if self._last_input_xform_stamp == xstamp:
                    xform_cache_hit = True
                elif _cache_matches(xform_path, xstamp):
                    xform_cache_hit = True
                elif self._last_input_xform_stamp is None and _output_is_fresh(xform_path, [mesh_path]):
                    xform_cache_hit = True
                    _write_cached_stamp(xform_path, xstamp)
            if not xform_cache_hit:
                err = _materialize_transformed_input(Path(mesh_path), xform_path, mesh_xform, debug=debug)
                if err:
                    self._status.setText(err)
                    self._view_btn.setEnabled(False)
                    try:
                        self._split_btn.setEnabled(True)
                    except Exception:
                        pass
                    self._set_param("mesh", mesh_path, notify_scene=False)
                    self._set_param("source", mesh_path, notify_scene=False)
                    self._set_param("volume", volume_path, notify_scene=False)
                    self._set_param("path", "", notify_scene=True)
                    return
                _write_cached_stamp(xform_path, xstamp)
            self._last_input_xform_stamp = xstamp
            split_mesh_path = str(xform_path)
            view_mesh_path = str(xform_path)
            _debug_log(
                "update_split_transformed_input",
                enabled=debug,
                mesh_path=mesh_path,
                xform_path=split_mesh_path,
                mesh_xform=mesh_xform,
                xstamp=xstamp,
            )

        try:
            st_mesh = os.stat(split_mesh_path)
            st_vol = os.stat(volume_path)
            stamp = (
                split_mesh_path,
                _mtime_ns_from_stat(st_mesh),
                int(st_mesh.st_size),
                volume_path,
                _mtime_ns_from_stat(st_vol),
                int(st_vol.st_size),
                int(bool(invert)),
            )
        except Exception:
            stamp = (split_mesh_path, None, None, volume_path, None, None, int(bool(invert)))
        _debug_log(
            "update_split_eval_paths",
            enabled=debug,
            mesh_path=mesh_path,
            split_mesh_path=split_mesh_path,
            view_mesh_path=view_mesh_path,
            volume_path=volume_path,
            invert=bool(invert),
            stamp=stamp,
        )

        out_path = _output_path(self._node_item, mesh_path, volume_path)
        cache_hit = False
        if (not force) and out_path.exists():
            if self._last_stamp == stamp:
                cache_hit = True
            elif _cache_matches(out_path, stamp):
                cache_hit = True
            elif self._last_stamp is None:
                cur_path = (_param_value(model, "path") if model is not None else "").strip()
                if _paths_equal(cur_path, str(out_path)) and _output_is_fresh(out_path, [split_mesh_path, volume_path]):
                    cache_hit = True
                    _write_cached_stamp(out_path, stamp)
        if cache_hit:
            self._last_stamp = stamp
            self._status.setText(mesh_label or Path(mesh_path).name)
            self._view_btn.setEnabled(True)
            try:
                self._split_btn.setEnabled(True)
            except Exception:
                pass
            self._set_param("mesh", mesh_path, notify_scene=False)
            self._set_param("source", mesh_path, notify_scene=False)
            self._set_param("volume", volume_path, notify_scene=False)
            path_changed = self._set_param("path", str(out_path), notify_scene=True)
            if apply_split and not path_changed:
                self._emit_param_changed()
            if apply_split:
                self._last_applied_output_path = str(out_path)
            _debug_log(
                "update_split_cache_hit",
                enabled=debug,
                out_path=str(out_path),
                stamp=stamp,
            )
            return

        pts, norms, uvs, faces, err = _split_mesh(
            Path(split_mesh_path),
            Path(volume_path),
            invert=invert,
            mesh_xform=None,
            debug=debug,
        )
        if err:
            self._status.setText(err)
            # Allow viewing inputs even when no faces are inside/outside the volume.
            if "no faces" in str(err).lower():
                self._view_btn.setEnabled(True)
                try:
                    self._split_btn.setEnabled(True)
                except Exception:
                    pass
                self._set_param("mesh", mesh_path, notify_scene=False)
                self._set_param("source", mesh_path, notify_scene=False)
                self._set_param("volume", volume_path, notify_scene=False)
                # Point path at the evaluated mesh so View shows the same transform state.
                path_changed = self._set_param("path", view_mesh_path, notify_scene=True)
                if apply_split and not path_changed:
                    self._emit_param_changed()
                _debug_log(
                    "update_split_no_faces",
                    enabled=debug,
                    mesh_path=mesh_path,
                    split_mesh_path=split_mesh_path,
                    view_mesh_path=view_mesh_path,
                    volume_path=volume_path,
                    error=err,
                )
            else:
                self._view_btn.setEnabled(False)
                try:
                    self._split_btn.setEnabled(True)
                except Exception:
                    pass
                self._set_param("mesh", mesh_path, notify_scene=False)
                self._set_param("source", mesh_path, notify_scene=False)
                self._set_param("volume", volume_path, notify_scene=False)
                self._set_param("path", "", notify_scene=True)
                _debug_log(
                    "update_split_error",
                    enabled=debug,
                    mesh_path=mesh_path,
                    split_mesh_path=split_mesh_path,
                    volume_path=volume_path,
                    error=err,
                )
            return

        err = _write_obj(out_path, pts, norms, uvs, faces=faces)
        if err:
            self._status.setText(err)
            self._view_btn.setEnabled(False)
            try:
                self._split_btn.setEnabled(True)
            except Exception:
                pass
            self._set_param("path", "", notify_scene=True)
            _debug_log(
                "update_split_error",
                enabled=debug,
                mesh_path=mesh_path,
                split_mesh_path=split_mesh_path,
                volume_path=volume_path,
                out_path=str(out_path),
                error=err,
            )
            return

        _write_cached_stamp(out_path, stamp)
        self._last_stamp = stamp
        self._status.setText(mesh_label or Path(mesh_path).name)
        self._view_btn.setEnabled(True)
        try:
            self._split_btn.setEnabled(True)
        except Exception:
            pass
        self._set_param("mesh", mesh_path, notify_scene=False)
        self._set_param("source", mesh_path, notify_scene=False)
        self._set_param("volume", volume_path, notify_scene=False)
        path_changed = self._set_param("path", str(out_path), notify_scene=True)
        if apply_split and not path_changed:
            self._emit_param_changed()
        if apply_split:
            self._last_applied_output_path = str(out_path)
        _debug_log(
            "update_split_success",
            enabled=debug,
            mesh_path=mesh_path,
            split_mesh_path=split_mesh_path,
            volume_path=volume_path,
            out_path=str(out_path),
            output_bounds=_points_bounds(pts),
            output_face_count=int(len(faces or [])),
            output_tri_equiv=int(sum(max(0, len(face) - 2) for face in (faces or []))),
        )

    def _on_split_clicked(self):
        try:
            _debug_log(
                "split_clicked",
                node_item=self._node_item,
                node=(getattr(getattr(self._node_item, "model", None), "name", "") or "").strip(),
            )
            self._update_split(force=True, apply_split=True)
            out_path = (getattr(self, "_last_applied_output_path", "") or "").strip()
            if out_path and os.path.exists(out_path):
                win = _resolve_window(self._node_item)
                handler = getattr(win, "open_scene_assets", None) if win is not None else None
                if callable(handler):
                    node_name = (getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
                    assets = [{
                        "path": out_path,
                        "node": node_name or Path(out_path).name,
                    }]
                    volume_asset = _resolve_input_asset(
                        self._node_item,
                        {"volume", "mask"},
                        fallback_index=1,
                        allow_any=False,
                    )
                    volume_view_path = (str(volume_asset.get("path") or "") or "").strip()
                    if volume_view_path and os.path.exists(volume_view_path):
                        volume_owner = (
                            str(volume_asset.get("owner") or "")
                            or ((node_name + " Volume").strip() if node_name else "")
                            or Path(volume_view_path).name
                        ).strip()
                        volume_entry = {
                            "path": volume_view_path,
                            "node": volume_owner,
                            "wire_only": True,
                            "volume": True,
                        }
                        if isinstance(volume_asset.get("xform"), dict):
                            volume_entry["xform"] = dict(volume_asset.get("xform") or {})
                        assets.append(volume_entry)
                    try:
                        handler(assets, frame=False)
                    except TypeError:
                        try:
                            handler(assets)
                        except Exception:
                            pass
        except Exception:
            pass

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
            mesh_asset = _resolve_input_asset(
                self._node_item,
                {"mesh", "source", "path"},
                fallback_index=0,
                allow_any=True,
            )
            volume_asset = _resolve_input_asset(
                self._node_item,
                {"volume", "mask"},
                fallback_index=1,
                allow_any=False,
            )
            mesh_view_path = (str(mesh_asset.get("path") or "") or mesh_val or path).strip()
            volume_view_path = (str(volume_asset.get("path") or "") or volume_val).strip()
            node_name = (getattr(self._node_item.model, "name", "") or "").strip()
            mesh_owner = (str(mesh_asset.get("owner") or "") or node_name or Path(mesh_view_path).name).strip() if mesh_view_path else ""
            volume_owner = (
                str(volume_asset.get("owner") or "")
                or ((node_name + " Volume").strip() if node_name else "")
                or (Path(volume_view_path).name if volume_view_path else "")
            ).strip()
            _debug_log(
                "view_clicked",
                node_item=self._node_item,
                node=node_name,
                path=path,
                mesh=mesh_val,
                volume=volume_val,
                mesh_view_path=mesh_view_path,
                volume_view_path=volume_view_path,
                mesh_owner=mesh_owner,
                volume_owner=volume_owner,
                mesh_has_transform=bool(mesh_asset.get("has_transform")),
                volume_has_transform=bool(volume_asset.get("has_transform")),
            )
            win = _resolve_window(self._node_item)
            handler = getattr(win, "open_scene_assets", None) if win is not None else None
            if volume_view_path and mesh_view_path:
                assets = []
                mesh_entry = {
                    "path": mesh_view_path,
                    "node": mesh_owner,
                }
                if isinstance(mesh_asset.get("xform"), dict):
                    mesh_entry["xform"] = dict(mesh_asset.get("xform") or {})
                assets.append(mesh_entry)
                volume_entry = {
                    "path": volume_view_path,
                    "node": volume_owner,
                    "wire_only": True,
                    "volume": True,
                }
                if isinstance(volume_asset.get("xform"), dict):
                    volume_entry["xform"] = dict(volume_asset.get("xform") or {})
                assets.append(volume_entry)
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
            if volume_view_path and not mesh_view_path:
                assets = [{
                    "path": volume_view_path,
                    "node": volume_owner,
                    "wire_only": True,
                    "volume": True,
                }]
                if isinstance(volume_asset.get("xform"), dict):
                    assets[0]["xform"] = dict(volume_asset.get("xform") or {})
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
            if mesh_view_path and not volume_view_path:
                assets = [{
                    "path": mesh_view_path,
                    "node": mesh_owner or Path(mesh_view_path).name,
                }]
                if isinstance(mesh_asset.get("xform"), dict):
                    assets[0]["xform"] = dict(mesh_asset.get("xform") or {})
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





