"""Post-process existing PriorMDM/HumanML animation without reconstructing it."""
from copy import deepcopy

import numpy as np
from scipy.spatial.transform import Rotation

from .conversion import JOINT_NAMES, PARENTS, REST_DIRECTIONS
from .rig_limits import PREVIEW_JOINT_LIMITS, clamp_preview_rotation
from .rig_volumes import ColliderModel, rig_scale
from .hand_rig import full_rig_layout, expanded_limits, primary_child, end_site_offsets
from .hands import DIGITS, FINGER_NAMES
from echograph.rigging.ik import forward_kinematics, solve_constrained_ik
from echograph.rigging.fbx_canonical import AnimationClip, JointAnimationTrack, QuatKeyframe
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time


def rig_layout(skeleton):
    """Map BVH depth-first ordering (and optional finger joints) by name."""
    lookup = {joint.name: i for i, joint in enumerate(skeleton.joints)}
    missing = set(JOINT_NAMES)-lookup.keys()
    if missing:
        raise ValueError('Rig Limiter needs the PriorMDM/HumanML skeleton. Missing joints: '+', '.join(sorted(missing)))
    indices = [lookup[name] for name in JOINT_NAMES]
    for j, index in enumerate(indices):
        expected = -1 if PARENTS[j] < 0 else indices[PARENTS[j]]
        if skeleton.joints[index].parent_index != expected:
            raise ValueError('Rig Limiter needs the PriorMDM/HumanML joint hierarchy.')
    offsets = np.array([skeleton.joints[i].local_bind.translation for i in indices], dtype=float)
    lengths = np.linalg.norm(offsets[1:], axis=1)
    if np.any(lengths < 1e-8) or not np.isfinite(offsets).all():
        raise ValueError('Rig Limiter cannot use degenerate or non-finite bone offsets.')
    # These anatomical limits are defined in the exported HumanML rest axes.
    bones = [next(i for i,p in enumerate(PARENTS) if p == j) for j in PREVIEW_JOINT_LIMITS]
    directions = offsets[bones]/np.linalg.norm(offsets[bones],axis=1)[:,None]
    if np.any(np.sum(directions*REST_DIRECTIONS[bones], axis=1) < .99):
        raise ValueError('Rig Limiter needs PriorMDM/HumanML rest axes. Apply it before retargeting.')
    return indices, offsets


def limit_rotations(rotations, offsets, roots, fps, limits, volumes, *, collisions=True, ik_repair=True, progress=None,
                    names=JOINT_NAMES, parents=PARENTS,end_sites=None):
    """Clamp joint limits, optionally using IK to clear finger and limb contacts."""
    result = np.asarray(rotations,dtype=float).copy()
    scale = rig_scale(offsets)
    offsets = np.asarray(offsets,dtype=float)/scale
    offsets = offsets.copy()
    offsets[0] = 0.  # Root placement has no effect on self-contact.
    axes = {}
    for j in limits:
        child = primary_child(j,names,parents)
        axis = offsets[child]
        axes[j] = axis/max(np.linalg.norm(axis),1e-8)
    colliders = ColliderModel(names,parents,offsets,volumes,end_sites=end_site_offsets(end_sites,scale))
    groups = []
    lookup = {name:i for i,name in enumerate(names)}
    for side in ('left','right'):
        for digit in DIGITS:
            chain = [lookup[f'{side}_{digit}{segment}'] for segment in (1,2,3)
                     if f'{side}_{digit}{segment}' in lookup]
            if chain:
                groups.append(('finger',tuple(chain),colliders.mask_for(names[j] for j in chain)))
    for side in ('left','right'):
        chain = tuple(lookup[side+'_'+part] for part in ('collar','shoulder','elbow','wrist')
                      if lookup[side+'_'+part] in limits)
        volumes_for_arm = [side+'_'+part for part in ('shoulder','elbow','wrist')]
        volumes_for_arm += [names[j] for j in colliders.hand_joints[side]]
        groups.append(('arm',chain,colliders.mask_for(volumes_for_arm)))
    for side in ('left','right'):
        chain = tuple(lookup[side+'_'+part] for part in ('hip','knee','ankle'))
        groups.append(('leg',chain,colliders.mask_for(names[j] for j in chain)))
    previous = {}
    before,after = [],[]
    changed,finger_frames,arm_frames = 0,0,0
    for frame,original in enumerate(rotations):
        if progress is not None:
            progress(frame,len(result))
        pose = result[frame]
        before.append(float(np.max(colliders.penetrations(*forward_kinematics(pose,offsets,parents)),initial=0)))
        def project(j,q):
            return clamp_preview_rotation(j,q,axes[j],limits=limits)
        for j in limits:
            pose[j] = Rotation.from_quat(project(j,Rotation.from_matrix(pose[j]).as_quat())).as_matrix()
        bounded = pose.copy()
        frame_fingers,frame_arms = False,False
        if collisions and ik_repair:
            for _pass in range(2):
                for kind,chain,mask in groups:
                    initial = colliders.penetrations(*forward_kinematics(pose,offsets,parents),mask=mask)
                    joint_axes = {j:([limits[j]['axis']]+([limits[j]['spread_axis']] if 'spread_axis' in limits[j] else []))
                                  if 'flex' in limits[j] else np.eye(3) for j in chain}
                    count = sum(len(a) for a in joint_axes.values())
                    old = previous.get(chain,np.zeros(count))
                    if np.max(initial,initial=0) < .0005 and np.linalg.norm(old) < .002:
                        continue
                    if progress is not None:
                        progress(frame,len(result))
                    def residual(points,world,local):
                        return colliders.penetrations(points,world,mask=mask)
                    retries = []
                    cursor = 0
                    for j,axes_for_joint in joint_axes.items():
                        # Retry hinges in both directions; arm retries focus on
                        # the shoulder, keeping collar motion inside its cone.
                        if kind != 'finger' and (names[j].endswith('shoulder') or names[j].endswith('hip')):
                            for k in range(len(axes_for_joint)):
                                for sign in (-1,1):
                                    seed = old.copy()
                                    seed[cursor+k] += sign*.8
                                    retries.append(seed)
                        cursor += len(axes_for_joint)
                    if kind == 'finger':
                        seed = old.copy()
                        cursor = 0
                        for a in joint_axes.values():
                            seed[cursor] += .6
                            cursor += len(a)
                        retries = [seed]
                    solved = solve_constrained_ik(pose,offsets,parents,joint_axes=joint_axes,
                        project_rotation=project,constraint_residual=residual,reference_rotations=bounded,
                        previous_parameters=old,temporal_weight=.008*fps/20,retry_seeds=retries,
                        tolerance=.0005 if kind == 'finger' else .002,max_iterations=22 if kind == 'finger' else 40)
                    final = residual(solved.points,forward_kinematics(solved.rotations,offsets,parents)[1],solved.rotations)
                    if np.sum(final**2) <= np.sum(initial**2)+1e-10:
                        moved = not np.allclose(pose[list(chain)],solved.rotations[list(chain)],atol=1e-7,rtol=0)
                        pose[:] = solved.rotations
                        previous[chain] = solved.parameters
                        frame_fingers |= moved and kind == 'finger'
                        frame_arms |= moved and kind == 'arm'
                    else:
                        previous[chain] = np.zeros(count)
                if np.max(colliders.penetrations(*forward_kinematics(pose,offsets,parents)),initial=0) < .0005:
                    break
        after.append(float(np.max(colliders.penetrations(*forward_kinematics(pose,offsets,parents)),initial=0)))
        changed += int(not np.allclose(pose,original,atol=1e-7,rtol=0))
        finger_frames += int(frame_fingers)
        arm_frames += int(frame_arms)
    return result,dict(corrected_frames=changed,frame_count=len(result),
                       finger_collision_corrected_frames=finger_frames,arm_ik_corrected_frames=arm_frames,
                       finger_collider_count=sum(name in FINGER_NAMES for name in colliders.names),
                       remaining_proxy_overlap_frames=int(np.count_nonzero(np.asarray(after) > .003)),
                       max_proxy_overlap_before=max(before,default=0)*scale,
                       max_proxy_overlap_after=max(after,default=0)*scale,collisions_enabled=bool(collisions),
                       ik_repair_enabled=bool(ik_repair))


def limit_clip(skeleton, clip, limits, volumes, *, collisions=True, ik_repair=True, progress=None, hand_limits=None):
    rig_layout(skeleton)
    indices,names,parents,offsets = full_rig_layout(skeleton)
    limits = expanded_limits(names,limits,hand_limits)
    if clip is None:
        raise ValueError('Connect an animated skeleton to Rig Limiter.')
    fps = float(clip.sample_rate_hz)
    count = max(1, int(round((clip.end_time-clip.start_time)*fps))+1)
    times = np.linspace(clip.start_time, clip.end_time, count)
    rotations, roots = [], []
    for frame,time in enumerate(times):
        if progress is not None and frame % 20 == 0:
            progress(0,len(times))
        local = evaluate_rig_at_time(skeleton, clip, float(time)).local_transforms
        positions = np.array([local[i].translation for i in indices])
        if not np.allclose(positions[1:], offsets[1:], atol=1e-5*rig_scale(offsets), rtol=1e-5):
            raise ValueError('Rig Limiter requires fixed bone lengths; apply it before translation/retarget edits.')
        if not np.allclose([local[i].scale for i in indices], 1., atol=1e-6):
            raise ValueError('Rig Limiter requires unscaled bone transforms.')
        rotations.append(Rotation.from_quat([local[i].rotation for i in indices]).as_matrix())
        roots.append(positions[0])
    corrected, report = limit_rotations(np.array(rotations), offsets, np.array(roots), fps,
                                       limits, volumes, collisions=collisions, ik_repair=ik_repair,
                                       progress=progress, names=names, parents=parents,
                                       end_sites=skeleton.metadata.get('end_sites'))
    # Construct fresh schema objects: evaluator runtime identities must not be copied.
    result = AnimationClip.from_dict(deepcopy(clip.to_dict()))
    result.name = clip.name+'_Limited'
    tracks = {track.joint_name: track for track in result.tracks}
    for j in limits:
        name = names[j]
        if name not in tracks:
            tracks[name] = JointAnimationTrack(name)
            result.tracks.append(tracks[name])
        quats = Rotation.from_matrix(corrected[:, j]).as_quat()
        for f in range(1, len(quats)):
            if quats[f] @ quats[f-1] < 0:
                quats[f] *= -1
        tracks[name].rotation_keys = [QuatKeyframe(float(t), tuple(q)) for t, q in zip(times, quats)]
    result.metadata['rig_limiter'] = report
    result.validate(skeleton)
    return result, report
