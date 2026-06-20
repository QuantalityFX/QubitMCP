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
    }
    return XPBDStrandResult(curves=out_curves, line_points=line_points, frames=frames, debug=debug)


__all__ = [
    "XPBDStrandConfig",
    "XPBDStrandError",
    "XPBDStrandResult",
    "build_strand_runtime",
    "curves_to_line_points",
    "reset_strand_runtime",
    "root_indices_for_curves",
    "runtime_line_points",
    "runtime_root_points",
    "simulate_strands",
    "step_strand_runtime",
]
