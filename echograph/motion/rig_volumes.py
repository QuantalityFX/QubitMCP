"""Shared tapered capsule geometry for rig inspection and motion repair.

Radii are in reference-rig units (a .44-unit thigh), scaled with the input.
Connected tissue is excluded; arms collide with the body and the other arm,
and the two legs collide with one another.
"""
import numpy as np
from scipy.spatial.transform import Rotation

from .conversion import JOINT_NAMES, PARENTS
from .rig_limits import clamp_preview_rotation
from .hands import DIGITS, FINGER_NAMES
from .hand_rig import primary_child, end_site_offsets
from echograph.rigging.ik import forward_kinematics

# Preserve the original preview's obstacle order.
VOLUMES = (
    ('pelvis', 0, 3, .115), ('spine1', 3, 9, .14), ('spine3', 9, 12, .12),
    ('neck', 12, 15, .045), ('left_hip', 1, 4, .075), ('right_hip', 2, 5, .075),
    ('left_knee', 4, 7, .05), ('right_knee', 5, 8, .05),
    ('right_shoulder', 17, 19, .04), ('right_elbow', 19, 21, .035), ('head', 15, 15, .09),
    ('left_shoulder', 16, 18, .04), ('left_elbow', 18, 20, .035),
    ('left_wrist', 20, 20, .028), ('right_wrist', 21, 21, .028),
    ('left_ankle', 7, 10, .045), ('right_ankle', 8, 11, .045),
)


def rig_scale(offsets):
    return max(1e-6, float(np.mean(np.linalg.norm(np.asarray(offsets)[[4, 5]], axis=1))) / .44)


def volume_settings(saved=None):
    result = {name: [radius, radius] for name, _, _, radius in VOLUMES}
    for side in ('left','right'):
        result[side+'_wrist'] = [.022,.028]
        result[side+'_ankle'] = [.045,.035]
        for digit in DIGITS:
            radius = .011 if digit == 'thumb' else (.008 if digit == 'pinky' else .0095)
            for segment in (1,2,3):
                result[f'{side}_{digit}{segment}'] = [radius*(1.-.12*(segment-1)),radius*(1.-.12*segment)]
    for name, value in (saved or {}).items():
        if name not in result:
            continue
        try:
            radii = np.asarray(value, dtype=float)
            if radii.shape == (2,) and np.isfinite(radii).all() and (radii > 0).all():
                result[name] = np.clip(radii, .001, 1.).tolist()
        except (TypeError, ValueError):
            pass
    return result


def capsules(points, scale, settings=None, *, world=None, offsets=None, names=JOINT_NAMES, parents=PARENTS,end_sites=None):
    model = ColliderModel(names,parents,offsets,settings,end_sites=end_sites)
    starts,ends = model.endpoints(points,world)
    return [(name,a,b,r*scale) for name,a,b,r in zip(model.names,starts,ends,model.radii)]


def tapered_clearances(a, b, ra, rb, c, d, rc, rd):
    """Signed distances between batches of convex hulls of endpoint spheres.

    Minimize center distance minus interpolated radii on two bounded segments.
    Test the four exact boundary minima and the interior stationary point.
    """
    a, b, c, d = (np.asarray(x, dtype=float) for x in (a, b, c, d))
    ra, rb, rc, rd = (np.asarray(x, dtype=float) for x in (ra, rb, rc, rd))
    u, v = b-a, d-c

    def coordinate(origin, direction, radius_delta):
        length = np.linalg.norm(direction, axis=1)
        parallel = np.einsum('ij,ij->i', origin, direction) / np.maximum(length, 1e-12)
        perpendicular2 = np.maximum(0., np.einsum('ij,ij->i', origin, origin)-parallel**2)
        shift = radius_delta*np.sqrt(perpendicular2/np.maximum(length**2-radius_delta**2, 1e-20))
        value = np.clip((shift-parallel)/np.maximum(length, 1e-12), 0, 1)
        value = np.where(radius_delta >= length, 1., value)
        return np.where(radius_delta <= -length, 0., value)

    def gap(s,t):
        return np.linalg.norm(a+s[:,None]*u-c-t[:,None]*v,axis=1)-(ra+s*(rb-ra)+rc+t*(rd-rc))

    candidates = []
    for end in (0.,1.):
        fixed = np.full(len(a),end)
        candidates.append(gap(fixed,coordinate(c-a-end*u,v,rd-rc)))
        candidates.append(gap(coordinate(a-c-end*v,u,rb-ra),fixed))
    # D = [u,-v]; solve D.T*x = radius_gradient * |x|.
    r = a-c
    aa = np.einsum('ij,ij->i',u,u)
    bb = -np.einsum('ij,ij->i',u,v)
    cc = np.einsum('ij,ij->i',v,v)
    det = aa*cc-bb*bb
    safe = np.maximum(det,1e-20)
    ru = np.einsum('ij,ij->i',r,u)
    rv = -np.einsum('ij,ij->i',r,v)
    s0,t0 = (bb*rv-cc*ru)/safe,(bb*ru-aa*rv)/safe
    es,et = (cc*(rb-ra)-bb*(rd-rc))/safe,(aa*(rd-rc)-bb*(rb-ra))/safe
    k = (rb-ra)*es+(rd-rc)*et
    perpendicular = r+s0[:,None]*u-t0[:,None]*v
    length = np.sqrt(np.einsum('ij,ij->i',perpendicular,perpendicular)/np.maximum(1-k,1e-20))
    s,t = s0+es*length,t0+et*length
    valid = (det > 1e-12*aa*cc) & (det > 1e-20) & (k < 1) & (s >= 0) & (s <= 1) & (t >= 0) & (t <= 1)
    candidates.append(np.where(valid,gap(s,t),np.inf))
    return np.min(candidates,axis=0)


# (volume A, volume B, beginning of A). Shoulders share tissue with chest.
def _pairs():
    rows = []
    for side, other in (('left', 'right'), ('right', 'left')):
        obstacles = ['pelvis', 'spine1', 'spine3', 'neck', 'head',
                     'left_hip', 'right_hip', 'left_knee', 'right_knee',
                     'left_ankle', 'right_ankle',
                     other+'_shoulder', other+'_elbow', other+'_wrist']
        for part in ('shoulder', 'elbow', 'wrist'):
            for obstacle in obstacles:
                start = (.45 if obstacle in ('pelvis', 'spine1', 'spine3') else .25) if part == 'shoulder' else 0.
                rows.append((side+'_'+part, obstacle, start))
        for obstacle in ('pelvis', 'spine1', 'spine3', 'neck', 'head'):
            rows.append((side+'_ankle', obstacle, 0.))
    for left in ('left_hip', 'left_knee', 'left_ankle'):
        for right in ('right_hip', 'right_knee', 'right_ankle'):
            rows.append((left, right, 0.))
    return rows


PAIRS = _pairs()


class ColliderModel:
    """Capsule endpoints follow actual joints, including palms and fingertips.

    Hand radii use the same reference units as body radii. Palm length follows
    the metacarpal roots. Missing terminal tip joints use the distal phalanx's
    length; no new joints or animation tracks are added to the source rig.
    """
    def __init__(self,names=JOINT_NAMES,parents=PARENTS,offsets=None,settings=None,*,end_sites=None,
                 reuse_unchanged_pairs=False):
        end_sites = end_site_offsets(end_sites)
        self.joint_names = tuple(names)
        self.parents = tuple(parents)
        self.offsets = None if offsets is None else np.asarray(offsets,dtype=float)
        self.names = [name for name,_,_,_ in VOLUMES]
        starts = [a for _,a,_,_ in VOLUMES]
        ends = [b for _,_,b,_ in VOLUMES]
        local_ends = [np.zeros(3) for _ in VOLUMES]
        lookup = {name:i for i,name in enumerate(names)}
        self.hand_joints = {side:[j for j,name in enumerate(names) if name in FINGER_NAMES and name.startswith(side+'_')]
                            for side in ('left','right')}
        for side,sign in (('left',1.),('right',-1.)):
            wrist = lookup[side+'_wrist']
            row = self.names.index(side+'_wrist')
            bases = [lookup[f'{side}_{digit}1'] for digit in DIGITS if digit != 'thumb' and f'{side}_{digit}1' in lookup]
            if bases and offsets is not None:
                local_ends[row] = np.mean(np.asarray(offsets)[bases],axis=0)
            else:
                hand_scale = np.linalg.norm(offsets[wrist])/.25 if offsets is not None else 1.
                local_ends[row] = np.array([sign*.07*hand_scale,0.,0.])
            for j in self.hand_joints[side]:
                name = names[j]
                segment = int(name[-1])
                child_name = name[:-1]+str(segment+1) if segment < 3 else name[:-1]+'_tip'
                child = lookup.get(child_name)
                if child is not None and parents[child] != j:
                    raise ValueError('Unexpected fingertip hierarchy at '+child_name)
                if child is None:
                    if segment < 3:
                        raise ValueError('Missing finger segment: '+child_name)
                    # A BVH may omit explicit tip joints. Keep a distal collider.
                    fallback = np.asarray(offsets[j])*.75 if offsets is not None else np.array([sign*.02,0.,0.])
                    end_offset = np.asarray(end_sites.get(name,fallback))
                    child = j
                else:
                    end_offset = np.zeros(3)
                self.names.append(name)
                starts.append(j)
                ends.append(child)
                local_ends.append(end_offset)
        self.starts,self.ends = np.array(starts),np.array(ends)
        self.local_ends = np.asarray(local_ends)
        radii = volume_settings(settings)
        self.radii = np.array([radii[name] for name in self.names])
        pairs = list(PAIRS)
        fingers = [n for n in self.names if n in FINGER_NAMES]
        for name in fingers:
            side = name.split('_')[0]
            for obstacle,_,_,_ in VOLUMES:
                if obstacle == side+'_wrist' and name.endswith('1'):
                    continue  # Shared palm/knuckle tissue.
                pairs.append((name,obstacle,0.))
        for i,name in enumerate(fingers):
            for other in fingers[i+1:]:
                # Adjacent phalanges share caps, as do neighboring knuckle roots.
                if name[:-1] == other[:-1] and abs(int(name[-1])-int(other[-1])) <= 1:
                    continue
                if name.split('_')[0] == other.split('_')[0] and name.endswith('1') and other.endswith('1'):
                    continue
                # The thumb/index web shares tissue in the preview hand.
                same_hand = name.split('_')[0] == other.split('_')[0]
                web = {name.split('_')[1],other.split('_')[1]} == {'index1','thumb2'}
                if same_hand and web:
                    continue
                pairs.append((name,other,0.))
        self.pairs = pairs
        volume_lookup = {name:i for i,name in enumerate(self.names)}
        self.a = np.array([volume_lookup[a] for a,_,_ in pairs])
        self.b = np.array([volume_lookup[b] for _,b,_ in pairs])
        self.trim = np.array([s for _,_,s in pairs])
        self.ra = self.radii[self.a,0]+self.trim*(self.radii[self.a,1]-self.radii[self.a,0])
        # Solver-local cache: finite-difference IK repeatedly tests pairs whose
        # endpoint geometry is identical. Clearance is applied after the cached
        # signed distance, so changing a contact margin cannot reuse a stale result.
        self._clearance_geometry = np.full((len(pairs),16),np.nan) if reuse_unchanged_pairs else None
        self._clearance_values = np.empty(len(pairs)) if reuse_unchanged_pairs else None

    def endpoints(self,points,world=None):
        points = np.asarray(points)
        starts,ends = points[self.starts],points[self.ends].copy()
        if world is not None:
            ends += np.einsum('nij,nj->ni',np.asarray(world)[self.ends],self.local_ends)
        else:
            # Position-only callers can infer extension along the preceding bone.
            for row,joint in enumerate(self.ends):
                if np.linalg.norm(self.local_ends[row]) == 0:
                    continue
                parent = self.parents[joint]
                direction = points[joint]-points[parent]
                ends[row] += direction/max(np.linalg.norm(direction),1e-8)*np.linalg.norm(self.local_ends[row])
        return starts,ends

    def penetrations(self,points,world=None,mask=None,*,clearance=0.):
        starts,ends = self.endpoints(points,world)
        select = slice(None) if mask is None else mask
        a,b,trim = self.a[select],self.b[select],self.trim[select]
        start = starts[a]+trim[:,None]*(ends[a]-starts[a])
        ra,rb,rc,rd = self.ra[select],self.radii[a,1],self.radii[b,0],self.radii[b,1]
        # Exact distance is only needed where capsule bounding boxes overlap.
        ar,br = np.maximum(ra,rb)[:,None]+clearance,np.maximum(rc,rd)[:,None]
        near = np.all((np.minimum(start,ends[a])-ar <= np.maximum(starts[b],ends[b])+br) &
                      (np.minimum(starts[b],ends[b])-br <= np.maximum(start,ends[a])+ar),axis=1)
        result = np.zeros(len(a))
        if near.any():
            if self._clearance_geometry is None:
                gaps = tapered_clearances(start[near],ends[a[near]],ra[near],rb[near],
                                          starts[b[near]],ends[b[near]],rc[near],rd[near])
            else:
                pair_ids = np.arange(len(self.a))[select][near]
                geometry = np.column_stack((start[near],ends[a[near]],starts[b[near]],ends[b[near]],
                                            ra[near],rb[near],rc[near],rd[near]))
                changed = np.any(geometry != self._clearance_geometry[pair_ids],axis=1)
                if changed.any():
                    rows = geometry[changed]
                    self._clearance_values[pair_ids[changed]] = tapered_clearances(
                        rows[:,:3],rows[:,3:6],rows[:,12],rows[:,13],
                        rows[:,6:9],rows[:,9:12],rows[:,14],rows[:,15])
                    self._clearance_geometry[pair_ids[changed]] = rows
                gaps = self._clearance_values[pair_ids]
            result[near] = np.maximum(0.,clearance-gaps)
        return result

    def mask_for(self,names):
        names = set(names)
        return np.array([a in names or b in names for a,b,_ in self.pairs])


def penetration_function(settings=None):
    return ColliderModel(settings=settings).penetrations


def constrain_volume_rotation(joint, desired, local, offsets, *, limits=None, volumes=None,
                              names=JOINT_NAMES, parents=PARENTS,end_sites=None):
    """Sweep a live edit and stop at contact, including fast mouse events.

    Existing overlaps (for example after increasing a radius) may decrease,
    but cannot grow. Each accepted step becomes the new contact baseline.
    """
    local = np.asarray(local, dtype=float).copy()
    end_sites = end_site_offsets(end_sites,rig_scale(offsets))
    offsets = np.asarray(offsets, dtype=float)/rig_scale(offsets)
    child = primary_child(joint,names,parents)
    axis = offsets[child]
    axis = axis/np.linalg.norm(axis) if np.linalg.norm(axis) > 1e-8 else np.array([0., 1., 0.])
    current = Rotation.from_matrix(local[joint])
    if not any(p == joint for p in parents) and names[joint] not in FINGER_NAMES:
        return desired
    delta = (Rotation.from_quat(desired)*current.inv()).as_rotvec()
    # Bound descendant travel by the smallest capsule diameter as well as angle.
    radii = volume_settings(volumes)
    active_volumes = [name for name,_,_,_ in VOLUMES]+[name for name in names if name in FINGER_NAMES]
    radius = min(min(radii[name]) for name in active_volumes)
    lengths = np.zeros(len(parents))
    for j,p in enumerate(parents):
        if p == joint or (p >= 0 and lengths[p] > 0):
            lengths[j] = lengths[p]+np.linalg.norm(offsets[j])
    reach = max(.1, float(max(lengths)))
    step_angle = min(np.deg2rad(2.), radius/reach)
    steps = max(1, int(np.ceil(np.linalg.norm(delta)/step_angle)))
    step = delta/steps
    model = ColliderModel(names,parents,offsets,radii,end_sites=end_sites)
    moved_names = [name for name,start,end in zip(model.names,model.starts,model.ends)
                   if lengths[start] > 0 or lengths[end] > 0 or start == joint or end == joint]
    mask = model.mask_for(moved_names)
    if joint == 0 or not mask.any():
        return desired
    def penetration():
        return model.penetrations(*forward_kinematics(local,offsets,parents),mask=mask)
    previous_overlap = penetration()
    for _ in range(steps):
        old = current.as_quat()
        candidate = clamp_preview_rotation(joint, (Rotation.from_rotvec(step)*current).as_quat(),
                                           axis, old, limits=limits)

        def sample(fraction):
            q = Rotation.from_rotvec((Rotation.from_quat(candidate)*current.inv()).as_rotvec()*fraction)*current
            local[joint] = q.as_matrix()
            return q, penetration()

        proposed, overlaps = sample(1.)
        if np.any(overlaps > np.maximum(previous_overlap, 1e-7)+1e-9):
            lo, hi = 0., 1.
            for _ in range(14):
                mid = (lo+hi)/2
                _, overlap = sample(mid)
                if np.any(overlap > np.maximum(previous_overlap, 1e-7)+1e-9):
                    hi = mid
                else:
                    lo = mid
            return sample(lo)[0].as_quat()
        current, previous_overlap = proposed, overlaps
    return current.as_quat()
