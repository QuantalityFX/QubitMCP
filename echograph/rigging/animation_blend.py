"""Prepend a bind-pose transition to an animation without resampling its tail."""
from __future__ import annotations

from dataclasses import replace
import math

from .fbx_canonical import (
    AnimationClip, JointAnimationTrack, JointTransform, QuatKeyframe,
    SkeletonAsset, Vec3Keyframe,
)
from .fbx_stage5_evaluator import _quat_slerp, _sample_track


class AnimationBlendError(ValueError):
    pass


def _joint_key(name: str) -> str:
    # FBX namespaces can differ between the character and its animation take.
    return str(name).rsplit("|", 1)[-1].rsplit(":", 1)[-1].casefold()


def compatible_joint_map(bind: SkeletonAsset, animated: SkeletonAsset) -> dict[str, str]:
    """Match names and parent relationships; blending does not retarget a rig."""
    bind.validate()
    animated.validate()
    output_names = {joint.name for joint in bind.joints}
    groups: dict[str, list[str]] = {}
    for joint in bind.joints:
        groups.setdefault(_joint_key(joint.name), []).append(joint.name)
    mapping = {}
    for joint in animated.joints:
        candidates = groups.get(_joint_key(joint.name), [])
        if joint.name in output_names:
            mapping[joint.name] = joint.name
        elif len(candidates) == 1:
            mapping[joint.name] = candidates[0]
    if not mapping or len(set(mapping.values())) != len(mapping):
        raise AnimationBlendError("Skeleton joints do not match uniquely. Retarget the animation to the bind-pose rig first.")
    parents = {joint.name: (bind.joints[joint.parent_index].name if joint.parent_index >= 0 else None)
               for joint in bind.joints}
    for joint in animated.joints:
        if joint.name not in mapping:
            continue
        parent = animated.joints[joint.parent_index].name if joint.parent_index >= 0 else None
        if (parent is not None and parent not in mapping) or parents[mapping[joint.name]] != mapping.get(parent):
            raise AnimationBlendError(f"Joint hierarchy differs at '{joint.name}'. Retarget the animation to the bind-pose rig first.")
    return mapping


def _frame_count(value: int, name: str) -> int:
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise AnimationBlendError(f"{name} must be a nonnegative whole number.") from None
    if not math.isfinite(number) or number < 0 or number != int(number):
        raise AnimationBlendError(f"{name} must be a nonnegative whole number.")
    return int(number)


def build_blended_clip(
    bind_skeleton: SkeletonAsset,
    animation_skeleton: SkeletonAsset,
    animation: AnimationClip,
    *,
    target_frame: int = 0,
    blend_frames: int = 30,
    hold_frames: int = 0,
    fps: float = 24.0,
    curve: str = "smooth",
    reference_pose: list[JointTransform] | None = None,
    name: str = "Blended Animation",
) -> AnimationClip:
    """Frame 0 is the bind pose; the tail retains its keys, speed and root motion.

    All frame numbers use ``fps`` and are relative to the clip's first frame.
    A zero-duration blend starts directly at the selected animation pose.
    """
    try:
        fps = float(fps)
    except (ValueError, TypeError):
        raise AnimationBlendError("Frame rate must be greater than zero.") from None
    if not math.isfinite(fps) or fps <= 0:
        raise AnimationBlendError("Frame rate must be greater than zero.")
    blend_frames = _frame_count(blend_frames, "Blend frames")
    hold_frames = _frame_count(hold_frames, "Hold pose frames")
    target_frame = _frame_count(target_frame, "Target frame")
    if curve not in {"linear", "smooth"}:
        raise AnimationBlendError("Blend curve must be Linear or Smooth.")
    if not all(math.isfinite(float(value)) for value in (animation.start_time, animation.end_time)):
        raise AnimationBlendError("The animation has an invalid time range.")
    animation.validate(skeleton=animation_skeleton)
    mapping = compatible_joint_map(bind_skeleton, animation_skeleton)
    unmapped = [track.joint_name for track in animation.tracks if track.joint_name not in mapping]
    if unmapped:
        raise AnimationBlendError("Animation joints are missing from the bind-pose rig: " + ", ".join(unmapped[:5])
                                  + ". Retarget the animation first.")
    if reference_pose is None:
        reference_pose = [joint.local_bind for joint in bind_skeleton.joints]
    if len(reference_pose) != len(bind_skeleton.joints):
        raise AnimationBlendError("The reference pose does not match the bind-pose skeleton.")
    for transform in reference_pose:
        transform.validate()

    target_time = min(animation.end_time, animation.start_time + target_frame / fps)
    hold_time = hold_frames / fps
    blend_time = blend_frames / fps
    join_time = hold_time + blend_time
    end_time = join_time + max(0.0, animation.end_time - target_time)
    reverse_map = {target: source for source, target in mapping.items()}
    source_joints = {joint.name: joint for joint in animation_skeleton.joints}
    source_tracks = {track.joint_name: track for track in animation.tracks}

    # Smooth easing needs only its transition sampled. Original animation keys
    # stay sparse and preserve step interpolation and fractional key times.
    if blend_frames == 0:
        transition = [(0.0, 1.0)]
    else:
        transition = [(0.0, 0.0)]
        if hold_frames:
            transition.append((hold_time, 0.0))
        sample_frames = range(1, blend_frames + 1) if curve == "smooth" else [blend_frames]
        for frame in sample_frames:
            alpha = frame / blend_frames
            if curve == "smooth":
                alpha = alpha * alpha * (3.0 - 2.0 * alpha)
            transition.append((hold_time + frame / fps, alpha))

    tracks = []
    for index, joint in enumerate(bind_skeleton.joints):
        reference = reference_pose[index]
        source_name = reverse_map.get(joint.name)
        original = source_tracks.get(source_name)
        source_bind = source_joints[source_name].local_bind if source_name else reference
        target = _sample_track(original, source_bind, target_time)
        if source_name is None:
            target = reference
        out = JointAnimationTrack(joint.name)
        for time, alpha in transition:
            out.translation_keys.append(Vec3Keyframe(time, tuple(a + (b - a) * alpha
                                                               for a, b in zip(reference.translation, target.translation))))
            out.rotation_keys.append(QuatKeyframe(time, _quat_slerp(reference.rotation, target.rotation, alpha)))
            out.scale_keys.append(Vec3Keyframe(time, tuple(a + (b - a) * alpha
                                                         for a, b in zip(reference.scale, target.scale))))
        if blend_frames == 0 and hold_frames:
            # Hold the bind pose, then switch at the end of a zero-length blend.
            for keys, value in ((out.translation_keys, reference.translation),
                                (out.rotation_keys, reference.rotation), (out.scale_keys, reference.scale)):
                target_key = keys.pop()
                keys.append(replace(target_key, value=tuple(value), interpolation="step"))
                keys.append(replace(target_key, time=join_time))
        if original is not None:
            for dest, source in ((out.translation_keys, original.translation_keys),
                                 (out.rotation_keys, original.rotation_keys), (out.scale_keys, original.scale_keys)):
                # The target pose must inherit the active key's interpolation
                # mode, including when the chosen frame falls between keys.
                preceding = [key for key in source if key.time <= target_time]
                if source:
                    active = preceding[-1] if preceding else source[0]
                    dest[-1].interpolation = active.interpolation
                dest.extend(replace(key, time=join_time + key.time - target_time)
                            for key in source if key.time > target_time)
        tracks.append(out)

    clip = AnimationClip(name, 0.0, end_time, fps, tracks, metadata={
        **animation.metadata,
        "animation_blend": True, "source_clip": animation.name,
        "target_frame": target_frame, "target_time": target_time,
        "blend_frames": blend_frames, "hold_frames": hold_frames,
        "blend_end_time": join_time, "frame_rate": fps, "curve": curve,
    })
    clip.validate(skeleton=bind_skeleton)
    return clip
