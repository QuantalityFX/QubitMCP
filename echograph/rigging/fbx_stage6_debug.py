from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

from .fbx_canonical import AnimationClip, SkeletonAsset
from .fbx_stage5_evaluator import RigEvaluationResult, evaluate_rig_at_time


class FBXStage6DebugError(RuntimeError):
    """Raised when stage 6 skeleton debug geometry generation fails."""


def _matrix_translation(matrix: Sequence[float], joint_name: str) -> Tuple[float, float, float]:
    values = tuple(matrix or ())
    if len(values) != 16:
        raise FBXStage6DebugError(
            f"Joint '{joint_name}' global matrix must contain 16 values."
        )
    return (float(values[3]), float(values[7]), float(values[11]))


def skeleton_line_points_from_global_matrices(
    skeleton: SkeletonAsset,
    global_matrices: Sequence[Sequence[float]],
) -> List[Tuple[float, float, float]]:
    skeleton.validate()
    matrices = list(global_matrices or [])
    joint_count = len(skeleton.joints)
    if len(matrices) != joint_count:
        raise FBXStage6DebugError(
            f"Expected {joint_count} global matrices, got {len(matrices)}."
        )

    joint_world_positions: List[Tuple[float, float, float]] = []
    for index, joint in enumerate(skeleton.joints):
        joint_world_positions.append(_matrix_translation(matrices[index], joint.name))

    line_points: List[Tuple[float, float, float]] = []
    for index, joint in enumerate(skeleton.joints):
        parent_index = int(joint.parent_index)
        if parent_index < 0:
            continue
        if parent_index >= len(joint_world_positions):
            raise FBXStage6DebugError(
                f"Joint '{joint.name}' parent index {parent_index} is out of range."
            )
        line_points.append(joint_world_positions[parent_index])
        line_points.append(joint_world_positions[index])
    return line_points


def skeleton_line_points_from_evaluation(
    skeleton: SkeletonAsset,
    evaluation: RigEvaluationResult,
) -> List[Tuple[float, float, float]]:
    return skeleton_line_points_from_global_matrices(skeleton, evaluation.global_matrices)


@dataclass(eq=True)
class SkeletonDebugSample:
    skeleton_name: str
    sampled_time: float
    clip_name: str | None = None
    line_points: List[Tuple[float, float, float]] = field(default_factory=list)


def evaluate_skeleton_line_points(
    skeleton: SkeletonAsset,
    clip: AnimationClip | None,
    time_seconds: float,
    *,
    loop: bool = True,
) -> SkeletonDebugSample:
    evaluation = evaluate_rig_at_time(
        skeleton=skeleton,
        clip=clip,
        time_seconds=float(time_seconds),
        loop=bool(loop),
    )
    return SkeletonDebugSample(
        skeleton_name=skeleton.name,
        sampled_time=float(evaluation.sampled_time),
        clip_name=evaluation.clip_name,
        line_points=skeleton_line_points_from_evaluation(skeleton, evaluation),
    )


__all__ = [
    "FBXStage6DebugError",
    "SkeletonDebugSample",
    "skeleton_line_points_from_global_matrices",
    "skeleton_line_points_from_evaluation",
    "evaluate_skeleton_line_points",
]
