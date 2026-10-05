"""Shared inferred-motion limits, in degrees (speed in degrees/second)."""

UPPER_LIMB_ROLL = 100.


def rotation_parts(quaternion, axis):
    """Return swing and signed axial twist in radians, independent of Euler order."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    axis = np.asarray(axis,dtype=float)
    axis = axis / np.linalg.norm(axis)
    rotation = Rotation.from_quat(quaternion)
    q = rotation.as_quat()
    twist_q = np.r_[axis*float(q[:3] @ axis),q[3]]
    twist = Rotation.from_quat(twist_q) if np.linalg.norm(twist_q)>1e-8 else Rotation.identity()
    return rotation*twist.inv(), float(twist.as_rotvec() @ axis)


def clamp_preview_rotation(joint, quaternion, axis, previous=None, *, limits=None):
    """Project an edit into the limits, sweeping large drag steps to avoid flips."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    limits = PREVIEW_JOINT_LIMITS if limits is None else limits
    if previous is None or joint not in limits:
        return _project_preview_rotation(joint, quaternion, axis, previous, limits=limits)
    # A fast mouse event can cross the forbidden region and end on the other
    # side of the cone. Apply its delta in small steps from the accepted pose,
    # projecting each step so an overshoot cannot tunnel through a limit.
    start = Rotation.from_quat(previous)
    delta = (Rotation.from_quat(quaternion)*start.inv()).as_rotvec()
    steps = max(1,int(np.ceil(np.linalg.norm(delta)/np.deg2rad(5.))))
    step = Rotation.from_rotvec(delta/steps)
    current = _project_preview_rotation(joint,previous,axis,limits=limits)
    for _ in range(steps):
        desired = (step*Rotation.from_quat(current)).as_quat()
        current = _project_preview_rotation(joint,desired,axis,current,limits=limits)
    return current


def _project_preview_rotation(joint, quaternion, axis, previous=None, *, limits):
    """Clamp a single pose; previous selects continuous hinge/twist angles."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    if joint in (15,20,21):
        return np.array([0.,0.,0.,1.])
    limit = limits.get(joint)
    if limit is None:
        return np.asarray(quaternion)
    if 'flex' in limit:
        hinge = np.asarray(limit['axis'])
        _, angle = rotation_parts(quaternion,hinge)
        if previous is not None:
            _, old = rotation_parts(previous,hinge)
            angle = old + (angle-old+np.pi)%(2*np.pi)-np.pi
        return Rotation.from_rotvec(hinge*np.clip(angle,np.deg2rad(limit.get('flex_min',0.)),np.deg2rad(limit['flex']))).as_quat()
    swing, twist = rotation_parts(quaternion,axis)
    if previous is not None:
        _, old = rotation_parts(previous,axis)
        twist = old + (twist-old+np.pi)%(2*np.pi)-np.pi
    vector = swing.as_rotvec()
    maximum = np.deg2rad(limit.get('swing',180.))
    minimum = np.deg2rad(limit.get('swing_min',0.))
    length = np.linalg.norm(vector)
    if length > maximum:
        vector *= maximum/length
    elif length < minimum:
        if length > 1e-8:
            vector *= minimum/length
        else:
            # At rest there is no bend direction. Prefer the previous pose's
            # direction; otherwise use a stable perpendicular to the bone.
            vector = rotation_parts(previous,axis)[0].as_rotvec() if previous is not None else np.zeros(3)
            if np.linalg.norm(vector) < 1e-8:
                vector = np.cross(axis,np.eye(3)[np.argmin(abs(np.asarray(axis)))])
            vector = vector/np.linalg.norm(vector)*minimum
    twist = np.clip(twist,np.deg2rad(limit.get('twist_min',-limit['twist'])),
                    np.deg2rad(limit.get('twist_max',limit['twist'])))
    return (Rotation.from_rotvec(vector)*Rotation.from_rotvec(np.asarray(axis)*twist)).as_quat()


JOINT_LIMITS = {
    3: dict(swing=35., twist=0., speed=180.),
    6: dict(swing=35., twist=0., speed=180.),
    9: dict(swing=45., twist=40., speed=240.),
    4: dict(flex=165., axis=(1., 0., 0.)),
    5: dict(flex=165., axis=(1., 0., 0.)),
    18: dict(flex=155., axis=(0., -1., 0.)),
    19: dict(flex=155., axis=(0., 1., 0.)),
    7: dict(swing=85., twist=0., speed=540.),
    8: dict(swing=85., twist=0., speed=540.),
    12: dict(swing=70., twist=0., speed=360.),
}

# Interactive pose inspection permits authored twist, unlike conservative
# reconstruction from positions where neck/foot/lower-spine twist is unknown.
PREVIEW_JOINT_LIMITS = {joint:dict(limit) for joint,limit in JOINT_LIMITS.items()}
for _joint in (3,6,9):
    PREVIEW_JOINT_LIMITS[_joint]['twist'] = 90.
for _joint in (1,2):
    # Swing is the half-angle from the resting bone, not the full opening.
    PREVIEW_JOINT_LIMITS[_joint] = dict(swing=60.,twist=UPPER_LIMB_ROLL)
for _joint in (16,17):
    PREVIEW_JOINT_LIMITS[_joint] = dict(swing=80.,twist=UPPER_LIMB_ROLL)
for _joint in (7,8):
    PREVIEW_JOINT_LIMITS[_joint]['swing'] = 25.
    PREVIEW_JOINT_LIMITS[_joint]['twist'] = 35.
PREVIEW_JOINT_LIMITS[12]['twist'] = 60.


def preview_limit_fields(joint, limit=None):
    """Editable degrees for one joint, independent of generation policy."""
    limit = PREVIEW_JOINT_LIMITS[joint] if limit is None else limit
    bend = 'flex' if 'flex' in limit else 'swing'
    return dict(bend_min=limit.get(bend+'_min',0.),bend_max=limit[bend],
                twist_min=limit.get('twist_min',-limit.get('twist',0.)),
                twist_max=limit.get('twist_max',limit.get('twist',0.)))


def preview_limit_from_fields(joint, fields):
    """Validate persisted/user ranges, retaining this joint's anatomical type."""
    import math
    limit = dict(PREVIEW_JOINT_LIMITS[joint])
    defaults = preview_limit_fields(joint)
    bend = 'flex' if 'flex' in limit else 'swing'
    for kind,lower,upper in (('bend',0.,179. if bend=='flex' else 89.),('twist',-179.,179.)):
        if kind == 'twist' and bend == 'flex':
            continue
        try:
            lo,hi = (float(fields.get(kind+suffix,defaults[kind+suffix])) for suffix in ('_min','_max'))
            if not math.isfinite(lo) or not math.isfinite(hi) or lo > hi:
                raise ValueError('Invalid angle range')
            lo,hi = max(lower,min(upper,lo)),max(lower,min(upper,hi))
        except (TypeError,ValueError,OverflowError):
            lo,hi = defaults[kind+'_min'],defaults[kind+'_max']
        if kind == 'bend':
            limit[bend+'_min'],limit[bend] = lo,hi
        else:
            limit['twist_min'],limit['twist_max'] = lo,hi
            limit['twist'] = max(abs(lo),abs(hi))
    return limit
