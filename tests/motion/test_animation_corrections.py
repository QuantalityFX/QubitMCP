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
from scipy.spatial.transform import Rotation

from echograph.model import GraphNode
from echograph.motion import animation_corrections as corrections
from echograph.motion.conversion import reconstruct_animation
from echograph.motion.rig_preview import build_guide, preview_offsets
from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
from echograph.rigging.fbx_canonical import AnimationClip, JointAnimationTrack, QuatKeyframe
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
        self.assertEqual(session.endpoints,{'left_wrist','right_wrist','left_ankle','right_ankle'})
        self.view.frame = 2
        initial = points(session.skeleton,session.draft,.1)
        for joint in sorted(session.endpoints):
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

    def test_wireframe_cubes_are_submitted_to_renderer_and_disappear_when_ik_is_off(self):
        session = self.session()
        scene = SimpleNamespace(_items=[])
        scene.items = lambda:list(scene._items)
        scene.add = lambda item:scene._items.append(item)
        self.view._mgl_scene = scene
        def wire(**kwargs):
            return SimpleNamespace(tag=kwargs['tag'],payload={'owner':kwargs['owner']},release=Mock())
        self.view._mgl_add_wire_item_from_points = Mock(side_effect=wire)
        handles = [dict(name=name,position=(index,0,0),radius=.1,control_radius=.25)
                   for index,name in enumerate(sorted(session.endpoints))]
        MGLRendererMixin._mgl_scene_skeleton_rebuild_handle_mesh_for_owner(self.view,'Correction',handles)
        self.assertEqual(len(scene._items),4)
        for call in self.view._mgl_add_wire_item_from_points.call_args_list:
            self.assertEqual(call.kwargs['tag'],'scene-skeleton-handles')
            self.assertEqual(call.kwargs['line_points'].shape,(24,3))
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
        self.select('left_elbow')
        self.view._timeline_curve_selected = {(3,1)}
        self.view._timeline_on_delete_key_clicked()
        for name in ('left_shoulder','left_elbow','left_wrist'):
            rows = session.keys_map(name)
            self.assertEqual({frame for frame,row in rows.items() if row.get('ik_key')},{3})
            self.assertIn(2,rows)
        np.testing.assert_allclose(points(session.skeleton,session.draft,.1),points(session.skeleton,session.base,.1),atol=1e-8)

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
