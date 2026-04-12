from __future__ import annotations

import unittest

from echograph.rigging.fbx_canonical import Joint, JointTransform, SkeletonAsset
from echograph.rigging.fbx_stage4_animation import (
    FBXAnimationIngestError,
    ingest_fbx_animation_data,
)


class _Channel:
    def __init__(
        self,
        node_name: str,
        *,
        positionkeys=None,
        rotationkeys=None,
        scalingkeys=None,
    ):
        self.node_name = node_name
        self.positionkeys = list(positionkeys or [])
        self.rotationkeys = list(rotationkeys or [])
        self.scalingkeys = list(scalingkeys or [])


class _Animation:
    def __init__(self, name: str, *, duration: float, tickspersecond: float, channels):
        self.name = name
        self.duration = float(duration)
        self.tickspersecond = float(tickspersecond)
        self.channels = list(channels or [])


class _Scene:
    def __init__(self, animations):
        self.animations = list(animations or [])


def _skeleton() -> SkeletonAsset:
    return SkeletonAsset(
        name="Rig",
        joints=[
            Joint(name="root", parent_index=-1, local_bind=JointTransform()),
            Joint(name="hip", parent_index=0, local_bind=JointTransform()),
        ],
    )


class FbxStage4AnimationTests(unittest.TestCase):
    def test_ticks_to_seconds_mapping(self) -> None:
        scene = _Scene(
            animations=[
                _Animation(
                    "Walk",
                    duration=48.0,
                    tickspersecond=24.0,
                    channels=[
                        _Channel(
                            "hip",
                            positionkeys=[
                                (0.0, (0.0, 0.0, 0.0)),
                                (24.0, (1.0, 0.0, 0.0)),
                                (48.0, (2.0, 0.0, 0.0)),
                            ],
                        )
                    ],
                )
            ]
        )
        result = ingest_fbx_animation_data(
            "V:/virtual/walk.fbx",
            scene=scene,
            skeleton=_skeleton(),
        )

        self.assertEqual(len(result.clips), 1)
        clip = result.clips[0]
        self.assertEqual(clip.name, "Walk")
        self.assertAlmostEqual(clip.start_time, 0.0)
        self.assertAlmostEqual(clip.end_time, 2.0)
        self.assertAlmostEqual(clip.sample_rate_hz, 24.0)
        self.assertEqual(len(clip.tracks), 1)
        track = clip.tracks[0]
        self.assertEqual(track.joint_name, "hip")
        self.assertEqual([round(k.time, 6) for k in track.translation_keys], [0.0, 1.0, 2.0])

    def test_unknown_tracks_are_filtered_against_skeleton(self) -> None:
        scene = _Scene(
            animations=[
                _Animation(
                    "UnknownOnly",
                    duration=30.0,
                    tickspersecond=30.0,
                    channels=[
                        _Channel(
                            "spine_extra",
                            positionkeys=[(0.0, (0.0, 0.0, 0.0)), (30.0, (0.0, 1.0, 0.0))],
                        )
                    ],
                )
            ]
        )
        result = ingest_fbx_animation_data(
            "V:/virtual/unknown.fbx",
            scene=scene,
            skeleton=_skeleton(),
        )

        self.assertEqual(result.clips, [])
        self.assertTrue(
            any("ignored because they are not part of the active skeleton" in msg for msg in result.warnings)
        )

    def test_unsorted_duplicate_keys_are_deduped_deterministically(self) -> None:
        scene = _Scene(
            animations=[
                _Animation(
                    "Scrambled",
                    duration=24.0,
                    tickspersecond=24.0,
                    channels=[
                        _Channel(
                            "hip",
                            positionkeys=[
                                (24.0, (1.0, 0.0, 0.0)),
                                (0.0, (0.0, 0.0, 0.0)),
                                (12.0, (0.5, 0.0, 0.0)),
                                (24.0, (1.1, 0.0, 0.0)),
                            ],
                        )
                    ],
                )
            ]
        )
        result = ingest_fbx_animation_data(
            "V:/virtual/scrambled.fbx",
            scene=scene,
            skeleton=_skeleton(),
        )

        track = result.clips[0].tracks[0]
        self.assertEqual([round(k.time, 6) for k in track.translation_keys], [0.0, 0.5, 1.0])
        # duplicate 24-tick key keeps deterministic last value
        self.assertAlmostEqual(track.translation_keys[-1].value[0], 1.1)

    def test_ingest_without_scene_requires_existing_path(self) -> None:
        with self.assertRaises(FBXAnimationIngestError):
            ingest_fbx_animation_data("V:/virtual/path_does_not_exist.fbx")


if __name__ == "__main__":
    unittest.main()
