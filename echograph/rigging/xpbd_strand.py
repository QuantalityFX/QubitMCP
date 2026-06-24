"""XPBD strand simulation for groom guide curves."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover - handled at runtime
    np = None  # type: ignore


class XPBDStrandError(RuntimeError):
    """Raised when strand simulation cannot run."""


@dataclass(frozen=True)
class XPBDStrandConfig:
    fps: float = 24.0
    frame_count: int = 48
    substeps: int = 4
    iterations: int = 8
    gravity: tuple[float, float, float] = (0.0, -9.81, 0.0)
    wind: tuple[float, float, float] = (0.0, 0.0, 0.0)
    damping: float = 0.04
    stretch_stiffness: float = 1.0
    bend_stiffness: float = 0.35
    stretch_compliance: float = 0.0
    bend_compliance: float = 0.0005
    root_pin_stiffness: float = 1.0
    max_velocity: float = 250.0
    store_frames: bool = True


@dataclass(frozen=True)
class XPBDStrandResult:
    curves: list[list[list[float]]]
    line_points: list[list[float]]
    frames: list[list[list[list[float]]]]
    debug: dict[str, Any]


def _line_indices_from_spans(spans: Sequence[tuple[int, int]]) -> Any:
    rows: list[int] = []
    for start, count in spans:
        start = int(start)
        count = int(count)
        for local in range(max(0, count - 1)):
            rows.append(start + local)
            rows.append(start + local + 1)
    return np.asarray(rows, dtype=np.int64).reshape(-1) if rows else np.zeros((0,), dtype=np.int64)


def curves_to_line_points(curves: Sequence[Sequence[Sequence[float]]]) -> list[list[float]]:
    line_points: list[list[float]] = []
    for curve in curves or []:
        if not isinstance(curve, (list, tuple)) or len(curve) < 2:
            continue
        for idx in range(len(curve) - 1):
            line_points.append([float(v) for v in curve[idx][:3]])
            line_points.append([float(v) for v in curve[idx + 1][:3]])
    return line_points


def root_indices_for_curves(curves: Sequence[Sequence[Sequence[float]]]) -> list[int]:
    roots: list[int] = []
    cursor = 0
    for curve in curves or []:
        count = len(curve) if isinstance(curve, (list, tuple)) else 0
        if count > 0:
            roots.append(int(cursor))
        cursor += max(0, int(count))
    return roots


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return out


def _vec3(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
    try:
        seq = list(value)
    except Exception:
        return default
    if len(seq) < 3:
        return default
    return (
        _finite_float(seq[0], default[0]),
        _finite_float(seq[1], default[1]),
        _finite_float(seq[2], default[2]),
    )


def _clean_config(config: XPBDStrandConfig) -> XPBDStrandConfig:
    fps = max(1.0e-3, min(1000.0, _finite_float(config.fps, 24.0)))
    frame_count = max(0, min(10000, int(round(_finite_float(config.frame_count, 48)))))
    substeps = max(1, min(128, int(round(_finite_float(config.substeps, 4)))))
    iterations = max(0, min(256, int(round(_finite_float(config.iterations, 8)))))
    damping = max(0.0, min(0.999, _finite_float(config.damping, 0.04)))
    stretch_stiffness = max(0.0, min(1.0, _finite_float(config.stretch_stiffness, 1.0)))
    bend_stiffness = max(0.0, min(1.0, _finite_float(config.bend_stiffness, 0.35)))
    stretch_compliance = max(0.0, _finite_float(config.stretch_compliance, 0.0))
    bend_compliance = max(0.0, _finite_float(config.bend_compliance, 0.0005))
    root_pin_stiffness = max(0.0, min(1.0, _finite_float(config.root_pin_stiffness, 1.0)))
    max_velocity = max(0.0, min(1.0e6, _finite_float(config.max_velocity, 250.0)))
    return XPBDStrandConfig(
        fps=fps,
        frame_count=frame_count,
        substeps=substeps,
        iterations=iterations,
        gravity=_vec3(config.gravity, (0.0, -9.81, 0.0)),
        wind=_vec3(config.wind, (0.0, 0.0, 0.0)),
        damping=damping,
        stretch_stiffness=stretch_stiffness,
        bend_stiffness=bend_stiffness,
        stretch_compliance=stretch_compliance,
        bend_compliance=bend_compliance,
        root_pin_stiffness=root_pin_stiffness,
        max_velocity=max_velocity,
        store_frames=bool(config.store_frames),
    )


def _normalize_curves(
    curves: Sequence[Sequence[Sequence[float]]],
) -> tuple[list[Any], list[tuple[int, int]], int]:
    if np is None:
        raise XPBDStrandError("NumPy is required for XPBD strand simulation.")
    normalized: list[Any] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    invalid_points = 0
    for curve in curves or []:
        rows: list[list[float]] = []
        if isinstance(curve, (list, tuple)):
            for point in curve:
                try:
                    seq = list(point)
                except Exception:
                    invalid_points += 1
                    continue
                if len(seq) < 3:
                    invalid_points += 1
                    continue
                row = [_finite_float(seq[0]), _finite_float(seq[1]), _finite_float(seq[2])]
                rows.append(row)
        arr = np.asarray(rows, dtype=np.float64).reshape((-1, 3)) if rows else np.zeros((0, 3), dtype=np.float64)
        normalized.append(arr)
        spans.append((cursor, int(arr.shape[0])))
        cursor += int(arr.shape[0])
    return normalized, spans, invalid_points


def _export_curves(x: Any, spans: Sequence[tuple[int, int]]) -> list[list[list[float]]]:
    curves: list[list[list[float]]] = []
    for start, count in spans:
        if count <= 0:
            curves.append([])
            continue
        arr = x[int(start) : int(start) + int(count)]
        curves.append([[float(row[0]), float(row[1]), float(row[2])] for row in arr])
    return curves


def _constraint_arrays(
    rest_x: Any,
    spans: Sequence[tuple[int, int]],
) -> tuple[Any, Any, Any, Any, Any, Any]:
    stretch_i: list[int] = []
    stretch_j: list[int] = []
    stretch_rest: list[float] = []
    bend_i: list[int] = []
    bend_j: list[int] = []
    bend_rest: list[float] = []
    eps = 1.0e-10
    for start, count in spans:
        start = int(start)
        count = int(count)
        for local in range(max(0, count - 1)):
            i = start + local
            j = i + 1
            rest = float(np.linalg.norm(rest_x[j] - rest_x[i]))
            if rest > eps:
                stretch_i.append(i)
                stretch_j.append(j)
                stretch_rest.append(rest)
        for local in range(max(0, count - 2)):
            i = start + local
            j = i + 2
            rest = float(np.linalg.norm(rest_x[j] - rest_x[i]))
            if rest > eps:
                bend_i.append(i)
                bend_j.append(j)
                bend_rest.append(rest)
    return (
        np.asarray(stretch_i, dtype=np.int64),
        np.asarray(stretch_j, dtype=np.int64),
        np.asarray(stretch_rest, dtype=np.float64),
        np.asarray(bend_i, dtype=np.int64),
        np.asarray(bend_j, dtype=np.int64),
        np.asarray(bend_rest, dtype=np.float64),
    )


def _coerce_points_array(points: Any, expected_count: int) -> Any | None:
    if np is None or points is None:
        return None
    try:
        arr = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    except Exception:
        return None
    if int(arr.shape[0]) != int(expected_count):
        return None
    return arr


def build_mesh_collider(
    vertices: Any,
    triangle_indices: Any,
    *,
    margin: float | None = None,
) -> dict[str, Any]:
    """Build a nearest-surface collider for a closed, outward-facing triangle mesh."""
    if np is None:
        raise XPBDStrandError("NumPy is required for strand mesh collision.")
    try:
        points = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
        triangles = np.asarray(triangle_indices, dtype=np.int64).reshape(-1, 3)
    except Exception as exc:
        raise XPBDStrandError(f"Collider mesh arrays are invalid: {exc}") from exc
    if points.shape[0] < 4 or triangles.shape[0] < 4:
        raise XPBDStrandError("Collider mesh needs at least four vertices and four triangles.")
    valid = np.all((triangles >= 0) & (triangles < int(points.shape[0])), axis=1)
    triangles = triangles[valid]
    if triangles.shape[0] < 4:
        raise XPBDStrandError("Collider mesh has no usable triangle surface.")

    tri_points = points[triangles]
    face_normals = np.cross(tri_points[:, 1] - tri_points[:, 0], tri_points[:, 2] - tri_points[:, 0])
    face_lengths = np.linalg.norm(face_normals, axis=1)
    usable_faces = face_lengths > 1.0e-12
    triangles = triangles[usable_faces]
    tri_points = tri_points[usable_faces]
    face_normals = face_normals[usable_faces]
    face_lengths = face_lengths[usable_faces]
    if triangles.shape[0] < 4:
        raise XPBDStrandError("Collider mesh triangles are degenerate.")
    face_normals /= face_lengths[:, None]

    vertex_normals = np.zeros_like(points, dtype=np.float64)
    for corner in range(3):
        np.add.at(vertex_normals, triangles[:, corner], face_normals)
    normal_lengths = np.linalg.norm(vertex_normals, axis=1)
    usable_vertices = normal_lengths > 1.0e-12
    if not np.any(usable_vertices):
        raise XPBDStrandError("Collider mesh has no usable surface normals.")
    vertex_normals[usable_vertices] /= normal_lengths[usable_vertices, None]
    surface_vertices = points[usable_vertices]
    surface_normals = vertex_normals[usable_vertices]

    if margin is None:
        sample = tri_points[: min(20000, int(tri_points.shape[0]))]
        edge_lengths = np.concatenate(
            (
                np.linalg.norm(sample[:, 1] - sample[:, 0], axis=1),
                np.linalg.norm(sample[:, 2] - sample[:, 1], axis=1),
                np.linalg.norm(sample[:, 0] - sample[:, 2], axis=1),
            )
        )
        positive_edges = edge_lengths[edge_lengths > 1.0e-12]
        diagonal = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
        edge_margin = float(np.median(positive_edges)) * 0.2 if positive_edges.size else 0.0
        collision_margin = max(diagonal * 1.0e-6, edge_margin)
    else:
        collision_margin = max(0.0, _finite_float(margin, 0.0))

    tree = None
    face_tree = None
    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(surface_vertices)
        face_tree = cKDTree(tri_points.mean(axis=1))
    except Exception:
        tree = None
        face_tree = None
    return {
        "schema": "qubit.xpbd_strand.mesh_collider.v1",
        "vertices": surface_vertices,
        "normals": surface_normals,
        "triangle_count": int(triangles.shape[0]),
        "triangle_points": tri_points,
        "face_normals": face_normals,
        "margin": float(collision_margin),
        "tree": tree,
        "face_tree": face_tree,
    }


def project_points_from_mesh_collider(
    points: Any,
    inv_mass: Any,
    collider: dict[str, Any] | None,
) -> int:
    """Project movable points out of a prepared closed mesh collider."""
    if np is None or not isinstance(collider, dict):
        return 0
    try:
        x = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        weights = np.asarray(inv_mass, dtype=np.float64).reshape(-1)
        surface = np.asarray(collider.get("vertices"), dtype=np.float64).reshape(-1, 3)
        normals = np.asarray(collider.get("normals"), dtype=np.float64).reshape(-1, 3)
    except Exception:
        return 0
    if x.shape[0] == 0 or weights.shape[0] != x.shape[0] or surface.shape != normals.shape or surface.shape[0] == 0:
        return 0
    movable = np.flatnonzero(weights > 0.0)
    if movable.size == 0:
        return 0
    query_points = x[movable]
    face_tree = collider.get("face_tree")
    triangle_points = collider.get("triangle_points")
    face_normals = collider.get("face_normals")
    signed_gap = None
    contact_normals = None
    if face_tree is not None and triangle_points is not None and face_normals is not None:
        try:
            from trimesh.triangles import closest_point as closest_points_on_triangles

            tri_points = np.asarray(triangle_points, dtype=np.float64).reshape(-1, 3, 3)
            tri_normals = np.asarray(face_normals, dtype=np.float64).reshape(-1, 3)
            candidate_count = min(12, int(tri_points.shape[0]))
            signed_chunks = []
            normal_chunks = []
            for start in range(0, int(query_points.shape[0]), 4096):
                query_chunk = query_points[start : start + 4096]
                try:
                    _distance, candidates = face_tree.query(query_chunk, k=candidate_count, workers=-1)
                except TypeError:
                    _distance, candidates = face_tree.query(query_chunk, k=candidate_count)
                candidates = np.asarray(candidates, dtype=np.int64)
                if candidates.ndim == 1:
                    candidates = candidates.reshape(-1, 1)
                candidate_triangles = tri_points[candidates].reshape(-1, 3, 3)
                repeated_points = np.repeat(query_chunk, candidates.shape[1], axis=0)
                closest = closest_points_on_triangles(candidate_triangles, repeated_points)
                delta = repeated_points - closest
                distances_sq = np.einsum("ij,ij->i", delta, delta).reshape(query_chunk.shape[0], candidates.shape[1])
                best_slot = np.argmin(distances_sq, axis=1)
                best_triangles = candidates[np.arange(query_chunk.shape[0]), best_slot]
                best_closest = closest.reshape(query_chunk.shape[0], candidates.shape[1], 3)[
                    np.arange(query_chunk.shape[0]), best_slot
                ]
                best_normals = tri_normals[best_triangles]
                signed_chunks.append(np.einsum("ij,ij->i", query_chunk - best_closest, best_normals))
                normal_chunks.append(best_normals)
            signed_gap = np.concatenate(signed_chunks, axis=0)
            contact_normals = np.concatenate(normal_chunks, axis=0)
        except Exception:
            signed_gap = None
            contact_normals = None
    tree = collider.get("tree")
    if signed_gap is None or contact_normals is None:
        if tree is not None:
            try:
                _distance, nearest = tree.query(query_points, k=1, workers=-1)
            except TypeError:
                _distance, nearest = tree.query(query_points, k=1)
            nearest = np.asarray(nearest, dtype=np.int64).reshape(-1)
        else:
            nearest = np.empty((query_points.shape[0],), dtype=np.int64)
            for index, point in enumerate(query_points):
                nearest[index] = int(np.argmin(np.einsum("ij,ij->i", surface - point, surface - point)))
        nearest = np.clip(nearest, 0, int(surface.shape[0]) - 1)
        contact_points = surface[nearest]
        contact_normals = normals[nearest]
        signed_gap = np.einsum("ij,ij->i", query_points - contact_points, contact_normals)
    margin = max(0.0, _finite_float(collider.get("margin"), 0.0))
    penetrating = signed_gap < margin
    if not np.any(penetrating):
        return 0
    correction = (margin - signed_gap[penetrating])[:, None] * contact_normals[penetrating]
    x[movable[penetrating]] += correction
    return int(np.count_nonzero(penetrating))


def build_strand_runtime(
    curves: Sequence[Sequence[Sequence[float]]],
    config: XPBDStrandConfig | None = None,
    *,
    initial_points: Any = None,
) -> dict[str, Any]:
    if np is None:
        raise XPBDStrandError("NumPy is required for XPBD strand simulation.")
    cfg = _clean_config(config or XPBDStrandConfig())
    normalized, spans, invalid_points = _normalize_curves(curves)
    if normalized:
        rest_x = np.concatenate(normalized, axis=0)
    else:
        rest_x = np.zeros((0, 3), dtype=np.float64)
    point_count = int(rest_x.shape[0])
    initial = _coerce_points_array(initial_points, point_count)
    x = np.array(initial if initial is not None else rest_x, dtype=np.float64, copy=True)
    inv_mass = np.ones((point_count,), dtype=np.float64)
    root_indices = _root_indices_from_spans(spans)
    if cfg.root_pin_stiffness > 0.0 and root_indices.size:
        inv_mass[root_indices] = 0.0
    stretch_i, stretch_j, stretch_rest, bend_i, bend_j, bend_rest = _constraint_arrays(rest_x, spans)
    return {
        "schema": "qubit.xpbd_strand.runtime.v1",
        "spans": list((int(start), int(count)) for start, count in spans),
        "invalid_points": int(invalid_points),
        "rest_points": np.asarray(rest_x, dtype=np.float64).reshape(-1, 3),
        "points": np.asarray(x, dtype=np.float64).reshape(-1, 3),
        "velocities": np.zeros((point_count, 3), dtype=np.float64),
        "inv_mass": inv_mass,
        "root_indices": root_indices,
        "rest_roots": np.asarray(x[root_indices], dtype=np.float64).reshape(-1, 3) if root_indices.size else np.zeros((0, 3), dtype=np.float64),
        "line_point_indices": _line_indices_from_spans(spans),
        "stretch_i": stretch_i,
        "stretch_j": stretch_j,
        "stretch_rest": stretch_rest,
        "bend_i": bend_i,
        "bend_j": bend_j,
        "bend_rest": bend_rest,
        "point_count": int(point_count),
        "root_count": int(root_indices.size),
        "segment_count": int(sum(max(0, int(count) - 1) for _start, count in spans)),
    }


def reset_strand_runtime(
    runtime: dict[str, Any],
    *,
    points: Any = None,
    root_targets: Any = None,
) -> None:
    if np is None:
        raise XPBDStrandError("NumPy is required for XPBD strand simulation.")
    if not isinstance(runtime, dict):
        raise XPBDStrandError("XPBD strand runtime is invalid.")
    current = np.asarray(runtime.get("points"), dtype=np.float64).reshape(-1, 3)
    replacement = _coerce_points_array(points, int(current.shape[0]))
    if replacement is None:
        rest = _coerce_points_array(runtime.get("rest_points"), int(current.shape[0]))
        replacement = rest if rest is not None else current
    runtime["points"] = np.asarray(replacement, dtype=np.float64).reshape(-1, 3).copy()
    runtime["velocities"] = np.zeros_like(runtime["points"], dtype=np.float64)
    roots = np.asarray(runtime.get("root_indices"), dtype=np.int64).reshape(-1)
    targets = _runtime_root_targets(runtime, root_targets)
    if roots.size and targets.shape[0] == roots.size:
        runtime["points"][roots] = targets
        runtime["rest_roots"] = np.asarray(targets, dtype=np.float64).reshape(-1, 3).copy()


def _runtime_root_targets(runtime: dict[str, Any], root_targets: Any = None) -> Any:
    roots = np.asarray(runtime.get("root_indices"), dtype=np.int64).reshape(-1)
    root_count = int(roots.size)
    if root_count <= 0:
        return np.zeros((0, 3), dtype=np.float64)
    if root_targets is not None:
        try:
            targets = np.asarray(root_targets, dtype=np.float64).reshape(-1, 3)
            if int(targets.shape[0]) >= root_count:
                return targets[:root_count].astype(np.float64, copy=False)
        except Exception:
            pass
    rest_roots = np.asarray(runtime.get("rest_roots"), dtype=np.float64).reshape(-1, 3)
    if int(rest_roots.shape[0]) == root_count:
        return rest_roots
    points = np.asarray(runtime.get("points"), dtype=np.float64).reshape(-1, 3)
    return points[roots] if points.shape[0] and roots.size else np.zeros((root_count, 3), dtype=np.float64)


def runtime_line_points(runtime: dict[str, Any]) -> Any:
    if np is None:
        raise XPBDStrandError("NumPy is required for XPBD strand simulation.")
    points = np.asarray(runtime.get("points"), dtype=np.float64).reshape(-1, 3)
    indices = np.asarray(runtime.get("line_point_indices"), dtype=np.int64).reshape(-1)
    if points.shape[0] <= 0 or indices.size <= 0:
        return np.zeros((0, 3), dtype="f4")
    valid = (indices >= 0) & (indices < int(points.shape[0]))
    if bool(np.all(valid)):
        return points[indices].astype("f4", copy=False)
    return points[indices[valid]].astype("f4", copy=False)


def runtime_root_points(runtime: dict[str, Any]) -> Any:
    if np is None:
        raise XPBDStrandError("NumPy is required for XPBD strand simulation.")
    points = np.asarray(runtime.get("points"), dtype=np.float64).reshape(-1, 3)
    roots = np.asarray(runtime.get("root_indices"), dtype=np.int64).reshape(-1)
    if points.shape[0] <= 0 or roots.size <= 0:
        return np.zeros((0, 3), dtype="f4")
    valid = roots[(roots >= 0) & (roots < int(points.shape[0]))]
    return points[valid].astype("f4", copy=False) if valid.size else np.zeros((0, 3), dtype="f4")


def step_strand_runtime(
    runtime: dict[str, Any],
    config: XPBDStrandConfig | None = None,
    *,
    root_targets: Any = None,
    steps: int = 1,
    dt: float | None = None,
    collider: dict[str, Any] | None = None,
) -> tuple[Any, Any, Any, dict[str, Any]]:
    if np is None:
        raise XPBDStrandError("NumPy is required for XPBD strand simulation.")
    if not isinstance(runtime, dict):
        raise XPBDStrandError("XPBD strand runtime is invalid.")
    cfg = _clean_config(config or XPBDStrandConfig())
    x = np.asarray(runtime.get("points"), dtype=np.float64).reshape(-1, 3)
    v = np.asarray(runtime.get("velocities"), dtype=np.float64).reshape(-1, 3)
    if x.shape != v.shape:
        v = np.zeros_like(x, dtype=np.float64)
    inv_mass = np.asarray(runtime.get("inv_mass"), dtype=np.float64).reshape(-1)
    if inv_mass.shape[0] != x.shape[0]:
        inv_mass = np.ones((x.shape[0],), dtype=np.float64)
    roots = np.asarray(runtime.get("root_indices"), dtype=np.int64).reshape(-1)
    roots_pinned = bool(cfg.root_pin_stiffness > 0.0 and roots.size > 0)
    if roots_pinned:
        inv_mass[roots] = 0.0
    stretch_i = np.asarray(runtime.get("stretch_i"), dtype=np.int64).reshape(-1)
    stretch_j = np.asarray(runtime.get("stretch_j"), dtype=np.int64).reshape(-1)
    stretch_rest = np.asarray(runtime.get("stretch_rest"), dtype=np.float64).reshape(-1)
    bend_i = np.asarray(runtime.get("bend_i"), dtype=np.int64).reshape(-1)
    bend_j = np.asarray(runtime.get("bend_j"), dtype=np.int64).reshape(-1)
    bend_rest = np.asarray(runtime.get("bend_rest"), dtype=np.float64).reshape(-1)

    step_count = max(0, min(240, int(round(_finite_float(steps, 1)))))
    frame_dt = float(dt) if dt is not None and math.isfinite(float(dt)) and float(dt) > 0.0 else 1.0 / max(float(cfg.fps), 1.0e-6)
    sub_dt = frame_dt / max(1, int(cfg.substeps))
    damping_factor = max(0.0, min(1.0, 1.0 - float(cfg.damping))) ** (1.0 / max(1, int(cfg.substeps)))
    accel = np.asarray(cfg.gravity, dtype=np.float64) + np.asarray(cfg.wind, dtype=np.float64)
    free_mask = inv_mass > 0.0
    targets = _runtime_root_targets(runtime, root_targets)
    collision_count = 0

    for _frame in range(step_count):
        for _substep in range(int(cfg.substeps)):
            if np.any(free_mask):
                v[free_mask] += accel * sub_dt
                v[free_mask] *= damping_factor
            x_prev = np.array(x, dtype=np.float64, copy=True)
            x += v * sub_dt
            if roots_pinned and targets.shape[0] == roots.size:
                x[roots] = targets
                v[roots] = 0.0

            stretch_lambdas = np.zeros((stretch_i.size,), dtype=np.float64)
            bend_lambdas = np.zeros((bend_i.size,), dtype=np.float64)
            for _iteration in range(int(cfg.iterations)):
                _solve_distance_constraints(
                    x,
                    inv_mass,
                    stretch_i,
                    stretch_j,
                    stretch_rest,
                    stretch_lambdas,
                    compliance=cfg.stretch_compliance,
                    stiffness=cfg.stretch_stiffness,
                    dt=sub_dt,
                )
                _solve_distance_constraints(
                    x,
                    inv_mass,
                    bend_i,
                    bend_j,
                    bend_rest,
                    bend_lambdas,
                    compliance=cfg.bend_compliance,
                    stiffness=cfg.bend_stiffness,
                    dt=sub_dt,
                )
                collision_count += project_points_from_mesh_collider(x, inv_mass, collider)
            if int(cfg.iterations) <= 0:
                collision_count += project_points_from_mesh_collider(x, inv_mass, collider)
            if roots_pinned and targets.shape[0] == roots.size:
                x[roots] = targets
            v = (x - x_prev) / sub_dt
            if roots_pinned:
                v[roots] = 0.0
            if cfg.max_velocity > 0.0 and v.size:
                speeds = np.linalg.norm(v, axis=1)
                mask = speeds > cfg.max_velocity
                if np.any(mask):
                    scale = cfg.max_velocity / np.maximum(speeds[mask], 1.0e-12)
                    v[mask] *= scale.reshape((-1, 1))

    if roots_pinned and targets.shape[0] == roots.size:
        x[roots] = targets
        v[roots] = 0.0
    runtime["points"] = x
    runtime["velocities"] = v
    runtime["inv_mass"] = inv_mass
    line_points = runtime_line_points(runtime)
    root_points = runtime_root_points(runtime)
    return (
        x.astype("f4", copy=False),
        line_points,
        root_points,
        {
            "point_count": int(x.shape[0]),
            "root_count": int(roots.size),
            "line_point_count": int(line_points.shape[0]),
            "steps": int(step_count),
            "substeps": int(cfg.substeps),
            "iterations": int(cfg.iterations),
            "roots_pinned": bool(roots_pinned),
            "runtime_cached": True,
            "collider_enabled": bool(isinstance(collider, dict)),
            "collision_projection_count": int(collision_count),
        },
    )


def _solve_distance_constraints(
    x: Any,
    inv_mass: Any,
    idx_i: Any,
    idx_j: Any,
    rest: Any,
    lambdas: Any,
    *,
    compliance: float,
    stiffness: float,
    dt: float,
) -> None:
    if idx_i.size == 0 or stiffness <= 0.0:
        return
    alpha = float(compliance) / max(float(dt) * float(dt), 1.0e-12)
    eps = 1.0e-12
    for c_idx in range(int(idx_i.size)):
        i = int(idx_i[c_idx])
        j = int(idx_j[c_idx])
        wi = float(inv_mass[i])
        wj = float(inv_mass[j])
        wsum = wi + wj
        if wsum <= 0.0:
            continue
        delta = x[j] - x[i]
        length = float(np.linalg.norm(delta))
        if length <= eps:
            continue
        grad = delta / length
        c_value = length - float(rest[c_idx])
        denom = wsum + alpha
        if denom <= eps:
            continue
        dlambda = -(c_value + alpha * float(lambdas[c_idx])) / denom
        dlambda *= float(stiffness)
        lambdas[c_idx] += dlambda
        if wi > 0.0:
            x[i] -= wi * dlambda * grad
        if wj > 0.0:
            x[j] += wj * dlambda * grad


def _root_indices_from_spans(spans: Sequence[tuple[int, int]]) -> Any:
    roots = [int(start) for start, count in spans if int(count) > 0]
    return np.asarray(roots, dtype=np.int64)


def _root_targets_for_frame(
    root_positions_by_frame: Sequence[Sequence[Sequence[float]]] | None,
    frame_index: int,
    rest_roots: Any,
) -> Any:
    if np is None:
        raise XPBDStrandError("NumPy is required for XPBD strand simulation.")
    if not root_positions_by_frame:
        return rest_roots
    try:
        frame_rows = root_positions_by_frame[min(max(0, int(frame_index)), len(root_positions_by_frame) - 1)]
    except Exception:
        return rest_roots
    rows = np.array(rest_roots, dtype=np.float64, copy=True)
    if not isinstance(frame_rows, (list, tuple)):
        return rows
    for idx in range(min(len(frame_rows), rows.shape[0])):
        rows[idx] = np.asarray(_vec3(frame_rows[idx], tuple(rows[idx])), dtype=np.float64)
    return rows


def simulate_strands(
    curves: Sequence[Sequence[Sequence[float]]],
    config: XPBDStrandConfig | None = None,
    *,
    root_positions_by_frame: Sequence[Sequence[Sequence[float]]] | None = None,
    collider: dict[str, Any] | None = None,
) -> XPBDStrandResult:
    if np is None:
        raise XPBDStrandError("NumPy is required for XPBD strand simulation.")
    cfg = _clean_config(config or XPBDStrandConfig())
    normalized, spans, invalid_points = _normalize_curves(curves)
    if normalized:
        rest_x = np.concatenate(normalized, axis=0)
    else:
        rest_x = np.zeros((0, 3), dtype=np.float64)
    if rest_x.size == 0:
        empty: list[list[list[float]]] = [[] for _ in spans]
        return XPBDStrandResult(
            curves=empty,
            line_points=[],
            frames=[empty] if cfg.store_frames else [],
            debug={
                "input_curve_count": int(len(curves or [])),
                "simulated_curve_count": 0,
                "point_count": 0,
                "invalid_points": int(invalid_points),
                "frame_count": int(cfg.frame_count),
                "substeps": int(cfg.substeps),
                "iterations": int(cfg.iterations),
            },
        )

    x = np.array(rest_x, dtype=np.float64, copy=True)
    v = np.zeros_like(x)
    inv_mass = np.ones((x.shape[0],), dtype=np.float64)
    root_indices = _root_indices_from_spans(spans)
    roots_pinned = bool(cfg.root_pin_stiffness > 0.0 and root_indices.size > 0)
    rest_roots = np.array(x[root_indices], dtype=np.float64, copy=True) if root_indices.size else np.zeros((0, 3), dtype=np.float64)
    if roots_pinned:
        inv_mass[root_indices] = 0.0

    stretch_i, stretch_j, stretch_rest, bend_i, bend_j, bend_rest = _constraint_arrays(rest_x, spans)
    accel = np.asarray(cfg.gravity, dtype=np.float64) + np.asarray(cfg.wind, dtype=np.float64)
    frame_dt = 1.0 / max(float(cfg.fps), 1.0e-6)
    sub_dt = frame_dt / max(1, int(cfg.substeps))
    damping_factor = max(0.0, min(1.0, 1.0 - float(cfg.damping))) ** (1.0 / max(1, int(cfg.substeps)))
    frames: list[list[list[list[float]]]] = []
    if cfg.store_frames:
        frames.append(_export_curves(x, spans))

    free_mask = inv_mass > 0.0
    collision_count = 0
    for frame_idx in range(1, int(cfg.frame_count) + 1):
        root_targets = _root_targets_for_frame(root_positions_by_frame, frame_idx, rest_roots)
        for _substep in range(int(cfg.substeps)):
            if np.any(free_mask):
                v[free_mask] += accel * sub_dt
                v[free_mask] *= damping_factor
            x_prev = np.array(x, dtype=np.float64, copy=True)
            x += v * sub_dt
            if roots_pinned:
                x[root_indices] = root_targets
                v[root_indices] = 0.0

            stretch_lambdas = np.zeros((stretch_i.size,), dtype=np.float64)
            bend_lambdas = np.zeros((bend_i.size,), dtype=np.float64)
            for _iteration in range(int(cfg.iterations)):
                _solve_distance_constraints(
                    x,
                    inv_mass,
                    stretch_i,
                    stretch_j,
                    stretch_rest,
                    stretch_lambdas,
                    compliance=cfg.stretch_compliance,
                    stiffness=cfg.stretch_stiffness,
                    dt=sub_dt,
                )
                _solve_distance_constraints(
                    x,
                    inv_mass,
                    bend_i,
                    bend_j,
                    bend_rest,
                    bend_lambdas,
                    compliance=cfg.bend_compliance,
                    stiffness=cfg.bend_stiffness,
                    dt=sub_dt,
                )
                collision_count += project_points_from_mesh_collider(x, inv_mass, collider)
            if int(cfg.iterations) <= 0:
                collision_count += project_points_from_mesh_collider(x, inv_mass, collider)
            if roots_pinned:
                x[root_indices] = root_targets
            v = (x - x_prev) / sub_dt
            if roots_pinned:
                v[root_indices] = 0.0
            if cfg.max_velocity > 0.0:
                speeds = np.linalg.norm(v, axis=1)
                mask = speeds > cfg.max_velocity
                if np.any(mask):
                    scale = cfg.max_velocity / np.maximum(speeds[mask], 1.0e-12)
                    v[mask] *= scale.reshape((-1, 1))
        if cfg.store_frames:
            frames.append(_export_curves(x, spans))

    out_curves = _export_curves(x, spans)
    line_points = curves_to_line_points(out_curves)
    debug = {
        "input_curve_count": int(len(curves or [])),
        "simulated_curve_count": int(sum(1 for _start, count in spans if int(count) > 0)),
        "point_count": int(x.shape[0]),
        "root_count": int(root_indices.size),
        "roots_pinned": bool(roots_pinned),
        "invalid_points": int(invalid_points),
        "stretch_constraints": int(stretch_i.size),
        "bend_constraints": int(bend_i.size),
        "line_point_count": int(len(line_points)),
        "frame_count": int(cfg.frame_count),
        "stored_frame_count": int(len(frames)),
        "fps": float(cfg.fps),
        "substeps": int(cfg.substeps),
        "iterations": int(cfg.iterations),
        "stretch_stiffness": float(cfg.stretch_stiffness),
        "bend_stiffness": float(cfg.bend_stiffness),
        "damping": float(cfg.damping),
        "gravity": [float(vv) for vv in cfg.gravity],
        "wind": [float(vv) for vv in cfg.wind],
        "collider_enabled": bool(isinstance(collider, dict)),
        "collision_projection_count": int(collision_count),
    }
    return XPBDStrandResult(curves=out_curves, line_points=line_points, frames=frames, debug=debug)


__all__ = [
    "XPBDStrandConfig",
    "XPBDStrandError",
    "XPBDStrandResult",
    "build_strand_runtime",
    "build_mesh_collider",
    "curves_to_line_points",
    "reset_strand_runtime",
    "project_points_from_mesh_collider",
    "root_indices_for_curves",
    "runtime_line_points",
    "runtime_root_points",
    "simulate_strands",
    "step_strand_runtime",
]
