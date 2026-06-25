from __future__ import annotations

import json
import math
from typing import Any, Sequence

try:
    import numpy as np
except Exception:
    np = None


DEFAULT_RADIUS_PROFILE = [(0.0, 1.0), (1.0, 1.0)]


def normalize_radius_profile(points: Any) -> list[tuple[float, float]]:
    if isinstance(points, str):
        try:
            points = json.loads(points)
        except Exception:
            points = None
    rows: list[tuple[float, float]] = []
    for entry in points or []:
        try:
            x = max(0.0, min(1.0, float(entry[0])))
            y = max(0.0, min(4.0, float(entry[1])))
        except Exception:
            continue
        rows.append((x, y))
    if not rows:
        rows = list(DEFAULT_RADIUS_PROFILE)
    rows.sort(key=lambda item: item[0])
    if rows[0][0] > 0.0:
        rows.insert(0, (0.0, rows[0][1]))
    else:
        rows[0] = (0.0, rows[0][1])
    if rows[-1][0] < 1.0:
        rows.append((1.0, rows[-1][1]))
    else:
        rows[-1] = (1.0, rows[-1][1])
    deduped: list[tuple[float, float]] = []
    for x, y in rows:
        if deduped and abs(deduped[-1][0] - x) <= 1.0e-6:
            deduped[-1] = (x, y)
        else:
            deduped.append((x, y))
    return deduped


def radius_profile_to_json(points: Any) -> str:
    rows = [[round(float(x), 6), round(float(y), 6)] for x, y in normalize_radius_profile(points)]
    return json.dumps(rows, separators=(",", ":"))


def sample_radius_profile(points: Any, u: float) -> float:
    rows = normalize_radius_profile(points)
    x = max(0.0, min(1.0, float(u)))
    for idx in range(len(rows) - 1):
        x0, y0 = rows[idx]
        x1, y1 = rows[idx + 1]
        if x <= x1 or idx == len(rows) - 2:
            span = max(1.0e-8, x1 - x0)
            t = max(0.0, min(1.0, (x - x0) / span))
            return float(y0 + ((y1 - y0) * t))
    return float(rows[-1][1])


def flatten_curves(curves: Sequence[Sequence[Sequence[float]]]) -> tuple[Any, list[tuple[int, int]]]:
    rows: list[list[float]] = []
    spans: list[tuple[int, int]] = []
    for curve in curves or []:
        start = len(rows)
        if isinstance(curve, (list, tuple)):
            for point in curve:
                try:
                    seq = list(point)
                    if len(seq) >= 3:
                        rows.append([float(seq[0]), float(seq[1]), float(seq[2])])
                except Exception:
                    continue
        count = len(rows) - start
        spans.append((start, count))
    if np is None:
        return rows, spans
    try:
        return np.asarray(rows, dtype="f4").reshape(-1, 3), spans
    except Exception:
        return np.zeros((0, 3), dtype="f4"), spans


def line_points_to_curve_points(line_points: Any, template_curves: Sequence[Sequence[Sequence[float]]]) -> Any:
    if np is None:
        return None
    try:
        lines = np.asarray(line_points, dtype="f4").reshape(-1, 3)
    except Exception:
        return None
    if lines.size == 0:
        return None
    rows = []
    cursor = 0
    for curve in template_curves or []:
        count = len(curve) if isinstance(curve, (list, tuple)) else 0
        if count <= 0:
            continue
        if count == 1:
            try:
                rows.append(np.asarray(curve[0], dtype="f4").reshape(3))
            except Exception:
                pass
            continue
        segment_count = count - 1
        need = segment_count * 2
        if cursor + need > int(lines.shape[0]):
            return None
        chunk = lines[cursor : cursor + need].reshape(segment_count, 2, 3)
        rows.append(chunk[0, 0])
        rows.extend(chunk[:, 1])
        cursor += need
    if not rows:
        return None
    try:
        return np.asarray(rows, dtype="f4").reshape(-1, 3)
    except Exception:
        return None


def _safe_normalize(vec: Any, fallback: Any) -> Any:
    if np is None:
        return fallback
    arr = np.asarray(vec, dtype="f4").reshape(3)
    length = float(np.linalg.norm(arr))
    if length <= 1.0e-8:
        return np.asarray(fallback, dtype="f4").reshape(3)
    return (arr / length).astype("f4", copy=False)


def _initial_normal(tangent: Any) -> Any:
    if np is None:
        return None
    t = _safe_normalize(tangent, np.asarray([0.0, 0.0, 1.0], dtype="f4"))
    ref = np.asarray([0.0, 1.0, 0.0], dtype="f4")
    if abs(float(np.dot(t, ref))) > 0.92:
        ref = np.asarray([1.0, 0.0, 0.0], dtype="f4")
    return _safe_normalize(np.cross(ref, t), np.asarray([1.0, 0.0, 0.0], dtype="f4"))


def _transport_normal(prev_normal: Any, tangent: Any) -> Any:
    if np is None:
        return None
    t = _safe_normalize(tangent, np.asarray([0.0, 0.0, 1.0], dtype="f4"))
    n = np.asarray(prev_normal, dtype="f4").reshape(3)
    n = n - t * float(np.dot(n, t))
    if float(np.linalg.norm(n)) <= 1.0e-6:
        return _initial_normal(t)
    return _safe_normalize(n, _initial_normal(t))


def build_tube_topology(
    curves: Sequence[Sequence[Sequence[float]]],
    *,
    root_radius: float = 0.006,
    tip_radius: float = 0.0015,
    radius_profile: Any = None,
    sides: int = 8,
    segment_subdivisions: int = 1,
    cap_root: bool = True,
    cap_tip: bool = True,
) -> dict[str, Any]:
    if np is None:
        return {"ok": False, "error": "NumPy is unavailable."}
    rest_points, spans = flatten_curves(curves)
    rest_points = np.asarray(rest_points, dtype="f4").reshape(-1, 3)
    sides = max(3, min(64, int(sides)))
    subdivisions = max(1, min(16, int(segment_subdivisions)))
    root_radius = max(0.0, float(root_radius))
    tip_radius = max(0.0, float(tip_radius))
    profile = normalize_radius_profile(radius_profile)

    p0_indices: list[int] = []
    p1_indices: list[int] = []
    segment_t: list[float] = []
    radii: list[float] = []
    angles: list[float] = []
    segment_ids: list[int] = []
    normal_modes: list[int] = []
    uvs: list[tuple[float, float]] = []
    indices: list[int] = []
    segment_p0: list[int] = []
    segment_p1: list[int] = []
    segment_curve: list[int] = []
    segment_local: list[int] = []
    rings_by_curve: list[list[int]] = []

    def radius_at(u_value: float) -> float:
        base = root_radius + ((tip_radius - root_radius) * max(0.0, min(1.0, float(u_value))))
        return max(0.0, float(base) * sample_radius_profile(profile, u_value))

    for curve_index, (start, count) in enumerate(spans):
        if count < 2:
            rings_by_curve.append([])
            continue
        pts = rest_points[start : start + count]
        seg_vec = pts[1:] - pts[:-1]
        seg_len = np.linalg.norm(seg_vec, axis=1).astype("f4")
        total_len = float(np.sum(seg_len))
        if total_len <= 1.0e-8:
            rings_by_curve.append([])
            continue
        cum_len = np.concatenate((np.zeros((1,), dtype="f4"), np.cumsum(seg_len, dtype="f4")))
        curve_segment_ids: list[int] = []
        for local_idx in range(count - 1):
            sid = len(segment_p0)
            segment_p0.append(start + local_idx)
            segment_p1.append(start + local_idx + 1)
            segment_curve.append(curve_index)
            segment_local.append(local_idx)
            curve_segment_ids.append(sid)

        ring_bases: list[int] = []

        def add_ring(local_segment: int, local_t: float) -> int:
            local_segment = max(0, min(count - 2, int(local_segment)))
            local_t = max(0.0, min(1.0, float(local_t)))
            sid = curve_segment_ids[local_segment]
            u_value = float((cum_len[local_segment] + (seg_len[local_segment] * local_t)) / total_len)
            radius = radius_at(u_value)
            base = len(p0_indices)
            for side in range(sides):
                angle = (math.tau * float(side)) / float(sides)
                p0_indices.append(start + local_segment)
                p1_indices.append(start + local_segment + 1)
                segment_t.append(local_t)
                radii.append(radius)
                angles.append(angle)
                segment_ids.append(sid)
                normal_modes.append(0)
                uvs.append((u_value, float(side) / float(sides)))
            ring_bases.append(base)
            return base

        add_ring(0, 0.0)
        for local_idx in range(count - 1):
            for sub_idx in range(1, subdivisions + 1):
                add_ring(local_idx, float(sub_idx) / float(subdivisions))

        for ring_idx in range(len(ring_bases) - 1):
            a0 = ring_bases[ring_idx]
            b0 = ring_bases[ring_idx + 1]
            for side in range(sides):
                a = a0 + side
                b = a0 + ((side + 1) % sides)
                c = b0 + side
                d = b0 + ((side + 1) % sides)
                indices.extend([a, c, b, b, c, d])

        if cap_root and ring_bases:
            center = len(p0_indices)
            sid = curve_segment_ids[0]
            p0_indices.append(start)
            p1_indices.append(start + 1)
            segment_t.append(0.0)
            radii.append(0.0)
            angles.append(0.0)
            segment_ids.append(sid)
            normal_modes.append(1)
            uvs.append((0.0, 0.5))
            root_base = ring_bases[0]
            for side in range(sides):
                indices.extend([center, root_base + ((side + 1) % sides), root_base + side])

        if cap_tip and ring_bases:
            center = len(p0_indices)
            sid = curve_segment_ids[-1]
            p0_indices.append(start + count - 2)
            p1_indices.append(start + count - 1)
            segment_t.append(1.0)
            radii.append(0.0)
            angles.append(0.0)
            segment_ids.append(sid)
            normal_modes.append(2)
            uvs.append((1.0, 0.5))
            tip_base = ring_bases[-1]
            for side in range(sides):
                indices.extend([center, tip_base + side, tip_base + ((side + 1) % sides)])

        rings_by_curve.append(ring_bases)

    if not p0_indices or not indices:
        return {
            "ok": False,
            "error": "No valid tube segments.",
            "rest_points": rest_points,
            "spans": spans,
        }

    topology = {
        "ok": True,
        "rest_points": rest_points,
        "spans": spans,
        "p0_indices": np.asarray(p0_indices, dtype=np.int64),
        "p1_indices": np.asarray(p1_indices, dtype=np.int64),
        "segment_t": np.asarray(segment_t, dtype="f4"),
        "radii": np.asarray(radii, dtype="f4"),
        "angles": np.asarray(angles, dtype="f4"),
        "segment_ids": np.asarray(segment_ids, dtype=np.int64),
        "normal_modes": np.asarray(normal_modes, dtype=np.int32),
        "uvs": np.asarray(uvs, dtype="f4").reshape(-1, 2),
        "indices": np.asarray(indices, dtype=np.uint32),
        "segment_p0_indices": np.asarray(segment_p0, dtype=np.int64),
        "segment_p1_indices": np.asarray(segment_p1, dtype=np.int64),
        "segment_curve_indices": np.asarray(segment_curve, dtype=np.int32),
        "segment_local_indices": np.asarray(segment_local, dtype=np.int32),
        "rings_by_curve": rings_by_curve,
        "sides": int(sides),
        "segment_subdivisions": int(subdivisions),
        "vertex_count": int(len(p0_indices)),
        "triangle_count": int(len(indices) // 3),
        "point_count": int(rest_points.shape[0]),
        "curve_count": int(sum(1 for _start, count in spans if count >= 2)),
    }
    points, normals = deform_tube_topology(topology, rest_points)
    topology["points"] = points
    topology["normals"] = normals
    return topology


def deform_tube_topology(topology: dict[str, Any], current_points: Any = None) -> tuple[Any, Any]:
    if np is None or not isinstance(topology, dict) or not bool(topology.get("ok", False)):
        return None, None
    rest_points = np.asarray(topology.get("rest_points"), dtype="f4").reshape(-1, 3)
    if current_points is None:
        points = rest_points
    else:
        try:
            points = np.asarray(current_points, dtype="f4").reshape(-1, 3)
        except Exception:
            points = rest_points
        if int(points.shape[0]) != int(rest_points.shape[0]):
            points = rest_points

    seg_p0_idx = np.asarray(topology.get("segment_p0_indices"), dtype=np.int64).reshape(-1)
    seg_p1_idx = np.asarray(topology.get("segment_p1_indices"), dtype=np.int64).reshape(-1)
    seg_count = int(seg_p0_idx.size)
    if seg_count <= 0:
        return None, None
    seg_p0 = points[seg_p0_idx]
    seg_p1 = points[seg_p1_idx]
    tangents = seg_p1 - seg_p0
    tangent_len = np.linalg.norm(tangents, axis=1).reshape(-1, 1)
    tangents = np.divide(
        tangents,
        np.maximum(tangent_len, 1.0e-8),
        out=np.zeros_like(tangents, dtype="f4"),
        where=tangent_len > 1.0e-8,
    ).astype("f4", copy=False)

    normals_by_segment = np.zeros((seg_count, 3), dtype="f4")
    binormals_by_segment = np.zeros((seg_count, 3), dtype="f4")
    segment_curves = np.asarray(topology.get("segment_curve_indices"), dtype=np.int32).reshape(-1)
    prev_curve = None
    prev_normal = None
    for sid in range(seg_count):
        curve_id = int(segment_curves[sid]) if sid < int(segment_curves.size) else -1
        if prev_curve != curve_id or prev_normal is None:
            normal = _initial_normal(tangents[sid])
        else:
            normal = _transport_normal(prev_normal, tangents[sid])
        tangent = _safe_normalize(tangents[sid], np.asarray([0.0, 0.0, 1.0], dtype="f4"))
        binormal = _safe_normalize(np.cross(tangent, normal), np.asarray([0.0, 1.0, 0.0], dtype="f4"))
        normal = _safe_normalize(np.cross(binormal, tangent), normal)
        normals_by_segment[sid] = normal
        binormals_by_segment[sid] = binormal
        prev_curve = curve_id
        prev_normal = normal

    p0 = points[np.asarray(topology.get("p0_indices"), dtype=np.int64).reshape(-1)]
    p1 = points[np.asarray(topology.get("p1_indices"), dtype=np.int64).reshape(-1)]
    t = np.asarray(topology.get("segment_t"), dtype="f4").reshape(-1, 1)
    centers = p0 + ((p1 - p0) * t)
    seg_ids = np.asarray(topology.get("segment_ids"), dtype=np.int64).reshape(-1)
    seg_ids = np.clip(seg_ids, 0, max(0, seg_count - 1))
    angles = np.asarray(topology.get("angles"), dtype="f4").reshape(-1)
    radii = np.asarray(topology.get("radii"), dtype="f4").reshape(-1, 1)
    radial = (
        normals_by_segment[seg_ids] * np.cos(angles).reshape(-1, 1)
        + binormals_by_segment[seg_ids] * np.sin(angles).reshape(-1, 1)
    ).astype("f4", copy=False)
    out_points = (centers + (radial * radii)).astype("f4", copy=False)
    out_normals = radial.astype("f4", copy=True)
    modes = np.asarray(topology.get("normal_modes"), dtype=np.int32).reshape(-1)
    if modes.size == out_normals.shape[0]:
        root_mask = modes == 1
        tip_mask = modes == 2
        if bool(np.any(root_mask)):
            out_normals[root_mask] = -tangents[seg_ids[root_mask]]
        if bool(np.any(tip_mask)):
            out_normals[tip_mask] = tangents[seg_ids[tip_mask]]
    normal_len = np.linalg.norm(out_normals, axis=1).reshape(-1, 1)
    out_normals = np.divide(
        out_normals,
        np.maximum(normal_len, 1.0e-8),
        out=np.zeros_like(out_normals, dtype="f4"),
        where=normal_len > 1.0e-8,
    ).astype("f4", copy=False)
    return out_points, out_normals


__all__ = [
    "DEFAULT_RADIUS_PROFILE",
    "build_tube_topology",
    "deform_tube_topology",
    "flatten_curves",
    "line_points_to_curve_points",
    "normalize_radius_profile",
    "radius_profile_to_json",
    "sample_radius_profile",
]
