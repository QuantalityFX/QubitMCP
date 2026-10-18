"""Portable per-take animation edits and bounded IK overrides."""
from copy import deepcopy
from bisect import bisect_right
from collections import OrderedDict
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from echograph.rigging.fbx_canonical import AnimationClip, SkeletonAsset, JointAnimationTrack, QuatKeyframe, Vec3Keyframe
from echograph.rigging.fbx_stage5_evaluator import (
    evaluate_rig_at_time, replace_clip_tracks, override_rotation_keys,
    _sample_vec3_channel, _sample_quat_channel,
)
from .rig_repair_cache import file_signature
from .curve_interpolation import curve_value


def sidecar_path(source):
    source = Path(source)
    return source.with_suffix(source.suffix.lower()+'.animation_correction.json')


def clip_signature(clip):
    motion = clip.to_dict()
    motion.pop('name',None)
    motion.pop('metadata',None)
    return hashlib.sha256(json.dumps(motion,sort_keys=True).encode()).hexdigest()


def read_document(source):
    path = sidecar_path(source)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data,dict) or data.get('version') not in (1,2)
            or not isinstance(data.get('keys',{}),dict) or not isinstance(data.get('tracks',{}),dict)
            or not isinstance(data.get('fk_keys',{}),dict)
            or not isinstance(data.get('ik_spans',[]),list)):
        raise ValueError('Unsupported animation correction file: '+str(path))
    if data.get('source_sha') != file_signature(source):
        return None
    return data


def _write_json_atomic(path, document):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                         prefix='.animation-correction-',suffix='.tmp',delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document,stream,indent=2,allow_nan=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_document(source, document):
    if document['source_sha'] != file_signature(source):
        raise ValueError('The source animation changed. Reopen the correction editor before saving.')
    _write_json_atomic(sidecar_path(source),dict(document,version=document.get('version',1)))


def timeline_range_path(source):
    return sidecar_path(source).with_suffix('.timeline.json')


def read_timeline_range(source, source_sha):
    try:
        saved = json.loads(timeline_range_path(source).read_text(encoding='utf-8'))
        if (isinstance(saved,dict) and saved.get('version') == 1 and saved.get('source_sha') == source_sha
                and isinstance(saved.get('timeline_range'),dict)):
            return saved['timeline_range']
    except (OSError,ValueError):
        pass
    return None


def save_timeline_range(source, source_sha, timeline_range):
    # View preferences do not change motion or require a full draft serialization.
    _write_json_atomic(timeline_range_path(source),dict(version=1,source_sha=source_sha,
                                                       timeline_range=timeline_range))


def exported_path(source, record):
    """Accept only our adjacent, intact animation exports."""
    if not isinstance(record,dict):
        return None
    source = Path(source)
    name = record.get('file','')
    if not isinstance(name,str) or not name or '/' in name or '\\' in name or Path(name).name != name:
        return None
    path = source.parent/name
    if path.resolve() == source.resolve() or path.suffix.lower() not in ('.bvh','.fbx'):
        return None
    try:
        return path if file_signature(path) == record.get('output_sha') else None
    except OSError:
        return None


def approved_record(source,document):
    record = document.get('approved') if document else None
    return record if exported_path(source,record) is not None else None


def generated_files(folder):
    result = set()
    for path in Path(folder).glob('*.animation_correction.json'):
        source = path.with_name(path.name[:-len('.animation_correction.json')])
        try:
            document = read_document(source)
            if document:
                for record in document.get('exports',[])+[document.get('approved')]:
                    output = exported_path(source,record)
                    if output is not None:
                        result.add(output.name.casefold())
        except (OSError,ValueError,TypeError,KeyError):
            continue
    return result


def set_rotation_key(skeleton, clip, keys, joint, time, quaternion):
    """Store a local-space delta relative to the incoming animation at this time."""
    if joint not in skeleton.joint_names or not clip.start_time <= time <= clip.end_time:
        raise ValueError('Select a valid joint and a frame inside this animation.')
    pose = evaluate_rig_at_time(skeleton,clip,float(time),loop=False)
    original = pose.local_transforms[skeleton.joint_names.index(joint)].rotation
    delta = Rotation.from_quat(original).inv()*Rotation.from_quat(quaternion)
    keys.setdefault(joint,{})[format(float(time),'.12g')] = delta.as_quat().tolist()


def _joint_role(name):
    name = re.sub(r'[^a-z0-9]', '', name.lower().rsplit('|',1)[-1].rsplit(':',1)[-1])
    return re.sub(r'(hand|wrist|foot|ankle)[lr]$', r'\1', name)


def limb_chain(skeleton,joint):
    """Hands/wrists and feet/ankles use their two upstream limb joints."""
    name = _joint_role(joint)
    if joint not in skeleton.joint_names or not name.endswith(('hand','wrist','foot','ankle')):
        raise ValueError('Select a hand/wrist or foot/ankle for limb IK.')
    end = skeleton.joint_names.index(joint)
    middle = skeleton.joints[end].parent_index
    # HumanML has a foot beyond the ankle; other BVHs have a hand beyond the
    # wrist. Move that endpoint by solving the hip/knee or shoulder/elbow.
    if middle >= 0 and _joint_role(skeleton.joints[middle].name).endswith(('wrist','ankle')):
        middle = skeleton.joints[middle].parent_index
    start = skeleton.joints[middle].parent_index if middle >= 0 else -1
    if start < 0 or skeleton.joints[start].parent_index < 0:
        raise ValueError('This joint does not have a two-bone limb chain.')
    return start,middle,end


def body_control(skeleton,joint):
    """Identify pelvis and chest controls, including common FBX aliases."""
    role = _joint_role(joint)
    if role in ('pelvis','hips','hip'):
        return 'pelvis'
    if role in ('spine3','spine03','chest'):
        return 'chest'
    if role == 'spine2':
        roles = {_joint_role(name) for name in skeleton.joint_names}
        if 'spine' in roles and not roles.intersection(('spine3','spine03')):
            return 'chest'
    return None


def ik_chain(skeleton,joint):
    kind = body_control(skeleton,joint)
    if kind is None:
        return limb_chain(skeleton,joint)
    end = skeleton.joint_names.index(joint)
    if kind == 'pelvis':
        root = end
        while skeleton.joints[root].parent_index >= 0:
            root = skeleton.joints[root].parent_index
        return (end,) if root == end else (root,end)
    middle = skeleton.joints[end].parent_index
    start = skeleton.joints[middle].parent_index if middle >= 0 else -1
    if start < 0:
        raise ValueError('Chest IK requires two upstream spine joints.')
    return start,middle,end


def limb_endpoints(skeleton):
    result = []
    for name in skeleton.joint_names:
        try:
            _,_,end = limb_chain(skeleton,name)
        except ValueError:
            continue
        parent = skeleton.joints[end].parent_index
        if not _joint_role(skeleton.joints[parent].name).endswith(('wrist','ankle')):
            result.append(name)
    return result


def ik_span_joints(skeleton,span):
    indices = list(ik_chain(skeleton,span['joint']))
    if body_control(skeleton,span['joint']) and span.get('lock_limbs',False):
        for name in limb_endpoints(skeleton):
            indices.extend(limb_chain(skeleton,name))
    return list(dict.fromkeys(skeleton.joints[index].name for index in indices))


def limb_world_targets(skeleton,pose):
    targets = {}
    for name in limb_endpoints(skeleton):
        matrix = np.asarray(pose.global_matrices[skeleton.joint_names.index(name)]).reshape(4,4)
        scale = np.linalg.norm(matrix[:3,:3],axis=0)
        targets[name] = dict(target=matrix[:3,3].tolist(),
                            rotation=Rotation.from_matrix(matrix[:3,:3]/scale).as_quat().tolist())
    return targets


def default_ik_pole(skeleton,pose,joint):
    """Place the pole beyond the elbow/knee in its current bend plane."""
    start,middle,end = ik_chain(skeleton,joint)
    matrices = np.asarray(pose.global_matrices).reshape(-1,4,4)
    a,b,c = matrices[[start,middle,end],:3,3]
    direction = c-a
    direction /= max(np.linalg.norm(direction),1e-10)
    bend = b-a-direction*np.dot(b-a,direction)
    if np.linalg.norm(bend) < 1e-8:
        axes = matrices[start,:3,:3]
        reference = axes[:,np.argmin(np.abs(axes.T@direction))]
        bend = reference-direction*np.dot(reference,direction)
    bend /= max(np.linalg.norm(bend),1e-10)
    length = max(np.linalg.norm(b-a)+np.linalg.norm(c-b),1e-6)
    return b+bend*length*.65


def span_ik_pole(skeleton,clip,span,time):
    """Interpolate authored poles, deriving a boundary for legacy IK keys."""
    first,last = span['start'],span.get('end',span['start'])
    if not any('pole' in key for key in (first,last)):
        return None
    poles = []
    for key in (first,last):
        pole = key.get('pole')
        if pole is None:
            posed = override_pose(skeleton,clip,key['time'],key['rotations'],key.get('translations'))
            pole = default_ik_pole(skeleton,evaluate_rig_at_time(skeleton,posed,key['time'],loop=False),span['joint'])
        poles.append(np.asarray(pole,dtype=float))
    alpha = (time-first['time'])/(last['time']-first['time']) if last['time'] > first['time'] else 0.
    return (1-alpha)*poles[0]+alpha*poles[1]


def body_reference_frames(skeleton,clip,pose,time):
    """Remember which body corrections were already present when a target was keyed."""
    source = evaluate_rig_at_time(skeleton,clip,time,loop=False)
    return {name:(np.asarray(posed).reshape(4,4)@np.linalg.inv(np.asarray(original).reshape(4,4))).ravel().tolist()
            for name,posed,original in zip(skeleton.joint_names,pose.global_matrices,source.global_matrices)
            if body_control(skeleton,name)}


def _blend_body_frame(first,last,alpha):
    first,last = np.asarray(first).reshape(4,4),np.asarray(last).reshape(4,4)
    result = np.eye(4)
    result[:3,:3] = Slerp([0.,1.],Rotation.from_matrix([first[:3,:3],last[:3,:3]]))([alpha]).as_matrix()[0]
    result[:3,3] = (1-alpha)*first[:3,3]+alpha*last[:3,3]
    return result


def _follow_body_target(skeleton,source,current,spans,span,time,alpha,target):
    """Unlocked body edits carry existing child IK handles along with the body."""
    kind = body_control(skeleton,span['joint'])
    if kind == 'pelvis':
        return target
    arm = _joint_role(span['joint']).endswith(('hand','wrist'))
    drivers = []
    for other in spans:
        driver_kind = body_control(skeleton,other['joint'])
        if driver_kind != 'pelvis' and not (driver_kind == 'chest' and kind is None and arm):
            continue
        if other['start']['time']-1e-8 <= time <= other.get('end',other['start'])['time']+1e-8:
            drivers.append(other)
    if not drivers:
        return target
    driver = max(drivers,key=lambda entry:body_control(skeleton,entry['joint']) == 'chest')
    if kind is None and driver.get('lock_limbs',False):
        return target
    name = driver['joint']
    first,last = span['start'],span.get('end',span['start'])
    identity = np.eye(4).ravel().tolist()
    authored = _blend_body_frame(first.get('body_frames',{}).get(name,identity),
                               last.get('body_frames',{}).get(name,identity),alpha)
    index = skeleton.joint_names.index(name)
    before = evaluate_rig_at_time(skeleton,source,time,loop=False)
    after = evaluate_rig_at_time(skeleton,current,time,loop=False)
    delta = (np.asarray(after.global_matrices[index]).reshape(4,4)
             @np.linalg.inv(np.asarray(before.global_matrices[index]).reshape(4,4))@np.linalg.inv(authored))
    return (delta@np.r_[target,1.])[:3]


def ik_pole_at_time(skeleton,source,current,spans,joint,time,*,pose=None):
    for span in reversed(spans):
        if span['joint'] != joint:
            continue
        first,last = span['start'],span.get('end',span['start'])
        if first['time']-1e-8 <= time <= last['time']+1e-8:
            pole = span_ik_pole(skeleton,source,span,time)
            if pole is not None:
                alpha = (time-first['time'])/(last['time']-first['time']) if last['time'] > first['time'] else 0.
                return _follow_body_target(skeleton,source,current,spans,span,time,alpha,pole)
    pose = pose or evaluate_rig_at_time(skeleton,current,time,loop=False)
    return default_ik_pole(skeleton,pose,joint)


def override_pose(skeleton,clip,time,rotations,translations=None):
    sampler = getattr(clip,'_runtime_sample_clip',None)
    if sampler is not None:
        clip = sampler(float(time))
    result = override_rotation_keys(skeleton,clip,{name:{time:value} for name,value in rotations.items()})
    if translations:
        originals = {track.joint_name:track for track in result.tracks}
        tracks = {}
        for name,value in translations.items():
            original = originals.get(name,JointAnimationTrack(name))
            keys = [key for key in original.translation_keys if abs(key.time-time) >= 1e-8]
            keys.append(Vec3Keyframe(float(time),tuple(value)))
            tracks[name] = replace(original,translation_keys=sorted(keys,key=lambda key:key.time))
        result = replace_clip_tracks(skeleton,result,tracks)
    return result


def solve_ik_pose(skeleton,clip,joint,time,target,*,seed=None,translations=None,locks=None,pole=None):
    """Solve a body/limb control and optionally pin limb positions and orientations."""
    kind = body_control(skeleton,joint)
    if kind is None:
        solved,error = solve_limb_pose(skeleton,clip,joint,time,target,seed=seed,pole=pole)
        return {**(seed or {}),**solved},{},error
    target = np.asarray(target,dtype=float)
    if target.shape != (3,) or not np.all(np.isfinite(target)):
        raise ValueError('An IK target must contain three finite coordinates.')
    chain = ik_chain(skeleton,joint)
    names = ik_span_joints(skeleton,dict(joint=joint,lock_limbs=bool(locks)))
    pose = evaluate_rig_at_time(skeleton,clip,time,loop=False)
    rotations = {name:pose.local_transforms[skeleton.joint_names.index(name)].rotation for name in names}
    rotations.update(seed or {})
    moved = dict(translations or {})
    working = override_pose(skeleton,clip,time,rotations,moved)
    if kind == 'pelvis':
        pose = evaluate_rig_at_time(skeleton,working,time,loop=False)
        root = chain[0]
        position = np.asarray(pose.global_matrices[chain[-1]]).reshape(4,4)[:3,3]
        moved[skeleton.joints[root].name] = np.asarray(pose.local_transforms[root].translation)+target-position
        error = 0.
    else:
        solved,error = _solve_two_bone_pose(skeleton,working,chain,time,target,seed=rotations,pole=pole)
        rotations.update(solved)
    working = override_pose(skeleton,clip,time,rotations,moved)
    for name,lock in (locks or {}).items():
        solved,limb_error = solve_limb_pose(skeleton,working,name,time,lock['target'],seed=rotations)
        rotations.update(solved)
        working = override_pose(skeleton,working,time,solved)
        pose = evaluate_rig_at_time(skeleton,working,time,loop=False)
        parent = skeleton.joints[skeleton.joint_names.index(name)].parent_index
        matrix = np.asarray(pose.global_matrices[parent]).reshape(4,4)[:3,:3]
        parent_rotation = Rotation.from_matrix(matrix/np.linalg.norm(matrix,axis=0))
        rotations[name] = (parent_rotation.inv()*Rotation.from_quat(lock['rotation'])).as_quat()
        working = override_pose(skeleton,working,time,{name:rotations[name]})
        error = max(error,limb_error)
    return rotations,moved,error


def solve_limb_pose(skeleton,clip,joint,time,target,*,seed=None,pole=None):
    return _solve_two_bone_pose(skeleton,clip,limb_chain(skeleton,joint),time,target,seed=seed,pole=pole)


def _solve_two_bone_pose(skeleton,clip,chain,time,target,*,seed=None,pole=None):
    from echograph.rigging.ik import solve_constrained_ik
    start,middle,end = chain
    evaluation = evaluate_rig_at_time(skeleton,clip,time,loop=False)
    local = evaluation.local_transforms
    target = np.asarray(target,dtype=float)
    if target.shape != (3,) or not np.all(np.isfinite(target)):
        raise ValueError('An IK target must contain three finite coordinates.')
    if pole is not None:
        pole = np.asarray(pole,dtype=float)
        if pole.shape != (3,) or not np.all(np.isfinite(pole)):
            raise ValueError('An IK pole must contain three finite coordinates.')
    if seed:
        for name,quaternion in seed.items():
            local[skeleton.joint_names.index(name)].rotation = tuple(quaternion)
    offsets = np.array([t.translation for t in local],dtype=float)
    scales = np.asarray([t.scale for t in local],dtype=float)
    if np.any(scales <= 0) or not np.allclose(scales,scales[:,:1],atol=1e-8,rtol=1e-7):
        raise ValueError('Arm / leg IK requires uniform bone scales.')
    # FBX skeletons commonly carry a uniform unit conversion on the root.
    # Bake ancestor scale into FK offsets while keeping the authored scales.
    accumulated = np.ones(len(local))
    for index,joint_data in enumerate(skeleton.joints):
        parent_scale = accumulated[joint_data.parent_index] if joint_data.parent_index >= 0 else 1.
        offsets[index] *= parent_scale
        accumulated[index] = parent_scale*scales[index,0]
    scale = max(float(np.linalg.norm(offsets[middle])+np.linalg.norm(offsets[end])),1e-8)
    rotations = Rotation.from_quat([t.rotation for t in local]).as_matrix()
    if skeleton.joints[end].parent_index == middle:
        # An analytic two-bone solve keeps dragging and interval baking fast.
        # The seeded elbow/knee defines the bend plane; source rotations inside
        # an override interval never influence that plane.
        world_rotations,positions = [],[]
        for index,data in enumerate(skeleton.joints):
            parent = data.parent_index
            parent_rotation = world_rotations[parent] if parent >= 0 else Rotation.identity()
            positions.append((positions[parent] if parent >= 0 else np.zeros(3))+parent_rotation.apply(offsets[index]))
            world_rotations.append(parent_rotation*Rotation.from_matrix(rotations[index]))
        a,b,c = (positions[index] for index in (start,middle,end))
        upper,lower = np.linalg.norm(b-a),np.linalg.norm(c-b)
        if min(upper,lower) <= 1e-10:
            raise ValueError('IK requires two nonzero bone lengths.')
        direction = target-a
        distance = np.linalg.norm(direction)
        direction = direction/distance if distance > 1e-10 else (c-a)/max(np.linalg.norm(c-a),1e-10)
        if np.linalg.norm(direction) < .5:
            direction = np.array([1.,0.,0.])
        reach = float(np.clip(distance,max(abs(upper-lower),1e-10),upper+lower))
        reference = pole-a if pole is not None else b-a
        bend = reference-direction*np.dot(reference,direction)
        if np.linalg.norm(bend) < 1e-8:
            bend = b-a-direction*np.dot(b-a,direction)
        if np.linalg.norm(bend) < 1e-8:
            reference = world_rotations[start].as_matrix()[:,np.argmin(np.abs(world_rotations[start].as_matrix().T@direction))]
            bend = reference-direction*np.dot(reference,direction)
        bend /= np.linalg.norm(bend)
        along = (upper*upper+reach*reach-lower*lower)/(2*reach)
        wanted_middle = a+direction*along+bend*np.sqrt(max(0.,upper*upper-along*along))
        wanted_end = a+direction*reach

        def align(before,after):
            before,after = before/np.linalg.norm(before),after/np.linalg.norm(after)
            dot = float(np.clip(np.dot(before,after),-1.,1.))
            if dot < -1+1e-10:
                axis = np.cross(before,np.eye(3)[np.argmin(np.abs(before))])
                return Rotation.from_rotvec(axis/np.linalg.norm(axis)*np.pi)
            return Rotation.from_quat(np.r_[np.cross(before,after),1+dot])

        delta = align(b-a,wanted_middle-a)
        start_world = delta*world_rotations[start]
        parent = skeleton.joints[start].parent_index
        parent_world = world_rotations[parent] if parent >= 0 else Rotation.identity()
        middle_world = align(delta.apply(c-b),wanted_end-wanted_middle)*delta*world_rotations[middle]
        return {skeleton.joints[start].name:(parent_world.inv()*start_world).as_quat(),
                skeleton.joints[middle].name:(start_world.inv()*middle_world).as_quat()},float(np.linalg.norm(wanted_end-target))
    seeds = []
    for axis in range(3):
        for sign in (-1,1):
            seed = np.zeros(6)
            seed[axis] = sign*.6
            seeds.append(seed)
    solved = solve_constrained_ik(rotations,offsets/scale,[j.parent_index for j in skeleton.joints],
        joint_axes={start:np.eye(3),middle:np.eye(3)},targets={end:np.asarray(target,dtype=float)/scale},
        pose_weight=.001,temporal_weight=0.,tolerance=.0001,max_iterations=60,retry_seeds=seeds,
        prioritize_constraints=True,reuse_unchanged_joints=True)
    return {skeleton.joints[j].name:Rotation.from_matrix(solved.rotations[j]).as_quat()
            for j in (start,middle)},solved.max_error*scale


def apply_document(skeleton, clip, document,*,ik_cache=None):
    """Apply saved FK edits, channel deletions, and bounded IK control ranges."""
    if not document:
        return clip
    result = apply_corrections(skeleton,clip,document.get('keys',{}),document.get('fps',24))
    overrides = document.get('tracks',{})
    tracks = {}
    for name,payload in overrides.items():
        if name not in skeleton.joint_names or payload.get('joint_name') != name:
            raise ValueError('Correction tracks do not match the input skeleton.')
        track = JointAnimationTrack.from_dict(payload)
        if track.translation_keys or track.rotation_keys or track.scale_keys:
            tracks[name] = track
        else:
            tracks[name] = None
    if tracks:
        result = replace_clip_tracks(skeleton,result,tracks)
        result.metadata['animation_correction'] = dict(joints=len(overrides))
    result = apply_ik_spans(skeleton,result,document_ik_spans(document),document.get('fps',clip.sample_rate_hz),cache=ik_cache)
    if document.get('fk_keys'):
        result = override_fk_rotation_keys(skeleton,result,document['fk_keys'],document_fk_rotation_masks(document,clip))
    return override_position_keys(skeleton,result,document_fk_positions(document,clip))


def document_fk_rotation_masks(document,clip):
    """Index sparse FK rotation ownership once per document revision."""
    result = {}
    for joint,saved in document.get('timeline_rows',{}).items():
        rate = float(saved['fps'])
        for frame,row in saved['keys'].items():
            if row.get('fk_key') and 'fk_rotation_axes' in row:
                time = clip.start_time+float(row.get('source_frame',frame))/rate
                mask = [bool(active) and row.get('axis_mask',[True]*6)[axis+3]
                        for axis,active in enumerate(row['fk_rotation_axes'])]
                if not all(mask):
                    result.setdefault(joint,{})[format(time,'.12g')] = mask
    return result


def override_fk_rotation_keys(skeleton,clip,keys,masks=None):
    """Apply authored FK components without replacing unselected IK rotations."""
    if not masks:
        return override_rotation_keys(skeleton,clip,keys) if keys else clip
    originals = {track.joint_name:track for track in clip.tracks}
    merged = {}
    for joint,entries in keys.items():
        track = originals.get(joint)
        for stamp,quaternion in entries.items():
            mask = masks.get(joint,{}).get(format(float(stamp),'.12g'),[True]*3)
            if not any(mask):
                continue
            if not all(mask):
                bind = skeleton.joints[skeleton.joint_names.index(joint)].local_bind.rotation
                source = _sample_quat_channel(track.rotation_keys if track else [],float(stamp),bind)
                angles = Rotation.from_quat(quaternion).as_euler('xyz',degrees=True)
                original = Rotation.from_quat(source).as_euler('xyz',degrees=True)
                quaternion = Rotation.from_euler('xyz',np.where(mask,angles,original),degrees=True).as_quat()
            merged.setdefault(joint,{})[stamp] = quaternion
    return override_rotation_keys(skeleton,clip,merged)


def document_fk_positions(document,clip):
    """Read independent local FK position keys from the existing timeline rows."""
    result = {}
    for joint,saved in document.get('timeline_rows',{}).items():
        rate = float(saved['fps'])
        for frame,row in saved['keys'].items():
            axes = row.get('fk_position_axes',[False]*3)
            mask = [bool(active) and row.get('axis_mask',[True]*6)[axis] for axis,active in enumerate(axes)]
            if any(mask):
                time = clip.start_time+float(row.get('source_frame',frame))/rate
                result.setdefault(joint,{})[time] = dict(value=list(row['xyz']),mask=mask)
    return result


def override_position_keys(skeleton,clip,positions):
    """Insert sparse local translation keys after IK without changing other axes."""
    if not positions:
        return clip
    originals = {track.joint_name:track for track in clip.tracks}
    tracks = {}
    for joint,entries in positions.items():
        if joint not in skeleton.joint_names:
            raise ValueError('FK position keys do not match the input skeleton.')
        original = originals.get(joint,JointAnimationTrack(joint))
        keys = {key.time:key for key in original.translation_keys}
        default = skeleton.joints[skeleton.joint_names.index(joint)].local_bind.translation
        for time,entry in entries.items():
            time = float(time)
            value,mask = np.asarray(entry['value'],dtype=float),np.asarray(entry['mask'],dtype=bool)
            if value.shape != (3,) or mask.shape != (3,) or not np.all(np.isfinite(value)) or not math.isfinite(time):
                raise ValueError('Invalid FK position key.')
            source = _sample_vec3_channel(original.translation_keys,time,tuple(default))
            keys[time] = Vec3Keyframe(time,tuple(np.where(mask,value,source)))
        tracks[joint] = replace(original,translation_keys=[keys[time] for time in sorted(keys)])
    return replace_clip_tracks(skeleton,clip,tracks)


def document_ik_spans(document):
    """Keep the keying clock distinct from the bake and playback rates."""
    spans = deepcopy(document.get('ik_spans',[]))
    rows = document.get('timeline_rows',{})
    for span in spans:
        span.setdefault('keyframe_fps',rows.get(span['joint'],{}).get('fps',document.get('fps',24.)))
    return spans


def reload_timeline_axes(source, joint, rows, axes, fps):
    """Restore source channel keys while retaining edits on the other axes."""
    axes = set(axes)
    restored = {}
    for frame,entry in rows.items():
        row = deepcopy(entry)
        row['axis_mask'] = [bool(active) and axis not in axes
                            for axis,active in enumerate(row.get('axis_mask',[True]*6))]
        row.pop('fk_key',None)  # Retained FK components move into the manual track.
        row.pop('fk_rotation_axes',None)
        if 'fk_position_axes' in row:
            row['fk_position_axes'] = [active and axis not in axes for axis,active in enumerate(row['fk_position_axes'])]
        if axes.intersection((3,4,5)):
            row.pop('rotation_quaternion',None)
        if 'curve_handles' in row:
            row['curve_handles'] = {axis:handle for axis,handle in row['curve_handles'].items()
                                    if int(axis) not in axes}
            if not row['curve_handles']:
                row.pop('curve_handles')
        if any(row['axis_mask']):
            restored[frame] = row
    for frame,source_row in joint_timeline_keys(source,joint,fps).items():
        selected = [axis in axes and bool(active)
                    for axis,active in enumerate(source_row['axis_mask'])]
        if not any(selected):
            continue
        row = restored.setdefault(frame,dict(source_frame=source_row['source_frame'],
            source_time=source_row['source_time'],axis_mask=[False]*6,
            animation_correction_key=True,scene_skeleton_authored=True))
        for axis,active in enumerate(selected):
            if active:
                field = 'xyz' if axis < 3 else 'rxyz'
                row.setdefault(field,list(source_row[field]))[axis%3] = source_row[field][axis%3]
                row['axis_mask'][axis] = True
                interpolation = 'translation_interpolation' if axis < 3 else 'rotation_interpolation'
                row.setdefault(interpolation,source_row.get(interpolation,'linear'))
    return restored


def reload_track_axes(skeleton, source, edited, joint, axes):
    """Merge actual channel samples, including source keys between timeline frames."""
    original = next((track for track in source.tracks if track.joint_name == joint),JointAnimationTrack(joint))
    current = next((track for track in edited.tracks if track.joint_name == joint),JointAnimationTrack(joint))
    result = replace(current)
    bind = skeleton.joints[skeleton.joint_names.index(joint)].local_bind
    for field,offset,sample,default,key_type in (
            ('translation_keys',0,_sample_vec3_channel,bind.translation,Vec3Keyframe),
            ('rotation_keys',3,_sample_quat_channel,bind.rotation,QuatKeyframe)):
        mask = [offset+axis in axes for axis in range(3)]
        if not any(mask):
            continue
        input_keys,current_keys = getattr(original,field),getattr(current,field)
        if all(mask):
            setattr(result,field,deepcopy(input_keys))
            continue
        stamps = sorted({key.time for key in input_keys+current_keys})
        if not stamps:
            continue
        input_values = np.asarray([sample(input_keys,time,tuple(default)) for time in stamps])
        current_values = np.asarray([sample(current_keys,time,tuple(default)) for time in stamps])
        if offset:
            input_values = Rotation.from_quat(input_values).as_euler('xyz',degrees=True)
            current_values = Rotation.from_quat(current_values).as_euler('xyz',degrees=True)
        values = np.where(mask,input_values,current_values)
        if offset:
            values = Rotation.from_euler('xyz',values,degrees=True).as_quat()
        current_times = [key.time for key in current_keys]
        merged = []
        for time,value in zip(stamps,values):
            index = max(0,bisect_right(current_times,time)-1)
            interpolation = current_keys[index].interpolation if current_keys else 'linear'
            merged.append(key_type(time,tuple(value),interpolation))
        setattr(result,field,merged)
    return result


def reload_ik_axes(skeleton, spans, joint, axes):
    """Remove selected IK channels without deleting independent control axes."""
    result = []
    for original in spans:
        names = ik_span_joints(skeleton,original)
        if joint not in names:
            result.append(original)
            continue
        span = deepcopy(original)
        masks = span.setdefault('channel_masks',{})
        mask = masks.setdefault(joint,[True]*3+
            ([False]*3 if joint == span['joint'] and 'position_mask' in original else [True]*3))
        for axis in axes:
            mask[axis] = False
        if joint == span['joint'] and any(axis < 3 for axis in axes):
            position = span.setdefault('position_mask',[True]*3)
            for axis in axes:
                if axis < 3:
                    position[axis] = False
            if not any(position):
                # With no positional control left, keep only the endpoint's
                # independent orientation. Its upstream bones follow input motion.
                span['rotation_only'] = True
                for name in names:
                    if name != joint:
                        masks[name] = [False]*6
        if span.get('rotation_only') and not any(mask[3:]):
            continue
        for boundary in ('start','end'):
            key = span.get(boundary)
            if key is None:
                continue
            handles = key.get('curve_handles',{})
            if joint in handles:
                handles[joint] = {axis:value for axis,value in handles[joint].items() if int(axis) not in axes}
                if not handles[joint]:
                    handles.pop(joint)
                if not handles:
                    key.pop('curve_handles',None)
        result.append(span)
    return result


def ik_position_mask(span):
    mask = span.get('position_mask',[True]*3)
    channels = span.get('channel_masks',{}).get(span['joint'],[True]*6)
    return [bool(active and channels[axis]) for axis,active in enumerate(mask)]


def independent_ik_positions(left,right):
    """Separate target coordinates can control the same chain at the same time."""
    return (left['joint'] == right['joint'] and 'position_mask' in left and 'position_mask' in right
            and not left.get('rotation_only') and not right.get('rotation_only')
            and not any(a and b for a,b in zip(ik_position_mask(left),ik_position_mask(right))))


def ik_timeline_mask(span,joint):
    endpoint = joint == span['joint']
    mask = ([bool(value) and endpoint for value in span['position_mask']]+[not endpoint]*3
            if 'position_mask' in span else [endpoint]*3+[True]*3)
    if 'channel_masks' in span:
        mask[3:] = [True]*3
    return [active and keyed for active,keyed in zip(mask,span.get('channel_masks',{}).get(joint,[True]*6))]


def timeline_ik_axis_keys(row):
    keys = row.get('ik_axis_keys',[row.get('ik_key')]*6)
    return [key if active else None for key,active in zip(keys,row.get('ik_axis_mask',[True]*6))]


def set_timeline_ik_axis_keys(row,keys):
    """Retain individual boundary IDs when independent axes share a frame."""
    ids = {key for key in keys if key}
    if not ids:
        for field in ('ik_key','ik_axis_keys','ik_axis_mask','ik_control'):
            row.pop(field,None)
        return
    row['ik_key'] = next(key for key in keys if key)
    row['ik_axis_mask'] = [bool(key) for key in keys]
    if len(ids) > 1:
        row['ik_axis_keys'] = list(keys)
    else:
        row.pop('ik_axis_keys',None)


def _same_ik_channels(left,right):
    def masks(span):
        # Reloaded/older documents may spell out masks that newer ranges leave
        # implicit. Compare their effective channels, not that storage detail.
        effective = dict(span.get('channel_masks',{}))
        if 'position_mask' in span:
            effective.setdefault(span['joint'],[True]*3+
                                 ([True]*3 if 'channel_masks' in span else [False]*3))
        return {name:tuple(mask) for name,mask in effective.items()
                if mask != [True]*3+([False]*3 if name == span['joint'] and
                                     'position_mask' in span else [True]*3)}
    return left['joint'] == right['joint'] and masks(left) == masks(right) and all(
        left.get(option,default) == right.get(option,default) for option,default in (
            ('position_mask',None),('rotation_only',False),('lock_limbs',False)))


def ik_gap_pair(spans,key_ids):
    """Find compatible neighboring ranges from their selected inner boundaries."""
    selected = set(key_ids)
    if len(selected) != 2:
        raise ValueError('Select an End IK key and the next Start IK key on the same control.')
    pair = next(((left,right) for left in spans if left.get('end',{}).get('id') in selected
                 for right in spans if right is not left and right['start']['id'] in selected
                 and left['end']['id'] != right['start']['id']
                 and left['joint'] == right['joint']
                 and left['end']['time'] < right['start']['time']-1e-8),None)
    if pair is None:
        raise ValueError('Select an End IK key on the left and a Start IK key on the right, on the same control.')
    left,right = pair
    if 'end' not in right:
        raise ValueError('Set the right range’s End IK key before joining the ranges.')
    sections = sorted((span for span in spans if span['joint'] == left['joint']
                       and not independent_ik_positions(span,left)),
                      key=lambda span:span['start']['time'])
    if sections[sections.index(left)+1] is not right:
        raise ValueError('Select boundaries of neighboring IK ranges.')
    if not _same_ik_channels(left,right):
        raise ValueError('The two IK ranges must use the same channels and limb-lock settings.')
    return left,right


def remove_ik_keys(spans,key_ids):
    """Delete keys while retaining the continuous ranges between surviving keys."""
    result = list(spans)
    if not key_ids:
        return result
    times = {key['id']:key['time'] for span in spans
             for key in (span['start'],span.get('end',span['start']))}
    for key_id in sorted(set(key_ids),key=lambda value:times.get(value,0.)):
        left = next((span for span in result if span.get('end',{}).get('id') == key_id),None)
        right = next((span for span in result if span['start']['id'] == key_id),None)
        if left is not None and right is not None and left is not right and _same_ik_channels(left,right):
            merged = dict(left)
            if 'end' in right:
                merged['end'] = right['end']
            else:
                merged.pop('end',None)
            result = [merged if span is left else span for span in result if span is not right]
        else:
            updated = []
            for span in result:
                if not any(span.get(boundary,{}).get('id') == key_id for boundary in ('start','end')):
                    updated.append(span)
                    continue
                remaining = {field:value for field,value in span.items()
                             if field not in ('start','end') or value['id'] != key_id}
                if 'start' not in remaining and 'end' in remaining:
                    remaining['start'] = remaining.pop('end')
                if 'start' in remaining:
                    updated.append(remaining)
            result = updated
    # An outer boundary's deletion can leave a singleton at an existing
    # intermediate key. That key is now the neighboring range's outer boundary,
    # not a new pending Start IK range.
    paired = {(span['joint'],key['id']) for span in result if 'end' in span
              for key in (span['start'],span['end'])}
    return [span for span in result if 'end' in span or (span['joint'],span['start']['id']) not in paired]


def _ik_spans_with_transitions(spans,fps):
    """Interpolate End/Start pairs with no unkeyed editing frame between them."""
    result = list(spans)
    controls = []
    for span in spans:
        sections = next((group for group in controls if _same_ik_channels(group[0],span)),None)
        if sections is None:
            sections = []
            controls.append(sections)
        sections.append(span)
    for sections in controls:
        sections = sorted(sections,key=lambda span:span['start']['time'])
        for left,right in zip(sections,sections[1:]):
            if 'end' not in left:
                continue
            gap = right['start']['time']-left['end']['time']
            rate = min(float(left.get('keyframe_fps',fps)),float(right.get('keyframe_fps',fps)))
            if (1e-8 < gap <= 1./rate+1e-8
                    and left.get('position_mask') == right.get('position_mask')
                    and left.get('channel_masks') == right.get('channel_masks')
                    and left.get('rotation_only') == right.get('rotation_only')
                    and left.get('lock_limbs',False) == right.get('lock_limbs',False)):
                result.append(dict(left,start=left['end'],end=right['start']))
    return result


def scale_curve_handles(handles,scale):
    """Convert horizontal handle lengths between timeline frames and seconds."""
    result = deepcopy(handles)
    for handle in result.values():
        if handle.get('mode') != 'straight':
            for side in ('in','out'):
                vector = handle.get(side)
                if vector is not None:
                    handle[side] = [float(vector[0])*scale,float(vector[1])]
    return result


def _ik_curve_value(first,last,joint,axis,time,start_value,end_value):
    """Use the same tangent interpolation as the curve editor."""
    handles = [key.get('curve_handles',{}).get(joint,{}).get(str(axis)) for key in (first,last)]
    return curve_value(first['time'],last['time'],start_value,end_value,time,*handles)


def validate_ik_spans(skeleton,clip,spans):
    occupied = {}
    for span in spans:
        if 'keyframe_fps' in span and (not math.isfinite(float(span['keyframe_fps'])) or float(span['keyframe_fps']) <= 0):
            raise ValueError('Invalid IK keyframe rate.')
        chain = tuple(skeleton.joints[index].name for index in ik_chain(skeleton,span['joint']))
        affected = ik_span_joints(skeleton,span)
        channel_masks = span.get('channel_masks',{})
        if (not isinstance(channel_masks,dict) or set(channel_masks)-set(affected) or any(
                not isinstance(mask,list) or len(mask) != 6 or
                not all(isinstance(value,bool) for value in mask) for mask in channel_masks.values())):
            raise ValueError('Invalid IK channel masks.')
        mask = span.get('position_mask')
        if mask is not None and (not isinstance(mask,list) or len(mask) != 3
                or not all(isinstance(value,bool) for value in mask)
                or (not any(mask) and not span.get('rotation_only'))):
            raise ValueError('An IK position mask must select at least one of Px, Py or Pz.')
        if 'rotation_only' in span and (span['rotation_only'] is not True or mask != [False]*3):
            raise ValueError('Rotation-only IK must have no positional channels.')
        start = span['start']
        end = span.get('end',start)
        if end['time'] < start['time']:
            raise ValueError('An IK end must be after its start.')
        for key in (start,end):
            time = float(key['time'])
            if not math.isfinite(time) or not clip.start_time-1e-8 <= time <= clip.end_time+1e-8:
                raise ValueError('IK keys must stay inside the input animation.')
            target = np.asarray(key['target'],dtype=float)
            if target.shape != (3,) or not np.all(np.isfinite(target)):
                raise ValueError('Invalid IK target.')
            if 'pole' in key and (body_control(skeleton,span['joint']) == 'pelvis' or
                    np.asarray(key['pole']).shape != (3,) or not np.all(np.isfinite(key['pole']))):
                raise ValueError('Invalid IK pole.')
            if set(key['rotations']) != set(affected):
                raise ValueError('IK keys must contain the full control chain.')
            Rotation.from_quat([key['rotations'][name] for name in affected])
            handles = key.get('curve_handles',{})
            if not isinstance(handles,dict) or set(handles)-set(affected):
                raise ValueError('IK curve handles must belong to the control chain.')
            for channels in handles.values():
                if not isinstance(channels,dict) or set(channels)-set(map(str,range(6))):
                    raise ValueError('Invalid IK curve channel.')
                for handle in channels.values():
                    if not isinstance(handle,dict) or handle.get('mode') not in ('straight','tied','untied'):
                        raise ValueError('Invalid IK curve handle mode.')
                    if handle['mode'] == 'straight':
                        continue
                    for side,sign in (('in',-1),('out',1)):
                        vector = np.asarray(handle.get(side),dtype=float)
                        if vector.shape != (2,) or not np.all(np.isfinite(vector)) or sign*vector[0] < 0:
                            raise ValueError('Invalid IK curve handle.')
                    weights = handle.get('weighted',[])
                    if not isinstance(weights,list) or any(side not in ('in','out') for side in weights):
                        raise ValueError('Invalid IK curve weights.')
            translations = key.get('translations',{})
            expected = {chain[0]} if body_control(skeleton,span['joint']) == 'pelvis' else set()
            if set(translations) != expected or any(np.asarray(value).shape != (3,) or
                    not np.all(np.isfinite(value)) for value in translations.values()):
                raise ValueError('Invalid body IK translations.')
            locks = key.get('locks',{})
            expected = set(limb_endpoints(skeleton)) if span.get('lock_limbs',False) else set()
            if set(locks) != expected:
                raise ValueError('Locked body IK keys must contain every arm/leg target.')
            for lock in locks.values():
                if np.asarray(lock['target']).shape != (3,) or not np.all(np.isfinite(lock['target'])):
                    raise ValueError('Invalid locked IK target.')
                Rotation.from_quat(lock['rotation'])
            for matrix in key.get('body_frames',{}).values():
                if np.asarray(matrix).shape != (16,) or not np.all(np.isfinite(matrix)) or abs(np.linalg.det(np.asarray(matrix).reshape(4,4))) < 1e-10:
                    raise ValueError('Invalid IK body reference frame.')
        for name in chain:
            for lo,hi,other in occupied.get(name,[]):
                if independent_ik_positions(span,other):
                    continue
                overlap = min(hi,end['time'])-max(lo,start['time']) > 1e-8
                pending_inside = start['time'] == end['time'] and lo+1e-8 < start['time'] < hi-1e-8
                contains_pending = lo == hi and start['time']+1e-8 < lo < end['time']-1e-8
                if overlap or pending_inside or contains_pending:
                    raise ValueError('IK override ranges for the same control chain cannot overlap.')
            occupied.setdefault(name,[]).append((start['time'],end['time'],span))


def preview_clip(skeleton,clip,spans,fps,*,fk_keys=None,fk_positions=None,fk_rotation_masks=None,cache=None):
    """Evaluate correction ranges on demand while editing, without baking a take."""
    if not spans:
        result = override_fk_rotation_keys(skeleton,clip,fk_keys,fk_rotation_masks) if fk_keys else clip
        return override_position_keys(skeleton,result,fk_positions)
    validate_ik_spans(skeleton,clip,spans)
    spans,fk_keys = deepcopy(spans),deepcopy(fk_keys or {})
    fk_positions = deepcopy(fk_positions or {})
    fk_rotation_masks = deepcopy(fk_rotation_masks or {})
    # Capture this revision's baked prefix. Later edits must not change an
    # older preview retained by undo/redo.
    baked = dict(cache or {})
    samples = OrderedDict()
    result = override_fk_rotation_keys(skeleton,clip,fk_keys,fk_rotation_masks) if fk_keys else replace_clip_tracks(skeleton,clip,{})
    result = override_position_keys(skeleton,result,fk_positions)
    def sample(time):
        if time in samples:
            samples.move_to_end(time)
            return samples[time]
        value = apply_ik_spans(skeleton,clip,spans,fps,cache=baked,sample_time=time)
        if fk_keys:
            value = override_fk_rotation_keys(skeleton,value,fk_keys,fk_rotation_masks)
        value = override_position_keys(skeleton,value,fk_positions)
        samples[time] = value
        if len(samples) > 16:
            samples.popitem(last=False)
        return value
    result._runtime_sample_clip = sample
    return result


def apply_ik_spans(skeleton,clip,spans,fps,*,cache=None,sample_time=None):
    """Bake bounded IK targets; preserve source channels outside each range.

    The document stores only control boundaries, not these generated samples.
    Interpolated boundary poses seed the bend plane, then each sample solves
    the interpolated target. Pelvis controls also key root translation; locked
    body ranges solve all four limbs against their stored world targets.
    With sample_time, bake only the neighboring samples needed to evaluate
    that instant, using the same interpolation and boundary guards as a full
    bake. The full-bake cache is left unchanged.
    """
    if not spans:
        if cache is not None and sample_time is None:
            cache.clear()
        return clip
    validate_ik_spans(skeleton,clip,spans)
    rate = max(float(fps),float(clip.sample_rate_hz))*2
    if not math.isfinite(rate) or not 0 < rate <= 10000:
        raise ValueError('Invalid IK sampling rate.')
    spans = _ik_spans_with_transitions(spans,fps)
    if sample_time is not None:
        sample_frame = (float(sample_time)-clip.start_time)*rate
        window_lo = clip.start_time+(math.floor(sample_frame)-1)/rate
        window_hi = clip.start_time+(math.ceil(sample_frame)+1)/rate
    result = clip
    # Solve parents before children, then explicit limb targets. Pelvis and
    # chest ranges can coexist with independent hand/foot overrides.
    def order(span):
        kind = body_control(skeleton,span['joint'])
        return ({'pelvis':0,'chest':1}.get(kind,2),span['start']['time'])
    reusable = (cache.get('steps',[]) if cache is not None
                and cache.get('skeleton') is skeleton and cache.get('clip') is clip
                and cache.get('rate') == rate else [])
    steps = []
    baselines = {}
    for index,span in enumerate(sorted(spans,key=order)):
        baseline = baselines.setdefault(span['joint'],result)
        independent = [other for other in spans if other is not span and independent_ik_positions(span,other)
                       and other['start']['time'] <= span.get('end',span['start'])['time']+1e-8
                       and other.get('end',other['start'])['time'] >= span['start']['time']-1e-8]
        if independent:
            # Compose target coordinates over the ranges already evaluated.
            # Restoring every unkeyed coordinate from the original clip would
            # erase Py as soon as a separate Px/Pz range was applied.
            baseline = result
        # Neighboring ranges also control the source samples immediately
        # outside a boundary, even when that neighbor is baked later.
        signature = (span,[(other['start']['time'],other.get('end',other['start'])['time'],other.get('position_mask'))
                           for other in spans if other is not span and other['joint'] == span['joint']])
        # Reuse only an identical prefix: later ranges depend on the results
        # of earlier body and limb controls. Never reuse across source edits.
        if index < len(reusable) and reusable[index][0] == signature:
            result = reusable[index][1]
            steps.append(reusable[index])
            continue
        reusable = []
        if sample_time is not None and (span['start']['time'] > window_hi+1e-9 or
                                       span.get('end',span['start'])['time'] < window_lo-1e-9):
            continue
        joint = span['joint']
        names = ik_span_joints(skeleton,span)
        originals = {track.joint_name:track for track in result.tracks}
        tracks = {}
        first,last = span['start'],span.get('end',span['start'])
        lo,hi = first['time'],last['time']
        curved_rotations = {}
        for name in names:
            channels = {axis for key in (first,last) for axis in key.get('curve_handles',{}).get(name,{})}
            axes = [axis for axis in range(3) if str(axis+3) in channels]
            if axes:
                curved_rotations[name] = (axes,Rotation.from_quat(first['rotations'][name]).as_euler('xyz',degrees=True),
                                          Rotation.from_quat(last['rotations'][name]).as_euler('xyz',degrees=True))
        if sample_time is not None:
            stamps = sorted({lo,hi,*[clip.start_time+frame/rate for frame in range(
                math.ceil((max(lo,window_lo)-clip.start_time)*rate),
                math.floor((min(hi,window_hi)-clip.start_time)*rate)+1)
                if lo+1e-9 < clip.start_time+frame/rate < hi-1e-9]})
        else:
            stamps = [lo]+[clip.start_time+frame/rate for frame in
                range(math.ceil((lo-clip.start_time)*rate),math.floor((hi-clip.start_time)*rate)+1)
                if lo+1e-9 < clip.start_time+frame/rate < hi-1e-9]
            if hi-lo > 1e-9:
                stamps.append(hi)
        if independent:
            # Preserve the other coordinates' boundary guards and subframe
            # keys when replacing the combined vector/quaternion track.
            extra = {stamp+offset for other in independent for boundary in ('start','end')
                     if boundary in other for stamp in (other[boundary]['time'],) for offset in (-1e-7,0.,1e-7)
                     if lo < stamp+offset < hi and
                     (sample_time is None or window_lo <= stamp+offset <= window_hi)}
            stamps = sorted(set(stamps)|extra)
        blends = {name:Slerp([lo,hi],Rotation.from_quat([first['rotations'][name],last['rotations'][name]]))
                  for name in names} if hi-lo > 1e-10 else {}
        lock_blends = {name:Slerp([lo,hi],Rotation.from_quat([value['rotation'],last['locks'][name]['rotation']]))
                       for name,value in first.get('locks',{}).items()} if blends else {}
        baked = {name:[] for name in names}
        moved = {name:[] for name in first.get('translations',{})}
        for time in stamps:
            source_pose = None
            alpha = (time-lo)/(hi-lo) if hi > lo else 0.
            seed = {name:(blends[name]([time]).as_quat()[0] if blends else first['rotations'][name]) for name in names}
            target = (1-alpha)*np.asarray(first['target'])+alpha*np.asarray(last['target'])
            for axis in range(3):
                target[axis] = _ik_curve_value(first,last,joint,axis,time,first['target'][axis],last['target'][axis])
            target = _follow_body_target(skeleton,clip,result,spans,span,time,alpha,target)
            pole = span_ik_pole(skeleton,clip,span,time)
            if pole is not None:
                pole = _follow_body_target(skeleton,clip,result,spans,span,time,alpha,pole)
            translations = {name:(1-alpha)*np.asarray(value)+alpha*np.asarray(last['translations'][name])
                            for name,value in first.get('translations',{}).items()}
            if 'position_mask' in span and not span.get('rotation_only'):
                # The unkeyed target coordinates and bend seed must come from
                # this animated sample, not from interpolated boundary poses.
                pose = evaluate_rig_at_time(skeleton,baseline,time,loop=False)
                source_pose = pose
                endpoint = skeleton.joint_names.index(joint)
                animated_target = np.asarray(pose.global_matrices[endpoint]).reshape(4,4)[:3,3]
                target = np.where(span['position_mask'],target,animated_target)
                seed = {name:pose.local_transforms[skeleton.joint_names.index(name)].rotation for name in names}
                translations = {name:pose.local_transforms[skeleton.joint_names.index(name)].translation
                                for name in translations}
            for name,(axes,start_angles,end_angles) in curved_rotations.items():
                angles = Rotation.from_quat(seed[name]).as_euler('xyz',degrees=True)
                for axis in axes:
                    angles[axis] = _ik_curve_value(first,last,name,axis+3,time,start_angles[axis],end_angles[axis])
                seed[name] = Rotation.from_euler('xyz',angles,degrees=True).as_quat()
            locks = {name:dict(target=(1-alpha)*np.asarray(value['target'])+alpha*np.asarray(last['locks'][name]['target']),
                rotation=lock_blends[name]([time]).as_quat()[0] if lock_blends else value['rotation'])
                for name,value in first.get('locks',{}).items()}
            if span.get('rotation_only'):
                solved = seed
            else:
                solved,translations,_ = solve_ik_pose(skeleton,result,joint,time,target,seed=seed,
                    translations=translations,locks=locks,pole=pole)
            if span.get('channel_masks'):
                source_pose = source_pose or evaluate_rig_at_time(skeleton,baseline,time,loop=False)
                for name,mask in span['channel_masks'].items():
                    source = source_pose.local_transforms[skeleton.joint_names.index(name)]
                    if not all(mask[3:]):
                        if not any(mask[3:]):
                            solved[name] = source.rotation
                        else:
                            angles = Rotation.from_quat(solved.get(name,seed[name])).as_euler('xyz',degrees=True)
                            original_angles = Rotation.from_quat(source.rotation).as_euler('xyz',degrees=True)
                            solved[name] = Rotation.from_euler('xyz',np.where(mask[3:],angles,original_angles),degrees=True).as_quat()
                    if name in translations and not all(mask[:3]):
                        translations[name] = np.where(mask[:3],translations[name],source.translation)
            for name in names:
                baked[name].append(QuatKeyframe(float(time),tuple(solved.get(name,seed[name]))))
            for name in moved:
                moved[name].append(Vec3Keyframe(float(time),tuple(translations[name])))
        for name in names:
            track = tracks.get(name)
            if track is None:
                track = tracks[name] = replace(originals[name]) if name in originals else JointAnimationTrack(name)
            kept = [key for key in track.rotation_keys if key.time < lo-1e-9 or key.time > hi+1e-9]
            # Source samples immediately outside the range prevent an IK key
            # from changing the interpolation leading into or out of it.
            for stamp in (lo-1e-7,hi+1e-7):
                if clip.start_time <= stamp <= clip.end_time and not any(
                        other is not span and other['joint'] == joint and not independent_ik_positions(span,other) and
                        other['start']['time']-1e-8 <= stamp <= other.get('end',other['start'])['time']+1e-8
                        for other in spans):
                    pose = evaluate_rig_at_time(skeleton,baseline,stamp,loop=False)
                    kept.append(QuatKeyframe(stamp,pose.local_transforms[skeleton.joint_names.index(name)].rotation))
            samples = {key.time:key for key in kept+baked[name]}
            track.rotation_keys = [samples[stamp] for stamp in sorted(samples)]
            if name in moved:
                kept = [key for key in track.translation_keys if key.time < lo-1e-9 or key.time > hi+1e-9]
                for stamp in (lo-1e-7,hi+1e-7):
                    if clip.start_time <= stamp <= clip.end_time and not any(
                            other is not span and other['joint'] == joint and not independent_ik_positions(span,other) and
                            other['start']['time']-1e-8 <= stamp <= other.get('end',other['start'])['time']+1e-8
                            for other in spans):
                        pose = evaluate_rig_at_time(skeleton,baseline,stamp,loop=False)
                        kept.append(Vec3Keyframe(stamp,pose.local_transforms[skeleton.joint_names.index(name)].translation))
                samples = {key.time:key for key in kept+moved[name]}
                track.translation_keys = [samples[stamp] for stamp in sorted(samples)]
        result = replace_clip_tracks(skeleton,result,tracks,sample_rate_hz=rate)
        result.metadata['animation_correction_ik'] = dict(ranges=index+1)
        steps.append((deepcopy(signature),result))
    if cache is not None and sample_time is None:
        cache.update(skeleton=skeleton,clip=clip,rate=rate,steps=steps)
    return result


def bvh_export_context(skeleton,clip):
    """Bake fixed FBX unit scales into bone offsets for the BVH exporter."""
    scales = np.asarray([joint.local_bind.scale for joint in skeleton.joints],dtype=float)
    names = skeleton.joint_names
    for track in clip.tracks:
        if track.scale_keys:
            values = np.asarray([key.value for key in track.scale_keys],dtype=float)
            if not np.allclose(values,values[:1],atol=1e-8,rtol=1e-7):
                raise ValueError('BVH approval cannot export animated bone scales.')
            scales[names.index(track.joint_name)] = values[0]
    if not np.allclose(scales,scales[:,:1],atol=1e-8,rtol=1e-7):
        raise ValueError('BVH approval requires uniform bone scales.')
    if np.allclose(scales,1.,atol=1e-8):
        return dict(skeleton=skeleton,clip=clip)
    output_skeleton = SkeletonAsset.from_dict(deepcopy(skeleton.to_dict()))
    output_clip = AnimationClip.from_dict(deepcopy(clip.to_dict()))
    accumulated = np.ones(len(names))
    factors = np.ones(len(names))
    for index,joint in enumerate(output_skeleton.joints):
        factors[index] = accumulated[joint.parent_index] if joint.parent_index >= 0 else 1.
        accumulated[index] = factors[index]*scales[index,0]
        joint.local_bind.translation = tuple(np.asarray(joint.local_bind.translation)*factors[index])
        joint.local_bind.scale = (1.,1.,1.)
    for track in output_clip.tracks:
        for key in track.translation_keys:
            key.value = tuple(np.asarray(key.value)*factors[names.index(track.joint_name)])
        track.scale_keys = []
    output_clip.tracks = [track for track in output_clip.tracks if track.rotation_keys or track.translation_keys]
    return dict(skeleton=output_skeleton,clip=output_clip)


def joint_timeline_keys(clip, joint, fps):
    """Expose actual source keys as editable timeline rows, without baking frames."""
    track = next((track for track in clip.tracks if track.joint_name == joint),None)
    rows = {}
    if track is None:
        return rows
    previous = None
    for channel,offset in ((track.translation_keys,0),(track.rotation_keys,3)):
        for key in channel:
            source_frame = (key.time-clip.start_time)*fps
            frame = round(source_frame)
            row = rows.setdefault(frame,dict(axis_mask=[False]*6,source_frame=source_frame,
                source_time=key.time,scene_skeleton_authored=True,animation_correction_key=True))
            if offset:
                angles = Rotation.from_quat(key.value).as_euler('xyz',degrees=True)
                if previous is not None:
                    angles += 360*np.round((previous-angles)/360)
                previous = angles
                row['rxyz'] = angles.tolist()
                row['rotation_quaternion'] = list(key.value)
            else:
                row['xyz'] = list(key.value)
            row['axis_mask'][offset:offset+3] = [True]*3
            row['rotation_interpolation' if offset else 'translation_interpolation'] = key.interpolation
    return rows


def track_from_timeline_keys(skeleton, clip, joint, rows, fps,*,fk_source=None):
    """Replace a joint's editable channels. Missing source keys stay deleted."""
    if joint not in skeleton.joint_names or not math.isfinite(fps) or fps <= 0:
        raise ValueError('Invalid joint or timeline frame rate.')
    original = next((track for track in clip.tracks if track.joint_name == joint),None)
    result = JointAnimationTrack(joint,scale_keys=deepcopy(original.scale_keys) if original else [])
    bind = skeleton.joints[skeleton.joint_names.index(joint)].local_bind
    defaults = (*bind.translation,*Rotation.from_quat(bind.rotation).as_euler('xyz',degrees=True))
    entries = []
    for frame,row in sorted(rows.items(),key=lambda pair:float(pair[0])):
        source_frame = float(row.get('source_frame',frame))
        time = clip.start_time+source_frame/fps
        exact = row.get('source_time')
        if exact is not None and abs((float(exact)-clip.start_time)*fps-source_frame) < 1e-6:
            time = float(exact)
        if not math.isfinite(time) or time < clip.start_time-1e-8:
            raise ValueError('Correction keys must be inside or after the animation.')
        entries.append((max(clip.start_time,time),row))
    for channel,offset in (('xyz',0),('rxyz',3)):
        keyed = [(time,row) for time,row in entries if any(row.get('axis_mask',[True]*6)[offset:offset+3])]
        stamps = [time for time,_ in keyed]
        values_by_axis = []
        for axis in range(3):
            samples = [(stamp,float(entry[channel][axis])) for stamp,entry in entries
                if entry.get('axis_mask',[True]*6)[offset+axis] and channel in entry]
            values_by_axis.append(np.interp(stamps,*zip(*samples)) if samples
                                  else np.full(len(stamps),defaults[offset+axis]))
        for index,(time,row) in enumerate(keyed):
            values = [axis[index] for axis in values_by_axis]
            if offset:
                quaternion = Rotation.from_euler('xyz',values,degrees=True).as_quat()
                original_q = row.get('rotation_quaternion')
                if original_q is not None and (Rotation.from_quat(original_q).inv()*Rotation.from_quat(quaternion)).magnitude() < 1e-8:
                    quaternion = original_q
                result.rotation_keys.append(QuatKeyframe(time,tuple(quaternion),row.get('rotation_interpolation','linear')))
            else:
                if fk_source is not None and any(row.get('fk_position_axes',[])):
                    source = next((track for track in fk_source.tracks if track.joint_name == joint),None)
                    source_value = _sample_vec3_channel(source.translation_keys if source else [],time,tuple(bind.translation))
                    # Independent FK positions apply after IK. Feeding them
                    # into the solve first would change bone lengths and make
                    # the solver counteract the user's FK translation.
                    values = np.where(row['fk_position_axes'],source_value,values)
                result.translation_keys.append(Vec3Keyframe(time,tuple(values),row.get('translation_interpolation','linear')))
    return result


def apply_corrections(skeleton, clip, keys, fps=24):
    """Blend correction deltas over the source motion, anchored at clip ends.

    An identity ('original pose') key bounds a correction's influence. Unedited
    joint tracks, translations, timing and source objects are preserved.
    """
    if not keys:
        return clip
    fps = float(fps)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('Correction frame rate must be positive.')
    result = AnimationClip.from_dict(deepcopy(clip.to_dict()))
    # Common integer BVH/editor rates retain both sets of frame boundaries.
    source_rate = float(clip.sample_rate_hz)
    if abs(source_rate-round(source_rate)) < 1e-5 and fps.is_integer():
        rate = math.lcm(max(1,round(source_rate)),int(fps))
    else:
        rate = max(source_rate,fps)
    if rate > 10000:
        raise ValueError('These frame rates require an excessive export sample rate.')
    result.sample_rate_hz = rate
    result.name = clip.name+'_Corrected'
    tracks = {track.joint_name:track for track in result.tracks}
    poses = {}
    for name,entries in keys.items():
        if name not in skeleton.joint_names or not isinstance(entries,dict):
            raise ValueError('Correction keys do not match the input skeleton.')
        if not entries:
            continue
        samples = {float(clip.start_time):(0.,0.,0.,1.),float(clip.end_time):(0.,0.,0.,1.)}
        for stamp,q in entries.items():
            time = float(stamp)
            if not math.isfinite(time) or not clip.start_time-1e-9 <= time <= clip.end_time+1e-9:
                raise ValueError('A correction key is outside this animation.')
            samples[min(clip.end_time,max(clip.start_time,time))] = Rotation.from_quat(q).as_quat()
        stamps = np.array(sorted(samples))
        deltas = Rotation.from_quat([samples[t] for t in stamps])
        track = tracks.get(name)
        if track is None:
            track = JointAnimationTrack(name)
            result.tracks.append(track)
        times = sorted(set(stamps) | {min(clip.end_time,max(clip.start_time,key.time)) for key in track.rotation_keys} |
                       set(np.linspace(clip.start_time,clip.end_time,max(1,round(clip.duration*rate)+1))))
        # np.linspace and BVH timestamps can describe the same frame with
        # slightly different floats. Canonical tracks require separated keys.
        unique_times = []
        for time in times:
            if not unique_times or time - unique_times[-1] > 1e-8:
                unique_times.append(time)
        times = unique_times
        rotations = deltas if len(stamps)==1 else Slerp(stamps,deltas)(times)
        index = skeleton.joint_names.index(name)
        for t in times:
            if t not in poses:
                poses[t] = evaluate_rig_at_time(skeleton,clip,float(t),loop=False).local_transforms
        base = Rotation.from_quat([poses[t][index].rotation for t in times])
        quats = (base*rotations).as_quat()
        track.rotation_keys = [QuatKeyframe(float(t),tuple(q)) for t,q in zip(times,quats)]
    result.metadata['animation_correction'] = dict(joints=len(keys),keys=sum(len(v) for v in keys.values()))
    result.validate(skeleton)
    return result
