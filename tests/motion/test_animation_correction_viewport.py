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
