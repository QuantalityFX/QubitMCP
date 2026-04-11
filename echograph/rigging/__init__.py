"""Rigging utilities."""

from .fbx_canonical import (
    AnimationClip,
    Joint,
    JointAnimationTrack,
    JointTransform,
    QuatKeyframe,
    SchemaValidationError,
    SkeletalMeshAsset,
    SkeletonAsset,
    Vec3Keyframe,
    VertexInfluence,
    VertexSkin,
)

__all__ = [
    "SchemaValidationError",
    "JointTransform",
    "Joint",
    "SkeletonAsset",
    "VertexInfluence",
    "VertexSkin",
    "SkeletalMeshAsset",
    "Vec3Keyframe",
    "QuatKeyframe",
    "JointAnimationTrack",
    "AnimationClip",
]
