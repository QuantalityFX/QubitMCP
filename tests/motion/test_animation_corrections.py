import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from scipy.spatial.transform import Rotation, Slerp

from echograph.model import GraphNode
from echograph.motion import animation_corrections as corrections
from echograph.motion.conversion import reconstruct_animation
from echograph.motion.rig_preview import build_guide, preview_offsets
from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
from echograph.rigging.fbx_canonical import AnimationClip, Joint, JointTransform, JointAnimationTrack, QuatKeyframe, Vec3Keyframe
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
from echograph.ui import gl_view_mouse
from echograph.ui.gl_mgl_renderer import MGLRendererMixin
from echograph.ui.gl_view import GraphGLView
from echograph.ui.gl_view_timeline_io import GraphGLTimelineIOMixin
from echograph.ui.gl_view_timeline_model import GraphGLTimelineModelMixin
from nodes.animation_correction import spec
from nodes.animation_correction.editor import CorrectionSession, CorrectionPanel, cube_curves, ik_control_radius
from nodes.mocap_collection import spec as collection
from nodes.rig_limiter.spec import export_context
from tests.motion.test_mocap_collection import Scene

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def fixture():
    offsets = preview_offsets('standardman')
    positions,_ = build_guide(offsets)
    skeleton,clip,_,_ = reconstruct_animation(np.repeat(positions[None],5,axis=0),20,{},rest_offsets=offsets)
    return skeleton,clip


def points(skeleton,clip,time):
    pose = evaluate_rig_at_time(skeleton,clip,time,loop=False)
    return np.asarray(pose.global_matrices).reshape(-1,4,4)[:,:3,3]


class Viewport(QtWidgets.QWidget,MGLRendererMixin,GraphGLTimelineIOMixin,GraphGLTimelineModelMixin):
    set_gizmo_visible = GraphGLView.set_gizmo_visible
    def __init__(self):
        super().__init__()
        self.resize(1000,800)
        self._mgl_ctx = None
        self.contexts = {}
        self.frame = 0
        self._timeline_fps = 20.
        self._timeline_keys = {}
        self._timeline_curve_selected = set()
        self._timeline_mode = 'owner_keys'
        self._timeline_enabled = True
        self._timeline_owner_name = ''
        self._mgl_scene_skeleton_active_owner = ''
        self._mgl_scene_skeleton_invalidate_owner = Mock()
        self._mgl_scene_skeleton_update_owner_handles = Mock()
        self._timeline_sync_range_controls = Mock()
        self._timeline_update_key_count_label = Mock()
        self._timeline_update_key_markers = Mock()
        self._timeline_refresh_coord_labels = Mock()
        self._timeline_sync_key_owner_from_outliner = Mock()
        self._timeline_active_locked_camera_owner = lambda: ''
        self._timeline_capture_camera_state = lambda: {}
        self._timeline_preview_context_active = lambda: True

    def _mgl_scene_owner_fbx_rig_context(self,owner):
        return next((context for key,context in self.contexts.items() if key.casefold()==str(owner).casefold()),None)
    def _mgl_timeline_time_seconds(self): return self.frame/self._timeline_fps
    def _mgl_timeline_frame_index(self): return self.frame
    def _mgl_timeline_fps_value(self): return self._timeline_fps
    def _timeline_current_frame(self): return self.frame
    def _timeline_set_fps(self,fps,**kwargs): self._timeline_fps = fps
    def _set_xform_gizmo_mode(self,mode): self._xform_gizmo_mode = mode
    def _bottom_overlay_height(self): return 120
    def _mgl_scene_skeleton_request_active(self,owner): self._mgl_scene_skeleton_active_owner = owner
    def _mgl_scene_skeleton_display_model_matrix(self,owner): return np.eye(4)
    def _timeline_current_cam_xyz(self):
        return self._mgl_scene_skeleton_joint_values(self._timeline_owner_name)[0]
    def _timeline_current_cam_rxyz(self):
        return self._mgl_scene_skeleton_joint_values(self._timeline_owner_name)[1]
    def set_timeline_scene_context(self,*,owner_name,**kwargs):
        self._timeline_owner_name = owner_name
        self._timeline_load_from_disk(apply_current_frame=False)


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(self.drain_events)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.skeleton,self.clip = fixture()
        self.paths = [Path(self.temp.name).resolve()/(name+'.bvh') for name in ('walk','turn')]
        for path in self.paths:
            export_context(dict(skeleton=self.skeleton,clip=self.clip),path)
        self.scene = Scene()
        self.addCleanup(self.scene.deleteLater)
        self.addCleanup(self.scene.clear)
        self.addCleanup(APP.processEvents)
        self.source = self.scene.add_node(GraphNode('Collection',kind='mocap_collection'),QtCore.QPointF())
        collection.add_paths(self.source.model,self.paths,self.scene)
        collection.save_state(self.source.model,collection.entries(self.source.model),1,self.scene)
        self.item = self.scene.add_node(GraphNode('Correction',kind='animation_correction'),QtCore.QPointF())
        self.scene.add_edge('Collection','Correction')
        self.scene._edges[-1].dst_port_name = 'animation'
        self.view = Viewport()
        self.addCleanup(self.view.deleteLater)
        self.window = SimpleNamespace(gl_view=self.view,open_scene_assets=self.open_assets)

    @staticmethod
    def drain_events():
        QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)
        APP.processEvents()

    def open_assets(self,assets,frame=True):
        self.view.contexts = {asset['node']:asset['fbx_rig_context'] for asset in assets}
        return True

    def context(self):
        errors = []
        context = spec.input_context(self.item,errors,[])
        self.assertEqual(errors,[])
        return context

    def session(self):
        session = CorrectionSession(self.item,self.context(),self.view)
        session.open(self.window)
        self.addCleanup(session.finish)
        return session

    def select(self,name):
        self.view.set_timeline_scene_context(owner_name=self.view._mgl_scene_skeleton_joint_owner_token('Correction',name))

    def test_keyframe_and_ik_buttons_autosave_without_refreshing_the_graph(self):
        session = self.session()
        self.select('left_wrist')
        session.select('left_wrist')
        with patch.object(self.scene,'set_node_params',wraps=self.scene.set_node_params) as graph, \
                patch.object(collection,'refresh_repair_records',wraps=collection.refresh_repair_records) as records, \
                patch.object(corrections,'save_document',wraps=corrections.save_document) as saved:
            self.view.frame = 1
            self.view._timeline_on_set_key_clicked()
            self.view.frame = 3
            self.view._timeline_on_set_key_clicked()
            session.panel.ik_buttons[1].click()
            self.assertEqual(saved.call_count,3)
            graph.assert_not_called()
            records.assert_not_called()
            self.assertTrue(session._graph_update_pending)
            self.assertFalse(session.dirty)
            document = corrections.read_document(session.path)
            self.assertEqual(document['ik_spans'],session.ik_spans)
            self.assertIn('end',document['ik_spans'][0])
            # The existing explicit Save Draft action still publishes changes.
            next(button for button in session.panel.findChildren(QtWidgets.QPushButton)
                 if button.text() == 'Save draft').click()
            self.assertTrue(graph.called)
            records.assert_called_once()
            self.assertFalse(session._graph_update_pending)

    def test_approval_publishes_once_after_the_completed_export_is_saved(self):
        session = self.session()
        self.make_ik_range(session)
        with patch.object(session,'_notify_saved_changes',wraps=session._notify_saved_changes) as publish:
            output = session.approve()
            self.assertTrue(output)
            publish.assert_called_once()
        self.assertTrue(output.is_file())
        self.assertFalse(session._graph_update_pending)
        self.assertEqual(corrections.read_document(session.path)['approved']['file'],output.name)
        self.assertTrue(collection.entries(self.source.model)[0]['approved'])

    def test_fk_keyframe_and_timeline_boundary_edits_also_save_without_graph_updates(self):
        session = self.session()
        session.set_ik(False)
        self.select('left_elbow')
        self.view.frame = 2
        with patch.object(self.scene,'set_node_params',wraps=self.scene.set_node_params) as graph, \
                patch.object(collection,'refresh_repair_records',wraps=collection.refresh_repair_records) as records:
            self.view._timeline_on_set_key_clicked()
            graph.assert_not_called()
            records.assert_not_called()
        self.assertTrue(corrections.read_document(session.path)['fk_keys'])
        session.set_ik(True)
        self.make_ik_range(session)
        self.select('left_wrist')
        with patch.object(self.scene,'set_node_params',wraps=self.scene.set_node_params) as graph, \
                patch.object(collection,'refresh_repair_records',wraps=collection.refresh_repair_records) as records:
            value = self.view._timeline_keys[3]['xyz'][0]
            self.view._timeline_drag_axis_key(0,3,4,value,commit=True)
            graph.assert_not_called()
            records.assert_not_called()
        self.assertAlmostEqual(corrections.read_document(session.path)['ik_spans'][0]['end']['time'],.2)

    def test_fk_set_key_respects_position_rotation_and_mixed_axis_selections(self):
        session = self.session()
        session.set_ik(False)
        self.select('left_wrist')
        self.view._timeline_fps = 40.
        for frame,axes in ((1,{0,1,2}),(3,{4}),(5,{0,5}),(7,set())):
            self.view.frame = frame
            self.view._timeline_curve_axes_filter = axes
            self.view._timeline_on_set_key_clicked()
            row = session.keys_map('left_wrist',fps=40.)[frame]
            expected = [axis in axes for axis in range(6)] if axes else [True]*6
            self.assertEqual(row['axis_mask'],expected)
            self.assertFalse(row.get('ik_key'))
            if axes == {0,1,2}:
                self.assertNotIn('left_wrist',session.fk_keys)
        document = corrections.read_document(session.path)
        self.assertEqual(document['timeline_rows']['left_wrist']['keys']['1']['axis_mask'],[True]*3+[False]*3)
        self.assertEqual(session.ik_spans,[])
        restored = self.session()
        self.assertEqual(restored.keys_map('left_wrist',fps=40.),session.keys_map('left_wrist',fps=40.))

    def test_fk_position_drag_moves_the_wrist_without_rotating_or_moving_its_parents_and_reopens(self):
        session = self.session()
        session.set_ik(False)
        self.select('left_wrist')
        self.view.frame = 2
        before = points(session.skeleton,session.draft,.1)
        index = session.skeleton.joint_names.index('left_wrist')
        chain = corrections.limb_chain(session.skeleton,'left_wrist')
        target = before[index]+[.01,.03,-.02]
        session.move('left_wrist',target)
        session.commit()
        after = points(session.skeleton,session.draft,.1)
        np.testing.assert_allclose(after[index],target,atol=1e-8)
        np.testing.assert_allclose(after[list(chain[:-1])],before[list(chain[:-1])],atol=1e-8)
        self.assertEqual(session.ik_spans,[])
        self.assertEqual(session.fk_keys,{})
        self.assertEqual(session.keys_map('left_wrist')[2]['fk_position_axes'],[True]*3)
        restored = self.session()
        np.testing.assert_allclose(points(restored.skeleton,restored.draft,.1),after,atol=1e-8)
        self.assertTrue(session.undo())
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),before,atol=1e-8)
        self.assertTrue(session.redo())
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),after,atol=1e-8)

    def test_moving_one_legacy_fk_rotation_axis_does_not_reinsert_it_or_key_other_axes(self):
        session = self.separated_ik_session()
        self.view._timeline_fps = 40.
        self.view.frame = 9  # Between source samples, outside both IK ranges.
        session.key_rotations({'left_shoulder':Rotation.from_euler('xyz',(12,23,34),degrees=True).as_quat()})
        session.commit()
        self.select('left_shoulder')
        before = deepcopy(session.document())
        spans = deepcopy(session.ik_spans)
        for source,destination in ((9,10),(10,11),(11,12),(12,13)):
            self.view._timeline_drag_axis_key(3,source,destination,12.,commit=False)
            self.view._timeline_drag_axis_key(3,destination,destination,12.,commit=True)
            rows = session.keys_map('left_shoulder',fps=40.)
            self.assertFalse(rows[9]['axis_mask'][3])
            self.assertEqual(rows[9]['fk_rotation_axes'],[False,True,True])
            self.assertEqual(rows[destination]['fk_rotation_axes'],[True,False,False])
            self.assertEqual(session.ik_spans,spans)
            for old in (11,):
                if old < destination:
                    self.assertNotIn(old,rows)
        rows = session.keys_map('left_shoulder',fps=40.)
        self.assertEqual(rows[13]['axis_mask'][3:],[True,False,False])
        restored = CorrectionSession(self.item,session.context,self.view)
        self.addCleanup(restored.finish)
        self.assertEqual(restored.keys_map('left_shoulder',fps=40.),rows)
        for time in (.225,.275,.325):
            np.testing.assert_allclose(points(session.skeleton,session.draft,time),
                                       points(restored.skeleton,restored.draft,time),atol=1e-8)
        for _ in range(4):
            self.assertTrue(session.undo())
        self.assertEqual(session.document(),before)
        self.assertTrue(session.redo())
        self.assertFalse(session.keys_map('left_shoulder',fps=40.)[9]['axis_mask'][3])

    def test_deleting_one_legacy_fk_rotation_axis_stays_deleted_after_refresh_and_reopen(self):
        session = self.session()
        self.view._timeline_fps = 40.
        self.view.frame = 3
        session.key_rotations({'left_shoulder':Rotation.from_euler('xyz',(12,23,34),degrees=True).as_quat()})
        session.commit()
        self.select('left_shoulder')
        self.view._timeline_curve_selected = {(4,3)}
        self.assertTrue(self.view.timeline_delete_selected_keys())
        for candidate in (session,self.session()):
            row = candidate.keys_map('left_shoulder',fps=40.)[3]
            self.assertEqual(row['axis_mask'][3:],[True,False,True])
            self.assertEqual(row['fk_rotation_axes'],[True,False,True])

    def test_sparse_fk_rotation_on_a_source_frame_preserves_other_ik_axes_when_moved_and_reopened(self):
        session = self.separated_ik_session()
        self.view._timeline_fps = 40.
        self.view.frame = 4
        name = 'left_shoulder'
        index = session.skeleton.joint_names.index(name)
        original = session.draft
        def angles(clip,time):
            return Rotation.from_quat(evaluate_rig_at_time(session.skeleton,clip,time,loop=False)
                                      .local_transforms[index].rotation).as_euler('xyz',degrees=True)
        desired = angles(original,.1)
        desired[0] += 5.
        session.key_fk(name,axes=[3],rotation=Rotation.from_euler('xyz',desired,degrees=True).as_quat(),save=True)
        np.testing.assert_allclose(angles(session.draft,.1),desired,atol=1e-7)
        self.select(name)
        self.assertFalse(self.view._timeline_key_is_ik(4,3))
        self.view._timeline_drag_axis_key(3,4,5,desired[0],commit=True)
        expected = angles(original,.125)
        expected[0] = desired[0]
        baked = corrections.apply_document(session.skeleton,session.base,session.document())
        restored = CorrectionSession(self.item,session.context,self.view)
        self.addCleanup(restored.finish)
        for clip in (session.draft,baked,restored.draft):
            np.testing.assert_allclose(angles(clip,.125),expected,atol=1e-7)
        for time in (0.,.049999,.05,.075,.1,.12,.125,.14,.15,.150001,.2,.4,.6):
            np.testing.assert_allclose(points(session.skeleton,session.draft,time),
                                       points(session.skeleton,baked,time),atol=1e-8)
        # A subsequent full gizmo rotation must replace the sparse ownership.
        self.view.frame = 5
        session.key_rotations({name:Rotation.from_euler('xyz',(7,8,9),degrees=True).as_quat()})
        session.commit()
        self.assertEqual(session.keys_map(name,fps=40.)[5]['fk_rotation_axes'],[True]*3)
        np.testing.assert_allclose(angles(session.draft,.125),(7,8,9),atol=1e-7)
        np.testing.assert_allclose(angles(corrections.apply_document(session.skeleton,session.base,session.document()),.125),
                                   (7,8,9),atol=1e-7)

    def test_moving_an_ik_boundary_does_not_copy_an_unselected_fk_rotation_key_at_the_same_frame(self):
        session = self.separated_ik_session()
        self.view.frame = 3
        session.key_fk('left_shoulder',axes=[3],rotation=Rotation.from_euler('xyz',(7,8,9),degrees=True).as_quat(),save=True)
        self.select('left_shoulder')
        self.assertFalse(self.view._timeline_key_is_ik(3,3))
        self.assertTrue(self.view._timeline_key_is_ik(3,4))
        original_fk = deepcopy(session.fk_keys)
        original_source = session.keys_map('left_shoulder',include_ik=False)
        self.view._timeline_curve_selected = {(4,3)}
        self.assertEqual(self.view._timeline_drag_selected_curve_keys(1,0.,commit=True),(1,0.))
        self.assertEqual(session.fk_keys.keys(),original_fk.keys())
        for joint,entries in original_fk.items():
            self.assertEqual(session.fk_keys[joint].keys(),entries.keys())
            for stamp,value in entries.items():
                np.testing.assert_allclose(session.fk_keys[joint][stamp],value,atol=1e-12)
        self.assertEqual(session.keys_map('left_shoulder',include_ik=False)[3]['axis_mask'],original_source[3]['axis_mask'])
        self.assertEqual(session.ik_spans[0]['end']['time'],.2)
        self.assertEqual(session.keys_map('left_shoulder')[3]['fk_rotation_axes'],[True,False,False])
        self.assertFalse(session.keys_map('left_shoulder')[4].get('fk_key'))

    def test_moving_multiple_fk_rotation_curves_preserves_sparse_channels_and_removes_empty_rows(self):
        session = self.separated_ik_session()
        self.view._timeline_fps = 40.
        self.view.frame = 9
        session.key_rotations({'left_shoulder':Rotation.from_euler('xyz',(12,23,34),degrees=True).as_quat()})
        session.commit()
        self.select('left_shoulder')
        self.view._timeline_curve_selected = {(3,9),(4,9)}
        self.assertEqual(self.view._timeline_drag_selected_curve_keys(2,0.,commit=True),(2,0.))
        rows = session.keys_map('left_shoulder',fps=40.)
        self.assertEqual(rows[9]['axis_mask'][3:],[False,False,True])
        self.assertEqual(rows[11]['axis_mask'][3:],[True,True,False])
        self.view._timeline_curve_selected = {(3,11),(4,11),(5,9)}
        self.assertEqual(self.view._timeline_drag_selected_keys(2,commit=True),2)
        rows = session.keys_map('left_shoulder',fps=40.)
        self.assertNotIn(9,rows)
        self.assertEqual(rows[11]['axis_mask'][3:],[False,False,True])
        self.assertEqual(rows[13]['axis_mask'][3:],[True,True,False])

    def test_deleting_a_full_fk_pose_reveals_the_ik_boundary_without_deleting_its_range(self):
        session = self.separated_ik_session()
        self.view.frame = 3
        session.key_rotations({'left_shoulder':Rotation.from_euler('xyz',(12,23,34),degrees=True).as_quat()})
        session.commit()
        self.select('left_shoulder')
        spans = deepcopy(session.ik_spans)
        self.assertTrue(all(not self.view._timeline_key_is_ik(3,axis) for axis in (3,4,5)))
        self.view._timeline_curve_selected = {(axis,3) for axis in (3,4,5)}
        self.assertTrue(self.view.timeline_delete_selected_keys())
        self.assertEqual(session.ik_spans,spans)
        self.assertNotIn('left_shoulder',session.fk_keys)
        self.assertTrue(all(self.view._timeline_key_is_ik(3,axis) for axis in (3,4,5)))

    def test_fk_position_overrides_ik_locally_without_deleting_the_range_and_matches_saved_evaluation(self):
        session = self.session()
        self.make_ik_range(session)
        spans = deepcopy(session.ik_spans)
        session.set_ik(False)
        self.view._timeline_fps = 40.
        self.view.frame = 3
        time = session.time()
        before = points(session.skeleton,session.draft,time)
        endpoint = session.skeleton.joint_names.index('left_wrist')
        target = before[endpoint]+[.02,.01,.03]
        session.move('left_wrist',target)
        session.commit()
        np.testing.assert_allclose(points(session.skeleton,session.draft,time)[endpoint],target,atol=1e-8)
        self.assertEqual(session.ik_spans,spans)
        self.assertEqual(session.fk_keys,{})
        row = session.keys_map('left_wrist',fps=40.)[3]
        self.assertEqual(row['axis_mask'],[True]*3+[False]*3)
        self.assertFalse(row.get('ik_key'))
        restored = corrections.apply_document(session.skeleton,session.base,session.document())
        for stamp in (.05,.075,.1,.125,.15):
            np.testing.assert_allclose(points(session.skeleton,session.draft,stamp),
                                       points(session.skeleton,restored,stamp),atol=1e-8)
        session.select('left_wrist')
        self.view._timeline_curve_axes_filter = {0,1,2}
        self.assertTrue(session.reload_joint())
        self.assertFalse(corrections.document_fk_positions(session.document(),session.base))

    def test_fk_rotation_drags_reuse_the_position_index_without_scanning_timeline_rows_again(self):
        session = self.session()
        self.make_ik_range(session)
        session.set_ik(False)
        self.view.frame = 2
        endpoint = session.skeleton.joint_names.index('left_wrist')
        target = points(session.skeleton,session.draft,.1)[endpoint]+[.01,.02,.03]
        session.move('left_wrist',target)
        session.commit()
        session.fk_positions()
        session.fk_rotation_masks()
        with patch.object(corrections,'document_fk_positions',side_effect=AssertionError('Rescanned unchanged rows')), \
                patch.object(corrections,'document_fk_rotation_masks',side_effect=AssertionError('Rescanned rotation rows')):
            session.begin_edit()
            for angle in (5.,10.,15.):
                session.rotate('left_elbow',(angle,0.,0.))
            session.commit()

    def test_fk_position_numeric_edit_and_curve_drag_keep_position_axes_and_do_not_change_ik_targets(self):
        session = self.session()
        self.make_ik_range(session)
        session.set_ik(False)
        self.view._timeline_fps = 40.
        self.view.frame = 2  # Shared IK boundary: local FK position must not replace its world-space target.
        original = deepcopy(session.ik_spans)
        endpoint = session.skeleton.joint_names.index('left_wrist')
        pose = evaluate_rig_at_time(session.skeleton,session.draft,session.time(),loop=False)
        value = pose.local_transforms[endpoint].translation[0]+.01
        self.view._timeline_on_coord_input_committed(0,SimpleNamespace(value=lambda:value))
        self.assertEqual(session.ik_spans,original)
        self.assertEqual(session.keys_map('left_wrist',fps=40.)[2]['fk_position_axes'],[True,False,False])
        self.assertFalse(self.view._timeline_key_is_ik(2,0))
        self.assertTrue(self.view._timeline_key_is_ik(2,3))
        self.view._timeline_drag_axis_key(0,2,3,value+.02,commit=True)
        self.assertEqual(session.ik_spans,original)
        row = session.keys_map('left_wrist',fps=40.)[3]
        self.assertEqual(row['fk_position_axes'],[True,False,False])
        self.assertEqual(row['axis_mask'],[True,False,False,False,False,False])
        self.assertFalse(row.get('ik_key'))
        self.assertEqual(session.fk_keys,{})
        expected = corrections.apply_document(session.skeleton,session.base,session.document())
        np.testing.assert_allclose(points(session.skeleton,session.draft,.075),
                                   points(session.skeleton,expected,.075),atol=1e-8)

    def test_closing_preview_publishes_autosaved_changes_once_without_resaving_motion(self):
        session = self.session()
        self.select('left_wrist')
        self.view.frame = 1
        self.view._timeline_on_set_key_clicked()
        self.assertTrue(session._graph_update_pending)
        with patch.object(session,'_notify_saved_changes',wraps=session._notify_saved_changes) as publish, \
                patch.object(corrections,'save_document',side_effect=AssertionError('Closing rewrote a saved draft')):
            session.finish()
            session.finish()
            publish.assert_called_once()
        self.assertFalse(session._graph_update_pending)
        self.assertTrue(corrections.read_document(session.path)['ik_spans'])

    def test_range_navigation_saves_only_small_preferences_and_never_notifies_motion_changes(self):
        session = self.session()
        self.assertTrue(session.save())
        draft = session.draft
        document_path = corrections.sidecar_path(session.path)
        before = document_path.read_bytes()
        with patch.object(session,'save',side_effect=AssertionError('Navigation saved the whole draft')), \
                patch.object(session,'document',side_effect=AssertionError('Navigation serialized motion')), \
                patch.object(self.scene,'set_node_params',side_effect=AssertionError('Navigation emitted a graph edit')), \
                patch.object(collection,'refresh_repair_records',side_effect=AssertionError('Navigation reloaded the collection')), \
                patch.object(corrections,'save_timeline_range',wraps=corrections.save_timeline_range) as saved:
            self.view._timeline_on_display_range_changed(1,3)
            self.view._timeline_range_save_to_disk()
            self.view._timeline_range_save_to_disk()  # No-op release should not rewrite preferences.
            self.assertEqual(saved.call_count,1)
        self.assertIs(session.draft,draft)
        self.assertFalse(session.dirty)
        self.assertEqual(document_path.read_bytes(),before)
        preferences = corrections.timeline_range_path(session.path)
        self.assertLess(preferences.stat().st_size,2048)
        self.assertEqual(corrections.read_timeline_range(session.path,session.source_sha),session.timeline_range)
        restored = self.session()
        self.assertEqual(restored.timeline_range,session.timeline_range)
        self.assertEqual(self.view._timeline_view_start,1)
        self.assertEqual(self.view._timeline_view_span,2)

    def test_range_preferences_reject_stale_or_broken_files_and_leave_draft_loading_intact(self):
        session = self.session()
        session.timeline_range = dict(fps=20.,start=1,end=4,view_start=1,view_end=3)
        self.assertTrue(session.save_timeline_range())
        self.assertIsNone(corrections.read_timeline_range(session.path,'a different animation'))
        self.assertTrue(session.save())
        corrections.timeline_range_path(session.path).write_text('{bad json',encoding='utf-8')
        restored = self.session()
        self.assertEqual(restored.timeline_range,session.timeline_range)

    def test_range_preference_save_does_not_commit_an_unkeyed_ik_preview(self):
        session = self.session()
        self.select('left_wrist')
        self.view.frame = 1
        self.assertTrue(session.save())
        positions = points(session.skeleton,session.draft,session.time())
        start,_,end = corrections.limb_chain(session.skeleton,'left_wrist')
        session.move('left_wrist',positions[start]+.8*(positions[end]-positions[start]))
        draft = session.draft
        preview_time = session._preview_time
        with patch.object(session,'save',side_effect=AssertionError('Range change committed a pose')):
            self.view._timeline_on_display_range_changed(1,3)
            self.view._timeline_range_save_to_disk()
        self.assertIs(session.draft,draft)
        self.assertEqual(session._preview_time,preview_time)
        self.assertEqual(session.ik_spans,[])

    def test_preview_has_only_small_panel_and_source_keys_in_native_timeline(self):
        with patch.object(self.scene,'views',return_value=[SimpleNamespace(window=lambda:self.window)]):
            self.item._plugin_proxies[0].widget().edit()
        session = self.view._animation_correction_session
        self.addCleanup(session.finish)
        self.assertIsInstance(session.panel,CorrectionPanel)
        self.assertFalse(session.panel.findChildren(QtWidgets.QSpinBox))
        self.assertFalse(session.panel.findChildren(QtWidgets.QDoubleSpinBox))
        self.assertLessEqual(session.panel.geometry().bottom(),self.view.height()-120)
        self.select('left_wrist')
        track = next(track for track in session.base.tracks if track.joint_name=='left_wrist')
        self.assertEqual(len(self.view._timeline_keys),len(track.rotation_keys))
        self.assertEqual(self.view._timeline_fps,session.base.sample_rate_hz)
        self.assertTrue(all(not row.get('fbx_clip_key') for row in self.view._timeline_keys.values()))
        for proxy in self.item._plugin_proxies:
            self.assertGreaterEqual(self.item.height,proxy.pos().y()+proxy.size().height()+8)

    def test_real_overlay_draws_source_joint_points_and_labels(self):
        session = self.session()
        self.view._mgl_scene_skeleton_selected_joint = 'left_wrist'
        positions = points(session.skeleton,session.draft,0.)
        self.view._mgl_scene_skeleton_handles_by_owner = {'Correction':[
            dict(role='scene_skeleton',owner='Correction',name=name,index=index,position=tuple(position),axes=[],radius=.02)
            for index,(name,position) in enumerate(zip(session.skeleton.joint_names,positions))]}
        projection = np.eye(4,dtype=np.float32)
        projection[0,0] = projection[1,1] = projection[2,2] = .7
        projection[1,3] = -.65
        self.view._mgl_pick_proj = projection
        self.view._mgl_pick_view = self.view._mgl_pick_model = np.eye(4,dtype=np.float32)
        data = self.view._mgl_scene_skeleton_qt_overlay_data()
        self.assertFalse(data['show_names'])
        session.rig['show_joint_names'] = True  # Legacy take preference is ignored.
        self.assertFalse(self.view._mgl_scene_skeleton_qt_overlay_data()['show_names'])
        self.view._mgl_scene_skeleton_show_joint_names = True
        self.assertTrue(self.view._mgl_scene_skeleton_qt_overlay_data()['show_names'])
        self.view._mgl_scene_skeleton_show_joint_names = False
        self.assertIs(data['rig_guide'],session.guide)
        self.assertEqual(len([p for p in data['points'] if isinstance(p,dict)]),len(session.skeleton.joints))
        image = QtGui.QImage(self.view.size(),QtGui.QImage.Format_ARGB32)
        image.fill(QtCore.Qt.black)
        painter = QtGui.QPainter(image)
        labels = []
        add_text = QtGui.QPainterPath.addText
        def record_text(path,point,font,text):
            labels.append(text)
            return add_text(path,point,font,text)
        try:
            with patch.object(QtGui.QPainterPath,'addText',record_text):
                self.view._draw_scene_skeleton_qt_overlay(painter)
            self.assertEqual(set(labels)&set(session.skeleton.joint_names),{'left_wrist'})
            labels.clear()
            self.view._mgl_scene_skeleton_show_joint_names = True
            with patch.object(QtGui.QPainterPath,'addText',record_text):
                self.view._draw_scene_skeleton_qt_overlay(painter)
            self.assertEqual(set(labels)&set(session.skeleton.joint_names),set(session.skeleton.joint_names))
        finally:
            painter.end()
        wrist = next(p for p in data['points'] if p and p['name']=='left_wrist')
        self.assertNotEqual(image.pixelColor(round(wrist['x']),round(wrist['y'])).name(),'#000000')
        picked = self.view.pick_retarget_joint_at(round(wrist['x']),round(wrist['y']),self.view.width(),self.view.height())
        self.assertEqual(picked['name'],'left_wrist')

    def test_preview_reuses_rig_guides_and_visibility_toggles_do_not_change_motion(self):
        session = self.session()
        guide = session.rig['priormdm_rig_debug']
        self.assertTrue(guide['read_only'])
        self.assertTrue(guide['limits'])
        self.assertTrue(guide['collisions'])
        self.assertTrue({'collision','limit'} <= {shape['kind'] for shape in guide['shapes']})
        session.panel.layout().activate()
        self.assertEqual(session.panel.limits.y(),session.panel.colliders.y())
        self.assertGreater(session.panel.limits.x(),session.panel.colliders.geometry().right())
        self.assertLess(session.panel.limits.geometry().right(),session.panel.width())
        before = deepcopy(session.draft.to_dict())
        session.panel.limits.setChecked(False)
        self.assertFalse(guide['limits'])
        self.assertTrue(guide['collisions'])
        session.panel.colliders.setChecked(False)
        self.assertFalse(guide['collisions'])
        self.assertFalse(guide['limits'])
        self.assertFalse(hasattr(session.panel,'names'))
        self.assertEqual(before,session.draft.to_dict())
        document = corrections.read_document(session.path)
        self.assertFalse(document['show_colliders'])
        self.assertFalse(document['show_limits'])
        self.assertNotIn('show_joint_names',document)
        restored = self.session()
        self.assertFalse(restored.panel.colliders.isChecked())
        self.assertFalse(restored.panel.limits.isChecked())
        self.assertFalse(restored.guide['limits'])
        restored.panel.limits.setChecked(True)
        self.assertTrue(restored.guide['limits'])
        self.assertFalse(restored.guide['collisions'])
        self.assertFalse(hasattr(restored.panel,'names'))

    def test_cube_click_selects_real_wrist_shows_gizmo_and_does_not_key_a_pose(self):
        session = self.session()
        self.view._mgl_gizmo_enabled = self.view._mgl_gizmo_visible = False
        handle = dict(role='scene_skeleton',owner='Correction',name='left_wrist',index=20,
                      position=(0.,0.,0.),control_radius=.1,ik_control=True)
        self.view._mgl_scene_skeleton_handles_by_owner = {'Correction':[handle]}
        event = SimpleNamespace(button=lambda:QtCore.Qt.LeftButton,accept=Mock(),
                                position=lambda:QtCore.QPointF(100,100))
        with patch.object(session,'ray',return_value=(np.array([0.,0.,3.]),np.array([0.,0.,-1.]))):
            self.assertTrue(gl_view_mouse._handle_animation_correction_control_press(self.view,event,False))
            with patch.object(session,'move') as move:
                session.drag_to(event)
                move.assert_not_called()
        session.commit()
        self.assertEqual(self.view._mgl_scene_skeleton_selected_joint,'left_wrist')
        self.assertEqual(self.view._xform_gizmo_owner,'Correction::skeleton_joint::left_wrist')
        self.assertEqual(self.view._xform_gizmo_owner_kind,'scene_skeleton_joint')
        self.assertEqual(self.view._xform_gizmo_mode,'translate')
        self.assertTrue(self.view._mgl_gizmo_visible)
        self.assertFalse(session.tracks)
        self.assertFalse(session.dirty)
        joint = dict(handle,name='left_elbow')
        self.view._mgl_scene_skeleton_handle_click(joint)
        self.assertEqual(self.view._xform_gizmo_mode,'rotate')
        self.assertTrue(self.view._mgl_gizmo_visible)

    def test_ik_cube_dimensions_are_exactly_one_third_of_previous_size(self):
        old_radius = .12*2.5
        current = ik_control_radius(.12)
        self.assertAlmostEqual(current,old_radius/3.)
        np.testing.assert_allclose(np.ptp(cube_curves((0,0,0),current),axis=0),np.full(3,2*old_radius/3.))

    def test_ik_cubes_follow_joint_axes_and_rotated_picking_matches_the_visible_box(self):
        axes = Rotation.from_euler('z',45,degrees=True).as_matrix().T
        center = np.array([1.,2.,3.])
        curves = cube_curves(center,.2,axes)
        np.testing.assert_allclose(np.abs((curves-center)@axes.T),.2,atol=2e-7)
        session = self.session()
        handle = dict(name='left_wrist',position=(0.,0.,0.),axes=axes,control_radius=1.,ik_control=True)
        self.view._mgl_scene_skeleton_handles_by_owner = {'Correction':[handle]}
        self.view._mgl_scene_skeleton_handle_click = Mock()
        event = SimpleNamespace(button=lambda:QtCore.Qt.LeftButton,accept=Mock(),position=lambda:QtCore.QPointF(100,100))
        for origin,hit in (([1.2,0,3],True),([.9,.9,3],False)):
            with self.subTest(origin=origin),patch.object(session,'ray',return_value=(np.array(origin),np.array([0.,0.,-1.]))),patch.object(session,'start_drag',return_value=True) as drag:
                self.assertEqual(gl_view_mouse._handle_animation_correction_control_press(self.view,event,False),hit)
                self.assertEqual(drag.called,hit)

    def test_pole_picker_selects_its_gizmo_and_direct_drag_routes_to_the_pole(self):
        session = self.session()
        handle = dict(name='left_wrist',owner='Correction',position=(0.,0.,0.),control_radius=.2,
                      ik_control=True,ik_pole=True)
        session.pole_handles = [handle]
        self.view._mgl_scene_skeleton_handles_by_owner = {'Correction':[]}
        event = SimpleNamespace(button=lambda:QtCore.Qt.LeftButton,accept=Mock(),position=lambda:QtCore.QPointF(100,100))
        overlay = dict(owner='Correction',pole_radius=9.,pole_points=[dict(name='left_wrist',x=100.,y=100.,z=0.)])
        with patch.object(session,'ray',return_value=(np.array([0.,0.,3.]),np.array([0.,0.,-1.]))),patch.object(self.view,'_mgl_scene_skeleton_qt_overlay_data',return_value=overlay):
            self.assertTrue(gl_view_mouse._handle_animation_correction_control_press(self.view,event,False))
        self.assertEqual(session.selected_pole,'left_wrist')
        np.testing.assert_allclose(self.view._xform_gizmo_pos,handle['position'])
        with patch.object(session,'ray',return_value=(np.array([.1,0.,3.]),np.array([0.,0.,-1.]))),patch.object(session,'move_pole') as move:
            event.position = lambda:QtCore.QPointF(110,100)
            session.drag_to(event)
            np.testing.assert_allclose(move.call_args.args[1],[.1,0,0])
            session.move('left_wrist',[.2,0,0])
            np.testing.assert_allclose(move.call_args.args[1],[.2,0,0])
        session.commit()
        session.select('left_wrist')
        self.assertIsNone(session.selected_pole)

    def test_pole_keys_interpolate_reopen_and_export_without_moving_the_hand_target(self):
        session = self.bent_session()
        joint = 'left_wrist'
        start,middle,end = corrections.ik_chain(session.skeleton,joint)
        initial = points(session.skeleton,session.base,0.)
        target = initial[start]+.8*(initial[end]-initial[start])
        axis = target-initial[start]
        axis /= np.linalg.norm(axis)
        pose = evaluate_rig_at_time(session.skeleton,session.base,0.,loop=False)
        reference = corrections.default_ik_pole(session.skeleton,pose,joint)-initial[start]
        reference -= axis*np.dot(reference,axis)
        reference /= np.linalg.norm(reference)
        across = np.cross(axis,reference)
        poles = [initial[start]+reference+across,initial[start]-reference+across]
        for frame,pole in zip((1,3),poles):
            self.view.frame = frame
            session.move(joint,target)
            session.commit()
            before = points(session.skeleton,session.draft,session.time())
            session.move_pole(joint,pole)
            session.commit()
            after = points(session.skeleton,session.draft,session.time())
            np.testing.assert_allclose(after[end],before[end],atol=1e-7)
            np.testing.assert_allclose(after[end],target,atol=1e-7)
            self.assertGreater(np.linalg.norm(after[middle]-before[middle]),.01)
            self.assertTrue(session.key_ik(joint,mode='start' if frame==1 else 'end'))
        self.assertTrue(session.save())
        document = corrections.read_document(session.path)
        np.testing.assert_allclose(document['ik_spans'][0]['start']['pole'],poles[0])
        np.testing.assert_allclose(document['ik_spans'][0]['end']['pole'],poles[1])
        restored = corrections.apply_document(session.skeleton,session.base,document)
        for stamp,alpha in ((.05,0),(.075,.25),(.1,.5),(.125,.75),(.15,1)):
            actual = points(session.skeleton,restored,stamp)
            np.testing.assert_allclose(actual[end],target,atol=1e-6)
            bend = actual[middle]-actual[start]
            bend -= axis*np.dot(bend,axis)
            wanted = (1-alpha)*poles[0]+alpha*poles[1]-actual[start]
            wanted -= axis*np.dot(wanted,axis)
            self.assertGreater(np.dot(bend,wanted)/(np.linalg.norm(bend)*np.linalg.norm(wanted)),.99999)
        output = session.approve()
        self.assertTrue(output)
        loaded = ingest_bvh_animation_data(output)
        for stamp in (.05,.1,.15):
            np.testing.assert_allclose(points(loaded.skeleton,loaded.clips[0],stamp),points(session.skeleton,restored,stamp),atol=2e-5)

    def test_pole_preview_undo_redo_preserves_the_pole_without_keying_it(self):
        session = self.bent_session()
        self.view.frame = 2
        start,_,end = corrections.ik_chain(session.skeleton,'left_wrist')
        initial = points(session.skeleton,session.base,.1)
        target = initial[start]+.8*(initial[end]-initial[start])
        session.move('left_wrist',target)
        session.commit()
        before = points(session.skeleton,session.draft,.1)
        pole = initial[start]+[0,0,1.]
        session.move_pole('left_wrist',pole)
        session.commit()
        after = points(session.skeleton,session.draft,.1)
        self.assertTrue(session.undo())
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),before,atol=1e-7)
        self.assertTrue(session.redo())
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),after,atol=1e-7)
        np.testing.assert_allclose(session.ik_pole('left_wrist'),pole,atol=1e-7)
        self.assertEqual(corrections.read_document(session.path)['ik_spans'],[])

    def test_native_delete_set_key_and_reload_do_not_reinsert_deleted_source_keys(self):
        session = self.session()
        session.set_ik(False)
        self.select('left_wrist')
        self.view.frame = 2
        self.view._timeline_curve_selected = {(axis,2) for axis in (3,4,5)}
        self.assertTrue(self.view.timeline_delete_selected_keys())
        rotation_times = [key.time for track in session.draft.tracks if track.joint_name=='left_wrist' for key in track.rotation_keys]
        self.assertFalse(any(abs(time-.1)<1e-7 for time in rotation_times))
        self.view._timeline_load_from_disk()
        self.assertNotIn(2,self.view._timeline_keys)
        session.finish()
        loaded = self.session()
        self.select('left_wrist')
        self.assertNotIn(2,self.view._timeline_keys)
        self.view._timeline_on_set_key_clicked()
        self.assertIn(2,self.view._timeline_keys)
        self.assertTrue(any(abs(key.time-.1)<1e-7 for track in loaded.draft.tracks if track.joint_name=='left_wrist' for key in track.rotation_keys))

    def test_scrubbing_uses_native_playhead_and_keeps_approval_and_keys_unchanged(self):
        session = self.session()
        self.select('left_wrist')
        self.view.frame = 1
        session.rotate('left_wrist',(0,45,0))
        session.commit()
        approved = session.approve()
        self.assertTrue(approved)
        before = deepcopy(session.document())
        self.view.frame = 3
        self.assertAlmostEqual(session.time(),.15)
        self.view._timeline_apply_frame_if_keyed(3,force=True)
        self.assertEqual(before,session.document())
        self.assertNotIn('_animation_correction_time',session.rig)

    def test_ik_for_both_arms_and_legs_preserves_lengths_and_keys_timeline_frame(self):
        session = self.session()
        self.assertEqual(session.endpoints,{'pelvis','spine3','left_wrist','right_wrist','left_ankle','right_ankle'})
        self.view.frame = 2
        initial = points(session.skeleton,session.draft,.1)
        for joint in corrections.limb_endpoints(session.skeleton):
            start,_,end = corrections.limb_chain(session.skeleton,joint)
            target = initial[start]+.8*(initial[end]-initial[start])
            target[2] += .04
            session.move(joint,target)
            actual = points(session.skeleton,session.draft,.1)
            self.assertLess(np.linalg.norm(actual[end]-target),.001)
            parents = np.array([joint.parent_index for joint in session.skeleton.joints[1:]])
            np.testing.assert_allclose(np.linalg.norm(actual[1:]-actual[parents],axis=1),
                                      np.linalg.norm(initial[1:]-initial[parents],axis=1),atol=1e-7)
        session.set_ik(False)
        with patch.object(corrections,'solve_limb_pose') as solve:
            session.move('left_wrist',[0,0,0])
            solve.assert_not_called()
        session.select('left_wrist')
        self.assertEqual(self.view._xform_gizmo_mode,'rotate')

    def bent_session(self):
        context = self.context()
        bent = {'left_elbow','right_elbow','left_knee','right_knee'}
        context['clip'] = replace(context['clip'],tracks=[replace(track,rotation_keys=[
            replace(key,value=tuple(Rotation.from_euler('z' if 'elbow' in track.joint_name else 'x',
                35,degrees=True).as_quat())) for key in track.rotation_keys]) if track.joint_name in bent else track
            for track in context['clip'].tracks])
        session = CorrectionSession(self.item,context,self.view)
        session.open(self.window)
        self.addCleanup(session.finish)
        return session

    def test_body_ik_controls_and_lock_checkbox_share_the_existing_preview(self):
        session = self.session()
        panel = session.panel
        self.assertEqual(panel.ik.text(),'IK Controls')
        self.assertEqual(panel.lock_limbs.text(),'Lock arm/leg IK')
        self.assertGreater(panel.lock_limbs.pos().x(),panel.ik.pos().x())
        self.assertEqual(panel.lock_limbs.pos().y(),panel.ik.pos().y())
        for name in ('pelvis','spine3'):
            session.select(name)
            self.assertEqual(self.view._xform_gizmo_mode,'translate')
            self.assertTrue(all(button.isEnabled() for button in panel.ik_buttons))
        panel.lock_limbs.setChecked(True)
        self.assertTrue(corrections.read_document(session.path)['lock_limbs'])
        panel.ik.setChecked(False)
        self.assertFalse(panel.lock_limbs.isEnabled())
        panel.ik.setChecked(True)
        self.assertTrue(panel.lock_limbs.isEnabled())

    def test_correction_preview_button_matches_limiter_height_and_fits_node_border(self):
        limiter = self.scene.add_node(GraphNode('Limiter',kind='rig_limiter'),QtCore.QPointF())
        correction = self.item._plugin_proxies[0].widget()
        controls = limiter._plugin_proxies[0].widget()
        self.assertEqual(correction.preview_button.height(),controls.preview_button.height())
        for proxy in self.item._plugin_proxies:
            self.assertGreaterEqual(self.item.height,proxy.pos().y()+proxy.size().height()+8)

    def test_unlocked_pelvis_moves_the_chest_hands_and_feet_and_can_be_keyed(self):
        session = self.session()
        self.view.frame = 2
        initial = points(session.skeleton,session.draft,.1)
        pelvis = session.skeleton.joint_names.index('pelvis')
        delta = np.array([.04,-.03,.02])
        session.move('pelvis',initial[pelvis]+delta)
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),initial+delta,atol=1e-7)
        session.commit()
        self.assertTrue(session.key_ik('pelvis'))
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),initial+delta,atol=1e-7)
        self.assertTrue(session.save())
        restored = corrections.apply_document(session.skeleton,session.base,corrections.read_document(session.path))
        np.testing.assert_allclose(points(session.skeleton,restored,.1),initial+delta,atol=1e-7)
        for stamp in (0.,.2):
            np.testing.assert_allclose(points(session.skeleton,restored,stamp),points(session.skeleton,session.base,stamp),atol=1e-7)

    def test_locked_pelvis_translation_and_rotation_preserve_world_limb_targets(self):
        session = self.bent_session()
        session.set_lock_limbs(True)
        self.view.frame = 2
        initial = points(session.skeleton,session.draft,.1)
        ends = [session.skeleton.joint_names.index(name) for name in corrections.limb_endpoints(session.skeleton)]
        pelvis = session.skeleton.joint_names.index('pelvis')
        for step in (1,2,3):
            session.move('pelvis',initial[pelvis]+[0,-.005*step,.002*step])
        session.rotate('pelvis',(0,3,0))
        actual = points(session.skeleton,session.draft,.1)
        np.testing.assert_allclose(actual[ends],initial[ends],atol=1e-6)
        pose = evaluate_rig_at_time(session.skeleton,session.draft,.1,loop=False)
        locks = corrections.limb_world_targets(session.skeleton,pose)
        for name,original in session._preview_locks.items():
            self.assertLess((Rotation.from_quat(original['rotation']).inv()*Rotation.from_quat(locks[name]['rotation'])).magnitude(),1e-7)
        session.commit()
        self.assertTrue(session.key_ik('pelvis'))
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1)[ends],initial[ends],atol=1e-6)

    def test_chest_ik_preserves_spine_lengths_and_moves_or_locks_hands(self):
        for locked in (False,True):
            with self.subTest(locked=locked):
                session = self.bent_session()
                session.set_lock_limbs(locked)
                self.view.frame = 2
                initial = points(session.skeleton,session.draft,.1)
                start,middle,chest = corrections.ik_chain(session.skeleton,'spine3')
                target = initial[start]+.96*(initial[chest]-initial[start])+[0,0,.012]
                session.move('spine3',target)
                actual = points(session.skeleton,session.draft,.1)
                np.testing.assert_allclose(actual[chest],target,atol=1e-6)
                for a,b in ((start,middle),(middle,chest)):
                    self.assertAlmostEqual(np.linalg.norm(actual[b]-actual[a]),np.linalg.norm(initial[b]-initial[a]),places=7)
                ends = [session.skeleton.joint_names.index(name) for name in corrections.limb_endpoints(session.skeleton)]
                if locked:
                    np.testing.assert_allclose(actual[ends],initial[ends],atol=1e-6)
                else:
                    wrists = [session.skeleton.joint_names.index(name) for name in ('left_wrist','right_wrist')]
                    self.assertGreater(np.linalg.norm(actual[wrists]-initial[wrists]),.001)
                session.commit()
                self.assertTrue(session.key_ik('spine3'))
                np.testing.assert_allclose(points(session.skeleton,session.draft,.1)[chest],target,atol=1e-6)
                session.finish()

    def test_paired_pelvis_and_chest_ranges_pin_limbs_reopen_and_export(self):
        session = self.bent_session()
        session.set_lock_limbs(True)
        names = session.skeleton.joint_names
        pelvis = names.index('pelvis')
        chest = names.index('spine3')
        ends = [names.index(name) for name in corrections.limb_endpoints(session.skeleton)]
        source = points(session.skeleton,session.base,0.)
        pelvis_targets,chest_targets = [],[]
        for frame,depth in ((1,.01),(3,.02)):
            self.view.frame = frame
            session.clear_preview()
            pelvis_target = source[pelvis]+[0,-depth,.004]
            session.move('pelvis',pelvis_target)
            session.commit()
            self.assertTrue(session.key_ik('pelvis',mode='start' if frame == 1 else 'end'))
            posed = points(session.skeleton,session.draft,session.time())
            start,_,_ = corrections.ik_chain(session.skeleton,'spine3')
            chest_target = posed[start]+.97*(posed[chest]-posed[start])+[0,0,.01]
            session.move('spine3',chest_target)
            session.commit()
            self.assertTrue(session.key_ik('spine3',mode='start' if frame == 1 else 'end'))
            pelvis_targets.append(pelvis_target)
            chest_targets.append(chest_target)
        expected = {}
        for stamp in (0.,.05,.075,.1,.125,.15,.2):
            actual = points(session.skeleton,session.draft,stamp)
            expected[stamp] = actual
            if .05 <= stamp <= .15:
                alpha = (stamp-.05)/.1
                np.testing.assert_allclose(actual[pelvis],(1-alpha)*pelvis_targets[0]+alpha*pelvis_targets[1],atol=1e-6)
                np.testing.assert_allclose(actual[chest],(1-alpha)*chest_targets[0]+alpha*chest_targets[1],atol=1e-6)
                np.testing.assert_allclose(actual[ends],source[ends],atol=1e-6)
            else:
                np.testing.assert_allclose(actual,source,atol=1e-7)
        self.assertTrue(session.save())
        restored = corrections.apply_document(session.skeleton,session.base,corrections.read_document(session.path))
        for stamp,actual in expected.items():
            np.testing.assert_allclose(points(session.skeleton,restored,stamp),actual,atol=1e-7)
        self.assertTrue(session.approve())
        record = corrections.read_document(session.path)['approved']
        output = ingest_bvh_animation_data(session.path.parent/record['file'])
        for stamp in (.05,.1,.15):
            np.testing.assert_allclose(points(output.skeleton,output.clips[0],stamp),expected[stamp],atol=2e-5)

    def test_lock_mode_can_change_while_completing_a_pelvis_range(self):
        session = self.bent_session()
        self.view.frame = 1
        pelvis = session.skeleton.joint_names.index('pelvis')
        source = points(session.skeleton,session.base,.05)
        session.move('pelvis',source[pelvis]+[0,-.01,0])
        session.commit()
        self.assertTrue(session.key_ik('pelvis',mode='start'))
        session.set_lock_limbs(True)
        self.view.frame = 3
        session.move('pelvis',source[pelvis]+[0,-.02,0])
        session.commit()
        self.assertTrue(session.key_ik('pelvis',mode='end'))
        self.assertTrue(session.ik_spans[0]['lock_limbs'])
        self.assertIn('end',session.ik_spans[0])
        corrections.validate_ik_spans(session.skeleton,session.base,session.ik_spans)
        session.set_lock_limbs(False)
        session.move('pelvis',source[pelvis]+[0,-.025,0])
        session.commit()
        self.assertTrue(session.key_ik('pelvis'))
        self.assertFalse(session.ik_spans[0]['lock_limbs'])
        self.assertEqual(session.ik_spans[0]['start']['locks'],{})
        self.assertEqual(set(session.ik_spans[0]['end']['rotations']),{'pelvis'})

    def test_existing_limb_keys_follow_unlocked_pelvis_and_stay_world_locked_when_checked(self):
        for locked in (False,True):
            with self.subTest(locked=locked):
                session = self.bent_session()
                self.view.frame = 2
                initial = points(session.skeleton,session.draft,.1)
                start,_,wrist = corrections.limb_chain(session.skeleton,'left_wrist')
                target = initial[start]+.9*(initial[wrist]-initial[start])
                session.move('left_wrist',target)
                session.commit()
                self.assertTrue(session.key_ik('left_wrist'))
                session.set_lock_limbs(locked)
                pelvis = session.skeleton.joint_names.index('pelvis')
                delta = np.array([0,-.015,.003])
                session.move('pelvis',initial[pelvis]+delta)
                session.commit()
                self.assertTrue(session.key_ik('pelvis'))
                expected = target if locked else target+delta
                np.testing.assert_allclose(points(session.skeleton,session.draft,.1)[wrist],expected,atol=1e-6)
                # Keying the child after the parent must not apply the parent's
                # movement twice; editing the parent later carries it once.
                if not locked:
                    session.move('left_wrist',expected+[0,.003,0])
                    session.commit()
                    self.assertTrue(session.key_ik('left_wrist'))
                    np.testing.assert_allclose(points(session.skeleton,session.draft,.1)[wrist],expected+[0,.003,0],atol=1e-6)
                    session.move('pelvis',initial[pelvis]+2*delta)
                    session.commit()
                    self.assertTrue(session.key_ik('pelvis'))
                    np.testing.assert_allclose(points(session.skeleton,session.draft,.1)[wrist],expected+delta+[0,.003,0],atol=1e-6)
                session.finish()

    def test_existing_chest_key_follows_pelvis_while_limbs_stay_locked(self):
        session = self.bent_session()
        session.set_lock_limbs(True)
        self.view.frame = 2
        initial = points(session.skeleton,session.draft,.1)
        names = session.skeleton.joint_names
        start,_,chest = corrections.ik_chain(session.skeleton,'spine3')
        chest_target = initial[start]+.97*(initial[chest]-initial[start])+[0,0,.01]
        session.move('spine3',chest_target)
        session.commit()
        self.assertTrue(session.key_ik('spine3'))
        delta = np.array([0,-.01,.002])
        session.move('pelvis',initial[names.index('pelvis')]+delta)
        session.commit()
        expected = points(session.skeleton,session.draft,.1)
        self.assertTrue(session.key_ik('pelvis'))
        actual = points(session.skeleton,session.draft,.1)
        np.testing.assert_allclose(actual[chest],chest_target+delta,atol=1e-6)
        np.testing.assert_allclose(actual,expected,atol=1e-6)

    def test_cubes_are_twelve_curves_and_direct_drag_changes_ik_target(self):
        curves = cube_curves((1,2,3),.2)
        self.assertEqual(curves.shape,(24,3))
        self.assertEqual(len(np.unique(curves,axis=0)),8)
        session = self.session()
        handle = dict(name='left_wrist',position=(0.,0.,0.),control_radius=.2,ik_control=True)
        self.view._mgl_scene_skeleton_handles_by_owner = {'Correction':[handle]}
        self.view._mgl_scene_skeleton_handle_click = Mock()
        event = SimpleNamespace(button=lambda:QtCore.Qt.LeftButton,accept=Mock(),position=lambda:QtCore.QPointF(100,100))
        with patch.object(session,'ray',return_value=(np.array([0.,0.,3.]),np.array([0.,0.,-1.]))):
            self.assertTrue(gl_view_mouse._handle_animation_correction_control_press(self.view,event,False))
        with patch.object(session,'ray',return_value=(np.array([.2,0.,3.]),np.array([0.,0.,-1.]))),patch.object(session,'move') as move:
            event.position = lambda:QtCore.QPointF(110,100)
            session.drag_to(event)
            np.testing.assert_allclose(move.call_args.args[1],[.2,0,0])
        session.commit()
        self.assertIsNone(self.view._animation_correction_drag)

    def test_json_draft_then_approval_exports_bvh_beside_source_and_marks_collection(self):
        session = self.session()
        original = self.paths[0].read_bytes()
        self.view.frame = 2
        session.rotate('left_wrist',(15,20,30))
        session.key_ik('left_wrist',mode='start')
        self.assertTrue(session.save())
        document = corrections.read_document(self.paths[0])
        self.assertEqual(document['version'],2)
        self.assertIsNone(document['approved'])
        self.assertEqual(len(list(self.paths[0].parent.glob('*.bvh'))),2)
        output = session.approve()
        self.assertEqual(output.parent,self.paths[0].parent)
        self.assertNotEqual(output,self.paths[0])
        self.assertEqual(original,self.paths[0].read_bytes())
        record = corrections.read_document(self.paths[0])['approved']
        self.assertEqual(output.name,record['file'])
        self.assertTrue(collection.entries(self.source.model)[0]['approved'])
        loaded = ingest_bvh_animation_data(output)
        np.testing.assert_allclose(points(loaded.skeleton,loaded.clips[0],.1),points(session.skeleton,session.draft,.1),atol=1e-5)
        self.assertNotIn(output,collection.bvh_paths_in_folder(self.paths[0].parent))
        session.rotate('left_wrist',(40,20,30))
        session.key_ik('left_wrist')
        session.save()
        self.assertIsNone(corrections.read_document(self.paths[0])['approved'])
        self.assertFalse(collection.entries(self.source.model)[0]['approved'])
        self.assertNotIn(output,collection.bvh_paths_in_folder(self.paths[0].parent))

    def test_approval_failure_never_marks_or_replaces_original(self):
        session = self.session()
        with patch('nodes.rig_limiter.spec.export_context',side_effect=OSError('disk full')):
            self.assertFalse(session.approve())
        self.assertIsNone(corrections.read_document(self.paths[0])['approved'])
        self.assertEqual(len(list(self.paths[0].parent.glob('*.bvh'))),2)

    def test_approval_exports_joint_positions_and_sparse_fk_rotations_as_shown_in_preview(self):
        session = self.session()
        original = session.path.read_bytes()
        self.make_ik_range(session)
        name = 'left_wrist'
        index = session.skeleton.joint_names.index(name)
        for frame,delta in ((0,.01),(4,.035)):
            self.view.frame = frame
            value = np.asarray(session.skeleton.joints[index].local_bind.translation)+[0,0,delta]
            self.assertTrue(session.key_fk(name,axes=[2],translation=value,save=True))
        self.view.frame = 2
        self.assertTrue(session.key_fk('left_shoulder',axes=[3],rotation=
            Rotation.from_euler('xyz',(13,24,35),degrees=True).as_quat(),save=True))
        before = {time:evaluate_rig_at_time(session.skeleton,session.draft,time,loop=False)
                  for time in (0.,.05,.1,.15,.2)}
        output = session.approve()
        self.assertTrue(output,session.panel.status.text())
        self.assertTrue(output.is_file())
        self.assertEqual(session.path.read_bytes(),original)
        loaded = ingest_bvh_animation_data(output)
        self.assertIn('Xposition',loaded.skeleton.metadata['channels'][name])
        self.assertNotIn('Xposition',loaded.skeleton.metadata['channels']['right_wrist'])
        order = [loaded.skeleton.joint_names.index(joint) for joint in session.skeleton.joint_names]
        for time,pose in before.items():
            actual = evaluate_rig_at_time(loaded.skeleton,loaded.clips[0],time,loop=False)
            np.testing.assert_allclose(np.asarray(actual.global_matrices)[order],pose.global_matrices,atol=2e-6)
        self.assertEqual(corrections.read_document(session.path)['approved']['file'],output.name)

    def test_approval_preserves_a_saved_constant_wrist_offset_without_relaxing_rig_limiter(self):
        from nodes.rig_limiter import spec as limiter
        session = self.session()
        name = 'left_wrist'
        track = deepcopy(next(track for track in session.base.tracks if track.joint_name == name))
        index = session.skeleton.joint_names.index(name)
        value = np.asarray(session.skeleton.joints[index].local_bind.translation)+[.012,-.004,.008]
        track.translation_keys = [Vec3Keyframe(.1,tuple(value))]
        session.rebuild(tracks={name:track.to_dict()})
        rejected = self.paths[0].with_name('rejected.bvh')
        with self.assertRaisesRegex(ValueError,'fixed offsets'):
            limiter.export_context(dict(skeleton=session.skeleton,clip=session.manual),rejected)
        self.assertFalse(rejected.exists())
        before = [points(session.skeleton,session.draft,time) for time in (0.,.1,.2)]
        output = session.approve()
        self.assertTrue(output,session.panel.status.text())
        loaded = ingest_bvh_animation_data(output)
        for time,expected in zip((0.,.1,.2),before):
            np.testing.assert_allclose(points(loaded.skeleton,loaded.clips[0],time),expected,atol=2e-6)

    def test_wireframe_cubes_are_submitted_to_renderer_and_disappear_when_ik_is_off(self):
        session = self.session()
        scene = SimpleNamespace(_items=[])
        scene.items = lambda:list(scene._items)
        scene.add = lambda item:scene._items.append(item)
        self.view._mgl_scene = scene
        def wire(**kwargs):
            return SimpleNamespace(tag=kwargs['tag'],payload={'owner':kwargs['owner']},release=Mock())
        self.view._mgl_add_wire_item_from_points = Mock(side_effect=wire)
        axes = Rotation.from_euler('xyz',[17,33,41],degrees=True).as_matrix().T
        handles = [dict(name=name,position=position,radius=.1,control_radius=.25,axes=axes)
                   for name,position in zip(session.skeleton.joint_names,points(session.skeleton,session.draft,0.))]
        session.update_pole_handles(handles)
        self.assertEqual({pole['name'] for pole in session.pole_handles},session.endpoints-{'pelvis'})
        MGLRendererMixin._mgl_scene_skeleton_rebuild_handle_mesh_for_owner(self.view,'Correction',handles)
        self.assertEqual(len(scene._items),11)
        by_name = {('Correction '+handle['name']+(' IK pole' if handle.get('ik_pole') else ' IK cube')):handle
                   for handle in handles+session.pole_handles}
        for call in self.view._mgl_add_wire_item_from_points.call_args_list:
            self.assertEqual(call.kwargs['tag'],'scene-skeleton-handles')
            handle = by_name[call.kwargs['name']]
            curves = call.kwargs['line_points']
            self.assertEqual(curves.shape,(2 if handle.get('ik_pole') else 24,3))
            if handle.get('ik_pole'):
                np.testing.assert_allclose(curves,[handle['bend_position'],handle['position']],atol=2e-7)
            else:
                np.testing.assert_allclose(np.abs((curves-handle['position'])@axes.T),handle['control_radius'],atol=2e-7)
        session.ik_enabled = False
        MGLRendererMixin._mgl_scene_skeleton_rebuild_handle_mesh_for_owner(self.view,'Correction',handles)
        self.assertEqual(scene._items,[])

    def test_edits_after_rig_limiter_survive_reloading_the_persisted_repair(self):
        from nodes.rig_limiter import spec as limiter
        self.scene._edges.clear()
        limited = self.scene.add_node(GraphNode('Limiter',kind='rig_limiter'),QtCore.QPointF())
        self.scene.add_edge('Collection','Limiter')
        self.scene._edges[-1].dst_port_name = 'animation'
        self.scene.add_edge('Limiter','Correction')
        self.scene._edges[-1].dst_port_name = 'animation'
        with patch.object(limiter,'run_repair',side_effect=lambda function,parent=None:function(lambda f,n:None)):
            limiter.prepare_repair(limited)
        session = self.session()
        session.set_ik(False)
        self.view.frame = 2
        session.rotate('left_wrist',(20,0,0))
        session.save()
        before = points(session.skeleton,session.draft,.1)
        del limited.model._rig_limiter_cache
        reloaded = self.session()
        self.assertIn('left_wrist',reloaded.fk_keys)
        np.testing.assert_allclose(points(reloaded.skeleton,reloaded.draft,.1),before,atol=1e-5)

    def test_switching_collection_takes_keeps_distinct_overrides(self):
        session = self.session()
        session.set_ik(False)
        self.view.frame = 2
        session.rotate('left_wrist',(20,0,0))
        session.save()
        collection.save_state(self.source.model,collection.entries(self.source.model),2,self.scene)
        other = self.session()
        self.assertFalse(other.fk_keys)
        collection.save_state(self.source.model,collection.entries(self.source.model),1,self.scene)
        restored = self.session()
        self.assertIn('left_wrist',restored.fk_keys)
        self.assertIsNot(restored.panel,session.panel)

    def test_fbx_context_is_accepted_and_uses_fbx_json_filename(self):
        path = Path(self.temp.name)/'motion.fbx'
        path.write_bytes(b'FBX test source')
        context = dict(self.context(),path=str(path),source_format='fbx')
        with patch('nodes.rig_limiter.spec.input_context',return_value=context):
            self.assertIs(spec.input_context(self.item,[],[]),context)
        session = CorrectionSession(self.item,context,self.view)
        self.assertTrue(session.save())
        self.assertEqual(corrections.sidecar_path(path).name,'motion.fbx.animation_correction.json')

    def test_source_change_guards_draft_save_and_cycles_are_rejected(self):
        session = self.session()
        self.paths[0].write_bytes(self.paths[0].read_bytes()+b'\n')
        self.assertFalse(session.save())
        self.scene._edges.clear()
        self.scene.add_edge('Correction','Correction')
        self.scene._edges[-1].dst_port_name = 'animation'
        errors = []
        self.assertIsNone(spec.resolve_context(self.item,errors,[]))
        self.assertTrue(errors)


    def make_ik_range(self,session,joint='left_wrist'):
        self.select(joint)
        targets = []
        for frame,depth in ((1,.03),(3,.08)):
            self.view.frame = frame
            start,_,end = corrections.limb_chain(session.skeleton,joint)
            source = points(session.skeleton,session.base,frame/self.view._timeline_fps)
            target = source[start]+.8*(source[end]-source[start])
            target[2] += depth
            session.move(joint,target)
            session.commit()
            self.assertTrue(session.key_ik(joint,mode='start' if frame == 1 else 'end'))
            targets.append(target)
        return targets

    def test_ik_drag_only_previews_until_the_user_keys_each_boundary(self):
        session = self.session()
        self.select('left_wrist')
        self.view.frame = 1
        original = points(session.skeleton,session.draft,.05)
        start,_,end = corrections.limb_chain(session.skeleton,'left_wrist')
        target = original[start]+.8*(original[end]-original[start])+[0,0,.04]
        session.move('left_wrist',target)
        session.commit()
        self.assertEqual(session.ik_spans,[])
        self.assertFalse(any(row.get('ik_key') for row in session.keys_map('left_wrist').values()))
        np.testing.assert_allclose(points(session.skeleton,session.draft,.05)[end],target,atol=1e-5)
        self.view._timeline_on_set_key_clicked()
        self.assertEqual(len(session.ik_spans),1)
        self.assertNotIn('end',session.ik_spans[0])
        self.view.frame = 3
        source = points(session.skeleton,session.base,.15)
        target = source[start]+.75*(source[end]-source[start])+[0,0,.08]
        session.move('left_wrist',target)
        session.commit()
        self.assertNotIn('end',session.ik_spans[0])
        self.view._timeline_on_set_key_clicked()
        self.assertIn('end',session.ik_spans[0])

    def test_fk_can_rotate_the_elbow_inside_a_saved_ik_range_and_reopen(self):
        session = self.session()
        self.make_ik_range(session)
        spans = deepcopy(session.ik_spans)
        session.set_ik(False)
        self.view.frame = 2
        wanted = Rotation.from_euler('xyz',(25,40,15),degrees=True)
        session.rotate('left_elbow',(25,40,15))
        session.commit()
        index = session.skeleton.joint_names.index('left_elbow')
        def local_rotation(current):
            pose = evaluate_rig_at_time(current.skeleton,current.draft,.1,loop=False)
            return Rotation.from_quat(pose.local_transforms[index].rotation)
        self.assertLess((local_rotation(session).inv()*wanted).magnitude(),1e-8)
        self.assertEqual(session.ik_spans,spans)
        restored = self.session()
        self.assertLess((local_rotation(restored).inv()*wanted).magnitude(),1e-8)

    def test_undo_redo_groups_a_joint_drag_and_routes_the_existing_actions(self):
        from echograph.ui import actions
        session = self.session()
        session.set_ik(False)
        self.view.frame = 2
        original = points(session.skeleton,session.draft,.1)
        self.view._xform_hist_start = None
        owner = self.view._mgl_scene_skeleton_joint_owner_token('Correction','left_elbow')
        GraphGLView._begin_xform_history(self.view,owner)
        for angle in (10,20,35):
            session.rotate('left_elbow',(0,angle,0))
        GraphGLView._commit_xform_history(self.view)
        changed = points(session.skeleton,session.draft,.1)
        self.assertEqual(len(session._undo),1)
        self.assertFalse(np.allclose(changed,original))
        with patch.object(actions,'_focus_is_text_input',return_value=False):
            self.assertTrue(actions.undo_scene_xform(self.window))
            np.testing.assert_allclose(points(session.skeleton,session.draft,.1),original,atol=1e-8)
            self.assertTrue(actions.redo_scene_xform(self.window))
            np.testing.assert_allclose(points(session.skeleton,session.draft,.1),changed,atol=1e-8)
        session.rotate('left_elbow',(0,50,0))
        self.assertEqual(session._redo,[])

    def test_ik_preview_can_be_undone_but_is_never_exported_without_a_key(self):
        session = self.session()
        self.view.frame = 2
        original = points(session.skeleton,session.draft,.1)
        start,_,end = corrections.limb_chain(session.skeleton,'left_wrist')
        target = original[start]+.7*(original[end]-original[start])+[0,0,.03]
        session.move('left_wrist',target)
        session.commit()
        moved = points(session.skeleton,session.draft,.1)
        self.assertTrue(session.undo())
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),original,atol=1e-8)
        self.assertTrue(session.redo())
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),moved,atol=1e-8)
        output = session.approve()
        loaded = ingest_bvh_animation_data(output)
        np.testing.assert_allclose(points(loaded.skeleton,loaded.clips[0],.1),original,atol=1e-5)

    def test_yellow_ik_bands_follow_actual_pairs_and_the_selected_chain(self):
        session = self.session()
        self.make_ik_range(session)
        self.select('left_elbow')
        self.assertEqual([pair[:2] for pair in self.view._timeline_ik_ranges()],[(1.,3.)])
        # The drawing fixture below maps local frames from zero.
        self.view._timeline_view_start = 0
        self.view._timeline_local_frame_to_tracks_x_float = lambda frame,clamp=False:30+40*frame
        self.view._timeline_axis_is_visible = lambda axis:True
        self.view._timeline_track_rows = []
        for axis in range(6):
            row = QtWidgets.QWidget(self.view)
            row.setGeometry(0,axis*14,250,12)
            self.view._timeline_track_rows.append(row)
        image = QtGui.QImage(250,90,QtGui.QImage.Format_ARGB32)
        image.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(image)
        self.view._timeline_paint_ik_ranges(painter)
        painter.end()
        self.assertGreater(image.pixelColor(110,48).alpha(),0)
        self.assertEqual(image.pixelColor(30,48).alpha(),0)
        self.assertEqual(image.pixelColor(190,48).alpha(),0)
        self.assertEqual(image.pixelColor(110,6).alpha(),0)
        self.view.frame = 2
        self.assertTrue(session.key_ik('left_wrist'))
        self.assertEqual([pair[:2] for pair in self.view._timeline_ik_ranges()],[(1.,2.),(2.,3.)])
        split_image = QtGui.QImage(image.size(),image.format())
        split_image.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(split_image)
        self.view._timeline_paint_ik_ranges(painter)
        painter.end()
        self.assertEqual(split_image,image,'Intermediate keys changed the band shading or added an internal border')
        curve_image = QtGui.QImage(image.size(),image.format())
        curve_image.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(curve_image)
        self.view._timeline_paint_ik_ranges(painter,QtCore.QRectF(0,0,250,90))
        painter.end()
        self.assertEqual(curve_image.pixelColor(110,48),curve_image.pixelColor(100,48))
        self.assertEqual(curve_image.pixelColor(100,48).alpha(),85)
        self.select('spine1')
        self.assertEqual(self.view._timeline_ik_ranges(),[])

    def test_explicit_ik_key_replaces_fk_overrides_for_its_limb_without_snapping(self):
        session = self.session()
        self.view.frame = 2
        session.set_ik(False)
        session.rotate('left_elbow',(0,30,0))
        session.commit()
        session.set_ik(True)
        original = points(session.skeleton,session.draft,.1)
        start,_,end = corrections.limb_chain(session.skeleton,'left_wrist')
        target = original[start]+.8*(original[end]-original[start])+[0,0,.03]
        session.move('left_wrist',target)
        session.commit()
        self.assertTrue(session.key_ik('left_wrist',mode='start'))
        self.assertNotIn('left_elbow',session.fk_keys)
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1)[end],target,atol=1e-5)

    def test_dragging_a_long_take_never_serializes_or_validates_the_whole_clip(self):
        context = self.context()
        source = context['clip']
        count = 1800
        tracks = [replace(track,rotation_keys=[QuatKeyframe(frame/20.,track.rotation_keys[0].value)
                  for frame in range(count)]) for track in source.tracks]
        clip = replace(source,tracks=tracks,end_time=(count-1)/20.)
        context['clip'] = clip
        session = CorrectionSession(self.item,context,self.view)
        session.open(self.window)
        self.addCleanup(session.finish)
        self.view.frame = 20
        evaluate_rig_at_time(session.skeleton,clip,1.,loop=False)
        session.set_ik(False)
        session.begin_edit()
        with (patch.object(AnimationClip,'to_dict',side_effect=AssertionError('full clip serialization')),
             patch.object(AnimationClip,'validate',side_effect=AssertionError('full clip validation')),
             patch.object(session,'rebuild',side_effect=AssertionError('full interval rebuild'))):
            for angle in range(12):
                session.rotate('left_elbow',(0,angle,0))
                evaluate_rig_at_time(session.skeleton,session.draft,1.,loop=False)
        session.commit()
        self.assertIs(next(track for track in session.draft.tracks if track.joint_name=='right_wrist'),
                      next(track for track in clip.tracks if track.joint_name=='right_wrist'))
        session.set_ik(True)
        original = points(session.skeleton,session.draft,1.)
        start,_,end = corrections.limb_chain(session.skeleton,'left_wrist')
        session.begin_edit()
        with (patch.object(AnimationClip,'to_dict',side_effect=AssertionError('full clip serialization')),
              patch.object(AnimationClip,'validate',side_effect=AssertionError('full clip validation')),
              patch.object(session,'rebuild',side_effect=AssertionError('full interval rebuild'))):
            for step in range(12):
                session.move('left_wrist',original[start]+.8*(original[end]-original[start])+[0,0,.001*step])
                evaluate_rig_at_time(session.skeleton,session.draft,1.,loop=False)
        session.commit()
        self.assertEqual(session.ik_spans,[])

    def test_retiming_long_ik_range_only_samples_preview_and_never_updates_graph(self):
        context = self.context()
        source = context['clip']
        count = 1800
        context['clip'] = replace(source,end_time=(count-1)/20.,tracks=[
            replace(track,rotation_keys=[QuatKeyframe(frame/20.,track.rotation_keys[0].value)
                    for frame in range(count)]) for track in source.tracks])
        session = CorrectionSession(self.item,context,self.view)
        session.open(self.window)
        self.addCleanup(session.finish)
        self.select('left_wrist')
        for frame,mode in ((1,'start'),(count-1,'end')):
            self.view.frame = frame
            self.assertTrue(session.key_ik('left_wrist',mode=mode))
        before = session.draft
        value = self.view._timeline_keys[count-1]['xyz'][0]
        self.view.frame = 500
        with patch.object(corrections,'apply_ik_spans',wraps=corrections.apply_ik_spans) as bake, \
                patch.object(corrections,'solve_ik_pose',wraps=corrections.solve_ik_pose) as solve, \
                patch.object(self.scene,'set_node_params') as graph, \
                patch.object(collection,'refresh_repair_records') as records, \
                patch.object(corrections,'save_document',wraps=corrections.save_document) as saved:
            self.view._timeline_drag_axis_key(0,count-1,count-2,value,commit=False)
            solve.assert_not_called()
            saved.assert_not_called()
            self.view._timeline_drag_axis_key(0,count-2,count-2,value,commit=True)
            graph.assert_not_called()
            records.assert_not_called()
            saved.assert_called_once()
            self.assertTrue(bake.called)
            self.assertTrue(all(call.kwargs.get('sample_time') is not None for call in bake.call_args_list))
            self.assertLessEqual(solve.call_count,8)
            # Scrubbing samples a small interpolation neighborhood, not 3600.
            solve.reset_mock()
            points(session.skeleton,session.draft,25.)
            self.assertLessEqual(solve.call_count,8)
            solve.reset_mock()
            points(session.skeleton,session.draft,25.)
            solve.assert_not_called()
        after = session.draft
        self.assertTrue(session.undo())
        self.assertIs(session.draft,before)
        self.assertTrue(session.redo())
        self.assertIs(session.draft,after)
        self.assertAlmostEqual(corrections.read_document(session.path)['ik_spans'][0]['end']['time'],(count-2)/20.)

    def test_on_demand_preview_matches_full_bake_and_fk_edits_preserve_ik(self):
        session = self.session()
        self.make_ik_range(session)
        for time in (0.,.05,.075,.1,.125,.15,.2):
            expected = corrections.apply_ik_spans(session.skeleton,session.manual,session.ik_spans,session.fps)
            np.testing.assert_allclose(points(session.skeleton,session.draft,time),
                                       points(session.skeleton,expected,time),atol=1e-8)
        session.set_ik(False)
        self.view.frame = 2
        session.key_rotations({'right_elbow':Rotation.from_euler('x',35,degrees=True).as_quat()})
        expected = corrections.apply_document(session.skeleton,session.base,session.document())
        for time in (.05,.1,.15):
            np.testing.assert_allclose(points(session.skeleton,session.draft,time),
                                       points(session.skeleton,expected,time),atol=1e-8)

    def test_joint_selection_preserves_the_preview_clock_for_a_resampled_limiter_clip(self):
        context = self.context()
        metadata = dict(context['clip'].metadata,
                        rig_limiter=dict(source_sample_rate_hz=30.,sample_rate_hz=120.,repair_substeps=4))
        context['clip'] = replace(context['clip'],end_time=60.,sample_rate_hz=120.,metadata=metadata)
        self.view._timeline_fps = 30.
        self.view.frame = 1149
        expected_time = self.view._mgl_fbx_context_timeline_sample_seconds(context,'Limiter')
        session = CorrectionSession(self.item,context,self.view)
        session.open(self.window)
        self.addCleanup(session.finish)
        self.assertAlmostEqual(session.time(),expected_time)
        self.assertEqual(self.view._timeline_fps,30.)
        self.assertEqual(self.view.frame,1149)
        for joint in ('left_elbow','left_wrist','right_ankle','pelvis'):
            self.select(joint)
            self.assertAlmostEqual(session.time(),expected_time)
            self.assertEqual(self.view._timeline_fps,30.)
            self.assertEqual(self.view._timeline_total_max,1800)
        # Changing which joint owns the keys must not reload a clip sample rate,
        # or reset a playback rate the user deliberately changed in the viewport.
        self.view._timeline_fps = 24.
        self.select('left_wrist')
        self.assertEqual(self.view._timeline_fps,24.)
        self.assertAlmostEqual(session.time(),1149/24.)

    def test_runtime_rotation_overrides_preserve_source_and_reject_invalid_keys(self):
        from echograph.rigging.fbx_stage5_evaluator import override_rotation_keys, FBXEvaluatorError
        skeleton,clip = fixture()
        before = clip.to_dict()
        updated = override_rotation_keys(skeleton,clip,{'left_elbow':{.1:(0.,.5,0.,.8660254038)}})
        self.assertEqual(clip.to_dict(),before)
        self.assertIsNot(updated,clip)
        self.assertIs(next(track for track in updated.tracks if track.joint_name=='right_wrist'),
                      next(track for track in clip.tracks if track.joint_name=='right_wrist'))
        for entries in ({'left_elbow':{100.:(0,0,0,1)}},
                        {'missing_joint':{.1:(0,0,0,1)}},
                        {'left_elbow':{.1:(0,0,0)}}):
            with self.assertRaises((ValueError,FBXEvaluatorError)):
                override_rotation_keys(skeleton,clip,entries)

    def test_start_end_ik_remember_the_highlighted_position_axis_and_keep_source_keys(self):
        session = self.session()
        self.select('left_wrist')
        source = session.keys_map('left_wrist',include_ik=False)
        self.view._timeline_curve_axes_filter = {1}  # Py
        self.view.frame = 1
        self.assertTrue(session.key_ik('left_wrist',mode='start'))
        self.assertEqual(session.ik_spans[0]['position_mask'],[False,True,False])
        # Completing a range cannot accidentally switch its channel.
        self.view._timeline_curve_axes_filter = {0}
        self.view.frame = 3
        self.assertTrue(session.key_ik('left_wrist',mode='end'))
        self.assertEqual(session.ik_spans[0]['position_mask'],[False,True,False])
        rows = session.keys_map('left_wrist')
        self.assertEqual(rows[2],source[2])
        self.assertEqual(rows[1]['ik_axis_mask'],[False,True,False,False,False,False])
        self.assertTrue(self.view._timeline_key_is_ik(1,1))
        self.assertFalse(self.view._timeline_key_is_ik(1,3))
        self.assertEqual(self.view._timeline_ik_ranges(include_axes=True)[0][3],
                         [False,True,False,False,False,False])
        session.save()
        restored = self.session()
        self.assertEqual(restored.ik_spans,session.ik_spans)
        self.assertEqual(restored.keys_map('left_wrist'),rows)

    def test_axis_ik_keeps_source_channel_edits_at_a_shared_boundary_frame(self):
        session = self.session()
        self.select('left_wrist')
        self.view._timeline_curve_axes_filter = {1}
        self.make_ik_range(session)
        rows = session.keys_map('left_wrist')
        rows[1]['rxyz'][0] = 25.
        self.assertTrue(session.apply_keys('left_wrist',rows,20.))
        self.assertEqual(len(session.ik_spans),1)
        self.assertEqual(session.ik_spans[0]['position_mask'],[False,True,False])
        self.assertAlmostEqual(session.keys_map('left_wrist',include_ik=False)[1]['rxyz'][0],25.)
        self.assertAlmostEqual(session.keys_map('left_wrist')[1]['rxyz'][0],25.)
        # Refining the range with another key retains its saved axis mask.
        self.view._timeline_curve_axes_filter = set()
        self.view.frame = 2
        self.assertTrue(session.key_ik('left_wrist'))
        self.assertEqual([span['position_mask'] for span in session.ik_spans],[[False,True,False]]*2)

    def test_pelvis_position_axes_have_independent_overlapping_ranges_and_shared_boundary_frames(self):
        session = self.session()
        session.lock_limbs = False
        self.select('pelvis')
        index = session.skeleton.joint_names.index('pelvis')
        for axis,frames,values in ((1,(0,4),(.8,.9)),(0,(1,3),(.03,.06)),(2,(1,3),(.02,.04))):
            self.view._timeline_curve_axes_filter = {axis}
            for frame,value,mode in zip(frames,values,('start','end')):
                self.view.frame = frame
                target = points(session.skeleton,session.base,frame/20.)[index].copy()
                target[axis] = value
                self.assertTrue(session.key_ik('pelvis',mode=mode,target=target,save=True),session.panel.status.text())
        self.assertEqual([span['position_mask'] for span in session.ik_spans],
                         [[False,True,False],[True,False,False],[False,False,True]])
        rows = session.keys_map('pelvis')
        for frame,label in ((1,'S'),(3,'E')):
            self.assertEqual(rows[frame]['ik_axis_mask'],[True,False,True,False,False,False])
            self.assertNotEqual(rows[frame]['ik_axis_keys'][0],rows[frame]['ik_axis_keys'][2])
            self.assertEqual(self.view._timeline_ik_boundary_label(frame,0),label)
            self.assertEqual(self.view._timeline_ik_boundary_label(frame,2),label)
        baked = corrections.apply_document(session.skeleton,session.base,session.document())
        for frame in (0,.5,1,1.5,2,2.5,3,3.5,4):
            time = frame/20.
            expected = points(session.skeleton,session.base,time)[index].copy()
            expected[1] = np.interp(frame,[0,4],[.8,.9])
            if 1 <= frame <= 3:
                expected[0] = np.interp(frame,[1,3],[.03,.06])
                expected[2] = np.interp(frame,[1,3],[.02,.04])
            np.testing.assert_allclose(points(session.skeleton,session.draft,time)[index],expected,atol=1e-7)
            np.testing.assert_allclose(points(session.skeleton,baked,time)[index],expected,atol=1e-7)
        restored = self.session()
        self.assertEqual(restored.ik_spans,session.ik_spans)
        self.assertEqual(restored.keys_map('pelvis'),rows)

    def test_shared_axis_ik_keys_edit_move_and_delete_without_changing_other_axes(self):
        session = self.session()
        session.lock_limbs = False
        self.select('pelvis')
        for axis in (1,0,2):
            self.view._timeline_curve_axes_filter = {axis}
            for frame,mode in ((1,'start'),(3,'end')):
                self.view.frame = frame
                self.assertTrue(session.key_ik('pelvis',mode=mode),session.panel.status.text())
        original = deepcopy(session.ik_spans)
        self.view._timeline_curve_selected = {(0,1)}
        self.assertEqual(self.view._timeline_selected_ik_key_ids(),{original[1]['start']['id']})
        self.assertTrue(self.view._timeline_set_axis_handle_value(0,1,'out',.4,.03,commit=True))
        self.assertEqual(session.ik_spans[0],original[0])
        self.assertEqual(session.ik_spans[2],original[2])
        self.assertEqual(self.view._timeline_drag_selected_keys(-1,commit=True),-1)
        self.assertEqual(session.ik_spans[1]['start']['time'],0.)
        self.assertEqual(session.ik_spans[0],original[0])
        self.assertEqual(session.ik_spans[2],original[2])
        self.assertTrue(session.undo())
        self.view._timeline_curve_selected = {(0,3)}
        self.assertTrue(self.view.timeline_delete_selected_keys())
        self.assertEqual([span for span in session.ik_spans if span['position_mask'] != [True,False,False]],
                         [original[0],original[2]])
        self.assertFalse(any(span['position_mask'] == [True,False,False] for span in session.ik_spans))
        self.assertTrue(session.undo())
        self.assertEqual(len(session.ik_spans),3)

    def test_pending_position_axes_and_joining_refining_one_axis_leave_other_ranges_intact(self):
        session = self.session()
        session.lock_limbs = False
        self.select('pelvis')
        # Separate pending starts must be completed on their own axes.
        for axis,frame in ((1,0),(0,0)):
            self.view._timeline_curve_axes_filter = {axis}
            self.view.frame = frame
            self.assertTrue(session.key_ik('pelvis',mode='start'))
        self.view.frame = 1
        self.assertTrue(session.key_ik('pelvis',mode='end'))
        self.view._timeline_curve_axes_filter = {1}
        self.view.frame = 4
        self.assertTrue(session.key_ik('pelvis',mode='end'))
        y_range = deepcopy(session.ik_spans[0])
        self.view._timeline_curve_axes_filter = {0}
        for frame,mode in ((3,'start'),(4,'end')):
            self.view.frame = frame
            self.assertTrue(session.key_ik('pelvis',mode=mode))
        x_ranges = [span for span in session.ik_spans if span['position_mask'][0]]
        self.view._timeline_curve_selected = {(0,1),(0,3)}
        self.assertTrue(self.view._timeline_join_selected_ik_ranges())
        self.assertEqual(session.ik_spans[0],y_range)
        self.view.frame = 2
        self.view._timeline_on_set_key_clicked()
        self.assertEqual(session.ik_spans[0],y_range)
        self.assertEqual(len(session.ik_spans),5)
        self.assertEqual(self.view._timeline_ik_boundary_label(2,0),'')
        self.assertEqual(self.view._timeline_ik_boundary_label(4,1),'E')
        self.assertEqual({key['id'] for span in x_ranges for key in (span['start'],span['end'])}-
                         {key['id'] for span in session.ik_spans for key in (span['start'],span['end'])},set())

    def test_start_ik_supports_multiple_position_channels_and_rejects_rotation_only_selection(self):
        session = self.session()
        self.select('left_wrist')
        self.view._timeline_curve_axes_filter = {3}
        self.assertFalse(session.key_ik('left_wrist',mode='start'))
        self.assertEqual(session.ik_spans,[])
        self.assertIn('Px, Py or Pz',session.panel.status.text())
        self.view._timeline_curve_axes_filter = {0,2}
        self.assertTrue(session.key_ik('left_wrist',mode='start'))
        self.assertEqual(session.ik_spans[0]['position_mask'],[True,False,True])

    def test_ik_boundaries_key_full_chain_and_hide_source_keys_only_inside_range(self):
        session = self.session()
        self.make_ik_range(session)
        self.assertEqual(len(session.ik_spans),1)
        chain = corrections.limb_chain(session.skeleton,'left_wrist')
        for index in chain:
            name = session.skeleton.joints[index].name
            self.select(name)
            rows = self.view._timeline_keys
            self.assertEqual(set(rows),{0,1,3,4})
            self.assertEqual({frame for frame,row in rows.items() if row.get('ik_key')},{1,3})
            self.assertTrue(self.view._timeline_key_is_ik(1))
            self.assertFalse(self.view._timeline_key_is_ik(0))
            track = next(track for track in session.draft.tracks if track.joint_name == name)
            self.assertTrue(any(abs(key.time-.1)<1e-8 for key in track.rotation_keys))
        self.select('spine1')
        self.assertEqual(set(self.view._timeline_keys),{0,1,2,3,4})
        self.assertFalse(any(row.get('ik_key') for row in self.view._timeline_keys.values()))

    def test_ik_curve_handles_survive_save_refresh_rekey_and_reopening(self):
        session = self.session()
        self.make_ik_range(session)
        self.select('left_wrist')
        manual = session.manual
        self.assertTrue(self.view._timeline_set_axis_handle_value(0,1,'out',.4,.03,commit=True))
        handles = self.view._timeline_axis_handles_for_key(0,1)
        np.testing.assert_allclose(handles['out'],[.4,.03])
        session.refresh_timeline()
        np.testing.assert_allclose(self.view._timeline_axis_handles_for_key(0,1)['out'],[.4,.03])
        self.assertIs(session.manual,manual)
        self.assertTrue(session.undo())
        self.assertNotIn('0',self.view._timeline_keys[1].get('curve_handles',{}))
        self.assertTrue(session.redo())
        np.testing.assert_allclose(self.view._timeline_axis_handles_for_key(0,1)['out'],[.4,.03])
        self.view.frame = 1
        self.assertTrue(session.key_ik('left_wrist',save=True))
        np.testing.assert_allclose(self.view._timeline_axis_handles_for_key(0,1)['out'],[.4,.03])
        restored = self.session()
        self.select('left_wrist')
        np.testing.assert_allclose(self.view._timeline_axis_handles_for_key(0,1)['out'],[.4,.03])
        doubled = restored.keys_map('left_wrist',fps=40.)
        np.testing.assert_allclose(doubled[2]['curve_handles']['0']['out'],[.8,.03])

    def test_ik_curve_handles_change_the_interpolated_target_in_preview_and_export(self):
        session = self.session()
        first,last = self.make_ik_range(session)
        self.select('left_wrist')
        for frame in (1,3):
            self.view._timeline_set_entry_axis_handles(self.view._timeline_keys[frame],0,frame,'untied',
                (-.5,0.),(.5,.04) if frame == 1 else (.5,0.))
        self.view._timeline_save_to_disk()
        expected = (first+last)*.5
        # Midpoint Hermite contribution: h10(.5) * 2 frames * (.04/.5).
        expected[0] += .02
        index = session.skeleton.joint_names.index('left_wrist')
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1)[index],expected,atol=1e-6)
        baked = corrections.apply_document(session.skeleton,session.base,session.document())
        np.testing.assert_allclose(points(session.skeleton,baked,.1)[index],expected,atol=1e-6)
        output = session.approve()
        self.assertTrue(output)
        exported = ingest_bvh_animation_data(output)
        target_index = exported.skeleton.joint_names.index('left_wrist')
        np.testing.assert_allclose(points(exported.skeleton,exported.clips[0],.1)[target_index],expected,atol=1e-5)

    def test_vertical_ik_tangents_and_untied_sides_survive_save_reopen_retime_and_bake(self):
        session = self.session()
        self.make_ik_range(session)
        self.select('left_wrist')
        self.view._timeline_curve_selected = {(0,1)}
        self.assertTrue(self.view._timeline_set_axis_handle_value(0,1,'out',0.,.02,commit=True))
        vertical = self.view._timeline_axis_handles_for_key(0,1)
        self.assertEqual(vertical['out'][0],0.)
        self.assertEqual(vertical['weighted'],['in','out'])
        self.view._timeline_on_handle_untied_clicked()
        self.assertTrue(self.view._timeline_set_axis_handle_value(0,1,'out',0.,.03,commit=True))
        independent = self.view._timeline_axis_handles_for_key(0,1)
        self.assertEqual(independent['in'],vertical['in'])
        self.assertEqual(independent['mode'],'untied')
        restored = self.session()
        self.assertEqual(restored.keys_map('left_wrist')[1]['curve_handles']['0']['weighted'],['in','out'])
        self.select('left_wrist')
        index = session.skeleton.joint_names.index('left_wrist')
        baked = corrections.apply_document(session.skeleton,session.base,session.document())
        for time in (.05,.075,.1,.125,.15):
            expected = self.view._timeline_eval_axis_curve(0,time*20.)
            for clip in (session.draft,restored.draft,baked):
                self.assertAlmostEqual(points(session.skeleton,clip,time)[index,0],expected,places=5)
        self.view._timeline_curve_selected = {(0,1)}
        self.assertEqual(self.view._timeline_drag_selected_keys(1,commit=True),1)
        retimed = restored.keys_map('left_wrist')[2]['curve_handles']['0']
        self.assertEqual(retimed['out'][0],0.)
        self.assertEqual(retimed['mode'],'untied')
        self.assertEqual(retimed['weighted'],['in','out'])

    def test_overlapping_and_long_ik_handles_reopen_and_match_curve_preview_and_bake(self):
        session = self.session()
        self.make_ik_range(session)
        self.select('left_wrist')
        for frame,side,dx,dy in ((1,'out',1.5,8.),(3,'in',-1.5,-6.)):
            self.view._timeline_curve_selected = {(5,frame)}
            self.view._timeline_on_handle_untied_clicked()
            self.assertTrue(self.view._timeline_set_axis_handle_value(5,frame,side,dx,dy,commit=True))
        restored = self.session()
        self.select('left_wrist')
        np.testing.assert_allclose(restored.keys_map('left_wrist')[1]['curve_handles']['5']['out'],[1.5,8.],atol=1e-12)
        np.testing.assert_allclose(restored.keys_map('left_wrist')[3]['curve_handles']['5']['in'],[-1.5,-6.],atol=1e-12)
        baked = corrections.apply_document(session.skeleton,session.base,session.document())
        index = session.skeleton.joint_names.index('left_wrist')
        for frame in (1.,1.5,2.,2.5,3.):
            expected = self.view._timeline_eval_axis_curve(5,frame)
            for clip in (session.draft,restored.draft,baked):
                quaternion = evaluate_rig_at_time(session.skeleton,clip,frame/20.,loop=False).local_transforms[index].rotation
                self.assertAlmostEqual(Rotation.from_quat(quaternion).as_euler('xyz',degrees=True)[2],expected,places=6)
        for frame,side,dx in ((1,'out',25.),(3,'in',-30.)):
            self.assertTrue(self.view._timeline_set_axis_handle_value(5,frame,side,dx,8.,commit=True))
        reopened = self.session()
        for frame,side,dx in ((1,'out',25.),(3,'in',-30.)):
            np.testing.assert_allclose(reopened.keys_map('left_wrist')[frame]['curve_handles']['5'][side],[dx,8.],atol=1e-12)

    def test_rekeying_ik_end_restores_chain_rotation_endpoints_and_does_not_resurrect_fk_after_a_drag(self):
        session = self.separated_ik_session()
        self.view.frame = 3
        session.key_fk('left_shoulder',axes=[3,4,5],rotation=Rotation.from_euler('xyz',(12,23,34),degrees=True).as_quat(),save=True)
        self.select('left_shoulder')
        self.assertFalse(self.view._timeline_key_is_ik(3,3))
        self.select('left_wrist')
        self.view._timeline_curve_axes_filter = set()
        self.view._timeline_on_set_key_clicked()
        self.assertNotIn('left_shoulder',session.fk_keys)
        self.select('left_shoulder')
        self.assertEqual(self.view._timeline_ik_boundary_label(3,3),'E')
        self.view._timeline_curve_selected = {(3,3)}
        self.view._timeline_drag_selected_keys(1,commit=True)
        self.assertNotIn('left_shoulder',session.fk_keys)
        self.assertEqual(self.view._timeline_ik_boundary_label(4,3),'E')

    def test_shared_ik_and_fk_boundary_keeps_both_channels_curve_handles(self):
        session = self.session()
        self.select('left_wrist')
        self.view._timeline_curve_axes_filter = {1}
        self.make_ik_range(session)
        self.assertTrue(self.view._timeline_set_axis_handle_value(3,1,'out',.25,2.,commit=True))
        self.assertTrue(self.view._timeline_set_axis_handle_value(1,1,'out',.35,.04,commit=True))
        np.testing.assert_allclose(self.view._timeline_axis_handles_for_key(3,1)['out'],[.25,2.])
        np.testing.assert_allclose(self.view._timeline_axis_handles_for_key(1,1)['out'],[.35,.04])
        self.assertEqual(set(session.ik_spans[0]['start']['curve_handles']['left_wrist']),{'1'})
        self.assertNotIn('1',session.keys_map('left_wrist',include_ik=False)[1].get('curve_handles',{}))
        self.session()
        self.select('left_wrist')
        np.testing.assert_allclose(self.view._timeline_axis_handles_for_key(3,1)['out'],[.25,2.])
        np.testing.assert_allclose(self.view._timeline_axis_handles_for_key(1,1)['out'],[.35,.04])

    def test_ik_rotation_handles_change_orientation_without_moving_the_target(self):
        session = self.session()
        self.make_ik_range(session)
        self.select('left_wrist')
        before = evaluate_rig_at_time(session.skeleton,session.draft,.1,loop=False)
        for frame in (1,3):
            self.view._timeline_set_entry_axis_handles(self.view._timeline_keys[frame],5,frame,'untied',
                (-.5,0.),(.5,2.) if frame == 1 else (.5,0.))
        self.view._timeline_save_to_disk()
        after = evaluate_rig_at_time(session.skeleton,session.draft,.1,loop=False)
        index = session.skeleton.joint_names.index('left_wrist')
        old,new = [np.asarray(pose.global_matrices[index]).reshape(4,4) for pose in (before,after)]
        np.testing.assert_allclose(new[:3,3],old[:3,3],atol=1e-7)
        delta = Rotation.from_matrix(new[:3,:3])*Rotation.from_matrix(old[:3,:3]).inv()
        self.assertAlmostEqual(np.degrees(delta.magnitude()),1.,places=5)

    def test_native_ik_set_key_creates_start_and_end_and_scrubbing_does_not_edit_them(self):
        session = self.session()
        self.select('left_wrist')
        self.view.frame = 1
        self.view._timeline_on_set_key_clicked()
        self.assertNotIn('end',session.ik_spans[0])
        self.view.frame = 3
        self.view._timeline_on_set_key_clicked()
        self.assertIn('end',session.ik_spans[0])
        before = deepcopy(session.document())
        for frame in (0,1,2,3,4):
            self.view.frame = frame
            self.view._timeline_apply_frame_if_keyed(frame,force=True)
        self.assertEqual(session.document(),before)

    def separated_ik_session(self,*,duration=.6):
        context = self.context()
        source = context['clip']
        context['clip'] = replace(source,end_time=duration,tracks=[replace(track,rotation_keys=[
            replace(track.rotation_keys[frame%len(track.rotation_keys)],time=frame/20.)
            for frame in range(13)]) for track in source.tracks])
        session = CorrectionSession(self.item,context,self.view)
        session.open(self.window)
        self.addCleanup(session.finish)
        self.select('left_wrist')
        session.select('left_wrist')
        for frame,mode in ((1,'start'),(3,'end'),(7,'start'),(9,'end')):
            self.view.frame = frame
            start,_,end = corrections.limb_chain(session.skeleton,'left_wrist')
            source = points(session.skeleton,session.base,frame/20.)
            target = source[start]+.7*(source[end]-source[start])+[0.,.01*frame,.015*frame]
            pole = source[start]+[0.,.4,.04*frame]
            quaternion = Rotation.from_euler('xyz',(4*frame,frame,-frame),degrees=True).as_quat()
            self.assertTrue(session.key_ik('left_wrist',mode=mode,target=target,
                                         rotations={'left_wrist':quaternion},pole=pole))
        return session

    def test_ik_boundary_labels_follow_joining_deletion_and_undo_without_labeling_intermediate_keys(self):
        session = self.separated_ik_session()
        view = self.view
        def labels():
            return {frame:view._timeline_ik_boundary_label(frame,0) for frame in (1,3,7,9)}
        self.assertEqual(labels(),{1:'S',3:'E',7:'S',9:'E'})
        self.assertIs(view._timeline_ik_boundary_labels(),view._timeline_ik_boundary_labels())
        self.assertEqual(view._timeline_ik_boundary_label(0,0),'')
        left,right = session.ik_spans
        self.assertTrue(session.join_ik_ranges({left['end']['id'],right['start']['id']}))
        self.assertEqual(labels(),{1:'S',3:'',7:'',9:'E'})
        view._timeline_curve_selected = {(0,9)}
        self.assertTrue(view.timeline_delete_selected_keys())
        self.assertEqual(labels(),{1:'S',3:'',7:'E',9:''})
        self.assertTrue(session.undo())
        self.assertEqual(labels(),{1:'S',3:'',7:'',9:'E'})
        self.assertTrue(session.undo())
        self.assertEqual(labels(),{1:'S',3:'E',7:'S',9:'E'})

    def test_pending_and_axis_specific_ik_keys_label_only_their_ik_channels(self):
        session = self.session()
        self.select('left_wrist')
        self.view._timeline_curve_axes_filter = {1}
        self.view.frame = 1
        self.assertTrue(session.key_ik('left_wrist',mode='start'))
        self.assertEqual(self.view._timeline_ik_boundary_label(1,1),'S')
        self.assertEqual(self.view._timeline_ik_boundary_label(1,3),'')
        self.view.frame = 3
        self.assertTrue(session.key_ik('left_wrist',mode='end'))
        self.assertEqual(self.view._timeline_ik_boundary_label(3,1),'E')
        self.assertEqual(self.view._timeline_ik_boundary_label(3,3),'')
        self.view._timeline_curve_selected = {(1,3)}
        self.assertTrue(self.view.timeline_delete_selected_keys())
        self.assertEqual(self.view._timeline_ik_boundary_label(1,1),'')
        self.assertFalse(self.view._timeline_ik_boundary_labels())

    def test_fuse_button_sits_between_ik_buttons_and_tracks_joinable_selection_with_cached_validation(self):
        self.view.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        self.view.show()
        session = self.separated_ik_session()
        APP.processEvents()
        panel = session.panel
        button = panel.join_ik_button
        self.assertLess(panel.ik_buttons[0].geometry().right(),button.geometry().left())
        self.assertLess(button.geometry().right(),panel.ik_buttons[1].geometry().left())
        self.assertFalse(button.isEnabled())
        self.assertFalse(button.icon().isNull())
        normal = button.icon().pixmap(24,24,QtGui.QIcon.Normal).toImage()
        disabled = button.icon().pixmap(24,24,QtGui.QIcon.Disabled).toImage()
        colors = [normal.pixelColor(x,y) for y in range(normal.height()) for x in range(normal.width())]
        grays = [disabled.pixelColor(x,y) for y in range(disabled.height()) for x in range(disabled.width())]
        self.assertTrue(any(color.alpha() and max(color.red(),color.green(),color.blue())-
                            min(color.red(),color.green(),color.blue()) > 20 for color in colors))
        self.assertTrue(any(color.alpha() for color in grays))
        self.assertTrue(all(color.red() == color.green() == color.blue() for color in grays if color.alpha()))
        self.assertEqual(normal.pixelColor(0,0).alpha(),disabled.pixelColor(0,0).alpha())
        self.view._timeline_curve_selected = {(0,3),(0,7)}
        self.view._timeline_update_handle_mode_buttons()
        self.assertTrue(button.isEnabled())
        with patch.object(corrections,'ik_gap_pair',side_effect=AssertionError('Revalidated unchanged selection')), \
                patch.object(session,'rebuild',side_effect=AssertionError('Rebuilt a preview to refresh a button')):
            for _ in range(10):
                self.view._timeline_update_handle_mode_buttons()
                panel.sync_join_button()
        for selected in ({(0,3)},{(0,1),(0,7)},{(3,0),(3,12)},set()):
            self.view._timeline_curve_selected = selected
            self.view._timeline_update_handle_mode_buttons()
            self.assertFalse(button.isEnabled())
        self.assertTrue(panel.rect().contains(QtCore.QRect(button.mapTo(panel,QtCore.QPoint()),button.size())))

    def test_fuse_button_uses_existing_join_action_keeps_inner_keys_and_updates_after_undo_redo(self):
        session = self.separated_ik_session()
        self.select('left_elbow')
        self.view._timeline_curve_selected = {(3,3),(4,7)}
        self.view._timeline_update_handle_mode_buttons()
        button = session.panel.join_ik_button
        self.assertTrue(button.isEnabled())
        left,right = deepcopy(session.ik_spans)
        with patch.object(self.view,'_timeline_join_selected_ik_ranges',wraps=self.view._timeline_join_selected_ik_ranges) as action, \
                patch.object(corrections,'save_document',wraps=corrections.save_document) as saved:
            button.click()
            action.assert_called_once_with()
            saved.assert_called_once()
        self.assertEqual(len(session.ik_spans),3)
        self.assertEqual(session.ik_spans[1]['start'],left['end'])
        self.assertEqual(session.ik_spans[1]['end'],right['start'])
        self.assertEqual(self.view._timeline_curve_selected,{(3,3),(4,7)})
        self.assertFalse(button.isEnabled())
        self.assertTrue(session.undo())
        self.assertTrue(button.isEnabled())
        self.assertTrue(session.redo())
        self.assertFalse(button.isEnabled())

    def test_fuse_button_rejects_incompatible_channels_and_selection_from_another_owner(self):
        session = self.separated_ik_session()
        self.select('left_elbow')
        self.view._timeline_curve_selected = {(3,3),(3,7)}
        self.view._timeline_update_handle_mode_buttons()
        button = session.panel.join_ik_button
        self.assertTrue(button.isEnabled())
        spans = deepcopy(session.ik_spans)
        spans[1]['position_mask'] = [True,False,False]
        session.rebuild(spans=spans)
        self.assertFalse(button.isEnabled())
        self.assertIn('same channels',button.toolTip())
        self.assertTrue(session.undo())
        self.assertTrue(button.isEnabled())
        self.view._timeline_owner_name = 'Another owner'
        self.view._timeline_update_handle_mode_buttons()
        self.assertFalse(button.isEnabled())

    def test_join_ik_ranges_keeps_every_key_pose_and_handle_and_fills_only_the_gap(self):
        session = self.separated_ik_session()
        spans = deepcopy(session.ik_spans)
        handle = dict(mode='untied',**{'in':[-.02,-.001],'out':[.02,.001]})
        for key in (spans[0]['end'],spans[1]['start']):
            key['curve_handles'] = {'left_wrist':{'0':deepcopy(handle)}}
        session.rebuild(spans=spans)
        left,right = deepcopy(session.ik_spans)
        before = {time:points(session.skeleton,session.draft,time) for time in
                  (0.,.05,.1,.15,.35,.4,.45,.5,.6)}
        manual,tracks,rows = session.manual,session.tracks,session.rows
        self.select('left_elbow')  # Selecting one chain rotation identifies the whole control key.
        self.view._timeline_curve_selected = {(3,3),(4,7)}
        selected = set(self.view._timeline_curve_selected)
        with patch.object(self.scene,'set_node_params') as graph, \
                patch.object(collection,'refresh_repair_records') as records, \
                patch.object(corrections,'save_document',wraps=corrections.save_document) as saved:
            self.assertTrue(self.view._timeline_join_selected_ik_ranges())
            saved.assert_called_once()
            graph.assert_not_called()
            records.assert_not_called()
        self.assertIs(session.manual,manual)
        self.assertIs(session.tracks,tracks)
        self.assertIs(session.rows,rows)
        self.assertEqual(self.view._timeline_curve_selected,selected)
        self.assertEqual(session.ik_spans[0],left)
        self.assertEqual(session.ik_spans[2],right)
        self.assertEqual(session.ik_spans[1]['start'],left['end'])
        self.assertEqual(session.ik_spans[1]['end'],right['start'])
        self.assertEqual({frame for frame,row in session.keys_map('left_wrist').items() if row.get('ik_key')},
                         {1,3,7,9})
        for time,expected in before.items():
            np.testing.assert_allclose(points(session.skeleton,session.draft,time),expected,atol=1e-8)
        endpoint = session.skeleton.joint_names.index('left_wrist')
        orientation = Slerp([.15,.35],Rotation.from_quat([
            left['end']['rotations']['left_wrist'],right['start']['rotations']['left_wrist']]))
        for time in (.2,.25,.3):
            expected = [corrections._ik_curve_value(left['end'],right['start'],'left_wrist',axis,time,
                        left['end']['target'][axis],right['start']['target'][axis]) for axis in range(3)]
            np.testing.assert_allclose(points(session.skeleton,session.draft,time)[endpoint],expected,atol=1e-7)
            pose = evaluate_rig_at_time(session.skeleton,session.draft,time,loop=False)
            actual = Rotation.from_quat(pose.local_transforms[endpoint].rotation)
            np.testing.assert_allclose(actual.as_matrix(),orientation([time]).as_matrix()[0],atol=1e-7)
            alpha = (time-.15)/.2
            expected_pole = (1-alpha)*np.asarray(left['end']['pole'])+alpha*np.asarray(right['start']['pole'])
            np.testing.assert_allclose(corrections.ik_pole_at_time(session.skeleton,session.manual,
                session.draft,session.ik_spans,'left_wrist',time),expected_pole,atol=1e-7)
        self.assertEqual(corrections.read_document(session.path)['ik_spans'],session.ik_spans)
        after = deepcopy(session.document())
        self.assertTrue(session.undo())
        self.assertEqual(session.ik_spans,[left,right])
        self.assertTrue(session.redo())
        self.assertEqual(session.document(),after)
        restored = CorrectionSession(self.item,session.context,self.view)
        restored.open(self.window)
        self.addCleanup(restored.finish)
        self.assertEqual(restored.ik_spans,session.ik_spans)
        output = restored.approve()
        self.assertTrue(output)
        loaded = ingest_bvh_animation_data(output)
        for time in (.05,.15,.2,.25,.3,.35,.45,.5):
            np.testing.assert_allclose(points(loaded.skeleton,loaded.clips[0],time),
                                       points(session.skeleton,session.draft,time),atol=2e-5)

    def test_joining_a_ten_minute_ik_gap_only_samples_the_preview_not_the_entire_interval(self):
        session = self.separated_ik_session(duration=600.)
        spans = deepcopy(session.ik_spans)
        spans[1]['start']['time'] = 599.7
        spans[1]['end']['time'] = 599.9
        session.rebuild(spans=spans)
        self.select('left_wrist')
        with patch.object(corrections,'apply_ik_spans',wraps=corrections.apply_ik_spans) as apply, \
                patch.object(corrections,'solve_ik_pose',wraps=corrections.solve_ik_pose) as solve:
            self.assertTrue(session.join_ik_ranges({spans[0]['end']['id'],spans[1]['start']['id']}))
            self.assertLess(solve.call_count,80)
            self.assertTrue(apply.call_args_list)
            self.assertTrue(all(call.kwargs.get('sample_time') is not None for call in apply.call_args_list))

    def test_joined_ik_band_has_continuous_yellow_fill_without_internal_edges(self):
        session = self.separated_ik_session()
        self.view._timeline_view_start = 0
        self.view._timeline_local_frame_to_tracks_x_float = lambda frame,clamp=False:30+40*frame
        def draw():
            image = QtGui.QImage(600,90,QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(image)
            self.view._timeline_paint_ik_ranges(painter,QtCore.QRectF(0,0,600,90))
            painter.end()
            return image
        self.assertEqual(draw().pixelColor(230,45).alpha(),0)
        self.view._timeline_curve_selected = {(0,3),(0,7)}
        self.assertTrue(self.view._timeline_join_selected_ik_ranges())
        image = draw()
        for frame in (2,3,5,7,8):
            self.assertEqual(image.pixelColor(30+40*frame,45),image.pixelColor(110,45))
        self.assertEqual(image.pixelColor(230,45).alpha(),85)
        self.assertEqual(image.pixelColor(30,45).alpha(),0)
        self.assertEqual(image.pixelColor(430,45).alpha(),0)

    def test_join_ik_ranges_rejects_invalid_pairs_without_changing_the_draft(self):
        session = self.separated_ik_session()
        left,right = session.ik_spans
        cases = ({left['start']['id'],left['end']['id']},
                 {left['start']['id'],right['start']['id']},
                 {left['end']['id']},set())
        before = deepcopy(session.document())
        undo_count = len(session._undo)
        for keys in cases:
            with self.subTest(keys=keys):
                self.assertFalse(session.join_ik_ranges(keys))
                self.assertEqual(session.document(),before)
                self.assertEqual(len(session._undo),undo_count)
        ids = {left['end']['id'],right['start']['id']}
        for kind in ('channels','pending','intervening'):
            spans = deepcopy([left,right])
            if kind == 'channels':
                spans[1]['position_mask'] = [False,True,False]
            elif kind == 'pending':
                spans[1].pop('end')
            else:
                key = deepcopy(left['end'])
                key.update(id='intervening',time=.25)
                spans.append(dict(left,start=key))
                spans[-1].pop('end')
            session.rebuild(spans=spans)
            before = deepcopy(session.document())
            with self.subTest(kind=kind):
                self.assertFalse(session.join_ik_ranges(ids))
                self.assertEqual(session.document(),before)

    def test_join_ik_ranges_respects_position_masks_and_leaves_other_channels_and_controls(self):
        session = self.separated_ik_session()
        spans = deepcopy(session.ik_spans)
        for span in spans:
            span['position_mask'] = [False,True,False]
        # A redundant mask from an older/reloaded range must compare equal to
        # the same default channels on the newly keyed neighboring range.
        spans[0]['channel_masks'] = {'left_wrist':[True,True,True,False,False,False]}
        session.rebuild(spans=spans)
        self.view.frame = 5
        session.key_rotations({'left_wrist':Rotation.from_euler('x',25,degrees=True).as_quat(),
                               'left_elbow':Rotation.from_euler('y',30,degrees=True).as_quat(),
                               'right_elbow':Rotation.from_euler('z',35,degrees=True).as_quat()})
        before = deepcopy(session.fk_keys)
        self.assertTrue(session.join_ik_ranges({spans[0]['end']['id'],spans[1]['start']['id']}))
        self.assertEqual(session.ik_spans[1]['position_mask'],[False,True,False])
        self.assertEqual(session.fk_keys['left_wrist'],before['left_wrist'])
        self.assertEqual(session.fk_keys['right_elbow'],before['right_elbow'])
        self.assertNotIn('left_elbow',session.fk_keys)
        self.assertEqual(session.keys_map('left_wrist')[5]['rxyz'],
                         session.keys_map('left_wrist',include_ik=False)[5]['rxyz'])

    def test_deleting_outer_end_promotes_the_last_intermediate_key_and_restores_source_after_it(self):
        session = self.separated_ik_session()
        left,right = deepcopy(session.ik_spans)
        self.assertTrue(session.join_ik_ranges({left['end']['id'],right['start']['id']}))
        last_pose = points(session.skeleton,session.draft,.35)
        self.view._timeline_curve_selected = {(0,9)}
        self.assertTrue(self.view.timeline_delete_selected_keys())
        self.assertEqual(len(session.ik_spans),2)
        self.assertTrue(all('end' in span for span in session.ik_spans))
        self.assertEqual(session.ik_spans[-1]['end'],right['start'])
        np.testing.assert_allclose(points(session.skeleton,session.draft,.35),last_pose,atol=1e-8)
        for time in (.375,.4,.45,.5):
            np.testing.assert_allclose(points(session.skeleton,session.draft,time),
                                       points(session.skeleton,session.base,time),atol=1e-8)
        self.assertNotIn('IK start set',session.panel.ik_range.text())
        self.assertEqual(corrections.read_document(session.path)['ik_spans'],session.ik_spans)
        self.assertTrue(session.undo())
        self.assertEqual(len(session.ik_spans),3)
        self.assertTrue(session.redo())
        self.assertEqual(session.ik_spans[-1]['end'],right['start'])

    def test_deleting_intermediate_ik_keys_keeps_the_remaining_range_connected(self):
        session = self.separated_ik_session()
        left,right = deepcopy(session.ik_spans)
        self.assertTrue(session.join_ik_ranges({left['end']['id'],right['start']['id']}))
        self.view._timeline_curve_selected = {(0,3),(0,7)}
        self.assertTrue(self.view.timeline_delete_selected_keys())
        self.assertEqual(session.ik_spans,[dict(left,end=right['end'])])
        self.assertEqual({frame for frame,row in session.keys_map('left_wrist').items() if row.get('ik_key')},{1,9})
        self.assertTrue(session.undo())
        self.view._timeline_curve_selected = {(0,7),(0,9)}
        self.assertTrue(self.view.timeline_delete_selected_keys())
        self.assertEqual(session.ik_spans,[left])

    def test_regular_set_key_refines_a_yellow_block_and_leaves_an_unfinished_start_elsewhere(self):
        session = self.session()
        self.make_ik_range(session)
        outer = deepcopy(session.ik_spans[0])
        self.view.frame = 4
        self.view._timeline_curve_axes_filter = {0}
        self.view._timeline_on_set_key_clicked()
        pending = deepcopy(next(span for span in session.ik_spans if 'end' not in span))
        # Saved documents need not put completed ranges before pending starts.
        session.rebuild(spans=[session.ik_spans[1],session.ik_spans[0]])
        self.view._timeline_curve_axes_filter = {3}
        self.view.frame = 2
        self.view._timeline_on_set_key_clicked()
        self.assertEqual(next(span for span in session.ik_spans if 'end' not in span),pending)
        completed = sorted((span for span in session.ik_spans if 'end' in span),
                           key=lambda span:span['start']['time'])
        self.assertEqual(len(completed),2)
        self.assertEqual(completed[0]['start'],outer['start'])
        self.assertEqual(completed[-1]['end'],outer['end'])
        self.assertEqual(completed[0]['end'],completed[1]['start'])
        self.assertTrue(all('position_mask' not in span for span in completed))
        row = session.keys_map('left_wrist')[2]
        self.assertTrue(row['ik_key'])
        self.assertEqual(row['ik_axis_mask'],[True]*6)
        self.assertEqual(self.view._timeline_ik_boundary_label(2),'')
        self.assertEqual(self.view._timeline_ik_boundary_label(4),'S')
        self.assertEqual(corrections.read_document(session.path)['ik_spans'],session.ik_spans)
        self.assertTrue(session.undo())
        self.assertEqual(len(session.ik_spans),2)
        self.assertTrue(session.redo())
        self.assertEqual(len(session.ik_spans),3)

    def test_rejected_overlapping_ik_range_keeps_previous_draft_and_controls(self):
        session = self.session()
        self.make_ik_range(session)
        before = deepcopy(session.document())
        self.view.frame = 2
        self.assertFalse(session.key_ik('left_wrist',mode='start'))
        self.assertEqual(session.document(),before)
        self.assertIn('overlap',session.panel.status.text())

    def test_ik_json_reopens_and_approved_bvh_matches_interpolated_targets(self):
        session = self.session()
        first,last = self.make_ik_range(session)
        session.save()
        restored = self.session()
        self.assertEqual(restored.ik_spans,session.ik_spans)
        self.assertEqual({frame for frame,row in restored.keys_map('left_elbow').items() if row.get('ik_key')},{1,3})
        output = restored.approve()
        self.assertTrue(output)
        loaded = ingest_bvh_animation_data(output)
        index = loaded.skeleton.joint_names.index('left_wrist')
        for time,alpha in ((.05,0),(.075,.25),(.1,.5),(.125,.75),(.15,1)):
            actual = points(loaded.skeleton,loaded.clips[0],time)[index]
            np.testing.assert_allclose(actual,(1-alpha)*first+alpha*last,atol=1e-5)

    def test_deleting_one_ik_axis_removes_that_boundary_across_the_chain(self):
        session = self.session()
        self.make_ik_range(session)
        retained = points(session.skeleton,session.draft,.15)
        self.select('left_elbow')
        self.view._timeline_curve_selected = {(3,1)}
        self.view._timeline_on_delete_key_clicked()
        for name in ('left_shoulder','left_elbow','left_wrist'):
            rows = session.keys_map(name)
            self.assertFalse(any(row.get('ik_key') for row in rows.values()))
            self.assertTrue(rows[3]['fk_key'])
            self.assertIn(2,rows)
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),points(session.skeleton,session.base,.1),atol=1e-8)
        np.testing.assert_allclose(points(session.skeleton,session.draft,.15),retained,atol=1e-8)

    def test_deleting_ik_control_reuses_remaining_bake_and_autosaves_without_graph_updates(self):
        session = self.session()
        self.make_ik_range(session,'left_wrist')
        self.make_ik_range(session,'right_wrist')
        before = session.draft
        manual = session.manual
        self.view._timeline_curve_selected = {(0,1),(0,3)}
        with patch.object(corrections,'solve_ik_pose',side_effect=AssertionError('Rebaked unchanged IK')), \
                patch.object(corrections,'track_from_timeline_keys',side_effect=AssertionError('Rebuilt untouched source keys')), \
                patch.object(self.scene,'set_node_params') as graph, \
                patch.object(collection,'refresh_repair_records') as records, \
                patch.object(corrections,'save_document',wraps=corrections.save_document) as saved:
            self.assertTrue(self.view.timeline_delete_selected_keys())
            saved.assert_called_once()
            graph.assert_not_called()
            records.assert_not_called()
        self.assertIs(session.manual,manual)
        self.assertEqual([span['joint'] for span in session.ik_spans],['left_wrist'])
        self.assertEqual(corrections.read_document(session.path)['ik_spans'],session.ik_spans)
        expected = corrections.apply_ik_spans(session.skeleton,manual,session.ik_spans,session.fps)
        for time in (0.,.05,.1,.15,.2):
            np.testing.assert_allclose(points(session.skeleton,session.draft,time),
                                       points(session.skeleton,expected,time),atol=1e-8)
        after = session.draft
        self.assertTrue(session.undo())
        self.assertIs(session.draft,before)
        self.assertTrue(session.redo())
        self.assertIs(session.draft,after)

    def test_canceling_a_pair_restores_source_and_converts_the_remaining_start_pose_to_fk(self):
        session = self.session()
        self.make_ik_range(session)
        expected = points(session.skeleton,session.draft,.05)
        self.view._timeline_curve_selected = {(0,3)}
        self.assertTrue(self.view.timeline_delete_selected_keys())
        self.assertFalse(session.ik_spans)
        self.assertFalse(self.view._timeline_ik_ranges())
        self.assertFalse(any(row.get('ik_key') for row in session.keys_map('left_wrist').values()))
        self.assertTrue(session.keys_map('left_wrist')[1]['fk_key'])
        np.testing.assert_allclose(points(session.skeleton,session.draft,.05),expected,atol=1e-8)
        for stamp in (.1,.125,.15,.175,.2):
            np.testing.assert_allclose(points(session.skeleton,session.draft,stamp),
                                       points(session.skeleton,session.base,stamp),atol=1e-8)
        restored = self.session()
        self.assertFalse(restored.ik_spans)
        np.testing.assert_allclose(points(restored.skeleton,restored.draft,.05),expected,atol=1e-8)
        self.assertTrue(session.undo())
        self.assertEqual(len(session.ik_spans),1)
        self.assertTrue(session.redo())
        self.assertFalse(session.ik_spans)

    def test_canceling_body_ik_keeps_the_remaining_root_translation_as_an_ordinary_key(self):
        session = self.session()
        self.select('pelvis')
        for frame,mode in ((1,'start'),(3,'end')):
            self.view.frame = frame
            pose = points(session.skeleton,session.draft,session.time())
            pelvis = session.skeleton.joint_names.index('pelvis')
            session.move('pelvis',pose[pelvis]+[0.,.03,0.])
            self.assertTrue(session.key_ik('pelvis',mode=mode))
        before = points(session.skeleton,session.draft,.05)
        self.view._timeline_curve_selected = {(1,3)}
        self.assertTrue(self.view.timeline_delete_selected_keys())
        self.assertEqual(session.ik_spans,[])
        np.testing.assert_allclose(points(session.skeleton,session.draft,.05),before,atol=1e-8)
        row = session.keys_map('pelvis')[1]
        self.assertTrue(row['fk_key'])
        self.assertEqual(row['fk_position_axes'],[True]*3)
        self.assertFalse(row.get('ik_key'))
        restored = corrections.apply_document(session.skeleton,session.base,session.document())
        np.testing.assert_allclose(points(session.skeleton,restored,.05),before,atol=1e-8)

    def test_deleting_end_ik_keeps_the_remaining_pose_as_fk_without_rebaking_other_controls(self):
        session = self.session()
        self.make_ik_range(session,'left_wrist')
        self.make_ik_range(session,'right_wrist')
        corrections.apply_ik_spans(session.skeleton,session.manual,session.ik_spans,session.fps,
                                  cache=session._ik_bake_cache)
        self.view._timeline_curve_selected = {(0,3)}
        with patch.object(corrections,'solve_ik_pose',wraps=corrections.solve_ik_pose) as solve:
            self.view._timeline_on_delete_key_clicked()
            self.assertEqual(solve.call_count,0)
        self.assertEqual([span['joint'] for span in session.ik_spans],['left_wrist'])
        expected = corrections.apply_document(session.skeleton,session.base,session.document())
        for time in (.05,.1,.15):
            np.testing.assert_allclose(points(session.skeleton,session.draft,time),
                                       points(session.skeleton,expected,time),atol=1e-8)

    def test_reopened_preview_reuses_existing_ik_bakes_when_deleting_keys(self):
        session = self.session()
        self.make_ik_range(session,'left_wrist')
        self.make_ik_range(session,'right_wrist')
        self.assertTrue(session.save(notify=False))
        session.finish()
        session = self.session()
        self.select('right_wrist')
        self.view._timeline_curve_selected = {(0,1),(0,3)}
        with patch.object(corrections,'solve_ik_pose',side_effect=AssertionError('Rebaked reopened preview')):
            self.assertTrue(self.view.timeline_delete_selected_keys())
        self.assertEqual([span['joint'] for span in session.ik_spans],['left_wrist'])

    def test_deleting_source_keys_invalidates_ik_bake_cache(self):
        session = self.session()
        self.make_ik_range(session)
        self.select('pelvis')
        self.view._timeline_curve_selected = {(axis,2) for axis in range(6)}
        manual = session.manual
        with patch.object(corrections,'solve_ik_pose',wraps=corrections.solve_ik_pose) as solve:
            self.view._timeline_on_delete_key_clicked()
            points(session.skeleton,session.draft,.1)
            self.assertTrue(solve.called)
        self.assertIsNot(session.manual,manual)
        expected = corrections.apply_ik_spans(session.skeleton,session.manual,session.ik_spans,session.fps)
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),
                                   points(session.skeleton,expected,.1),atol=1e-8)

    def test_single_axis_end_ik_drag_keeps_its_pair_through_preview_and_release(self):
        session = self.session()
        self.make_ik_range(session)
        self.select('left_wrist')
        boundary = deepcopy(session.ik_spans[0]['end'])
        value = self.view._timeline_keys[3]['xyz'][0]
        self.assertEqual(self.view._timeline_drag_axis_key(0,3,4,value,commit=False),4)
        self.assertEqual(self.view._timeline_keys[4]['ik_key'],boundary['id'])
        self.assertEqual([row[:2] for row in self.view._timeline_ik_ranges()],[(1.,4.)])
        # Release at the same frame as the last mouse move must still commit.
        self.assertEqual(self.view._timeline_drag_axis_key(0,4,4,value,commit=True),4)
        self.assertEqual(session.ik_spans[0]['end']['id'],boundary['id'])
        self.assertAlmostEqual(session.ik_spans[0]['end']['time'],.2)
        self.assertEqual([row[:2] for row in self.view._timeline_ik_ranges()],[(1.,4.)])
        for name in ('left_shoulder','left_elbow','left_wrist'):
            self.assertEqual({frame for frame,row in session.keys_map(name).items() if row.get('ik_key')},{1,4})
        # Dragging past the start clamps to the next frame instead of collapsing the pair.
        self.assertEqual(self.view._timeline_drag_axis_key(0,4,0,value,commit=True),2)
        self.assertEqual([row[:2] for row in self.view._timeline_ik_ranges()],[(1.,2.)])

    def test_single_axis_ik_drag_preserves_axis_mask_and_source_keys_at_both_frames(self):
        session = self.session()
        self.select('left_wrist')
        self.view._timeline_curve_axes_filter = {1}
        source = session.keys_map('left_wrist',include_ik=False)
        self.make_ik_range(session)
        boundary = deepcopy(session.ik_spans[0]['end'])
        value = self.view._timeline_keys[3]['xyz'][1]
        self.assertEqual(self.view._timeline_drag_axis_key(1,3,4,value,commit=False),4)
        self.assertFalse(self.view._timeline_keys[3].get('ik_key'))
        self.assertEqual(self.view._timeline_keys[4]['ik_axis_mask'],[False,True,False,False,False,False])
        self.view._timeline_drag_axis_key(1,4,4,value,commit=True)
        self.assertEqual(session.ik_spans[0]['position_mask'],[False,True,False])
        self.assertEqual(session.ik_spans[0]['end']['id'],boundary['id'])
        self.assertEqual(session.ik_spans[0]['end']['target'],boundary['target'])
        for frame in (3,4):
            self.assertEqual(session.keys_map('left_wrist',include_ik=False)[frame]['rxyz'],source[frame]['rxyz'])
        self.assertFalse(self.view._timeline_key_is_ik(4,3))
        restored = self.session()
        self.assertEqual(restored.ik_spans,session.ik_spans)

    def test_ik_boundary_drag_keeps_triangle_and_moves_full_chain_atomically(self):
        session = self.session()
        self.make_ik_range(session)
        self.select('left_wrist')
        self.view._timeline_curve_selected = {(0,1)}
        self.assertEqual(self.view._timeline_drag_selected_keys(-1,commit=True),-1)
        for name in ('left_shoulder','left_elbow','left_wrist'):
            rows = session.keys_map(name)
            self.assertEqual({frame for frame,row in rows.items() if row.get('ik_key')},{0,3})
        self.select('left_wrist')
        self.view._timeline_curve_selected = {(0,0)}
        before = np.array(session.ik_spans[0]['start']['target'])
        self.view._timeline_curve_min,self.view._timeline_curve_max = -100,100
        applied = self.view._timeline_drag_selected_curve_keys(0,.01,commit=True)
        self.assertEqual(applied,(0,.01))
        np.testing.assert_allclose(session.ik_spans[0]['start']['target'],before+[.01,0,0],atol=1e-8)

    def test_reload_selected_joint_restores_source_and_removes_only_its_ik_chain(self):
        session = self.session()
        self.make_ik_range(session)
        self.make_ik_range(session,'right_ankle')
        self.view.frame = 2
        session.rotate('spine1',(0,12,0))
        other = deepcopy(session.fk_keys['spine1'])
        session.select('left_elbow')
        self.assertTrue(session.reload_joint())
        self.assertEqual([span['joint'] for span in session.ik_spans],['right_ankle'])
        self.assertEqual(session.fk_keys['spine1'],other)
        original = evaluate_rig_at_time(session.skeleton,session.base,.1,loop=False)
        actual = evaluate_rig_at_time(session.skeleton,session.draft,.1,loop=False)
        for name in ('left_shoulder','left_elbow','left_wrist'):
            index = session.skeleton.joint_names.index(name)
            np.testing.assert_allclose(actual.local_transforms[index].rotation,original.local_transforms[index].rotation,atol=1e-8)
        session.select('spine1')
        self.assertTrue(session.reload_joint())
        self.assertNotIn('spine1',session.fk_keys)
        restored = self.session()
        self.assertEqual([span['joint'] for span in restored.ik_spans],['right_ankle'])

    def test_reload_all_after_deselecting_clears_ik_fk_and_manual_edits_and_can_undo(self):
        session = self.session()
        self.assertEqual(session.panel.reload.text(),'Reload all')
        self.make_ik_range(session)
        self.make_ik_range(session,'right_ankle')
        session.set_ik(False)
        self.view.frame = 2
        session.rotate('spine1',(12,23,34))
        rows = session.keys_map('pelvis',timeline_space=False,include_ik=False)
        rows[2]['xyz'][0] += .4
        self.assertTrue(session.apply_keys('pelvis',rows,session.fps))
        session.select('left_wrist')
        before = session._state()
        gl_view_mouse._handle_mouse_release_moderngl_pick_empty(self.view)
        self.assertIsNone(session.reload_selection)
        self.assertEqual(session.panel.reload.text(),'Reload all')
        self.view._timeline_curve_axes_filter = {4}  # No joint: Reload all ignores a stale channel filter.
        self.assertTrue(session.reload_joint())
        self.assertFalse(session.tracks or session.rows or session.ik_spans or session.fk_keys)
        for time in (0.,.05,.1,.15,.2):
            np.testing.assert_allclose(points(session.skeleton,session.draft,time),
                                       points(session.skeleton,session.base,time),atol=1e-8)
        document = corrections.read_document(session.path)
        self.assertFalse(document['tracks'] or document['timeline_rows'] or document['ik_spans'] or document['fk_keys'])
        self.assertTrue(session.undo())
        self.assertEqual(session.ik_spans,before['ik_spans'])
        self.assertEqual(session.fk_keys,before['fk_keys'])
        self.assertEqual(session.tracks,before['tracks'])
        self.assertTrue(session.redo())
        reopened = self.session()
        self.assertFalse(reopened.tracks or reopened.rows or reopened.ik_spans or reopened.fk_keys)

    def test_reload_selected_axes_restores_fk_and_deleted_input_keys_but_keeps_other_axes(self):
        session = self.session()
        session.set_ik(False)
        session.select('pelvis')
        self.view.frame = 2
        session.rotate('pelvis',(12,23,34))
        rows = session.keys_map('pelvis',timeline_space=False,include_ik=False)
        for row in rows.values():
            if 'xyz' in row:
                row['xyz'] = (np.asarray(row['xyz'])+[.4,.5,.6]).tolist()
        rows[2]['curve_handles'] = {'0':dict(mode='straight'),'1':dict(mode='straight')}
        rows[2]['axis_mask'][0] = False
        self.assertTrue(session.apply_keys('pelvis',rows,session.fps))
        session.rotate('spine1',(5,6,7))
        other = deepcopy(session.fk_keys['spine1'])
        index = session.skeleton.joint_names.index('pelvis')
        before = [evaluate_rig_at_time(session.skeleton,session.draft,t,loop=False).local_transforms[index]
                  for t in (0.,.05,.1,.15,.2)]
        self.view._timeline_on_axis_label_clicked(0)
        self.view._timeline_on_axis_label_clicked(4,additive=True)
        self.assertEqual(session.panel.reload.text(),'Reload Px, Ry')
        self.assertTrue(session.reload_joint())
        self.assertEqual(session.fk_keys['spine1'],other)
        self.assertNotIn('0',session.rows['pelvis']['keys'][2]['curve_handles'])
        self.assertIn('1',session.rows['pelvis']['keys'][2]['curve_handles'])
        self.assertTrue(session.rows['pelvis']['keys'][2]['axis_mask'][0])
        reopened = self.session()
        for clip in (session.draft,reopened.draft):
            for old,time in zip(before,(0.,.05,.1,.15,.2)):
                source = evaluate_rig_at_time(session.skeleton,session.base,time,loop=False).local_transforms[index]
                actual = evaluate_rig_at_time(session.skeleton,clip,time,loop=False).local_transforms[index]
                np.testing.assert_allclose(actual.translation[0],source.translation[0],atol=1e-8)
                np.testing.assert_allclose(actual.translation[1:],old.translation[1:],atol=1e-8)
                angles = Rotation.from_quat(actual.rotation).as_euler('xyz',degrees=True)
                original = Rotation.from_quat(source.rotation).as_euler('xyz',degrees=True)
                previous = Rotation.from_quat(old.rotation).as_euler('xyz',degrees=True)
                np.testing.assert_allclose(angles[1],original[1],atol=1e-7)
                np.testing.assert_allclose(angles[[0,2]],previous[[0,2]],atol=1e-7)

    def test_reload_one_ik_target_axis_preserves_other_targets_boundaries_and_independent_limbs(self):
        session = self.session()
        self.make_ik_range(session)
        self.make_ik_range(session,'right_ankle')
        other = deepcopy(session.ik_spans[1])
        before = deepcopy(session.ik_spans[0])
        session.select('left_wrist')
        self.view._timeline_curve_axes_filter = {0}
        self.assertTrue(session.reload_joint())
        span = session.ik_spans[0]
        self.assertEqual(span['position_mask'],[False,True,True])
        self.assertEqual(session.ik_spans[1],other)
        for boundary in ('start','end'):
            self.assertEqual(span[boundary]['id'],before[boundary]['id'])
            self.assertEqual(span[boundary]['target'],before[boundary]['target'])
        rows = session.keys_map('left_wrist')
        self.assertTrue(any(row.get('ik_axis_mask',[False]*6)[1] for row in rows.values()))
        self.assertFalse(any(row.get('ik_axis_mask',[False]*6)[0] for row in rows.values()))
        reopened = self.session()
        self.assertEqual(reopened.ik_spans,session.ik_spans)
        for time in (.05,.1,.15):
            np.testing.assert_allclose(points(session.skeleton,reopened.draft,time),
                                       points(session.skeleton,session.draft,time),atol=1e-7)

    def test_reload_ik_rotation_axis_restores_input_in_preview_saved_draft_and_export(self):
        session = self.session()
        self.make_ik_range(session)
        spans = deepcopy(session.ik_spans)
        for boundary,angles in (('start',(12,23,34)),('end',(25,35,45))):
            spans[0][boundary]['rotations']['left_wrist'] = Rotation.from_euler('xyz',angles,degrees=True).as_quat().tolist()
        session.rebuild(spans=spans)
        session.select('left_wrist')
        index = session.skeleton.joint_names.index('left_wrist')
        before = [Rotation.from_quat(evaluate_rig_at_time(session.skeleton,session.draft,t,loop=False)
                  .local_transforms[index].rotation).as_euler('xyz',degrees=True) for t in (.05,.1,.15)]
        self.view._timeline_curve_axes_filter = {3}
        self.assertTrue(session.reload_joint())
        self.assertEqual(session.ik_spans[0]['channel_masks']['left_wrist'],[True,True,True,False,True,True])
        rows = session.keys_map('left_wrist')
        self.assertFalse(any(row.get('ik_axis_mask',[False]*6)[3] for row in rows.values()))
        self.assertTrue(any(row.get('ik_axis_mask',[False]*6)[4] for row in rows.values()))
        exported = corrections.apply_document(session.skeleton,session.base,corrections.read_document(session.path))
        for clip in (session.draft,exported):
            for previous,time in zip(before,(.05,.1,.15)):
                source = evaluate_rig_at_time(session.skeleton,session.base,time,loop=False).local_transforms[index]
                actual = evaluate_rig_at_time(session.skeleton,clip,time,loop=False).local_transforms[index]
                angles = Rotation.from_quat(actual.rotation).as_euler('xyz',degrees=True)
                original = Rotation.from_quat(source.rotation).as_euler('xyz',degrees=True)
                np.testing.assert_allclose(angles[0],original[0],atol=1e-7)
                np.testing.assert_allclose(angles[1:],previous[1:],atol=1e-7)

    def test_reload_all_ik_position_axes_retains_only_independent_endpoint_orientation(self):
        session = self.session()
        self.make_ik_range(session)
        spans = deepcopy(session.ik_spans)
        for boundary in ('start','end'):
            spans[0][boundary]['rotations']['left_wrist'] = Rotation.from_euler('xyz',(12,23,34),degrees=True).as_quat().tolist()
        session.rebuild(spans=spans)
        session.select('left_wrist')
        self.view._timeline_curve_axes_filter = {0,1,2}
        self.assertTrue(session.reload_joint())
        self.assertTrue(session.ik_spans[0]['rotation_only'])
        exported = corrections.apply_document(session.skeleton,session.base,session.document())
        index = session.skeleton.joint_names.index('left_wrist')
        for clip in (session.draft,exported):
            for time in (.05,.1,.15):
                np.testing.assert_allclose(points(session.skeleton,clip,time)[index],
                                           points(session.skeleton,session.base,time)[index],atol=1e-7)
                angles = Rotation.from_quat(evaluate_rig_at_time(session.skeleton,clip,time,loop=False)
                    .local_transforms[index].rotation).as_euler('xyz',degrees=True)
                np.testing.assert_allclose(angles,(12,23,34),atol=1e-7)

    def test_infocard_embeds_entire_joint_hierarchy_and_selects_viewport_joint(self):
        session = self.session()
        host = QtWidgets.QWidget()
        self.addCleanup(host.deleteLater)
        layout = QtWidgets.QVBoxLayout(host)
        card = SimpleNamespace(_node_ref=self.item.model,_graph_scene=self.scene)
        self.assertTrue(spec.augment_infocard_footer(card,layout))
        hierarchy = host.findChild(spec.CorrectionHierarchy)
        self.assertFalse(hierarchy.expand.isChecked())
        self.assertTrue(hierarchy.tree.isHidden())
        hierarchy.expand.setChecked(True)
        self.assertEqual(len(hierarchy._items),len(session.skeleton.joints))
        for joint in session.skeleton.joints:
            row = hierarchy._items[joint.name]
            if joint.parent_index >= 0:
                self.assertEqual(row.parent().text(0),session.skeleton.joints[joint.parent_index].name)
        hierarchy.tree.itemClicked.emit(hierarchy._items['left_wrist'],0)
        self.assertEqual(session.selected,'left_wrist')
        self.assertTrue(self.view._timeline_owner_name.endswith('::left_wrist'))
        self.assertTrue(self.view._mgl_gizmo_visible)
        session.select_joint('right_ankle')
        self.assertEqual(hierarchy.tree.currentItem().text(0),'right_ankle')
        hierarchy.expand.setChecked(False)
        self.assertFalse(hierarchy.timer.isActive())


class CorrectionMathTests(unittest.TestCase):
    def test_bvh_joint_position_channels_preserve_scaled_bones_and_nonzero_root_offsets(self):
        skeleton,clip = fixture()
        skeleton.joints[0].local_bind.translation = (.3,-.2,.4)
        skeleton.joints[0].local_bind.scale = (.01,)*3
        root = next(track for track in clip.tracks if track.joint_name == skeleton.joints[0].name)
        root.translation_keys = [Vec3Keyframe(key.time,tuple(np.asarray(key.value)+[.3,-.2,.4]))
                                 for key in root.translation_keys]
        name = 'left_wrist'
        index = skeleton.joint_names.index(name)
        offset = np.asarray(skeleton.joints[index].local_bind.translation)
        track = next(track for track in clip.tracks if track.joint_name == name)
        track.translation_keys = [Vec3Keyframe(time,tuple(offset+delta)) for time,delta in
                                  ((0.,np.array((.1,.02,-.03))),(.1,np.array((.2,-.01,.04))),
                                   (.2,np.array((.12,.05,.02))))]
        before = deepcopy(clip.to_dict())
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'joint_positions.bvh'
            context = corrections.bvh_export_context(skeleton,clip)
            export_context(context,output,allow_joint_positions=True)
            loaded = ingest_bvh_animation_data(output)
            order = [loaded.skeleton.joint_names.index(joint) for joint in skeleton.joint_names]
            for time in (0.,.05,.1,.15,.2):
                np.testing.assert_allclose(points(loaded.skeleton,loaded.clips[0],time)[order],
                                           points(skeleton,clip,time),atol=2e-7)
            self.assertEqual(loaded.skeleton.joints[0].local_bind.translation,(.3,-.2,.4))
            np.testing.assert_allclose(loaded.skeleton.joints[order[index]].local_bind.translation,offset*.01,atol=1e-9)
        self.assertEqual(clip.to_dict(),before)

    def test_reload_track_axes_retains_subframe_input_keys_and_untouched_components(self):
        skeleton,clip = fixture()
        joint = 'pelvis'
        source_track = deepcopy(next(track for track in clip.tracks if track.joint_name == joint))
        edited_track = deepcopy(source_track)
        source_track.rotation_keys.insert(1,QuatKeyframe(.025,tuple(
            Rotation.from_euler('xyz',(10,20,30),degrees=True).as_quat())))
        edited_track.rotation_keys.insert(1,QuatKeyframe(.025,tuple(
            Rotation.from_euler('xyz',(40,50,60),degrees=True).as_quat())))
        source = corrections.replace_clip_tracks(skeleton,clip,{joint:source_track})
        edited = corrections.replace_clip_tracks(skeleton,clip,{joint:edited_track})
        restored = corrections.reload_track_axes(skeleton,source,edited,joint,{4})
        key = next(key for key in restored.rotation_keys if key.time == .025)
        np.testing.assert_allclose(Rotation.from_quat(key.value).as_euler('xyz',degrees=True),(40,20,60),atol=1e-8)
        self.assertEqual(restored.translation_keys,edited_track.translation_keys)
        self.assertEqual(restored.scale_keys,edited_track.scale_keys)
        self.assertEqual({key.time for key in restored.rotation_keys},{key.time for key in source_track.rotation_keys})

    def axis_range(self, joint='left_wrist', mask=None):
        from echograph.rigging.fbx_canonical import Vec3Keyframe
        skeleton,clip = fixture()
        clip = corrections.override_rotation_keys(skeleton,clip,{
            'left_elbow':{time:Rotation.from_euler('z',angle,degrees=True).as_quat()
                          for time,angle in ((0.,60),(.2,95))},
            'left_shoulder':{time:Rotation.from_euler('y',angle,degrees=True).as_quat()
                             for time,angle in ((0.,-10),(.2,15))},
            'left_wrist':{time:Rotation.from_euler('x',angle,degrees=True).as_quat()
                          for time,angle in ((0.,10),(.2,40))}})
        clip = replace(clip,tracks=[replace(track,rotation_keys=[key for key in track.rotation_keys
            if key.time in (0.,.2)]) if track.joint_name in {'left_elbow','left_shoulder','left_wrist'}
            else track for track in clip.tracks])
        root = next(track for track in clip.tracks if track.joint_name == skeleton.joints[0].name)
        origin = np.asarray(root.translation_keys[0].value)
        clip = replace(clip,tracks=[replace(track,translation_keys=[
            Vec3Keyframe(time,tuple(origin+np.array((.3,-.1,.2))*time)) for time in (0.,.1,.2)])
            if track is root else track for track in clip.tracks])
        options = dict(joint=joint, position_mask=mask or [False,True,False])
        names = corrections.ik_span_joints(skeleton,options)
        keys = []
        for time in (.05,.15):
            pose = evaluate_rig_at_time(skeleton,clip,time,loop=False)
            target = points(skeleton,clip,time)[skeleton.joint_names.index(joint)]+[.015,.02,.01]
            key = dict(id=str(time),time=time,target=target.tolist(),rotations={
                name:list(pose.local_transforms[skeleton.joint_names.index(name)].rotation) for name in names})
            if corrections.body_control(skeleton,joint) == 'pelvis':
                root_name = skeleton.joints[corrections.ik_chain(skeleton,joint)[0]].name
                key['translations'] = {root_name:list(pose.local_transforms[skeleton.joint_names.index(root_name)].translation)}
            keys.append(key)
        return skeleton,clip,dict(options,start=keys[0],end=keys[1])

    def test_preview_and_bake_agree_at_subframes_and_range_edges(self):
        for joint in ('pelvis','left_wrist'):
            with self.subTest(joint=joint):
                skeleton,clip,span = self.axis_range(joint)
                baked = corrections.apply_ik_spans(skeleton,clip,[span],25.)
                preview = corrections.preview_clip(skeleton,clip,[span],25.)
                for time in (.05-5e-8,.05,.061,.083,.097,.124,.149,.15,.15+5e-8):
                    np.testing.assert_allclose(points(skeleton,preview,time),
                                               points(skeleton,baked,time),atol=1e-7,
                                               err_msg=f'{joint} preview differs at {time}')

    def test_independent_target_axes_compose_at_staggered_subframe_boundaries_in_preview_and_bake(self):
        for joint in ('pelvis','left_wrist'):
            with self.subTest(joint=joint):
                skeleton,clip,template = self.axis_range(joint)
                endpoint = skeleton.joint_names.index(joint)
                spans = []
                for axis,lo,hi,delta in ((1,.025,.175,.02),(0,.075,.15,.01),(2,.1,.2,.015)):
                    span = deepcopy(template)
                    span['position_mask'] = [index == axis for index in range(3)]
                    for boundary,time in (('start',lo),('end',hi)):
                        key = span[boundary]
                        key.update(id=f'{axis}-{boundary}',time=time,
                                   target=points(skeleton,clip,time)[endpoint].tolist())
                        key['target'][axis] += delta
                    spans.append(span)
                cache = {}
                baked = corrections.apply_ik_spans(skeleton,clip,spans,25.,cache=cache)
                preview = corrections.preview_clip(skeleton,clip,spans,25.)
                stamps = sorted({0.,.04,.061,.083,.097,.124,.149,.18,.2}|
                    {key['time']+offset for span in spans for key in (span['start'],span['end'])
                     for offset in (-2e-7,0.,2e-7) if 0 <= key['time']+offset <= .2})
                for time in stamps:
                    expected = points(skeleton,clip,time)[endpoint].copy()
                    for span in spans:
                        first,last = span['start'],span['end']
                        if first['time'] <= time <= last['time']:
                            axis = span['position_mask'].index(True)
                            expected[axis] = np.interp(time,[first['time'],last['time']],
                                                      [first['target'][axis],last['target'][axis]])
                    np.testing.assert_allclose(points(skeleton,baked,time)[endpoint],expected,atol=2e-4,
                                               err_msg=f'{joint} at {time}')
                    np.testing.assert_allclose(points(skeleton,preview,time),points(skeleton,baked,time),atol=1e-7,
                                               err_msg=f'{joint} preview at {time}')
                with patch.object(corrections,'solve_ik_pose',side_effect=AssertionError('Rebaked unchanged ranges')):
                    self.assertIs(corrections.apply_ik_spans(skeleton,clip,spans,25.,cache=cache),baked)

    def test_preview_and_scene_agree_for_adjacent_pelvis_ranges_with_an_internal_key(self):
        skeleton,clip,span = self.axis_range('pelvis')
        middle = deepcopy(span['start'])
        middle.update(id='middle',time=.1,target=list(span['start']['target']))
        middle['target'][1] += .08
        spans = [dict(span,end=deepcopy(middle)),dict(span,start=deepcopy(middle))]
        baked = corrections.apply_ik_spans(skeleton,clip,spans,25.)
        preview = corrections.preview_clip(skeleton,clip,spans,25.)
        for fps in (24.,25.,80.):
            for time in np.arange(0.,.2,1/fps):
                np.testing.assert_allclose(points(skeleton,preview,float(time)),
                                           points(skeleton,baked,float(time)),atol=1e-7)
        for time in (.1-1e-7,.1,.1+1e-7):
            np.testing.assert_allclose(points(skeleton,preview,time),
                                       points(skeleton,baked,time),atol=1e-7)

    def test_next_frame_end_start_interpolate_without_source_height_pop_at_other_playback_rates(self):
        skeleton,clip,span = self.axis_range('pelvis')
        left,right = deepcopy(span),deepcopy(span)
        for section,lo,hi,height in ((left,0.,.05,.65),(right,.05+1/24.,.2,.75)):
            for boundary,time in (('start',lo),('end',hi)):
                section[boundary].update(id=f'{height}-{boundary}',time=time)
                section[boundary]['target'][1] = height
        document = dict(fps=25.,ik_spans=[left,right],timeline_rows={'pelvis':dict(fps=24.,keys={})})
        spans = corrections.document_ik_spans(document)
        self.assertTrue(all(section['keyframe_fps']==24. for section in spans))
        baked = corrections.apply_document(skeleton,clip,document)
        preview = corrections.preview_clip(skeleton,clip,spans,25.)
        for time in (.05,.06,.08,.09,right['start']['time']):
            expected = np.interp(time,[left['end']['time'],right['start']['time']],[.65,.75])
            for animation in (preview,baked):
                self.assertAlmostEqual(points(skeleton,animation,time)[0,1],expected,places=7)
        # A gap with an actual unkeyed frame must still return to source.
        right['start']['time'] = .05+2/24.
        document['ik_spans'] = [left,right]
        baked = corrections.apply_document(skeleton,clip,document)
        np.testing.assert_allclose(points(skeleton,baked,.09)[0],points(skeleton,clip,.09)[0],atol=1e-7)

    def test_axis_ik_keeps_other_target_coordinates_and_endpoint_rotation_animated(self):
        for joint,mask in (('left_wrist',[False,True,False]),
                           ('left_wrist',[True,True,False]),('pelvis',[False,True,False])):
            with self.subTest(joint=joint,mask=mask):
                skeleton,clip,span = self.axis_range(joint,mask)
                corrected = corrections.apply_ik_spans(skeleton,clip,[span],20.)
                end = skeleton.joint_names.index(joint)
                for time in (.05,.075,.1,.125,.15):
                    source = points(skeleton,clip,time)[end]
                    alpha = (time-.05)/.1
                    target = (1-alpha)*np.array(span['start']['target'])+alpha*np.array(span['end']['target'])
                    expected = np.where(mask,target,source)
                    np.testing.assert_allclose(points(skeleton,corrected,time)[end],expected,atol=1e-7)
                    before = evaluate_rig_at_time(skeleton,clip,time,loop=False).local_transforms[end].rotation
                    after = evaluate_rig_at_time(skeleton,corrected,time,loop=False).local_transforms[end].rotation
                    self.assertLess((Rotation.from_quat(before).inv()*Rotation.from_quat(after)).magnitude(),1e-7)
                for time in (0.,.2):
                    np.testing.assert_allclose(points(skeleton,corrected,time),points(skeleton,clip,time),atol=1e-8)
                self.assertGreater(np.ptp([points(skeleton,corrected,t)[end,2] for t in (.05,.1,.15)]),.001)

    def test_ik_rejects_invalid_axis_masks(self):
        skeleton,clip,span = self.axis_range()
        for mask in ([],[False]*3,[True],[True,False,False,False],[1,0,0],'Py'):
            with self.subTest(mask=mask):
                invalid = dict(span,position_mask=mask)
                with self.assertRaisesRegex(ValueError,'position mask'):
                    corrections.validate_ik_spans(skeleton,clip,[invalid])

    def test_arm_leg_and_chest_poles_steer_bends_while_preserving_targets_and_lengths(self):
        skeleton,clip = fixture()
        initial = points(skeleton,clip,.1)
        for joint in corrections.limb_endpoints(skeleton)+['spine3']:
            start,middle,end = corrections.ik_chain(skeleton,joint)
            target = initial[start]+.8*(initial[end]-initial[start])
            axis = target-initial[start]
            axis /= np.linalg.norm(axis)
            reference = np.eye(3)[np.argmin(np.abs(axis))]
            normal = np.cross(axis,reference)
            normal /= np.linalg.norm(normal)
            for bend_direction in (normal,np.cross(axis,normal),-normal):
                with self.subTest(joint=joint,pole=bend_direction):
                    pole = initial[start]+bend_direction
                    rotations,translations,error = corrections.solve_ik_pose(skeleton,clip,joint,.1,target,pole=pole)
                    result = corrections.override_pose(skeleton,clip,.1,rotations,translations)
                    actual = points(skeleton,result,.1)
                    self.assertLess(error,1e-7)
                    np.testing.assert_allclose(actual[end],target,atol=1e-7)
                    bend = actual[middle]-actual[start]
                    bend -= axis*np.dot(bend,axis)
                    np.testing.assert_allclose(bend/np.linalg.norm(bend),bend_direction,atol=1e-7)
                    for a,b in ((start,middle),(middle,end)):
                        self.assertAlmostEqual(np.linalg.norm(actual[b]-actual[a]),np.linalg.norm(initial[b]-initial[a]),places=7)
        with self.assertRaisesRegex(ValueError,'pole'):
            corrections.solve_limb_pose(skeleton,clip,'left_wrist',.1,initial[20],pole=[float('nan'),0,0])

    def test_body_ik_supports_namespaced_fbx_pelvis_below_a_scaled_root(self):
        skeleton,clip = fixture()
        aliases = {'pelvis':'Hips','spine1':'Spine','spine2':'Spine1','spine3':'Spine2'}
        renamed = {name:'rig|mixamorig:'+aliases.get(name,name) for name in skeleton.joint_names}
        skeleton = replace(skeleton,joints=[Joint('Root',-1,JointTransform(scale=(100.,100.,100.)))]+[
            replace(joint,name=renamed[joint.name],parent_index=joint.parent_index+1) for joint in skeleton.joints])
        clip = replace(clip,tracks=[replace(track,joint_name=renamed[track.joint_name]) for track in clip.tracks])
        pelvis,chest = renamed['pelvis'],renamed['spine3']
        self.assertEqual(corrections.body_control(skeleton,pelvis),'pelvis')
        self.assertEqual(corrections.body_control(skeleton,chest),'chest')
        self.assertIsNone(corrections.body_control(skeleton,renamed['spine2']))
        initial = points(skeleton,clip,.1)
        delta = np.array([1.,-2.,.5])
        rotations,translations,error = corrections.solve_ik_pose(skeleton,clip,pelvis,.1,initial[1]+delta)
        self.assertAlmostEqual(error,0.)
        self.assertEqual(set(translations),{'Root'})
        corrected = corrections.override_pose(skeleton,clip,.1,rotations,translations)
        np.testing.assert_allclose(points(skeleton,corrected,.1)[1:],initial[1:]+delta,atol=1e-7)
        pose = evaluate_rig_at_time(skeleton,corrected,.1,loop=False)
        source = evaluate_rig_at_time(skeleton,clip,.1,loop=False)
        self.assertEqual(pose.local_transforms[1].translation,source.local_transforms[1].translation)
        start,_,end = corrections.ik_chain(skeleton,chest)
        current = points(skeleton,corrected,.1)
        target = current[start]+.95*(current[end]-current[start])+[0,0,.5]
        rotations,translations,error = corrections.solve_ik_pose(skeleton,corrected,chest,.1,target)
        corrected = corrections.override_pose(skeleton,corrected,.1,rotations,translations)
        np.testing.assert_allclose(points(skeleton,corrected,.1)[end],target,atol=1e-6)

    def test_ik_interval_ignores_interior_source_rotations_preserves_source_outside(self):
        skeleton,clip = fixture()
        shoulder = next(track for track in clip.tracks if track.joint_name=='left_shoulder')
        shoulder.rotation_keys[2].value = tuple(Rotation.from_euler('z',130,degrees=True).as_quat())
        before = deepcopy(clip.to_dict())
        indices = corrections.limb_chain(skeleton,'left_wrist')
        keys = []
        for time,depth in ((.05,.02),(.15,.08)):
            pose = evaluate_rig_at_time(skeleton,clip,time,loop=False)
            positions = points(skeleton,clip,time)
            target = positions[indices[0]]+.8*(positions[indices[-1]]-positions[indices[0]])
            target[2] += depth
            solved,_ = corrections.solve_limb_pose(skeleton,clip,'left_wrist',time,target)
            rotations = {skeleton.joints[index].name:list(solved.get(skeleton.joints[index].name,pose.local_transforms[index].rotation)) for index in indices}
            keys.append(dict(id=str(time),time=time,target=target.tolist(),rotations=rotations))
        document = dict(fps=20,ik_spans=[dict(joint='left_wrist',start=keys[0],end=keys[1])])
        corrected = corrections.apply_document(skeleton,clip,document)
        for time in (.05,.075,.1,.125,.15):
            alpha = (time-.05)/.1
            target = (1-alpha)*np.array(keys[0]['target'])+alpha*np.array(keys[1]['target'])
            np.testing.assert_allclose(points(skeleton,corrected,time)[indices[-1]],target,atol=1e-8)
        for time in (0,.025,.175,.2):
            np.testing.assert_allclose(points(skeleton,corrected,time),points(skeleton,clip,time),atol=1e-7)
        self.assertEqual(clip.to_dict(),before)

    def test_native_triangle_marker_has_a_distinct_shape_from_fk_circle(self):
        from echograph.ui.gl_view_timeline_model import TimelineKeyMarker
        images = []
        for ik in (False,True):
            marker = TimelineKeyMarker(None,ik=ik)
            marker.resize(20,20)
            image = QtGui.QImage(20,20,QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            marker.render(image)
            images.append(image)
            marker.deleteLater()
        self.assertNotEqual(images[0],images[1])
        self.assertGreater(images[1].pixelColor(3,16).red(),images[0].pixelColor(3,16).red())

    def test_ik_accounts_for_uniform_fbx_root_scale(self):
        skeleton,clip = fixture()
        skeleton.joints[0].local_bind.scale = (100.,100.,100.)
        initial = points(skeleton,clip,.1)
        start,_,end = corrections.limb_chain(skeleton,'left_wrist')
        target = initial[start]+.8*(initial[end]-initial[start])
        target[2] += 4.
        rotations,error = corrections.solve_limb_pose(skeleton,clip,'left_wrist',.1,target)
        self.assertLess(error,.05)
        keys = {}
        for name,quaternion in rotations.items():
            corrections.set_rotation_key(skeleton,clip,keys,name,.1,quaternion)
        corrected = corrections.apply_corrections(skeleton,clip,keys,20)
        self.assertLess(np.linalg.norm(points(skeleton,corrected,.1)[end]-target),.05)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'scaled.bvh'
            export_context(corrections.bvh_export_context(skeleton,corrected),output)
            loaded = ingest_bvh_animation_data(output)
            order = [loaded.skeleton.joint_names.index(name) for name in skeleton.joint_names]
            np.testing.assert_allclose(points(loaded.skeleton,loaded.clips[0],.1)[order],points(skeleton,corrected,.1),atol=1e-4)

    def test_legacy_delta_corrections_still_blend_without_modifying_source(self):
        skeleton,clip = fixture()
        before = deepcopy(clip.to_dict())
        keys = {}
        original = evaluate_rig_at_time(skeleton,clip,.1,loop=False).local_transforms[16].rotation
        desired = (Rotation.from_quat(original)*Rotation.from_euler('y',40,degrees=True)).as_quat()
        corrections.set_rotation_key(skeleton,clip,keys,'left_shoulder',.1,desired)
        result = corrections.apply_document(skeleton,clip,dict(keys=keys,fps=20))
        for time,degrees in ((0,0),(.05,20),(.1,40),(.15,20),(.2,0)):
            source = evaluate_rig_at_time(skeleton,clip,time,loop=False)
            actual = evaluate_rig_at_time(skeleton,result,time,loop=False)
            delta = Rotation.from_quat(source.local_transforms[16].rotation).inv()*Rotation.from_quat(actual.local_transforms[16].rotation)
            self.assertAlmostEqual(np.degrees(delta.magnitude()),degrees,places=5)
        self.assertEqual(before,clip.to_dict())

    def test_deleting_all_joint_keys_removes_track_and_uses_bind_pose(self):
        skeleton,clip = fixture()
        track = corrections.track_from_timeline_keys(skeleton,clip,'left_wrist',{},20)
        result = corrections.apply_document(skeleton,clip,dict(tracks={'left_wrist':track.to_dict()}))
        self.assertNotIn('left_wrist',[track.joint_name for track in result.tracks])

    def test_unchanged_sparse_fbx_quaternions_and_scale_are_preserved(self):
        skeleton,clip = fixture()
        track = next(track for track in clip.tracks if track.joint_name=='left_wrist')
        track.rotation_keys = [QuatKeyframe(.03,tuple(Rotation.from_euler('xyz',[10,20,30],degrees=True).as_quat())),
                               QuatKeyframe(.17,tuple(Rotation.from_euler('xyz',[30,40,50],degrees=True).as_quat()))]
        rows = corrections.joint_timeline_keys(clip,'left_wrist',100)
        restored = corrections.track_from_timeline_keys(skeleton,clip,'left_wrist',rows,100)
        self.assertEqual(restored.to_dict(),track.to_dict())


if __name__ == '__main__':
    unittest.main()
