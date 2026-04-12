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
from .fbx_stage6_debug import (
    FBXStage6DebugError,
    SkeletonDebugSample,
    evaluate_skeleton_line_points,
    skeleton_line_points_from_evaluation,
    skeleton_line_points_from_global_matrices,
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
    "FBXStage6DebugError",
    "SkeletonDebugSample",
    "skeleton_line_points_from_global_matrices",
    "skeleton_line_points_from_evaluation",
    "evaluate_skeleton_line_points",
]
