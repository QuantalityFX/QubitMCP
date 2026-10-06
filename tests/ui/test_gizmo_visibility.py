import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PySide6 import QtCore, QtWidgets

from echograph.ui.gizmo_visibility import sync_gizmo_visibility
from echograph.ui.gl_view import GraphGLView
from echograph.ui.gl_view_mouse import (
    _handle_mouse_press_moderngl_left_gizmo_build_context,
    _handle_mouse_press_moderngl_left_gizmo_owner_pos,
    _handle_mouse_release_moderngl_pick_empty,
)
from echograph.ui.gl_view_paint import paint_gl


class GizmoVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.toggle = QtWidgets.QCheckBox()
        self.addCleanup(self.toggle.deleteLater)
        self.view = SimpleNamespace(
            _mgl_gizmo_enabled=True, _mgl_gizmo_visible=True,
            _mgl_gizmo_toggle=self.toggle, _xform_gizmo_owner='mesh',
            _xform_gizmo_owner_kind='mesh', _mesh_select_mode='object',
            _xform_gizmo_pos=(0, 0, 0), _xform_gizmo_idle_visible=True,
            _gl=Mock(), _use_moderngl=True, _paint_mgl=Mock(),
            _mgl_bind_default_fbo=Mock(), _debug_show_axis_overlay=True,
            _axis_overlay=SimpleNamespace(ensure_gl=Mock(return_value=False)),
            _mgl_pick_proj=np.eye(4), _mgl_pick_view=np.eye(4), _mgl_pick_model=np.eye(4),
            rect=lambda: QtCore.QRect(0, 0, 640, 480), update=Mock(),
            _rot_shared=SimpleNamespace(project_to_screen=Mock(return_value=None), end_drag=Mock()),
            _handle_mouse_press_moderngl_left_gizmo_owner_pos=lambda: ('mesh', (0, 0, 0)),
            _handle_mouse_press_moderngl_left_gizmo_pick_viewport=Mock(return_value=(1, 640, 480)),
            _handle_mouse_press_moderngl_left_gizmo_pick_matrices=Mock(return_value=None),
        )

    def assert_hidden(self):
        view = self.view
        for mode in ('translate', 'scale', 'rotate'):
            view._xform_gizmo_mode = mode
            view._axis_overlay.ensure_gl.reset_mock()
            view._rot_shared.project_to_screen.reset_mock()
            view._handle_mouse_press_moderngl_left_gizmo_pick_viewport.reset_mock()
            paint_gl(view)
            GraphGLView._draw_rotate_shared_overlay(view)
            painter = Mock()
            GraphGLView._draw_xform_gizmo_top_overlay(view, painter)
            _handle_mouse_press_moderngl_left_gizmo_build_context(view, None)
            view._axis_overlay.ensure_gl.assert_not_called()
            view._rot_shared.project_to_screen.assert_not_called()
            view._handle_mouse_press_moderngl_left_gizmo_pick_viewport.assert_not_called()
            painter.save.assert_not_called()
            self.assertFalse(self.toggle.isChecked())
        view._paint_mgl.assert_called()  # Hiding handles must not skip scene rendering.

    def test_deselection_hides_all_draw_and_pick_paths_even_with_idle_flag(self):
        for owner in (None, ''):
            self.view._xform_gizmo_owner = owner
            self.assert_hidden()

    def test_manual_switch_hides_rotation_as_well_as_translation_and_scale(self):
        GraphGLView.set_gizmo_visible(self.view, False)
        self.assert_hidden()
        GraphGLView.set_gizmo_visible(self.view, True)
        self.assertTrue(self.toggle.isChecked())
        self.view._xform_gizmo_mode = 'rotate'
        GraphGLView._draw_rotate_shared_overlay(self.view)
        self.view._rot_shared.project_to_screen.assert_called_once()
        _handle_mouse_press_moderngl_left_gizmo_build_context(self.view, None)
        self.view._handle_mouse_press_moderngl_left_gizmo_pick_viewport.assert_called_once()

    def test_automatic_hide_does_not_disable_next_selection_or_emit_toggle_signals(self):
        changed = Mock()
        self.toggle.toggled.connect(changed)
        for owner in ('mesh', None, 'Rig::skeleton_joint::left_elbow', '', 'other_mesh'):
            self.view._xform_gizmo_owner = owner
            self.assertEqual(sync_gizmo_visibility(self.view), bool(owner))
            self.assertEqual(self.toggle.isChecked(), bool(owner))
        changed.assert_not_called()
        GraphGLView.set_gizmo_visible(self.view, False)
        self.view._xform_gizmo_owner = None
        sync_gizmo_visibility(self.view)
        self.view._xform_gizmo_owner = 'other_mesh'
        self.assertFalse(sync_gizmo_visibility(self.view))

    def test_components_require_actual_selection_and_ignore_hover(self):
        for mode in ('point', 'edge', 'face'):
            with self.subTest(mode=mode):
                self.view._mesh_select_mode = mode
                element = dict(owner='mesh', mode=mode, index=1)
                self.view._mesh_select_selected_many = [element]
                self.assertTrue(sync_gizmo_visibility(self.view))
                self.view._mesh_select_selected_many = []
                self.view._mesh_select_hover = element
                self.assert_hidden()
                self.view._mesh_select_selected = element
                self.assertTrue(sync_gizmo_visibility(self.view))
                self.view._mesh_select_selected = None
        self.view._xform_gizmo_owner_kind = 'scene_skeleton_joint'
        self.assertTrue(sync_gizmo_visibility(self.view))
        self.view._xform_gizmo_owner_kind = 'retarget_target_joint'
        self.assertTrue(sync_gizmo_visibility(self.view))

    def test_look_through_camera_hides_only_that_camera_gizmo(self):
        view = self.view
        view._mgl_active_scene_camera_owner = lambda: 'Camera_A'
        view._xform_gizmo_owner = 'Camera_A'
        self.assertFalse(sync_gizmo_visibility(view))
        view._xform_gizmo_owner = 'mesh'
        self.assertTrue(sync_gizmo_visibility(view))

    def test_empty_rig_click_clears_selection_and_checkbox_then_reselects(self):
        view = self.view
        view._mgl_scene_skeleton_selected_joints = ['left_shoulder', 'left_elbow']
        view._mgl_scene_skeleton_selected_joint = 'left_elbow'
        view._mgl_scene_skeleton_active_owner = 'Rig'
        view._xform_gizmo_owner_kind = 'scene_skeleton_joint'
        sync_gizmo_visibility(view)
        _handle_mouse_release_moderngl_pick_empty(view)
        self.assertEqual(view._mgl_scene_skeleton_selected_joints, [])
        self.assertEqual(view._mgl_scene_skeleton_active_owner, 'Rig')
        view._rot_shared.end_drag.assert_called_once()
        self.assert_hidden()
        view._xform_gizmo_owner = 'Rig::skeleton_joint::left_shoulder'
        view._xform_gizmo_owner_kind = 'scene_skeleton_joint'
        self.assertTrue(sync_gizmo_visibility(view))

    def test_selected_retarget_joint_can_reattach_before_visibility_check(self):
        view = self.view
        view._xform_gizmo_owner = None
        view._xform_gizmo_owner_kind = 'retarget_target_joint'
        view._reattach_retarget_target_pose_gizmo_if_selected = lambda: setattr(view, '_xform_gizmo_owner', 'target_joint')
        view._handle_mouse_press_moderngl_left_gizmo_owner_pos = lambda: _handle_mouse_press_moderngl_left_gizmo_owner_pos(view)
        _handle_mouse_press_moderngl_left_gizmo_build_context(view, None)
        view._handle_mouse_press_moderngl_left_gizmo_pick_viewport.assert_called_once()
        self.assertTrue(self.toggle.isChecked())


if __name__ == '__main__':
    unittest.main()
