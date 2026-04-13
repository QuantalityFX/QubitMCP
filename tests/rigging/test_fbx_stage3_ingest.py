from __future__ import annotations

import unittest

from echograph.rigging.fbx_canonical import Joint, JointTransform, SkeletonAsset
from echograph.rigging.fbx_stage3_ingest import (
    FBXBindIngestError,
    _fbxsdk_skin_deformer_type,
    compare_skeleton_layout,
    ingest_fbx_bind_data,
)
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time


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


def _row_translation_matrix(tx: float, ty: float, tz: float):
    return (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        float(tx), float(ty), float(tz), 1.0,
    )


def _col_translation_matrix(tx: float, ty: float, tz: float):
    return (
        1.0, 0.0, 0.0, float(tx),
        0.0, 1.0, 0.0, float(ty),
        0.0, 0.0, 1.0, float(tz),
        0.0, 0.0, 0.0, 1.0,
    )


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


def _make_row_layout_scene() -> _Scene:
    hip = _Node("hip", transformation=_row_translation_matrix(0.0, 5.0, 0.0))
    root = _Node(
        "root",
        children=[hip],
        transformation=_row_translation_matrix(10.0, 0.0, 0.0),
    )
    mesh = _Mesh(
        "Body",
        vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        faces=[[0, 1, 2]],
        bones=[
            _Bone(
                "hip",
                weights=[_Weight(0, 1.0), _Weight(1, 1.0), _Weight(2, 1.0)],
                offsetmatrix=_IDENTITY_4X4,
            ),
        ],
    )
    return _Scene(rootnode=root, meshes=[mesh])


def _make_scene_with_non_identity_source_offset() -> _Scene:
    hip = _Node("hip", transformation=_col_translation_matrix(0.0, 5.0, 0.0))
    root = _Node(
        "root",
        children=[hip],
        transformation=_col_translation_matrix(10.0, 0.0, 0.0),
    )
    mesh = _Mesh(
        "Body",
        vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        faces=[[0, 1, 2]],
        bones=[
            _Bone(
                "hip",
                weights=[_Weight(0, 1.0), _Weight(1, 1.0), _Weight(2, 1.0)],
                offsetmatrix=_col_translation_matrix(-2.0, 3.0, 4.0),
            ),
        ],
    )
    return _Scene(rootnode=root, meshes=[mesh])


def _make_col_layout_scene_with_row_offset() -> _Scene:
    root = _Node(
        "root",
        transformation=_col_translation_matrix(3.0, 0.0, 0.0),
    )
    mesh = _Mesh(
        "Body",
        vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        faces=[[0, 1, 2]],
        bones=[
            _Bone(
                "root",
                weights=[_Weight(0, 1.0), _Weight(1, 1.0), _Weight(2, 1.0)],
                offsetmatrix=_row_translation_matrix(-2.0, 3.0, 4.0),
            ),
        ],
    )
    return _Scene(rootnode=root, meshes=[mesh])


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

    def test_row_layout_is_normalized_and_inverse_bind_rebuilt(self) -> None:
        scene = _make_row_layout_scene()
        result = ingest_fbx_bind_data("V:/virtual/row_layout.fbx", scene=scene)
        self.assertTrue(
            any("row-vector fbx matrix layout" in msg.lower() for msg in result.warnings)
        )
        self.assertEqual([j.name for j in result.skeleton.joints], ["root", "hip"])
        self.assertAlmostEqual(result.skeleton.joints[0].local_bind.translation[0], 10.0, places=5)
        self.assertAlmostEqual(result.skeleton.joints[1].local_bind.translation[1], 5.0, places=5)
        evaluation = evaluate_rig_at_time(result.skeleton, None, time_seconds=0.0, loop=False)
        self.assertEqual(len(evaluation.skin_matrices), 2)
        for matrix in evaluation.skin_matrices:
            for got, expected in zip(tuple(matrix), _IDENTITY_4X4):
                self.assertAlmostEqual(got, expected, places=5)
        self.assertEqual(
            result.skeleton.metadata.get("inverse_bind_source"),
            "recomputed_from_local_bind",
        )

    def test_non_identity_source_inverse_bind_is_preserved(self) -> None:
        scene = _make_scene_with_non_identity_source_offset()
        result = ingest_fbx_bind_data("V:/virtual/source_offset.fbx", scene=scene)
        self.assertEqual(
            result.skeleton.metadata.get("inverse_bind_source"),
            "source_offsets",
        )
        self.assertEqual([j.name for j in result.skeleton.joints], ["root", "hip"])
        inv = tuple(result.skeleton.joints[1].inverse_bind_matrix)
        expected = _col_translation_matrix(-2.0, 3.0, 4.0)
        self.assertEqual(inv, expected)

    def test_row_offset_matrix_does_not_force_layout_transpose(self) -> None:
        scene = _make_col_layout_scene_with_row_offset()
        result = ingest_fbx_bind_data("V:/virtual/col_nodes_row_offset.fbx", scene=scene)
        self.assertFalse(
            any("row-vector fbx matrix layout" in msg.lower() for msg in result.warnings)
        )
        self.assertEqual([j.name for j in result.skeleton.joints], ["root"])
        self.assertAlmostEqual(result.skeleton.joints[0].local_bind.translation[0], 3.0, places=5)

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
