from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple
import math

from .fbx_canonical import (
    AnimationClip,
    JointAnimationTrack,
    JointTransform,
    QuatKeyframe,
    SkeletonAsset,
    Vec3Keyframe,
)

_TIME_EPSILON = 1e-8
_IDENTITY_MATRIX_4X4: Tuple[float, ...] = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)


class FBXEvaluatorError(RuntimeError):
    """Raised when canonical rig evaluation fails."""


@dataclass(eq=True)
class RigEvaluationResult:
    skeleton_name: str
    sampled_time: float
    clip_name: str | None = None
    local_transforms: List[JointTransform] = field(default_factory=list)
    local_matrices: List[Tuple[float, ...]] = field(default_factory=list)
    global_matrices: List[Tuple[float, ...]] = field(default_factory=list)
    skin_matrices: List[Tuple[float, ...]] = field(default_factory=list)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _quat_normalize(q: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x, y, z, w = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
    n2 = (x * x) + (y * y) + (z * z) + (w * w)
    if n2 <= 1e-16:
        return (0.0, 0.0, 0.0, 1.0)
    inv = 1.0 / math.sqrt(n2)
    return (x * inv, y * inv, z * inv, w * inv)


def _quat_slerp(
    qa: Tuple[float, float, float, float],
    qb: Tuple[float, float, float, float],
    alpha: float,
) -> Tuple[float, float, float, float]:
    ax, ay, az, aw = _quat_normalize(qa)
    bx, by, bz, bw = _quat_normalize(qb)
    dot = (ax * bx) + (ay * by) + (az * bz) + (aw * bw)
    if dot < 0.0:
        bx, by, bz, bw = -bx, -by, -bz, -bw
        dot = -dot

    if dot > 0.9995:
        out = (
            ax + (bx - ax) * alpha,
            ay + (by - ay) * alpha,
            az + (bz - az) * alpha,
            aw + (bw - aw) * alpha,
        )
        return _quat_normalize(out)

    dot = _clamp(dot, -1.0, 1.0)
    theta_0 = math.acos(dot)
    sin_theta_0 = math.sin(theta_0)
    if abs(sin_theta_0) <= 1e-12:
        return _quat_normalize((ax, ay, az, aw))

    theta = theta_0 * alpha
    sin_theta = math.sin(theta)
    s0 = math.cos(theta) - dot * sin_theta / sin_theta_0
    s1 = sin_theta / sin_theta_0
    return _quat_normalize(
        (
            (ax * s0) + (bx * s1),
            (ay * s0) + (by * s1),
            (az * s0) + (bz * s1),
            (aw * s0) + (bw * s1),
        )
    )


def _matrix4_mul_row_major(a: Tuple[float, ...], b: Tuple[float, ...]) -> Tuple[float, ...]:
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[(r * 4) + c] = (
                (a[(r * 4) + 0] * b[(0 * 4) + c])
                + (a[(r * 4) + 1] * b[(1 * 4) + c])
                + (a[(r * 4) + 2] * b[(2 * 4) + c])
                + (a[(r * 4) + 3] * b[(3 * 4) + c])
            )
    return tuple(out)


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

    # Column-vector convention with translation in the last column.
    # M = T * R * S, which scales rotation basis columns by sx/sy/sz.
    return (
        r00 * sx, r01 * sy, r02 * sz, tx,
        r10 * sx, r11 * sy, r12 * sz, ty,
        r20 * sx, r21 * sy, r22 * sz, tz,
        0.0, 0.0, 0.0, 1.0,
    )


def _sample_vec3_channel(
    keys: Sequence[Vec3Keyframe],
    t: float,
    default_value: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    values = list(keys or [])
    if not values:
        return tuple(default_value)
    if len(values) == 1:
        return tuple(values[0].value)

    if t <= values[0].time + _TIME_EPSILON:
        return tuple(values[0].value)
    if t >= values[-1].time - _TIME_EPSILON:
        return tuple(values[-1].value)

    for i in range(1, len(values)):
        left = values[i - 1]
        right = values[i]
        if t > right.time + _TIME_EPSILON:
            continue
        if left.interpolation == "step":
            return tuple(left.value)
        dt = max(_TIME_EPSILON, float(right.time) - float(left.time))
        alpha = _clamp((float(t) - float(left.time)) / dt, 0.0, 1.0)
        return (
            float(left.value[0]) + (float(right.value[0]) - float(left.value[0])) * alpha,
            float(left.value[1]) + (float(right.value[1]) - float(left.value[1])) * alpha,
            float(left.value[2]) + (float(right.value[2]) - float(left.value[2])) * alpha,
        )
    return tuple(values[-1].value)


def _sample_quat_channel(
    keys: Sequence[QuatKeyframe],
    t: float,
    default_value: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    values = list(keys or [])
    if not values:
        return _quat_normalize(tuple(default_value))
    if len(values) == 1:
        return _quat_normalize(tuple(values[0].value))

    if t <= values[0].time + _TIME_EPSILON:
        return _quat_normalize(tuple(values[0].value))
    if t >= values[-1].time - _TIME_EPSILON:
        return _quat_normalize(tuple(values[-1].value))

    for i in range(1, len(values)):
        left = values[i - 1]
        right = values[i]
        if t > right.time + _TIME_EPSILON:
            continue
        if left.interpolation == "step":
            return _quat_normalize(tuple(left.value))
        dt = max(_TIME_EPSILON, float(right.time) - float(left.time))
        alpha = _clamp((float(t) - float(left.time)) / dt, 0.0, 1.0)
        return _quat_slerp(tuple(left.value), tuple(right.value), alpha)
    return _quat_normalize(tuple(values[-1].value))


def _sample_track(track: JointAnimationTrack | None, bind: JointTransform, t: float) -> JointTransform:
    if track is None:
        out = JointTransform(
            translation=tuple(bind.translation),
            rotation=tuple(bind.rotation),
            scale=tuple(bind.scale),
        )
        out.validate()
        return out

    out = JointTransform(
        translation=_sample_vec3_channel(track.translation_keys, t, tuple(bind.translation)),
        rotation=_sample_quat_channel(track.rotation_keys, t, tuple(bind.rotation)),
        scale=_sample_vec3_channel(track.scale_keys, t, tuple(bind.scale)),
    )
    out.validate()
    return out


def _resolve_sample_time(clip: AnimationClip | None, time_seconds: float, *, loop: bool) -> float:
    if clip is None:
        return float(time_seconds)
    start = float(clip.start_time)
    end = float(clip.end_time)
    t = float(time_seconds)
    if end <= start + _TIME_EPSILON:
        return start
    if loop:
        span = end - start
        if span <= _TIME_EPSILON:
            return start
        wrapped = (t - start) % span
        return start + wrapped
    return _clamp(t, start, end)


def evaluate_rig_at_time(
    skeleton: SkeletonAsset,
    clip: AnimationClip | None,
    time_seconds: float,
    *,
    loop: bool = False,
) -> RigEvaluationResult:
    skeleton.validate()
    if clip is not None:
        clip.validate(skeleton=skeleton)
    t = _resolve_sample_time(clip, float(time_seconds), loop=bool(loop))

    track_map = {}
    if clip is not None:
        track_map = {track.joint_name: track for track in clip.tracks}

    local_transforms: List[JointTransform] = []
    local_matrices: List[Tuple[float, ...]] = []
    global_matrices: List[Tuple[float, ...]] = []
    skin_matrices: List[Tuple[float, ...]] = []

    for joint in skeleton.joints:
        sampled = _sample_track(track_map.get(joint.name), joint.local_bind, t)
        local_transforms.append(sampled)
        local_matrices.append(_matrix4_from_trs(sampled))

    for idx, joint in enumerate(skeleton.joints):
        local = local_matrices[idx]
        parent_idx = int(joint.parent_index)
        if parent_idx < 0:
            global_matrices.append(local)
        elif parent_idx >= idx:
            raise FBXEvaluatorError(
                f"Skeleton parent order is invalid at joint index {idx}."
            )
        else:
            global_matrices.append(
                _matrix4_mul_row_major(global_matrices[parent_idx], local)
            )
        inv_bind = tuple(joint.inverse_bind_matrix or _IDENTITY_MATRIX_4X4)
        if len(inv_bind) != 16:
            raise FBXEvaluatorError(
                f"Joint '{joint.name}' inverse_bind_matrix must contain 16 values."
            )
        skin_matrices.append(_matrix4_mul_row_major(global_matrices[idx], inv_bind))

    return RigEvaluationResult(
        skeleton_name=skeleton.name,
        sampled_time=float(t),
        clip_name=(clip.name if clip is not None else None),
        local_transforms=local_transforms,
        local_matrices=local_matrices,
        global_matrices=global_matrices,
        skin_matrices=skin_matrices,
    )


__all__ = [
    "FBXEvaluatorError",
    "RigEvaluationResult",
    "evaluate_rig_at_time",
]
