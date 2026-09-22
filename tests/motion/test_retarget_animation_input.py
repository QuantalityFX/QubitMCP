from types import SimpleNamespace
import math
import unittest
from unittest.mock import patch

from PySide6 import QtWidgets
from nodes.anim_retarget import spec
from echograph.rigging.fbx_canonical import (
    AnimationClip, Joint, JointAnimationTrack, QuatKeyframe, SkeletonAsset,
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
