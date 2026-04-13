from __future__ import annotations

import unittest

from echograph.rigging.fbx_canonical import (
    AnimationClip,
    JointAnimationTrack,
    QuatKeyframe,
    Vec3Keyframe,
)
from echograph.rigging.fbx_stage7_timeline import (
    clip_marker_frames,
    clip_sample_time_from_timeline_seconds,
)


def _make_clip() -> AnimationClip:
    return AnimationClip(
        name="ClipA",
        start_time=41.625,
        end_time=67.58333333333333,
        sample_rate_hz=24.0,
        tracks=[
            JointAnimationTrack(
                joint_name="root",
                translation_keys=[
                    Vec3Keyframe(time=41.625, value=(0.0, 0.0, 0.0)),
                    Vec3Keyframe(time=42.0, value=(1.0, 0.0, 0.0)),
                    Vec3Keyframe(time=43.0, value=(2.0, 0.0, 0.0)),
                ],
                rotation_keys=[
                    QuatKeyframe(time=41.625, value=(0.0, 0.0, 0.0, 1.0)),
                    QuatKeyframe(time=43.0, value=(0.0, 0.0, 0.7071, 0.7071)),
                ],
                scale_keys=[],
            )
        ],
    )


class FbxStage7TimelineTests(unittest.TestCase):
    def test_clip_sample_time_uses_clip_start_offset(self) -> None:
        clip = _make_clip()
        self.assertAlmostEqual(
            clip_sample_time_from_timeline_seconds(clip, 0.0),
            41.625,
        )
        self.assertAlmostEqual(
            clip_sample_time_from_timeline_seconds(clip, 1.0),
            42.625,
        )

    def test_clip_sample_time_without_clip_passthrough(self) -> None:
        self.assertAlmostEqual(
            clip_sample_time_from_timeline_seconds(None, 2.5),
            2.5,
        )

    def test_clip_marker_frames_are_relative_to_clip_start(self) -> None:
        clip = _make_clip()
        frames = clip_marker_frames(clip, fps=24.0)
        self.assertIn(0, frames)
        self.assertIn(9, frames)  # 42.0 - 41.625 = 0.375 sec -> 9 frames @ 24fps
        self.assertIn(33, frames)  # 43.0 - 41.625 = 1.375 sec -> 33 frames @ 24fps
        self.assertIn(623, frames)  # clip end frame

    def test_clip_marker_frames_falls_back_to_clip_rate(self) -> None:
        clip = _make_clip()
        frames = clip_marker_frames(clip, fps=0.0)
        self.assertIn(9, frames)
        self.assertIn(623, frames)


if __name__ == "__main__":
    unittest.main()
