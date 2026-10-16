import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6 import QtCore, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.motion.animation_corrections import read_document
from echograph.motion.conversion import reconstruct_animation
from echograph.motion.rig_preview import build_guide, preview_offsets
from echograph.ui.gl_view import GraphGLView
from echograph.ui.timeline_controller import TimelineController
from nodes.animation_correction.editor import CorrectionSession
from nodes.rig_limiter.spec import export_context


class TimelineRangeControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        offsets = preview_offsets('standardman')
        points, _ = build_guide(offsets)
        skeleton, clip, _, _ = reconstruct_animation(
            np.repeat(points[None], 5, axis=0), 30, {}, rest_offsets=offsets)
        clip = replace(clip, end_time=60.)
        path = Path(self.temp.name)/'take.bvh'
        export_context(dict(skeleton=skeleton, clip=clip), path)
        self.context = dict(skeleton=skeleton, clip=clip, path=str(path), kind='mocap_collection')
        self.item = SimpleNamespace(model=GraphNode('Correction', kind='animation_correction'), scene=lambda: None)
        self.graph = QtWidgets.QGraphicsScene()
        self.window = QtWidgets.QWidget()
        self.window._current_path = str(Path(self.temp.name)/'workflow.json')
        self.window._active_scene_preview_context = dict(scene_name='CorrectionPreview', node='Correction')
        self.window.open_scene_assets = self.open_assets
        self.view = GraphGLView(self.graph, self.window)
        self.window.gl_view = self.view
        self.controller = TimelineController(self.window)
        self.view._timeline_fps = 30.
        self.view._timeline_frame_spin.setValue(1149)
        self.contexts = {}
        self.view._mgl_scene_owner_fbx_rig_context = self.contexts.get
        self.session = self.open_session()
        self.panel = self.view._timeline_panel
        self.host = QtWidgets.QWidget()
        self.host.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        self.panel.setParent(self.host)
        self.host.resize(1100, 800)
        self.view.resize(1100, 800)
        self.host.show()
        self.panel.show()
        self.view._layout_timeline_panel()
        self.app.processEvents()
        self.controls = self.view._timeline_range_controls

    def open_assets(self, assets, frame=True):
        self.contexts.clear()
        self.contexts.update((asset['node'], asset['fbx_rig_context']) for asset in assets)
        return True

    def open_session(self):
        session = CorrectionSession(self.item, self.context, self.view)
        session.open(self.window)
        return session

    def tearDown(self):
        self.session.finish()
        self.view.deleteLater()
        self.window.deleteLater()
        self.host.deleteLater()
        self.graph.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def drag(self, start_x, end_x):
        bar = self.controls.bar
        y = bar.height()//2
        QtTest.QTest.mousePress(bar, QtCore.Qt.LeftButton, pos=QtCore.QPoint(round(start_x), y))
        QtTest.QTest.mouseMove(bar, QtCore.QPoint(round(end_x), y))
        QtTest.QTest.mouseRelease(bar, QtCore.Qt.LeftButton, pos=QtCore.QPoint(round(end_x), y))

    def test_bottom_row_has_four_inputs_and_replaces_the_header_end_input(self):
        controls = self.controls
        self.assertTrue(controls.isVisible())
        self.assertTrue(self.view._timeline_end_frame_spin.isHidden())
        self.assertTrue(self.view._timeline_end_frame_label.isHidden())
        self.assertTrue(self.view._timeline_scrollbar.isHidden())
        self.assertEqual(controls.start_spin.value(), 1)
        self.assertEqual(controls.end_spin.value(), 1800)
        children = (controls.start_spin, controls.view_start_spin, controls.bar,
                    controls.view_end_spin, controls.end_spin)
        for left, right in zip(children, children[1:]):
            self.assertLess(left.geometry().right(), right.x())
        for child in children:
            self.assertTrue(controls.rect().contains(child.geometry()))
        self.assertFalse(controls.findChildren(QtWidgets.QLabel))
        self.session.finish()
        self.assertFalse(controls.isHidden())
        self.assertTrue(self.view._timeline_end_frame_spin.isHidden())

    def test_preview_sync_keeps_selected_joint_after_range_and_key_edits(self):
        for joint in ('left_wrist', 'left_elbow'):
            self.view._mgl_scene_skeleton_handle_click(dict(
                role='scene_skeleton', owner='Correction', name=joint, position=(0., 1., 0.)))
            owner = self.view._timeline_owner_name
            self.view._timeline_on_display_range_changed(10, 210)
            self.controller.sync_timeline_context(apply_current_frame=False, load_audio=False)
            self.assertEqual(self.view._timeline_owner_name, owner)
            self.assertEqual(self.view._timeline_mode, 'owner_keys')
            self.assertEqual(self.view._timeline_view_start, 10)
            self.assertEqual(self.view._timeline_view_span, 200)
            # The key editor must survive a completed edit and its deferred UI
            # refresh, even if another widget temporarily clears the gizmo.
            self.view._timeline_drag_axis_key(3, 0, 1, 15., commit=True)
            self.view._timeline_curve_selected = {(3, 1)}
            self.assertTrue(self.view.timeline_delete_selected_keys())
            self.view._xform_gizmo_owner = None
            QtCore.QTimer.singleShot(0, self.controller.sync_timeline_context)
            self.app.processEvents()
            self.assertEqual(self.view._timeline_owner_name, owner)
            self.assertEqual(self.view._timeline_mode, 'owner_keys')
            self.assertEqual(self.view._timeline_view_start, 10)

    def test_master_is_explicit_and_all_modes_share_the_range_controls(self):
        self.view._timeline_on_display_range_changed(10, 210)
        token = self.view._timeline_owner_name
        self.view._timeline_show_composition_mode()
        self.controller.sync_timeline_context(apply_current_frame=False, load_audio=False)
        self.assertEqual(self.view._timeline_mode, 'composition')
        self.assertIsNone(self.view._timeline_owner_name)
        self.assertTrue(self.controls.isVisible())
        self.controls.end_spin.setValue(500)
        self.controls.view_start_spin.setValue(5)
        self.controls.view_end_spin.setValue(20)
        self.assertEqual(self.view._timeline_view_span, 15)
        self.view._timeline_enter_owner_key_mode('Cube')
        self.assertEqual(self.controls.view_start_spin.value(), 5)
        self.assertEqual(self.controls.view_end_spin.value(), 20)
        self.view._timeline_set_curves_mode(True)
        self.assertTrue(self.controls.isVisible())
        self.assertTrue(self.view._timeline_end_frame_spin.isHidden())
        self.view._timeline_set_fps(30., save=False)
        self.view._timeline_enter_owner_key_mode(token)
        self.assertEqual(self.controls.view_start_spin.value(), 10)
        self.assertEqual(self.controls.view_end_spin.value(), 210)

    def test_standard_range_persists_across_scene_contexts(self):
        self.view._timeline_show_composition_mode()
        self.controls.start_spin.setValue(0)
        self.controls.end_spin.setValue(500)
        self.controls.view_start_spin.setValue(10)
        self.controls.view_end_spin.setValue(210)
        self.view.set_timeline_scene_context(scene_name='OtherScene', apply_current_frame=False)
        self.view.set_timeline_scene_context(scene_name='CorrectionPreview', apply_current_frame=False)
        self.assertEqual(self.controls.start_spin.value(), 0)
        self.assertEqual(self.controls.end_spin.value(), 500)
        self.assertEqual(self.controls.view_start_spin.value(), 10)
        self.assertEqual(self.controls.view_end_spin.value(), 210)

    def test_preview_sync_does_not_carry_joint_editor_to_a_different_preview(self):
        self.window._active_scene_preview_context = dict(scene_name='AnotherPreview')
        self.controller.sync_timeline_context(apply_current_frame=False, load_audio=False)
        self.assertEqual(self.view._timeline_mode, 'composition')
        self.assertIsNone(self.view._timeline_owner_name)

    def test_standard_range_restores_before_animated_assets_finish_loading(self):
        self.view._timeline_show_composition_mode()
        self.view._timeline_out_frame = None
        self.view._timeline_total_max = 1800
        self.view._timeline_sync_range_controls()
        self.view._timeline_on_display_range_changed(1000, 1200)
        self.view._timeline_range_save_to_disk()
        self.view.set_timeline_scene_context(scene_name='OtherScene', apply_current_frame=False)
        self.view.set_timeline_scene_context(scene_name='CorrectionPreview', apply_current_frame=False)
        self.assertEqual(self.controls.end_spin.value(), 1800)
        self.assertEqual(self.controls.view_start_spin.value(), 1000)
        self.assertEqual(self.controls.view_end_spin.value(), 1200)

    def test_scene_outliner_parent_row_keeps_the_selected_joint_editor(self):
        active = GraphNode('CorrectionPreview', kind='scene')
        card = SimpleNamespace(_node_ref=active, _scene_outliner_user_selected=True,
                               _scene_selected_kind='rig', _scene_selected_owner='Correction')
        self.window._active_scene_preview_context = None
        self.window._active_scene_node = active
        self.window._card_by_node = {'CorrectionPreview': card}
        self.view._mgl_scene_skeleton_handle_click(dict(
            role='scene_skeleton', owner='Correction', name='left_elbow', position=(0., 1., 0.)))
        owner = self.view._timeline_owner_name
        self.controller.sync_timeline_context(apply_current_frame=False, load_audio=False)
        self.assertEqual(self.view._timeline_owner_name, owner)
        self.view._timeline_sync_key_owner_from_outliner()
        self.assertEqual(self.view._timeline_owner_name, owner)
        # Selecting a different outliner object still changes the key target.
        card._scene_selected_owner = 'Cube'
        self.controller.sync_timeline_context(apply_current_frame=False, load_audio=False)
        self.assertEqual(self.view._timeline_owner_name, 'Cube')

    def test_workflow_switch_does_not_reuse_a_previous_workflows_key_target(self):
        self.window._current_path = str(Path(self.temp.name)/'other_workflow.json')
        self.controller.sync_timeline_context(apply_current_frame=False, load_audio=False)
        self.assertEqual(self.view._timeline_mode, 'composition')
        self.assertIsNone(self.view._timeline_owner_name)

    def test_numeric_range_clamps_display_without_changing_pose_or_joint_keys(self):
        before_keys = self.session.draft.to_dict()
        self.controls.view_start_spin.setValue(10)
        self.controls.view_end_spin.setValue(210)
        self.assertEqual(self.view._timeline_view_start, 10)
        self.assertEqual(self.view._timeline_frame_slider.maximum(), 200)
        self.assertEqual(self.view._timeline_current_frame(), 1149)
        self.assertAlmostEqual(self.session.time(), 1149/30.)
        self.assertTrue(self.view._timeline_playhead.isHidden())
        for joint in ('left_wrist', 'left_elbow', 'right_ankle'):
            token = self.view._mgl_scene_skeleton_joint_owner_token('Correction', joint)
            self.view.set_timeline_scene_context(owner_name=token, apply_current_frame=False, load_audio=False)
            self.assertEqual(self.view._timeline_view_start, 10)
            self.assertEqual(self.view._timeline_view_span, 200)
            self.assertAlmostEqual(self.session.time(), 1149/30.)
        self.assertEqual(self.session.draft.to_dict(), before_keys)

    def test_triangle_handles_resize_and_middle_pans_without_writing_during_drag(self):
        self.view._timeline_on_display_range_changed(100, 1000)
        bar = self.controls.bar
        left, right = bar.handle_positions()
        with patch.object(self.session, 'save', wraps=self.session.save) as save:
            y = bar.height()//2
            QtTest.QTest.mousePress(bar, QtCore.Qt.LeftButton, pos=QtCore.QPoint(round(left), y))
            QtTest.QTest.mouseMove(bar, QtCore.QPoint(round(left+30), y))
            save.assert_not_called()
            QtTest.QTest.mouseRelease(bar, QtCore.Qt.LeftButton, pos=QtCore.QPoint(round(left+30), y))
            save.assert_called_once()
        self.assertGreater(self.controls.view_start_spin.value(), 100)
        self.assertEqual(self.controls.view_end_spin.value(), 1000)
        left, right = bar.handle_positions()
        self.drag(right, right-25)
        self.assertLess(self.controls.view_end_spin.value(), 1000)
        span = self.view._timeline_view_span
        left, right = bar.handle_positions()
        start = self.view._timeline_view_start
        self.drag((left+right)/2, (left+right)/2+20)
        self.assertGreater(self.view._timeline_view_start, start)
        self.assertEqual(self.view._timeline_view_span, span)
        self.assertEqual(self.view._timeline_current_frame(), 1149)

    def test_editing_full_bounds_clips_the_visible_window_at_that_end(self):
        self.view._timeline_on_display_range_changed(10, 210)
        self.controls.start_spin.setValue(20)
        self.assertEqual(self.controls.view_start_spin.value(), 20)
        self.assertEqual(self.controls.view_end_spin.value(), 210)
        self.controls.end_spin.setValue(200)
        self.assertEqual(self.controls.view_start_spin.value(), 20)
        self.assertEqual(self.controls.view_end_spin.value(), 200)

    def test_saved_bounds_and_display_range_survive_reopening(self):
        self.controls.start_spin.setValue(0)
        self.controls.end_spin.setValue(500)
        self.controls.view_start_spin.setValue(10)
        self.controls.view_end_spin.setValue(210)
        saved = read_document(self.session.path)['timeline_range']
        self.assertEqual(saved, dict(fps=30., start=0, end=500, view_start=10, view_end=210))
        self.session.finish()
        self.session = self.open_session()
        self.assertEqual(self.controls.start_spin.value(), 0)
        self.assertEqual(self.controls.end_spin.value(), 500)
        self.assertEqual(self.controls.view_start_spin.value(), 10)
        self.assertEqual(self.controls.view_end_spin.value(), 210)

    def test_short_display_window_and_handles_stay_inside_full_bounds(self):
        self.view._timeline_on_display_range_changed(10, 12)
        self.assertEqual(self.view._timeline_frame_slider.maximum(), 2)
        bar = self.controls.bar
        left, right = bar.handle_positions()
        self.drag(left, right+100)
        self.assertEqual(self.view._timeline_view_span, 1)
        self.assertLess(self.controls.view_start_spin.value(), self.controls.view_end_spin.value())
        self.controls.start_spin.setValue(1800)
        self.assertEqual(self.controls.view_start_spin.value(), 1800)
        self.assertEqual(self.controls.view_end_spin.value(), 1800)
        self.assertEqual(self.view._timeline_frame_slider.maximum(), 0)

    def test_fps_has_one_decimal_and_speed_only_shows_entered_decimals(self):
        fps = self.view._timeline_fps_spin
        speed = self.view._timeline_speed_spin
        self.assertEqual(fps.cleanText(), '30.0')
        self.assertLess(fps.width(), 68)
        self.assertLess(speed.width(), 78)
        blocked = QtCore.QSignalBlocker(speed)
        for text, value, displayed in (('100', 100., '100%'), ('75.5', 75.5, '75.5%'),
                                       ('12.25', 12.25, '12.25%'), ('100.00', 100., '100%')):
            speed.lineEdit().setText(text)
            speed.interpretText()
            self.assertEqual(speed.value(), value)
            self.assertEqual(speed.text(), displayed)
        del blocked


if __name__ == '__main__':
    unittest.main()
