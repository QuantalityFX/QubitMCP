from __future__ import annotations

import unittest

from echograph.rigging.fbx_canonical import Joint, JointTransform, SkeletonAsset
from echograph.rigging.fbx_stage3_ingest import (
    FBXBindIngestError,
    _fbxsdk_skin_deformer_type,
    compare_skeleton_layout,
    ingest_fbx_bind_data,
)


_IDENTITY_4X4 = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)


class _Node:
    def __init__(self, name: str, *, children=None, transformation=None):
        self.name = name
        self.children = list(children or [])
        self.transformation = transformation or _IDENTITY_4X4


class _Weight:
    def __init__(self, vertexid: int, weight: float):
        self.vertexid = int(vertexid)
        self.weight = float(weight)


class _Bone:
    def __init__(self, name: str, *, weights=None, offsetmatrix=None):
        self.name = name
        self.weights = list(weights or [])
        self.offsetmatrix = offsetmatrix or _IDENTITY_4X4


class _Mesh:
    def __init__(self, name: str, *, vertices, faces, bones=None):
        self.name = name
        self.vertices = list(vertices)
        self.faces = list(faces)
        self.bones = list(bones or [])


class _Scene:
    def __init__(self, *, rootnode, meshes=None):
        self.rootnode = rootnode
        self.meshes = list(meshes or [])


def _make_scene_with_clamped_weights() -> _Scene:
    chest = _Node("chest")
    hip = _Node("hip", children=[chest])
    arm = _Node("arm")
    root = _Node("root", children=[hip, arm])

    mesh = _Mesh(
        "HeroMesh",
        vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        faces=[[0, 1, 2]],
        bones=[
            _Bone("hip", weights=[_Weight(1, 0.4), _Weight(0, 1.0)]),
            _Bone("chest", weights=[_Weight(1, 0.4)]),
            _Bone("arm", weights=[_Weight(1, 0.4)]),
        ],
    )
    return _Scene(rootnode=root, meshes=[mesh])


def _make_scene_with_missing_bone_node() -> _Scene:
    hip = _Node("hip")
    root = _Node("root", children=[hip])
    mesh = _Mesh(
        "Body",
        vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        faces=[[0, 1, 2]],
        bones=[
            _Bone("hip", weights=[_Weight(0, 1.0)]),
            _Bone("phantom", weights=[_Weight(1, 1.0)]),
        ],
    )
    return _Scene(rootnode=root, meshes=[mesh])


def _make_joints_only_scene() -> _Scene:
    neck = _Node("neck")
    spine = _Node("spine", children=[neck])
    hip = _Node("hip", children=[spine])
    root = _Node("root", children=[hip])
    return _Scene(rootnode=root, meshes=[])


def _make_base_skeleton() -> SkeletonAsset:
    return SkeletonAsset(
        name="HeroSkeleton",
        joints=[
            Joint(name="root", parent_index=-1, local_bind=JointTransform()),
            Joint(name="hip", parent_index=0, local_bind=JointTransform()),
        ],
    )


class FbxStage3IngestTests(unittest.TestCase):
    def test_fbxsdk_skin_deformer_type_supports_direct_enum(self) -> None:
        class _FbxDeformer:
            eSkin = "direct"

        class _Mod:
            FbxDeformer = _FbxDeformer

        self.assertEqual(_fbxsdk_skin_deformer_type(_Mod), "direct")

    def test_fbxsdk_skin_deformer_type_supports_nested_enum(self) -> None:
        class _EType:
            eSkin = "nested"

        class _FbxDeformer:
            EDeformerType = _EType

        class _Mod:
            FbxDeformer = _FbxDeformer

        self.assertEqual(_fbxsdk_skin_deformer_type(_Mod), "nested")

    def test_ingest_is_deterministic_and_clamps_weights(self) -> None:
        scene = _make_scene_with_clamped_weights()
        result_a = ingest_fbx_bind_data("V:/virtual/hero.fbx", scene=scene, max_influences=2)
        result_b = ingest_fbx_bind_data("V:/virtual/hero.fbx", scene=scene, max_influences=2)

        self.assertEqual(result_a, result_b)
        self.assertEqual([j.name for j in result_a.skeleton.joints], ["root", "arm", "hip", "chest"])
        self.assertTrue(any("clamped to 2" in w for w in result_a.warnings))

        mesh = result_a.meshes[0]
        self.assertEqual(mesh.vertex_count, 3)
        self.assertEqual(mesh.triangle_indices, [0, 1, 2])
        self.assertEqual(len(mesh.vertex_skins), 2)

        skin_vertex_0 = mesh.vertex_skins[0]
        self.assertEqual(skin_vertex_0.vertex_index, 0)
        self.assertEqual(len(skin_vertex_0.influences), 1)
        self.assertAlmostEqual(skin_vertex_0.influences[0].weight, 1.0)

        skin_vertex_1 = mesh.vertex_skins[1]
        self.assertEqual(skin_vertex_1.vertex_index, 1)
        self.assertEqual([inf.joint_index for inf in skin_vertex_1.influences], [1, 2])
        self.assertAlmostEqual(skin_vertex_1.influences[0].weight, 0.5)
        self.assertAlmostEqual(skin_vertex_1.influences[1].weight, 0.5)

    def test_missing_bone_node_creates_synthetic_joint(self) -> None:
        scene = _make_scene_with_missing_bone_node()
        result = ingest_fbx_bind_data("V:/virtual/missing.fbx", scene=scene)
        names = [j.name for j in result.skeleton.joints]
        self.assertIn("phantom", names)
        phantom_idx = names.index("phantom")
        self.assertEqual(result.skeleton.joints[phantom_idx].parent_index, -1)
        self.assertTrue(
            any("synthetic root joint created" in msg.lower() for msg in result.warnings)
        )

    def test_joints_only_scene_uses_hierarchy_skeleton(self) -> None:
        scene = _make_joints_only_scene()
        result = ingest_fbx_bind_data("V:/virtual/joints_only.fbx", scene=scene)
        self.assertEqual([j.name for j in result.skeleton.joints], ["root", "hip", "spine", "neck"])
        self.assertTrue(
            any("using node hierarchy as skeleton source" in msg.lower() for msg in result.warnings)
        )
        self.assertEqual(result.meshes, [])

    def test_compare_skeleton_layout_detects_mismatch(self) -> None:
        base = _make_base_skeleton()
        override = SkeletonAsset(
            name="OverrideSkeleton",
            joints=[
                Joint(name="root", parent_index=-1, local_bind=JointTransform()),
                Joint(name="hip", parent_index=-1, local_bind=JointTransform()),
            ],
        )
        report = compare_skeleton_layout(base, override)
        self.assertFalse(report.compatible)
        self.assertTrue(any("parent mismatch" in e.lower() for e in report.errors))

    def test_compare_skeleton_layout_allows_extras_with_warning(self) -> None:
        base = _make_base_skeleton()
        override = SkeletonAsset(
            name="OverrideSkeleton",
            joints=[
                Joint(name="root", parent_index=-1, local_bind=JointTransform()),
                Joint(name="hip", parent_index=0, local_bind=JointTransform()),
                Joint(name="arm", parent_index=1, local_bind=JointTransform()),
            ],
        )
        report = compare_skeleton_layout(base, override)
        self.assertTrue(report.compatible)
        self.assertTrue(any("extra joints" in w.lower() for w in report.warnings))

    def test_ingest_without_scene_requires_existing_path(self) -> None:
        with self.assertRaises(FBXBindIngestError):
            ingest_fbx_bind_data("V:/virtual/path_does_not_exist.fbx")


if __name__ == "__main__":
    unittest.main()
