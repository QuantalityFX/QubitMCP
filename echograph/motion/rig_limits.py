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


def clamp_preview_rotation(joint, quaternion, axis, previous=None):
    """Project an edit into the limits, sweeping large drag steps to avoid flips."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    if previous is None or joint not in PREVIEW_JOINT_LIMITS:
        return _project_preview_rotation(joint, quaternion, axis, previous)
    # A fast mouse event can cross the forbidden region and end on the other
    # side of the cone. Apply its delta in small steps from the accepted pose,
    # projecting each step so an overshoot cannot tunnel through a limit.
    start = Rotation.from_quat(previous)
    delta = (Rotation.from_quat(quaternion)*start.inv()).as_rotvec()
    steps = max(1,int(np.ceil(np.linalg.norm(delta)/np.deg2rad(5.))))
    step = Rotation.from_rotvec(delta/steps)
    current = _project_preview_rotation(joint,previous,axis)
    for _ in range(steps):
        desired = (step*Rotation.from_quat(current)).as_quat()
        current = _project_preview_rotation(joint,desired,axis,current)
    return current


def _project_preview_rotation(joint, quaternion, axis, previous=None):
    """Clamp a single pose; previous selects continuous hinge/twist angles."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    if joint in (15,20,21):
        return np.array([0.,0.,0.,1.])
    limit = PREVIEW_JOINT_LIMITS.get(joint)
    if limit is None:
        return np.asarray(quaternion)
    if 'flex' in limit:
        hinge = np.asarray(limit['axis'])
        _, angle = rotation_parts(quaternion,hinge)
        if previous is not None:
            _, old = rotation_parts(previous,hinge)
            angle = old + (angle-old+np.pi)%(2*np.pi)-np.pi
        return Rotation.from_rotvec(hinge*np.clip(angle,0,np.deg2rad(limit['flex']))).as_quat()
    swing, twist = rotation_parts(quaternion,axis)
    if previous is not None:
        _, old = rotation_parts(previous,axis)
        twist = old + (twist-old+np.pi)%(2*np.pi)-np.pi
    vector = swing.as_rotvec()
    maximum = np.deg2rad(limit.get('swing',180.))
    length = np.linalg.norm(vector)
    if length > maximum:
        vector *= maximum/length
    twist = np.clip(twist,-np.deg2rad(limit['twist']),np.deg2rad(limit['twist']))
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
    PREVIEW_JOINT_LIMITS[_joint]['twist'] = 35.
PREVIEW_JOINT_LIMITS[12]['twist'] = 60.
