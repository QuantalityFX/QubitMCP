from __future__ import annotations

import math
import unittest

from echograph.rigging.fbx_canonical import (
    AnimationClip,
    Joint,
    JointAnimationTrack,
    JointTransform,
    QuatKeyframe,
    SkeletonAsset,
    Vec3Keyframe,
)
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time


def _translation_matrix(tx: float, ty: float, tz: float):
    return (
        1.0, 0.0, 0.0, float(tx),
        0.0, 1.0, 0.0, float(ty),
        0.0, 0.0, 1.0, float(tz),
        0.0, 0.0, 0.0, 1.0,
    )


def _mat_almost_equal(a, b, places=6):
    if len(a) != len(b):
        return False
    for i in range(len(a)):
        if round(float(a[i]) - float(b[i]), places) != 0:
            return False
    return True


def _make_skeleton() -> SkeletonAsset:
    return SkeletonAsset(
        name="Rig",
        joints=[
            Joint(
                name="root",
                parent_index=-1,
                local_bind=JointTransform(
                    translation=(1.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    scale=(1.0, 1.0, 1.0),
                ),
                inverse_bind_matrix=_translation_matrix(-1.0, 0.0, 0.0),
            ),
            Joint(
                name="hip",
                parent_index=0,
                local_bind=JointTransform(
                    translation=(0.0, 2.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    scale=(1.0, 1.0, 1.0),
                ),
                inverse_bind_matrix=_translation_matrix(0.0, -2.0, 0.0),
            ),
        ],
    )


def _make_clip() -> AnimationClip:
    q0 = (0.0, 0.0, 0.0, 1.0)
    qz_180 = (0.0, 0.0, 1.0, 0.0)
    return AnimationClip(
        name="Move",
        start_time=0.0,
        end_time=2.0,
        sample_rate_hz=30.0,
        tracks=[
            JointAnimationTrack(
                joint_name="root",
                translation_keys=[
                    Vec3Keyframe(time=0.0, value=(1.0, 0.0, 0.0)),
                    Vec3Keyframe(time=2.0, value=(3.0, 0.0, 0.0)),
                ],
                rotation_keys=[],
                scale_keys=[],
            ),
            JointAnimationTrack(
                joint_name="hip",
                translation_keys=[],
                rotation_keys=[
                    QuatKeyframe(time=0.0, value=q0),
                    QuatKeyframe(time=2.0, value=qz_180),
                ],
                scale_keys=[],
            ),
        ],
    )


class FbxStage5EvaluatorTests(unittest.TestCase):
    def test_bind_pose_without_clip(self) -> None:
        skeleton = _make_skeleton()
        result = evaluate_rig_at_time(skeleton, None, time_seconds=1.25)

        self.assertEqual(result.sampled_time, 1.25)
        self.assertEqual(result.clip_name, None)
        self.assertEqual(len(result.local_transforms), 2)
        self.assertEqual(result.local_transforms[0].translation, (1.0, 0.0, 0.0))
        self.assertEqual(result.local_transforms[1].translation, (0.0, 2.0, 0.0))
        # Child global translation should include parent translation.
        self.assertAlmostEqual(result.global_matrices[1][3], 1.0)
        self.assertAlmostEqual(result.global_matrices[1][7], 2.0)
        self.assertAlmostEqual(result.global_matrices[1][11], 0.0)

    def test_clip_sampling_is_deterministic_and_interpolated(self) -> None:
        skeleton = _make_skeleton()
        clip = _make_clip()
        a = evaluate_rig_at_time(skeleton, clip, time_seconds=1.0)
        b = evaluate_rig_at_time(skeleton, clip, time_seconds=1.0)
        self.assertEqual(a, b)
        # Root translation interpolates from 1 -> 3 at t=1.0 (midpoint).
        self.assertAlmostEqual(a.local_transforms[0].translation[0], 2.0)
        # Hip rotation should be around 90 deg around Z at midpoint.
        q = a.local_transforms[1].rotation
        # z and w should both be around sqrt(0.5), sign-stable in this setup.
        self.assertAlmostEqual(abs(q[2]), math.sqrt(0.5), places=5)
        self.assertAlmostEqual(abs(q[3]), math.sqrt(0.5), places=5)

    def test_loop_and_clamp_time_modes(self) -> None:
        skeleton = _make_skeleton()
        clip = _make_clip()

        clamped = evaluate_rig_at_time(skeleton, clip, time_seconds=9.0, loop=False)
        self.assertAlmostEqual(clamped.sampled_time, 2.0)
        self.assertAlmostEqual(clamped.local_transforms[0].translation[0], 3.0)

        looped = evaluate_rig_at_time(skeleton, clip, time_seconds=2.5, loop=True)
        # clip range [0,2], so 2.5 wraps to 0.5
        self.assertAlmostEqual(looped.sampled_time, 0.5)
        self.assertAlmostEqual(looped.local_transforms[0].translation[0], 1.5)

    def test_skin_matrices_use_global_times_inverse_bind(self) -> None:
        skeleton = _make_skeleton()
        result = evaluate_rig_at_time(skeleton, None, time_seconds=0.0)

        expected_root_skin = _translation_matrix(0.0, 0.0, 0.0)
        expected_hip_skin = _translation_matrix(1.0, 0.0, 0.0)
        self.assertTrue(_mat_almost_equal(result.skin_matrices[0], expected_root_skin))
        self.assertTrue(_mat_almost_equal(result.skin_matrices[1], expected_hip_skin))

    def test_missing_channels_fall_back_to_bind(self) -> None:
        skeleton = _make_skeleton()
        clip = AnimationClip(
            name="OnlyRootTx",
            start_time=0.0,
            end_time=1.0,
            sample_rate_hz=30.0,
            tracks=[
                JointAnimationTrack(
                    joint_name="root",
                    translation_keys=[
                        Vec3Keyframe(time=0.0, value=(1.0, 0.0, 0.0)),
                        Vec3Keyframe(time=1.0, value=(2.0, 0.0, 0.0)),
                    ],
                    rotation_keys=[],
                    scale_keys=[],
                )
            ],
        )
        result = evaluate_rig_at_time(skeleton, clip, time_seconds=0.25)
        # hip has no track; should remain bind local
        self.assertEqual(result.local_transforms[1].translation, (0.0, 2.0, 0.0))
        self.assertEqual(result.local_transforms[1].scale, (1.0, 1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
