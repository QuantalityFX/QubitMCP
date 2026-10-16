"""Visible transform endpoints must start the same drags as their shafts."""
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from PySide6 import QtCore

import numpy as np

from echograph.ui import gl_view_mouse as mouse
from echograph.ui.gl_view_math import dist_pt_seg, project_local


class GizmoTipPickingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mouse._ensure_gl_view_globals()

    def setUp(self):
        self.view = SimpleNamespace(
            devicePixelRatioF=lambda: 1.0,
            _begin_xform_history=Mock(), setCursor=Mock(),
            _mgl_pick_press_pos=object(),
        )
        for name in dir(mouse):
            if name.startswith('_handle_mouse_press_moderngl_left_gizmo_'):
                setattr(self.view, name, MethodType(getattr(mouse, name), self.view))
        self.mvp = np.eye(4, dtype=np.float32)
        self.mvp[:2, :3] = ((.8, 0, -.4), (0, .8, -.4))
        self.p0 = project_local((0, 0, 0), self.mvp, 500, 500)

    def test_cone_tips_start_axis_translation(self):
        view = self.view
        points = view._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_map(
            axis_len=1.0, PVTRS=self.mvp, vw=500, vh=500)
        for index, axis in enumerate(('x', 'y', 'z')):
            with self.subTest(axis=axis):
                tip = project_local(np.eye(3)[index], self.mvp, 500, 500)
                np.testing.assert_allclose(points[axis], tip)
                self.assertFalse(view._handle_mouse_press_moderngl_left_gizmo_translate_is_far_from_center(
                    p0=self.p0, px_dev=tip[0], py_dev=tip[1],
                    max_axis_len=view._handle_mouse_press_moderngl_left_gizmo_axis_proj_max_len(
                        p0=self.p0, axis_proj=points)))
                event = Mock()
                self.assertTrue(view._handle_mouse_press_moderngl_left_gizmo_translate_start_axis_drag(
                    e=event, owner='mesh', g=np.zeros(3), p0=self.p0,
                    axis_dirs=dict(zip(('x', 'y', 'z'), np.eye(3))), axis_proj=points,
                    renderer=view, px_dev=tip[0], py_dev=tip[1], dist_pt_seg=dist_pt_seg))
                self.assertTrue(view._xform_dragging)
                self.assertEqual(view._xform_drag_mode, 'translate')
                self.assertEqual(view._xform_drag_axis, axis)
                np.testing.assert_allclose(view._xform_drag_axis_world, np.eye(3)[index])
                event.accept.assert_called_once()

    def test_enlarged_cube_outer_corners_start_axis_scaling(self):
        view = self.view
        view._mgl_gizmo_tips_size_scale = 2.0
        points = view._handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_map(
            cube_axis_pos=.87, PVTRS=self.mvp, vw=500, vh=500)
        for index, axis in enumerate(('x', 'y', 'z')):
            for dpr in (1.0, 2.0):
                with self.subTest(axis=axis, dpr=dpr):
                    view.devicePixelRatioF = lambda: dpr
                    # Outermost vertex of the actual 0.16-wide rendered cube.
                    corner = np.eye(3)[index] * .87 + np.array((.08, -.08, -.08))
                    px, py = project_local(corner, self.mvp, 500, 500)
                    pick_axis, dx, dy = view._handle_mouse_press_moderngl_left_gizmo_scale_pick_axis(
                        p0=self.p0, axis_proj=points, px_dev=px, py_dev=py)
                    self.assertEqual(pick_axis, axis)
                    event = Mock()
                    self.assertTrue(view._handle_mouse_press_moderngl_left_gizmo_scale_start(
                        e=event, ctx=dict(renderer=view, owner='mesh', g=np.zeros(3),
                            p0=self.p0, px_dev=px), pick_axis=pick_axis, dx0=dx, dy0=dy,
                        use_local=False, R=np.eye(4)))
                    self.assertEqual(view._xform_drag_mode, 'scale')
                    self.assertEqual(view._xform_drag_axis, axis)
                    np.testing.assert_allclose(view._xform_scale_axis_world, np.eye(3)[index])
                    event.accept.assert_called_once()

    def test_clicks_outside_gizmo_remain_available_for_navigation(self):
        view = self.view
        view._mgl_gizmo_tips_size_scale = 2.0
        points = {'x': (450, 250), 'y': (250, 50), 'z': (150, 350)}
        axis, _, _ = view._handle_mouse_press_moderngl_left_gizmo_scale_pick_axis(
            p0=self.p0, axis_proj=points, px_dev=490, py_dev=490)
        self.assertIsNone(axis)
        self.assertTrue(view._handle_mouse_press_moderngl_left_gizmo_translate_is_far_from_center(
            p0=self.p0, px_dev=490, py_dev=490, max_axis_len=200))

    def press_view(self, *, hovered='x', center=False, gizmo_hit=True):
        view = self.view
        view._use_moderngl = True
        view._mesh_select_mode = ''
        view._xform_hover_axis = hovered
        view._xform_hover_center = center
        view._xform_gizmo_mode = 'translate'
        view._cloth_mouse_press = Mock(return_value=False)
        view._mesh_box_select_active_mode = Mock(return_value='')
        view._handle_mouse_press_moderngl_mesh_box_select_start = Mock(return_value=False)
        view._handle_mouse_press_moderngl_left_gizmo = Mock(return_value=gizmo_hit)
        view._handle_mouse_press_moderngl_retarget_joint = Mock(return_value=True)
        event = Mock()
        event.button.return_value = QtCore.Qt.LeftButton
        event.modifiers.return_value = QtCore.Qt.NoModifier
        return view,event

    def test_scale_shafts_accept_clicks_throughout_their_hover_halo(self):
        points = {'x':(450,250),'y':(250,50),'z':(150,350)}
        for tip_scale,offset in ((1.,14.),(2.,28.)):
            with self.subTest(tip_scale=tip_scale):
                self.view._mgl_gizmo_tips_size_scale = tip_scale
                axis,_,_ = self.view._handle_mouse_press_moderngl_left_gizmo_scale_pick_axis(
                    p0=self.p0,axis_proj=points,px_dev=350,py_dev=250+offset)
                self.assertEqual(axis,'x')

    def test_drawn_axes_are_picked_after_hover_clears_in_offset_high_dpi_viewports(self):
        view = self.view
        view._xform_overlay_owner = 'mesh'
        view._xform_overlay_center_px = QtCore.QPointF(250,250)
        view._xform_overlay_axis_proj = dict(x=QtCore.QPointF(450,250),
            y=QtCore.QPointF(250,50),z=QtCore.QPointF(150,350))
        view._xform_hover_axis = None
        view._mgl_active_viewport_qrectf = lambda:QtCore.QRectF(100,50,500,500)
        view._handle_mouse_press_moderngl_left_gizmo_translate_start_view_ray = Mock(return_value=None)
        for mode in ('translate','scale'):
            view._xform_overlay_mode = mode
            for axis,point in view._xform_overlay_axis_proj.items():
                with self.subTest(mode=mode,axis=axis):
                    event = Mock()
                    event.position.return_value = point
                    ctx = dict(mode=mode,owner='mesh',dpr=2.,renderer=view,g=np.zeros(3),
                        axes=dict(zip(('x','y','z'),np.eye(3))),px_dev=(point.x()-100)*2,
                        py_dev=(point.y()-50)*2,vw=1000,vh=1000,P=np.eye(4),V=np.eye(4),M=np.eye(4))
                    self.assertTrue(mouse._try_drawn_xform_gizmo_press(view,event,ctx))
                    self.assertEqual(view._xform_drag_axis,axis)
                    self.assertEqual(view._xform_drag_mode,mode)
                    event.accept.assert_called_once()

    def test_highlighted_arms_and_tips_win_over_nearby_ik_controls_and_joints(self):
        for mode in ('translate','scale'):
            for axis in ('x','y','z'):
                with self.subTest(mode=mode,axis=axis):
                    view,event = self.press_view(hovered=axis)
                    view._xform_gizmo_mode = mode
                    with patch.object(mouse,'_handle_animation_correction_control_press',return_value=True) as control:
                        self.assertTrue(mouse._handle_mouse_press_moderngl(view,event))
                    view._handle_mouse_press_moderngl_left_gizmo.assert_called_once_with(event)
                    control.assert_not_called()
                    view._handle_mouse_press_moderngl_retarget_joint.assert_not_called()
                    view._cloth_mouse_press.assert_not_called()

    def test_highlighted_center_still_grabs_the_gizmo(self):
        view,event = self.press_view(hovered=None,center=True)
        with patch.object(mouse,'_handle_animation_correction_control_press',return_value=True) as control:
            self.assertTrue(mouse._handle_mouse_press_moderngl(view,event))
        view._handle_mouse_press_moderngl_left_gizmo.assert_called_once_with(event)
        control.assert_not_called()

    def test_joint_controls_keep_clicks_when_the_drawn_gizmo_is_not_hit(self):
        for hovered,hit in ((None,False),('x',False)):
            with self.subTest(hovered=hovered,hit=hit):
                view,event = self.press_view(hovered=hovered,gizmo_hit=hit)
                with patch.object(mouse,'_handle_animation_correction_control_press',return_value=True) as control:
                    self.assertTrue(mouse._handle_mouse_press_moderngl(view,event))
                control.assert_called_once_with(view,event,False)

    def test_shift_selection_and_alt_navigation_do_not_use_hover_priority(self):
        for modifier in (QtCore.Qt.ShiftModifier,QtCore.Qt.AltModifier):
            with self.subTest(modifier=modifier):
                view,event = self.press_view()
                event.modifiers.return_value = modifier
                with patch.object(mouse,'_handle_animation_correction_control_press',return_value=True):
                    self.assertTrue(mouse._handle_mouse_press_moderngl(view,event))
                view._handle_mouse_press_moderngl_left_gizmo.assert_not_called()


if __name__ == '__main__':
    unittest.main()
