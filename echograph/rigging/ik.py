"""Reusable constrained pose IK, independent of nodes, Qt, and HumanML rigs."""
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation


def forward_kinematics(local_rotations, offsets, parents, root=(0., 0., 0.)):
    """Evaluate a parent-ordered skeleton without changing its bone lengths."""
    local = np.asarray(local_rotations, dtype=float)
    offsets = np.asarray(offsets, dtype=float)
    if local.shape != (len(parents), 3, 3) or offsets.shape != (len(parents), 3):
        raise ValueError('IK rotation, offset, and hierarchy sizes must match.')
    world = local.copy()
    points = np.empty_like(offsets)
    for joint, parent in enumerate(parents):
        if parent < 0:
            points[joint] = np.asarray(root)+offsets[joint]
        elif parent >= joint:
            raise ValueError('IK parents must precede their children.')
        else:
            points[joint] = points[parent]+world[parent]@offsets[joint]
            world[joint] = world[parent]@local[joint]
    return points, world


@dataclass
class IKResult:
    rotations: np.ndarray
    points: np.ndarray
    parameters: np.ndarray
    max_error: float
    converged: bool


def solve_constrained_ik(local_rotations, offsets, parents, *,
                         joint_axes: Mapping[int, Sequence[Sequence[float]]],
                         project_rotation: Callable | None = None,
                         targets: Mapping[int, Sequence[float]] | None = None,
                         constraint_residual: Callable | None = None,
                         reference_rotations=None, previous_parameters=None,
                         pose_weight=.008, temporal_weight=.008,
                         tolerance=.0005, max_iterations=45, retry_seeds=(),
                         check_cancel=None, prioritize_constraints=False,
                         reuse_unchanged_joints=False):
    """Solve positional targets and/or caller-supplied geometric constraints.

    ``joint_axes`` defines local rotation DOFs: one axis for a hinge, three for
    a ball joint. Parameters are radians relative to ``reference_rotations``.
    ``project_rotation(joint, xyzw)`` enforces anatomical limits on *every*
    candidate. ``constraint_residual(points, world_rotations, local_rotations)``
    returns errors in the same units as offsets. Positive penetration depths
    work directly. Use normalized units when combining differently sized rigs.

    Only listed joints can change. Root translations and bone lengths remain
    fixed. The returned arrays are independent of the inputs. Callers can carry
    parameters to the next frame for temporal regularization, or supply retry
    seeds to escape symmetric/centered contacts. Unreachable goals are reported
    through ``max_error`` and ``converged`` rather than relaxing joint limits.

    ``reuse_unchanged_joints`` reuses exact rotation projections and unchanged
    FK branches during numerical differentiation. Enable only when the rotation
    projector is deterministic for the duration of this solve.
    """
    original = np.asarray(local_rotations, dtype=float)
    reference = original if reference_rotations is None else np.asarray(reference_rotations, dtype=float)
    if reference.shape != original.shape or not np.isfinite(reference).all() or not np.isfinite(original).all():
        raise ValueError('IK reference and current rotations must have matching shapes and finite values.')
    if not np.isfinite(offsets).all():
        raise ValueError('IK offsets must be finite.')
    blocks, count = [], 0
    for joint, axes in joint_axes.items():
        axes = np.asarray(axes, dtype=float).reshape(-1, 3)
        if not 0 <= joint < len(parents) or not np.isfinite(axes).all() or np.any(np.linalg.norm(axes,axis=1) < 1e-8):
            raise ValueError('IK needs valid joint indices and nonzero, finite axes.')
        axes = axes/np.linalg.norm(axes,axis=1)[:,None]
        blocks.append((joint, axes, slice(count,count+len(axes))))
        count += len(axes)
    targets = dict(targets or {})
    for joint,target in targets.items():
        target = np.asarray(target,dtype=float)
        if not 0 <= joint < len(parents) or target.shape != (3,) or not np.isfinite(target).all():
            raise ValueError('IK targets need valid joint indices and finite 3D positions.')
    if not targets and constraint_residual is None:
        raise ValueError('IK needs targets or a constraint residual.')
    old = np.zeros(count) if previous_parameters is None else np.asarray(previous_parameters,dtype=float)
    if old.shape != (count,) or not np.isfinite(old).all():
        raise ValueError('IK previous parameters do not match the rotation DOFs.')

    projected = [{} for _ in blocks]
    reference_q = [Rotation.from_matrix(reference[j]) for j,_,_ in blocks] if reuse_unchanged_joints else None
    cached_local = cached_points = cached_world = None
    descendants = None
    if reuse_unchanged_joints:
        # Validate the hierarchy through the normal FK path before indexing it.
        cached_points,cached_world = forward_kinematics(original,offsets,parents)
        cached_local = original.copy()
        descendants = np.eye(len(parents),dtype=bool)
        for child in range(len(parents)-1,-1,-1):
            parent = parents[child]
            if parent >= 0:
                descendants[parent] |= descendants[child]

    def pose(parameters):
        nonlocal cached_local,cached_points,cached_world
        local = original.copy()
        for block,(joint,axes,section) in enumerate(blocks):
            key = parameters[section].tobytes() if reuse_unchanged_joints else None
            cache = projected[block]
            if key is not None and key in cache:
                local[joint] = cache[key]
                continue
            base = reference_q[block] if reuse_unchanged_joints else Rotation.from_matrix(reference[joint])
            q = (base*Rotation.from_rotvec(parameters[section]@axes)).as_quat()
            if project_rotation is not None:
                q = project_rotation(joint,q)
            local[joint] = Rotation.from_quat(q).as_matrix()
            if key is not None:
                if len(cache) >= 8:
                    cache.pop(next(iter(cache)))
                cache[key] = local[joint].copy()
        if reuse_unchanged_joints:
            changed = np.any(local != cached_local,axis=(1,2))
            affected = np.any(descendants[changed],axis=0)
            points,world = cached_points.copy(),cached_world.copy()
            for joint in np.flatnonzero(affected):
                parent = parents[joint]
                if parent < 0:
                    points[joint] = offsets[joint]
                    world[joint] = local[joint]
                else:
                    points[joint] = points[parent]+world[parent]@offsets[joint]
                    world[joint] = world[parent]@local[joint]
            cached_local,cached_points,cached_world = local.copy(),points.copy(),world.copy()
        else:
            points, world = forward_kinematics(local,offsets,parents)
        return local,points,world

    def errors(local,points,world):
        values = [(points[j]-np.asarray(target)).ravel() for j,target in targets.items()]
        if constraint_residual is not None:
            values.append(np.asarray(constraint_residual(points,world,local),dtype=float).ravel())
        return np.concatenate(values) if values else np.empty(0)

    def residual(parameters):
        if check_cancel is not None:
            check_cancel()
        return np.r_[errors(*pose(parameters))*16,parameters*pose_weight,
                     (parameters-old)*temporal_weight]

    initial = np.clip(old,-np.pi,np.pi)
    if count:
        candidates = [initial, np.zeros(count)]
        candidates.append(least_squares(residual,initial,bounds=(-np.pi,np.pi),max_nfev=max_iterations).x)
        if np.max(abs(errors(*pose(candidates[-1]))),initial=0) > tolerance:
            for seed in retry_seeds:
                seed = np.asarray(seed,dtype=float)
                if seed.shape != (count,):
                    raise ValueError('IK retry seed does not match the rotation DOFs.')
                candidates.append(least_squares(residual,np.clip(seed,-np.pi,np.pi),
                                                bounds=(-np.pi,np.pi),max_nfev=max_iterations).x)
                if np.max(abs(errors(*pose(candidates[-1]))),initial=0) <= tolerance:
                    break
        def score(parameters):
            values = residual(parameters)
            if prioritize_constraints:
                error = errors(*pose(parameters))
                return (float(np.max(abs(error),initial=0)), float(values@values))
            return float(values@values)
        best = min(candidates,key=score)
    else:
        best = initial
    local,points,world = pose(best)
    maximum = float(np.max(abs(errors(local,points,world)),initial=0))
    return IKResult(local,points,best.copy(),maximum,maximum <= tolerance)
