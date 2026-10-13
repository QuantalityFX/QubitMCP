from __future__ import annotations

import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6 import QtCore, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.persistence import _node_to_dict
from echograph.rigging.animation_blend import AnimationBlendError, build_blended_clip
from echograph.rigging.fbx_canonical import (
    AnimationClip, Joint, JointAnimationTrack, JointTransform, QuatKeyframe,
    SkeletalMeshAsset, SkeletonAsset, Vec3Keyframe, VertexInfluence, VertexSkin,
)
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
from echograph.ui.node_item import NodeItem
from nodes.animation_blend import spec
from nodes.anim_retarget import spec as retarget
from nodes.mocap_collection import spec as collection_spec
from nodes.scene import spec as scene_spec


def fixture():
    skeleton = SkeletonAsset("Rig", [Joint("Root", -1),
                                     Joint("Tip", 0, JointTransform(translation=(0, 1, 0)))])
    evaluation = evaluate_rig_at_time(skeleton, None, 0)
    for joint, matrix in zip(skeleton.joints, evaluation.global_matrices):
        joint.inverse_bind_matrix = tuple(np.linalg.inv(np.array(matrix).reshape(4, 4)).ravel())
    clip = AnimationClip("Action", 5, 7, 30, [
        JointAnimationTrack("Root",
            translation_keys=[Vec3Keyframe(5, (2, 0, 0)), Vec3Keyframe(5.79, (3, 0, 0)), Vec3Keyframe(7, (5, 0, 0))],
            rotation_keys=[QuatKeyframe(5, (0, 0, 0, 1)), QuatKeyframe(7, (0, 1, 0, 0))],
            scale_keys=[Vec3Keyframe(5, (1, 1, 1)), Vec3Keyframe(7, (2, 1, 1))]),
        JointAnimationTrack("Tip", rotation_keys=[QuatKeyframe(5, (0, 0, 0, 1), "step"),
                                                 QuatKeyframe(5.79, (0, 0, 1, 0)), QuatKeyframe(7, (0, 0, 0, 1))]),
    ], metadata={"source_format": "fbx"})
    return skeleton, clip


def matrices(skeleton, clip, time):
    return np.array(evaluate_rig_at_time(skeleton, clip, time).global_matrices).reshape(-1, 4, 4)


class BlendTimingTests(unittest.TestCase):
    def setUp(self):
        self.skeleton, self.animation = fixture()

    def test_bind_hold_blend_and_original_motion_continue_without_a_gap(self):
        before = copy.deepcopy(self.animation.to_dict())
        output = build_blended_clip(self.skeleton, self.skeleton, self.animation,
                                    target_frame=12, blend_frames=24, hold_frames=6, fps=24)
        for time in (0, .1, .25):
            np.testing.assert_allclose(matrices(self.skeleton, output, time), matrices(self.skeleton, None, 0))
        self.assertAlmostEqual(output.duration, 2.75)
        for offset in (0, .037, .28, .4, 1.5):
            np.testing.assert_allclose(matrices(self.skeleton, output, 1.25 + offset),
                                       matrices(self.skeleton, self.animation, 5.5 + offset), atol=1e-7)
        self.assertLess(np.max(np.abs(matrices(self.skeleton, output, 1.25 - 1e-6)
                                     - matrices(self.skeleton, output, 1.25))), 1e-5)
        self.assertEqual(self.animation.to_dict(), before)
        self.assertEqual(output.metadata["source_format"], "fbx")
        # A key between output frames remains at its original fractional offset.
        self.assertTrue(any(abs(key.time - 1.54) < 1e-9 for key in output.tracks[0].translation_keys))

    def test_default_targets_first_frame_instead_of_absolute_time_zero(self):
        output = build_blended_clip(self.skeleton, self.skeleton, self.animation, blend_frames=12, fps=24)
        np.testing.assert_allclose(matrices(self.skeleton, output, 0), matrices(self.skeleton, None, 0))
        np.testing.assert_allclose(matrices(self.skeleton, output, .5), matrices(self.skeleton, self.animation, 5))
        np.testing.assert_allclose(matrices(self.skeleton, output, 2.5), matrices(self.skeleton, self.animation, 7))

    def test_smooth_easing_and_linear_quaternion_shortest_path(self):
        self.animation.tracks[0].rotation_keys = [QuatKeyframe(5, (0, 0, -.70710678, -.70710678))]
        smooth = build_blended_clip(self.skeleton, self.skeleton, self.animation, blend_frames=4, fps=4)
        linear = build_blended_clip(self.skeleton, self.skeleton, self.animation, blend_frames=4, fps=4, curve="linear")
        s = evaluate_rig_at_time(self.skeleton, smooth, .25).local_transforms[0]
        l = evaluate_rig_at_time(self.skeleton, linear, .25).local_transforms[0]
        self.assertAlmostEqual(s.translation[0], 2 * .15625)
        self.assertAlmostEqual(l.translation[0], .5)
        self.assertAlmostEqual(np.linalg.norm(l.rotation), 1)
        expected = np.array([[np.cos(np.pi / 8), -np.sin(np.pi / 8), 0],
                             [np.sin(np.pi / 8), np.cos(np.pi / 8), 0], [0, 0, 1]])
        np.testing.assert_allclose(matrices(self.skeleton, linear, .25)[0, :3, :3], expected, atol=1e-7)

    def test_zero_blend_and_zero_blend_after_hold_have_defined_behavior(self):
        output = build_blended_clip(self.skeleton, self.skeleton, self.animation, blend_frames=0, target_frame=12)
        np.testing.assert_allclose(matrices(self.skeleton, output, 0), matrices(self.skeleton, self.animation, 5.5))
        held = build_blended_clip(self.skeleton, self.skeleton, self.animation,
                                  blend_frames=0, hold_frames=12, target_frame=12)
        np.testing.assert_allclose(matrices(self.skeleton, held, .499), matrices(self.skeleton, None, 0))
        np.testing.assert_allclose(matrices(self.skeleton, held, .5), matrices(self.skeleton, self.animation, 5.5))

    def test_target_beyond_end_and_single_frame_animation_finish_at_target(self):
        output = build_blended_clip(self.skeleton, self.skeleton, self.animation, target_frame=10000)
        self.assertAlmostEqual(output.duration, 30 / 24)
        np.testing.assert_allclose(matrices(self.skeleton, output, output.end_time),
                                   matrices(self.skeleton, self.animation, 7), atol=1e-7)
        one = AnimationClip("Pose", 10, 10, 30, [JointAnimationTrack("Root", translation_keys=[Vec3Keyframe(10, (3, 0, 0))])])
        output = build_blended_clip(self.skeleton, self.skeleton, one)
        self.assertAlmostEqual(output.end_time, 30 / 24)
        self.assertEqual(evaluate_rig_at_time(self.skeleton, output, 100).local_transforms[0].translation, (3, 0, 0))

    def test_namespaces_and_extra_bind_joints_preserve_the_output_hierarchy(self):
        animated = copy.deepcopy(self.skeleton)
        animation = copy.deepcopy(self.animation)
        for joint in animated.joints:
            joint.name = "Motion:" + joint.name
        for track in animation.tracks:
            track.joint_name = "Motion:" + track.joint_name
        self.skeleton.joints.append(Joint("Finger", 1, JointTransform(translation=(.2, 0, 0))))
        output = build_blended_clip(self.skeleton, animated, animation)
        self.assertEqual([track.joint_name for track in output.tracks], ["Root", "Tip", "Finger"])
        self.assertEqual(evaluate_rig_at_time(self.skeleton, output, 2).local_transforms[-1].translation, (.2, 0, 0))

    def test_incompatible_joint_names_and_hierarchies_are_rejected(self):
        changed = copy.deepcopy(self.skeleton)
        changed.joints[1].parent_index = -1
        with self.assertRaisesRegex(AnimationBlendError, "hierarchy.*Retarget"):
            build_blended_clip(self.skeleton, changed, self.animation)
        changed = copy.deepcopy(self.skeleton)
        changed.joints[1].name = "Different"
        clip = copy.deepcopy(self.animation)
        clip.tracks[1].joint_name = "Different"
        with self.assertRaisesRegex(AnimationBlendError, "missing.*Retarget"):
            build_blended_clip(self.skeleton, changed, clip)

    def test_invalid_frame_counts_and_rates_are_rejected(self):
        for config in ({"blend_frames": -1}, {"hold_frames": 1.5}, {"target_frame": float("nan")},
                       {"fps": 0}, {"fps": float("inf")}, {"curve": "unknown"}):
            with self.subTest(config=config), self.assertRaises(AnimationBlendError):
                build_blended_clip(self.skeleton, self.skeleton, self.animation, **config)

    def test_stepped_target_pose_is_reached_on_its_exact_key_frame(self):
        track = self.animation.tracks[0]
        track.translation_keys = [Vec3Keyframe(5, (2, 0, 0), "step"), Vec3Keyframe(6, (3, 0, 0), "step"),
                                  Vec3Keyframe(7, (4, 0, 0))]
        track.rotation_keys = [QuatKeyframe(5, (0, 0, 0, 1), "step"), QuatKeyframe(6, (0, 0, 1, 0), "step"),
                               QuatKeyframe(7, (0, 0, 0, 1))]
        output = build_blended_clip(self.skeleton, self.skeleton, self.animation, target_frame=24, blend_frames=24)
        pose = evaluate_rig_at_time(self.skeleton, output, 1).local_transforms[0]
        self.assertEqual(pose.translation, (3, 0, 0))
        np.testing.assert_allclose(np.abs(pose.rotation), (0, 0, 1, 0), atol=1e-7)


class Graph(QtWidgets.QGraphicsScene):
    linksChanged = QtCore.Signal()
    paramChanged = QtCore.Signal(str)
    _scene_asset_revision = 0
    _edge_index_version = 0

    def __init__(self):
        super().__init__()
        self._node_items = {}
        self.edges = {}

    def _ordered_in_edges(self, item):
        return self.edges.get(item, [])

    _in_edges = _ordered_in_edges

    def set_node_params(self, name, parameters, **kwargs):
        self._node_items[name].model.params = parameters
        self.paramChanged.emit(name)


class BlendNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.graph = Graph()
        self.addCleanup(self.graph.clear)
        self.node = self.add_node("Blend", "animation_blend")
        self.bind = self.add_node("Bind", "fbx_import")
        self.motion = self.add_node("Motion", "fbx_animation")
        self.scene = self.add_node("Scene", "scene")
        self.graph.edges[self.node] = [SimpleNamespace(src=self.bind, dst_port_name="bind_pose"),
                                      SimpleNamespace(src=self.motion, dst_port_name="animation")]
        self.graph.edges[self.scene] = [SimpleNamespace(src=self.node)]
        self.skeleton, self.animation = fixture()
        self.mesh = SkeletalMeshAsset("Body", "Rig", 3, [0, 1, 2],
            [VertexSkin(i, [VertexInfluence(0 if i == 0 else 1, 1)]) for i in range(3)],
            metadata={"bind_positions": [(0, 0, 0), (1, 1, 0), (0, 2, 0)]})
        self.bind_context = dict(skeleton=self.skeleton, clip=None, clips=[], meshes=[self.mesh],
            path="character.fbx", source_format="fbx", rig_context={"skeleton": self.skeleton, "meshes": [self.mesh]},
            preview_asset={"node": "Bind", "path": "character.fbx", "ext": ".fbx"})
        self.motion_asset = {"node": "Motion", "path": "motion.fbx", "ext": ".fbx", "fbx_rig_context": {
            "skeleton": self.skeleton, "clip": self.animation, "meshes": [], "loop": True}}
        self.addCleanup(patch.stopall)
        patch.object(retarget, "_fbx_context_from_item", return_value=self.bind_context).start()
        patch('nodes.fbx_animation_import.spec.build_fbx_animation_scene_asset', return_value=self.motion_asset).start()

    def add_node(self, name, kind, params=None):
        item = NodeItem(GraphNode(name, kind=kind, params=params or []))
        self.graph.addItem(item)
        self.graph._node_items[name] = item
        return item

    def set_value(self, name, value):
        next(p for p in self.node.model.params if p.get("name") == name)["value"] = str(value)

    def output(self):
        errors, warnings = [], []
        context = spec.resolve_context(self.node, errors, warnings)
        self.assertEqual(errors, [])
        self.assertIsNotNone(context)
        return context

    def test_cached_motion_rebuilds_on_parameters_and_animation_replacement(self):
        first = self.output()
        second = self.output()
        self.assertIs(first["clip"], second["clip"])
        self.set_value("blend_frames", 12)
        changed = self.output()
        self.assertIsNot(first["clip"], changed["clip"])
        self.assertEqual(changed["clip"].metadata["blend_frames"], 12)
        self.motion_asset["fbx_rig_context"]["clip"] = copy.deepcopy(self.animation)
        self.assertIsNot(changed["clip"], self.output()["clip"])

    def test_bvh_reference_pose_and_clip_replacement_invalidate_the_blend(self):
        imported = self.add_node("Reference BVH", "mocap_import")
        self.graph.edges[self.node][0].src = imported
        reference = AnimationClip.from_dict(self.animation.to_dict())
        reference.tracks[0].translation_keys[0].value = (3, 90, -5)
        reference.tracks[1].rotation_keys[0].value = (0, 0, -.3826834324, .9238795325)
        context = dict(self.bind_context, source_format="bvh", clip=reference)
        with patch.object(retarget, "_mocap_context_from_item", return_value=context):
            first = self.output()
            np.testing.assert_allclose(matrices(first["skeleton"], first["clip"], 0),
                                       matrices(self.skeleton, reference, reference.start_time), atol=1e-7)
            self.assertIs(first["clip"], self.output()["clip"])
            changed_reference = AnimationClip.from_dict(reference.to_dict())
            changed_reference.tracks[0].translation_keys[0].value = (8, 110, 2)
            context["clip"] = changed_reference
            changed = self.output()
            self.assertIsNot(first["clip"], changed["clip"])
            np.testing.assert_allclose(matrices(changed["skeleton"], changed["clip"], 0),
                                       matrices(self.skeleton, changed_reference, changed_reference.start_time), atol=1e-7)

    def test_override_is_optional_and_the_last_frame_does_not_loop_to_bind(self):
        self.set_value("target_frame", 24)
        self.assertEqual(self.output()["clip"].metadata["target_time"], 5)
        self.set_value("use_target_frame", 1)
        context = self.output()
        self.assertEqual(context["clip"].metadata["target_time"], 6)
        self.assertFalse(context["rig_context"]["loop"])
        evaluated = evaluate_rig_at_time(context["skeleton"], context["clip"], 100, loop=context["rig_context"]["loop"])
        np.testing.assert_allclose(evaluated.global_matrices, evaluate_rig_at_time(self.skeleton, self.animation, 7).global_matrices)

    def test_fbx_inverse_bind_pose_keeps_the_character_undeformed_at_start(self):
        self.skeleton.joints[1].local_bind = JointTransform(translation=(0, 9, 0))
        context = self.output()
        skin = np.array(evaluate_rig_at_time(context["skeleton"], context["clip"], 0).skin_matrices).reshape(-1, 4, 4)
        np.testing.assert_allclose(skin, np.tile(np.eye(4), (2, 1, 1)), atol=1e-7)
        self.assertIs(context["meshes"][0], self.mesh)
        self.assertEqual(self.skeleton.joints[1].local_bind.translation, (0, 9, 0))

    def test_scene_and_outliner_use_blended_mesh_and_saved_placement(self):
        self.scene.model._scene_xforms = {"Blend": {"pos": [3, 2, 1], "rot": [0, 90, 0], "scl": [1, 1, 1]}}
        with patch('nodes.transforms.spec._load_mesh_arrays', return_value=(None, None, None)):
            assets = self.scene._collect_scene_assets()
        self.assertEqual(len(assets), 1)
        asset = assets[0]
        self.assertEqual(asset["node"], "Blend")
        self.assertEqual(asset["path"], "character.fbx")
        self.assertEqual(asset["xform"]["pos"], [3, 2, 1])
        self.assertIs(asset["fbx_rig_context"]["meshes"][0], self.mesh)
        self.assertTrue(asset["fbx_rig_context"]["mesh_skinning_enabled"])
        self.assertFalse(asset["fbx_rig_context"]["preview_bind_geometry_only"])
        card = QtWidgets.QWidget()
        self.addCleanup(card.deleteLater)
        card._node_ref, card._graph_scene = self.scene.model, self.graph
        with patch.object(scene_spec, "_collect_assets", return_value=assets):
            scene_spec.augment_infocard_footer(card, QtWidgets.QVBoxLayout(card))
        outliner = card._scene_outliner_widget
        kinds = [outliner.item(i).data(QtCore.Qt.UserRole + 1) for i in range(outliner.count())]
        self.assertIn("skeleton", kinds)

    def test_missing_input_and_cycle_never_return_a_stale_animation(self):
        self.output()
        self.graph.edges[self.node] = [SimpleNamespace(src=self.bind, dst_port_name="bind_pose")]
        self.assertIsNone(spec.build_animation_blend_scene_asset(self.node))
        self.assertIn("animation input", self.node.model._animation_blend_error)
        self.graph.edges[self.node].append(SimpleNamespace(src=self.node, dst_port_name="animation"))
        self.assertIsNone(spec.build_animation_blend_scene_asset(self.node))
        self.assertIn("cycle", self.node.model._animation_blend_error)

    def test_ports_and_all_controls_stay_inside_the_border_after_rebuild(self):
        for rebuild in range(4):
            if rebuild:
                self.node._recompute_height()
                self.node._build_widgets()
            self.app.processEvents()
            output = self.node.mapFromScene(self.node.port_anchor("animation", side="out"))
            self.assertAlmostEqual(output.x(), self.node.width)
            self.assertEqual(self.node.output_port_hit(output), "animation")
            for proxy in [*self.node._plugin_proxies, *self.node._param_proxies]:
                rect = proxy.mapRectToParent(proxy.boundingRect())
                self.assertLessEqual(rect.right(), self.node.width)
                self.assertLessEqual(rect.bottom() + 9.99, self.node.height)
        self.assertEqual(self.node.input_port_names(), ["bind_pose", "animation"])
        self.assertEqual(self.node.output_port_names(), ["animation"])
        controls = self.node._plugin_proxies[-1].widget()
        controls.refresh()
        self.assertFalse(controls.spins["target_frame"].isEnabled())
        controls.override.setChecked(True)
        controls.spins["blend_frames"].setValue(18)
        QtTest.QTest.qWait(150)
        self.assertTrue(controls.spins["target_frame"].isEnabled())
        self.assertEqual(spec.values(self.node.model)["blend_frames"], "18")
        self.assertEqual(self.output()["clip"].metadata["blend_frames"], 18)

    def test_output_wire_attaches_to_border_and_retarget_animation_pin(self):
        from echograph.ui.graph_items import EdgeItem

        consumer = self.add_node("Retarget", "anim_retarget")
        self.node.setPos(30, 40)
        consumer.setPos(700, 90)
        edge = EdgeItem(self.node, consumer, src_port_name="animation", dst_port_name="animation")
        self.graph.addItem(edge)
        for width in (self.node.width, 220, 500):
            self.node.prepareGeometryChange()
            self.node.width = width
            self.node._build_widgets()
            edge.updatePath()
            output = self.node.mapToScene(QtCore.QPointF(self.node.width, self.node._output_port_pos["animation"][0].y()))
            target = consumer.port_anchor("animation", side="in")
            start = edge.path().elementAt(0)
            self.assertAlmostEqual(start.x, output.x())
            self.assertAlmostEqual(start.y, output.y())
            self.assertEqual(edge.path().pointAtPercent(1), target)
            self.assertEqual(self.node.output_port_hit(self.node.mapFromScene(output)), "animation")
            self.assertEqual(consumer.input_port_hit(consumer.mapFromScene(target)), "animation")

    def test_blend_output_supplies_retarget_animation_override(self):
        import json

        reference = AnimationClip("Reference", 0, 1, 24, [
            JointAnimationTrack("Root", translation_keys=[Vec3Keyframe(0, (0, 0, 0))],
                                rotation_keys=[QuatKeyframe(0, (0, 0, 0, 1))])])
        self.bind_context.update(clip=reference, clips=[reference])
        target = self.add_node("Character", "fbx_import")
        target_skeleton = copy.deepcopy(self.skeleton)
        target_skeleton.joints[0].name = "Hips"
        target_skeleton.joints[1].name = "Chest"
        target_skeleton.joints[1].local_bind.translation = (0, 2, 0)
        for joint, matrix in zip(target_skeleton.joints, matrices(target_skeleton, None, 0)):
            joint.inverse_bind_matrix = tuple(np.linalg.inv(matrix).ravel())
        target_context = dict(self.bind_context, skeleton=target_skeleton, clip=None, clips=[], meshes=[],
                              rig_context={"skeleton": target_skeleton, "meshes": []})
        consumer = self.add_node("Retarget", "anim_retarget", params=[
            {"name": "joint_map", "value": json.dumps({"Root": "Hips", "Tip": "Chest"})},
            {"name": "scale_mode", "value": "none"}])
        self.graph.edges[consumer] = [SimpleNamespace(src=self.bind, dst_port_name="source"),
                                      SimpleNamespace(src=target, dst_port_name="target"),
                                      SimpleNamespace(src=self.node, src_port_name="animation", dst_port_name="animation")]
        for name, value in (("hold_frames", 12), ("blend_frames", 24), ("use_target_frame", 1), ("target_frame", 12)):
            self.set_value(name, value)
        with patch.object(retarget, "_fbx_context_from_item", side_effect=lambda item, *args, **kwargs:
                          target_context if item is target else self.bind_context):
            blended = self.output()
            result = retarget.resolve_anim_retarget_inputs(consumer, persist=True)
            self.assertEqual(result.errors, [])
            self.assertTrue(result.animation_connected)
            self.assertIs(result.animation_context["clip"], blended["clip"])
            output = retarget.build_anim_retarget_clip(consumer, result)
            self.assertIsNotNone(output)
            self.assertAlmostEqual(output.start_time, 0)
            self.assertAlmostEqual(output.end_time, 3)
            np.testing.assert_allclose(matrices(target_skeleton, output, 0), matrices(target_skeleton, None, 0), atol=1e-6)
            for time in (0, 0.25, 0.5, 1, 1.5, 2.25, 3):
                expected = matrices(blended["skeleton"], blended["clip"], time)[0, :3, 3]
                np.testing.assert_allclose(matrices(target_skeleton, output, time)[0, :3, 3], expected, atol=1e-6)
            self.set_value("blend_frames", 48)
            changed = retarget.build_anim_retarget_clip(consumer)
            self.assertIsNot(changed, output)
            self.assertAlmostEqual(changed.end_time, 4)

    def test_workflow_saves_settings_without_serializing_runtime_clips(self):
        self.set_value("hold_frames", 24)
        self.output()
        data = _node_to_dict(self.node.model)
        params = {p["name"]: p["value"] for p in data["params"]}
        self.assertEqual(params["hold_frames"], "24")
        self.assertNotIn("_animation_blend_clip_cache", data)
        restored = self.add_node("Restored", "animation_blend", params=copy.deepcopy(data["params"]))
        self.graph.edges[restored] = self.graph.edges[self.node]
        errors, warnings = [], []
        output = spec.resolve_context(restored, errors, warnings)
        self.assertEqual(errors, [])
        self.assertEqual(output["clip"].metadata["hold_frames"], 24)

    def test_transforms_and_downstream_rig_nodes_resolve_the_blended_clip(self):
        transformed = self.add_node("Placement", "transforms", params=[{"name": "pos", "value": "3,0,0"}])
        self.graph.edges[transformed] = [SimpleNamespace(src=self.node, dst_port_name="mesh")]
        errors, warnings = [], []
        output = retarget._context_for_source_item(transformed, "transforms", errors, warnings)
        self.assertEqual(errors, [])
        self.assertTrue(output["clip"].metadata["animation_blend"])
        np.testing.assert_allclose(output["transform_xform"]["pos"], [3, 0, 0])
        assets = transformed._collect_scene_assets()
        self.assertEqual(assets[0]["node"], "Placement")
        self.assertIs(assets[0]["fbx_rig_context"]["clip"], output["clip"])
        np.testing.assert_allclose(assets[0]["xform"]["pos"], [3, 0, 0])
        self.graph.edges[self.scene] = [SimpleNamespace(src=transformed)]
        with patch('nodes.transforms.spec._load_mesh_arrays', return_value=(None, None, None)):
            assets = scene_spec._collect_assets(self.scene)
        self.assertEqual(assets[0]["node"], "Placement")
        self.assertIs(assets[0]["fbx_rig_context"]["clip"], output["clip"])

    def test_anim_retarget_output_can_supply_the_animation_input(self):
        import json
        produced = self.add_node("Retarget", "anim_retarget", params=[
            {"name": "joint_map", "value": json.dumps({"Root": "Root", "Tip": "Tip"})}])
        self.graph.edges[produced] = [SimpleNamespace(src=self.motion, dst_port_name="source"),
                                      SimpleNamespace(src=self.bind, dst_port_name="target")]
        expected = retarget.build_anim_retarget_scene_asset(produced)
        self.assertIsNotNone(expected)
        self.graph.edges[self.node][1].src = produced
        context = self.output()
        original = expected["fbx_rig_context"]
        np.testing.assert_allclose(matrices(context["skeleton"], context["clip"], 1.25),
                                   matrices(original["skeleton"], original["clip"], original["clip"].start_time), atol=1e-6)

    def test_blended_character_is_a_deforming_cloth_collider(self):
        from echograph.ui.gl_mgl_renderer import MGLRendererMixin
        from echograph.physics.cloth import ClothSettings
        from echograph.patterns.commands import rectangle
        from echograph.patterns.document import PatternDocument
        from echograph.patterns.serialization import save_document
        from nodes.cloth_solver.spec import build_cloth_scene_asset

        self.mesh.vertex_count = 4
        self.mesh.triangle_indices = [0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3]
        self.mesh.vertex_skins = [VertexSkin(i, [VertexInfluence(0 if i == 0 else 1, 1)]) for i in range(4)]
        self.mesh.metadata["bind_positions"] = [(0, 0, 0), (.5, 1, 0), (0, 1, 1), (0, 2, 0)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shirt.pattern.json"
            save_document(path, PatternDocument(pieces=[rectangle("Front", 50, 50)]))
            pattern = self.add_node("Pattern", "pattern_design", [{"name": "pattern_path", "value": str(path)}])
            cloth = self.add_node("Cloth", "cloth_solver")
            self.graph.edges[cloth] = [SimpleNamespace(src=pattern, dst_port_name="pattern"),
                                      SimpleNamespace(src=self.node, dst_port_name="collider")]
            asset = build_cloth_scene_asset(cloth)
            self.assertIsNotNone(asset, getattr(cloth.model, "_cloth_build_error", ""))
            self.assertEqual([c["node"] for c in asset["cloth"]["colliders"]], ["Blend"])
            rig = asset["cloth"]["colliders"][0]["fbx_rig_context"]
            renderer = object.__new__(MGLRendererMixin)
            current = {"time": 0}
            renderer._mgl_timeline_frame_index = lambda: round(current["time"] * 24)
            renderer._mgl_fbx_context_timeline_sample_seconds = lambda context, owner: current["time"]
            renderer._cloth_matrix = lambda owner: np.eye(4)
            state = SimpleNamespace(solver=SimpleNamespace(settings=ClothSettings()), collider_previous={})
            body = SimpleNamespace(payload={"owner": "Blend", "fbx_rig_context": rig})
            points = np.asarray(self.mesh.metadata["bind_positions"])
            previous = None
            for time in (0, .625, 1.25, 2.25, rig["clip"].end_time):
                current["time"] = time
                collider = renderer._cloth_colliders(asset["cloth"], state, [body])[0]
                skin = np.asarray(evaluate_rig_at_time(rig["skeleton"], rig["clip"], time).skin_matrices).reshape(-1, 4, 4)
                expected = np.array([(skin[0 if index == 0 else 1] @ np.append(point, 1))[:3]
                                     for index, point in enumerate(points)])
                expanded = expected[np.asarray(self.mesh.triangle_indices)]
                np.testing.assert_allclose(state.collider_previous["Blend"], expanded, atol=1e-6)
                self.assertEqual(collider.triangles.shape, (4, 3))
                if previous is not None:
                    self.assertFalse(np.allclose(expected, previous))
                previous = expected
    def test_real_bvh_import_uses_imported_pose_and_continues_the_selected_take(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "action.bvh"
            path.write_text("""HIERARCHY
ROOT Root
{
 OFFSET 0 0 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT Tip
 {
  OFFSET 0 1 0
  CHANNELS 3 Zrotation Xrotation Yrotation
 }
}
MOTION
Frames: 3
Frame Time: 0.5
2 0 0 0 0 0 0 0 0
3 0 0 90 0 0 0 0 0
4 0 0 180 0 0 0 0 0
""")
            imported = self.add_node("BVH", "mocap_import", [{"name": "path", "value": str(path)}])
            self.graph.edges[self.node] = [SimpleNamespace(src=imported, dst_port_name=port)
                                          for port in ("bind_pose", "animation")]
            self.set_value("blend_frames", 24)
            asset = spec.build_animation_blend_scene_asset(self.node)
            self.assertIsNotNone(asset, self.node.model._animation_blend_error)
            rig = asset["fbx_rig_context"]
            self.assertTrue(asset["wire_only"])
            self.assertEqual(asset["ext"], ".bvh")
            start = evaluate_rig_at_time(rig["skeleton"], rig["clip"], 0)
            target = evaluate_rig_at_time(rig["skeleton"], rig["clip"], 1)
            end = evaluate_rig_at_time(rig["skeleton"], rig["clip"], 2)
            self.assertEqual(start.local_transforms[0].translation, (2, 0, 0))
            self.assertEqual(target.local_transforms[0].translation, (2, 0, 0))
            self.assertEqual(end.local_transforms[0].translation, (4, 0, 0))
            # Both branches may share a Transform; it is not an input cycle.
            placement = self.add_node("BVH Placement", "transforms", [{"name": "pos", "value": "3,0,0"}])
            self.graph.edges[placement] = [SimpleNamespace(src=imported, dst_port_name="mesh")]
            self.graph.edges[self.node] = [SimpleNamespace(src=placement, dst_port_name=port)
                                          for port in ("bind_pose", "animation")]
            asset = spec.build_animation_blend_scene_asset(self.node)
            self.assertIsNotNone(asset, self.node.model._animation_blend_error)
            np.testing.assert_allclose(asset["xform"]["pos"], [3, 0, 0])

    def test_bvh_a_pose_keeps_root_height_and_collection_animation_placement(self):
        hierarchy = """HIERARCHY
ROOT Root
{
 OFFSET 0 0 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT Shoulder
 {
  OFFSET 10 25 0
  CHANNELS 3 Zrotation Xrotation Yrotation
  JOINT Tip
  {
   OFFSET 35 0 0
   CHANNELS 3 Zrotation Xrotation Yrotation
  }
 }
}
MOTION
Frames: {frames}
Frame Time: 0.5
{motion}
"""
        with tempfile.TemporaryDirectory() as directory:
            reference_path = Path(directory) / "a_pose.bvh"
            motion_path = Path(directory) / "action.bvh"
            reference_path.write_text(hierarchy.replace("{frames}", "2").replace("{motion}", (
                "4 100 -3 15 0 0 -45 0 0 0 0 0\n"
                "8 110 2 0 0 0 0 0 0 0 0 0")), encoding="utf-8")
            motion_path.write_text(hierarchy.replace("{frames}", "3").replace("{motion}", (
                "20 90 6 0 0 0 25 0 0 0 0 0\n"
                "25 92 8 30 0 0 -10 0 0 0 0 0\n"
                "30 94 10 60 0 0 -35 0 0 0 0 0")), encoding="utf-8")
            reference = self.add_node("A Pose", "mocap_import", [{"name": "path", "value": str(reference_path)}])
            motion = self.add_node("BVH Collection", "mocap_collection")
            collection_spec.add_paths(motion.model, [motion_path], self.graph)
            self.graph.edges[self.node] = [SimpleNamespace(src=reference, dst_port_name="bind_pose"),
                                          SimpleNamespace(src=motion, dst_port_name="animation")]
            self.set_value("hold_frames", 12)
            self.set_value("blend_frames", 24)
            self.set_value("use_target_frame", 1)
            self.set_value("target_frame", 6)
            output = self.output()
            first = reference.model._mocap_animation_result
            second = motion.model._mocap_animation_result
            first_clip, second_clip = first.clips[0], second.clips[0]
            expected_start = matrices(first.skeleton, first_clip, first_clip.start_time)
            self.assertFalse(np.allclose(expected_start, matrices(first.skeleton, None, 0)))
            start = evaluate_rig_at_time(output["skeleton"], output["clip"], 0)
            self.assertEqual(start.local_transforms[0].translation, (4, 100, -3))
            for time in (0, .25, .5):
                np.testing.assert_allclose(matrices(output["skeleton"], output["clip"], time),
                                           expected_start, atol=1e-7)
            self.assertEqual(output["clip"].metadata["target_time"], .25)
            self.assertEqual(output["clip"].end_time, 2.25)
            for output_time, source_time in ((1.5, .25), (1.75, .5), (2, .75), (2.25, 1)):
                np.testing.assert_allclose(matrices(output["skeleton"], output["clip"], output_time),
                                           matrices(second.skeleton, second_clip, source_time), atol=1e-7)


if __name__ == "__main__":
    unittest.main()
