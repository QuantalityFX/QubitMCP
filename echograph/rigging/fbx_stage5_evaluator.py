from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple
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

# Runtime caches to avoid repeating expensive schema validation and per-clip track mapping.
# These objects are effectively immutable after ingest in the current pipeline.
_VALIDATED_SKELETON_IDS: set[int] = set()
_VALIDATED_CLIP_SKELETON_IDS: set[tuple[int, int]] = set()
_TRACKS_FOR_SKELETON_CACHE: Dict[tuple[int, int], Dict[str, object]] = {}


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
    a00, a01, a02, a03, a10, a11, a12, a13, a20, a21, a22, a23, a30, a31, a32, a33 = a
    b00, b01, b02, b03, b10, b11, b12, b13, b20, b21, b22, b23, b30, b31, b32, b33 = b
    return (
        (a00 * b00) + (a01 * b10) + (a02 * b20) + (a03 * b30),
        (a00 * b01) + (a01 * b11) + (a02 * b21) + (a03 * b31),
        (a00 * b02) + (a01 * b12) + (a02 * b22) + (a03 * b32),
        (a00 * b03) + (a01 * b13) + (a02 * b23) + (a03 * b33),
        (a10 * b00) + (a11 * b10) + (a12 * b20) + (a13 * b30),
        (a10 * b01) + (a11 * b11) + (a12 * b21) + (a13 * b31),
        (a10 * b02) + (a11 * b12) + (a12 * b22) + (a13 * b32),
        (a10 * b03) + (a11 * b13) + (a12 * b23) + (a13 * b33),
        (a20 * b00) + (a21 * b10) + (a22 * b20) + (a23 * b30),
        (a20 * b01) + (a21 * b11) + (a22 * b21) + (a23 * b31),
        (a20 * b02) + (a21 * b12) + (a22 * b22) + (a23 * b32),
        (a20 * b03) + (a21 * b13) + (a22 * b23) + (a23 * b33),
        (a30 * b00) + (a31 * b10) + (a32 * b20) + (a33 * b30),
        (a30 * b01) + (a31 * b11) + (a32 * b21) + (a33 * b31),
        (a30 * b02) + (a31 * b12) + (a32 * b22) + (a33 * b32),
        (a30 * b03) + (a31 * b13) + (a32 * b23) + (a33 * b33),
    )


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
    if not keys:
        return tuple(default_value)
    if len(keys) == 1:
        return tuple(keys[0].value)

    if t <= keys[0].time + _TIME_EPSILON:
        return tuple(keys[0].value)
    if t >= keys[-1].time - _TIME_EPSILON:
        return tuple(keys[-1].value)

    lo = 1
    hi = len(keys) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if t <= float(keys[mid].time):
            hi = mid
        else:
            lo = mid + 1

    right = keys[lo]
    left = keys[lo - 1]
    if left.interpolation == "step":
        return tuple(left.value)
    dt = max(_TIME_EPSILON, float(right.time) - float(left.time))
    alpha = _clamp((float(t) - float(left.time)) / dt, 0.0, 1.0)
    return (
        float(left.value[0]) + (float(right.value[0]) - float(left.value[0])) * alpha,
        float(left.value[1]) + (float(right.value[1]) - float(left.value[1])) * alpha,
        float(left.value[2]) + (float(right.value[2]) - float(left.value[2])) * alpha,
    )


def _sample_quat_channel(
    keys: Sequence[QuatKeyframe],
    t: float,
    default_value: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    if not keys:
        return _quat_normalize(tuple(default_value))
    if len(keys) == 1:
        return _quat_normalize(tuple(keys[0].value))

    if t <= keys[0].time + _TIME_EPSILON:
        return _quat_normalize(tuple(keys[0].value))
    if t >= keys[-1].time - _TIME_EPSILON:
        return _quat_normalize(tuple(keys[-1].value))

    lo = 1
    hi = len(keys) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if t <= float(keys[mid].time):
            hi = mid
        else:
            lo = mid + 1

    right = keys[lo]
    left = keys[lo - 1]
    if left.interpolation == "step":
        return _quat_normalize(tuple(left.value))
    dt = max(_TIME_EPSILON, float(right.time) - float(left.time))
    alpha = _clamp((float(t) - float(left.time)) / dt, 0.0, 1.0)
    return _quat_slerp(tuple(left.value), tuple(right.value), alpha)


def _sample_track(track: JointAnimationTrack | None, bind: JointTransform, t: float) -> JointTransform:
    if track is None:
        return JointTransform(
            translation=tuple(bind.translation),
            rotation=tuple(bind.rotation),
            scale=tuple(bind.scale),
        )

    return JointTransform(
        translation=_sample_vec3_channel(track.translation_keys, t, tuple(bind.translation)),
        rotation=_sample_quat_channel(track.rotation_keys, t, tuple(bind.rotation)),
        scale=_sample_vec3_channel(track.scale_keys, t, tuple(bind.scale)),
    )


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


def _validated_joint_tracks_for_skeleton(
    skeleton: SkeletonAsset,
    clip: AnimationClip | None,
) -> List[JointAnimationTrack | None]:
    if clip is None:
        return [None] * int(len(skeleton.joints))
    key = (id(skeleton), id(clip))
    joint_count = int(len(skeleton.joints))
    tracks_obj = list(getattr(clip, "tracks", []) or [])
    tracks_id = id(getattr(clip, "tracks", None))
    track_count = int(len(tracks_obj))
    cached = _TRACKS_FOR_SKELETON_CACHE.get(key)
    if isinstance(cached, dict):
        if (
            int(cached.get("joint_count", -1)) == joint_count
            and int(cached.get("track_count", -1)) == track_count
            and int(cached.get("tracks_id", -1)) == tracks_id
        ):
            tracks = cached.get("tracks")
            if isinstance(tracks, list) and len(tracks) == joint_count:
                return tracks

    track_map = {track.joint_name: track for track in tracks_obj}
    tracks_for_joints: List[JointAnimationTrack | None] = [None] * joint_count
    for idx, joint in enumerate(skeleton.joints):
        tracks_for_joints[idx] = track_map.get(joint.name)
    _TRACKS_FOR_SKELETON_CACHE[key] = {
        "joint_count": joint_count,
        "track_count": track_count,
        "tracks_id": tracks_id,
        "tracks": tracks_for_joints,
    }
    return tracks_for_joints


def evaluate_rig_at_time(
    skeleton: SkeletonAsset,
    clip: AnimationClip | None,
    time_seconds: float,
    *,
    loop: bool = False,
    include_debug_data: bool = True,
) -> RigEvaluationResult:
    skeleton_id = id(skeleton)
    if skeleton_id not in _VALIDATED_SKELETON_IDS:
        skeleton.validate()
        _VALIDATED_SKELETON_IDS.add(skeleton_id)
    if clip is not None:
        clip_key = (id(clip), skeleton_id)
        if clip_key not in _VALIDATED_CLIP_SKELETON_IDS:
            clip.validate(skeleton=skeleton)
            _VALIDATED_CLIP_SKELETON_IDS.add(clip_key)
    t = _resolve_sample_time(clip, float(time_seconds), loop=bool(loop))

    tracks_for_joints = _validated_joint_tracks_for_skeleton(skeleton, clip)

    local_transforms: List[JointTransform] = [] if bool(include_debug_data) else []
    local_matrices_debug: List[Tuple[float, ...]] = [] if bool(include_debug_data) else []
    local_matrices_eval: List[Tuple[float, ...]] = []
    global_matrices_debug: List[Tuple[float, ...]] = [] if bool(include_debug_data) else []
    global_matrices_eval: List[Tuple[float, ...]] = []
    skin_matrices: List[Tuple[float, ...]] = []

    for idx, joint in enumerate(skeleton.joints):
        sampled = _sample_track(tracks_for_joints[idx], joint.local_bind, t)
        if bool(include_debug_data):
            local_transforms.append(sampled)
        local_matrix = _matrix4_from_trs(sampled)
        local_matrices_eval.append(local_matrix)
        if bool(include_debug_data):
            local_matrices_debug.append(local_matrix)

    for idx, joint in enumerate(skeleton.joints):
        local = local_matrices_eval[idx]
        parent_idx = int(joint.parent_index)
        if parent_idx < 0:
            global_matrices_eval.append(local)
        elif parent_idx >= idx:
            raise FBXEvaluatorError(
                f"Skeleton parent order is invalid at joint index {idx}."
            )
        else:
            global_matrices_eval.append(
                _matrix4_mul_row_major(global_matrices_eval[parent_idx], local)
            )
        if bool(include_debug_data):
            global_matrices_debug.append(global_matrices_eval[idx])
        inv_bind = tuple(joint.inverse_bind_matrix or _IDENTITY_MATRIX_4X4)
        if len(inv_bind) != 16:
            raise FBXEvaluatorError(
                f"Joint '{joint.name}' inverse_bind_matrix must contain 16 values."
            )
        skin_matrices.append(_matrix4_mul_row_major(global_matrices_eval[idx], inv_bind))

    return RigEvaluationResult(
        skeleton_name=skeleton.name,
        sampled_time=float(t),
        clip_name=(clip.name if clip is not None else None),
        local_transforms=local_transforms,
        local_matrices=local_matrices_debug,
        global_matrices=global_matrices_debug,
        skin_matrices=skin_matrices,
    )


__all__ = [
    "FBXEvaluatorError",
    "RigEvaluationResult",
    "evaluate_rig_at_time",
]
