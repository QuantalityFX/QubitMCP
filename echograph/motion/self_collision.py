"""Conservative arm self-collision cleanup for reconstructed HumanML BVHs.

Capsules estimate body volume from skeleton proportions. This is not a target
mesh collision solver: clothing, fingers, and unusual target proportions may
still need mesh-specific correction after retargeting.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from .rotation_reconstruction import _bound_limb_roll
from .rig_limits import JOINT_LIMITS


PARENTS = (-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19)
ARMS = ((16, 18, 20), (17, 19, 21))


def world_pose(local, offsets, root):
    world = local.copy()
    points = np.zeros((22, 3))
    points[0] = root
    for joint, parent in enumerate(PARENTS):
        if parent >= 0:
            points[joint] = points[parent] + world[parent] @ offsets[joint]
            world[joint] = world[parent] @ local[joint]
    return points, world


def segment_distances(a, b, c, d):
    """Distances from one segment to a batch of segments, including endpoints."""
    u, v, r = b - a, d - c, a - c
    aa = float(u @ u)
    ee = np.einsum('ij,ij->i', v, v)
    bb = v @ u
    cc = r @ u
    ff = np.einsum('ij,ij->i', v, r)
    den = aa * ee - bb * bb
    s = np.clip(np.divide(bb * ff - cc * ee, den, out=np.zeros_like(den), where=den > 1e-12), 0, 1)
    t = np.divide(bb * s + ff, ee, out=np.zeros_like(ee), where=ee > 1e-12)
    s = np.where(t < 0, np.clip(-cc / max(aa, 1e-12), 0, 1), s)
    s = np.where(t > 1, np.clip((bb - cc) / max(aa, 1e-12), 0, 1), s)
    # A zero-length obstacle is a sphere; project its center onto the arm.
    s = np.where(ee <= 1e-12, np.clip(-cc / max(aa, 1e-12), 0, 1), s)
    t = np.clip(t, 0, 1)
    return np.linalg.norm(r + s[:, None] * u - t[:, None] * v, axis=1)


def body_capsules(points, scale, arm):
    other = ARMS[1] if arm == ARMS[0] else ARMS[0]
    pairs = [(0, 3), (3, 9), (9, 12), (12, 15),
             (1, 4), (2, 5), (4, 7), (5, 8),
             (other[0], other[1]), (other[1], other[2]), (15, 15)]
    # The neck is narrower than the skull: extending a head-sized capsule all
    # the way to its base incorrectly traps arms at their shoulder attachments.
    radii = np.array([.115, .14, .12, .045, .075, .075, .05, .05, .04, .035, .09]) * scale
    return (np.array([points[a] for a, _ in pairs]),
            np.array([points[b] for _, b in pairs]), radii)


def arm_penetrations(points, obstacles, arm, scale):
    shoulder, elbow, wrist = points[list(arm)]
    starts, ends, radii = obstacles
    upper_start = shoulder + (elbow - shoulder) * .25
    # Ignore the shoulder attachment zone, where the upper arm and chest share
    # skin. The rest of each segment is tested continuously, not just the joints.
    upper = segment_distances(upper_start, elbow, starts, ends)
    # Chest capsules overlap the shoulder socket itself. Exclude that connected
    # tissue from chest tests only; do not give the arm the same exemption from
    # legs, head, or the other arm.
    chest_start = shoulder + (elbow - shoulder) * .45
    upper[:3] = segment_distances(chest_start, elbow, starts[:3], ends[:3])
    lower = segment_distances(elbow, wrist, starts, ends)
    hand = segment_distances(wrist, wrist, starts, ends)
    return np.maximum(0, np.concatenate((radii + .04*scale - upper,
                                        radii + .04*scale - lower,
                                        radii + .06*scale - hand)))


def correct_arm_collisions(rotations, offsets, roots, fps):
    """Adjust shoulder orientation/elbow flexion, preserving lengths and root.

    A small pose/temporal penalty keeps corrections local and lets them fade
    out. Collision detection runs on the final fixed-bone pose, after twist
    stabilization, so a later reconstruction cannot undo the correction.
    """
    output = rotations.copy()
    # Typical hip-to-knee length is about 0.44m. Scale all proxies together for
    # native/model and standardman proportions and differently sized skeletons.
    scale = max(1e-6, float(np.mean(np.linalg.norm(offsets[[4, 5]], axis=1))) / .44)
    normalized_offsets = np.round(offsets / scale, 12)
    previous = {arm: np.zeros(4) for arm in ARMS}
    corrected = np.zeros((len(output), 2), dtype=bool)
    before, after = [], []
    for frame in range(len(output)):
        original_points, _ = world_pose(output[frame], offsets, roots[frame])
        before.append(max(float(max(arm_penetrations(original_points, body_capsules(original_points, scale, arm), arm, scale)))
                          for arm in ARMS))
        for visit, arm in enumerate(ARMS * 2):
            side = visit % 2
            shoulder, elbow, wrist = arm
            points, world = world_pose(output[frame], offsets, roots[frame])
            if visit >= 2 and all(max(arm_penetrations(points, body_capsules(points, scale, candidate), candidate, scale))
                                  < .003*scale for candidate in ARMS):
                break
            parent = world[PARENTS[shoulder]]
            # Solve in dimensionless shoulder-parent space. A body flip, world
            # translation, or unit conversion must not change the chosen escape.
            points = np.round((points-points[shoulder]) @ parent / scale, 12)
            obstacles = body_capsules(points, 1., arm)
            initial = arm_penetrations(points, obstacles, arm, 1.)
            if max(initial) < .0005 and np.linalg.norm(previous[arm]) < .002:
                previous[arm][:] = 0
                continue
            base = rotations[frame, shoulder].copy()
            hinge = np.array([0., -1. if side == 0 else 1., 0.])
            flex = float(Rotation.from_matrix(rotations[frame, elbow]).as_rotvec() @ hinge)
            anchor = points[shoulder].copy()
            old_wrist = points[wrist].copy()
            old_delta = previous[arm].copy()

            def shoulder_local(delta):
                local = base @ Rotation.from_rotvec(delta[:3]).as_matrix()
                axis = normalized_offsets[elbow] / np.linalg.norm(normalized_offsets[elbow])
                return _bound_limb_roll(local, axis, local @ axis)

            def pose(delta):
                upper = shoulder_local(delta)
                lower = upper @ Rotation.from_rotvec(hinge * (flex + delta[3])).as_matrix()
                updated = points.copy()
                updated[elbow] = anchor + upper @ normalized_offsets[elbow]
                updated[wrist] = updated[elbow] + lower @ normalized_offsets[wrist]
                return updated

            def residual(delta):
                moved = pose(delta)
                penetration = arm_penetrations(moved, obstacles, arm, 1.)
                return np.concatenate((penetration * 12, delta * .008,
                                       (delta-old_delta) * (.012*fps/20),
                                       (moved[wrist]-old_wrist) * .02))

            lower_bounds = np.array([-2.5, -2.5, -2.5, -flex])
            upper_bounds = np.array([2.5, 2.5, 2.5, np.deg2rad(JOINT_LIMITS[elbow]['flex'])-flex])
            seed = np.clip(old_delta, lower_bounds+1e-8, upper_bounds-1e-8)
            candidates = [least_squares(residual, seed, bounds=(lower_bounds, upper_bounds), max_nfev=35).x]
            best_error = float(max(arm_penetrations(pose(candidates[0]), obstacles, arm, 1.)))
            if best_error > .003:
                # A segment exactly through a capsule's center has no unique
                # escape direction. Try deterministic alternatives around each
                # shoulder axis, also bending the elbow to avoid trapped poses.
                for axis in range(3):
                    for sign in (-1, 1):
                        seed = np.zeros(4)
                        seed[axis] = sign * .9
                        seed[3] = np.deg2rad(80)-flex
                        seed = np.clip(seed, lower_bounds+1e-8, upper_bounds-1e-8)
                        candidates.append(least_squares(residual, seed, bounds=(lower_bounds, upper_bounds), max_nfev=60).x)
            delta = min(candidates, key=lambda x: float(np.sum(residual(x)**2)))
            final = arm_penetrations(pose(delta), obstacles, arm, 1.)
            if max(final) > max(initial) + .0005:
                previous[arm][:] = 0
                continue
            output[frame, shoulder] = shoulder_local(delta)
            output[frame, elbow] = Rotation.from_rotvec(hinge * (flex+delta[3])).as_matrix()
            previous[arm] = delta
            corrected[frame, side] = np.linalg.norm(delta) > 1e-5
        # Measure the finished pose, including any interaction between arms.
        points, _ = world_pose(output[frame], offsets, roots[frame])
        after.append(max(float(max(arm_penetrations(points, body_capsules(points, scale, arm), arm, scale)))
                         for arm in ARMS))
    return output, corrected, {
        'version': 1,
        'method': 'arm IK against skeleton-scaled torso, head, leg, and opposite-arm capsules',
        'corrected_frames': int(np.count_nonzero(corrected.any(axis=1))),
        'remaining_proxy_overlap_frames': int(np.count_nonzero(np.array(after) > .003*scale)),
        'max_proxy_overlap_before': float(max(before, default=0)),
        'max_proxy_overlap_after': float(max(after, default=0)),
    }
