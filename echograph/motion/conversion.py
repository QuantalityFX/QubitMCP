"""HumanML3D XYZ to canonical animation plus a fixed-bone BVH preview.

XYZ cannot determine bone twist. Rotations are geometric estimates, not SMPL
fitting. The portable archive preserves exact positions and upstream features;
the BVH uses median bone lengths and is intended for skeleton preview/blocking.
"""
from __future__ import annotations

import json
from pathlib import Path
import warnings

import numpy as np
from scipy.spatial.transform import Rotation

from echograph.rigging.fbx_canonical import (
    AnimationClip, Joint, JointAnimationTrack, JointTransform,
    QuatKeyframe, SkeletonAsset, Vec3Keyframe,
)
from .types import GeneratedMotion

JOINT_NAMES = ("pelvis", "left_hip", "right_hip", "spine1", "left_knee", "right_knee",
               "spine2", "left_ankle", "right_ankle", "spine3", "left_foot", "right_foot",
               "neck", "left_collar", "right_collar", "head", "left_shoulder",
               "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist")
PARENTS = (-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19)
# Canonical T-pose; HumanML's joint ordering and hierarchy are from paramUtil.py.
REST_DIRECTIONS = np.array([(0, 0, 0), (1, 0, 0), (-1, 0, 0), (0, 1, 0),
                            (0, -1, 0), (0, -1, 0), (0, 1, 0), (0, -1, 0),
                            (0, -1, 0), (0, 1, 0), (0, 0, 1), (0, 0, 1),
                            (0, 1, 0), (1, 0, 0), (-1, 0, 0), (0, 1, 0),
                            (1, 0, 0), (-1, 0, 0), (1, 0, 0), (-1, 0, 0),
                            (1, 0, 0), (-1, 0, 0)], dtype=float)


def load_motion_archive(path: Path) -> tuple[np.ndarray, float, dict]:
    with np.load(path, allow_pickle=False) as archive:
        positions = np.asarray(archive["positions"], dtype=float)
        fps = float(archive["fps"])
        metadata = json.loads(str(archive["metadata"].item()))
    if positions.ndim != 3 or positions.shape[1:] != (22, 3) or not 2 <= len(positions) <= 196:
        raise ValueError("Expected one HumanML3D motion with 2–196 frames and 22 XYZ joints.")
    if not np.isfinite(positions).all() or not np.isfinite(fps) or fps <= 0:
        raise ValueError("Motion positions and frame rate must be finite; frame rate must be positive.")
    if not isinstance(metadata, dict):
        raise ValueError("Motion metadata must be a JSON object.")
    return positions, fps, metadata


def _align_vector(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source = source / np.linalg.norm(source)
    target = target / np.linalg.norm(target)
    cross = np.cross(source, target)
    dot = float(np.clip(source @ target, -1, 1))
    if dot < -1 + 1e-8:
        axis = np.cross(source, np.eye(3)[np.argmin(np.abs(source))])
        return Rotation.from_rotvec(axis / np.linalg.norm(axis) * np.pi).as_matrix()
    if np.linalg.norm(cross) < 1e-10:
        return np.eye(3)
    skew = np.array([[0, -cross[2], cross[1]], [cross[2], 0, -cross[0]], [-cross[1], cross[0], 0]])
    return np.eye(3) + skew + skew @ skew / (1 + dot)


def reconstruct_animation(positions: np.ndarray, fps: float, metadata: dict):
    children = [[j for j, p in enumerate(PARENTS) if p == i] for i in range(22)]
    offsets = REST_DIRECTIONS.copy()
    for joint in range(1, 22):
        length = np.median(np.linalg.norm(positions[:, joint] - positions[:, PARENTS[joint]], axis=1))
        if length < 1e-7:
            raise ValueError(f"Motion has a degenerate bone: {JOINT_NAMES[joint]}.")
        offsets[joint] *= length
    global_rot = np.zeros((len(positions), 22, 3, 3))
    local_rot = np.zeros_like(global_rot)
    local_pos = np.zeros_like(positions)
    for frame, pose in enumerate(positions):
        for joint, parent in enumerate(PARENTS):
            parent_rotation = global_rot[frame, parent] if parent >= 0 else np.eye(3)
            valid = [c for c in children[joint] if np.linalg.norm(pose[c] - pose[joint]) > 1e-7]
            if valid:
                source = offsets[valid]
                target = pose[valid] - pose[joint]
                source = source / np.linalg.norm(source, axis=1, keepdims=True)
                target = target / np.linalg.norm(target, axis=1, keepdims=True)
                if len(valid) > 1 and np.linalg.matrix_rank(source) > 1 and np.linalg.matrix_rank(target) > 1:
                    u, _, vt = np.linalg.svd(target.T @ source)
                    rotation = u @ np.diag([1, 1, np.linalg.det(u @ vt)]) @ vt
                else:
                    # Parallel transport the prior orientation to avoid arbitrary
                    # twist flips along single-child limbs between frames.
                    previous = global_rot[frame - 1, joint] if frame else parent_rotation
                    rotation = _align_vector(previous @ source[0], target[0]) @ previous
            else:
                rotation = parent_rotation
            global_rot[frame, joint] = rotation
            local_rot[frame, joint] = parent_rotation.T @ rotation
            local_pos[frame, joint] = parent_rotation.T @ (pose[joint] - pose[parent]) if parent >= 0 else pose[joint]
    metadata = {**metadata, "rotation_method": "geometric XYZ reconstruction; twist is underdetermined",
                "bvh_note": "Fixed median bone lengths; skeleton preview/blocking, not a fitted production rig.",
                "units": "metres", "up_axis": "Y", "frame_count": len(positions)}
    bind_globals = np.zeros((22, 3))
    joints = []
    tracks = []
    quaternions = Rotation.from_matrix(local_rot.reshape(-1, 3, 3)).as_quat().reshape(len(positions), 22, 4)
    for index, (name, parent) in enumerate(zip(JOINT_NAMES, PARENTS)):
        bind_globals[index] = offsets[index] + (bind_globals[parent] if parent >= 0 else 0)
        inverse_bind = np.eye(4)
        inverse_bind[:3, 3] = -bind_globals[index]
        joints.append(Joint(name, parent, JointTransform(translation=tuple(offsets[index])),
                            tuple(inverse_bind.ravel())))
        tracks.append(JointAnimationTrack(
            name,
            translation_keys=[Vec3Keyframe(f / fps, tuple(local_pos[f, index])) for f in range(len(positions))],
            rotation_keys=[QuatKeyframe(f / fps, tuple(quaternions[f, index])) for f in range(len(positions))],
        ))
    skeleton = SkeletonAsset("HumanML3D", joints, metadata)
    clip = AnimationClip("GeneratedMotion", 0, (len(positions) - 1) / fps, fps, tracks, metadata)
    skeleton.validate()
    clip.validate(skeleton)
    return skeleton, clip, local_rot, offsets


def write_bvh(path: Path, positions: np.ndarray, fps: float, local_rot: np.ndarray, offsets: np.ndarray) -> None:
    lines = ["HIERARCHY"]
    order = []

    def visit(index: int, depth: int) -> None:
        indent = "  " * depth
        order.append(index)
        lines.extend([f"{indent}{'ROOT' if index == 0 else 'JOINT'} {JOINT_NAMES[index]}", indent + "{"])
        lines.append(indent + "  OFFSET " + " ".join(f"{v:.9f}" for v in offsets[index]))
        channels = "6 Xposition Yposition Zposition Zrotation Xrotation Yrotation" if index == 0 else "3 Zrotation Xrotation Yrotation"
        lines.append(indent + "  CHANNELS " + channels)
        children = [j for j, p in enumerate(PARENTS) if p == index]
        for child in children:
            visit(child, depth + 1)
        if not children:
            lines.extend([indent + "  End Site", indent + "  {", indent + "    OFFSET 0 0 0", indent + "  }"])
        lines.append(indent + "}")

    visit(0, 0)
    lines.extend(["MOTION", f"Frames: {len(positions)}", f"Frame Time: {1 / fps:.12f}"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # Euler gimbal lock preserves the same orientation.
        angles = Rotation.from_matrix(local_rot.reshape(-1, 3, 3)).as_euler("ZXY", degrees=True).reshape(len(positions), 22, 3)
    for frame in range(len(positions)):
        values = list(positions[frame, 0])
        for joint in order:
            values.extend(angles[frame, joint])
        lines.append(" ".join(f"{value:.9f}" for value in values))
    with path.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def save_generated_motion(run_dir: Path) -> GeneratedMotion:
    archive = run_dir / "motion.npz"
    positions, fps, metadata = load_motion_archive(archive)
    skeleton, clip, rotations, offsets = reconstruct_animation(positions, fps, metadata)
    animation = run_dir / "animation.json"
    bvh = run_dir / "preview.bvh"
    if animation.exists() or bvh.exists():
        raise FileExistsError("Converted output already exists; load the saved result instead.")
    write_bvh(bvh, positions, fps, rotations, offsets)
    with animation.open("x", encoding="utf-8") as stream:
        json.dump({"schema_version": 1, "skeleton": skeleton.to_dict(), "clip": clip.to_dict()}, stream, ensure_ascii=False)
    return GeneratedMotion(run_dir, archive, animation, bvh, clip.metadata)
