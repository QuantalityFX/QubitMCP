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

REPAIR_VERSION = 5


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


def _repair_setup(offsets, limits, volumes, names, parents, end_sites):
    scale = rig_scale(offsets)
    offsets = np.asarray(offsets,dtype=float)/scale
    offsets = offsets.copy()
    offsets[0] = 0.  # Root placement has no effect on self-contact.
    axes = {}
    for j in limits:
        child = primary_child(j,names,parents)
        axis = offsets[child]
        axes[j] = axis/max(np.linalg.norm(axis),1e-8)
    colliders = ColliderModel(names,parents,offsets,volumes,end_sites=end_site_offsets(end_sites,scale),
                              reuse_unchanged_pairs=True)
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
    return scale,offsets,axes,colliders,groups,lookup


def limit_rotations(rotations, offsets, roots, fps, limits, volumes, *, collisions=True, ik_repair=True, progress=None,
                    names=JOINT_NAMES, parents=PARENTS,end_sites=None, _setup=None, temporal_weight=None):
    """Clamp joint limits, optionally using IK to clear finger and limb contacts."""
    result = np.asarray(rotations,dtype=float).copy()
    scale,offsets,axes,colliders,groups,lookup = (
        _repair_setup(offsets,limits,volumes,names,parents,end_sites) if _setup is None else _setup)
    temporal_weight = .008*fps/20 if temporal_weight is None else temporal_weight
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
        def check_cancel():
            if progress is not None:
                progress(frame,len(result))
        if collisions and ik_repair:
            for _pass in range(2):
                for kind,chain,mask in groups:
                    initial = colliders.penetrations(*forward_kinematics(pose,offsets,parents),mask=mask)
                    joint_axes = {j:([limits[j]['axis']]+([limits[j]['spread_axis']] if 'spread_axis' in limits[j] else []))
                                  if 'flex' in limits[j] else np.eye(3) for j in chain}
                    count = sum(len(a) for a in joint_axes.values())
                    old = previous.get(chain,np.zeros(count))
                    if np.max(initial,initial=0) < .0005:
                        # A temporal IK offset is only useful while resolving a
                        # contact. Avoid running least-squares on every later
                        # frame merely to decay a correction that is no longer
                        # needed; the bounded pose is already collision-free.
                        previous[chain] = np.zeros(count)
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
                        previous_parameters=old,temporal_weight=temporal_weight,retry_seeds=retries,
                        check_cancel=check_cancel,reuse_unchanged_joints=True,
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
            # Recheck hands after the legs and opposite arm have moved. Solve
            # the arm and fingers together when separate passes left contact.
            # A small clearance keeps the result outside the capsule surface.
            for _pass in range(3):
                moved_hand = False
                for side in ('left','right'):
                    hand = [lookup[side+'_wrist'], *colliders.hand_joints[side]]
                    hand_mask = colliders.mask_for(names[j] for j in hand)
                    points,world = forward_kinematics(pose,offsets,parents)
                    if np.max(colliders.penetrations(points,world,mask=hand_mask),initial=0) <= .00005:
                        continue
                    initial = colliders.penetrations(points,world,mask=hand_mask,clearance=.001)
                    if np.max(initial,initial=0) <= .00005:
                        continue
                    chain = [lookup[side+'_'+part] for part in ('collar','shoulder','elbow')]
                    chain += hand
                    joint_axes = {j: ([limits[j]['axis']] + ([limits[j]['spread_axis']] if 'spread_axis' in limits[j] else []))
                                  if 'flex' in limits[j] else np.eye(3) for j in chain if j in limits}
                    baseline = colliders.penetrations(points,world)
                    other_mask = ~hand_mask
                    other_baseline = baseline[other_mask]
                    def residual(points,world,local):
                        hands = colliders.penetrations(points,world,mask=hand_mask,clearance=.001)
                        # Do not buy hand clearance by creating another collision.
                        # Hand pairs were measured above; avoid measuring them twice.
                        other = np.maximum(0.,colliders.penetrations(points,world,mask=other_mask)-other_baseline)
                        return np.concatenate((hands,other))
                    count = sum(len(a) for a in joint_axes.values())
                    seeds = []
                    cursor = 0
                    for j,a in joint_axes.items():
                        if names[j].endswith('shoulder'):
                            for axis in range(len(a)):
                                for sign in (-1,1):
                                    seed = np.zeros(count)
                                    seed[cursor+axis] = sign*.8
                                    seeds.append(seed)
                        cursor += len(a)
                    solved = solve_constrained_ik(pose,offsets,parents,joint_axes=joint_axes,
                        project_rotation=project,constraint_residual=residual,
                        pose_weight=.001,temporal_weight=0.,tolerance=.00005,max_iterations=65,
                        retry_seeds=seeds,check_cancel=check_cancel,prioritize_constraints=True,reuse_unchanged_joints=True)
                    solved_pose = forward_kinematics(solved.rotations,offsets,parents)
                    final = colliders.penetrations(*solved_pose)
                    hand_final = colliders.penetrations(*solved_pose,mask=hand_mask,clearance=.001)
                    if (np.max(hand_final,initial=0) < np.max(initial,initial=0)-1e-8
                            and np.all(final[~hand_mask] <= baseline[~hand_mask]+.00005)):
                        frame_fingers |= not np.allclose(pose[hand[1:]],solved.rotations[hand[1:]],atol=1e-7,rtol=0)
                        frame_arms |= not np.allclose(pose[chain[:3]+hand[:1]],solved.rotations[chain[:3]+hand[:1]],atol=1e-7,rtol=0)
                        pose[:] = solved.rotations
                        moved_hand = True
                if not moved_hand:
                    break
        after.append(float(np.max(colliders.penetrations(*forward_kinematics(pose,offsets,parents)),initial=0)))
        changed += int(not np.allclose(pose,original,atol=1e-7,rtol=0))
        finger_frames += int(frame_fingers)
        arm_frames += int(frame_arms)
    return result,dict(corrected_frames=changed,frame_count=len(result),
                       finger_collision_corrected_frames=finger_frames,arm_ik_corrected_frames=arm_frames,
                       finger_collider_count=sum(name in FINGER_NAMES for name in colliders.names),
                       remaining_proxy_overlap_frames=int(np.count_nonzero(np.asarray(after) > .00005)),
                       max_proxy_overlap_before=max(before,default=0)*scale,
                       max_proxy_overlap_after=max(after,default=0)*scale,collisions_enabled=bool(collisions),
                       ik_repair_enabled=bool(ik_repair))


def _refine_contacts(rotations, offsets, parents, names, limits, volumes, fps, *, end_sites=None, progress=None,
                     max_refinements=1):
    """Refine affected intervals, retaining sparse keys at bounded substeps.

    Each leaf is checked at quarter intervals, including after a repair. Only
    intervals with added contact are subdivided. Scratch geometry is bounded
    by recursion depth, rather than frames multiplied by all collider pairs.
    This remains sampled collision detection; unresolved checks are reported.
    """
    setup = _repair_setup(offsets,limits,volumes,names,parents,end_sites)
    scale,normalized,_,model,_,_ = setup
    corrected = attempted = remaining = max_depth = 0
    maximum = output_max = 0.
    output_remaining = 0
    poses, samples = [rotations[0]], [0.]
    total = max(1,len(rotations)-1)

    def notify(sample):
        if progress is not None:
            progress(sample,total)

    def overlaps(pose):
        return model.penetrations(*forward_kinematics(pose,normalized,parents))

    def record(values):
        nonlocal output_remaining,output_max
        value = float(np.max(values,initial=0))
        output_remaining += int(value > .00005)
        output_max = max(output_max,value)

    def refine(left, right, a, b, start, span, depth):
        nonlocal corrected,attempted,remaining,maximum,max_depth
        notify(start)
        if np.array_equal(left,right):
            # Includes stationary, impossible overlaps. Interpolation is exact.
            poses.append(right)
            samples.append(start+span)
            record(b)
            return
        rotation = Rotation.from_matrix(left)
        delta = (rotation.inv()*Rotation.from_matrix(right)).as_rotvec()
        dense = [left]+[(rotation*Rotation.from_rotvec(delta*(step/4))).as_matrix()
                       for step in (1,2,3)]+[right]
        measured = [a]+[overlaps(pose) for pose in dense[1:4]]+[b]
        allowance = np.maximum(a,b)
        excesses = [float(np.max(value-allowance,initial=0)) for value in measured[1:4]]
        pending = [step for step,excess in enumerate(excesses,1) if excess > .00005]
        if not pending or depth == max_refinements:
            remaining += len(pending)
            maximum = max(maximum,*excesses)
            poses.append(right)
            samples.append(start+span)
            record(b)
            return
        moved = False
        for step in pending:
            sample = start+span*step/4
            notify(sample)
            callback = (lambda f,n:notify(sample)) if progress is not None else None
            repaired,_ = limit_rotations(np.asarray(dense[step])[None],offsets,np.zeros((1,3)),
                fps*4**(depth+1),limits,volumes,names=names,parents=parents,end_sites=end_sites,
                # An isolated substep has no preceding IK parameters to smooth
                # against. Scaling that penalty with substep FPS fights repair
                # and repeatedly triggers the expensive combined hand fallback.
                progress=callback,_setup=setup,temporal_weight=0.)
            attempted += 1
            if not np.allclose(repaired[0],dense[step],atol=1e-10,rtol=0):
                dense[step] = repaired[0]
                measured[step] = overlaps(dense[step])
                corrected += 1
                moved = True
        if not moved:
            # Do not expand the same curve again when IK cannot change it.
            remaining += len(pending)
            maximum = max(maximum,*excesses)
            poses.append(right)
            samples.append(start+span)
            record(b)
            return
        max_depth = max(max_depth,depth+1)
        for step in range(4):
            refine(dense[step],dense[step+1],measured[step],measured[step+1],
                   start+span*step/4,span/4,depth+1)

    first = overlaps(rotations[0])
    record(first)
    for interval in range(len(rotations)-1):
        notify(interval)
        last = first if np.array_equal(rotations[interval],rotations[interval+1]) else overlaps(rotations[interval+1])
        refine(rotations[interval],rotations[interval+1],first,last,float(interval),1.,0)
        first = last
        notify(interval+1)
    return np.asarray(poses),np.asarray(samples),fps*4**max_depth,dict(
        interpolated_samples_repaired=corrected,interpolated_samples_attempted=attempted,
        remaining_interpolated_contact_samples=remaining,max_interpolated_overlap_increase=maximum*scale,
        output_remaining_proxy_overlap_frames=output_remaining,output_max_proxy_overlap=output_max*scale)


def _progress_phase(progress, start, end, label, *, unit='Frame'):
    if progress is None:
        return None
    stage = getattr(progress,'set_stage',None)
    if stage is not None:
        stage(label)
    value = start
    last_detail = None
    def update(frame,total):
        nonlocal value,last_detail
        value = max(value,min(end,start+int((end-start)*frame/max(1,total))))
        progress(value,1000)
        if stage is not None:
            detail = f'{label}\n{unit} {min(int(frame)+1,max(1,int(total)))} of {max(1,int(total))}'
            if detail != last_detail:
                stage(detail)
                last_detail = detail
    progress(start,1000)
    return update


def limit_clip(skeleton, clip, limits, volumes, *, collisions=True, ik_repair=True, progress=None, hand_limits=None,
               thorough_repair=False):
    rig_layout(skeleton)
    indices,names,parents,offsets = full_rig_layout(skeleton)
    limits = expanded_limits(names,limits,hand_limits)
    if clip is None:
        raise ValueError('Connect an animated skeleton to Rig Limiter.')
    fps = float(clip.sample_rate_hz)
    source_count = max(1, int(round((clip.end_time-clip.start_time)*fps))+1)
    count = source_count
    times = np.linspace(clip.start_time, clip.end_time, count)
    rotations, roots = [], []
    sampling = _progress_phase(progress,0,100,'Reading animation poses...')
    for frame,time in enumerate(times):
        if sampling is not None:
            sampling(frame,len(times))
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
                                       progress=_progress_phase(progress,100,650,'Correcting joint limits and capsule intersections...'),
                                       names=names, parents=parents,
                                       end_sites=skeleton.metadata.get('end_sites'))
    if collisions and ik_repair:
        corrected,samples,fps,interpolation_report = _refine_contacts(corrected,offsets,parents,names,limits,volumes,fps,
            end_sites=skeleton.metadata.get('end_sites'),
            progress=_progress_phase(progress,650,900,'Checking and repairing between-frame contacts...',unit='Interval'),
            max_refinements=3 if thorough_repair else 1)
        report.update(interpolation_report)
        times = clip.start_time+samples*((clip.end_time-clip.start_time)/max(1,source_count-1))
    baking = _progress_phase(progress,900,1000,'Building corrected animation keys...',unit='Joint')
    # Construct fresh schema objects: evaluator runtime identities must not be copied.
    result = AnimationClip.from_dict(deepcopy(clip.to_dict()))
    result.name = clip.name+'_Limited'
    result.sample_rate_hz = fps
    report.update(source_frame_count=source_count, source_sample_rate_hz=float(clip.sample_rate_hz),
                  repair_substeps=int(round(fps/clip.sample_rate_hz)), sample_rate_hz=fps,
                  thorough_repair=bool(thorough_repair),
                  output_frame_count=len(corrected),baked_frame_count=(source_count-1)*int(round(fps/clip.sample_rate_hz))+1)
    tracks = {track.joint_name: track for track in result.tracks}
    for number,j in enumerate(limits):
        if baking is not None:
            baking(number,len(limits))
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
    if baking is not None:
        baking(1,1)
    return result, report
