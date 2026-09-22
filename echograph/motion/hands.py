"""Decode and layer local hand rotations without importing torch or SMPL-X.

The built-in hand proportions are an explicit skeleton preview, not fitted SMPL-X
geometry. A production mesh needs a calibrated rig mapping in the retarget layer.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from echograph.rigging.fbx_canonical import Joint, JointTransform, JointAnimationTrack, QuatKeyframe
from .director import HandIntent

DIGITS = ("index", "middle", "pinky", "ring", "thumb")  # SMPL-X parameter order
FINGER_NAMES = tuple(f"{side}_{digit}{segment}" for side in ("left", "right")
                     for digit in DIGITS for segment in (1, 2, 3))


@dataclass
class HandMotionClip:
    rotations: np.ndarray  # [frame, 30, 4], local xyzw quaternions
    fps: float
    metadata: dict

    def validate(self):
        if self.rotations.ndim != 3 or self.rotations.shape[1:] != (30, 4) or len(self.rotations) < 2:
            raise ValueError("Expected at least two frames with 30 local finger quaternions.")
        if not np.isfinite(self.rotations).all() or not np.isfinite(self.fps) or self.fps <= 0:
            raise ValueError("Hand rotations and frame rate must be finite, with positive fps.")
        if not np.allclose(np.linalg.norm(self.rotations, axis=-1), 1.0, atol=1e-5):
            raise ValueError("Hand quaternions must have unit length.")


def decode_hand_features(features: np.ndarray, fps=25.0, metadata=None) -> HandMotionClip:
    features = np.asarray(features, dtype=float)
    if features.ndim != 2 or features.shape[1] != 274 or not 2 <= len(features) <= 10000:
        raise ValueError("Expected a frame-by-274 HandMDM feature array.")
    if not np.isfinite(features).all():
        raise ValueError("Hand features contain NaN or infinity.")
    data = features[:, 78:258].reshape(-1, 30, 6)
    first, second = data[..., :3], data[..., 3:]
    length = np.linalg.norm(first, axis=-1, keepdims=True)
    if (length < 1e-8).any():
        raise ValueError("Degenerate first axis in hand 6D rotations.")
    first = first / length
    second = second - np.sum(first * second, axis=-1, keepdims=True) * first
    length = np.linalg.norm(second, axis=-1, keepdims=True)
    if (length < 1e-8).any():
        raise ValueError("Collinear axes in hand 6D rotations.")
    second = second / length
    matrix = np.stack((first, second, np.cross(first, second)), axis=-2)
    # Matches released np_feats_to_smplx: negative axis-angle == inverse rotation.
    matrix = matrix.swapaxes(-1, -2)
    rotations = Rotation.from_matrix(matrix.reshape(-1, 3, 3)).as_quat().reshape(-1, 30, 4)
    # A consistent sign avoids interpolation/export discontinuities at +/-q.
    for frame in range(1, len(rotations)):
        flip = np.sum(rotations[frame - 1] * rotations[frame], axis=-1) < 0
        rotations[frame, flip] *= -1
    result = HandMotionClip(rotations, float(fps), dict(metadata or {}))
    result.validate()
    return result


def load_hand_archive(path: Path) -> HandMotionClip:
    with np.load(path, allow_pickle=False) as archive:
        metadata = json.loads(str(archive["metadata"].item()))
        if not isinstance(metadata, dict):
            raise ValueError("Hand archive metadata must be an object.")
        return decode_hand_features(archive["features"], float(archive["fps"]), metadata)


def add_preview_hands(skeleton):
    """Attach a named, scaled 3-bone-per-finger preview and explicit fingertip joints."""
    skeleton = deepcopy(skeleton)
    if any(name in skeleton.joint_names for name in FINGER_NAMES):
        raise ValueError("Preview hand creation requires a body-only skeleton.")
    globals_ = []
    for joint in skeleton.joints:
        position = np.asarray(joint.local_bind.translation, dtype=float)
        globals_.append(position + (globals_[joint.parent_index] if joint.parent_index >= 0 else 0))
    # Approximate palm/finger proportions, in metres for a 25 cm forearm.
    dimensions = {
        "index": ((0.065, 0, 0.026), (0.039, 0.026, 0.020)),
        "middle": ((0.070, 0, 0.003), (0.044, 0.029, 0.022)),
        "ring": ((0.065, 0, -0.020), (0.041, 0.027, 0.020)),
        "pinky": ((0.054, 0, -0.039), (0.031, 0.021, 0.018)),
        "thumb": ((0.026, -0.010, 0.037), (0.037, 0.026, 0.021)),
    }
    for side in ("left", "right"):
        wrist = skeleton.joint_names.index(f"{side}_wrist")
        sign = 1 if side == "left" else -1
        scale = np.clip(np.linalg.norm(skeleton.joints[wrist].local_bind.translation) / 0.25, 0.5, 2.0)
        for digit in DIGITS:
            base, lengths = dimensions[digit]
            parent = wrist
            for segment in (1, 2, 3, 4):
                name = f"{side}_{digit}{segment}" if segment < 4 else f"{side}_{digit}_tip"
                offset = np.array(base if segment == 1 else (lengths[segment - 2], 0, 0)) * scale
                offset[0] *= sign
                global_position = globals_[parent] + offset
                inverse = np.eye(4)
                inverse[:3, 3] = -global_position
                skeleton.joints.append(Joint(name, parent, JointTransform(translation=tuple(offset)), tuple(inverse.ravel())))
                globals_.append(global_position)
                parent = len(skeleton.joints) - 1
    skeleton.name += "_HandPreview"
    skeleton.metadata = {**skeleton.metadata, "hand_geometry": "approximate preview proportions; not fitted SMPL-X",
                         "finger_parameter_order": list(FINGER_NAMES)}
    skeleton.validate()
    return skeleton


def _sample_keys(keys, times, default):
    if not keys:
        return np.tile(default, (len(times), 1))
    if len(keys) == 1:
        return np.tile(keys[0].value, (len(times), 1))
    key_times = np.array([key.time for key in keys])
    return Slerp(key_times, Rotation.from_quat([key.value for key in keys]))(
        np.clip(times, key_times[0], key_times[-1])).as_quat()


def _unique_times(values):
    """Coalesce equivalent frame/event times at the canonical schema tolerance."""
    result = []
    for value in np.sort(values):
        if not result or value > result[-1] + 1e-8:
            result.append(float(value))
    return np.asarray(result)


def merge_hand_layer(skeleton, body_clip, hands: HandMotionClip, intent: HandIntent):
    """Replace only selected named finger tracks; retain every body track exactly.

    Source plays at native speed, holds its final pose, then blends back to the
    existing finger animation. The rig must use the SMPL-X local finger basis.
    """
    hands.validate()
    intent.validate(body_clip.end_time)
    clip = deepcopy(body_clip)
    clip.validate(skeleton)
    frame_count = int(round((clip.end_time - clip.start_time) * clip.sample_rate_hz)) + 1
    # Retain sub-frame event boundaries so short gestures cannot vanish between frames.
    times = _unique_times(np.concatenate((np.linspace(clip.start_time, clip.end_time, frame_count),
                                      [intent.start, intent.end, intent.start + intent.blend, intent.end - intent.blend])))
    source_times = np.arange(len(hands.rotations)) / hands.fps
    weight = ((times >= intent.start) & (times <= intent.end)).astype(float)
    if intent.blend:
        ramp = np.clip(np.minimum((times - intent.start) / intent.blend,
                                  (intent.end - times) / intent.blend), 0, 1)
        weight *= ramp * ramp * (3 - 2 * ramp)
    tracks = {track.joint_name: track for track in clip.tracks}
    joints = {joint.name: joint for joint in skeleton.joints}
    for index, name in enumerate(FINGER_NAMES):
        if intent.side != "both" and not name.startswith(intent.side + "_"):
            continue
        if name not in joints:
            raise ValueError(f"Hand rig mapping is missing joint: {name}")
        joint = joints[name]
        track = tracks.get(name, JointAnimationTrack(name))
        # Sample existing keys as well, preserving authored motion outside the event.
        joint_times = _unique_times(np.concatenate((times, [key.time for key in track.rotation_keys])))
        source = Slerp(source_times, Rotation.from_quat(hands.rotations[:, index]))(
            np.clip(joint_times - intent.start, 0, source_times[-1]))
        base = Rotation.from_quat(_sample_keys(track.rotation_keys, joint_times, joint.local_bind.rotation))
        joint_weight = np.interp(joint_times, times, weight)
        delta = (base.inv() * source).as_rotvec() * joint_weight[:, None]
        blended = (base * Rotation.from_rotvec(delta)).as_quat()
        track.rotation_keys = [QuatKeyframe(float(time), tuple(quaternion)) for time, quaternion in zip(joint_times, blended)]
        if name not in tracks:
            clip.tracks.append(track)
    clip.metadata = {**clip.metadata, "hand_intent": asdict(intent), "hand_source": hands.metadata,
                     "hand_timing": "native 25-fps motion, hold final pose, blend to original outside interval",
                     "wrist_ownership": "body tracks preserved; XYZ preview wrist twist remains approximate"}
    clip.validate(skeleton)
    return clip


def export_preview_bvh(path: Path, skeleton, clip):
    """Use the existing BVH exporter and sampling rate for the expanded skeleton."""
    from .conversion import write_bvh
    count = int(round(clip.duration * clip.sample_rate_hz)) + 1
    times = np.linspace(clip.start_time, clip.end_time, count)
    tracks = {track.joint_name: track for track in clip.tracks}
    rotations = []
    for joint in skeleton.joints:
        track = tracks.get(joint.name)
        quaternions = _sample_keys(track.rotation_keys if track else [], times, joint.local_bind.rotation)
        rotations.append(Rotation.from_quat(quaternions).as_matrix())
    positions = np.zeros((count, len(skeleton.joints), 3))
    root = tracks.get(skeleton.joints[0].name)
    if root and root.translation_keys:
        key_times = [key.time for key in root.translation_keys]
        keys = np.array([key.value for key in root.translation_keys])
        positions[:, 0] = np.stack([np.interp(times, key_times, keys[:, axis]) for axis in range(3)], axis=-1)
    write_bvh(path, positions, clip.sample_rate_hz, np.stack(rotations, axis=1),
              np.array([joint.local_bind.translation for joint in skeleton.joints]),
              names=skeleton.joint_names, parents=[joint.parent_index for joint in skeleton.joints])
