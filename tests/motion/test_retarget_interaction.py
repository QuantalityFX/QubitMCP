from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid

from nodes.anim_retarget import spec
from echograph.ui.gl_view_mouse import _handle_mouse_press_moderngl
from echograph.ui.gl_scene import MGLSceneItem
from echograph.rigging.fbx_canonical import (
    AnimationClip, Joint, JointAnimationTrack, JointTransform, QuatKeyframe,
    SkeletonAsset, Vec3Keyframe,
)
from test_retarget_viewport import Renderer


class RetargetInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.model = SimpleNamespace(name="Retarget", kind="anim_retarget", params=[])
        self.item = SimpleNamespace(model=self.model)
        self.view = Renderer()
        self.view._mgl_retarget_node_item = self.item
        self.view._mgl_retarget_node_model = self.model
        self.view._mgl_retarget_refresh_target_pose_contexts = Mock()

    def test_constraint_preserves_mapping_and_has_distinct_line(self):
        spec.set_joint_map_link(self.item, "root", "tip", notify_scene=False)
        self.view._mgl_retarget_joint_map = {"root": "tip"}
        self.assertTrue(self.view._mgl_retarget_set_pelvis_constraint_link("root", "root"))
        self.assertEqual(spec._joint_map_payload(self.model), {"root": "tip"})
        self.assertEqual(self.view._mgl_retarget_joint_map, {"root": "tip"})
        self.view._mgl_retarget_visible_position_map = lambda role: {
            "root": (0 if role == "source" else 3, 0, 0), "tip": (3, 2, 0)}
        made = []
        def wire(**kwargs):
            item = MGLSceneItem(kwargs["name"], lambda *args: None, tag=kwargs["tag"])
            made.append(item)
            return item
        self.view._mgl_add_wire_item_from_points = wire
        self.view._mgl_retarget_refresh_link_item()
        self.assertEqual(len(made), 2)
        self.assertNotEqual(made[0].payload["color"], made[1].payload["color"])
        self.view._mgl_retarget_joint_map = {}
        made.clear()
        self.view._mgl_retarget_refresh_link_item()
        self.assertEqual(len(made), 1)  # Constraints render without regular mappings.

    def test_constraint_animates_root_without_replacing_pelvis_rotation(self):
        source = SkeletonAsset("source", [Joint("pelvis", -1)])
        target = SkeletonAsset("target", [Joint("root", -1),
            Joint("pelvis", 0, JointTransform(translation=(0, 1, 0)))])
        clip = AnimationClip("walk", 0, 1, 30, [JointAnimationTrack("pelvis",
            translation_keys=[Vec3Keyframe(0, (0, 0, 0)), Vec3Keyframe(1, (2, 0, 0))],
            rotation_keys=[QuatKeyframe(0, (0, 0, 0, 1)), QuatKeyframe(1, (0, 0.70710678, 0, 0.70710678))])])
        result = spec.RetargetSourceTargetResult("ok",
            source_context={"skeleton": source, "clip": clip},
            target_context={"skeleton": target})
        spec.set_joint_map_link(self.item, "pelvis", "pelvis", notify_scene=False)
        spec.set_pelvis_constraint_link(self.item, "pelvis", "root", notify_scene=False)
        output = spec.build_anim_retarget_clip(self.item, result)
        self.assertIsNotNone(output)
        tracks = {track.joint_name: track for track in output.tracks}
        self.assertTrue(tracks["pelvis"].rotation_keys)
        self.assertTrue(tracks["root"].translation_keys)
        self.assertNotEqual(tracks["root"].translation_keys[0].value,
                            tracks["root"].translation_keys[-1].value)
        self.assertEqual(spec._joint_map_payload(self.model), {"pelvis": "pelvis"})

    def test_target_pose_gizmo_gets_click_before_joint_picker(self):
        view = SimpleNamespace(_use_moderngl=True, _xform_gizmo_owner="retarget-target-pose::root",
            _xform_gizmo_owner_kind="retarget_target_joint",
            _handle_mouse_press_moderngl_mesh_box_select_start=Mock(return_value=False),
            _handle_mouse_press_moderngl_left_gizmo=Mock(return_value=True),
            _handle_mouse_press_moderngl_retarget_joint=Mock(return_value=True))
        event = SimpleNamespace(modifiers=lambda: QtCore.Qt.NoModifier)
        self.assertTrue(_handle_mouse_press_moderngl(view, event))
        view._handle_mouse_press_moderngl_retarget_joint.assert_not_called()

    def test_pose_refresh_uploads_changed_handles_even_without_selection(self):
        self.view._mgl_retarget_joint_positions_for_context = Mock(return_value=[(0, 0, 0), (2, 0, 0)])
        self.view._mgl_retarget_rebuild_handle_mesh_for_owner = Mock()
        self.view._mgl_retarget_selected_joint = None
        self.view._mgl_retarget_refresh_target_pose_handles()
        self.view._mgl_retarget_rebuild_handle_mesh_for_owner.assert_not_called()
        self.view._mgl_retarget_refresh_selection_item()
        self.view._mgl_retarget_rebuild_handle_mesh_for_owner.assert_called_once()
        self.assertEqual(self.view._mgl_retarget_joint_handles_by_owner["target"][1]["position"], (2, 0, 0))

    def make_card(self):
        card = QtWidgets.QWidget()
        self.addCleanup(lambda: card.close() if isValid(card) else None)
        card._node_ref = self.model
        card._graph_scene = SimpleNamespace(_node_items={self.model.name: self.item})
        self.assertTrue(spec.augment_infocard_footer(card, QtWidgets.QVBoxLayout(card)))
        return card

    def test_panel_modes_pose_table_and_animate_work_without_panel_view_click(self):
        result = spec.RetargetSourceTargetResult("ok")
        window = SimpleNamespace(gl_view=self.view)
        # The panel itself is a separate window: resolve through the graph window.
        with patch.object(spec, "_resolve_window", return_value=window), \
             patch.object(spec, "resolve_anim_retarget_inputs", return_value=result), \
             patch.object(spec, "open_anim_retarget_preview", return_value=True) as preview:
            card = self.make_card()
            tabs = card.findChild(QtWidgets.QTabWidget)
            def select(label):
                index = next(i for i in range(tabs.count()) if label in tabs.tabText(i).lower())
                tabs.setCurrentIndex(index)
                return tabs.widget(index)
            select("constraint")
            self.assertEqual(self.view._mgl_retarget_pick_mode(), "pelvis_constraint")
            pose_tab = select("target pose")
            self.assertEqual(self.view._mgl_retarget_pick_mode(), "target_pose")
            self.assertTrue(self.view._mgl_retarget_handle_target_pose_click(
                self.view._mgl_retarget_joint_handles_by_owner["target"][0]))
            self.assertTrue(self.view._mgl_retarget_set_target_pose_joint_rotation(
                "retarget-target-pose::root", (0, 45, 0)))
            table = pose_tab.findChild(QtWidgets.QTableWidget)
            self.assertEqual(table.rowCount(), 1)
            self.assertEqual(table.item(0, 0).text(), "root")
            self.assertEqual(spec._target_pose_offsets_payload(self.model)["root"]["rotation"], [0, 45, 0])
            skeleton = SkeletonAsset("target", [Joint("root", -1),
                Joint("tip", 0, JointTransform(translation=(2, 0, 0)))])
            positions = self.view._mgl_retarget_joint_positions_for_context(
                {"skeleton": skeleton}, "target", "target")
            np.testing.assert_allclose(positions[1], (2 ** 0.5, 0, -(2 ** 0.5)), atol=1e-5)
            select("constraint")
            self.assertEqual(self.view._xform_gizmo_owner, "")
            spec.set_joint_map_link(self.item, "root", "tip", notify_scene=False)
            self.view._mgl_retarget_joint_map = {"root": "tip"}
            for role in ("source", "target"):
                self.assertTrue(self.view._mgl_retarget_handle_click(
                    self.view._mgl_retarget_joint_handles_by_owner[role][0]))
            self.assertEqual(spec._joint_map_payload(self.model), {"root": "tip"})
            self.assertEqual(spec._pelvis_constraint_payload(self.model)["target"], "root")
            checkbox = next(c for c in card.findChildren(QtWidgets.QCheckBox)
                            if "Animate Target" in c.text())
            checkbox.setChecked(not checkbox.isChecked())
            preview.assert_called_once()
            self.assertFalse(preview.call_args.kwargs["frame"])
            # Settings for another node must not replace the currently viewed node.
            self.view._mgl_retarget_node_item = SimpleNamespace(model=SimpleNamespace(name="Other"))
            checkbox.setChecked(not checkbox.isChecked())
            preview.assert_called_once()
            self.view._mgl_retarget_node_item = self.item
            # A retiring panel must not reset the mode of its replacement.
            replacement = self.make_card()
            new_tabs = replacement.findChild(QtWidgets.QTabWidget)
            new_tabs.setCurrentIndex(next(i for i in range(new_tabs.count())
                                         if "constraint" in new_tabs.tabText(i).lower()))
            card.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            self.assertEqual(self.view._mgl_retarget_pick_mode(), "pelvis_constraint")


if __name__ == "__main__":
    unittest.main()
