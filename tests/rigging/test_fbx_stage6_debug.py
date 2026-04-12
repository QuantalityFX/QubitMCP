from __future__ import annotations

import unittest

from echograph.rigging.fbx_canonical import (
    AnimationClip,
    Joint,
    JointAnimationTrack,
    JointTransform,
    SkeletonAsset,
    Vec3Keyframe,
)
from echograph.rigging.fbx_stage6_debug import (
    FBXStage6DebugError,
    evaluate_skeleton_line_points,
    skeleton_line_points_from_global_matrices,
)


def _make_skeleton() -> SkeletonAsset:
    return SkeletonAsset(
        name="Rig",
        joints=[
            Joint(name="root", parent_index=-1, local_bind=JointTransform()),
            Joint(
                name="hip",
                parent_index=0,
                local_bind=JointTransform(translation=(1.0, 0.0, 0.0)),
            ),
            Joint(
                name="knee",
                parent_index=1,
                local_bind=JointTransform(translation=(0.0, 2.0, 0.0)),
            ),
        ],
    )


def _make_root_translate_clip() -> AnimationClip:
    return AnimationClip(
        name="MoveRootX",
        start_time=0.0,
        end_time=1.0,
        sample_rate_hz=30.0,
        tracks=[
            JointAnimationTrack(
                joint_name="root",
                translation_keys=[
                    Vec3Keyframe(time=0.0, value=(0.0, 0.0, 0.0)),
                    Vec3Keyframe(time=1.0, value=(2.0, 0.0, 0.0)),
                ],
                rotation_keys=[],
                scale_keys=[],
            )
        ],
    )


class FbxStage6DebugTests(unittest.TestCase):
    def test_bind_pose_line_points(self) -> None:
        skeleton = _make_skeleton()
        sample = evaluate_skeleton_line_points(skeleton, None, time_seconds=0.0, loop=False)
        self.assertEqual(sample.skeleton_name, "Rig")
        self.assertEqual(sample.clip_name, None)
        self.assertEqual(
            sample.line_points,
            [
                (0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                (1.0, 0.0, 0.0), (1.0, 2.0, 0.0),
            ],
        )

    def test_clip_sampling_updates_line_points(self) -> None:
        skeleton = _make_skeleton()
        clip = _make_root_translate_clip()
        sample = evaluate_skeleton_line_points(skeleton, clip, time_seconds=0.5, loop=False)
        self.assertAlmostEqual(sample.sampled_time, 0.5)
        self.assertEqual(
            sample.line_points,
            [
                (1.0, 0.0, 0.0), (2.0, 0.0, 0.0),
                (2.0, 0.0, 0.0), (2.0, 2.0, 0.0),
            ],
        )

    def test_global_matrix_count_mismatch_raises(self) -> None:
        skeleton = _make_skeleton()
        with self.assertRaises(FBXStage6DebugError):
            skeleton_line_points_from_global_matrices(
                skeleton,
                [
                    (
                        1.0, 0.0, 0.0, 0.0,
                        0.0, 1.0, 0.0, 0.0,
                        0.0, 0.0, 1.0, 0.0,
                        0.0, 0.0, 0.0, 1.0,
                    )
                ],
            )


if __name__ == "__main__":
    unittest.main()
