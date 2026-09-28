import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6 import QtCore, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.persistence import _node_to_dict
from echograph.rigging.fbx_canonical import (SkeletonAsset, Joint, JointTransform, AnimationClip,
    JointAnimationTrack, Vec3Keyframe, QuatKeyframe, SkeletalMeshAsset, VertexSkin, VertexInfluence)
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time, evaluation_cache_token
from echograph.rigging.delete_joints import filter_joints
from echograph.ui.node_item import NodeItem
from nodes.delete_joint import register, spec
from nodes.anim_retarget import spec as retarget

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def fixture():
    skeleton = SkeletonAsset("rig", [
        Joint("Root", -1, JointTransform(translation=(2, 0, 0))),
        Joint("Hips", 0, JointTransform(translation=(0, 1, 0))),
        Joint("Spine", 1, JointTransform(translation=(0, 1, 0))),
        Joint("Head", 2, JointTransform(translation=(0, 1, 0))),
    ])
    clip = AnimationClip("walk", 0, 1, 20, [JointAnimationTrack("Root",
        translation_keys=[Vec3Keyframe(0, (2, 0, 0)), Vec3Keyframe(1, (4, 0, 0))],
        rotation_keys=[QuatKeyframe(0, (0, 0, 0, 1)), QuatKeyframe(1, (0, 0, 0.70710678, 0.70710678))])])
    return skeleton, clip


def context():
    skeleton, clip = fixture()
    return dict(skeleton=skeleton, clips=[clip], clip=clip, meshes=[], joint_count=4, clip_count=1,
                mesh_count=0, path="test.fbx", source_format="fbx")


class RemovalTests(unittest.TestCase):
    def test_unchanged_filtered_rig_is_reused_and_changes_invalidate_cache(self):
        data = context()
        owner = GraphNode("Delete", kind="delete_joint")
        with patch.object(spec, "filter_joints", wraps=spec.filter_joints) as filter_call:
            first = spec.filtered_context(data, {"Root"}, cache_owner=owner)
            updated = dict(data, transform_xform={"pos": [3, 0, 0]})
            again = spec.filtered_context(updated, {"Root"}, cache_owner=owner)
            self.assertIs(first["skeleton"], again["skeleton"])
            self.assertEqual(again["transform_xform"], updated["transform_xform"])
            self.assertEqual(filter_call.call_count, 1)
            changed = spec.filtered_context(data, {"Head"}, cache_owner=owner)
            self.assertNotIn("Head", changed["skeleton"].joint_names)
            self.assertEqual(filter_call.call_count, 2)
            replaced_input = spec.filtered_context(context(), {"Head"}, cache_owner=owner)
            self.assertIsNot(replaced_input["skeleton"], changed["skeleton"])
            self.assertEqual(filter_call.call_count, 3)

    def test_previously_viewed_rig_does_not_restore_deleted_joint_in_preview(self):
        data = context()
        original = data["skeleton"]
        world = np.array(evaluate_rig_at_time(original, None, 0).global_matrices).reshape(-1, 4, 4)
        for joint, matrix in zip(original.joints, world):
            joint.inverse_bind_matrix = tuple(np.linalg.inv(matrix).ravel())
        # Opening Retarget before deletion populates runtime pose caches on the import.
        cached = retarget._retarget_eval_target_skeleton(original)
        self.assertEqual(cached.joint_names, original.joint_names)
        filtered = spec.filtered_context(data, {"Root"})
        item = SimpleNamespace(model=GraphNode("Retarget", kind="anim_retarget"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rig.fbx"
            path.touch()
            data["path"] = filtered["path"] = str(path)
            result = retarget.RetargetSourceTargetResult("ok", source_context=filtered, target_context=filtered)
            assets = retarget.build_anim_retarget_preview_assets(item, result)
            self.assertEqual(len(assets), 3)
            for asset in assets[:2]:
                self.assertNotIn("Root", asset["fbx_rig_context"]["skeleton"].joint_names)
            for role in ("source_handles", "target_handles"):
                self.assertNotIn("Root", [row["name"] for row in assets[2][role]])
        self.assertEqual(original.joint_names, ["Root", "Hips", "Spine", "Head"])

    def test_root_and_internal_removal_preserves_sampled_world_transforms(self):
        original, clip = fixture()
        saved = original.to_dict()
        for removed in ({"Root"}, {"Hips", "Spine"}, {"Root", "Spine"}, {"Head"}):
            skeleton, clips, _ = filter_joints(original, [clip], [], removed)
            kept = [i for i, joint in enumerate(original.joints) if joint.name not in removed]
            for time in np.linspace(0, 1, 21):
                before = np.array(evaluate_rig_at_time(original, clip, time).global_matrices)[kept]
                after = np.array(evaluate_rig_at_time(skeleton, clips[0], time).global_matrices)
                np.testing.assert_allclose(after, before, atol=1e-6)
            before = np.array(evaluate_rig_at_time(original, None, 0).global_matrices)[kept]
            np.testing.assert_allclose(evaluate_rig_at_time(skeleton, None, 0).global_matrices, before, atol=1e-6)
        self.assertEqual(original.to_dict(), saved)

    def test_removing_all_rejected(self):
        skeleton, clip = fixture()
        with self.assertRaisesRegex(ValueError, "at least one"):
            filter_joints(skeleton, [clip], [], skeleton.joint_names)

    def test_filtered_rigs_get_independent_evaluation_cache_identities(self):
        skeleton, clip = fixture()
        evaluate_rig_at_time(skeleton, clip, 0)
        output, clips, _ = filter_joints(skeleton, [clip], [], {"Root"})
        self.assertNotEqual(evaluation_cache_token(output), evaluation_cache_token(skeleton))
        self.assertNotEqual(evaluation_cache_token(clips[0]), evaluation_cache_token(clip))

    def test_deleted_influences_removed_and_remaining_weights_normalized(self):
        skeleton, _ = fixture()
        mesh = SkeletalMeshAsset("mesh", "rig", 1, vertex_skins=[VertexSkin(0,
            [VertexInfluence(0, 0.2), VertexInfluence(1, 0.3), VertexInfluence(3, 0.5)])])
        filtered, _, meshes = filter_joints(skeleton, [], [mesh], {"Root"})
        influences = meshes[0].vertex_skins[0].influences
        self.assertEqual([inf.joint_index for inf in influences], [0, 2])
        np.testing.assert_allclose([inf.weight for inf in influences], [0.375, 0.625])
        meshes[0].validate(filtered)
        self.assertEqual(mesh.vertex_skins[0].influences[0].weight, 0.2)

    def test_deletion_does_not_assign_fully_removed_weights_to_arbitrary_joint(self):
        skeleton, _ = fixture()
        mesh = SkeletalMeshAsset("mesh", "rig", 1, vertex_skins=[VertexSkin(0, [VertexInfluence(0, 1)])])
        with self.assertRaisesRegex(ValueError, "without skin weights"):
            filter_joints(skeleton, [], [mesh], {"Root"})
        self.assertEqual(mesh.vertex_skins[0].influences, [VertexInfluence(0, 1)])

    def test_mesh_specific_bind_matrices_stay_aligned_through_retarget_scene(self):
        original, clip = fixture()
        bind = np.array(evaluate_rig_at_time(original, None, 0).global_matrices).reshape(-1, 4, 4)
        for joint, matrix in zip(original.joints, bind):
            joint.inverse_bind_matrix = tuple(np.linalg.inv(matrix).ravel())
        mesh_transform = np.eye(4)
        mesh_transform[:3, 3] = (0.3, 0.2, -0.5)
        inverse = np.linalg.inv(bind) @ mesh_transform
        mesh = SkeletalMeshAsset("mesh", "rig", 3, triangle_indices=[0, 1, 2], vertex_skins=[
            VertexSkin(0, [VertexInfluence(1, 1)]), VertexSkin(1, [VertexInfluence(2, 1)]),
            VertexSkin(2, [VertexInfluence(2, 0.4), VertexInfluence(3, 0.6)])], metadata={
                "inverse_bind_matrices": inverse.reshape(-1, 16).tolist(),
                "bind_positions": [[0, 1, 0], [0.2, 2, 0], [0, 3, 0]], "inverse_bind_source": "mesh_clusters"})
        original_mesh = mesh.to_dict()
        data = dict(skeleton=original, clips=[clip], clip=clip, meshes=[mesh], path=__file__, source_format="fbx")
        filtered = spec.filtered_context(data, {"Root"})
        item = SimpleNamespace(model=GraphNode("Retarget", kind="anim_retarget"))
        result = retarget.RetargetSourceTargetResult("ok", source_context=data, target_context=filtered)
        with patch.object(retarget, "build_anim_retarget_clip", return_value=filtered["clip"]):
            asset = retarget.build_anim_retarget_scene_asset(item, result)
        rig = asset["fbx_rig_context"]
        def deform(skeleton, animation, mesh, time):
            globals_ = np.array(evaluate_rig_at_time(skeleton, animation, time).global_matrices).reshape(-1, 4, 4)
            inverses = np.array(mesh.metadata["inverse_bind_matrices"]).reshape(-1, 4, 4)
            # The scene renderer indexes this palette with the vertex joint indices.
            palette = globals_ @ inverses[:len(globals_)]
            points = np.column_stack((mesh.metadata["bind_positions"], np.ones(mesh.vertex_count)))
            return np.array([sum(inf.weight * (palette[inf.joint_index] @ points[skin.vertex_index])
                                for inf in skin.influences) for skin in mesh.vertex_skins])
        for time in (0, 0.5, 1):
            np.testing.assert_allclose(deform(rig["skeleton"], rig["clip"], rig["meshes"][0], time),
                                       deform(original, clip, mesh, time), atol=1e-6)
        np.testing.assert_allclose(np.array(rig["meshes"][0].metadata["inverse_bind_matrices"]).reshape(-1, 4, 4), inverse[1:])
        self.assertEqual(mesh.to_dict(), original_mesh)

    def test_both_retarget_roles_receive_filtered_context(self):
        scene = SimpleNamespace()
        upstream = SimpleNamespace(model=GraphNode("FBX", kind="fbx_import"))
        node = SimpleNamespace(model=GraphNode("Delete", kind="delete_joint", params=[{"name": "removed_joints", "value": '["Root"]'}]), scene=lambda: scene)
        scene._ordered_in_edges = lambda item: [SimpleNamespace(src=upstream, dst_port_name="rig")]
        for resolver in (retarget._context_for_target_item, retarget._context_for_source_item):
            with patch.object(retarget, "_fbx_context_from_item", return_value=context()):
                errors = []
                result = resolver(node, "delete_joint", errors, [])
                self.assertFalse(errors)
                self.assertEqual(result["skeleton"].joint_names, ["Hips", "Spine", "Head"])
                self.assertEqual(result["skeleton"].joints[0].parent_index, -1)

    def test_cycle_rejected(self):
        scene = SimpleNamespace()
        node = SimpleNamespace(model=GraphNode("Delete", kind="delete_joint"), scene=lambda: scene)
        scene._ordered_in_edges = lambda item: [SimpleNamespace(src=node, dst_port_name="rig")]
        errors = []
        self.assertIsNone(spec.resolve_context(node, errors, [], target=True))
        self.assertIn("cycle", errors[0])

    def test_real_bvh_flows_through_delete_to_retarget_target(self):
        from echograph.motion.hands import export_preview_bvh
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "motion.bvh"
            skeleton, clip = fixture()
            export_preview_bvh(path, skeleton, clip)
            edges = {}
            scene = SimpleNamespace(_ordered_in_edges=lambda item: edges.get(id(item), []))
            def item(name, kind, params=None):
                return SimpleNamespace(model=GraphNode(name, kind=kind, params=params or []), scene=lambda: scene)
            source = item("BVH", "mocap_import", [{"name": "path", "value": str(path)}])
            delete = item("Delete", "delete_joint", [{"name": "removed_joints", "value": '["Root"]'}])
            target = item("Retarget", "anim_retarget")
            edges[id(delete)] = [SimpleNamespace(src=source, dst_port_name="rig")]
            edges[id(target)] = [SimpleNamespace(src=source, dst_port_name="source"), SimpleNamespace(src=delete, dst_port_name="target")]
            result = retarget.resolve_anim_retarget_inputs(target, persist=True)
            self.assertFalse(result.errors, result.errors)
            self.assertEqual(result.target_context["skeleton"].joint_names, ["Hips", "Spine", "Head"])
            self.assertEqual(result.source_context["skeleton"].joint_names, ["Root", "Hips", "Spine", "Head"])

    def test_capture_clip_also_uses_filtered_hierarchy(self):
        original = context()
        original["rig_context"] = {"capture_clip": original["clip"]}
        output = spec.filtered_context(original, {"Root"})
        capture = output["rig_context"]["capture_clip"]
        capture.validate(skeleton=output["skeleton"])
        self.assertNotIn("Root", [track.joint_name for track in capture.tracks])


class JointUITests(unittest.TestCase):
    def test_view_result_button_sends_filtered_rig_to_main_viewport(self):
        window = QtWidgets.QMainWindow()
        scene = QtWidgets.QGraphicsScene(window)
        window.setCentralWidget(QtWidgets.QGraphicsView(scene))
        window.open_scene_assets = Mock()
        model = GraphNode("Delete", kind="delete_joint", params=[{"name": "removed_joints", "value": '["Root"]'}])
        item = SimpleNamespace(model=model, scene=lambda: scene)
        data = context()
        controls = spec.JointControls(item)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rig.fbx"
            path.touch()
            data["path"] = str(path)
            for source_format in ("fbx", "bvh"):
                data["source_format"] = source_format
                with patch.object(spec, "input_context", return_value=data):
                    controls.view_button.click()
                args, kwargs = window.open_scene_assets.call_args
                asset = args[0][0]
                self.assertTrue(kwargs["frame"])
                self.assertEqual(asset["node"], "Delete")
                rig = asset["fbx_rig_context"]
                self.assertEqual(rig["skeleton"].joint_names, ["Hips", "Spine", "Head"])
                self.assertFalse(rig["mesh_skinning_enabled"])
                self.assertTrue(rig["show_capture_joints"] if source_format == "fbx" else rig["show_animated_joints"])
                self.assertNotIn("retarget_preview_role", rig)
        controls.close()
        window.close()

    def test_view_result_invalid_input_does_not_replace_main_view(self):
        item = SimpleNamespace(model=GraphNode("Delete", kind="delete_joint"), scene=lambda: None)
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            self.assertFalse(spec.view_result(item))
            warning.assert_called_once()

    def test_fbx_selector_uses_bind_pose_instead_of_flat_local_transforms(self):
        data = context()
        expected = np.array([(0, 0, 0), (0, 1, 0), (0, 2, 0), (0, 3, 0)], dtype=float)
        for transposed in (False, True):
            for joint, position in zip(data["skeleton"].joints, expected):
                joint.local_bind = JointTransform(translation=(1, 0, 0))
                world = np.eye(4)
                world[:3, 3] = position
                inverse = np.linalg.inv(world)
                joint.inverse_bind_matrix = tuple((inverse.T if transposed else inverse).ravel())
            dialog = spec.JointDialog(SimpleNamespace(model=GraphNode("Delete", kind="delete_joint"), scene=lambda: None), data)
            np.testing.assert_allclose(dialog.viewport.points, expected)
            projected = dialog.viewport.projected()
            self.assertGreater(abs(projected[-1].y() - projected[0].y()), 100)
            dialog.close()

    def test_selector_uses_capture_sample_and_bvh_first_frame(self):
        data = context()
        data["rig_context"] = {"capture_clip": data["clip"], "capture_sample_time": 1.0}
        expected = np.array(evaluate_rig_at_time(data["skeleton"], data["clip"], 1).global_matrices).reshape(-1, 4, 4)[:, :3, 3]
        np.testing.assert_allclose(spec.preview_joint_positions(data), expected)
        data["rig_context"] = {}
        data["source_format"] = "bvh"
        data["clip"].start_time = 0.5
        expected = np.array(evaluate_rig_at_time(data["skeleton"], data["clip"], 0.5).global_matrices).reshape(-1, 4, 4)[:, :3, 3]
        np.testing.assert_allclose(spec.preview_joint_positions(data), expected)

    def test_viewport_pick_delete_restore_and_serialization(self):
        model = GraphNode("Delete", kind="delete_joint", params=[])
        item = SimpleNamespace(model=model, scene=lambda: None)
        dialog = spec.JointDialog(item, context())
        dialog.show()
        APP.processEvents()
        QtTest.QTest.mouseClick(dialog.viewport, QtCore.Qt.LeftButton, pos=dialog.viewport.projected()[0].toPoint())
        self.assertEqual(dialog.viewport.selected, {"Root"})
        original_points = dialog.viewport.points.copy()
        camera = (dialog.viewport.yaw, dialog.viewport.pitch, dialog.viewport.zoom, dialog.viewport.extent)
        dialog.delete_selected()
        self.assertEqual(spec.removed_names(model), {"Root"})
        self.assertEqual(dialog.viewport.skeleton.joint_names, ["Hips", "Spine", "Head"])
        self.assertEqual(dialog.viewport.skeleton.joints[0].parent_index, -1)
        np.testing.assert_allclose(dialog.viewport.points, original_points[1:])
        self.assertEqual(camera, (dialog.viewport.yaw, dialog.viewport.pitch, dialog.viewport.zoom, dialog.viewport.extent))
        self.assertTrue(dialog.joints.item(0).font().strikeOut())
        reopened = spec.JointDialog(item, context())
        self.assertNotIn("Root", reopened.viewport.skeleton.joint_names)
        reopened.close()
        saved = _node_to_dict(model)
        self.assertTrue(any(row["name"] == "removed_joints" and json.loads(row["value"]) == ["Root"] for row in saved["params"]))
        dialog.restore_selected()
        self.assertEqual(spec.removed_names(model), set())
        self.assertEqual(dialog.viewport.skeleton.joint_names, ["Root", "Hips", "Spine", "Head"])
        np.testing.assert_allclose(dialog.viewport.points, original_points)
        self.assertFalse(dialog.joints.item(0).font().strikeOut())
        dialog.close()

    def test_node_border_contains_controls_and_has_rig_input(self):
        register()
        model = GraphNode("Delete", kind="delete_joint")
        scene = QtWidgets.QGraphicsScene()
        item = NodeItem(model)
        scene.addItem(item)
        self.assertIn("rig", model._named_inputs)
        self.assertTrue(item._plugin_proxies)
        for proxy in item._plugin_proxies + item._param_proxies:
            self.assertLessEqual(proxy.pos().y() + proxy.size().height() + 5, item.height)
        rig_pin = item._input_port_pos["rig"][0]
        self.assertLess(rig_pin.y(), item._plugin_proxies[0].pos().y())
        self.assertLess(rig_pin.y(), item.height - 5)
        scene.clear()


if __name__ == "__main__":
    unittest.main()
