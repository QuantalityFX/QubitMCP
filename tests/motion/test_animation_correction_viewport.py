"""Exercise correction scrubbing and preview changes on the desktop GL backend."""
import ctypes
from dataclasses import replace
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from scipy.spatial.transform import Rotation

from echograph.model import GraphNode
from echograph.motion.conversion import reconstruct_animation
from echograph.motion.rig_preview import build_guide, preview_offsets
from echograph.rigging.fbx_canonical import JointAnimationTrack, QuatKeyframe
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
from echograph.ui.gl_view import GraphGLView
from echograph.ui.timeline_controller import TimelineController
from nodes.animation_correction.editor import CorrectionSession
from nodes.rig_limiter.spec import export_context


@unittest.skipUnless(sys.platform == 'win32', 'Requires desktop OpenGL')
class AnimationCorrectionViewportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        offsets = preview_offsets('standardman')
        positions, _ = build_guide(offsets)
        skeleton, clip, _, _ = reconstruct_animation(
            np.repeat(positions[None], 5, axis=0), 20, {}, rest_offsets=offsets)
        arm = next(track for track in clip.tracks if track.joint_name == 'left_shoulder')
        arm.rotation_keys = [QuatKeyframe(i / 20., tuple(Rotation.from_euler(
            'z', i * 18., degrees=True).as_quat())) for i in range(5)]
        self.path = Path(self.temp.name) / 'limited.bvh'
        export_context(dict(skeleton=skeleton, clip=clip), self.path)
        self.context = dict(skeleton=skeleton, clip=clip, path=str(self.path),
                            kind='rig_limiter', model=GraphNode('Limiter', kind='rig_limiter'))
        self.scene = QtWidgets.QGraphicsScene()
        self.addCleanup(self.scene.deleteLater)
        self.view = GraphGLView(self.scene)
        self.addCleanup(self.view.close)
        self.view.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        self.view.resize(1000, 800)
        self.view.show()
        self.app.processEvents()
        if not self.view.isValid() or self.view._mgl_ctx is None:
            self.skipTest('OpenGL context unavailable')
        item = SimpleNamespace(model=GraphNode('Correction', kind='animation_correction'),
                               scene=lambda: None)
        self.session = CorrectionSession(item, self.context, self.view)
        self.session.open(SimpleNamespace(open_scene_assets=self.view.load_scene_assets))
        self.addCleanup(self.finish_session)
        self.app.processEvents()

    def finish_session(self):
        from shiboken6 import isValid
        if isValid(self.session):
            self.session.finish()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def paint_frame(self, frame):
        self.view._timeline_frame_spin.setValue(frame)
        self.view.repaint()
        self.app.processEvents()

    def test_reload_scope_tracks_real_joint_axis_and_empty_viewport_selection_without_rebuilding(self):
        from echograph.ui.gl_view_mouse import _handle_mouse_release_moderngl_pick_empty
        session,view = self.session,self.view
        self.assertEqual(session.panel.reload.text(),'Reload all')
        self.assertIsNone(session.reload_selection)
        self.assertTrue(session.select_joint('left_wrist'))
        self.app.processEvents()
        self.assertEqual(session.reload_selection,'left_wrist')
        self.assertEqual(session.panel.reload.text(),'Reload left_wrist')
        with patch.object(session,'rebuild') as rebuild, patch.object(session,'save') as save:
            QtTest.QTest.mouseClick(view._timeline_axis_labels[1],QtCore.Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(view._timeline_curve_axes_filter,{1})
            self.assertEqual(session.panel.reload.text(),'Reload Py')
            _handle_mouse_release_moderngl_pick_empty(view)
            self.assertEqual(session.panel.reload.text(),'Reload all')
            self.assertIsNone(session.reload_selection)
            self.assertFalse(any(button.isEnabled() for button in session.panel.ik_buttons))
            rebuild.assert_not_called()
            save.assert_not_called()

    def test_fk_position_axis_selection_and_numeric_edit_use_actual_timeline_controls(self):
        session,view = self.session,self.view
        session.set_ik(False)
        session.select_joint('left_wrist')
        view._timeline_set_fps(40.)
        self.paint_frame(3)
        QtTest.QTest.mouseClick(view._timeline_axis_labels[1],QtCore.Qt.LeftButton)
        self.assertEqual(view._timeline_curve_axes_filter,{1})
        button = view._timeline_panel.findChild(QtWidgets.QPushButton,'GLTimelineSetKeyButton')
        button.click()
        self.app.processEvents()
        self.assertEqual(view._timeline_keys[3]['axis_mask'],[False,True,False,False,False,False])
        self.assertEqual(session.fk_keys,{})
        field = view._timeline_coord_y
        value = field.value()+.02
        field.setValue(value)
        field.editingFinished.emit()
        self.app.processEvents()
        index = session.skeleton.joint_names.index('left_wrist')
        pose = evaluate_rig_at_time(session.skeleton,session.draft,session.time(),loop=False)
        self.assertAlmostEqual(pose.local_transforms[index].translation[1],value,places=6)
        self.assertFalse(view._timeline_key_is_ik(3,1))
        self.assertEqual(session.fk_keys,{})

    def test_join_ik_ranges_shortcut_keeps_all_four_controller_keys(self):
        from echograph.ui import hotkeys_config
        session,view = self.session,self.view
        session.select_joint('left_wrist')
        for frame,mode in ((0,'start'),(1,'end'),(3,'start'),(4,'end')):
            self.paint_frame(frame)
            self.assertTrue(session.key_ik('left_wrist',mode=mode))
        left,right = session.ik_spans
        view._timeline_set_curves_mode(True)
        view._timeline_curve_selected = {(0,1),(0,3)}
        with patch.object(hotkeys_config,'_KEYMAP_CACHE',dict(hotkeys_config.DEFAULT_KEYMAP)):
            QtTest.QTest.keyClick(view._timeline_curves_canvas,QtCore.Qt.Key_J,QtCore.Qt.ControlModifier)
        self.assertEqual(len(session.ik_spans),3)
        self.assertEqual(session.ik_spans[1]['start'],left['end'])
        self.assertEqual(session.ik_spans[1]['end'],right['start'])
        self.assertEqual({frame for frame,row in view._timeline_keys.items() if row.get('ik_key')},{0,1,3,4})

    def test_fuse_button_follows_rows_and_curve_selection_and_joins_without_losing_selected_keys(self):
        session,view = self.session,self.view
        session.select_joint('left_wrist')
        for frame,mode in ((0,'start'),(1,'end'),(3,'start'),(4,'end')):
            self.paint_frame(frame)
            self.assertTrue(session.key_ik('left_wrist',mode=mode))
        left,right = session.ik_spans
        button = session.panel.join_ik_button
        view._timeline_set_curves_mode(False)
        view._timeline_curve_selected = {(0,1),(0,3)}
        view._timeline_update_key_markers()
        self.assertTrue(button.isEnabled())
        view._timeline_set_curves_mode(True)
        canvas = view._timeline_curves_canvas
        canvas._set_selected_set({(0,1)})
        self.assertFalse(button.isEnabled())
        canvas._set_selected_set({(0,1),(0,3)})
        self.assertTrue(button.isEnabled())
        height_before = session.panel.height()
        QtTest.QTest.mouseClick(button,QtCore.Qt.LeftButton)
        session.panel.sync_selection()
        self.app.processEvents()
        self.assertEqual(len(session.ik_spans),3)
        self.assertEqual(session.ik_spans[1]['start'],left['end'])
        self.assertEqual(session.ik_spans[1]['end'],right['start'])
        self.assertEqual(view._timeline_curve_selected,{(0,1),(0,3)})
        self.assertLessEqual(session.panel.height(),height_before)
        self.assertEqual(session.panel.status.text(),'IK ranges joined. Draft saved.')
        self.assertFalse(button.isEnabled())
        self.assertTrue(session.undo())
        self.assertTrue(button.isEnabled())
        self.assertTrue(session.redo())
        self.assertFalse(button.isEnabled())
        self.assertLess(session.panel.geometry().bottom(),view._timeline_panel.geometry().top())

    def test_correction_panel_grows_upward_for_messages_without_a_scrollbar(self):
        view = self.view
        view.resize(1200,1200)
        self.app.processEvents()
        panel, timeline = self.session.panel,view._timeline_panel
        initial_top = panel.y()
        original_clip = self.session.draft
        with patch.object(self.session,'rebuild') as rebuild:
            view._timeline_resize_panel(timeline.height()+180)
            self.app.processEvents()
            self.assertLess(panel.y(),initial_top)
            self.assertLess(panel.geometry().bottom(),timeline.y())
            height,bottom,top = panel.height(),panel.geometry().bottom(),panel.y()
            panel.status.setText('BVH export requires fixed offsets on every non-root joint. '
                                 'Select the corrected take and review its joint position keys before exporting.')
            self.app.processEvents()
            self.assertGreater(panel.height(),height)
            self.assertLess(panel.y(),top)
            self.assertEqual(panel.geometry().bottom(),bottom)
            self.assertLess(panel.geometry().bottom(),timeline.y())
            self.assertFalse(panel.findChildren(QtWidgets.QScrollArea))
            bounds = panel.status.fontMetrics().boundingRect(
                QtCore.QRect(0,0,panel.status.width(),10000),QtCore.Qt.TextWordWrap,panel.status.text())
            self.assertLessEqual(bounds.height(),panel.status.height())
            for button in panel.findChildren(QtWidgets.QPushButton):
                self.assertTrue(panel.rect().contains(QtCore.QRect(button.mapTo(panel,QtCore.QPoint()),button.size())))
            view._timeline_set_detached(True)
            self.app.processEvents()
            self.assertGreater(panel.geometry().bottom(),timeline.y())
            self.assertFalse(panel.findChildren(QtWidgets.QScrollArea))
            view._timeline_set_detached(False)
            self.app.processEvents()
            self.assertLess(panel.geometry().bottom(),timeline.y())
            view._timeline_resize_panel(300)
            self.app.processEvents()
            self.assertFalse(panel.findChildren(QtWidgets.QScrollArea))
            bottom = panel.geometry().bottom()
            view.set_timeline_visible(False)
            self.app.processEvents()
            self.assertGreater(panel.geometry().bottom(),bottom)
            self.assertTrue(panel.isVisible())
            rebuild.assert_not_called()
        self.assertIs(self.session.draft,original_clip)

    def test_scrub_and_frame_keep_joints_and_guides_on_the_current_pose(self):
        for frame in (0, 4, 1, 3, 2):
            self.paint_frame(frame)
            handles = self.view._mgl_scene_skeleton_handles_by_owner['Correction']
            wrist = next(handle for handle in handles if handle['name'] == 'left_wrist')
            self.view._mgl_scene_skeleton_handle_click(wrist)
            self.assertTrue(self.view._frame_selected_retarget_joint())
            self.view.repaint()
            self.app.processEvents()
            pose = evaluate_rig_at_time(self.session.skeleton, self.session.draft,
                                        self.session.time(), loop=False)
            expected = np.asarray(pose.global_matrices).reshape(-1, 4, 4)[:, :3, 3]
            handles = self.view._mgl_scene_skeleton_handles_by_owner['Correction']
            np.testing.assert_allclose([handle['position'] for handle in handles], expected,
                                       atol=1e-6)
            from echograph.ui.priormdm_rig_overlay import rig_guide_shapes
            data = self.view._mgl_scene_skeleton_qt_overlay_data()
            rig_guide_shapes(self.view, data)
            matrices = self.session.guide['_posed_shapes'][0][0]
            indices = self.session.guide['joint_indices']
            np.testing.assert_allclose(np.frombuffer(matrices).reshape(-1, 4, 4)[:, :3, 3],
                                       expected[indices], atol=1e-6)

    def test_gizmo_vertex_layout_survives_scrubbing_framing_and_stats(self):
        self.view._mgl_gizmo_tips_test_enabled = True
        self.view._set_xform_gizmo_mode('translate')
        for frame in (0, 4, 1, 3, 0):
            self.paint_frame(frame)
            handles = self.view._mgl_scene_skeleton_handles_by_owner['Correction']
            self.view._mgl_scene_skeleton_handle_click(
                next(handle for handle in handles if handle['name'] == 'left_wrist'))
            self.view._frame_selected_retarget_joint()
            self.view._debug_overlay = True
            self.view.repaint()
            self.app.processEvents()
            self.view.makeCurrent()
            vao = self.view._mgl_gizmo_tips_test_vao
            vbo = self.view._mgl_gizmo_tips_test_vbo
            gl = self.view.context().extraFunctions()
            gl.glBindVertexArray(vao.glo)
            get_attribute = ctypes.WINFUNCTYPE(
                None, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int))(
                    self.view.context().getProcAddress(b'glGetVertexAttribiv'))
            try:
                for index in (0, 1):
                    buffer = ctypes.c_int()
                    get_attribute(index, 0x889F, ctypes.byref(buffer))
                    self.assertEqual(buffer.value, vbo.glo,
                                     'Qt overlay overwrote the persistent gizmo vertex layout')
            finally:
                gl.glBindVertexArray(0)
                self.view.doneCurrent()

    def test_frame_joint_before_the_pending_repaint_uses_the_scrubbed_pose(self):
        self.paint_frame(0)
        self.session.select_joint('left_wrist')
        self.view._timeline_frame_spin.setValue(4)
        token = self.view._mgl_scene_skeleton_joint_owner_token('Correction', 'left_wrist')
        pose = evaluate_rig_at_time(self.session.skeleton, self.session.draft,
                                    self.session.time(), loop=False)
        index = self.session.skeleton.joint_names.index('left_wrist')
        expected = np.asarray(pose.global_matrices).reshape(-1, 4, 4)[index, :3, 3]
        with patch.object(self.view, '_mgl_scene_skeleton_rebuild_handle_mesh_for_owner') as upload:
            np.testing.assert_allclose(self.view._mgl_scene_skeleton_joint_world_position(token),
                                       expected, atol=1e-6)
            self.session.set_ik(False)
            self.session.select_joint('left_wrist')
            upload.assert_not_called()
        self.view.repaint()
        self.app.processEvents()
        self.assertFalse(list(self.view._mgl_scene.iter_by_tag('scene-skeleton-handles')))

    def test_selected_transform_gizmo_follows_the_joint_while_scrubbing(self):
        self.paint_frame(0)
        self.session.select_joint('left_wrist')
        for frame in (4, 1, 3):
            self.paint_frame(frame)
            pose = evaluate_rig_at_time(self.session.skeleton, self.session.draft,
                                        self.session.time(), loop=False)
            index = self.session.skeleton.joint_names.index('left_wrist')
            expected = np.asarray(pose.global_matrices).reshape(-1,4,4)[index,:3,3]
            np.testing.assert_allclose(self.view._xform_gizmo_pos, expected, atol=1e-6)

    def test_manual_ik_pairing_and_native_marker_layer(self):
        from echograph.motion.animation_corrections import limb_chain
        from echograph.ui.gl_view_timeline_model import TimelineCorrectionKeys, TimelineKeyMarker
        self.session.select_joint('left_wrist')
        for frame in (1,3):
            self.paint_frame(frame)
            pose = evaluate_rig_at_time(self.session.skeleton,self.session.draft,
                                        self.session.time(),loop=False)
            positions = np.asarray(pose.global_matrices).reshape(-1,4,4)[:,:3,3]
            start,_,end = limb_chain(self.session.skeleton,'left_wrist')
            target = positions[start]+.8*(positions[end]-positions[start])+[0,0,.02]
            self.session.move('left_wrist',target)
            self.session.commit()
            self.assertEqual(len(self.session.ik_spans),0 if frame==1 else 1)
            if frame==3:
                self.assertNotIn('end',self.session.ik_spans[0])
            self.view._timeline_on_set_key_clicked()
        self.assertEqual([pair[:2] for pair in self.view._timeline_ik_ranges()],[(1.,3.)])
        self.paint_frame(4)
        self.view._timeline_on_set_key_clicked()  # Leave a new Start IK unfinished.
        pending = next(span for span in self.session.ik_spans if 'end' not in span)
        self.paint_frame(2)
        self.view._timeline_on_set_key_clicked()
        self.assertEqual(len(self.session.ik_spans),3)
        self.assertIn(pending,self.session.ik_spans)
        self.assertTrue(self.view._timeline_key_is_ik(2,0))
        self.assertEqual(self.view._timeline_ik_boundary_label(2,0),'')
        self.view.repaint()
        self.app.processEvents()
        layers = [layer for layer in self.view.findChildren(TimelineCorrectionKeys) if layer.isVisible()]
        self.assertEqual(len(layers),1)
        self.assertFalse([marker for marker in self.view.findChildren(TimelineKeyMarker) if marker.isVisible()])

    def test_pelvis_and_chest_handles_select_and_follow_the_current_pose(self):
        for frame in (1,3):
            self.paint_frame(frame)
            for name in ('pelvis','spine3'):
                handles = self.view._mgl_scene_skeleton_handles_by_owner['Correction']
                handle = next(row for row in handles if row['name'] == name)
                self.assertTrue(handle.get('ik_control'))
                self.view._mgl_scene_skeleton_handle_click(handle)
                self.assertEqual(self.session.selected,name)
                self.assertEqual(self.view._xform_gizmo_mode,'translate')
                self.view.repaint()
                self.app.processEvents()
                np.testing.assert_allclose(self.view._xform_gizmo_pos,handle['position'],atol=1e-6)
        self.session.panel.ik.setChecked(False)
        self.view.repaint()
        self.app.processEvents()
        self.assertFalse(any(row.get('ik_control') for row in self.view._mgl_scene_skeleton_handles_by_owner['Correction']))
        self.assertEqual(self.session.pole_handles,[])

    def test_dragged_ik_in_and_out_curve_handles_stay_after_release_and_refresh(self):
        from echograph.motion.animation_corrections import limb_chain
        for frame,mode in ((1,'start'),(3,'end')):
            self.paint_frame(frame)
            pose = evaluate_rig_at_time(self.session.skeleton,self.session.draft,self.session.time(),loop=False)
            positions = np.asarray(pose.global_matrices).reshape(-1,4,4)[:,:3,3]
            start,_,end = limb_chain(self.session.skeleton,'left_wrist')
            self.session.move('left_wrist',positions[start]+.8*(positions[end]-positions[start])+[0,0,.02])
            self.session.commit()
            self.assertTrue(self.session.key_ik('left_wrist',mode=mode))
        self.session.select_joint('left_wrist')
        self.view._timeline_curve_axes_filter = {0}
        self.view._timeline_fit_curve_values()
        self.app.processEvents()
        canvas = self.view._timeline_curves_canvas
        for frame,side in ((1,'out'),(3,'in')):
            with self.subTest(frame=frame,side=side):
                self.view._timeline_curve_selected = {(0,frame)}
                self.view._timeline_update_handle_mode_buttons()
                self.view._timeline_handle_untied_btn.click()
                handles = canvas._selected_handle_points(canvas._axis_points())
                handle = next(handle for handle in handles if handle['frame'] == frame)
                point = QtCore.QPointF(*handle[side])
                rect = canvas._graph_rect()
                target = QtCore.QPointF(point.x()+(16 if side == 'out' else -16),
                    rect.top()-48. if side == 'out' else rect.bottom()+48.)
                before = self.view._timeline_axis_handles_for_key(0,frame)
                canvas.mousePressEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress,point,point,
                    QtCore.Qt.LeftButton,QtCore.Qt.LeftButton,QtCore.Qt.NoModifier))
                self.assertIsNotNone(canvas._handle_drag)
                canvas.mouseMoveEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseMove,target,target,
                    QtCore.Qt.NoButton,QtCore.Qt.LeftButton,QtCore.Qt.NoModifier))
                expected = self.view._timeline_axis_handles_for_key(0,frame)
                self.assertNotEqual(before[side],expected[side])
                handle_value = handle['value']+expected[side][1]
                if side == 'out':
                    self.assertGreater(handle_value,self.view._timeline_curve_max)
                else:
                    self.assertLess(handle_value,self.view._timeline_curve_min)
                other = 'in' if side == 'out' else 'out'
                self.assertEqual(expected['mode'],'untied')
                self.assertEqual(before[other],expected[other])
                canvas.mouseReleaseEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease,target,target,
                    QtCore.Qt.LeftButton,QtCore.Qt.NoButton,QtCore.Qt.NoModifier))
                self.view._timeline_load_from_disk(apply_current_frame=False)
                self.app.processEvents()
                after = self.view._timeline_axis_handles_for_key(0,frame)
                np.testing.assert_allclose(after['in'],expected['in'])
                np.testing.assert_allclose(after['out'],expected['out'])
                self.assertEqual(after['mode'],'untied')
                self.assertEqual(after.get('weighted'),expected.get('weighted'))

    def test_graph_navigation_shortcuts_never_reach_the_3d_camera(self):
        from echograph.ui import hotkeys_config
        view = self.view
        view._timeline_set_curves_mode(True)
        view._timeline_in_frame,view._timeline_out_frame = 0,40
        view._timeline_curve_axes_filter = {0}
        view._timeline_keys = {frame:dict(xyz=[value,0.,0.],axis_mask=[True,False,False,False,False,False])
                               for frame,value in ((5,-10.),(15,20.))}
        view._timeline_curve_selected = {(0,5),(0,15)}
        canvas = view._timeline_curves_canvas
        camera_events = []
        class CameraEvents(QtCore.QObject):
            def eventFilter(self, obj, event):
                if event.type() in (QtCore.QEvent.MouseButtonPress,QtCore.QEvent.MouseMove,
                                    QtCore.QEvent.MouseButtonRelease):
                    camera_events.append(event.type())
                return False
        tracker = CameraEvents(view)
        view.installEventFilter(tracker)
        camera_center = np.asarray(view._mgl_center).copy()
        camera_rotation = np.asarray(view._mgl_arcball.Transform).copy()
        camera_zoom = view._mgl_camera_zoom
        with patch.object(hotkeys_config,'_KEYMAP_CACHE',dict(hotkeys_config.DEFAULT_KEYMAP)), \
                patch.object(view,'_on_frame_clicked') as frame_camera, \
                patch.object(view,'_timeline_range_save_to_disk'):
            for detached in (False,True):
                with self.subTest(detached=detached):
                    view._timeline_set_detached(detached)
                    view._timeline_on_display_range_changed(10,30)
                    view._timeline_set_curve_range(-5.,5.)
                    self.app.processEvents()
                    point = QtCore.QPointF(canvas.rect().center())
                    # Deliver through Qt so ignored events really propagate to parents.
                    for widget in (canvas,view._timeline_panel,view._timeline_axis_labels[0]):
                        for kind,position,button,buttons in (
                            (QtCore.QEvent.MouseButtonPress,point,QtCore.Qt.MiddleButton,QtCore.Qt.MiddleButton),
                            (QtCore.QEvent.MouseMove,point+QtCore.QPointF(-80,30),QtCore.Qt.NoButton,QtCore.Qt.MiddleButton),
                            (QtCore.QEvent.MouseButtonRelease,point+QtCore.QPointF(-80,30),QtCore.Qt.MiddleButton,QtCore.Qt.NoButton)):
                            event = QtGui.QMouseEvent(kind,position,widget.mapToGlobal(position.toPoint()),
                                                     button,buttons,QtCore.Qt.AltModifier)
                            QtWidgets.QApplication.sendEvent(widget,event)
                            if widget is canvas:
                                self.assertTrue(event.isAccepted())
                    self.assertGreater(view._timeline_view_start,10)
                    self.assertEqual(view._timeline_view_span,20)
                    self.assertGreater(view._timeline_curve_min,-5.)
                    for kind,position,button,buttons in (
                        (QtCore.QEvent.MouseButtonPress,point,QtCore.Qt.RightButton,QtCore.Qt.RightButton),
                        (QtCore.QEvent.MouseMove,point+QtCore.QPointF(80,30),QtCore.Qt.NoButton,QtCore.Qt.RightButton),
                        (QtCore.QEvent.MouseButtonRelease,point+QtCore.QPointF(80,30),QtCore.Qt.RightButton,QtCore.Qt.NoButton)):
                        event = QtGui.QMouseEvent(kind,position,canvas.mapToGlobal(position.toPoint()),
                                                 button,buttons,QtCore.Qt.AltModifier)
                        QtWidgets.QApplication.sendEvent(canvas,event)
                        self.assertTrue(event.isAccepted())
                    self.assertLess(view._timeline_view_span,20)
                    self.assertGreater(view._timeline_curve_max-view._timeline_curve_min,10.)
                    QtTest.QTest.keyClick(canvas,QtCore.Qt.Key_F)
                    self.assertLessEqual(view._timeline_view_start,5)
                    self.assertGreaterEqual(view._timeline_view_start+view._timeline_view_span,15)
                    self.assertLess(view._timeline_curve_min,-10.)
                    self.assertGreater(view._timeline_curve_max,20.)
            frame_camera.assert_not_called()
        self.assertEqual(camera_events,[])
        np.testing.assert_array_equal(view._mgl_center,camera_center)
        np.testing.assert_array_equal(view._mgl_arcball.Transform,camera_rotation)
        self.assertEqual(view._mgl_camera_zoom,camera_zoom)

    def test_fk_joint_clicks_work_with_mesh_selection_modes_still_enabled(self):
        self.session.set_ik(False)
        self.paint_frame(1)
        data = self.view._mgl_scene_skeleton_qt_overlay_data()
        point = next(point for point in data['points'] if point and point['name'] == 'left_elbow')
        cursor = QtCore.QPointF(point['x'], point['y'])
        for mode in ('object', 'point', 'edge', 'face'):
            with self.subTest(mode=mode):
                self.session.select_joint('pelvis')
                self.view._mesh_select_mode = mode
                self.view._mesh_box_select_enabled = True
                press = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, cursor, cursor,
                    QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                self.view.mousePressEvent(press)
                self.view.mouseReleaseEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, cursor, cursor,
                    QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
                self.assertEqual(self.session.selected, 'left_elbow')
                self.assertEqual(self.view._xform_gizmo_mode, 'rotate')
                self.assertTrue(self.view._mgl_gizmo_visible)
                self.assertIsNone(getattr(self.view, '_mesh_box_select_drag', None))

    def test_bent_fingers_share_the_animation_pose_with_bones_handles_and_colliders(self):
        from echograph.motion.hands import add_preview_hands, FINGER_NAMES
        from echograph.ui.priormdm_rig_overlay import rig_guide_shapes
        self.session.finish()
        self.app.processEvents()
        skeleton = add_preview_hands(self.context['skeleton'])
        fingers = [JointAnimationTrack(name, rotation_keys=[QuatKeyframe(i/20., tuple(
            Rotation.from_euler('z', (25+i*7)*(1 if name.startswith('left') else -1),
                                degrees=True).as_quat())) for i in range(5)]) for name in FINGER_NAMES]
        clip = replace(self.context['clip'], tracks=self.context['clip'].tracks+fingers)
        context = dict(self.context, skeleton=skeleton, clip=clip)
        self.session = CorrectionSession(self.session.item, context, self.view)
        self.session.open(SimpleNamespace(open_scene_assets=self.view.load_scene_assets))
        renderer = self.session.renderer
        for frame in (0, 4, 2):
            with self.subTest(frame=frame):
                renderer._mgl_scene_skeleton_invalidate_owner('Correction')
                with patch.object(renderer, '_mgl_add_wire_item_from_points',
                                  wraps=renderer._mgl_add_wire_item_from_points) as wire:
                    self.paint_frame(frame)
                pose = evaluate_rig_at_time(skeleton, self.session.draft, self.session.time(), loop=False)
                positions = np.asarray(pose.global_matrices).reshape(-1, 4, 4)[:, :3, 3]
                expected_lines = np.asarray([positions[j] for i, joint in enumerate(skeleton.joints)
                    if joint.parent_index >= 0 for j in (joint.parent_index, i)])
                bone_calls = [call for call in wire.call_args_list if call.kwargs.get('tag') == 'scene-rig-joints']
                self.assertTrue(bone_calls)
                for call in bone_calls:
                    np.testing.assert_allclose(call.kwargs['line_points'], expected_lines, atol=2e-6)
                handles = renderer._mgl_scene_skeleton_handles_by_owner['Correction']
                np.testing.assert_allclose([handle['position'] for handle in handles], positions, atol=2e-6)
                rig_guide_shapes(renderer, renderer._mgl_scene_skeleton_qt_overlay_data())
                guide_matrices = np.frombuffer(self.session.guide['_posed_shapes'][0][0]).reshape(-1, 4, 4)
                np.testing.assert_allclose(guide_matrices[:, :3, 3],
                    positions[self.session.guide['joint_indices']], atol=2e-6)
        # Arm IK changes its chain while preserving the local finger motion on
        # both hands, including the hand being moved.
        from echograph.motion.animation_corrections import limb_chain
        start,_,end = limb_chain(skeleton,'left_wrist')
        self.session.move('left_wrist',positions[start]+.8*(positions[end]-positions[start]))
        after = evaluate_rig_at_time(skeleton,self.session.draft,self.session.time(),loop=False)
        for name in FINGER_NAMES:
            index = skeleton.joint_names.index(name)
            np.testing.assert_allclose(Rotation.from_quat(after.local_transforms[index].rotation).as_matrix(),
                Rotation.from_quat(pose.local_transforms[index].rotation).as_matrix(),atol=2e-6)

    def test_ik_boxes_follow_animated_joint_axes_and_pole_gizmo_edits_the_bend(self):
        renderer = self.session.renderer
        for frame in (0,3,1):
            renderer._mgl_scene_skeleton_invalidate_owner('Correction')
            with patch.object(renderer,'_mgl_add_wire_item_from_points',wraps=renderer._mgl_add_wire_item_from_points) as wire:
                self.paint_frame(frame)
            pose = evaluate_rig_at_time(self.session.skeleton,self.session.draft,self.session.time(),loop=False)
            matrices = np.asarray(pose.global_matrices).reshape(-1,4,4)
            controls = [handle for handle in renderer._mgl_scene_skeleton_handles_by_owner['Correction'] if handle.get('ik_control')]
            for handle in controls:
                expected = matrices[handle['index'],:3,:3].T
                expected /= np.linalg.norm(expected,axis=1,keepdims=True)
                np.testing.assert_allclose(handle['axes'],expected,atol=2e-6)
                curves = next(call.kwargs['line_points'] for call in wire.call_args_list
                              if call.kwargs['name'] == 'Correction '+handle['name']+' IK cube')
                local = (curves-handle['position'])@np.linalg.inv(expected)
                np.testing.assert_allclose(np.abs(local),handle['control_radius'],atol=2e-6)
            self.assertEqual(len(self.session.pole_handles),5)
        from echograph.motion.animation_corrections import limb_chain
        from echograph.ui.gl_view_mouse import _handle_mouse_move_moderngl_xform_translate_apply_owner
        start,_,end = limb_chain(self.session.skeleton,'left_wrist')
        positions = matrices[:,:3,3]
        target = positions[start]+.8*(positions[end]-positions[start])
        self.session.move('left_wrist',target)
        self.session.commit()
        self.view.repaint()
        self.app.processEvents()
        pole = next(handle for handle in self.session.pole_handles if handle['name'] == 'left_wrist')
        renderer._mgl_scene_skeleton_handle_click(pole)
        self.session.select_pole(pole)
        self.view.repaint()
        self.app.processEvents()
        np.testing.assert_allclose(self.view._xform_gizmo_pos,pole['position'],atol=1e-6)
        moved = np.asarray(pole['position'])+[0,0,.15]
        token = renderer._mgl_scene_skeleton_joint_owner_token('Correction','left_wrist')
        _handle_mouse_move_moderngl_xform_translate_apply_owner(self.view,moved,token,renderer)
        self.session.commit()
        pose = evaluate_rig_at_time(self.session.skeleton,self.session.draft,self.session.time(),loop=False)
        np.testing.assert_allclose(np.asarray(pose.global_matrices).reshape(-1,4,4)[end,:3,3],target,atol=1e-6)
        np.testing.assert_allclose(renderer._mgl_scene_skeleton_joint_world_position(token),moved,atol=1e-6)
        self.view._timeline_on_set_key_clicked()
        np.testing.assert_allclose(self.session.ik_spans[0]['start']['pole'],moved,atol=1e-6)

    def test_range_bar_pan_and_handles_release_without_saving_or_reloading_motion(self):
        view = self.view
        self.assertTrue(self.session.save())
        view._timeline_in_frame = 0
        view._timeline_out_frame = 240
        view._timeline_on_display_range_changed(24,96)
        bar = view._timeline_range_controls.bar
        draft = self.session.draft
        frame = view._timeline_current_frame()
        with patch.object(self.session,'save',wraps=self.session.save) as full_save, \
                patch.object(self.session,'document',wraps=self.session.document) as serialize:
            for part in ('pan','start','end'):
                with self.subTest(part=part):
                    left,right = bar.handle_positions()
                    x = (left+right)/2 if part == 'pan' else (left if part == 'start' else right)
                    point = QtCore.QPoint(round(x),bar.height()//2)
                    moved = point+QtCore.QPoint(20,0)
                    QtTest.QTest.mousePress(bar,QtCore.Qt.LeftButton,pos=point)
                    QtTest.QTest.mouseMove(bar,moved)
                    QtTest.QTest.mouseRelease(bar,QtCore.Qt.LeftButton,pos=moved)
                    self.assertEqual(self.session.timeline_range,self.session._saved_timeline_range)
                    self.assertEqual(self.session.timeline_range['view_start'],view._timeline_view_start)
                    self.assertEqual(self.session.timeline_range['view_end'],view._timeline_view_start+view._timeline_view_span)
            full_save.assert_not_called()
            serialize.assert_not_called()
        self.assertIs(self.session.draft,draft)
        self.assertEqual(view._timeline_current_frame(),frame)

    def test_dragging_a_single_end_triangle_in_timeline_rows_keeps_its_ik_range(self):
        view = self.view
        self.session.select_joint('left_wrist')
        view._timeline_curve_axes_filter = {1}
        for frame,mode in ((1,'start'),(3,'end')):
            self.paint_frame(frame)
            self.assertTrue(self.session.key_ik('left_wrist',mode=mode))
        end = dict(self.session.ik_spans[0]['end'])
        view._timeline_curves_mode = False
        view._timeline_view_start = 0
        view._timeline_slider_to_tracks_x = lambda local:40+50*local
        view._timeline_local_frame_to_tracks_x_float = lambda frame,clamp=False:40+50*frame
        view._timeline_tracks_x_to_slider_value = lambda x:round((x-40)/50)
        host = view._timeline_rows_host
        view._timeline_tracks_stack.setCurrentWidget(host)
        view._timeline_update_key_markers()
        _,_,x,y,_ = next(point for point in view._timeline_visible_row_key_points()
                         if point[0] == 1 and point[1] == 3)
        start = QtCore.QPointF(x,y)
        destination = QtCore.QPointF(240,y)
        host.mousePressEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress,start,start,
            QtCore.Qt.LeftButton,QtCore.Qt.LeftButton,QtCore.Qt.NoModifier))
        self.assertFalse(host._drag['multi'])
        host.mouseMoveEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseMove,destination,destination,
            QtCore.Qt.NoButton,QtCore.Qt.LeftButton,QtCore.Qt.NoModifier))
        self.assertEqual(view._timeline_keys[4]['ik_key'],end['id'])
        self.assertAlmostEqual(view._timeline_keys[4]['source_frame'],4.,msg=repr(view._timeline_owner_name))
        self.assertEqual([row[:2] for row in view._timeline_ik_ranges()],[(1.,4.)])
        host.mouseReleaseEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease,destination,destination,
            QtCore.Qt.LeftButton,QtCore.Qt.NoButton,QtCore.Qt.NoModifier))
        self.assertEqual(self.session.ik_spans[0]['end']['id'],end['id'])
        self.assertEqual(self.session.ik_spans[0]['end']['target'],end['target'])
        self.assertEqual(self.session.ik_spans[0]['position_mask'],[False,True,False])
        self.assertTrue(view._timeline_key_is_ik(4,1),self.session.panel.status.text())
        self.assertEqual([row[:2] for row in view._timeline_ik_ranges()],[(1.,4.)])

    def test_drawn_y_arm_keeps_the_ik_control_when_a_neighbor_overlaps_and_hover_is_cleared(self):
        from echograph.ui import gl_view_mouse
        renderer = self.session.renderer
        self.paint_frame(1)
        handles = renderer._mgl_scene_skeleton_handles_by_owner['Correction']
        wrist = next(handle for handle in handles if handle['name'] == 'left_wrist')
        renderer._mgl_scene_skeleton_handle_click(wrist)
        self.view.repaint()
        self.app.processEvents()
        center = self.view._xform_overlay_center_px
        tip = self.view._xform_overlay_axis_proj['y']
        cursor = center+(tip-center)*.5
        self.view._xform_mouse_px = cursor
        self.view.repaint()
        self.app.processEvents()
        self.assertEqual(self.view._xform_hover_axis,'y')
        owner = self.view._xform_gizmo_owner
        press = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress,cursor,cursor,
            QtCore.Qt.LeftButton,QtCore.Qt.LeftButton,QtCore.Qt.NoModifier)
        origin,direction = self.session.ray(press)
        distance = np.dot(np.asarray(wrist['position'])-origin,direction)
        neighbor = dict(next(handle for handle in handles if handle['name'] == 'right_wrist'),
                        position=tuple(origin+direction*distance),control_radius=.04,axes=np.eye(3))
        renderer._mgl_scene_skeleton_handles_by_owner['Correction'] = [
            neighbor if handle['name'] == 'right_wrist' else handle for handle in handles]
        with patch.object(renderer,'_mgl_scene_skeleton_handle_click',return_value=True) as picked, \
                patch.object(self.session,'start_drag',return_value=True):
            self.assertTrue(gl_view_mouse._handle_animation_correction_control_press(self.view,press,False))
        self.assertEqual(picked.call_args.args[0]['name'],'right_wrist')
        # A press-time repaint can clear hover even though this is the arm the
        # user just saw highlighted. Its painted geometry must remain pickable.
        self.view._xform_hover_axis = None
        self.view._xform_hover_center = False
        with patch.object(gl_view_mouse,'_handle_animation_correction_control_press',
                          wraps=gl_view_mouse._handle_animation_correction_control_press) as controls:
            self.view.mousePressEvent(press)
        controls.assert_not_called()
        self.assertTrue(self.view._xform_dragging)
        self.assertEqual(self.view._xform_drag_axis,'y')
        self.assertEqual(self.view._xform_gizmo_owner,owner)
        self.assertEqual(self.view._xform_gizmo_mode,'translate')
        moved = cursor+QtCore.QPointF(0,-18)
        self.view.mouseMoveEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseMove,moved,moved,
            QtCore.Qt.NoButton,QtCore.Qt.LeftButton,QtCore.Qt.NoModifier))
        self.view.mouseReleaseEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease,moved,moved,
            QtCore.Qt.LeftButton,QtCore.Qt.NoButton,QtCore.Qt.NoModifier))
        self.assertEqual(self.view._xform_gizmo_owner,owner)
        self.assertEqual(self.view._xform_gizmo_mode,'translate')
        pose = evaluate_rig_at_time(self.session.skeleton,self.session.draft,self.session.time(),loop=False)
        actual = np.asarray(pose.global_matrices[wrist['index']]).reshape(4,4)[:3,3]
        self.assertGreater(np.linalg.norm(actual-np.asarray(wrist['position'])),1e-4)

    def test_start_end_buttons_use_the_highlighted_timeline_position_channel(self):
        renderer = self.session.renderer
        self.paint_frame(1)
        handle = next(handle for handle in renderer._mgl_scene_skeleton_handles_by_owner['Correction']
                      if handle['name'] == 'left_wrist')
        renderer._mgl_scene_skeleton_handle_click(handle)
        self.view._timeline_on_axis_label_clicked(1)
        chain = self.session.skeleton.joint_names.index('left_wrist')
        for frame,button in ((1,0),(3,1)):
            self.paint_frame(frame)
            pose = evaluate_rig_at_time(self.session.skeleton,self.session.manual,self.session.time(),loop=False)
            positions = np.asarray(pose.global_matrices).reshape(-1,4,4)[:,:3,3]
            target = positions[chain].copy()
            target[1] -= .04
            self.session.move('left_wrist',target)
            self.session.commit()
            self.session.panel.ik_buttons[button].click()
        self.assertEqual(self.session.ik_spans[0]['position_mask'],[False,True,False])
        self.assertIn('end',self.session.ik_spans[0])
        span = self.session.ik_spans[0]
        rate = max(self.session.fps,self.session.manual.sample_rate_hz)*2
        times = np.arange(span['start']['time'],span['end']['time']+1e-8,1./rate)
        for time in times:
            original = evaluate_rig_at_time(self.session.skeleton,self.session.manual,time,loop=False)
            corrected = evaluate_rig_at_time(self.session.skeleton,self.session.draft,time,loop=False)
            expected = np.asarray(original.global_matrices[chain]).reshape(4,4)[:3,3]
            actual = np.asarray(corrected.global_matrices[chain]).reshape(4,4)[:3,3]
            np.testing.assert_allclose(actual[[0,2]],expected[[0,2]],atol=1e-6)

    def test_pelvis_start_end_buttons_allow_px_and_pz_inside_a_py_range(self):
        session,view = self.session,self.view
        self.assertTrue(session.select_joint('pelvis'))
        for axis,frames in ((1,(0,4)),(0,(1,3)),(2,(1,3))):
            QtTest.QTest.mouseClick(view._timeline_axis_labels[axis],QtCore.Qt.LeftButton)
            self.assertEqual(view._timeline_curve_axes_filter,{axis})
            for frame,button in zip(frames,session.panel.ik_buttons):
                self.paint_frame(frame)
                QtTest.QTest.mouseClick(button,QtCore.Qt.LeftButton)
                self.app.processEvents()
        self.assertEqual([span['position_mask'] for span in session.ik_spans],
                         [[False,True,False],[True,False,False],[False,False,True]])
        self.assertTrue(all('end' in span for span in session.ik_spans))
        view._timeline_set_curves_mode(True)
        for axis in (0,2):
            self.assertTrue(view._timeline_key_is_ik(1,axis))
            self.assertTrue(view._timeline_key_is_ik(3,axis))
            self.assertEqual(view._timeline_ik_boundary_label(1,axis),'S')
            self.assertEqual(view._timeline_ik_boundary_label(3,axis),'E')
        view._timeline_curves_canvas._set_selected_set({(0,1)})
        self.assertEqual(view._timeline_selected_ik_key_ids(),{session.ik_spans[1]['start']['id']})
        for frame in (0,1,2,3,4):
            self.paint_frame(frame)

    def test_all_axis_views_keep_drawn_and_pickable_gizmos_at_constant_size_during_zoom(self):
        self.session.select_joint('left_wrist')
        self.assertTrue(self.view._frame_selected_retarget_joint())
        self.view._xform_use_local = False
        self.view._mgl_gizmo_tips_test_enabled = True
        renderer = self.session.renderer
        expected = self.view._rot_shared.xyz_ring_radius_px()/self.view.devicePixelRatioF()
        for name,number in (('Front',3),('Back',3),('Left',4),('Right',4),('Top',2),('Bottom',2)):
            self.view._set_camera_view(number,view_name=name)
            for zoom in (.6,3.,12.):
                renderer._mgl_camera_zoom = zoom
                for mode in ('translate','scale','rotate'):
                    with self.subTest(view=name,zoom=zoom,mode=mode):
                        self.view._set_xform_gizmo_mode(mode)
                        self.view.repaint()
                        self.app.processEvents()
                        if mode == 'rotate':
                            shared = self.view._rot_shared
                            rect = self.view._rot_shared_viewport_rect
                            center = self.view._rot_shared_center_px
                            radii = []
                            for axes in ((0,1),(0,2),(1,2)):
                                for angle in np.linspace(0,2*np.pi,72,endpoint=False):
                                    point = np.zeros(3)
                                    point[list(axes)] = shared.gizmo_radius*np.array([np.cos(angle),np.sin(angle)])
                                    screen = shared.project_to_screen(int(rect.width()),int(rect.height()),
                                        self.view._rot_shared_mvp,QtGui.QVector3D(*point),rect.topLeft())
                                    radii.append(np.hypot(screen.x()-center.x(),screen.y()-center.y()))
                            self.assertAlmostEqual(max(radii),expected,delta=.5)
                        else:
                            center = self.view._xform_overlay_center_px
                            points = self.view._xform_overlay_axis_proj
                            size = max(np.hypot(point.x()-center.x(),point.y()-center.y())
                                       for point in points.values() if point is not None)
                            axis_end = .87 if mode == 'scale' else 1.
                            self.assertAlmostEqual(size,expected*axis_end/self.view._rot_shared.gizmo_radius,delta=.5)
                            mvp = self.view._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_pvtrs(
                                g=self.view._xform_gizmo_pos,dpr=self.view.devicePixelRatioF(),
                                P=renderer._mgl_pick_proj,V=renderer._mgl_pick_view,M=renderer._mgl_pick_model,
                                R=np.eye(4,dtype=np.float32),renderer=renderer)
                            rect = self.view._mgl_active_viewport_qrectf()
                            from echograph.ui.gl_view_math import project_local
                            origin = np.asarray(project_local((0,0,0),mvp,rect.width(),rect.height()))
                            pick_size = max(np.linalg.norm(np.asarray(project_local(axis*axis_end,mvp,
                                rect.width(),rect.height()))-origin) for axis in np.eye(3))
                            self.assertAlmostEqual(pick_size,size,delta=.5)

    def test_viewport_hover_and_deferred_refresh_keep_the_joint_key_editor(self):
        view = self.view
        view.gl_view = view
        view._active_scene_preview_context = dict(scene_name=view._timeline_scene_name)
        controller = TimelineController(view)
        for joint in ('left_wrist', 'left_elbow'):
            self.session.select_joint(joint)
            self.paint_frame(3)
            owner = view._timeline_owner_name
            view._timeline_on_display_range_changed(1, 3)
            view._timeline_drag_axis_key(3, 0, 1, 15., commit=True)
            view._timeline_curve_selected = {(3, 1)}
            self.assertTrue(view.timeline_delete_selected_keys())
            QtTest.QTest.mouseMove(view._timeline_range_controls.bar, QtCore.QPoint(30, 10))
            QtTest.QTest.mouseMove(view, QtCore.QPoint(60, 60))
            QtCore.QTimer.singleShot(0, controller.sync_timeline_context)
            self.app.processEvents()
            self.assertEqual(view._timeline_mode, 'owner_keys')
            self.assertEqual(view._timeline_owner_name, owner)
            self.assertEqual(view._timeline_view_start, 1)
            self.assertEqual(view._timeline_view_span, 2)
            self.assertEqual(view._timeline_current_frame(), 3)
        view._timeline_show_composition_mode()
        QtTest.QTest.mouseMove(view, QtCore.QPoint(80, 60))
        controller.sync_timeline_context()
        self.assertEqual(view._timeline_mode, 'composition')
        self.assertTrue(view._timeline_range_controls.isVisible())

    def test_limiter_preview_keeps_frame_1149_pose_after_selecting_correction_joints(self):
        self.session.finish()
        clip = self.context['clip']
        tracks = [replace(track,rotation_keys=[QuatKeyframe(0.,(0.,0.,0.,1.)),
                   QuatKeyframe(60.,tuple(Rotation.from_euler('z',90.,degrees=True).as_quat()))])
                  if track.joint_name=='left_shoulder' else track for track in clip.tracks]
        clip = replace(clip,tracks=tracks,end_time=60.,sample_rate_hz=120.,metadata=dict(clip.metadata,
                       rig_limiter=dict(source_sample_rate_hz=30.,sample_rate_hz=120.,repair_substeps=4)))
        context = dict(self.context,clip=clip)
        rig = dict(skeleton=context['skeleton'],clip=clip,clips=[clip],loop=False,
                   show_capture_joints=False,show_animated_joints=True,mesh_skinning_enabled=False)
        self.view._timeline_set_fps(30.,save=False)
        self.view.load_scene_assets([dict(node='Limiter',kind='rig_limiter',path=str(self.path),ext='.bvh',
                                         fbx_rig_context=rig,wire_only=True)])
        self.view._timeline_set_end_frame(1800,save=False)
        self.paint_frame(1149)
        expected_time = self.view._mgl_fbx_context_timeline_sample_seconds(rig,'Limiter')
        self.assertAlmostEqual(expected_time,1149/30.)
        expected = evaluate_rig_at_time(context['skeleton'],clip,expected_time,loop=False)
        positions = np.asarray(expected.global_matrices).reshape(-1,4,4)[:,:3,3]
        item = SimpleNamespace(model=GraphNode('Correction',kind='animation_correction'),scene=lambda:None)
        self.session = CorrectionSession(item,context,self.view)
        self.session.open(SimpleNamespace(open_scene_assets=self.view.load_scene_assets))
        self.assertEqual(self.view._timeline_current_frame(),1149)
        self.assertAlmostEqual(self.session.time(),expected_time)
        for joint in ('left_elbow','left_wrist','right_ankle','pelvis'):
            self.session.select_joint(joint)
            self.view.repaint()
            self.app.processEvents()
            self.assertEqual(self.view._timeline_fps,30.)
            self.assertEqual(self.view._timeline_current_frame(),1149)
            self.assertAlmostEqual(self.session.time(),expected_time)
            handles = self.view._mgl_scene_skeleton_handles_by_owner['Correction']
            np.testing.assert_allclose([handle['position'] for handle in handles],positions,atol=1e-6)

    def test_mocap_collection_f_frames_scrubbed_rig_without_joint_selection(self):
        from nodes.mocap_import.spec import _build_preview_asset
        self.session.finish()
        model = GraphNode('Collection', kind='mocap_collection')
        asset = _build_preview_asset(model, SimpleNamespace(resolved_path=str(self.path)))
        self.assertIsNotNone(asset)
        self.assertIsNot(self.view.load_scene_assets([asset], frame=False), False)
        context = asset['fbx_rig_context']
        self.view._timeline_set_fps(20., save=False)
        self.view._xform_gizmo_owner = None
        self.view._mgl_scene_skeleton_selected_joint = ''
        self.view._mgl_scene_skeleton_selected_joints = []
        self.view._mgl_retarget_selected_joint = None
        self.view._mgl_base_center = np.array([1000., 500., -500.], dtype=np.float32)
        self.view._mgl_base_zoom = 500.
        for frame, mode, size in ((0, 'perspective', (1000, 800)),
                                  (3, 'perspective', (640, 900)),
                                  (1, 'orthographic', (1000, 800))):
            self.view.resize(*size)
            self.view._mgl_projection = mode
            angle = 25. if frame == 3 else 0.
            self.view._mgl_arcball.Transform[:3, :3] = Rotation.from_euler('z', angle, degrees=True).as_matrix()
            self.view._timeline_frame_spin.setValue(frame)
            seconds = self.view._mgl_fbx_context_timeline_sample_seconds(context, 'Collection')
            pose = evaluate_rig_at_time(context['skeleton'], context['clip'], seconds, loop=True)
            positions = np.asarray(pose.global_matrices).reshape(-1, 4, 4)[:, :3, 3]
            positions, _ = self.view._mgl_scene_skeleton_display_positions('Collection', positions)
            event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_F, QtCore.Qt.NoModifier)
            self.assertTrue(self.view._handle_viewport_hotkeys(event, require_no_text_focus=False))
            self.assertLess(self.view._mgl_camera_zoom, 10.)
            self.view.repaint()
            self.app.processEvents()
            projection = self.view._mgl_pick_proj @ self.view._mgl_pick_view @ self.view._mgl_pick_model
            projected = np.column_stack((positions, np.ones(len(positions)))) @ projection.T
            self.assertTrue(np.all(projected[:, 3] > 0.))
            ndc = projected[:, :3]/projected[:, 3, None]
            pixels = np.column_stack(((ndc[:, 0]+1)*self.view.width()/2,
                                      (1-ndc[:, 1])*self.view.height()/2))
            self.assertGreater(pixels[:, 0].min(), 0.)
            self.assertLess(pixels[:, 0].max(), self.view.width())
            self.assertGreater(pixels[:, 1].min(), 0.)
            self.assertLess(pixels[:, 1].max(), self.view.height()-self.view._bottom_overlay_height())

    def test_switching_to_collection_preview_releases_the_correction_session(self):
        context = dict(skeleton=self.context['skeleton'], clip=self.context['clip'],
                       loop=False, show_capture_joints=False, show_animated_joints=True)
        self.view.load_scene_assets([dict(node='Collection', kind='mocap_import',
                                         path=str(self.path), ext='.bvh',
                                         fbx_rig_context=context, wire_only=True)])
        for frame in (0, 4, 1):
            self.paint_frame(frame)
        self.assertIsNone(getattr(self.view, '_animation_correction_session', None))
        self.assertNotEqual(self.view._mgl_scene_skeleton_active_owner, 'Correction')
        self.assertNotIn('_animation_correction_session', self.session.rig)


if __name__ == '__main__':
    unittest.main()
