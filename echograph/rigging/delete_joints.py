"""Non-destructive joint removal in the canonical rig representation."""
from copy import deepcopy
from dataclasses import replace

import numpy as np
from scipy.spatial.transform import Rotation

from .fbx_canonical import JointTransform, JointAnimationTrack, Vec3Keyframe, QuatKeyframe, VertexInfluence
from .fbx_stage5_evaluator import evaluate_rig_at_time


def _transform(matrix):
    scale = np.linalg.norm(matrix[:3, :3], axis=0)
    if np.any(scale < 1e-10):
        raise ValueError("Cannot remove joints with a collapsed transform scale.")
    rotation = matrix[:3, :3] / scale
    if np.linalg.det(rotation) < 0:
        scale[0] *= -1
        rotation[:, 0] *= -1
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5):
        raise ValueError("Removing this joint would introduce shear; apply its scale before removal.")
    return JointTransform(tuple(matrix[:3, 3]), tuple(Rotation.from_matrix(rotation).as_quat()), tuple(scale))


def filter_joints(skeleton, clips, meshes, removed):
    """Preserve world poses; bake reparented animation at its native sample rate."""
    skeleton.validate()
    removed = set(removed)
    kept = [i for i, joint in enumerate(skeleton.joints) if joint.name not in removed]
    if not kept:
        raise ValueError("Keep at least one joint in the skeleton.")
    remap = {old: new for new, old in enumerate(kept)}
    parents = {}
    for old in kept:
        parent = skeleton.joints[old].parent_index
        while parent >= 0 and parent not in remap:
            parent = skeleton.joints[parent].parent_index
        parents[old] = parent
    # Copy canonical fields only. Runtime pose caches on the imported skeleton
    # still contain removed joints and must never follow a changed hierarchy.
    result = replace(skeleton, joints=[deepcopy(skeleton.joints[i]) for i in kept],
                     metadata=deepcopy(skeleton.metadata))
    bind = np.array(evaluate_rig_at_time(skeleton, None, 0).global_matrices).reshape(-1, 4, 4)
    changed = [old for old in kept if parents[old] != skeleton.joints[old].parent_index]
    for old, joint in zip(kept, result.joints):
        parent = parents[old]
        joint.parent_index = remap.get(parent, -1)
        if old in changed:
            joint.local_bind = _transform(np.linalg.solve(bind[parent], bind[old]) if parent >= 0 else bind[old])
    result.validate()
    new_clips = []
    for clip in clips:
        output = replace(clip, tracks=[deepcopy(track) for track in clip.tracks if track.joint_name not in removed],
                         metadata=deepcopy(clip.metadata))
        if changed:
            count = max(1, int(np.ceil((clip.end_time - clip.start_time) * clip.sample_rate_hz)))
            times = set(np.linspace(clip.start_time, clip.end_time, count + 1))
            for track in clip.tracks:
                for channel in (track.translation_keys, track.rotation_keys, track.scale_keys):
                    times.update(key.time for key in channel if clip.start_time <= key.time <= clip.end_time)
            tracks = {old: JointAnimationTrack(skeleton.joints[old].name) for old in changed}
            for time in sorted(times):
                world = np.array(evaluate_rig_at_time(skeleton, clip, time).global_matrices).reshape(-1, 4, 4)
                for old, track in tracks.items():
                    parent = parents[old]
                    xf = _transform(np.linalg.solve(world[parent], world[old]) if parent >= 0 else world[old])
                    quat = np.array(xf.rotation)
                    if track.rotation_keys and np.dot(track.rotation_keys[-1].value, quat) < 0:
                        quat *= -1
                    track.translation_keys.append(Vec3Keyframe(float(time), xf.translation))
                    track.rotation_keys.append(QuatKeyframe(float(time), tuple(quat)))
                    track.scale_keys.append(Vec3Keyframe(float(time), xf.scale))
            names = {track.joint_name for track in tracks.values()}
            output.tracks = [track for track in output.tracks if track.joint_name not in names] + list(tracks.values())
        output.validate(skeleton=result)
        new_clips.append(output)
    new_meshes = deepcopy(meshes)
    for mesh in new_meshes:
        mesh.validate(skeleton=skeleton)
        inverse_binds = mesh.metadata.get("inverse_bind_matrices")
        if inverse_binds is not None:
            matrices = np.asarray(inverse_binds, dtype=float).reshape(-1, 16)
            if len(matrices) != len(skeleton.joints):
                raise ValueError(f"Mesh '{mesh.name}' bind matrix count does not match its input skeleton.")
            # Mesh cluster offsets may differ from the skeleton inverse binds.
            # Preserve their values, but reorder them with the weight indices.
            mesh.metadata["inverse_bind_matrices"] = matrices[kept].tolist()
        for skin in mesh.vertex_skins:
            weights = {}
            for influence in skin.influences:
                old = influence.joint_index
                if old not in remap:
                    continue
                index = remap[old]
                weights[index] = weights.get(index, 0.0) + influence.weight
            total = sum(weights.values())
            if total <= 0:
                raise ValueError(f"Deleting these joints leaves mesh '{mesh.name}', vertex {skin.vertex_index}, "
                                 "without skin weights. Keep its influencing joint or reweight the mesh first.")
            skin.influences = [VertexInfluence(index, weight / total) for index, weight in sorted(weights.items())]
        mesh.validate(skeleton=result)
    return result, new_clips, new_meshes
