from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from echograph.rigging.fbx_canonical import Joint, JointTransform, SkeletonAsset
from echograph.rigging.fbx_stage3_ingest import (
    FBXBindIngestError,
    FBXBindIngestResult,
    SkeletonCompatibilityReport,
)
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


def _skeleton(name: str, include_hip: bool = True) -> SkeletonAsset:
    joints = [Joint(name="root", parent_index=-1, local_bind=JointTransform())]
    if include_hip:
        joints.append(Joint(name="hip", parent_index=0, local_bind=JointTransform()))
    return SkeletonAsset(name=name, joints=joints)


class FbxImportStage3BindValidationTests(unittest.TestCase):
    def test_bind_validation_compatible_capture_persists_results(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        capture = Path("V:/virtual/capture.fbx")
        model = _FakeModel(
            name="FBXImportStage3A",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose=str(capture), animated_pose=""),
        )
        node = _FakeNodeItem(model, _FakeScene())

        rest_result = FBXBindIngestResult(source_path=str(rest), skeleton=_skeleton("RestSkeleton"))
        capture_result = FBXBindIngestResult(
            source_path=str(capture),
            skeleton=_skeleton("RestSkeleton"),
        )

        def _resolve(raw, _base):
            text = str(raw)
            if text == str(rest):
                return rest
            if text == str(capture):
                return capture
            return None

        with patch("nodes.fbx_import.spec._resolve_existing_path", side_effect=_resolve), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            side_effect=[rest_result, capture_result],
        ) as ingest_mock, patch(
            "nodes.fbx_import.spec.compare_skeleton_layout",
            return_value=SkeletonCompatibilityReport(compatible=True),
        ):
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.effective_sources["rest_geometry"], str(rest))
        self.assertEqual(result.effective_sources["capture_pose"], str(capture))
        self.assertIs(getattr(model, "_fbx_bind_rest_result", None), rest_result)
        self.assertIs(getattr(model, "_fbx_bind_capture_result", None), capture_result)
        self.assertEqual(ingest_mock.call_count, 2)

    def test_incompatible_capture_falls_back_to_rest(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        capture = Path("V:/virtual/capture_bad.fbx")
        model = _FakeModel(
            name="FBXImportStage3B",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose=str(capture), animated_pose=""),
        )
        node = _FakeNodeItem(model, _FakeScene())

        rest_result = FBXBindIngestResult(source_path=str(rest), skeleton=_skeleton("RestSkeleton"))
        capture_result = FBXBindIngestResult(
            source_path=str(capture),
            skeleton=_skeleton("RestSkeleton", include_hip=False),
        )

        def _resolve(raw, _base):
            text = str(raw)
            if text == str(rest):
                return rest
            if text == str(capture):
                return capture
            return None

        with patch("nodes.fbx_import.spec._resolve_existing_path", side_effect=_resolve), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            side_effect=[rest_result, capture_result],
        ), patch(
            "nodes.fbx_import.spec.compare_skeleton_layout",
            return_value=SkeletonCompatibilityReport(
                compatible=False,
                errors=["Missing required joint 'hip'."],
            ),
        ):
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
            )

        self.assertEqual(result.status, "warning")
        self.assertEqual(result.effective_sources["capture_pose"], str(rest))
        self.assertTrue(any("override ignored" in msg for msg in result.warnings))

    def test_rest_bind_failure_is_error(self) -> None:
        rest = Path("V:/virtual/rest_bad.fbx")
        model = _FakeModel(
            name="FBXImportStage3C",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose="", animated_pose=""),
        )
        node = _FakeNodeItem(model, _FakeScene())

        with patch(
            "nodes.fbx_import.spec._resolve_existing_path",
            side_effect=lambda raw, _base: rest if str(raw) == str(rest) else None,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            side_effect=FBXBindIngestError("malformed bind data"),
        ):
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
            )

        self.assertEqual(result.status, "error")
        self.assertTrue(any("bind ingest failed" in msg for msg in result.errors))

    def test_backend_unavailable_downgrades_to_warning(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        model = _FakeModel(
            name="FBXImportStage3D",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose="", animated_pose=""),
        )
        node = _FakeNodeItem(model, _FakeScene())

        with patch(
            "nodes.fbx_import.spec._resolve_existing_path",
            side_effect=lambda raw, _base: rest if str(raw) == str(rest) else None,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            side_effect=FBXBindIngestError("pyassimp unavailable: No module named 'pyassimp'"),
        ):
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
            )

        self.assertEqual(result.status, "warning")
        self.assertEqual(result.errors, [])
        self.assertTrue(any("stage3 bind ingest skipped" in msg for msg in result.warnings))

    def test_backend_parser_limitation_downgrades_to_warning(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        model = _FakeModel(
            name="FBXImportStage3E",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose="", animated_pose=""),
        )
        node = _FakeNodeItem(model, _FakeScene())

        with patch(
            "nodes.fbx_import.spec._resolve_existing_path",
            side_effect=lambda raw, _base: rest if str(raw) == str(rest) else None,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            side_effect=FBXBindIngestError("Failed to load FBX via pyassimp: NULL pointer access"),
        ):
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
            )

        self.assertEqual(result.status, "warning")
        self.assertEqual(result.errors, [])
        self.assertTrue(any("stage3 bind ingest skipped" in msg for msg in result.warnings))
        self.assertTrue(any("NULL pointer access" in msg for msg in result.warnings))

    def test_parser_limitation_with_missing_fbxsdk_adds_guidance_warning(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        model = _FakeModel(
            name="FBXImportStage3F",
            kind="fbx_import",
            params=_param_list(rest_geometry=str(rest), capture_pose="", animated_pose=""),
        )
        node = _FakeNodeItem(model, _FakeScene())

        with patch(
            "nodes.fbx_import.spec._resolve_existing_path",
            side_effect=lambda raw, _base: rest if str(raw) == str(rest) else None,
        ), patch(
            "nodes.fbx_import.spec.ingest_fbx_bind_data",
            side_effect=FBXBindIngestError(
                "Failed to load FBX with available backends: "
                "fbx sdk: fbx sdk unavailable: No module named 'fbx' | "
                "pyassimp: Failed to load FBX via pyassimp: NULL pointer access"
            ),
        ):
            result = resolve_fbx_import_sources(
                node,
                base_dir=Path("V:/virtual"),
                persist=True,
                validate_bind_data=True,
            )

        self.assertEqual(result.status, "warning")
        self.assertEqual(result.errors, [])
        self.assertTrue(
            any("FBX SDK backend is not available" in msg for msg in result.warnings)
        )


if __name__ == "__main__":
    unittest.main()
