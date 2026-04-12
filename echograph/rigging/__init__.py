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
from .fbx_stage3_ingest import (
    FBXBindIngestError,
    FBXBindIngestResult,
    SkeletonCompatibilityReport,
    compare_skeleton_layout,
    ingest_fbx_bind_data,
)
from .fbx_stage4_animation import (
    FBXAnimationIngestError,
    FBXAnimationIngestResult,
    ingest_fbx_animation_data,
)
from .fbx_stage5_evaluator import (
    FBXEvaluatorError,
    RigEvaluationResult,
    evaluate_rig_at_time,
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
    "FBXBindIngestError",
    "FBXBindIngestResult",
    "SkeletonCompatibilityReport",
    "ingest_fbx_bind_data",
    "compare_skeleton_layout",
    "FBXAnimationIngestError",
    "FBXAnimationIngestResult",
    "ingest_fbx_animation_data",
    "FBXEvaluatorError",
    "RigEvaluationResult",
    "evaluate_rig_at_time",
]
