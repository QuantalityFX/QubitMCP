from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple
import math

import numpy as np

from .fbx_stage5_evaluator import evaluate_rig_at_time


class GroomDeformError(RuntimeError):
    """Raised when groom guide deformation cannot be evaluated."""


def curves_to_line_points(curves: Sequence[Sequence[Sequence[float]]]) -> List[List[float]]:
    line_points: List[List[float]] = []
    for curve in list(curves or []):
        if not isinstance(curve, list) or len(curve) < 2:
            continue
        for idx in range(len(curve) - 1):
            line_points.append([float(v) for v in curve[idx][:3]])
            line_points.append([float(v) for v in curve[idx + 1][:3]])
    return line_points


def _hidden_name_set(hidden_submeshes: Sequence[str] | None) -> set[str]:
    return {str(name).strip().lower() for name in list(hidden_submeshes or []) if str(name).strip()}


def _mesh_visible(mesh_obj: Any, hidden: set[str]) -> bool:
    if not hidden:
        return True
    names = _mesh_names(mesh_obj)
    return not bool(names & hidden)


def _mesh_names(mesh_obj: Any) -> set[str]:
    names = {
        str(getattr(mesh_obj, "name", "") or "").strip().lower(),
    }
    metadata = getattr(mesh_obj, "metadata", None)
    if isinstance(metadata, dict):
        names.add(str(metadata.get("source_mesh_name") or "").strip().lower())
        names.add(str(metadata.get("source_mesh_index") or "").strip().lower())
    names.discard("")
    return names


def _bind_positions(mesh_obj: Any) -> np.ndarray:
    try:
        vertex_count = int(getattr(mesh_obj, "vertex_count", 0) or 0)
    except Exception:
        vertex_count = 0
    metadata = getattr(mesh_obj, "metadata", None)
    raw = metadata.get("bind_positions") if isinstance(metadata, dict) else None
    if vertex_count <= 0 or raw is None:
        return np.zeros((0, 3), dtype="f4")
    try:
        arr = np.asarray(raw, dtype="f4").reshape(-1, 3)
    except Exception:
        return np.zeros((0, 3), dtype="f4")
    if int(arr.shape[0]) < vertex_count:
        padded = np.zeros((vertex_count, 3), dtype="f4")
        padded[: int(arr.shape[0])] = arr
        return padded
    return arr[:vertex_count].astype("f4", copy=False)


def _vertex_influences(mesh_obj: Any, vertex_count: int) -> List[List[Tuple[int, float]]]:
    rows: List[List[Tuple[int, float]]] = [[] for _ in range(max(0, int(vertex_count)))]
    for skin in list(getattr(mesh_obj, "vertex_skins", None) or []):
        try:
            vid = int(getattr(skin, "vertex_index", -1))
        except Exception:
            vid = -1
        if vid < 0 or vid >= len(rows):
            continue
        dst: List[Tuple[int, float]] = []
        for influence in list(getattr(skin, "influences", None) or []):
            try:
                joint = int(getattr(influence, "joint_index", -1))
                weight = float(getattr(influence, "weight", 0.0) or 0.0)
            except Exception:
                continue
            if joint >= 0 and weight > 1.0e-8:
                dst.append((joint, weight))
        rows[vid] = dst
    return rows


def _normalize_influences(values: Dict[int, float], *, max_influences: int = 8) -> List[Dict[str, float | int]]:
    packed = [(int(joint), float(weight)) for joint, weight in values.items() if int(joint) >= 0 and float(weight) > 1.0e-8]
    packed.sort(key=lambda item: (-float(item[1]), int(item[0])))
    if int(max_influences) > 0 and len(packed) > int(max_influences):
        packed = packed[: int(max_influences)]
    total = sum(float(weight) for _, weight in packed)
    if total <= 1.0e-8:
        return []
    return [
        {"joint_index": int(joint), "weight": float(weight) / float(total)}
        for joint, weight in packed
    ]


def _barycentric_3d(point: np.ndarray, tri: np.ndarray) -> np.ndarray:
    a = tri[0].astype("f4", copy=False)
    b = tri[1].astype("f4", copy=False)
    c = tri[2].astype("f4", copy=False)
    v0 = b - a
    v1 = c - a
    v2 = point.astype("f4", copy=False) - a
    d00 = float(np.dot(v0, v0))
    d01 = float(np.dot(v0, v1))
    d11 = float(np.dot(v1, v1))
    d20 = float(np.dot(v2, v0))
    d21 = float(np.dot(v2, v1))
    denom = (d00 * d11) - (d01 * d01)
    if abs(denom) <= 1.0e-12:
        return np.asarray((1.0, 0.0, 0.0), dtype="f4")
    v = ((d11 * d20) - (d01 * d21)) / denom
    w = ((d00 * d21) - (d01 * d20)) / denom
    u = 1.0 - v - w
    bary = np.asarray((u, v, w), dtype="f4")
    bary = np.maximum(bary, np.float32(0.0))
    total = float(bary.sum())
    if total <= 1.0e-8:
        return np.asarray((1.0, 0.0, 0.0), dtype="f4")
    return (bary / np.float32(total)).astype("f4", copy=False)


def _triangle_skin_binding(
    mesh_obj: Any,
    point: np.ndarray,
    *,
    mesh_index: int,
    triangle_index: int,
    max_influences: int,
) -> Dict[str, Any] | None:
    positions = _bind_positions(mesh_obj)
    if positions.size == 0:
        return None
    try:
        tri_indices = np.asarray(list(getattr(mesh_obj, "triangle_indices", None) or []), dtype=np.int64).reshape(-1, 3)
    except Exception:
        return None
    if tri_indices.size == 0:
        return None
    valid = np.all((tri_indices >= 0) & (tri_indices < int(positions.shape[0])), axis=1)
    if not bool(np.any(valid)):
        return None
    if int(triangle_index) < 0 or int(triangle_index) >= int(valid.shape[0]) or not bool(valid[int(triangle_index)]):
        return None
    tri_vertices = tri_indices[int(triangle_index)]
    tri = positions[tri_vertices]
    bary = _barycentric_3d(point, tri)
    vertex_rows = _vertex_influences(mesh_obj, int(positions.shape[0]))
    merged: Dict[int, float] = {}
    vertices = [int(v) for v in tri_vertices.tolist()]
    for corner, vertex_index in enumerate(vertices):
        corner_weight = float(bary[corner])
        if corner_weight <= 1.0e-8:
            continue
        for joint, weight in vertex_rows[vertex_index]:
            merged[int(joint)] = float(merged.get(int(joint), 0.0)) + (float(weight) * corner_weight)
    influences = _normalize_influences(merged, max_influences=max_influences)
    if not influences:
        return None
    projected = (tri * bary[:, None]).sum(axis=0)
    distance = float(np.linalg.norm(projected - point))
    return {
        "mesh_index": int(mesh_index),
        "mesh_name": str(getattr(mesh_obj, "name", "") or ""),
        "triangle_index": int(triangle_index),
        "triangle_vertices": vertices,
        "barycentric": [float(v) for v in bary.tolist()],
        "influences": influences,
        "distance": float(distance),
    }


def _point_to_triangle_influences(
    mesh_obj: Any,
    point: np.ndarray,
    *,
    mesh_index: int,
    max_influences: int,
) -> Dict[str, Any] | None:
    positions = _bind_positions(mesh_obj)
    if positions.size == 0:
        return None
    try:
        tri_indices = np.asarray(list(getattr(mesh_obj, "triangle_indices", None) or []), dtype=np.int64).reshape(-1, 3)
    except Exception:
        return None
    if tri_indices.size == 0:
        return None
    valid = np.all((tri_indices >= 0) & (tri_indices < int(positions.shape[0])), axis=1)
    if not bool(np.any(valid)):
        return None
    original_indices = np.nonzero(valid)[0]
    valid_tri_indices = tri_indices[valid]
    tri_positions = positions[valid_tri_indices]
    centers = tri_positions.mean(axis=1)
    delta = centers - point.reshape(1, 3)
    distances = np.sum(delta * delta, axis=1)
    if distances.size == 0:
        return None
    local = int(np.argmin(distances))
    return _triangle_skin_binding(
        mesh_obj,
        point,
        mesh_index=mesh_index,
        triangle_index=int(original_indices[local]),
        max_influences=max_influences,
    )


def _preferred_triangle_binding(
    meshes: Sequence[Any],
    preferred: Dict[str, Any],
    point: np.ndarray,
    *,
    max_influences: int,
) -> Dict[str, Any] | None:
    if not isinstance(preferred, dict):
        return None
    mesh_name = str(
        preferred.get("skin_mesh_name")
        or preferred.get("source_mesh_name")
        or preferred.get("mesh_name")
        or ""
    ).strip().lower()
    try:
        triangle_index = int(
            preferred.get("source_mesh_triangle_index", preferred.get("triangle_index", -1))
        )
    except Exception:
        triangle_index = -1
    if not mesh_name or triangle_index < 0:
        return None
    for mesh_index, mesh_obj in enumerate(list(meshes or [])):
        if mesh_name not in _mesh_names(mesh_obj):
            continue
        binding = _triangle_skin_binding(
            mesh_obj,
            point,
            mesh_index=mesh_index,
            triangle_index=triangle_index,
            max_influences=max_influences,
        )
        if isinstance(binding, dict):
            binding["preferred_triangle"] = True
            return binding
    return None


def transfer_groom_root_skin_weights(
    root_positions: Sequence[Sequence[float]],
    rig_context: Dict[str, Any] | None,
    *,
    hidden_submeshes: Sequence[str] | None = None,
    preferred_bindings: Sequence[Dict[str, Any]] | None = None,
    max_influences: int = 8,
) -> List[Dict[str, Any]]:
    hidden = _hidden_name_set(hidden_submeshes)
    meshes = [
        mesh
        for mesh in list((rig_context or {}).get("meshes") or [])
        if _mesh_visible(mesh, hidden)
    ]
    out: List[Dict[str, Any]] = []
    for raw in list(root_positions or []):
        try:
            point = np.asarray(raw, dtype="f4").reshape(3)
        except Exception:
            point = np.zeros((3,), dtype="f4")
        preferred = (
            preferred_bindings[len(out)]
            if preferred_bindings is not None and len(out) < len(preferred_bindings) and isinstance(preferred_bindings[len(out)], dict)
            else {}
        )
        best = _preferred_triangle_binding(
            meshes,
            preferred,
            point,
            max_influences=max_influences,
        )
        if isinstance(best, dict):
            best["bind_position"] = [float(v) for v in point.tolist()]
            out.append(best)
            continue
        best: Dict[str, Any] | None = None
        best_distance = 1.0e30
        for mesh_index, mesh_obj in enumerate(meshes):
            candidate = _point_to_triangle_influences(
                mesh_obj,
                point,
                mesh_index=mesh_index,
                max_influences=max_influences,
            )
            if not isinstance(candidate, dict):
                continue
            distance = float(candidate.get("distance", 1.0e30) or 1.0e30)
            if distance < best_distance:
                best = candidate
                best_distance = distance
        if best is None:
            best = {
                "mesh_index": -1,
                "mesh_name": "",
                "triangle_index": -1,
                "triangle_vertices": [],
                "barycentric": [],
                "influences": [],
                "distance": 0.0,
            }
        best["bind_position"] = [float(v) for v in point.tolist()]
        out.append(best)
    return out


def _transform_points_row_major(mats: np.ndarray, points: np.ndarray) -> np.ndarray:
    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]
    out = np.empty_like(points, dtype="f4")
    out[:, 0] = (mats[:, 0, 0] * x) + (mats[:, 0, 1] * y) + (mats[:, 0, 2] * z) + mats[:, 0, 3]
    out[:, 1] = (mats[:, 1, 0] * x) + (mats[:, 1, 1] * y) + (mats[:, 1, 2] * z) + mats[:, 1, 3]
    out[:, 2] = (mats[:, 2, 0] * x) + (mats[:, 2, 1] * y) + (mats[:, 2, 2] * z) + mats[:, 2, 3]
    return out


def _transform_vectors_row_major(mats: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    x = vectors[:, 0]
    y = vectors[:, 1]
    z = vectors[:, 2]
    out = np.empty_like(vectors, dtype="f4")
    out[:, 0] = (mats[:, 0, 0] * x) + (mats[:, 0, 1] * y) + (mats[:, 0, 2] * z)
    out[:, 1] = (mats[:, 1, 0] * x) + (mats[:, 1, 1] * y) + (mats[:, 1, 2] * z)
    out[:, 2] = (mats[:, 2, 0] * x) + (mats[:, 2, 1] * y) + (mats[:, 2, 2] * z)
    return out


def _binding_influences(binding: Dict[str, Any]) -> List[Tuple[int, float]]:
    raw = binding.get("skin_influences")
    if raw is None and isinstance(binding.get("skin"), dict):
        raw = binding.get("skin", {}).get("influences")
    if raw is None:
        raw = binding.get("influences")
    rows: Dict[int, float] = {}
    for entry in list(raw or []):
        if isinstance(entry, dict):
            joint = entry.get("joint_index")
            weight = entry.get("weight")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            joint, weight = entry[0], entry[1]
        else:
            continue
        try:
            joint_i = int(joint)
            weight_f = float(weight)
        except Exception:
            continue
        if joint_i >= 0 and weight_f > 1.0e-8:
            rows[joint_i] = float(rows.get(joint_i, 0.0)) + weight_f
    normalized = _normalize_influences(rows)
    return [(int(entry["joint_index"]), float(entry["weight"])) for entry in normalized]


def _mesh_for_binding(rig_context: Dict[str, Any], binding: Dict[str, Any]) -> Any | None:
    meshes = list((rig_context or {}).get("meshes") or [])
    mesh_name = str(
        binding.get("skin_mesh_name")
        or binding.get("mesh_name")
        or binding.get("source_mesh_name")
        or ""
    ).strip().lower()
    if mesh_name:
        for mesh_obj in meshes:
            if mesh_name in _mesh_names(mesh_obj):
                return mesh_obj
    for key in ("skin_mesh_index", "mesh_index", "source_submesh_index"):
        try:
            mesh_index = int(binding.get(key, -1))
        except Exception:
            mesh_index = -1
        if 0 <= mesh_index < len(meshes):
            return meshes[mesh_index]
    return None


def _mesh_skin_cache_entry(mesh_obj: Any, cache: Dict[int, Tuple[np.ndarray, List[List[Tuple[int, float]]]]]):
    key = int(id(mesh_obj))
    entry = cache.get(key)
    if entry is not None:
        return entry
    positions = _bind_positions(mesh_obj)
    rows = _vertex_influences(mesh_obj, int(positions.shape[0])) if positions.size else []
    entry = (positions, rows)
    cache[key] = entry
    return entry


def _deformed_mesh_vertex(
    mesh_obj: Any,
    vertex_index: int,
    skin_mats: np.ndarray,
    cache: Dict[int, Tuple[np.ndarray, List[List[Tuple[int, float]]]]],
) -> np.ndarray | None:
    positions, rows = _mesh_skin_cache_entry(mesh_obj, cache)
    if positions.size == 0 or vertex_index < 0 or vertex_index >= int(positions.shape[0]):
        return None
    bind = positions[int(vertex_index):int(vertex_index) + 1].astype("f4", copy=False)
    influences = rows[int(vertex_index)] if int(vertex_index) < len(rows) else []
    if not influences:
        return bind[0].astype("f4", copy=True)
    out = np.zeros((1, 3), dtype="f4")
    total = 0.0
    joint_count = int(skin_mats.shape[0])
    for joint, weight in influences:
        if 0 <= int(joint) < joint_count and float(weight) > 1.0e-8:
            out += _transform_points_row_major(skin_mats[int(joint):int(joint) + 1], bind) * np.float32(float(weight))
            total += float(weight)
    if total <= 1.0e-8:
        return bind[0].astype("f4", copy=True)
    if abs(total - 1.0) > 1.0e-5:
        out /= np.float32(total)
    return out[0].astype("f4", copy=False)


def _deformed_scalp_root(
    binding: Dict[str, Any],
    rig_context: Dict[str, Any],
    skin_mats: np.ndarray,
    cache: Dict[int, Tuple[np.ndarray, List[List[Tuple[int, float]]]]],
) -> np.ndarray | None:
    mesh_obj = _mesh_for_binding(rig_context, binding)
    if mesh_obj is None:
        return None
    vertices = list(binding.get("skin_triangle_vertices") or binding.get("triangle_vertices") or [])
    bary = list(binding.get("skin_barycentric") or binding.get("barycentric") or [])
    if len(vertices) < 3 or len(bary) < 3:
        return None
    try:
        weights = np.asarray([float(v) for v in bary[:3]], dtype="f4").reshape(3)
    except Exception:
        return None
    total = float(weights.sum())
    if total <= 1.0e-8:
        return None
    weights = (weights / np.float32(total)).astype("f4", copy=False)
    root = np.zeros((3,), dtype="f4")
    for corner, raw_vertex in enumerate(vertices[:3]):
        try:
            vertex_index = int(raw_vertex)
        except Exception:
            return None
        pos = _deformed_mesh_vertex(mesh_obj, vertex_index, skin_mats, cache)
        if pos is None:
            return None
        root += pos.astype("f4", copy=False) * np.float32(weights[corner])
    return root


def _bind_scalp_root(
    binding: Dict[str, Any],
    rig_context: Dict[str, Any],
    cache: Dict[int, Tuple[np.ndarray, List[List[Tuple[int, float]]]]],
) -> np.ndarray | None:
    mesh_obj = _mesh_for_binding(rig_context, binding)
    if mesh_obj is None:
        return None
    vertices = list(binding.get("skin_triangle_vertices") or binding.get("triangle_vertices") or [])
    bary = list(binding.get("skin_barycentric") or binding.get("barycentric") or [])
    if len(vertices) < 3 or len(bary) < 3:
        return None
    try:
        weights = np.asarray([float(v) for v in bary[:3]], dtype="f4").reshape(3)
    except Exception:
        return None
    total = float(weights.sum())
    if total <= 1.0e-8:
        return None
    weights = (weights / np.float32(total)).astype("f4", copy=False)
    positions, _rows = _mesh_skin_cache_entry(mesh_obj, cache)
    if positions.size == 0:
        return None
    root = np.zeros((3,), dtype="f4")
    for corner, raw_vertex in enumerate(vertices[:3]):
        try:
            vertex_index = int(raw_vertex)
        except Exception:
            return None
        if vertex_index < 0 or vertex_index >= int(positions.shape[0]):
            return None
        root += positions[int(vertex_index)].astype("f4", copy=False) * np.float32(weights[corner])
    return root


def _binding_bind_position(binding: Dict[str, Any]) -> np.ndarray | None:
    for key in ("bind_position", "source_bind_position", "root_position"):
        raw = binding.get(key)
        if raw is None:
            continue
        try:
            return np.asarray(raw, dtype="f4").reshape(3)
        except Exception:
            continue
    return None


def _source_space_deformed_scalp_root(
    binding: Dict[str, Any],
    rig_context: Dict[str, Any],
    skin_mats: np.ndarray,
    cache: Dict[int, Tuple[np.ndarray, List[List[Tuple[int, float]]]]],
) -> np.ndarray | None:
    deformed_root = _deformed_scalp_root(binding, rig_context, skin_mats, cache)
    if deformed_root is None:
        return None
    source_bind = _binding_bind_position(binding)
    rig_bind = _bind_scalp_root(binding, rig_context, cache)
    if source_bind is not None and rig_bind is not None:
        return (source_bind + (deformed_root - rig_bind)).astype("f4", copy=False)
    return deformed_root


def deform_groom_curves(
    curves: Sequence[Sequence[Sequence[float]]],
    guide_bindings: Sequence[Dict[str, Any]],
    rig_context: Dict[str, Any],
    *,
    sample_seconds: float = 0.0,
    mode: str = "skinned_cv",
) -> Tuple[List[List[List[float]]], List[List[float]], List[List[float]], Dict[str, Any]]:
    skeleton = rig_context.get("skeleton") if isinstance(rig_context, dict) else None
    if skeleton is None:
        raise GroomDeformError("rig_context does not contain a skeleton.")
    clip = rig_context.get("clip") if isinstance(rig_context, dict) else None
    evaluation = evaluate_rig_at_time(
        skeleton,
        clip,
        float(sample_seconds),
        loop=bool((rig_context or {}).get("loop", True)),
        include_debug_data=False,
    )
    skin_mats = np.asarray(evaluation.skin_matrices, dtype="f4").reshape(-1, 4, 4)
    joint_count = int(skin_mats.shape[0])
    mode_key = str(mode or "skinned_cv").strip().lower()
    out_curves: List[List[List[float]]] = []
    out_roots: List[List[float]] = []
    bound_count = 0
    missing_count = 0
    scalp_attached_count = 0
    mesh_cache: Dict[int, Tuple[np.ndarray, List[List[Tuple[int, float]]]]] = {}
    for curve_index, curve in enumerate(list(curves or [])):
        try:
            bind_points = np.asarray(curve, dtype="f4").reshape(-1, 3)
        except Exception:
            bind_points = np.zeros((0, 3), dtype="f4")
        if bind_points.size == 0:
            out_curves.append([])
            continue
        binding = guide_bindings[curve_index] if curve_index < len(guide_bindings) and isinstance(guide_bindings[curve_index], dict) else {}
        influences = _binding_influences(binding)
        scalp_root = _source_space_deformed_scalp_root(binding, rig_context, skin_mats, mesh_cache)
        if scalp_root is not None and bind_points.size:
            offsets = (bind_points - bind_points[:1]).astype("f4", copy=False)
            if influences and joint_count > 0 and mode_key in {"skinned_cv", "skinned", "root_frame", "root_rotate", "rotate"}:
                out_offsets = np.zeros_like(offsets, dtype="f4")
                total = 0.0
                for joint, weight in influences:
                    if 0 <= joint < joint_count and float(weight) > 1.0e-8:
                        mats = np.repeat(skin_mats[joint:joint + 1], int(offsets.shape[0]), axis=0)
                        out_offsets += _transform_vectors_row_major(mats, offsets) * np.float32(float(weight))
                        total += float(weight)
                if total > 1.0e-8:
                    if abs(total - 1.0) > 1.0e-5:
                        out_offsets /= np.float32(total)
                    out_points = scalp_root.reshape(1, 3) + out_offsets
                    bound_count += 1
                else:
                    out_points = bind_points + (scalp_root - bind_points[0]).reshape(1, 3)
                    missing_count += 1
            else:
                out_points = bind_points + (scalp_root - bind_points[0]).reshape(1, 3)
                if influences and joint_count > 0:
                    bound_count += 1
                else:
                    missing_count += 1
            scalp_attached_count += 1
        elif not influences or joint_count <= 0:
            out_points = bind_points.astype("f4", copy=True)
            missing_count += 1
        elif mode_key in {"root_offset", "root_translate", "translate"}:
            root = bind_points[:1]
            deformed_root = np.zeros((1, 3), dtype="f4")
            for joint, weight in influences:
                if 0 <= joint < joint_count:
                    deformed_root += _transform_points_row_major(skin_mats[joint:joint + 1], root) * np.float32(weight)
            out_points = bind_points + (deformed_root[0] - root[0]).reshape(1, 3)
            bound_count += 1
        else:
            out_points = np.zeros_like(bind_points, dtype="f4")
            for joint, weight in influences:
                if 0 <= joint < joint_count:
                    mats = np.repeat(skin_mats[joint:joint + 1], int(bind_points.shape[0]), axis=0)
                    out_points += _transform_points_row_major(mats, bind_points) * np.float32(weight)
            bound_count += 1
        curve_out = [[float(v) for v in row] for row in out_points.tolist()]
        out_curves.append(curve_out)
        if curve_out:
            out_roots.append(curve_out[0])
    return (
        out_curves,
        curves_to_line_points(out_curves),
        out_roots,
        {
            "sample_seconds": float(sample_seconds),
            "sampled_time": float(getattr(evaluation, "sampled_time", sample_seconds)),
            "bound_guides": int(bound_count),
            "missing_guides": int(missing_count),
            "scalp_attached_guides": int(scalp_attached_count),
            "joint_count": int(joint_count),
            "mode": mode_key,
        },
    )


__all__ = [
    "GroomDeformError",
    "curves_to_line_points",
    "deform_groom_curves",
    "transfer_groom_root_skin_weights",
]
