from types import SimpleNamespace
import math
import unittest
from unittest.mock import patch

from PySide6 import QtWidgets
from nodes.anim_retarget import spec
from echograph.rigging.fbx_canonical import (
    AnimationClip, Joint, JointAnimationTrack, JointTransform, QuatKeyframe, SkeletonAsset, Vec3Keyframe,
)


class AnimationInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.model = SimpleNamespace(name="Retarget", kind="anim_retarget", params=[])
        self.item = SimpleNamespace(model=self.model, scene=lambda: None)
        self.skeleton = SkeletonAsset("source", [Joint("root", -1), Joint("tip", 0)])
        self.reference = self.clip("reference", (0, 0, 0, 1))
        self.motion = self.clip("motion", (0, math.sqrt(0.5), 0, math.sqrt(0.5)))
        self.source = {"skeleton": self.skeleton, "clip": self.reference}
        self.animation = {"skeleton": self.skeleton, "clip": self.motion}
        self.target = {"skeleton": SkeletonAsset("target", [Joint("root", -1), Joint("tip", 0)])}
        spec.set_joint_map_link(self.item, "root", "root", notify_scene=False)

    @staticmethod
    def clip(name, rotation):
        return AnimationClip(name, 0, 1, 30, [JointAnimationTrack("root",
            rotation_keys=[QuatKeyframe(0, rotation), QuatKeyframe(1, rotation)])])

    def resolve(self, connected=True):
        source = SimpleNamespace(model=SimpleNamespace(name="Source", kind="mocap_import"))
        target = SimpleNamespace(model=SimpleNamespace(name="Target", kind="fbx_import"))
        animation = SimpleNamespace(model=SimpleNamespace(name="Animation", kind="mocap_import"))
        ports = {"source": source, "target": target, "animation": animation if connected else None}
        with patch.object(spec, "_connected_item_for_port", side_effect=lambda s, n, p: (ports[p], "")), \
             patch.object(spec, "_context_for_source_item", side_effect=lambda i, *a: self.source if i is source else self.animation), \
             patch.object(spec, "_context_for_target_item", return_value=self.target):
            return spec.resolve_anim_retarget_inputs(self.item, persist=True)

    def test_override_preserves_first_pose_and_source_reference(self):
        result = self.resolve()
        output = spec.build_anim_retarget_clip(self.item, result)
        self.assertEqual(output.name, "motion_retarget")
        rotation = output.tracks[0].rotation_keys[0].value
        self.assertAlmostEqual(abs(rotation[1]), math.sqrt(0.5))
        self.assertIs(result.source_context, self.source)
        self.assertIs(result.source_context["clip"], self.reference)

    def test_disconnect_restores_source_and_invalidates_cached_motion(self):
        spec.build_anim_retarget_clip(self.item, self.resolve())
        output = spec.build_anim_retarget_clip(self.item, self.resolve(False))
        self.assertEqual(output.name, "reference_retarget")
        self.assertEqual(output.tracks[0].rotation_keys[0].value, (0, 0, 0, 1))
        self.assertEqual(self.item._retarget_animation_error, "")

    def test_pelvis_modes_follow_motion_with_different_position_policies(self):
        self.motion.tracks[0].translation_keys = [Vec3Keyframe(0, (2, 3, 4))]
        spec.set_pelvis_constraint_link(self.item, "root", "root", mode="snap", notify_scene=False)
        output = spec.build_anim_retarget_clip(self.item, self.resolve())
        track = next(t for t in output.tracks if t.joint_name == "root")
        self.assertEqual(track.translation_keys[0].value, (2, 3, 4))
        self.assertAlmostEqual(abs(spec._quat_dot(track.rotation_keys[0].value,
                                                self.motion.tracks[0].rotation_keys[0].value)), 1)
        # Position Offset changes translation only; rotation must still match.
        spec.set_pelvis_constraint_link(self.item, "root", "root", mode="position_offset", notify_scene=False)
        output = spec.build_anim_retarget_clip(self.item, self.resolve())
        self.assertEqual(output.tracks[0].translation_keys[0].value, (0, 0, 0))
        self.assertAlmostEqual(abs(spec._quat_dot(output.tracks[0].rotation_keys[0].value,
                                                self.motion.tracks[0].rotation_keys[0].value)), 1)
        spec._set_param_value(self.model, spec.PELVIS_CONSTRAINT_MODE_PARAM, "none")
        output = spec.build_anim_retarget_clip(self.item, self.resolve())
        self.assertAlmostEqual(abs(spec._quat_dot(output.tracks[0].rotation_keys[0].value,
                                                self.motion.tracks[0].rotation_keys[0].value)), 1)

    def test_snap_without_mapping_or_local_track_inherits_source_parent_rotation(self):
        spec.set_pelvis_constraint_link(self.item, "tip", "tip", mode="snap", notify_scene=False)
        output = spec.build_anim_retarget_clip(self.item, self.resolve())
        rotations = spec._global_sample_rotations(self.target["skeleton"], spec._track_lookup(output), 0)
        self.assertAlmostEqual(abs(spec._quat_dot(rotations[1], self.motion.tracks[0].rotation_keys[0].value)), 1)

    def test_constraint_world_rotation_accounts_for_rig_bases_and_animated_parents(self):
        qx = (math.sqrt(0.5), 0, 0, math.sqrt(0.5))
        qz = (0, 0, math.sqrt(0.5), math.sqrt(0.5))
        self.skeleton.joints[1].local_bind.rotation = qx
        self.target["skeleton"].joints[0].local_bind.rotation = qz
        self.motion.tracks[0].rotation_keys.insert(1, QuatKeyframe(0.5, qx))
        reference_source = spec._global_sample_rotations(self.skeleton, spec._track_lookup(self.motion), 0)
        target_bind = spec._global_bind_rotations(self.target["skeleton"])
        for mode in ("snap", "position_offset"):
            with self.subTest(mode=mode):
                keys = spec._build_global_rotation_key_sets(
                    self.skeleton, self.target["skeleton"], self.motion, {"root": "root"},
                    spec._track_lookup(self.motion), spec._joint_lookup(self.skeleton),
                    spec._joint_lookup(self.target["skeleton"]), qx, qz,
                    pelvis_constraint={"source": "tip", "target": "tip", "mode": mode},
                )
                tracks = {name: JointAnimationTrack(name, rotation_keys=rows) for name, rows in keys.items()}
                for time in (0, 0.5, 1):
                    source = spec._global_sample_rotations(self.skeleton, spec._track_lookup(self.motion), time)
                    target = spec._global_sample_rotations(self.target["skeleton"], tracks, time)
                    source_delta = spec._quat_mul(source[1], spec._quat_inverse(reference_source[1]))
                    source_world_delta = spec._quat_mul(qx, spec._quat_mul(source_delta, spec._quat_inverse(qx)))
                    target_world = spec._quat_mul(qz, target[1])
                    target_world_reference = spec._quat_mul(qz, target_bind[1])
                    target_world_delta = spec._quat_mul(target_world, spec._quat_inverse(target_world_reference))
                    self.assertAlmostEqual(abs(spec._quat_dot(source_world_delta, target_world_delta)), 1)

    def test_mismatch_blocks_override_and_clears_on_reconnect(self):
        for joints in ([Joint("root", -1), Joint("other", 0)],
                       [Joint("root", -1), Joint("tip", -1)]):
            self.animation["skeleton"] = SkeletonAsset("different", joints)
            result = self.resolve()
            self.assertIn("rig differs", result.animation_error)
            self.assertTrue(self.item._retarget_animation_error)
            self.assertIsNone(spec.build_anim_retarget_clip(self.item, result))
        self.animation["skeleton"] = self.skeleton
        self.assertEqual(self.resolve().animation_error, "")

    def test_position_offset_matches_rotation_and_preserves_position_offset(self):
        qx = (math.sqrt(0.5), 0, 0, math.sqrt(0.5))
        qz = (0, 0, math.sqrt(0.5), math.sqrt(0.5))
        self.target["skeleton"].joints[1].local_bind.rotation = qz
        self.target["skeleton"].joints[1].local_bind.translation = (0, 8, 0)
        spec._set_param_value(self.model, "scale_mode", "none")
        self.reference.tracks.append(JointAnimationTrack("tip", rotation_keys=[QuatKeyframe(0, qx)]))
        self.motion.tracks = [JointAnimationTrack("tip",
            rotation_keys=[QuatKeyframe(0, qx), QuatKeyframe(1, spec._quat_mul(qz, qx))],
            translation_keys=[Vec3Keyframe(0, (2, 3, 4)), Vec3Keyframe(1, (3, 5, 4))])]
        spec.set_pelvis_constraint_link(self.item, "tip", "tip", mode="position_offset", notify_scene=False)
        for conflicting_mapping in (False, True):
            with self.subTest(conflicting_mapping=conflicting_mapping):
                if conflicting_mapping:
                    spec.set_joint_map_link(self.item, "root", "tip", notify_scene=False)
                output = spec.build_anim_retarget_clip(self.item, self.resolve())
                track = next(t for t in output.tracks if t.joint_name == "tip")
                self.assertTrue(track.rotation_keys)
                self.assertEqual(track.translation_keys[0].value, (0, 8, 0))
                self.assertEqual(track.translation_keys[-1].value, (1, 10, 0))
                for time, expected in ((0, qz), (1, spec._quat_mul(qz, qz))):
                    rotations = spec._global_sample_rotations(self.target["skeleton"], spec._track_lookup(output), time)
                    self.assertAlmostEqual(abs(spec._quat_dot(rotations[1], expected)), 1)

    def test_position_offset_constraint_alone_follows_inherited_rotation(self):
        spec.remove_joint_map_link(self.item, "root", "root", notify_scene=False)
        spec.set_pelvis_constraint_link(self.item, "tip", "tip", mode="position_offset", notify_scene=False)
        output = spec.build_anim_retarget_clip(self.item, self.resolve())
        self.assertIsNotNone(output)
        rotations = spec._global_sample_rotations(self.target["skeleton"], spec._track_lookup(output), 0)
        self.assertAlmostEqual(abs(spec._quat_dot(rotations[1], self.motion.tracks[0].rotation_keys[0].value)), 1)

    def test_pelvis_body_axes_match_across_different_joint_axes_and_root_deletion(self):
        import numpy as np
        from scipy.spatial.transform import Rotation
        from echograph.rigging.delete_joints import filter_joints
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time

        parent = Rotation.from_euler('x', 90, degrees=True)
        pelvis_axes = Rotation.from_euler('z', 90, degrees=True)
        offsets = {'left_hip': (1, 0, 0), 'right_hip': (-1, 0, 0), 'spine': (0, 1, 0)}
        source = SkeletonAsset('source', [Joint('pelvis', -1)] + [
            Joint(name, 0, JointTransform(translation=offset)) for name, offset in offsets.items()])
        target = SkeletonAsset('target', [
            Joint('root', -1, JointTransform(rotation=tuple(parent.as_quat()))),
            Joint('pelvis', 0, JointTransform(rotation=tuple((parent.inv() * pelvis_axes).as_quat()))),
        ] + [Joint(name, 1, JointTransform(translation=tuple(pelvis_axes.inv().apply(offset))))
             for name, offset in offsets.items()])
        reference = AnimationClip('reference', 0, 1, 30, [])
        motion = AnimationClip('turn', 0, 1, 30, [JointAnimationTrack('pelvis', rotation_keys=[
            QuatKeyframe(t, tuple(Rotation.from_euler('y', angle, degrees=True).as_quat()))
            for t, angle in ((0, 0), (0.5, 45), (1, 90))])])
        for delete_root in (False, True):
            rig = filter_joints(target, [], [], {'root'})[0] if delete_root else target
            for mode in ('snap', 'position_offset'):
                with self.subTest(delete_root=delete_root, mode=mode):
                    model = SimpleNamespace(params=[])
                    item = SimpleNamespace(model=model, scene=lambda: None)
                    spec.set_pelvis_constraint_link(item, 'pelvis', 'pelvis', mode=mode, notify_scene=False)
                    result = spec.RetargetSourceTargetResult('ok',
                        source_context={'skeleton': source, 'clip': reference},
                        target_context={'skeleton': rig},
                        animation_context={'skeleton': source, 'clip': motion}, animation_connected=True)
                    output = spec.build_anim_retarget_clip(item, result)
                    for time in (0, 0.25, 0.5, 0.75, 1):
                        source_world = np.array(evaluate_rig_at_time(source, motion, time).global_matrices).reshape(-1, 4, 4)
                        target_world = np.array(evaluate_rig_at_time(rig, output, time).global_matrices).reshape(-1, 4, 4)
                        # Joint quaternions differ, but the actual hip and spine directions must agree.
                        for name in offsets:
                            si, ti = spec._joint_lookup(source), spec._joint_lookup(rig)
                            np.testing.assert_allclose(
                                target_world[ti[name], :3, 3] - target_world[ti['pelvis'], :3, 3],
                                source_world[si[name], :3, 3] - source_world[si['pelvis'], :3, 3], atol=1e-6)

    def test_joint_order_does_not_change_structure(self):
        reordered = SkeletonAsset("reordered", [Joint("tip", 1), Joint("root", -1)])
        self.assertEqual(spec._rig_structure(self.skeleton), spec._rig_structure(reordered))

    def test_switch_routes_selected_bvh_and_detects_mismatch(self):
        edges = {}
        def ordered(item):
            incoming = edges.get(id(item), [])
            if item.model.kind == "switch":
                return incoming[item.model.switch_index:item.model.switch_index + 1]
            return incoming
        scene = SimpleNamespace(_ordered_in_edges=ordered)
        def item(name, kind):
            return SimpleNamespace(model=SimpleNamespace(name=name, kind=kind, switch_index=0), scene=lambda: scene)
        source, target = item("Source", "mocap_import"), item("Target", "fbx_import")
        motion, bad = item("Motion", "mocap_import"), item("Bad", "mocap_import")
        switch, nested = item("Switch", "switch"), item("Nested", "switch")
        def connect(dst, *inputs):
            edges[id(dst)] = [SimpleNamespace(src=src, dst_port_name=port) for src, port in inputs]
        self.item.scene = lambda: scene
        connect(self.item, (source, "source"), (target, "target"), (nested, "animation"))
        connect(nested, (switch, ""))
        connect(switch, (motion, ""), (bad, ""))
        bad_context = {"skeleton": SkeletonAsset("bad", [Joint("other", -1)]), "clip": self.motion}
        contexts = {id(source): self.source, id(motion): self.animation, id(bad): bad_context}
        with patch.object(spec, "_mocap_context_from_item", side_effect=lambda i, *a: contexts[id(i)]), \
             patch.object(spec, "_fbx_context_from_item", return_value=self.target):
            resolve = lambda: spec.resolve_anim_retarget_inputs(self.item, persist=True)
            self.assertIs(spec._animation_clip(resolve()), self.motion)
            switch.model.switch_index = 1
            self.assertIn("rig differs", resolve().animation_error)
            switch.model.switch_index = 0
            self.assertEqual(resolve().animation_error, "")
            # Switches also route the reference source and FBX target.
            connect(nested, (source, ""))
            connect(switch, (target, ""))
            connect(self.item, (nested, "source"), (switch, "target"))
            result = resolve()
            self.assertEqual(result.errors, [])
            self.assertIs(result.source_context, self.source)
            self.assertIs(result.target_context, self.target)
            connect(nested, (nested, ""))
            self.assertTrue(any("cycle" in e for e in resolve().errors))
            connect(nested)
            self.assertTrue(any("no connected input" in e for e in resolve().errors))

    def test_connected_empty_clip_does_not_fall_back(self):
        self.animation["clip"] = None
        result = self.resolve()
        self.assertTrue(result.animation_error)
        self.assertIsNone(spec.build_anim_retarget_clip(self.item, result))

    def test_unrelated_changes_do_not_schedule_retarget_validation(self):
        source = SimpleNamespace(model=SimpleNamespace(name="Source"))
        scene = SimpleNamespace(_ordered_in_edges=lambda item: [SimpleNamespace(src=source)] if item is self.item else [])
        self.item.scene = lambda: scene
        button = spec.AnimRetargetNodeViewButton(self.item)
        button._validation_timer.stop()
        button._schedule_validation("Unrelated Collection", [])
        self.assertFalse(button._validation_timer.isActive())
        button._schedule_validation("Source", [])
        self.assertTrue(button._validation_timer.isActive())
        button._validation_timer.stop()
        button._schedule_validation()  # Wiring changes still require validation.
        self.assertTrue(button._validation_timer.isActive())
        button._validation_timer.stop()
        button.deleteLater()
        self.item.scene = lambda: None

    def preview_source_rig(self, connected=True):
        self.source.update(path=__file__, source_format="bvh",
                           rig_context={"clips": [self.reference]})
        self.target["path"] = __file__
        assets = spec.build_anim_retarget_preview_assets(self.item, self.resolve(connected))
        return next(asset["fbx_rig_context"] for asset in assets if asset.get("node") == "Retarget Source")

    def test_source_preview_uses_override_and_toggle_restores_reference(self):
        # A saved legacy source-reference setting must not prevent playback.
        spec._set_param_value(self.model, spec.SOURCE_REST_POSE_PARAM, "1")
        spec._set_param_value(self.model, spec.PREVIEW_TARGET_MESH_PARAM, "1")
        rig = self.preview_source_rig()
        self.assertIs(rig["skeleton"], self.skeleton)
        self.assertIs(rig["clip"], self.motion)
        self.assertEqual(rig["clips"], [self.motion])
        self.assertFalse(rig.get("retarget_static_pose", False))
        spec._set_param_value(self.model, spec.PREVIEW_TARGET_MESH_PARAM, "0")
        rig = self.preview_source_rig()
        self.assertIsNone(rig["clip"])
        self.assertEqual(rig["clips"], [])
        self.assertTrue(rig["retarget_static_pose"])
        self.assertEqual(rig["skeleton"].joints[0].local_bind.rotation, (0, 0, 0, 1))
        self.assertIs(self.source["clip"], self.reference)

    def test_source_preview_falls_back_without_animation_connection(self):
        spec._set_param_value(self.model, spec.PREVIEW_TARGET_MESH_PARAM, "1")
        self.assertIs(self.preview_source_rig(False)["clip"], self.reference)

    def test_invalid_override_keeps_source_preview_static(self):
        spec._set_param_value(self.model, spec.PREVIEW_TARGET_MESH_PARAM, "1")
        self.animation["skeleton"] = SkeletonAsset("different", [Joint("other", -1)])
        rig = self.preview_source_rig()
        self.assertIsNone(rig["clip"])
        self.assertTrue(rig["retarget_static_pose"])


if __name__ == "__main__":
    unittest.main()
