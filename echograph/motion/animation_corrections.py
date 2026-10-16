"""Portable per-take animation edits and bounded IK overrides."""
from copy import deepcopy
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
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time, replace_clip_tracks, override_rotation_keys
from .rig_repair_cache import file_signature


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


def save_document(source, document):
    if document['source_sha'] != file_signature(source):
        raise ValueError('The source animation changed. Reopen the correction editor before saving.')
    path = sidecar_path(source)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                         prefix='.animation-correction-',suffix='.tmp',delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(dict(document,version=document.get('version',1)),stream,indent=2,allow_nan=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
    name = re.sub(r'[^a-z0-9]', '', name.lower().rsplit(':',1)[-1])
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


def solve_limb_pose(skeleton,clip,joint,time,target,*,seed=None):
    from echograph.rigging.ik import solve_constrained_ik
    start,middle,end = limb_chain(skeleton,joint)
    evaluation = evaluate_rig_at_time(skeleton,clip,time,loop=False)
    local = evaluation.local_transforms
    target = np.asarray(target,dtype=float)
    if target.shape != (3,) or not np.all(np.isfinite(target)):
        raise ValueError('An IK target must contain three finite coordinates.')
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


def apply_document(skeleton, clip, document):
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
    result = apply_ik_spans(skeleton,result,document.get('ik_spans',[]),document.get('fps',clip.sample_rate_hz))
    return override_rotation_keys(skeleton,result,document['fk_keys']) if document.get('fk_keys') else result


def validate_ik_spans(skeleton,clip,spans):
    occupied = {}
    for span in spans:
        chain = tuple(skeleton.joints[index].name for index in limb_chain(skeleton,span['joint']))
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
            if set(key['rotations']) != set(chain):
                raise ValueError('IK keys must contain the full limb chain.')
            Rotation.from_quat([key['rotations'][name] for name in chain])
        for name in chain:
            for lo,hi in occupied.get(name,[]):
                overlap = min(hi,end['time'])-max(lo,start['time']) > 1e-8
                pending_inside = start['time'] == end['time'] and lo+1e-8 < start['time'] < hi-1e-8
                contains_pending = lo == hi and start['time']+1e-8 < lo < end['time']-1e-8
                if overlap or pending_inside or contains_pending:
                    raise ValueError('IK override ranges for the same limb cannot overlap.')
            occupied.setdefault(name,[]).append((start['time'],end['time']))


def apply_ik_spans(skeleton,clip,spans,fps):
    """Bake bounded IK targets; preserve source channels outside each range.

    The document stores only control boundaries, not these generated samples.
    Interpolated boundary poses seed the bend plane, then each sample solves
    the interpolated target. All three chain joints receive rotation keys.
    """
    if not spans:
        return clip
    validate_ik_spans(skeleton,clip,spans)
    rate = max(float(fps),float(clip.sample_rate_hz))*2
    if not math.isfinite(rate) or not 0 < rate <= 10000:
        raise ValueError('Invalid IK sampling rate.')
    tracks = {}
    originals = {track.joint_name:track for track in clip.tracks}
    # Adjacent intervals share the same boundary; process them chronologically.
    for span in sorted(spans,key=lambda entry:entry['start']['time']):
        joint = span['joint']
        names = [skeleton.joints[index].name for index in limb_chain(skeleton,joint)]
        first,last = span['start'],span.get('end',span['start'])
        lo,hi = first['time'],last['time']
        stamps = [lo]+[clip.start_time+frame/rate for frame in
            range(math.ceil((lo-clip.start_time)*rate),math.floor((hi-clip.start_time)*rate)+1)
            if lo+1e-9 < clip.start_time+frame/rate < hi-1e-9]
        if hi-lo > 1e-9:
            stamps.append(hi)
        blends = {name:Slerp([lo,hi],Rotation.from_quat([first['rotations'][name],last['rotations'][name]]))
                  for name in names} if hi-lo > 1e-10 else {}
        baked = {name:[] for name in names}
        for time in stamps:
            alpha = (time-lo)/(hi-lo) if hi > lo else 0.
            seed = {name:(blends[name]([time]).as_quat()[0] if blends else first['rotations'][name]) for name in names}
            target = (1-alpha)*np.asarray(first['target'])+alpha*np.asarray(last['target'])
            solved,_ = solve_limb_pose(skeleton,clip,joint,time,target,seed=seed)
            for name in names:
                baked[name].append(QuatKeyframe(float(time),tuple(solved.get(name,seed[name]))))
        for name in names:
            track = tracks.get(name)
            if track is None:
                track = tracks[name] = replace(originals[name]) if name in originals else JointAnimationTrack(name)
            kept = [key for key in track.rotation_keys if key.time < lo-1e-9 or key.time > hi+1e-9]
            # Source samples immediately outside the range prevent an IK key
            # from changing the interpolation leading into or out of it.
            for stamp in (lo-1e-7,hi+1e-7):
                if clip.start_time <= stamp <= clip.end_time and not any(
                        other is not span and other['joint'] == joint and
                        other['start']['time']-1e-8 <= stamp <= other.get('end',other['start'])['time']+1e-8
                        for other in spans):
                    pose = evaluate_rig_at_time(skeleton,clip,stamp,loop=False)
                    kept.append(QuatKeyframe(stamp,pose.local_transforms[skeleton.joint_names.index(name)].rotation))
            samples = {key.time:key for key in kept+baked[name]}
            track.rotation_keys = [samples[stamp] for stamp in sorted(samples)]
    result = replace_clip_tracks(skeleton,clip,tracks,sample_rate_hz=rate)
    result.metadata['animation_correction_ik'] = dict(ranges=len(spans))
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


def track_from_timeline_keys(skeleton, clip, joint, rows, fps):
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
