from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from echograph.rigging.fbx_canonical import (
    AnimationClip,
    Joint,
    JointAnimationTrack,
    JointTransform,
    QuatKeyframe,
    SkeletonAsset,
    Vec3Keyframe,
)
from echograph.rigging.fbx_stage3_ingest import FBXBindIngestResult
from echograph.rigging.fbx_stage4_animation import (
    FBXAnimationIngestError,
    FBXAnimationIngestResult,
)
from echograph.rigging.bvh_ingest import BVHAnimationIngestResult
from nodes.fbx_import.spec import resolve_fbx_import_sources


@dataclass
class _FakeModel:
    name: str
    kind: str
    params: list[dict]


class _FakeNodeItem:
    def __init__(self, model: _FakeModel, scene=None):
        self.model = model
        self._scene = scene

    def scene(self):
        return self._scene


class _FakeScene:
    def _ordered_in_edges(self, _item):
        return []

    def _in_edges(self, _item):
        return []


def _param_list(**kwargs) -> list[dict]:
    return [{"name": key, "value": str(value)} for key, value in kwargs.items()]


def _skeleton(name: str) -> SkeletonAsset:
    return SkeletonAsset(
        name=name,
        joints=[
            Joint(name="root", parent_index=-1, local_bind=JointTransform()),
            Joint(name="hip", parent_index=0, local_bind=JointTransform()),
        ],
    )


def _clip(name: str, joint_name: str = "hip") -> AnimationClip:
    return AnimationClip(
        name=name,
        start_time=0.0,
        end_time=1.0,
        sample_rate_hz=30.0,
        tracks=[
            JointAnimationTrack(
                joint_name=joint_name,
                translation_keys=[
                    Vec3Keyframe(time=0.0, value=(0.0, 0.0, 0.0)),
                    Vec3Keyframe(time=1.0, value=(1.0, 0.0, 0.0)),
                ],
                rotation_keys=[
                    QuatKeyframe(time=0.0, value=(0.0, 0.0, 0.0, 1.0)),
                ],
                scale_keys=[],
            )
        ],
    )


class FbxImportStage4AnimationValidationTests(unittest.TestCase):
    def test_animated_override_persists_when_ingest_succeeds(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        animated = Path("V:/virtual/anim.fbx")
        model = _FakeModel(
            name="FBXImportStage4A",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose="", animated_pose=str(animated)),
        )
        node = _FakeNodeItem(model, _FakeScene())

        rest_bind = FBXBindIngestResult(source_path=str(rest), skeleton=_skeleton("Rig"))
        rest_anim = FBXAnimationIngestResult(source_path=str(rest), clips=[_clip("RestClip")])
        animated_anim = FBXAnimationIngestResult(source_path=str(animated), clips=[_clip("RunClip")])

        def _resolve(raw, _base):
            text = str(raw)
            if text == str(rest):
                return rest
            if text == str(animated):
                return animated
            return None

        with patch("nodes.fbx_import.spec._resolve_existing_path", side_effect=_resolve), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            return_value=rest_bind,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_animation_data",
            side_effect=[rest_anim, animated_anim],
        ):
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
                validate_animation_data=True,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.effective_sources["animated_pose"], str(animated))
        self.assertIs(getattr(model, "_fbx_anim_animated_result", None), animated_anim)

    def test_failed_animated_override_falls_back_to_rest(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        animated = Path("V:/virtual/anim_bad.fbx")
        model = _FakeModel(
            name="FBXImportStage4B",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose="", animated_pose=str(animated)),
        )
        node = _FakeNodeItem(model, _FakeScene())

        rest_bind = FBXBindIngestResult(source_path=str(rest), skeleton=_skeleton("Rig"))
        rest_anim = FBXAnimationIngestResult(source_path=str(rest), clips=[_clip("RestClip")])

        def _resolve(raw, _base):
            text = str(raw)
            if text == str(rest):
                return rest
            if text == str(animated):
                return animated
            return None

        with patch("nodes.fbx_import.spec._resolve_existing_path", side_effect=_resolve), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            return_value=rest_bind,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_animation_data",
            side_effect=[rest_anim, FBXAnimationIngestError("bad animation source")],
        ):
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
                validate_animation_data=True,
            )

        self.assertEqual(result.status, "warning")
        self.assertEqual(result.effective_sources["animated_pose"], str(rest))
        self.assertTrue(
            any("using rest_geometry animation clips" in msg for msg in result.warnings)
        )

    def test_backend_unavailable_warning_is_non_fatal_for_animation(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        model = _FakeModel(
            name="FBXImportStage4C",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose="", animated_pose=""),
        )
        node = _FakeNodeItem(model, _FakeScene())

        rest_bind = FBXBindIngestResult(source_path=str(rest), skeleton=_skeleton("Rig"))

        with patch(
            "nodes.fbx_import.spec._resolve_existing_path",
            side_effect=lambda raw, _base: rest if str(raw) == str(rest) else None,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            return_value=rest_bind,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_animation_data",
            side_effect=FBXAnimationIngestError("fbx sdk unavailable: No module named 'fbx'"),
        ):
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
                validate_animation_data=True,
            )

        self.assertEqual(result.status, "warning")
        self.assertEqual(result.errors, [])
        self.assertTrue(any("stage4 animation ingest skipped" in msg for msg in result.warnings))
        self.assertTrue(
            any("limited FBX animation compatibility" in msg for msg in result.warnings)
        )

    def test_large_frame0_bind_mismatch_surfaces_warning(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        model = _FakeModel(
            name="FBXImportStage4D",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose="", animated_pose=""),
        )
        node = _FakeNodeItem(model, _FakeScene())

        rest_bind = FBXBindIngestResult(source_path=str(rest), skeleton=_skeleton("Rig"))
        rest_anim = FBXAnimationIngestResult(source_path=str(rest), clips=[_clip("RestClip")])

        eval_obj = type(
            "_Eval",
            (),
            {
                "local_transforms": [
                    JointTransform(
                        translation=(0.0, 0.0, 0.0),
                        rotation=(0.0, 1.0, 0.0, 0.0),
                        scale=(1.0, 1.0, 1.0),
                    ),
                    JointTransform(
                        translation=(0.0, 0.0, 0.0),
                        rotation=(0.0, 1.0, 0.0, 0.0),
                        scale=(1.0, 1.0, 1.0),
                    ),
                ]
            },
        )()

        with patch(
            "nodes.fbx_import.spec._resolve_existing_path",
            side_effect=lambda raw, _base: rest if str(raw) == str(rest) else None,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            return_value=rest_bind,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_animation_data",
            return_value=rest_anim,
        ), patch(
            "nodes.fbx_import.spec.evaluate_rig_at_time",
            return_value=eval_obj,
        ):
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
                validate_animation_data=True,
            )

        self.assertEqual(result.status, "warning")
        self.assertTrue(
            any("frame0 differs strongly from bind pose" in msg for msg in result.warnings)
        )

    def test_bvh_animated_pose_uses_bvh_ingest(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        animated = Path("V:/virtual/walk.bvh")
        model = _FakeModel(
            name="FBXImportStage4E",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose="", animated_pose=str(animated)),
        )
        node = _FakeNodeItem(model, _FakeScene())

        skeleton = _skeleton("Rig")
        rest_bind = FBXBindIngestResult(source_path=str(rest), skeleton=skeleton)
        rest_anim = FBXAnimationIngestResult(source_path=str(rest), clips=[_clip("RestClip")])
        bvh_anim = BVHAnimationIngestResult(source_path=str(animated), skeleton=skeleton, clips=[_clip("WalkClip")])

        def _resolve(raw, _base):
            text = str(raw)
            if text == str(rest):
                return rest
            if text == str(animated):
                return animated
            return None

        with patch("nodes.fbx_import.spec._resolve_existing_path", side_effect=_resolve), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            return_value=rest_bind,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_animation_data",
            return_value=rest_anim,
        ), patch(
            "nodes.fbx_import.spec.ingest_bvh_animation_data",
            return_value=bvh_anim,
        ) as bvh_mock:
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
                validate_animation_data=True,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.effective_sources["animated_pose"], str(animated))
        self.assertIs(getattr(model, "_fbx_anim_animated_result", None), bvh_anim)
        bvh_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
