from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from echograph.rigging.fbx_canonical import JointTransform
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
from echograph.rigging.fbx_stage7_timeline import clip_sample_time_from_timeline_seconds
from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

FBX_KIND_ALIASES = {"fbx_import", "fbx import", "fbximport"}
_IDENTITY_MATRIX_4X4: Tuple[float, ...] = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def _sanitize_name(name: str, fallback: str = "fbx_animation") -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    return safe.strip("_") or fallback


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


def _export_dir(node_item) -> Path:
    base = _workflow_dir_for_node(node_item)
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    out = base / "exports"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if isinstance(p, dict) and (p.get("name") or "").strip().lower() == key:
            return p.get("value", "") or ""
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


def _param_int(model, name: str, default: int) -> int:
    try:
        return int(float(_param_value(model, name)))
    except Exception:
        return int(default)


def _param_float(model, name: str, default: float) -> float:
    try:
        value = float(_param_value(model, name))
    except Exception:
        value = float(default)
    if not math.isfinite(value):
        value = float(default)
    return float(value)


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


def _find_param_entry(model, name: str):
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return entry
    return None


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    hidden_entry = None
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == store_key:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": store_key, "value": ""}
        params.append(hidden_entry)

    raw = hidden_entry.get("value", "")
    hidden = {tok.strip().lower() for tok in str(raw).split(",") if tok.strip()}
    for name in (names or []):
        if name:
            hidden.add(str(name).strip().lower())
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _migrate_export_scale_default(node_item) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    marker = _param_value(model, "__export_scale_default_v2").strip()
    if marker:
        return
    entry = _find_param_entry(model, "export_scale")
    if entry is not None:
        raw = str(entry.get("value", "") or "").strip().lower()
        if raw in {"", "0", "false", "no", "off", "n"}:
            entry["value"] = "1"
    _ensure_param(node_item, "__export_scale_default_v2", "1")


def _set_param_value(node_item, name: str, value: str, notify_scene: bool = True) -> None:
    try:
        node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
    except Exception:
        pass


def build_ports(node_item) -> None:
    _ensure_param(node_item, "output", "")
    _ensure_param(node_item, "export_animation", "1")
    _ensure_param(node_item, "export_scale", "1")
    _migrate_export_scale_default(node_item)
    _ensure_param(node_item, "start_frame", "0")
    _ensure_param(node_item, "end_frame", "120")
    _ensure_param(node_item, "fps", "30")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [
            "output",
            "export_animation",
            "export_scale",
            "start_frame",
            "end_frame",
            "fps",
            "__export_scale_default_v2",
        ],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("source")


def _ordered_in_edges(scene, item):
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _edge_dst_name(edge) -> str:
    return (
        (getattr(edge, "dst_port_name", None) or "")
        or (getattr(edge, "dst_label", None) or "")
        or (getattr(edge, "dst_name", None) or "")
    ).strip()


def _trace_source_item(scene, src_item):
    visited = set()
    current = src_item
    depth = 0
    while current is not None and depth < 8:
        cur_id = id(current)
        if cur_id in visited:
            return None
        visited.add(cur_id)
        depth += 1

        model = getattr(current, "model", None)
        kind = (getattr(model, "kind", "") or "").strip().lower() if model is not None else ""
        if kind != "switch":
            return current

        upstream = _ordered_in_edges(scene, current)
        if not upstream:
            return None
        current = getattr(upstream[0], "src", None)
    return None


def _resolve_source_item(node_item):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return None

    edges = _ordered_in_edges(scene, node_item)
    if not edges:
        return None
    selected = None
    for edge in edges:
        if _edge_dst_name(edge).lower() == "source":
            selected = edge
            break
    if selected is None:
        selected = edges[0]
    return _trace_source_item(scene, getattr(selected, "src", None))


def _source_display_name(source_item) -> str:
    model = getattr(source_item, "model", None)
    name = (getattr(model, "name", "") or "").strip() if model is not None else ""
    return name or "fbx_animation"


def _default_output_path(node_item) -> Path:
    source_item = _resolve_source_item(node_item)
    if source_item is not None:
        source_name = _sanitize_name(_source_display_name(source_item), "fbx_animation")
    else:
        source_name = _sanitize_name(getattr(getattr(node_item, "model", None), "name", "") or "fbx_animation")
    return _export_dir(node_item) / f"{source_name}_animation_export.fbx"


def _resolve_context_for_export(node_item) -> Tuple[Dict[str, Any], List[str]]:
    source_item = _resolve_source_item(node_item)
    if source_item is None:
        raise RuntimeError("Connect an FBX Import node to this Export FBX Animation node.")
    model = getattr(source_item, "model", None)
    kind = (getattr(model, "kind", "") or "").strip().lower() if model is not None else ""
    if kind not in FBX_KIND_ALIASES:
        raise RuntimeError("Source must be an FBX Import node.")

    try:
        from nodes.fbx_import import spec as fbx_import_spec
    except Exception as exc:
        raise RuntimeError(f"FBX Import module is unavailable: {exc}") from exc

    resolver = getattr(fbx_import_spec, "resolve_fbx_import_sources", None)
    builder = getattr(fbx_import_spec, "_build_preview_asset", None)
    if not callable(resolver) or not callable(builder):
        raise RuntimeError("FBX Import resolver is unavailable.")

    result = resolver(
        source_item,
        base_dir=_workflow_dir_for_node(node_item),
        persist=True,
        validate_bind_data=True,
        validate_animation_data=True,
    )
    errors = [str(msg) for msg in list(getattr(result, "errors", []) or []) if str(msg).strip()]
    if errors:
        raise RuntimeError("\n".join(errors))

    asset = builder(model, result)
    context = asset.get("fbx_rig_context") if isinstance(asset, dict) else None
    if not isinstance(context, dict):
        raise RuntimeError("FBX Import did not expose a rig context. Validate the FBX Import node first.")
    skeleton = context.get("skeleton")
    meshes = list(context.get("meshes") or [])
    if skeleton is None:
        raise RuntimeError("FBX Import rig context has no skeleton.")
    if not meshes:
        raise RuntimeError("FBX Import rig context has no skinned meshes to export.")
    warnings = [str(msg) for msg in list(getattr(result, "warnings", []) or []) if str(msg).strip()]
    return context, warnings


def _quat_normalize(q) -> Tuple[float, float, float, float]:
    try:
        x, y, z, w = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
    except Exception:
        x, y, z, w = 0.0, 0.0, 0.0, 1.0
    length = math.sqrt(max(1.0e-16, (x * x) + (y * y) + (z * z) + (w * w)))
    return (x / length, y / length, z / length, w / length)


def _quat_to_euler_xyz_deg(q) -> Tuple[float, float, float]:
    x, y, z, w = _quat_normalize(q)
    xx = x * x
    yy = y * y
    zz = z * z
    xy = x * y
    xz = x * z
    yz = y * z
    wx = w * x
    wy = w * y
    wz = w * z

    m00 = 1.0 - (2.0 * (yy + zz))
    m01 = 2.0 * (xy - wz)
    m02 = 2.0 * (xz + wy)
    m10 = 2.0 * (xy + wz)
    m11 = 1.0 - (2.0 * (xx + zz))
    m12 = 2.0 * (yz - wx)
    m20 = 2.0 * (xz - wy)
    m21 = 2.0 * (yz + wx)
    m22 = 1.0 - (2.0 * (xx + yy))

    sy = max(-1.0, min(1.0, -m20))
    ry = math.asin(sy)
    cy = math.cos(ry)
    if abs(cy) > 1.0e-8:
        rx = math.atan2(m21, m22)
        rz = math.atan2(m10, m00)
    else:
        rx = math.atan2(-m12, m11)
        rz = 0.0
    return (math.degrees(rx), math.degrees(ry), math.degrees(rz))


def _joint_transform_is_identity(xf: JointTransform, eps: float = 1.0e-6) -> bool:
    tx, ty, tz = tuple(getattr(xf, "translation", (0.0, 0.0, 0.0)))
    sx, sy, sz = tuple(getattr(xf, "scale", (1.0, 1.0, 1.0)))
    qx, qy, qz, qw = _quat_normalize(getattr(xf, "rotation", (0.0, 0.0, 0.0, 1.0)))
    return (
        abs(float(tx)) <= eps
        and abs(float(ty)) <= eps
        and abs(float(tz)) <= eps
        and abs(float(qx)) <= eps
        and abs(float(qy)) <= eps
        and abs(float(qz)) <= eps
        and abs(float(qw) - 1.0) <= eps
        and abs(float(sx) - 1.0) <= eps
        and abs(float(sy) - 1.0) <= eps
        and abs(float(sz) - 1.0) <= eps
    )


def _matrix4_mul(a: Tuple[float, ...], b: Tuple[float, ...]) -> Tuple[float, ...]:
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[(r * 4) + c] = (
                (a[(r * 4) + 0] * b[(0 * 4) + c])
                + (a[(r * 4) + 1] * b[(1 * 4) + c])
                + (a[(r * 4) + 2] * b[(2 * 4) + c])
                + (a[(r * 4) + 3] * b[(3 * 4) + c])
            )
    return tuple(float(v) for v in out)


def _matrix4_from_trs(xf: JointTransform) -> Tuple[float, ...]:
    tx, ty, tz = xf.translation
    x, y, z, w = _quat_normalize(xf.rotation)
    sx, sy, sz = xf.scale

    xx = x * x
    yy = y * y
    zz = z * z
    xy = x * y
    xz = x * z
    yz = y * z
    wx = w * x
    wy = w * y
    wz = w * z

    r00 = 1.0 - (2.0 * (yy + zz))
    r01 = 2.0 * (xy - wz)
    r02 = 2.0 * (xz + wy)
    r10 = 2.0 * (xy + wz)
    r11 = 1.0 - (2.0 * (xx + zz))
    r12 = 2.0 * (yz - wx)
    r20 = 2.0 * (xz - wy)
    r21 = 2.0 * (yz + wx)
    r22 = 1.0 - (2.0 * (xx + yy))
    return (
        r00 * sx, r01 * sy, r02 * sz, tx,
        r10 * sx, r11 * sy, r12 * sz, ty,
        r20 * sx, r21 * sy, r22 * sz, tz,
        0.0, 0.0, 0.0, 1.0,
    )


def _matrix4_inverse(matrix16: Tuple[float, ...]) -> Tuple[float, ...] | None:
    values = tuple(matrix16 or ())
    if len(values) != 16:
        return None
    rows: List[List[float]] = []
    for r in range(4):
        left = [float(values[(r * 4) + c]) for c in range(4)]
        right = [1.0 if r == c else 0.0 for c in range(4)]
        rows.append(left + right)

    for col in range(4):
        pivot_row = col
        pivot_abs = abs(rows[pivot_row][col])
        for cand in range(col + 1, 4):
            val = abs(rows[cand][col])
            if val > pivot_abs:
                pivot_abs = val
                pivot_row = cand
        if pivot_abs <= 1.0e-12:
            return None
        if pivot_row != col:
            rows[col], rows[pivot_row] = rows[pivot_row], rows[col]

        pivot = rows[col][col]
        inv_pivot = 1.0 / pivot
        rows[col] = [value * inv_pivot for value in rows[col]]

        for r in range(4):
            if r == col:
                continue
            factor = rows[r][col]
            if abs(factor) <= 1.0e-16:
                continue
            rows[r] = [rows[r][idx] - (factor * rows[col][idx]) for idx in range(8)]
    return tuple(float(rows[r][4 + c]) for r in range(4) for c in range(4))


def _decompose_matrix_trs(matrix16: Tuple[float, ...]) -> JointTransform:
    values = tuple(matrix16 or _IDENTITY_MATRIX_4X4)
    if len(values) != 16:
        values = _IDENTITY_MATRIX_4X4
    tx, ty, tz = float(values[3]), float(values[7]), float(values[11])
    m00, m01, m02 = float(values[0]), float(values[1]), float(values[2])
    m10, m11, m12 = float(values[4]), float(values[5]), float(values[6])
    m20, m21, m22 = float(values[8]), float(values[9]), float(values[10])

    sx = math.sqrt(max(1.0e-16, (m00 * m00) + (m10 * m10) + (m20 * m20)))
    sy = math.sqrt(max(1.0e-16, (m01 * m01) + (m11 * m11) + (m21 * m21)))
    sz = math.sqrt(max(1.0e-16, (m02 * m02) + (m12 * m12) + (m22 * m22)))
    r00, r01, r02 = m00 / sx, m01 / sy, m02 / sz
    r10, r11, r12 = m10 / sx, m11 / sy, m12 / sz
    r20, r21, r22 = m20 / sx, m21 / sy, m22 / sz
    trace = r00 + r11 + r22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * s
        qx = (r21 - r12) / s
        qy = (r02 - r20) / s
        qz = (r10 - r01) / s
    elif r00 > r11 and r00 > r22:
        s = math.sqrt(max(0.0, 1.0 + r00 - r11 - r22)) * 2.0
        qw = (r21 - r12) / s if s else 1.0
        qx = 0.25 * s
        qy = (r01 + r10) / s if s else 0.0
        qz = (r02 + r20) / s if s else 0.0
    elif r11 > r22:
        s = math.sqrt(max(0.0, 1.0 + r11 - r00 - r22)) * 2.0
        qw = (r02 - r20) / s if s else 1.0
        qx = (r01 + r10) / s if s else 0.0
        qy = 0.25 * s
        qz = (r12 + r21) / s if s else 0.0
    else:
        s = math.sqrt(max(0.0, 1.0 + r22 - r00 - r11)) * 2.0
        qw = (r10 - r01) / s if s else 1.0
        qx = (r02 + r20) / s if s else 0.0
        qy = (r12 + r21) / s if s else 0.0
        qz = 0.25 * s
    return JointTransform(
        translation=(tx, ty, tz),
        rotation=_quat_normalize((qx, qy, qz, qw)),
        scale=(sx, sy, sz),
    )


def _global_bind_matrices(skeleton) -> List[Tuple[float, ...]]:
    joints = list(getattr(skeleton, "joints", []) or [])
    globals_out: List[Tuple[float, ...]] = []
    for idx, joint in enumerate(joints):
        local = _matrix4_from_trs(getattr(joint, "local_bind", JointTransform()))
        parent_idx = int(getattr(joint, "parent_index", -1))
        if parent_idx >= 0 and parent_idx < idx:
            globals_out.append(_matrix4_mul(globals_out[parent_idx], local))
        else:
            globals_out.append(local)
    return globals_out


def _fbx_vec4(fbx_mod, values, w: float = 0.0):
    return fbx_mod.FbxVector4(float(values[0]), float(values[1]), float(values[2]), float(w))


def _fbx_double3(fbx_mod, values):
    return fbx_mod.FbxDouble3(float(values[0]), float(values[1]), float(values[2]))


def _fbx_matrix_from_canonical(fbx_mod, matrix16: Tuple[float, ...]):
    xf = _decompose_matrix_trs(tuple(matrix16 or _IDENTITY_MATRIX_4X4))
    mat = fbx_mod.FbxAMatrix()
    qx, qy, qz, qw = _quat_normalize(xf.rotation)
    mat.SetTQS(
        _fbx_vec4(fbx_mod, xf.translation, 1.0),
        fbx_mod.FbxQuaternion(float(qx), float(qy), float(qz), float(qw)),
        _fbx_vec4(fbx_mod, xf.scale, 0.0),
    )
    return mat


def _set_node_local_transform(fbx_mod, node, xf: JointTransform) -> None:
    node.LclTranslation.Set(_fbx_double3(fbx_mod, xf.translation))
    node.LclRotation.Set(_fbx_double3(fbx_mod, _quat_to_euler_xyz_deg(xf.rotation)))
    node.LclScaling.Set(_fbx_double3(fbx_mod, xf.scale))


def _mesh_bind_positions(mesh_obj) -> List[Tuple[float, float, float]]:
    metadata = getattr(mesh_obj, "metadata", None)
    raw = metadata.get("bind_positions") if isinstance(metadata, dict) else None
    out: List[Tuple[float, float, float]] = []
    for row in list(raw or []):
        try:
            out.append((float(row[0]), float(row[1]), float(row[2])))
        except Exception:
            out.append((0.0, 0.0, 0.0))
    return out


def _mesh_inverse_bind_matrices(mesh_obj, joint_count: int) -> List[Tuple[float, ...] | None]:
    metadata = getattr(mesh_obj, "metadata", None)
    raw = metadata.get("inverse_bind_matrices") if isinstance(metadata, dict) else None
    out: List[Tuple[float, ...] | None] = []
    for row in list(raw or []):
        try:
            values = tuple(float(v) for v in list(row))
        except Exception:
            values = ()
        if len(values) == 16:
            out.append(values)
    if len(out) < joint_count:
        out.extend([None] * (int(joint_count) - len(out)))
    return out[:joint_count]


def _direct_weighted_joint_indices(meshes) -> set[int]:
    out: set[int] = set()
    for mesh_obj in list(meshes or []):
        for skin in list(getattr(mesh_obj, "vertex_skins", []) or []):
            for influence in list(getattr(skin, "influences", []) or []):
                try:
                    joint_idx = int(getattr(influence, "joint_index", -1))
                    weight = float(getattr(influence, "weight", 0.0) or 0.0)
                except Exception:
                    continue
                if joint_idx >= 0 and weight > 0.0:
                    out.add(joint_idx)
    return out


def _export_joint_source_indices(joints, meshes) -> List[int]:
    source_indices = list(range(int(len(joints))))
    if len(source_indices) <= 1:
        return source_indices
    first = joints[0]
    first_name = str(getattr(first, "name", "") or "").strip()
    first_parent = int(getattr(first, "parent_index", -1))
    first_bind = getattr(first, "local_bind", JointTransform())
    direct_weighted = _direct_weighted_joint_indices(meshes)
    if (
        first_name == "RootNode"
        and first_parent < 0
        and 0 not in direct_weighted
        and _joint_transform_is_identity(first_bind)
    ):
        return source_indices[1:]
    return source_indices


def _export_parent_index_for_source(
    source_idx: int,
    joints,
    source_to_export: Dict[int, int],
) -> int:
    try:
        parent_idx = int(getattr(joints[source_idx], "parent_index", -1))
    except Exception:
        parent_idx = -1
    seen: set[int] = set()
    while parent_idx >= 0 and parent_idx < len(joints) and parent_idx not in seen:
        if parent_idx in source_to_export:
            return int(source_to_export[parent_idx])
        seen.add(parent_idx)
        try:
            parent_idx = int(getattr(joints[parent_idx], "parent_index", -1))
        except Exception:
            parent_idx = -1
    return -1


def _cluster_joint_indices_with_ancestors(
    weighted_joint_indices,
    joints,
    source_to_export: Dict[int, int] | None = None,
) -> List[int]:
    out: set[int] = set()
    exportable = source_to_export if isinstance(source_to_export, dict) else None
    joint_count = int(len(joints))
    for raw_idx in list(weighted_joint_indices or []):
        try:
            idx = int(raw_idx)
        except Exception:
            continue
        seen: set[int] = set()
        while 0 <= idx < joint_count and idx not in seen:
            if exportable is None or idx in exportable:
                out.add(idx)
            seen.add(idx)
            try:
                idx = int(getattr(joints[idx], "parent_index", -1))
            except Exception:
                idx = -1
    return sorted(out)


def _set_curve_keys(fbx_mod, prop, layer, axis: str, keys: List[Tuple[float, float]]) -> None:
    curve = prop.GetCurve(layer, axis, True)
    if curve is None:
        return
    curve.KeyModifyBegin()
    try:
        for seconds, value in keys:
            time_obj = fbx_mod.FbxTime()
            time_obj.SetSecondDouble(float(seconds))
            key_index = curve.KeyAdd(time_obj)[0]
            curve.KeySetValue(key_index, float(value))
            curve.KeySetInterpolation(
                key_index,
                fbx_mod.FbxAnimCurveDef.EInterpolationType.eInterpolationLinear,
            )
    finally:
        curve.KeyModifyEnd()


def _fps_time_mode(fbx_mod, fps: float):
    rounded = int(round(float(fps)))
    name = {
        24: "eFrames24",
        25: "eFrames25",
        30: "eFrames30",
        48: "eFrames48",
        50: "eFrames50",
        60: "eFrames60",
        72: "eFrames72",
        96: "eFrames96",
        100: "eFrames100",
        120: "eFrames120",
    }.get(rounded, "eFrames30")
    try:
        return getattr(fbx_mod.FbxTime.EMode, name)
    except Exception:
        return fbx_mod.FbxTime.EMode.eFrames30


def _save_fbx_scene(fbx_mod, manager, scene_obj, output_path: Path, export_animation: bool) -> None:
    ios = manager.GetIOSettings()
    if ios is not None:
        for prop_name, value in (
            ("EXP_FBX_MATERIAL", True),
            ("EXP_FBX_TEXTURE", False),
            ("EXP_FBX_EMBEDDED", False),
            ("EXP_FBX_SHAPE", True),
            ("EXP_FBX_ANIMATION", bool(export_animation)),
            ("EXP_FBX_GLOBAL_SETTINGS", True),
        ):
            prop = getattr(fbx_mod, prop_name, None)
            if prop is not None:
                try:
                    ios.SetBoolProp(prop, bool(value))
                except Exception:
                    pass
    exporter = fbx_mod.FbxExporter.Create(manager, "")
    if exporter is None:
        raise RuntimeError("FBX exporter creation failed.")
    try:
        if not bool(exporter.Initialize(str(output_path), -1, manager.GetIOSettings())):
            status = ""
            try:
                status = exporter.GetStatus().GetErrorString()
            except Exception:
                status = ""
            raise RuntimeError(status or "FBX exporter initialization failed.")
        if not bool(exporter.Export(scene_obj)):
            status = ""
            try:
                status = exporter.GetStatus().GetErrorString()
            except Exception:
                status = ""
            raise RuntimeError(status or "FBX export failed.")
    finally:
        try:
            exporter.Destroy()
        except Exception:
            pass


def export_fbx_animation_from_context(
    context: Dict[str, Any],
    output_path: Path,
    *,
    export_animation: bool,
    export_scale: bool = True,
    start_frame: int,
    end_frame: int,
    fps: float,
) -> Dict[str, Any]:
    try:
        import fbx  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"Autodesk FBX SDK Python runtime is unavailable: {exc}") from exc

    skeleton = context.get("skeleton") if isinstance(context, dict) else None
    meshes = list(context.get("meshes") or []) if isinstance(context, dict) else []
    clip = context.get("clip") if isinstance(context, dict) else None
    loop = bool(context.get("loop", True)) if isinstance(context, dict) else True
    if skeleton is None:
        raise RuntimeError("No skeleton found in source rig context.")
    joints = list(getattr(skeleton, "joints", []) or [])
    if not joints:
        raise RuntimeError("Source skeleton has no joints.")
    if not meshes:
        raise RuntimeError("Source rig has no meshes.")
    if bool(export_animation) and clip is None:
        raise RuntimeError("Export animation is enabled, but the source FBX Import has no animation clip.")

    fps_value = max(1.0, float(fps or 30.0))
    frame_a = int(start_frame)
    frame_b = int(end_frame)
    if frame_b < frame_a:
        frame_a, frame_b = frame_b, frame_a
    frame_count = (frame_b - frame_a + 1) if bool(export_animation) else 1

    output_path = Path(output_path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manager = fbx.FbxManager.Create()
    if manager is None:
        raise RuntimeError("FBX SDK manager creation failed.")
    try:
        ios = fbx.FbxIOSettings.Create(manager, getattr(fbx, "IOSROOT", ""))
        if ios is not None:
            manager.SetIOSettings(ios)
        scene_obj = fbx.FbxScene.Create(manager, "FBXAnimationExport")
        if scene_obj is None:
            raise RuntimeError("FBX scene creation failed.")
        settings = scene_obj.GetGlobalSettings()
        try:
            settings.SetTimeMode(_fps_time_mode(fbx, fps_value))
        except Exception:
            pass

        root = scene_obj.GetRootNode()
        try:
            root.SetName("SceneRoot")
        except Exception:
            pass
        export_joint_sources = _export_joint_source_indices(joints, meshes)
        source_to_export = {int(source_idx): idx for idx, source_idx in enumerate(export_joint_sources)}

        joint_nodes = []
        for source_idx in export_joint_sources:
            joint = joints[source_idx]
            name = _sanitize_name(
                getattr(joint, "name", "") or f"joint_{source_idx}",
                f"joint_{source_idx}",
            )
            attr = fbx.FbxSkeleton.Create(manager, f"{name}_attr")
            if attr is None:
                raise RuntimeError(f"Failed to create skeleton attribute for joint {name!r}.")
            if _export_parent_index_for_source(int(source_idx), joints, source_to_export) < 0:
                attr.SetSkeletonType(fbx.FbxSkeleton.EType.eRoot)
            else:
                attr.SetSkeletonType(fbx.FbxSkeleton.EType.eLimbNode)
            try:
                attr.Size.Set(1.0)
            except Exception:
                pass
            local_bind = getattr(joint, "local_bind", JointTransform())
            node = fbx.FbxNode.Create(manager, name)
            if node is None:
                raise RuntimeError(f"Failed to create skeleton node {name!r}.")
            node.SetNodeAttribute(attr)
            _set_node_local_transform(fbx, node, local_bind)
            joint_nodes.append(node)

        for idx, node in enumerate(joint_nodes):
            source_idx = int(export_joint_sources[idx])
            parent_idx = _export_parent_index_for_source(source_idx, joints, source_to_export)
            if parent_idx >= 0 and parent_idx < idx:
                joint_nodes[parent_idx].AddChild(node)
            else:
                root.AddChild(node)

        global_bind = _global_bind_matrices(skeleton)
        exported_meshes = 0
        mesh_nodes = []
        for mesh_index, mesh_obj in enumerate(meshes):
            positions = _mesh_bind_positions(mesh_obj)
            vertex_count = int(getattr(mesh_obj, "vertex_count", 0) or len(positions))
            if vertex_count <= 0 or not positions:
                continue
            if len(positions) < vertex_count:
                positions.extend([(0.0, 0.0, 0.0)] * (vertex_count - len(positions)))
            positions = positions[:vertex_count]

            mesh_name = _sanitize_name(getattr(mesh_obj, "name", "") or f"mesh_{mesh_index}", f"mesh_{mesh_index}")
            fbx_mesh = fbx.FbxMesh.Create(manager, mesh_name)
            if fbx_mesh is None:
                continue
            fbx_mesh.InitControlPoints(vertex_count)
            for vid, point in enumerate(positions):
                fbx_mesh.SetControlPointAt(_fbx_vec4(fbx, point, 0.0), int(vid))

            triangles = [int(v) for v in list(getattr(mesh_obj, "triangle_indices", []) or [])]
            tri_count = int((len(triangles) // 3) * 3)
            for base in range(0, tri_count, 3):
                a, b, c = triangles[base], triangles[base + 1], triangles[base + 2]
                if not (0 <= a < vertex_count and 0 <= b < vertex_count and 0 <= c < vertex_count):
                    continue
                fbx_mesh.BeginPolygon()
                fbx_mesh.AddPolygon(int(a))
                fbx_mesh.AddPolygon(int(b))
                fbx_mesh.AddPolygon(int(c))
                fbx_mesh.EndPolygon()

            mesh_node = fbx.FbxNode.Create(manager, mesh_name)
            if mesh_node is None:
                continue
            mesh_node.SetNodeAttribute(fbx_mesh)
            root.AddChild(mesh_node)
            mesh_nodes.append(mesh_node)

            skins_by_joint: Dict[int, List[Tuple[int, float]]] = {}
            for skin in list(getattr(mesh_obj, "vertex_skins", []) or []):
                try:
                    vid = int(getattr(skin, "vertex_index", -1))
                except Exception:
                    vid = -1
                if vid < 0 or vid >= vertex_count:
                    continue
                for influence in list(getattr(skin, "influences", []) or []):
                    try:
                        joint_idx = int(getattr(influence, "joint_index", -1))
                        weight = float(getattr(influence, "weight", 0.0) or 0.0)
                    except Exception:
                        joint_idx = -1
                        weight = 0.0
                    if 0 <= joint_idx < len(joints) and joint_idx in source_to_export and weight > 0.0:
                        skins_by_joint.setdefault(joint_idx, []).append((vid, weight))

            if skins_by_joint:
                skin_deformer = fbx.FbxSkin.Create(manager, f"{mesh_name}_skin")
                inv_bind_mats = _mesh_inverse_bind_matrices(mesh_obj, len(joints))
                transform_matrix = _fbx_matrix_from_canonical(fbx, _IDENTITY_MATRIX_4X4)
                cluster_joint_indices = _cluster_joint_indices_with_ancestors(
                    skins_by_joint.keys(),
                    joints,
                    source_to_export,
                )
                for source_joint_idx in cluster_joint_indices:
                    export_joint_idx = source_to_export.get(int(source_joint_idx))
                    if export_joint_idx is None:
                        continue
                    cluster = fbx.FbxCluster.Create(manager, f"{mesh_name}_{joint_nodes[export_joint_idx].GetName()}_cluster")
                    if cluster is None:
                        continue
                    cluster.SetLink(joint_nodes[export_joint_idx])
                    cluster.SetLinkMode(fbx.FbxCluster.ELinkMode.eTotalOne)
                    for vid, weight in skins_by_joint.get(source_joint_idx, []):
                        cluster.AddControlPointIndex(int(vid), float(weight))
                    cluster.SetTransformMatrix(transform_matrix)
                    link_matrix = None
                    inv = inv_bind_mats[source_joint_idx] if source_joint_idx < len(inv_bind_mats) else None
                    bind_global = _matrix4_inverse(tuple(inv)) if inv is not None and len(inv) == 16 else None
                    joint_inv = tuple(getattr(joints[source_joint_idx], "inverse_bind_matrix", ()) or ())
                    if bind_global is None and len(joint_inv) == 16:
                        bind_global = _matrix4_inverse(joint_inv)
                    if bind_global is None:
                        bind_global = global_bind[source_joint_idx] if source_joint_idx < len(global_bind) else _IDENTITY_MATRIX_4X4
                    link_matrix = _fbx_matrix_from_canonical(fbx, bind_global)
                    cluster.SetTransformLinkMatrix(link_matrix)
                    skin_deformer.AddCluster(cluster)
                fbx_mesh.AddDeformer(skin_deformer)
            exported_meshes += 1

        if exported_meshes <= 0:
            raise RuntimeError("No valid source meshes could be exported.")

        pose = fbx.FbxPose.Create(manager, "BindPose")
        if pose is not None:
            pose.SetIsBindPose(True)
            identity = _fbx_matrix_from_canonical(fbx, _IDENTITY_MATRIX_4X4)
            for mesh_node in mesh_nodes:
                pose.Add(mesh_node, fbx.FbxMatrix(identity), False)
            for idx, node in enumerate(joint_nodes):
                source_idx = int(export_joint_sources[idx])
                matrix = global_bind[source_idx] if source_idx < len(global_bind) else _IDENTITY_MATRIX_4X4
                pose.Add(node, fbx.FbxMatrix(_fbx_matrix_from_canonical(fbx, matrix)), False)
            scene_obj.AddPose(pose)

        if bool(export_animation):
            stack = fbx.FbxAnimStack.Create(scene_obj, "ExportedAnimation")
            layer = fbx.FbxAnimLayer.Create(scene_obj, "Base Layer")
            stack.AddMember(layer)
            scene_obj.SetCurrentAnimationStack(stack)

            span_start = fbx.FbxTime()
            span_stop = fbx.FbxTime()
            span_start.SetSecondDouble(0.0)
            span_stop.SetSecondDouble(max(0.0, float(frame_b - frame_a) / fps_value))
            span = fbx.FbxTimeSpan()
            span.Set(span_start, span_stop)
            try:
                stack.SetLocalTimeSpan(span)
                scene_obj.GetGlobalSettings().SetTimelineDefaultTimeSpan(span)
            except Exception:
                pass

            per_joint_keys: List[Dict[str, List[Tuple[float, float]]]] = [
                {
                    "tx": [], "ty": [], "tz": [],
                    "rx": [], "ry": [], "rz": [],
                    "sx": [], "sy": [], "sz": [],
                }
                for _ in joint_nodes
            ]
            for frame in range(frame_a, frame_b + 1):
                source_timeline_seconds = float(frame) / fps_value
                sample_seconds = clip_sample_time_from_timeline_seconds(clip, source_timeline_seconds)
                export_seconds = float(frame - frame_a) / fps_value
                evaluation = evaluate_rig_at_time(
                    skeleton,
                    clip,
                    sample_seconds,
                    loop=bool(loop),
                    include_debug_data=True,
                )
                local_transforms = list(getattr(evaluation, "local_transforms", []) or [])
                for export_idx, source_idx in enumerate(export_joint_sources):
                    if source_idx >= len(local_transforms):
                        continue
                    local = local_transforms[source_idx]
                    tx, ty, tz = tuple(getattr(local, "translation", (0.0, 0.0, 0.0)))
                    rx, ry, rz = _quat_to_euler_xyz_deg(getattr(local, "rotation", (0.0, 0.0, 0.0, 1.0)))
                    keys = per_joint_keys[export_idx]
                    keys["tx"].append((export_seconds, float(tx)))
                    keys["ty"].append((export_seconds, float(ty)))
                    keys["tz"].append((export_seconds, float(tz)))
                    keys["rx"].append((export_seconds, float(rx)))
                    keys["ry"].append((export_seconds, float(ry)))
                    keys["rz"].append((export_seconds, float(rz)))
                    if bool(export_scale):
                        sx, sy, sz = tuple(getattr(local, "scale", (1.0, 1.0, 1.0)))
                        keys["sx"].append((export_seconds, float(sx)))
                        keys["sy"].append((export_seconds, float(sy)))
                        keys["sz"].append((export_seconds, float(sz)))

            for idx, node in enumerate(joint_nodes):
                keys = per_joint_keys[idx]
                _set_curve_keys(fbx, node.LclTranslation, layer, "X", keys["tx"])
                _set_curve_keys(fbx, node.LclTranslation, layer, "Y", keys["ty"])
                _set_curve_keys(fbx, node.LclTranslation, layer, "Z", keys["tz"])
                _set_curve_keys(fbx, node.LclRotation, layer, "X", keys["rx"])
                _set_curve_keys(fbx, node.LclRotation, layer, "Y", keys["ry"])
                _set_curve_keys(fbx, node.LclRotation, layer, "Z", keys["rz"])
                if bool(export_scale):
                    _set_curve_keys(fbx, node.LclScaling, layer, "X", keys["sx"])
                    _set_curve_keys(fbx, node.LclScaling, layer, "Y", keys["sy"])
                    _set_curve_keys(fbx, node.LclScaling, layer, "Z", keys["sz"])

        _save_fbx_scene(fbx, manager, scene_obj, output_path, bool(export_animation))
    finally:
        try:
            manager.Destroy()
        except Exception:
            pass

    return {
        "output": str(output_path),
        "mesh_count": int(exported_meshes),
        "joint_count": int(len(joint_nodes)),
        "frame_count": int(frame_count),
        "export_animation": bool(export_animation),
        "export_scale": bool(export_scale),
    }


def export_from_node(node_item) -> Tuple[Path, Dict[str, Any], List[str]]:
    model = getattr(node_item, "model", None)
    raw_output = (_param_value(model, "output") if model is not None else "").strip()
    output_path = Path(raw_output).expanduser() if raw_output else _default_output_path(node_item)
    if output_path.suffix.lower() != ".fbx":
        output_path = output_path.with_suffix(".fbx")

    export_animation = _param_bool(model, "export_animation", True) if model is not None else True
    export_scale = _param_bool(model, "export_scale", True) if model is not None else True
    start_frame = _param_int(model, "start_frame", 0) if model is not None else 0
    end_frame = _param_int(model, "end_frame", 120) if model is not None else 120
    fps = max(1.0, _param_float(model, "fps", 30.0) if model is not None else 30.0)
    context, warnings = _resolve_context_for_export(node_item)
    stats = export_fbx_animation_from_context(
        context,
        output_path,
        export_animation=bool(export_animation),
        export_scale=bool(export_scale),
        start_frame=int(start_frame),
        end_frame=int(end_frame),
        fps=float(fps),
    )
    return output_path, stats, warnings


def _dialog_parent(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                if win is not None:
                    return win
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


class FBXAnimationExportWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._busy = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setMinimumWidth(0)
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(self._status, 0)

        path_row = QtWidgets.QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(4)
        self._output_edit = QtWidgets.QLineEdit()
        self._output_edit.setPlaceholderText("Output FBX path")
        self._output_edit.setMinimumWidth(0)
        self._output_edit.setMinimumHeight(24)
        self._output_edit.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self._output_edit.setStyleSheet(
            "QLineEdit{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
        )
        self._output_edit.editingFinished.connect(self._on_output_edit_committed)
        path_row.addWidget(self._output_edit, 1)

        self._browse_btn = QtWidgets.QPushButton("Browse")
        self._browse_btn.setFixedWidth(70)
        self._browse_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:2px 6px;}"
            "QPushButton:hover{background:#273449;}"
        )
        self._browse_btn.clicked.connect(self._on_browse_clicked)
        path_row.addWidget(self._browse_btn, 0)
        layout.addLayout(path_row, 0)

        toggle_row = QtWidgets.QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, 0)
        toggle_row.setSpacing(10)

        self._export_animation = QtWidgets.QCheckBox("Export animation")
        self._export_animation.setStyleSheet("QCheckBox{color:#94a3b8;font-size:10px;}")
        self._export_animation.stateChanged.connect(self._on_export_animation_changed)
        toggle_row.addWidget(self._export_animation, 0)

        self._export_scale = QtWidgets.QCheckBox("Scale curves")
        self._export_scale.setStyleSheet("QCheckBox{color:#94a3b8;font-size:10px;}")
        self._export_scale.setToolTip("Export joint scale animation curves. Disable only when the target does not support animated joint scale.")
        self._export_scale.stateChanged.connect(self._on_export_scale_changed)
        toggle_row.addWidget(self._export_scale, 0)
        toggle_row.addStretch(1)
        layout.addLayout(toggle_row, 0)

        range_row = QtWidgets.QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(4)
        label_style = "QLabel{color:#94a3b8;font-size:10px;}"
        for label_text, attr_name, min_val, max_val in (
            ("Start", "_start_spin", -100000, 100000),
            ("End", "_end_spin", -100000, 100000),
        ):
            label = QtWidgets.QLabel(label_text)
            label.setStyleSheet(label_style)
            range_row.addWidget(label, 0)
            spin = QtWidgets.QSpinBox()
            spin.setRange(min_val, max_val)
            spin.setMinimumWidth(58)
            spin.setFixedHeight(22)
            spin.setStyleSheet(
                "QSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:4px;padding:1px 4px;}"
            )
            range_row.addWidget(spin, 1)
            setattr(self, attr_name, spin)
        fps_label = QtWidgets.QLabel("FPS")
        fps_label.setStyleSheet(label_style)
        range_row.addWidget(fps_label, 0)
        self._fps_spin = QtWidgets.QDoubleSpinBox()
        self._fps_spin.setRange(1.0, 240.0)
        self._fps_spin.setDecimals(3)
        self._fps_spin.setSingleStep(1.0)
        self._fps_spin.setMinimumWidth(64)
        self._fps_spin.setFixedHeight(22)
        self._fps_spin.setStyleSheet(
            "QDoubleSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:4px;padding:1px 4px;}"
        )
        range_row.addWidget(self._fps_spin, 1)
        layout.addLayout(range_row, 0)

        self._start_spin.valueChanged.connect(self._on_start_changed)
        self._end_spin.valueChanged.connect(self._on_end_changed)
        self._fps_spin.valueChanged.connect(self._on_fps_changed)

        self._export_btn = QtWidgets.QPushButton("Export FBX")
        self._export_btn.setStyleSheet(
            "QPushButton{background:#7c3aed;color:#f8fafc;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#8b5cf6;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._export_btn.clicked.connect(self._on_export_clicked)
        layout.addWidget(self._export_btn, 0)

        self._ensure_scene()
        self._sync_controls_from_params()
        self._refresh_status()
        try:
            QtCore.QTimer.singleShot(0, self._deferred_refresh_from_scene)
        except Exception:
            pass

    def sizeHint(self):
        return QtCore.QSize(260, 142)

    def _ensure_scene(self):
        if self._scene is None:
            self._scene = self._node_item.scene()
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._on_scene_links_changed)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(self._on_scene_param_changed)
            except Exception:
                pass
        self._scene_connected = True

    def _on_scene_links_changed(self, *_args):
        self._refresh_status()

    def _deferred_refresh_from_scene(self):
        try:
            self._ensure_scene()
        except Exception:
            pass
        self._refresh_status()

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._sync_controls_from_params()
            self._refresh_status()

    def _sync_controls_from_params(self):
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        raw_output = (_param_value(model, "output") or "").strip()
        if not raw_output:
            raw_output = str(_default_output_path(self._node_item))
        try:
            self._output_edit.blockSignals(True)
            self._output_edit.setText(raw_output)
        finally:
            self._output_edit.blockSignals(False)

        export_animation = _param_bool(model, "export_animation", True)
        try:
            self._export_animation.blockSignals(True)
            self._export_animation.setChecked(export_animation)
        finally:
            self._export_animation.blockSignals(False)

        export_scale = _param_bool(model, "export_scale", True)
        try:
            self._export_scale.blockSignals(True)
            self._export_scale.setChecked(export_scale)
        finally:
            self._export_scale.blockSignals(False)

        try:
            self._start_spin.blockSignals(True)
            self._start_spin.setValue(_param_int(model, "start_frame", 0))
        finally:
            self._start_spin.blockSignals(False)
        try:
            self._end_spin.blockSignals(True)
            self._end_spin.setValue(_param_int(model, "end_frame", 120))
        finally:
            self._end_spin.blockSignals(False)
        try:
            self._fps_spin.blockSignals(True)
            self._fps_spin.setValue(max(1.0, _param_float(model, "fps", 30.0)))
        finally:
            self._fps_spin.blockSignals(False)
        self._set_range_enabled(export_animation)

    def _set_range_enabled(self, enabled: bool):
        for widget in (self._start_spin, self._end_spin, self._fps_spin):
            widget.setEnabled(bool(enabled))
        self._export_scale.setEnabled(bool(enabled))

    def _on_output_edit_committed(self):
        text = (self._output_edit.text() or "").strip()
        _set_param_value(self._node_item, "output", text, notify_scene=True)

    def _on_browse_clicked(self):
        current = (self._output_edit.text() or "").strip()
        if not current:
            current = str(_default_output_path(self._node_item))
        parent = _dialog_parent(self._node_item)
        path, _selected = QtWidgets.QFileDialog.getSaveFileName(
            parent,
            "Export FBX Animation",
            current,
            "FBX Files (*.fbx);;All Files (*)",
        )
        if path:
            if not path.lower().endswith(".fbx"):
                path += ".fbx"
            self._output_edit.setText(path)
            _set_param_value(self._node_item, "output", path, notify_scene=True)

    def _on_export_animation_changed(self, state):
        enabled = bool(state == QtCore.Qt.Checked or state == 2)
        self._set_range_enabled(enabled)
        _set_param_value(self._node_item, "export_animation", "1" if enabled else "0", notify_scene=True)

    def _on_export_scale_changed(self, state):
        enabled = bool(state == QtCore.Qt.Checked or state == 2)
        _set_param_value(self._node_item, "export_scale", "1" if enabled else "0", notify_scene=True)

    def _on_start_changed(self, value):
        _set_param_value(self._node_item, "start_frame", str(int(value)), notify_scene=True)

    def _on_end_changed(self, value):
        _set_param_value(self._node_item, "end_frame", str(int(value)), notify_scene=True)

    def _on_fps_changed(self, value):
        _set_param_value(self._node_item, "fps", f"{float(value):.3f}".rstrip("0").rstrip("."), notify_scene=True)

    def _refresh_status(self):
        source = _resolve_source_item(self._node_item)
        if source is None:
            self._status.setText("Connect FBX Import")
            return
        model = getattr(source, "model", None)
        kind = (getattr(model, "kind", "") or "").strip().lower() if model is not None else ""
        if kind not in FBX_KIND_ALIASES:
            self._status.setText("Source must be FBX Import")
            return
        self._status.setText(f"Source: {_source_display_name(source)}")

    def _show_popup(self, icon, text: str, detail: str = ""):
        box = QtWidgets.QMessageBox(_dialog_parent(self._node_item))
        box.setIcon(icon)
        box.setWindowTitle("Export FBX Animation")
        box.setText(text)
        if detail:
            box.setDetailedText(detail)
        box.exec()

    def _on_export_clicked(self):
        if self._busy:
            return
        self._busy = True
        self._export_btn.setEnabled(False)
        self._export_btn.setText("Exporting...")
        try:
            out_path, stats, warnings = export_from_node(self._node_item)
            frame_text = (
                f"{stats.get('frame_count', 0)} frame(s)"
                if bool(stats.get("export_animation", False))
                else "bind pose only"
            )
            detail = "\n".join(warnings[:20])
            self._status.setText(f"Exported: {out_path.name}")
            self._show_popup(
                QtWidgets.QMessageBox.Information,
                f"Exported FBX Animation to:\n{out_path}\n\n"
                f"Meshes: {stats.get('mesh_count', 0)} | Joints: {stats.get('joint_count', 0)} | {frame_text}",
                detail,
            )
        except Exception as exc:
            self._status.setText("Export failed")
            self._show_popup(QtWidgets.QMessageBox.Critical, "Export failed.", str(exc))
        finally:
            self._busy = False
            self._export_btn.setEnabled(True)
            self._export_btn.setText("Export FBX")


def render_node_body(node_item, y_cursor: int) -> int:
    if QtWidgets is None:
        return y_cursor
    body = FBXAnimationExportWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    w = int(getattr(node_item, "width", 240))
    try:
        body.setMinimumWidth(w)
        body.setMaximumWidth(w)
    except Exception:
        pass
    try:
        proxy.setMinimumWidth(w)
        proxy.setMaximumWidth(w)
    except Exception:
        pass
    h = max(int(body.sizeHint().height()), 142)
    proxy.resize(w, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    try:
        node_item.height = max(int(getattr(node_item, "height", 0) or 0), int(y_cursor + h + 10))
    except Exception:
        pass
    return y_cursor + h + 8


FBX_ANIMATION_EXPORT_SPEC = Spec(
    stripe_color="#8b5cf6",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "FBX_ANIMATION_EXPORT_SPEC",
    "build_ports",
    "export_fbx_animation_from_context",
    "export_from_node",
    "render_node_body",
]
