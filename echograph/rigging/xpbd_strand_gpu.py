"""ModernGL compute backend for XPBD groom guide strands."""

from __future__ import annotations

import math
from typing import Any, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover - handled at runtime
    np = None  # type: ignore

from .xpbd_strand import XPBDStrandConfig, XPBDStrandError, build_strand_runtime


_LOCAL_SIZE = 128
_GPU_COLLIDER_MAX_SAMPLES = 16384


_INTEGRATE_SHADER = """
#version 430
layout(local_size_x = 128) in;

layout(std430, binding = 0) buffer PositionsBlock { vec4 Pos[]; };
layout(std430, binding = 1) buffer PreviousBlock { vec4 Prev[]; };
layout(std430, binding = 2) buffer VelocitiesBlock { vec4 Vel[]; };

uniform int PointCount;
uniform float Dt;
uniform vec3 Accel;
uniform float Damping;

void main() {
    uint id = gl_GlobalInvocationID.x;
    if (id >= uint(PointCount)) {
        return;
    }
    vec4 p4 = Pos[id];
    vec3 p = p4.xyz;
    vec3 v = Vel[id].xyz;
    Prev[id] = vec4(p, 0.0);
    if (p4.w > 0.0) {
        v += Accel * Dt;
        v *= Damping;
        p += v * Dt;
    } else {
        v = vec3(0.0);
    }
    Pos[id] = vec4(p, p4.w);
    Vel[id] = vec4(v, 0.0);
}
"""


_PIN_SHADER = """
#version 430
layout(local_size_x = 128) in;

layout(std430, binding = 0) buffer PositionsBlock { vec4 Pos[]; };
layout(std430, binding = 2) buffer VelocitiesBlock { vec4 Vel[]; };
layout(std430, binding = 3) buffer RootsBlock { vec4 Roots[]; };
layout(std430, binding = 4) buffer RootTargetsBlock { vec4 RootTargets[]; };

uniform int RootCount;

void main() {
    uint id = gl_GlobalInvocationID.x;
    if (id >= uint(RootCount)) {
        return;
    }
    int point_id = int(Roots[id].x + 0.5);
    vec3 target = RootTargets[id].xyz;
    float inv_mass = Pos[point_id].w;
    Pos[point_id] = vec4(target, inv_mass);
    Vel[point_id] = vec4(0.0);
}
"""


_SOLVE_DISTANCE_SHADER = """
#version 430
layout(local_size_x = 128) in;

layout(std430, binding = 0) buffer PositionsBlock { vec4 Pos[]; };
layout(std430, binding = 5) buffer ConstraintsBlock { vec4 Constraints[]; };

uniform int ConstraintCount;
uniform float Compliance;
uniform float Stiffness;
uniform float Dt;

void main() {
    uint id = gl_GlobalInvocationID.x;
    if (id >= uint(ConstraintCount) || Stiffness <= 0.0) {
        return;
    }
    vec4 row = Constraints[id];
    int i = int(row.x + 0.5);
    int j = int(row.y + 0.5);
    float rest_length = row.z;
    vec4 pi4 = Pos[i];
    vec4 pj4 = Pos[j];
    float wi = pi4.w;
    float wj = pj4.w;
    float wsum = wi + wj;
    if (wsum <= 0.0) {
        return;
    }
    vec3 delta = pj4.xyz - pi4.xyz;
    float length_now = length(delta);
    if (length_now <= 1.0e-8) {
        return;
    }
    float alpha = Compliance / max(Dt * Dt, 1.0e-12);
    float denom = wsum + alpha;
    if (denom <= 1.0e-8) {
        return;
    }
    vec3 grad = delta / length_now;
    float c = length_now - rest_length;
    float dlambda = -(c / denom) * Stiffness;
    if (wi > 0.0) {
        pi4.xyz -= wi * dlambda * grad;
    }
    if (wj > 0.0) {
        pj4.xyz += wj * dlambda * grad;
    }
    Pos[i] = pi4;
    Pos[j] = pj4;
}
"""


_SOLVE_MESH_COLLISION_SHADER = """
#version 430
layout(local_size_x = 128) in;

layout(std430, binding = 0) buffer PositionsBlock { vec4 Pos[]; };
layout(std430, binding = 5) readonly buffer ColliderVerticesBlock { vec4 ColliderVertices[]; };
layout(std430, binding = 6) readonly buffer ColliderNormalsBlock { vec4 ColliderNormals[]; };

uniform int PointCount;
uniform int ColliderCount;
uniform float CollisionMargin;

void main() {
    uint id = gl_GlobalInvocationID.x;
    if (id >= uint(PointCount) || ColliderCount <= 0) {
        return;
    }
    vec4 p4 = Pos[id];
    if (p4.w <= 0.0) {
        return;
    }

    vec3 p = p4.xyz;
    float best_distance_sq = 3.402823e+38;
    vec3 surface_point = vec3(0.0);
    vec3 surface_normal = vec3(0.0);
    for (int collider_id = 0; collider_id < ColliderCount; ++collider_id) {
        vec3 candidate = ColliderVertices[collider_id].xyz;
        vec3 delta = p - candidate;
        float distance_sq = dot(delta, delta);
        if (distance_sq < best_distance_sq) {
            best_distance_sq = distance_sq;
            surface_point = candidate;
            surface_normal = ColliderNormals[collider_id].xyz;
        }
    }
    float normal_length = length(surface_normal);
    if (normal_length <= 1.0e-8) {
        return;
    }
    surface_normal /= normal_length;
    float signed_gap = dot(p - surface_point, surface_normal);
    if (signed_gap < CollisionMargin) {
        p += (CollisionMargin - signed_gap) * surface_normal;
        Pos[id] = vec4(p, p4.w);
    }
}
"""


_FINALIZE_SHADER = """
#version 430
layout(local_size_x = 128) in;

layout(std430, binding = 0) buffer PositionsBlock { vec4 Pos[]; };
layout(std430, binding = 1) buffer PreviousBlock { vec4 Prev[]; };
layout(std430, binding = 2) buffer VelocitiesBlock { vec4 Vel[]; };

uniform int PointCount;
uniform float Dt;
uniform float MaxVelocity;

void main() {
    uint id = gl_GlobalInvocationID.x;
    if (id >= uint(PointCount)) {
        return;
    }
    vec4 p4 = Pos[id];
    vec3 v = vec3(0.0);
    if (p4.w > 0.0) {
        v = (p4.xyz - Prev[id].xyz) / max(Dt, 1.0e-12);
        float speed = length(v);
        if (MaxVelocity > 0.0 && speed > MaxVelocity) {
            v *= MaxVelocity / max(speed, 1.0e-12);
        }
    }
    Vel[id] = vec4(v, 0.0);
}
"""


_BUILD_SEGMENTS_SHADER = """
#version 430
layout(local_size_x = 128) in;

layout(std430, binding = 0) buffer PositionsBlock { vec4 Pos[]; };
layout(std430, binding = 6) buffer SegmentIndicesBlock { vec4 SegmentIndices[]; };
layout(std430, binding = 7) buffer SegmentDataBlock { float SegmentData[]; };

uniform int SegmentCount;

void main() {
    uint id = gl_GlobalInvocationID.x;
    if (id >= uint(SegmentCount)) {
        return;
    }
    vec4 row = SegmentIndices[id];
    int i = int(row.x + 0.5);
    int j = int(row.y + 0.5);
    vec3 a = Pos[i].xyz;
    vec3 b = Pos[j].xyz;
    uint base = id * 6u;
    SegmentData[base + 0u] = a.x;
    SegmentData[base + 1u] = a.y;
    SegmentData[base + 2u] = a.z;
    SegmentData[base + 3u] = b.x;
    SegmentData[base + 4u] = b.y;
    SegmentData[base + 5u] = b.z;
}
"""


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        out = float(value)
    except Exception:
        out = float(default)
    if not math.isfinite(out):
        out = float(default)
    return max(float(low), min(float(high), out))


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        out = int(round(float(value)))
    except Exception:
        out = int(default)
    return max(int(low), min(int(high), out))


def _groups(count: int) -> int:
    return max(1, int((max(0, int(count)) + _LOCAL_SIZE - 1) // _LOCAL_SIZE))


def _as_vec4_positions(points: Any, inv_mass: Any) -> Any:
    pts = np.asarray(points, dtype="f4").reshape(-1, 3)
    masses = np.asarray(inv_mass, dtype="f4").reshape(-1)
    out = np.zeros((int(pts.shape[0]), 4), dtype="f4")
    out[:, :3] = pts
    if masses.shape[0] == pts.shape[0]:
        out[:, 3] = masses
    else:
        out[:, 3] = 1.0
    return out


def _indexed_vec4(indices: Any) -> Any:
    idx = np.asarray(indices, dtype="f4").reshape(-1)
    out = np.zeros((int(idx.shape[0]), 4), dtype="f4")
    if idx.size:
        out[:, 0] = idx
    return out


def _segment_index_vec4(indices: Any) -> Any:
    idx = np.asarray(indices, dtype=np.int64).reshape(-1)
    if idx.size % 2 != 0:
        idx = idx[: idx.size - 1]
    pairs = idx.reshape(-1, 2) if idx.size else np.zeros((0, 2), dtype=np.int64)
    out = np.zeros((int(pairs.shape[0]), 4), dtype="f4")
    if pairs.size:
        out[:, 0:2] = pairs.astype("f4", copy=False)
    return out


def _constraint_groups(rest_points: Any, spans: Sequence[tuple[int, int]], *, distance: int, modulo: int) -> list[Any]:
    rest = np.asarray(rest_points, dtype="f4").reshape(-1, 3)
    rows: list[list[tuple[float, float, float, float]]] = [[] for _ in range(int(modulo))]
    eps = 1.0e-8
    for start, count in spans:
        start_i = int(start)
        count_i = int(count)
        for local in range(max(0, count_i - int(distance))):
            i = start_i + local
            j = i + int(distance)
            delta = rest[j] - rest[i]
            length = float(np.linalg.norm(delta))
            if length <= eps:
                continue
            rows[int(local) % int(modulo)].append((float(i), float(j), float(length), 0.0))
    return [
        np.asarray(group, dtype="f4").reshape((-1, 4)) if group else np.zeros((0, 4), dtype="f4")
        for group in rows
    ]


class XPBDStrandGPUBackend:
    """OpenGL 4.3 compute implementation for realtime groom guide XPBD."""

    def __init__(self, ctx: Any) -> None:
        if np is None:
            raise XPBDStrandError("NumPy is required for GPU strand simulation.")
        if ctx is None or not hasattr(ctx, "compute_shader"):
            raise XPBDStrandError("OpenGL compute shaders are not available.")
        try:
            version_code = int(getattr(ctx, "version_code", 0) or 0)
        except Exception:
            version_code = 0
        if version_code and version_code < 430:
            raise XPBDStrandError("OpenGL 4.3 or newer is required for GPU strand simulation.")
        self._ctx = ctx
        self._integrate = self._compile_compute("integrate", _INTEGRATE_SHADER)
        self._pin = self._compile_compute("pin_roots", _PIN_SHADER)
        self._solve = self._compile_compute("solve_distance", _SOLVE_DISTANCE_SHADER)
        self._solve_mesh = self._compile_compute("solve_mesh_collision", _SOLVE_MESH_COLLISION_SHADER)
        self._finalize = self._compile_compute("finalize", _FINALIZE_SHADER)
        self._build_segments = self._compile_compute("build_segments", _BUILD_SEGMENTS_SHADER)

    def _compile_compute(self, stage: str, source: str) -> Any:
        try:
            return self._ctx.compute_shader(source)
        except Exception as exc:
            raise XPBDStrandError(f"GPU strand {stage} compute shader failed: {exc!r}") from exc

    def build_runtime(
        self,
        curves: Sequence[Sequence[Sequence[float]]],
        config: XPBDStrandConfig,
        *,
        initial_points: Any = None,
        root_targets: Any = None,
    ) -> dict[str, Any]:
        cpu_runtime = build_strand_runtime(curves, config, initial_points=initial_points)
        point_count = int(cpu_runtime.get("point_count", 0) or 0)
        segment_count = int(cpu_runtime.get("segment_count", 0) or 0)
        if point_count <= 0 or segment_count <= 0:
            raise XPBDStrandError("GPU strand runtime needs at least one non-empty segment.")

        points4 = _as_vec4_positions(cpu_runtime.get("points"), cpu_runtime.get("inv_mass"))
        zeros4 = np.zeros_like(points4, dtype="f4")
        root_indices = np.asarray(cpu_runtime.get("root_indices"), dtype=np.int64).reshape(-1)
        roots4 = _indexed_vec4(root_indices)
        root_target_arr = self._root_targets(cpu_runtime, root_targets)
        root_targets4 = np.zeros((int(root_target_arr.shape[0]), 4), dtype="f4")
        if root_target_arr.size:
            root_targets4[:, :3] = root_target_arr.astype("f4", copy=False)

        segment_indices4 = _segment_index_vec4(cpu_runtime.get("line_point_indices"))
        spans = list((int(start), int(count)) for start, count in (cpu_runtime.get("spans") or []))
        stretch_groups = _constraint_groups(cpu_runtime.get("rest_points"), spans, distance=1, modulo=2)
        bend_groups = _constraint_groups(cpu_runtime.get("rest_points"), spans, distance=2, modulo=3)

        runtime = {
            "schema": "qubit.xpbd_strand.gpu_runtime.v1",
            "device": "gpu",
            "point_count": point_count,
            "segment_count": int(segment_indices4.shape[0]),
            "root_count": int(root_indices.size),
            "root_indices": root_indices.astype(np.int64, copy=False),
            "line_point_indices": np.asarray(cpu_runtime.get("line_point_indices"), dtype=np.int64).reshape(-1),
            "spans": spans,
            "pos_buffer": self._ctx.buffer(points4.tobytes()),
            "prev_buffer": self._ctx.buffer(zeros4.tobytes()),
            "vel_buffer": self._ctx.buffer(zeros4.tobytes()),
            "root_buffer": self._ctx.buffer(roots4.tobytes()) if roots4.size else None,
            "root_target_buffer": self._ctx.buffer(root_targets4.tobytes()) if root_targets4.size else None,
            "segment_index_buffer": self._ctx.buffer(segment_indices4.tobytes()),
            "segment_buffer": self._buffer_reserve(max(1, int(segment_indices4.shape[0]) * 6 * 4)),
            "stretch_groups": self._make_constraint_buffers(stretch_groups),
            "bend_groups": self._make_constraint_buffers(bend_groups),
            "root_targets_cpu": root_target_arr.astype("f4", copy=True),
            "roots_pinned": bool(float(getattr(config, "root_pin_stiffness", 1.0) or 0.0) > 0.0 and root_indices.size > 0),
        }
        if bool(runtime.get("roots_pinned", False)):
            self._pin_roots(runtime)
        self._build_segment_buffer(runtime)
        return runtime

    def reset_runtime(self, runtime: dict[str, Any], *, points: Any = None, root_targets: Any = None) -> None:
        point_count = int(runtime.get("point_count", 0) or 0)
        if point_count <= 0:
            return
        if points is not None:
            try:
                pts = np.asarray(points, dtype="f4").reshape(-1, 3)
            except Exception:
                pts = None
            if pts is not None and int(pts.shape[0]) == point_count:
                current = self.read_points(runtime)
                inv_mass = current[:, 3] if current.ndim == 2 and current.shape[1] >= 4 else np.ones((point_count,), dtype="f4")
                runtime["pos_buffer"].write(_as_vec4_positions(pts, inv_mass).tobytes())
                zero = np.zeros((point_count, 4), dtype="f4")
                runtime["prev_buffer"].write(zero.tobytes())
                runtime["vel_buffer"].write(zero.tobytes())
        self._write_root_targets(runtime, root_targets)
        if bool(runtime.get("roots_pinned", False)):
            self._pin_roots(runtime)
        self._build_segment_buffer(runtime)

    def step_runtime(
        self,
        runtime: dict[str, Any],
        config: XPBDStrandConfig,
        *,
        root_targets: Any = None,
        steps: int = 1,
        dt: float | None = None,
        collider: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._write_root_targets(runtime, root_targets)
        collider_enabled = self.update_mesh_collider(runtime, collider)

        point_count = int(runtime.get("point_count", 0) or 0)
        root_count = int(runtime.get("root_count", 0) or 0)
        roots_pinned = bool(float(getattr(config, "root_pin_stiffness", 1.0) or 0.0) > 0.0 and root_count > 0)
        runtime["roots_pinned"] = roots_pinned
        segment_count = int(runtime.get("segment_count", 0) or 0)
        step_count = _clamp_int(steps, 1, 0, 240)
        fps = _clamp_float(getattr(config, "fps", 24.0), 24.0, 1.0e-3, 1000.0)
        substeps = _clamp_int(getattr(config, "substeps", 4), 4, 1, 128)
        iterations = _clamp_int(getattr(config, "iterations", 8), 8, 0, 256)
        frame_dt = float(dt) if dt is not None and math.isfinite(float(dt)) and float(dt) > 0.0 else 1.0 / fps
        sub_dt = frame_dt / max(1, substeps)
        damping = _clamp_float(getattr(config, "damping", 0.04), 0.04, 0.0, 0.999)
        damping_factor = max(0.0, min(1.0, 1.0 - damping)) ** (1.0 / max(1, substeps))
        gravity = tuple(float(v) for v in tuple(getattr(config, "gravity", (0.0, -9.81, 0.0)))[:3])
        wind = tuple(float(v) for v in tuple(getattr(config, "wind", (0.0, 0.0, 0.0)))[:3])
        accel = (
            float(gravity[0]) + float(wind[0]),
            float(gravity[1]) + float(wind[1]),
            float(gravity[2]) + float(wind[2]),
        )
        stretch_stiffness = _clamp_float(getattr(config, "stretch_stiffness", 1.0), 1.0, 0.0, 1.0)
        bend_stiffness = _clamp_float(getattr(config, "bend_stiffness", 0.35), 0.35, 0.0, 1.0)
        max_velocity = _clamp_float(getattr(config, "max_velocity", 250.0), 250.0, 0.0, 1.0e6)

        for _frame in range(step_count):
            for _substep in range(substeps):
                self._bind_point_buffers(runtime)
                self._set(self._integrate, "PointCount", point_count)
                self._set(self._integrate, "Dt", float(sub_dt))
                self._set(self._integrate, "Accel", accel)
                self._set(self._integrate, "Damping", float(damping_factor))
                self._run(self._integrate, point_count)
                self._barrier()
                if roots_pinned:
                    self._pin_roots(runtime)
                for _iteration in range(iterations):
                    self._solve_groups(runtime, runtime.get("stretch_groups") or [], sub_dt, stretch_stiffness, getattr(config, "stretch_compliance", 0.0))
                    self._solve_groups(runtime, runtime.get("bend_groups") or [], sub_dt, bend_stiffness, getattr(config, "bend_compliance", 0.0005))
                    if roots_pinned:
                        self._pin_roots(runtime)
                if collider_enabled:
                    self._apply_mesh_collision(runtime)
                    if roots_pinned:
                        self._pin_roots(runtime)
                self._bind_point_buffers(runtime)
                self._set(self._finalize, "PointCount", point_count)
                self._set(self._finalize, "Dt", float(sub_dt))
                self._set(self._finalize, "MaxVelocity", float(max_velocity))
                self._run(self._finalize, point_count)
                self._barrier()
        if roots_pinned:
            self._pin_roots(runtime)
        self._build_segment_buffer(runtime)
        return {
            "segment_buffer": runtime.get("segment_buffer"),
            "segment_count": int(segment_count),
            "root_points": np.asarray(runtime.get("root_targets_cpu"), dtype="f4").reshape(-1, 3),
            "debug": {
                "device": "gpu",
                "backend": "moderngl_compute",
                "point_count": int(point_count),
                "root_count": int(root_count),
                "line_point_count": int(segment_count * 2),
                "line_segment_count": int(segment_count),
                "steps": int(step_count),
                "substeps": int(substeps),
                "iterations": int(iterations),
                "roots_pinned": bool(roots_pinned),
                "runtime_cached": True,
                "collider_enabled": bool(collider_enabled),
                "collider_mode": "gpu_nearest_surface" if collider_enabled else "",
                "collider_samples": int(runtime.get("collider_count", 0) or 0) if collider_enabled else 0,
            },
        }

    def update_mesh_collider(self, runtime: dict[str, Any], collider: dict[str, Any] | None) -> bool:
        """Upload the current animated surface for GPU point-to-mesh projection.

        The collision pass samples the nearest outward-facing surface vertex.
        Volume meshes at their default resolution are uploaded in full; very
        dense meshes are deterministically capped to keep each solver step
        realtime.
        """
        if not isinstance(runtime, dict) or not isinstance(collider, dict):
            self._clear_mesh_collider(runtime)
            return False
        try:
            vertices = np.asarray(collider.get("vertices"), dtype="f4").reshape(-1, 3)
            normals = np.asarray(collider.get("normals"), dtype="f4").reshape(-1, 3)
        except Exception:
            self._clear_mesh_collider(runtime)
            return False
        if vertices.shape != normals.shape or int(vertices.shape[0]) <= 0:
            self._clear_mesh_collider(runtime)
            return False
        valid = np.isfinite(vertices).all(axis=1) & np.isfinite(normals).all(axis=1)
        normal_lengths = np.linalg.norm(normals, axis=1)
        valid &= normal_lengths > np.float32(1.0e-8)
        if not bool(np.any(valid)):
            self._clear_mesh_collider(runtime)
            return False
        vertices = vertices[valid]
        normals = normals[valid]
        normal_lengths = normal_lengths[valid]
        normals = normals / normal_lengths[:, None]
        source_count = int(vertices.shape[0])
        if source_count > _GPU_COLLIDER_MAX_SAMPLES:
            sample_indices = np.linspace(0, source_count - 1, num=_GPU_COLLIDER_MAX_SAMPLES, dtype=np.int64)
            vertices = vertices[sample_indices]
            normals = normals[sample_indices]
        count = int(vertices.shape[0])
        if count <= 0:
            self._clear_mesh_collider(runtime)
            return False
        vertices4 = np.zeros((count, 4), dtype="f4")
        normals4 = np.zeros((count, 4), dtype="f4")
        vertices4[:, :3] = vertices
        normals4[:, :3] = normals
        byte_count = int(vertices4.nbytes)
        vertex_buffer = runtime.get("collider_vertex_buffer")
        normal_buffer = runtime.get("collider_normal_buffer")
        capacity = int(runtime.get("collider_buffer_bytes", 0) or 0)
        if vertex_buffer is None or normal_buffer is None or capacity != byte_count:
            self._release_resource(vertex_buffer)
            self._release_resource(normal_buffer)
            vertex_buffer = self._ctx.buffer(vertices4.tobytes())
            normal_buffer = self._ctx.buffer(normals4.tobytes())
            runtime["collider_vertex_buffer"] = vertex_buffer
            runtime["collider_normal_buffer"] = normal_buffer
            runtime["collider_buffer_bytes"] = byte_count
        else:
            vertex_buffer.write(vertices4.tobytes())
            normal_buffer.write(normals4.tobytes())
        runtime["collider_count"] = count
        try:
            runtime["collider_margin"] = max(0.0, float(collider.get("margin", 0.0) or 0.0))
        except Exception:
            runtime["collider_margin"] = 0.0
        runtime["collider_source_count"] = source_count
        return True

    def runtime_resources(self, runtime: dict[str, Any]) -> list[Any]:
        resources: list[Any] = []
        for key in (
            "pos_buffer",
            "prev_buffer",
            "vel_buffer",
            "root_buffer",
            "root_target_buffer",
            "segment_index_buffer",
            "segment_buffer",
            "collider_vertex_buffer",
            "collider_normal_buffer",
        ):
            value = runtime.get(key)
            if value is not None and not any(value is existing for existing in resources):
                resources.append(value)
        for group in list(runtime.get("stretch_groups") or []) + list(runtime.get("bend_groups") or []):
            buf = group.get("buffer") if isinstance(group, dict) else None
            if buf is not None and not any(buf is existing for existing in resources):
                resources.append(buf)
        return resources

    def release_runtime(self, runtime: Any) -> None:
        if not isinstance(runtime, dict):
            return
        for resource in self.runtime_resources(runtime):
            try:
                if hasattr(resource, "release"):
                    resource.release()
            except Exception:
                pass

    def read_points(self, runtime: dict[str, Any]) -> Any:
        point_count = int(runtime.get("point_count", 0) or 0)
        if point_count <= 0:
            return np.zeros((0, 4), dtype="f4")
        data = runtime.get("pos_buffer").read()
        return np.frombuffer(data, dtype="f4").reshape(-1, 4)[:point_count].copy()

    def read_line_points(self, runtime: dict[str, Any]) -> Any:
        segment_count = int(runtime.get("segment_count", 0) or 0)
        if segment_count <= 0:
            return np.zeros((0, 3), dtype="f4")
        data = runtime.get("segment_buffer").read()
        return np.frombuffer(data, dtype="f4").reshape(-1, 6)[:segment_count].reshape(-1, 3).copy()

    def _make_constraint_buffers(self, arrays: Sequence[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for arr in arrays:
            rows = np.asarray(arr, dtype="f4").reshape(-1, 4)
            if rows.size == 0:
                continue
            out.append({"buffer": self._ctx.buffer(rows.tobytes()), "count": int(rows.shape[0])})
        return out

    def _buffer_reserve(self, byte_count: int) -> Any:
        try:
            return self._ctx.buffer(reserve=int(byte_count))
        except TypeError:
            return self._ctx.buffer(b"\x00" * int(byte_count))

    def _root_targets(self, cpu_runtime: dict[str, Any], root_targets: Any = None) -> Any:
        root_indices = np.asarray(cpu_runtime.get("root_indices"), dtype=np.int64).reshape(-1)
        if root_indices.size <= 0:
            return np.zeros((0, 3), dtype="f4")
        if root_targets is not None:
            try:
                targets = np.asarray(root_targets, dtype="f4").reshape(-1, 3)
                if int(targets.shape[0]) >= int(root_indices.size):
                    return targets[: int(root_indices.size)].astype("f4", copy=False)
            except Exception:
                pass
        points = np.asarray(cpu_runtime.get("points"), dtype="f4").reshape(-1, 3)
        return points[root_indices].astype("f4", copy=False)

    def _write_root_targets(self, runtime: dict[str, Any], root_targets: Any = None) -> None:
        root_count = int(runtime.get("root_count", 0) or 0)
        if root_count <= 0:
            return
        targets = None
        if root_targets is not None:
            try:
                arr = np.asarray(root_targets, dtype="f4").reshape(-1, 3)
                if int(arr.shape[0]) >= root_count:
                    targets = arr[:root_count]
            except Exception:
                targets = None
        if targets is None:
            targets = np.asarray(runtime.get("root_targets_cpu"), dtype="f4").reshape(-1, 3)
        if int(targets.shape[0]) != root_count:
            return
        out = np.zeros((root_count, 4), dtype="f4")
        out[:, :3] = targets.astype("f4", copy=False)
        buf = runtime.get("root_target_buffer")
        if buf is None:
            runtime["root_target_buffer"] = self._ctx.buffer(out.tobytes())
        else:
            buf.write(out.tobytes())
        runtime["root_targets_cpu"] = targets.astype("f4", copy=True)

    def _bind_point_buffers(self, runtime: dict[str, Any]) -> None:
        runtime.get("pos_buffer").bind_to_storage_buffer(0)
        runtime.get("prev_buffer").bind_to_storage_buffer(1)
        runtime.get("vel_buffer").bind_to_storage_buffer(2)

    def _pin_roots(self, runtime: dict[str, Any]) -> None:
        root_count = int(runtime.get("root_count", 0) or 0)
        if root_count <= 0:
            return
        root_buffer = runtime.get("root_buffer")
        target_buffer = runtime.get("root_target_buffer")
        if root_buffer is None or target_buffer is None:
            return
        self._bind_point_buffers(runtime)
        root_buffer.bind_to_storage_buffer(3)
        target_buffer.bind_to_storage_buffer(4)
        self._set(self._pin, "RootCount", root_count)
        self._run(self._pin, root_count)
        self._barrier()

    def _solve_groups(self, runtime: dict[str, Any], groups: Sequence[dict[str, Any]], dt: float, stiffness: float, compliance: float) -> None:
        if not groups or stiffness <= 0.0:
            return
        for group in groups:
            buf = group.get("buffer") if isinstance(group, dict) else None
            count = int(group.get("count", 0) or 0) if isinstance(group, dict) else 0
            if buf is None or count <= 0:
                continue
            runtime.get("pos_buffer").bind_to_storage_buffer(0)
            buf.bind_to_storage_buffer(5)
            self._set(self._solve, "ConstraintCount", int(count))
            self._set(self._solve, "Compliance", float(compliance))
            self._set(self._solve, "Stiffness", float(stiffness))
            self._set(self._solve, "Dt", float(dt))
            self._run(self._solve, count)
            self._barrier()

    def _apply_mesh_collision(self, runtime: dict[str, Any]) -> None:
        count = int(runtime.get("collider_count", 0) or 0)
        vertices = runtime.get("collider_vertex_buffer")
        normals = runtime.get("collider_normal_buffer")
        if count <= 0 or vertices is None or normals is None:
            return
        self._bind_point_buffers(runtime)
        vertices.bind_to_storage_buffer(5)
        normals.bind_to_storage_buffer(6)
        self._set(self._solve_mesh, "PointCount", int(runtime.get("point_count", 0) or 0))
        self._set(self._solve_mesh, "ColliderCount", count)
        self._set(self._solve_mesh, "CollisionMargin", float(runtime.get("collider_margin", 0.0) or 0.0))
        self._run(self._solve_mesh, int(runtime.get("point_count", 0) or 0))
        self._barrier()

    def _clear_mesh_collider(self, runtime: Any) -> None:
        if not isinstance(runtime, dict):
            return
        self._release_resource(runtime.pop("collider_vertex_buffer", None))
        self._release_resource(runtime.pop("collider_normal_buffer", None))
        runtime.pop("collider_buffer_bytes", None)
        runtime.pop("collider_count", None)
        runtime.pop("collider_margin", None)
        runtime.pop("collider_source_count", None)

    @staticmethod
    def _release_resource(resource: Any) -> None:
        if resource is None:
            return
        try:
            resource.release()
        except Exception:
            pass

    def _build_segment_buffer(self, runtime: dict[str, Any]) -> None:
        segment_count = int(runtime.get("segment_count", 0) or 0)
        if segment_count <= 0:
            return
        runtime.get("pos_buffer").bind_to_storage_buffer(0)
        runtime.get("segment_index_buffer").bind_to_storage_buffer(6)
        runtime.get("segment_buffer").bind_to_storage_buffer(7)
        self._set(self._build_segments, "SegmentCount", segment_count)
        self._run(self._build_segments, segment_count)
        self._barrier()

    def _set(self, program: Any, name: str, value: Any) -> None:
        try:
            program[name].value = value
        except Exception:
            pass

    def _run(self, program: Any, count: int) -> None:
        groups = _groups(count)
        try:
            program.run(group_x=groups, group_y=1, group_z=1)
        except TypeError:
            program.run(groups, 1, 1)

    def _barrier(self) -> None:
        barrier = getattr(self._ctx, "memory_barrier", None)
        if not callable(barrier):
            return
        try:
            barrier()
        except TypeError:
            try:
                barrier(0xFFFFFFFF)
            except Exception:
                pass


__all__ = ["XPBDStrandGPUBackend"]
