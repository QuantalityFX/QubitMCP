from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

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


@dataclass
class _FakeEdge:
    src: _FakeNodeItem
    dst_port_name: str


class _FakeScene:
    def __init__(self, edge_map: dict):
        self._edge_map = edge_map

    def _ordered_in_edges(self, item):
        return list(self._edge_map.get(item, []))

    def _in_edges(self, item):
        return self._ordered_in_edges(item)


def _param_list(**kwargs) -> list[dict]:
    return [{"name": key, "value": str(value)} for key, value in kwargs.items()]


class FbxImportStage2Tests(unittest.TestCase):
    def test_rest_param_resolves_and_fills_optional_roles(self) -> None:
        model = _FakeModel(
            name="FBXImportA",
            kind="fbx_import",
            params=_param_list(rest_geometry="rest.fbx", capture_pose="", animated_pose=""),
        )
        scene = _FakeScene({})
        node = _FakeNodeItem(model, scene)
        resolved_rest = Path("V:/virtual/rest.fbx")

        with patch(
            "nodes.fbx_import.spec._resolve_existing_path",
            side_effect=lambda raw, _base: resolved_rest if str(raw) == "rest.fbx" else None,
        ):
            result = resolve_fbx_import_sources(node, base_dir=Path("V:/virtual"))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.effective_sources["rest_geometry"], str(resolved_rest))
        self.assertEqual(result.effective_sources["capture_pose"], str(resolved_rest))
        self.assertEqual(result.effective_sources["animated_pose"], str(resolved_rest))
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])

    def test_invalid_capture_override_falls_back_to_rest(self) -> None:
        model = _FakeModel(
            name="FBXImportB",
            kind="fbx_import",
            params=_param_list(
                rest_geometry="rest.fbx",
                capture_pose="missing_capture.fbx",
                animated_pose="",
            ),
        )
        scene = _FakeScene({})
        node = _FakeNodeItem(model, scene)
        resolved_rest = Path("V:/virtual/rest.fbx")

        with patch(
            "nodes.fbx_import.spec._resolve_existing_path",
            side_effect=lambda raw, _base: resolved_rest if str(raw) == "rest.fbx" else None,
        ):
            result = resolve_fbx_import_sources(node, base_dir=Path("V:/virtual"))

        self.assertEqual(result.status, "warning")
        self.assertEqual(result.effective_sources["rest_geometry"], str(resolved_rest))
        self.assertEqual(result.effective_sources["capture_pose"], str(resolved_rest))
        self.assertTrue(any("capture_pose" in msg and "override ignored" in msg for msg in result.warnings))

    def test_multiple_wires_use_first_edge_deterministically(self) -> None:
        first = Path("V:/virtual/first.fbx")
        second = Path("V:/virtual/second.fbx")

        src1 = _FakeNodeItem(
            _FakeModel("Import1", "import", _param_list(path=str(first))),
            None,
        )
        src2 = _FakeNodeItem(
            _FakeModel("Import2", "import", _param_list(path=str(second))),
            None,
        )
        dst = _FakeNodeItem(
            _FakeModel("FBXImportC", "fbx_import", _param_list(rest_geometry="", capture_pose="", animated_pose="")),
            None,
        )
        scene = _FakeScene(
            {
                dst: [
                    _FakeEdge(src=src2, dst_port_name="rest_geometry"),
                    _FakeEdge(src=src1, dst_port_name="rest_geometry"),
                ]
            }
        )
        dst._scene = scene

        def _fake_resolve(raw, _base):
            text = str(raw)
            if text == str(first):
                return first
            if text == str(second):
                return second
            return None

        with patch("nodes.fbx_import.spec._resolve_existing_path", side_effect=_fake_resolve):
            result = resolve_fbx_import_sources(dst, base_dir=Path("V:/virtual"))

        self.assertEqual(result.effective_sources["rest_geometry"], str(second))
        self.assertTrue(any("multiple inputs connected" in msg for msg in result.warnings))

    def test_missing_rest_source_reports_error(self) -> None:
        clip = Path("V:/virtual/clip.fbx")
        model = _FakeModel(
            name="FBXImportD",
            kind="fbx_import",
            params=_param_list(rest_geometry="", capture_pose="", animated_pose=str(clip)),
        )
        scene = _FakeScene({})
        node = _FakeNodeItem(model, scene)

        with patch(
            "nodes.fbx_import.spec._resolve_existing_path",
            side_effect=lambda raw, _base: clip if str(raw) == str(clip) else None,
        ):
            result = resolve_fbx_import_sources(node, base_dir=Path("V:/virtual"))

        self.assertEqual(result.status, "error")
        self.assertTrue(any("rest_geometry" in msg for msg in result.errors))
        self.assertEqual(result.effective_sources["rest_geometry"], "")
        self.assertEqual(result.effective_sources["capture_pose"], "")
        self.assertEqual(result.effective_sources["animated_pose"], "")

    def test_mocap_import_can_feed_bvh_animated_pose(self) -> None:
        rest = Path("V:/virtual/rest.fbx")
        mocap = Path("V:/virtual/walk.bvh")
        src = _FakeNodeItem(
            _FakeModel("MocapA", "mocap_import", _param_list(path=str(mocap))),
            None,
        )
        dst = _FakeNodeItem(
            _FakeModel("FBXImportE", "fbx_import", _param_list(rest_geometry=str(rest), capture_pose="", animated_pose="")),
            None,
        )
        scene = _FakeScene({dst: [_FakeEdge(src=src, dst_port_name="animated_pose")]})
        dst._scene = scene

        def _resolve(raw, _base):
            text = str(raw)
            if text == str(rest):
                return rest
            if text == str(mocap):
                return mocap
            return None

        with patch("nodes.fbx_import.spec._resolve_existing_path", side_effect=_resolve):
            result = resolve_fbx_import_sources(dst, base_dir=Path("V:/virtual"))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.effective_sources["rest_geometry"], str(rest))
        self.assertEqual(result.effective_sources["animated_pose"], str(mocap))


if __name__ == "__main__":
    unittest.main()
