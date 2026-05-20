from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import numpy as np
except Exception:
    np = None

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant


KIND_ALIASES = {
    "copy_to_points",
    "copy to points",
    "copy_to_point",
    "copy to point",
    "copytopoints",
}
_TEXTURE_KINDS = {"texture", "texture_pro", "texture_layer"}
_MATERIAL_KINDS = {"mnaterial", "material"}

COPY_TO_POINTS_NODE_W = 236
COPY_TO_POINTS_BODY_H = 140


@dataclass(frozen=True)
class CopyToPointsBuildOutcome:
    asset: Optional[Dict[str, Any]]
    status: str
    detail: str


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value") or "")
    return ""


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = _param_value(model, name).strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


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
        params = list(params)
        try:
            setattr(model, "params", params)
        except Exception:
            return
    key = name.strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _set_param(node_item, name: str, value: str, *, notify_scene: bool = True) -> bool:
    setter = getattr(node_item, "_set_param_value", None)
    if callable(setter):
        try:
            setter(name, str(value), rebuild=False, notify_scene=bool(notify_scene))
            return True
        except Exception:
            pass
    _ensure_param(node_item, name, str(value))
    model = getattr(node_item, "model", None)
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value)
            return True
    return False


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    entry = None
    for param in params:
        if isinstance(param, dict) and (param.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = param
            break
    if entry is None:
        entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(entry)
    hidden = {
        part.strip().lower()
        for part in str(entry.get("value") or "").split(",")
        if part.strip()
    }
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    try:
        model.params = params
    except Exception:
        pass


def _ensure_visible_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    entry = None
    for param in params:
        if isinstance(param, dict) and (param.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = param
            break
    if entry is None:
        return
    hidden = {
        part.strip().lower()
        for part in str(entry.get("value") or "").split(",")
        if part.strip()
    }
    for name in names or []:
        if name:
            hidden.discard(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    try:
        model.params = params
    except Exception:
        pass


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


def _connected_input_item(node_item, names: set[str]):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    for edge in _ordered_in_edges(scene, node_item):
        if _edge_dst_name(edge).lower() in names:
            return getattr(edge, "src", None)
    return None


def _node_name(item) -> str:
    return str(getattr(getattr(item, "model", None), "name", "") or "").strip()


def _node_kind(item) -> str:
    return str(getattr(getattr(item, "model", None), "kind", "") or "").strip().lower()


def _resolve_window(node_item):
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
        active = QtWidgets.QApplication.activeWindow()
        if active is not None and active.isWindow():
            return active
    except Exception:
        pass
    return None


def _workflow_dir_for_node(node_item) -> Optional[Path]:
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    workflow_path = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                workflow_path = getattr(views[0].window(), "_current_path", None)
        except Exception:
            pass
        workflow_path = workflow_path or getattr(scene, "_filename", None)
    if not workflow_path:
        return None
    try:
        return Path(workflow_path).parent
    except Exception:
        return None


def _copy_dir(node_item) -> Path:
    base = _workflow_dir_for_node(node_item)
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    out = base / "copy_to_points"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _sanitize_name(raw: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(raw or "").strip())
    return safe.strip("_") or "copy_to_points"


def _mesh_path_from_item(item) -> str:
    model = getattr(item, "model", None)
    for name in ("path", "mesh", "source"):
        raw = _param_value(model, name).strip() if model is not None else ""
        if raw:
            return raw
    return ""


def _connected_mesh_input_item(node_item):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    for edge in _ordered_in_edges(scene, node_item):
        if _edge_dst_name(edge).lower() in {"mesh", "path", "source"}:
            return getattr(edge, "src", None)
    return None


def _copy_surface_payload(copy_item) -> Dict[str, Any]:
    item = copy_item
    visited = set()
    depth = 0
    texture_model = None
    texture_kind = ""
    while item is not None and item not in visited and depth < 8:
        visited.add(item)
        depth += 1
        model = getattr(item, "model", None)
        kind = _node_kind(item)
        if kind in _MATERIAL_KINDS:
            try:
                from nodes import material as material_node  # type: ignore

                build_asset = getattr(material_node, "build_material_asset", None)
                asset = build_asset(item) if callable(build_asset) else None
            except Exception:
                asset = None
            if isinstance(asset, dict):
                payload: Dict[str, Any] = {}
                texture = str(asset.get("texture") or "").strip()
                if texture:
                    payload["texture"] = texture
                material = asset.get("material")
                if isinstance(material, dict):
                    payload["material"] = dict(material)
                if asset.get("texture_provider") is not None:
                    payload["texture_provider"] = asset.get("texture_provider")
                return payload
        if kind in _TEXTURE_KINDS and texture_model is None:
            texture_model = model
            texture_kind = kind
        if kind not in (_TEXTURE_KINDS | _MATERIAL_KINDS):
            break
        item = _connected_mesh_input_item(item)

    payload: Dict[str, Any] = {}
    if texture_model is not None:
        texture = _param_value(texture_model, "texture").strip()
        if texture:
            payload["texture"] = texture
        if texture_kind == "texture_pro":
            provider = getattr(texture_model, "_texture_pro_provider", None)
            if provider is not None:
                payload["texture_provider"] = provider
        elif texture_kind == "texture_layer":
            provider = getattr(texture_model, "_texture_layer_provider", None)
            if provider is not None:
                payload["texture_provider"] = provider
    return payload


def _load_mesh_arrays(path: Path):
    if np is None:
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
            arrays = gl_loaders.load_fbx_mesh_arrays_pyassimp(path)
            return arrays.points, arrays.normals, arrays.uvs
        if ext in {".gltf", ".glb"}:
            arrays = gl_loaders.load_gltf_mesh_arrays(path)
            return arrays.points, arrays.normals, arrays.uvs
    except Exception:
        pass
    try:
        model = gl_loaders.load_model(path)
    except Exception:
        model = None
    if model is None or not model.vertices:
        return None, None, None
    points = np.asarray(model.vertices, dtype="f4").reshape(-1, 3)
    return points, np.zeros_like(points), np.zeros((points.shape[0], 2), dtype="f4")


def _ensure_normals(points, normals):
    if np is None or points is None or not getattr(points, "size", 0):
        return normals
    try:
        out = np.asarray(normals, dtype="f4").reshape(-1, 3)
    except Exception:
        out = np.zeros_like(points)
    if out.shape != points.shape or not np.any(np.linalg.norm(out, axis=1) > 1.0e-6):
        out = np.zeros_like(points)
        for idx in range(0, points.shape[0], 3):
            tri = points[idx:idx + 3]
            if tri.shape[0] != 3:
                continue
            normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
            length = float(np.linalg.norm(normal))
            if length > 1.0e-6:
                normal = normal / length
            out[idx:idx + 3] = normal
    return out.astype("f4", copy=False)


def _ensure_uvs(points, uvs):
    if np is None or points is None or not getattr(points, "size", 0):
        return uvs
    try:
        out = np.asarray(uvs, dtype="f4").reshape(-1, 2)
    except Exception:
        out = np.zeros((points.shape[0], 2), dtype="f4")
    if out.shape[0] != points.shape[0]:
        out = np.zeros((points.shape[0], 2), dtype="f4")
    return out.astype("f4", copy=False)


def _unique_points_and_normals(points, normals):
    if np is None:
        return None, None
    points = np.asarray(points, dtype="f4").reshape(-1, 3)
    normals = _ensure_normals(points, normals)
    merged: dict[tuple[float, float, float], dict[str, Any]] = {}
    for point, normal in zip(points, normals):
        key = tuple(round(float(v), 6) for v in point)
        row = merged.setdefault(
            key,
            {
                "point": point.astype("f4", copy=True),
                "normal": np.zeros(3, dtype="f4"),
            },
        )
        row["normal"] += normal
    if not merged:
        return None, None
    out_points = []
    out_normals = []
    for row in merged.values():
        point = row["point"]
        normal = row["normal"]
        length = float(np.linalg.norm(normal))
        if length > 1.0e-6:
            normal = normal / length
        else:
            point_len = float(np.linalg.norm(point))
            normal = (point / point_len) if point_len > 1.0e-6 else np.array([0.0, 1.0, 0.0], dtype="f4")
        out_points.append(point)
        out_normals.append(normal.astype("f4", copy=False))
    pts = np.asarray(out_points, dtype="f4")
    nrms = np.asarray(out_normals, dtype="f4")
    try:
        center = (pts.min(axis=0) + pts.max(axis=0)) * 0.5
        outward = pts - center
        flip = np.sum(nrms * outward, axis=1) < 0.0
        if np.any(flip):
            nrms[flip] *= -1.0
    except Exception:
        pass
    return pts, nrms


def _rotation_from_up(normal):
    if np is None:
        return None
    src = np.array([0.0, 1.0, 0.0], dtype="f4")
    dst = np.asarray(normal, dtype="f4").reshape(3)
    length = float(np.linalg.norm(dst))
    if length <= 1.0e-6:
        return np.identity(3, dtype="f4")
    dst = dst / length
    dot = float(np.clip(np.dot(src, dst), -1.0, 1.0))
    if dot >= 1.0 - 1.0e-6:
        return np.identity(3, dtype="f4")
    if dot <= -1.0 + 1.0e-6:
        return np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]], dtype="f4")
    axis = np.cross(src, dst)
    axis_len = float(np.linalg.norm(axis))
    if axis_len <= 1.0e-6:
        return np.identity(3, dtype="f4")
    axis = axis / axis_len
    x, y, z = axis
    skew = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype="f4")
    return (
        np.identity(3, dtype="f4")
        + (skew * math.sin(math.acos(dot)))
        + ((skew @ skew) * (1.0 - math.cos(math.acos(dot))))
    ).astype("f4")


def _write_triangle_obj(path: Path, points, normals, uvs=None) -> None:
    lines = ["# EchoGraph copy to points"]
    for point in np.asarray(points, dtype="f4").reshape(-1, 3):
        lines.append(f"v {float(point[0]):.6f} {float(point[1]):.6f} {float(point[2]):.6f}")
    uv_values = _ensure_uvs(np.asarray(points, dtype="f4").reshape(-1, 3), uvs)
    for uv in np.asarray(uv_values, dtype="f4").reshape(-1, 2):
        lines.append(f"vt {float(uv[0]):.6f} {float(uv[1]):.6f}")
    for normal in np.asarray(normals, dtype="f4").reshape(-1, 3):
        lines.append(f"vn {float(normal[0]):.6f} {float(normal[1]):.6f} {float(normal[2]):.6f}")
    vertex_count = int(np.asarray(points).reshape(-1, 3).shape[0])
    for start in range(0, vertex_count, 3):
        if start + 2 >= vertex_count:
            break
        a, b, c = start + 1, start + 2, start + 3
        lines.append(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mesh_stamp(path: str) -> tuple[str, Optional[int], Optional[int]]:
    text = str(path or "")
    try:
        st = os.stat(text)
        return (
            text,
            int(getattr(st, "st_mtime_ns", int(float(st.st_mtime) * 1_000_000_000))),
            int(st.st_size),
        )
    except Exception:
        return (text, None, None)


def _build_output_path(node_item, points_path: str, copy_path: str, match_normal: bool, pack: bool) -> Path:
    model = getattr(node_item, "model", None)
    node_name = _sanitize_name(str(getattr(model, "name", "") or "copy_to_points"))
    sig = hashlib.sha1(
        repr(
            (
                _mesh_stamp(points_path),
                _mesh_stamp(copy_path),
                bool(match_normal),
                bool(pack),
            )
        ).encode("utf-8")
    ).hexdigest()[:10]
    return _copy_dir(node_item) / f"{node_name}_{sig}.obj"


def _bbox_corners(points):
    pts = np.asarray(points, dtype="f4").reshape(-1, 3)
    mins = pts.min(axis=0)
    maxs = pts.max(axis=0)
    return np.asarray(
        [
            [mins[0], mins[1], mins[2]],
            [mins[0], mins[1], maxs[2]],
            [mins[0], maxs[1], mins[2]],
            [mins[0], maxs[1], maxs[2]],
            [maxs[0], mins[1], mins[2]],
            [maxs[0], mins[1], maxs[2]],
            [maxs[0], maxs[1], mins[2]],
            [maxs[0], maxs[1], maxs[2]],
        ],
        dtype="f4",
    )


def _instance_matrix_columns(rotation, translation):
    rot = np.asarray(rotation, dtype="f4").reshape(3, 3)
    tr = np.asarray(translation, dtype="f4").reshape(3)
    return [
        [float(rot[0, 0]), float(rot[1, 0]), float(rot[2, 0]), 0.0],
        [float(rot[0, 1]), float(rot[1, 1]), float(rot[2, 1]), 0.0],
        [float(rot[0, 2]), float(rot[1, 2]), float(rot[2, 2]), 0.0],
        [float(tr[0]), float(tr[1]), float(tr[2]), 1.0],
    ]


def build_copy_to_points_scene_asset(node_item) -> CopyToPointsBuildOutcome:
    if np is None:
        return CopyToPointsBuildOutcome(None, "error", "NumPy is required for Copy To Points.")

    model = getattr(node_item, "model", None)
    points_item = _connected_input_item(node_item, {"points", "target", "surface", "mesh"})
    copy_item = _connected_input_item(node_item, {"copy", "source", "instance", "prototype"})
    points_path = _mesh_path_from_item(points_item).strip()
    copy_path = _mesh_path_from_item(copy_item).strip()
    if not points_item or not points_path:
        return CopyToPointsBuildOutcome(None, "error", "Connect a points source.")
    if not copy_item or not copy_path:
        return CopyToPointsBuildOutcome(None, "error", "Connect a copy source model.")
    try:
        points_file = Path(points_path)
        copy_file = Path(copy_path)
    except Exception:
        return CopyToPointsBuildOutcome(None, "error", "Connected paths are invalid.")
    if not points_file.exists():
        return CopyToPointsBuildOutcome(None, "error", "Points source file was not found.")
    if not copy_file.exists():
        return CopyToPointsBuildOutcome(None, "error", "Copy source file was not found.")

    target_tri_points, target_tri_normals, _target_uvs = _load_mesh_arrays(points_file)
    copy_points, copy_normals, copy_uvs = _load_mesh_arrays(copy_file)
    if target_tri_points is None or copy_points is None:
        return CopyToPointsBuildOutcome(None, "error", "Could not read one of the input meshes.")
    target_points, target_normals = _unique_points_and_normals(target_tri_points, target_tri_normals)
    copy_points = np.asarray(copy_points, dtype="f4").reshape(-1, 3)
    copy_normals = _ensure_normals(copy_points, copy_normals)
    copy_uvs = _ensure_uvs(copy_points, copy_uvs)
    if target_points is None or target_points.size == 0:
        return CopyToPointsBuildOutcome(None, "error", "Points source has no usable points.")
    if copy_points.size == 0:
        return CopyToPointsBuildOutcome(None, "error", "Copy source has no usable geometry.")

    match_normal = _param_bool(model, "match_normal", False)
    pack = _param_bool(model, "pack", False)
    gpu_instances = _param_bool(model, "gpu_instances", True)
    source_center = ((copy_points.min(axis=0) + copy_points.max(axis=0)) * 0.5).astype("f4")
    centered_copy_points = copy_points - source_center
    copy_corners = _bbox_corners(copy_points)
    all_points = []
    all_normals = []
    all_uvs = []
    copies = []
    instance_matrices = []
    instance_bounds_min = None
    instance_bounds_max = None
    vertex_count = int(centered_copy_points.shape[0])
    for point_index, (target_point, target_normal) in enumerate(zip(target_points, target_normals)):
        rotation = _rotation_from_up(target_normal) if match_normal else np.identity(3, dtype="f4")
        normal = np.asarray(target_normal, dtype="f4").reshape(3)
        normal_len = float(np.linalg.norm(normal))
        if normal_len > 1.0e-6:
            normal = normal / normal_len
        if gpu_instances:
            translation = np.asarray(target_point, dtype="f4").reshape(3) - (rotation @ source_center)
            matrix_cols = _instance_matrix_columns(rotation, translation)
            instance_matrices.append(matrix_cols)
            corners = (copy_corners @ rotation.T) + translation
            bmin = corners.min(axis=0).astype("f4")
            bmax = corners.max(axis=0).astype("f4")
            if instance_bounds_min is None or instance_bounds_max is None:
                instance_bounds_min = bmin
                instance_bounds_max = bmax
            else:
                instance_bounds_min = np.minimum(instance_bounds_min, bmin)
                instance_bounds_max = np.maximum(instance_bounds_max, bmax)
            copies.append(
                {
                    "point_index": int(point_index),
                    "packed_center": [float(v) for v in target_point],
                    "packed_normal": [float(v) for v in normal],
                }
            )
            continue
        copy_pts = (centered_copy_points @ rotation.T) + target_point
        copy_nrm = copy_normals @ rotation.T
        start = len(all_points) * vertex_count
        all_points.append(copy_pts.astype("f4", copy=False))
        all_normals.append(copy_nrm.astype("f4", copy=False))
        all_uvs.append(copy_uvs)
        copies.append(
            {
                "point_index": int(point_index),
                "vertex_start": int(start),
                "vertex_count": int(vertex_count),
                "packed_center": [float(v) for v in target_point],
                "packed_normal": [float(v) for v in normal],
            }
        )

    if gpu_instances:
        _set_param(node_item, "path", copy_path, notify_scene=False)
        _set_param(node_item, "points_source", points_path, notify_scene=False)
        _set_param(node_item, "copy_source", copy_path, notify_scene=False)
        copy_to_points: Dict[str, Any] = {
            "schema": "qubit.copy_to_points.v1",
            "gpu_instances": True,
            "match_normal": bool(match_normal),
            "pack": False,
            "pack_requested": bool(pack),
            "prototype_path": str(copy_file),
            "source_center": [float(v) for v in source_center],
            "copy_vertex_count": int(vertex_count),
            "copy_count": int(len(copies)),
            "copies": copies,
            "instance_matrices": instance_matrices,
        }
        if instance_bounds_min is not None and instance_bounds_max is not None:
            copy_to_points["bounds_min"] = [float(v) for v in instance_bounds_min]
            copy_to_points["bounds_max"] = [float(v) for v in instance_bounds_max]
        asset = {
            "path": str(copy_file),
            "ext": str(copy_file.suffix).lower(),
            "node": str(getattr(model, "name", "") or "copy_to_points"),
            "kind": "copy_to_points",
            "visible": True,
            "copy_to_points": copy_to_points,
        }
        asset.update(_copy_surface_payload(copy_item))
        detail = f"GPU instanced {int(len(copies))} model(s) to points."
        if pack:
            detail += " Pack is baked-mode only."
        return CopyToPointsBuildOutcome(asset, "ok", detail)

    merged_points = np.concatenate(all_points, axis=0).astype("f4", copy=False)
    merged_normals = np.concatenate(all_normals, axis=0).astype("f4", copy=False)
    merged_uvs = np.concatenate(all_uvs, axis=0).astype("f4", copy=False)
    output_path = _build_output_path(node_item, points_path, copy_path, match_normal, pack)
    try:
        _write_triangle_obj(output_path, merged_points, merged_normals, merged_uvs)
    except Exception as exc:
        return CopyToPointsBuildOutcome(None, "error", f"Failed to write copied mesh: {exc}")

    _set_param(node_item, "path", str(output_path), notify_scene=False)
    _set_param(node_item, "points_source", points_path, notify_scene=False)
    _set_param(node_item, "copy_source", copy_path, notify_scene=False)
    asset: Dict[str, Any] = {
        "path": str(output_path),
        "ext": ".obj",
        "node": str(getattr(model, "name", "") or "copy_to_points"),
        "kind": "copy_to_points",
        "visible": True,
        "copy_to_points": {
            "schema": "qubit.copy_to_points.v1",
            "gpu_instances": False,
            "match_normal": bool(match_normal),
            "pack": bool(pack),
            "source_center": [float(v) for v in source_center],
            "copy_vertex_count": int(vertex_count),
            "copy_count": int(len(copies)),
            "copies": copies,
        },
    }
    asset.update(_copy_surface_payload(copy_item))
    return CopyToPointsBuildOutcome(
        asset,
        "ok",
        f"Copied {int(len(copies))} model(s) to points.",
    )


def build_ports(node_item) -> None:
    for name, default in (
        ("points", ""),
        ("copy", ""),
        ("match_normal", "0"),
        ("pack", "0"),
        ("gpu_instances", "1"),
        ("path", ""),
        ("points_source", ""),
        ("copy_source", ""),
    ):
        _ensure_param(node_item, name, default)
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        ["match_normal", "pack", "gpu_instances", "path", "points_source", "copy_source"],
    )
    _ensure_visible_params(getattr(node_item, "model", None), ["points", "copy"])
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("points")
        node_item.ensure_input("copy")


class CopyToPointsWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._pending = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("Connect points and copy inputs")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        layout.addWidget(self._status, 0)

        self._match_normal = QtWidgets.QCheckBox("Match Normal")
        self._match_normal.setToolTip(
            "Rotate each copy so its local up axis follows the target point normal."
        )
        self._match_normal.toggled.connect(self._on_match_normal_toggled)
        layout.addWidget(self._match_normal, 0)

        self._pack = QtWidgets.QCheckBox("Pack")
        self._pack.setToolTip(
            "Baked mode only: records each copied mesh range so per-copy effects can deform it later."
        )
        self._pack.toggled.connect(self._on_pack_toggled)
        layout.addWidget(self._pack, 0)

        self._gpu_instances = QtWidgets.QCheckBox("GPU Instances")
        self._gpu_instances.setToolTip(
            "Draws the source mesh once per point with GPU instance transforms. Faster to render, "
            "but it does not create baked per-copy vertex ranges."
        )
        self._gpu_instances.toggled.connect(self._on_gpu_instances_toggled)
        layout.addWidget(self._gpu_instances, 0)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setMinimumHeight(24)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        layout.addWidget(self._view_btn, 0)

        self._sync_from_params()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    def sizeHint(self):
        return QtCore.QSize(COPY_TO_POINTS_NODE_W - 12, COPY_TO_POINTS_BODY_H)

    def _ensure_scene(self) -> None:
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None:
            return
        try:
            if hasattr(self._scene, "linksChanged"):
                self._scene.linksChanged.connect(self._schedule_refresh)
            if hasattr(self._scene, "paramChanged"):
                self._scene.paramChanged.connect(self._on_scene_param_changed)
        except Exception:
            pass

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._schedule_refresh()

    def _schedule_refresh(self, *_args):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(60, self._refresh_status)

    def _sync_from_params(self):
        model = getattr(self._node_item, "model", None)
        for widget, name in (
            (self._match_normal, "match_normal"),
            (self._pack, "pack"),
            (self._gpu_instances, "gpu_instances"),
        ):
            try:
                widget.blockSignals(True)
                widget.setChecked(_param_bool(model, name, True if name == "gpu_instances" else False))
            finally:
                widget.blockSignals(False)
        try:
            gpu = _param_bool(model, "gpu_instances", True)
            self._pack.setEnabled(not gpu)
        except Exception:
            pass

    def _refresh_status(self):
        self._pending = False
        self._sync_from_params()
        outcome = build_copy_to_points_scene_asset(self._node_item)
        self._view_btn.setEnabled(bool(outcome.asset and outcome.status == "ok"))
        if outcome.status == "ok":
            self._status.setStyleSheet("color:#22c55e;font-size:11px;")
        else:
            self._status.setStyleSheet("color:#ef4444;font-size:11px;")
        self._status.setText(outcome.detail)

    def _on_match_normal_toggled(self, checked: bool):
        _set_param(self._node_item, "match_normal", "1" if checked else "0", notify_scene=True)
        self._schedule_refresh()

    def _on_pack_toggled(self, checked: bool):
        _set_param(self._node_item, "pack", "1" if checked else "0", notify_scene=True)
        self._schedule_refresh()

    def _on_gpu_instances_toggled(self, checked: bool):
        _set_param(self._node_item, "gpu_instances", "1" if checked else "0", notify_scene=True)
        self._schedule_refresh()

    def _on_view_clicked(self):
        outcome = build_copy_to_points_scene_asset(self._node_item)
        if not outcome.asset:
            self._refresh_status()
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if callable(handler):
            handler([dict(outcome.asset)], frame=True)


def render_node_body(node_item, y_cursor: int) -> int:
    body = CopyToPointsWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    proxy.resize(node_item.width, body.sizeHint().height())
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + body.sizeHint().height()


COPY_TO_POINTS_SPEC = Spec(
    stripe_color="#06b6d4",
    build_ports=build_ports,
    render_node_body=render_node_body,
)


__all__ = [
    "COPY_TO_POINTS_SPEC",
    "CopyToPointsBuildOutcome",
    "build_copy_to_points_scene_asset",
]
