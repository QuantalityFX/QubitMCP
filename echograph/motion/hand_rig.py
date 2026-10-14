"""Anatomical hand constraints and canonical body/hand layout adapters."""
import numpy as np

from .conversion import JOINT_NAMES
from .hands import DIGITS, FINGER_NAMES
from .rig_limits import PREVIEW_JOINT_LIMITS, preview_limit_from_fields


def hand_limit_defaults():
    limits = {}
    for side,sign in (('left',1.),('right',-1.)):
        limits[side+'_wrist'] = dict(swing=65.,twist=45.)
        for digit in DIGITS:
            for segment in (1,2,3):
                thumb = digit == 'thumb'
                limit = dict(flex=(60.,80.,80.)[segment-1] if thumb else (90.,110.,80.)[segment-1],
                             axis=(0.,sign,0.) if thumb else (0.,0.,-sign))
                if segment == 1:
                    limit.update(spread_axis=(0.,0.,-sign) if thumb else (0.,sign,0.),
                                 twist=35. if thumb else 20.)
                limits[f'{side}_{digit}{segment}'] = limit
    return limits


HAND_LIMITS = hand_limit_defaults()


def primary_child(joint,names,parents):
    name = names[joint]
    middle = name.split('_')[0]+'_middle1'
    if name.endswith('_wrist') and middle in names:
        return names.index(middle)
    return next((i for i,p in enumerate(parents) if p == joint),joint)


def end_site_offsets(saved,scale=1.):
    """Read nonzero BVH End Site offsets, excluding empty exporter markers."""
    result = {}
    for name,values in (saved.items() if isinstance(saved,dict) else ()):
        try:
            vector = np.asarray(values,dtype=float).reshape(-1,3)[0]
            if np.isfinite(vector).all() and np.linalg.norm(vector) > 1e-8:
                result[name] = (vector/scale).tolist()
        except (ValueError,TypeError,IndexError):
            continue
    return result


def named_limit_default(name):
    if name in HAND_LIMITS:
        return dict(HAND_LIMITS[name])
    if name in JOINT_NAMES:
        return dict(PREVIEW_JOINT_LIMITS.get(JOINT_NAMES.index(name),{}))
    return {}


def hand_limits_from_fields(saved=None):
    saved = saved if isinstance(saved,dict) else {}
    return {name:preview_limit_from_fields(None,saved.get(name,{}) if isinstance(saved.get(name),dict) else {},
                                          defaults=limit) for name,limit in HAND_LIMITS.items()}


def full_rig_layout(skeleton):
    """Body first, then remaining joints in parent order; never reorder the asset."""
    lookup = {joint.name:i for i,joint in enumerate(skeleton.joints)}
    indices = [lookup[name] for name in JOINT_NAMES]
    indices += [i for i in range(len(skeleton.joints)) if i not in indices]
    mapped = {old:new for new,old in enumerate(indices)}
    names = tuple(skeleton.joints[i].name for i in indices)
    parents = tuple(mapped.get(skeleton.joints[i].parent_index,-1) for i in indices)
    offsets = np.array([skeleton.joints[i].local_bind.translation for i in indices],dtype=float)
    if any(p >= j for j,p in enumerate(parents)):
        raise ValueError('Rig Limiter requires parents before children.')
    # Named hand joints must use the exported wrist -> 1 -> 2 -> 3 topology.
    for j,name in enumerate(names):
        if name not in FINGER_NAMES:
            continue
        expected = name.rsplit('_',1)[0]+'_wrist' if name[-1]=='1' else name[:-1]+str(int(name[-1])-1)
        if parents[j] < 0 or names[parents[j]] != expected:
            raise ValueError('Unexpected finger hierarchy at '+name)
    return indices,names,parents,offsets


def expanded_limits(names,body_limits,hand_limits=None):
    result = {j:dict(limit) for j,limit in body_limits.items()}
    hand_limits = hand_limits_from_fields() if hand_limits is None else hand_limits
    for j,name in enumerate(names):
        if name in FINGER_NAMES or (name in ('left_wrist','right_wrist') and any(
                n in FINGER_NAMES and n.startswith(name.split('_')[0]+'_') for n in names)):
            result[j] = dict(hand_limits.get(name,HAND_LIMITS[name]))
    return result
