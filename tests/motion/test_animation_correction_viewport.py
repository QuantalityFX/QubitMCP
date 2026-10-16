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
from echograph.rigging.fbx_canonical import QuatKeyframe
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
