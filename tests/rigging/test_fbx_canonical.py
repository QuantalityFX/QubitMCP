from __future__ import annotations

import json
import unittest

from echograph.rigging.fbx_canonical import (
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


def _make_skeleton() -> SkeletonAsset:
    return SkeletonAsset(
        name="HeroSkeleton",
        joints=[
            Joint(
                name="root",
                parent_index=-1,
                local_bind=JointTransform(
                    translation=(0.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    scale=(1.0, 1.0, 1.0),
                ),
            ),
            Joint(
                name="hip",
                parent_index=0,
                local_bind=JointTransform(
                    translation=(0.0, 1.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    scale=(1.0, 1.0, 1.0),
                ),
            ),
        ],
    )


def _make_mesh() -> SkeletalMeshAsset:
    return SkeletalMeshAsset(
        name="HeroMesh",
        skeleton_name="HeroSkeleton",
        vertex_count=3,
        triangle_indices=[0, 1, 2],
        vertex_skins=[
            VertexSkin(
                vertex_index=0,
                influences=[VertexInfluence(joint_index=0, weight=1.0)],
            ),
            VertexSkin(
                vertex_index=1,
                influences=[
                    VertexInfluence(joint_index=0, weight=0.5),
                    VertexInfluence(joint_index=1, weight=0.5),
                ],
            ),
            VertexSkin(
                vertex_index=2,
                influences=[VertexInfluence(joint_index=1, weight=1.0)],
            ),
        ],
    )


def _make_clip() -> AnimationClip:
    return AnimationClip(
        name="Walk",
        start_time=0.0,
        end_time=1.0,
        sample_rate_hz=30.0,
        tracks=[
            JointAnimationTrack(
                joint_name="root",
                translation_keys=[
                    Vec3Keyframe(time=0.0, value=(0.0, 0.0, 0.0)),
                    Vec3Keyframe(time=1.0, value=(1.0, 0.0, 0.0)),
                ],
            ),
            JointAnimationTrack(
                joint_name="hip",
                rotation_keys=[
                    QuatKeyframe(time=0.0, value=(0.0, 0.0, 0.0, 1.0)),
                    QuatKeyframe(time=1.0, value=(0.0, 0.382683, 0.0, 0.923879)),
                ],
                scale_keys=[
                    Vec3Keyframe(time=0.0, value=(1.0, 1.0, 1.0)),
                    Vec3Keyframe(time=1.0, value=(1.0, 1.0, 1.0)),
                ],
            ),
        ],
    )


class CanonicalSchemaTests(unittest.TestCase):
    def test_skeleton_round_trip(self) -> None:
        skeleton = _make_skeleton()
        skeleton.validate()
        payload = json.loads(json.dumps(skeleton.to_dict()))
        round_trip = SkeletonAsset.from_dict(payload)
        self.assertEqual(round_trip, skeleton)

    def test_mesh_round_trip(self) -> None:
        skeleton = _make_skeleton()
        mesh = _make_mesh()
        mesh.validate(skeleton)
        payload = json.loads(json.dumps(mesh.to_dict()))
        round_trip = SkeletalMeshAsset.from_dict(payload)
        round_trip.validate(skeleton)
        self.assertEqual(round_trip, mesh)

    def test_animation_clip_round_trip(self) -> None:
        skeleton = _make_skeleton()
        clip = _make_clip()
        clip.validate(skeleton)
        payload = json.loads(json.dumps(clip.to_dict()))
        round_trip = AnimationClip.from_dict(payload)
        round_trip.validate(skeleton)
        self.assertEqual(round_trip, clip)

    def test_duplicate_joint_name_is_rejected(self) -> None:
        skeleton = _make_skeleton()
        skeleton.joints[1].name = "root"
        with self.assertRaises(SchemaValidationError):
            skeleton.validate()

    def test_invalid_parent_index_is_rejected(self) -> None:
        skeleton = _make_skeleton()
        skeleton.joints[1].parent_index = 99
        with self.assertRaises(SchemaValidationError):
            skeleton.validate()

    def test_non_normalized_weights_are_rejected(self) -> None:
        skeleton = _make_skeleton()
        mesh = _make_mesh()
        mesh.vertex_skins[1].influences[1].weight = 0.6
        with self.assertRaises(SchemaValidationError):
            mesh.validate(skeleton)

    def test_track_joint_missing_in_skeleton_is_rejected(self) -> None:
        skeleton = _make_skeleton()
        clip = _make_clip()
        clip.tracks[1].joint_name = "arm"
        with self.assertRaises(SchemaValidationError):
            clip.validate(skeleton)

    def test_unsorted_key_times_are_rejected(self) -> None:
        clip = _make_clip()
        clip.tracks[0].translation_keys = [
            Vec3Keyframe(time=0.5, value=(0.0, 0.0, 0.0)),
            Vec3Keyframe(time=0.25, value=(0.0, 0.0, 0.0)),
        ]
        with self.assertRaises(SchemaValidationError):
            clip.validate()


if __name__ == "__main__":
    unittest.main()
