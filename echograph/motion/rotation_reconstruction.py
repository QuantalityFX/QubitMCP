"""Stable body-relative frames for position-only HumanML motion.

Positions do not measure axial twist. Use the parent frame for unobserved
rotation and a measured bend plane for the upper arms/thighs. In particular,
never transport each joint independently in world space during a body turn.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation
from .rig_limits import JOINT_LIMITS, UPPER_LIMB_ROLL


def align_vector(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source = source / np.linalg.norm(source)
    target = target / np.linalg.norm(target)
    cross = np.cross(source, target)
    dot = float(np.clip(source @ target, -1, 1))
    if dot < -1 + 1e-8:
        axis = np.cross(source, np.eye(3)[np.argmin(np.abs(source))])
        return Rotation.from_rotvec(axis / np.linalg.norm(axis) * np.pi).as_matrix()
    if np.linalg.norm(cross) < 1e-10:
        return np.eye(3)
    skew = np.array([[0, -cross[2], cross[1]], [cross[2], 0, -cross[0]], [-cross[1], cross[0], 0]])
    return np.eye(3) + skew + skew @ skew / (1 + dot)


def _smoothstep(value):
    value = float(np.clip(value, 0, 1))
    return value * value * (3 - 2 * value)


def parent_swing(source, target, previous=None):
    """Follow a bone direction without inventing twist relative to its parent.

    Near an exact reversal the minimum swing has no unique axis. Transport the
    preceding *local* frame there, fading back to minimum swing outside the
    singular region. This avoids an arbitrary pole flip without world roll drift.
    """
    source = source / np.linalg.norm(source)
    target = target / np.linalg.norm(target)
    swing = align_vector(source, target)
    dot = float(source @ target)
    if previous is not None and dot < -0.95:
        transported = align_vector(previous @ source, target) @ previous
        correction = Rotation.from_matrix(transported.T @ swing).as_rotvec()
        weight = _smoothstep((dot + 0.999) / 0.049)
        swing = transported @ Rotation.from_rotvec(correction * weight).as_matrix()
    return swing


def _bound_limb_roll(frame, source, target):
    # Do not repair knee twist by moving an entire half-turn into the thigh.
    neutral = parent_swing(source, target)
    delta = Rotation.from_matrix(frame @ neutral.T).as_rotvec()
    twist = float(delta @ target)
    limit = np.deg2rad(UPPER_LIMB_ROLL)
    return Rotation.from_rotvec(target * np.clip(twist, -limit, limit)).as_matrix() @ neutral


def bend_frame(source, target, bend_direction, rest_bend_direction, previous=None, *, fps=20.):
    """Aim the upper limb and set its roll from a reliable elbow/knee bend.

    An almost straight limb has no useful bend plane. Keep its preceding local
    roll until the bend becomes measurable, instead of flipping on tiny noise.
    """
    source = source / np.linalg.norm(source)
    target = target / np.linalg.norm(target)
    frame = parent_swing(source, target, previous)
    if previous is not None:
        frame = align_vector(previous @ source, target) @ previous
    length = float(np.linalg.norm(bend_direction))
    if length < 1e-8:
        return _bound_limb_roll(frame, source, target)
    bend_direction = bend_direction / length
    pole = bend_direction - target * float(target @ bend_direction)
    strength = float(np.linalg.norm(pole))
    # Below 5 degrees of flexion the pole is mostly positional noise; a
    # 20-degree bend fully determines the plane. Both values are scale invariant.
    weight = _smoothstep((strength - np.sin(np.deg2rad(5))) /
                         (np.sin(np.deg2rad(20)) - np.sin(np.deg2rad(5))))
    if weight <= 0:
        return _bound_limb_roll(frame, source, target)
    pole /= strength
    current = frame @ rest_bend_direction
    current -= target * float(target @ current)
    current_length = float(np.linalg.norm(current))
    if current_length < 1e-8:
        return _bound_limb_roll(frame, source, target)
    current /= current_length
    angle = np.arctan2(target @ np.cross(current, pole), np.clip(current @ pole, -1, 1))
    # Pole estimates can reverse when a generated knee/elbow almost straightens.
    # Bound only the inferred roll speed; the measured bone direction is retained.
    correction = angle * weight
    if previous is not None:
        correction = float(np.clip(correction, -np.deg2rad(360) / fps, np.deg2rad(360) / fps))
    frame = Rotation.from_rotvec(target * correction).as_matrix() @ frame
    return _bound_limb_roll(frame, source, target)


def stabilize_spine(joint, local, previous, fps):
    """Bound parent-relative torso motion, including the multi-child chest.

    Lower spine positions only measure swing; collar positions also measure
    chest twist. Interpolate bounded swing vectors and twist separately so
    smoothing cannot reintroduce roll or cross the +/-180 degree branch cut.
    """
    bend_limit = np.deg2rad(JOINT_LIMITS[joint]['swing'])
    twist_limit = np.deg2rad(JOINT_LIMITS[joint]['twist'])

    def components(matrix):
        q = Rotation.from_matrix(matrix).as_quat()
        twist_q = np.array([0., q[1], 0., q[3]])
        norm = np.linalg.norm(twist_q)
        twist = Rotation.from_quat(twist_q / norm) if norm > 1e-8 else Rotation.identity()
        swing = (Rotation.from_matrix(matrix) * twist.inv()).as_rotvec()
        swing[1] = 0.
        angle = np.linalg.norm(swing)
        if angle > bend_limit:
            swing *= bend_limit / angle
        return swing, float(np.clip(twist.as_rotvec()[1], -twist_limit, twist_limit))

    swing, twist = components(local)
    if previous is not None:
        old_swing, old_twist = components(previous)
        distance = np.linalg.norm(swing-old_swing) + abs(twist-old_twist)
        step = np.deg2rad(JOINT_LIMITS[joint]['speed']) / fps
        weight = min(1., step / max(distance, 1e-12))
        swing = old_swing + weight * (swing-old_swing)
        twist = old_twist + weight * (twist-old_twist)
    return (Rotation.from_rotvec(swing) * Rotation.from_rotvec([0., twist, 0.])).as_matrix()


def stabilize_joint(joint, source, target, local, previous, fps):
    """Conservative limits for inferred orientations, not authored animation.

    Root rotation is unrestricted, so flips and spins remain possible.
    Spine stabilization also covers multi-child frames in the caller.
    Knees/elbows retain the measured flexion angle on their anatomical hinge;
    feet/neck/collars use a local swing cone. Inconsistent source poses are corrected
    rather than transferring sideways or backward knee bends into a skin.
    The XYZ archive is retained separately from this fixed-bone reconstruction.
    """
    axis = {4: 1, 5: 1, 18: 0, 19: 0, 7: 2, 8: 2, 12: 1, 13: 0, 14: 0}.get(joint)
    if axis is None:
        return local
    # Reversal handling can retain some pole roll. These joints have no measured
    # axial orientation, so retain only swing before applying anatomical bounds.
    quaternion = Rotation.from_matrix(local).as_quat()
    quaternion[axis] = 0.
    norm = float(np.linalg.norm(quaternion))
    local = Rotation.from_quat(quaternion / norm).as_matrix() if norm > 1e-8 else np.eye(3)
    if joint in (4, 5, 18, 19):
        source = source / np.linalg.norm(source)
        target = target / np.linalg.norm(target)
        angle = float(np.arccos(np.clip(source @ target, -1, 1)))
        hinge = np.array(JOINT_LIMITS[joint]['axis'])
        limit = np.deg2rad(JOINT_LIMITS[joint]['flex'])
        local = Rotation.from_rotvec(hinge * min(angle, limit)).as_matrix()
        # Fast airborne flexion is measured motion, not unobserved roll. A
        # rate cap here would make the feet/hands lag behind a backflip.
        return local
    elif joint in (7, 8, 12, 13, 14):
        vector = Rotation.from_matrix(local).as_rotvec()
        angle = float(np.linalg.norm(vector))
        limit = np.deg2rad(JOINT_LIMITS[joint]['swing'])
        if angle > limit:
            local = Rotation.from_rotvec(vector * (limit / angle)).as_matrix()
        speed = JOINT_LIMITS[joint]['speed']
    else:
        return local
    if previous is not None:
        delta = Rotation.from_matrix(previous.T @ local).as_rotvec()
        angle = float(np.linalg.norm(delta))
        step = np.deg2rad(speed) / fps
        if angle > step:
            local = previous @ Rotation.from_rotvec(delta * (step / angle)).as_matrix()
    return local
