import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation
from PySide6 import QtCore, QtGui, QtWidgets

from echograph.model import GraphNode
from echograph.motion.conversion import JOINT_NAMES, PARENTS, reconstruct_animation, write_bvh
from echograph.motion.rig_limiter import limit_clip, limit_rotations, rig_layout
from echograph.motion.rig_limits import rotation_parts
from echograph.motion.rig_preview import build_guide, preview_offsets
from echograph.motion.rig_preview_settings import default_preview_limits, load_preview_volumes, save_preview_limits
from echograph.motion.rig_volumes import (tapered_clearances, volume_settings, penetration_function,
                                         constrain_volume_rotation, rig_scale, ColliderModel)
from echograph.rigging.ik import forward_kinematics
from echograph.motion.self_collision import world_pose
from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
from echograph.rigging.fbx_canonical import Joint, JointTransform, JointAnimationTrack, QuatKeyframe
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
from echograph.ui.node_item import NodeItem
from echograph.ui.priormdm_rig_overlay import pick_guide_joint, draw_rig_guide
from echograph.ui.priormdm_rig_panel import RigPreviewPanel
from nodes.rig_limiter import spec
from nodes.anim_retarget import spec as retarget
from tests.motion.test_mocap_collection import Scene
from tests.motion.test_rig_preview_settings import PanelViewport

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def fixture(frames=3):
    offsets = preview_offsets('standardman')
    points,_ = build_guide(offsets)
    skeleton,clip,rotations,_ = reconstruct_animation(np.repeat(points[None],frames,axis=0),20,{},rest_offsets=offsets)
    return skeleton,clip,rotations,offsets


class VolumeTests(unittest.TestCase):
    def test_foot_capsules_follow_ankles_and_collide_during_edits_and_repair(self):
        offsets = preview_offsets('standardman')
        local = np.tile(np.eye(3),(22,1,1))
        local[8] = Rotation.from_euler('y',25,degrees=True).as_matrix()
        limits,volumes = default_preview_limits(),volume_settings()
        collision_offsets = offsets/rig_scale(offsets)
        model = ColliderModel(offsets=collision_offsets,settings=volumes)
        points,world = forward_kinematics(local,collision_offsets,PARENTS)
        starts,ends = model.endpoints(points,world)
        for side in ('left','right'):
            name = side+'_ankle'
            row = model.names.index(name)
            np.testing.assert_allclose(starts[row],points[JOINT_NAMES.index(name)])
            np.testing.assert_allclose(ends[row],points[JOINT_NAMES.index(side+'_foot')])
        desired = Rotation.from_euler('y',-25,degrees=True).as_quat()
        accepted = constrain_volume_rotation(7,desired,local,offsets,limits=limits,volumes=volumes)
        self.assertLess(Rotation.from_quat(accepted).magnitude(),Rotation.from_quat(desired).magnitude())
        local[7] = Rotation.from_quat(accepted).as_matrix()
        self.assertLess(model.penetrations(*forward_kinematics(local,collision_offsets,PARENTS)).max(),1e-5)
        local[7] = Rotation.from_quat(desired).as_matrix()
        source = local.copy()
        corrected,report = limit_rotations(local[None],offsets,np.zeros((1,3)),20,limits,volumes)
        self.assertGreater(report['max_proxy_overlap_before'],.01)
        self.assertLess(report['max_proxy_overlap_after'],.001)
        np.testing.assert_array_equal(local,source)
        posed,rotations = forward_kinematics(corrected[0],offsets,PARENTS)
        matrices = np.tile(np.eye(4),(22,1,1))
        matrices[:,:3,3],matrices[:,:3,:3] = posed,rotations
        for pose in (None,matrices):
            _,guide = build_guide(offsets,pose,volumes=volumes)
            drawn = {shape['joint'] for shape in guide['shapes'] if shape['kind']=='collision'}
            self.assertTrue({'left_ankle','right_ankle'} <= drawn)
        np.testing.assert_allclose(np.linalg.norm(posed[1:]-posed[np.array(PARENTS[1:])],axis=1),
                                   np.linalg.norm(offsets[1:],axis=1),atol=1e-8)

    def test_foot_radius_controls_save_without_losing_other_settings(self):
        view = PanelViewport()
        self.addCleanup(view.deleteLater)
        panel = RigPreviewPanel(view)
        panel.sync(view.data('left_ankle'),'Left foot capsule')
        section = panel.sections['left_ankle']
        self.assertTrue(all(spin.isEnabled() for spin in section.volume_fields))
        guide = view.context['priormdm_rig_debug']
        original_right = list(guide['volumes']['right_ankle'])
        section.volume_fields[1].setValue(.08*rig_scale(guide['offsets']))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'settings.json'
            with patch('echograph.ui.priormdm_rig_panel.save_preview_limits',
                       side_effect=lambda limits,**kwargs:save_preview_limits(limits,path,**kwargs)):
                panel.save()
            saved = load_preview_volumes(path)
            self.assertAlmostEqual(saved['left_ankle'][1],.08,delta=.0001)
            self.assertEqual(saved['right_ankle'],original_right)

    def test_tapered_distance_matches_independent_convex_optimization(self):
        rng = np.random.default_rng(27)
        a,b,c,d = rng.normal(size=(4,50,3))
        radii = rng.uniform(.001,.7,(4,50))
        # Include spheres, containment, parallel and exactly crossing segments.
        b[:4] = a[:4]
        d[4:8] = c[4:8]
        d[8:12] = c[8:12]+b[8:12]-a[8:12]
        actual = tapered_clearances(a,b,*radii[:2],c,d,*radii[2:])
        for i in range(len(a)):
            def objective(st):
                s,t = st
                return (np.linalg.norm(a[i]+s*(b[i]-a[i])-c[i]-t*(d[i]-c[i]))
                        -radii[0,i]-s*(radii[1,i]-radii[0,i])-radii[2,i]-t*(radii[3,i]-radii[2,i]))
            numerical = minimize(objective,[.5,.5],bounds=((0,1),(0,1)),method='SLSQP',options={'ftol':1e-11,'maxiter':300})
            self.assertAlmostEqual(actual[i],numerical.fun,delta=2e-5)

    def test_fast_drag_stops_at_opposite_arm_and_can_reverse_immediately(self):
        offsets = preview_offsets('standardman')
        local = np.tile(np.eye(3),(22,1,1))
        for joint,degrees in ((16,-80),(17,80),(18,-10),(19,50)):
            local[joint] = Rotation.from_euler('y',degrees,degrees=True).as_matrix()
        limits = default_preview_limits()
        volumes = volume_settings()
        desired = Rotation.from_euler('y',-130,degrees=True).as_quat()
        accepted = constrain_volume_rotation(18,desired,local,offsets,limits=limits,volumes=volumes)
        angle = -np.degrees(Rotation.from_quat(accepted).as_rotvec()[1])
        self.assertGreater(angle,10)
        self.assertLess(angle,80)
        local[18] = Rotation.from_quat(accepted).as_matrix()
        overlap = penetration_function(volumes)(world_pose(local,offsets/rig_scale(offsets),np.zeros(3))[0])
        self.assertLess(max(overlap),2e-7)
        reverse = Rotation.from_euler('y',-(angle-5),degrees=True).as_quat()
        np.testing.assert_allclose(constrain_volume_rotation(18,reverse,local,offsets,limits=limits,volumes=volumes),reverse,atol=1e-7)

    def test_radius_changes_contact_and_persists_independently(self):
        offsets = preview_offsets('standardman')
        local = np.tile(np.eye(3),(22,1,1))
        for j,a in ((16,-60),(17,60),(19,65)):
            local[j] = Rotation.from_euler('y',a,degrees=True).as_matrix()
        desired = Rotation.from_euler('y',-150,degrees=True).as_quat()
        limits,volumes = default_preview_limits(),volume_settings()
        normal = constrain_volume_rotation(18,desired,local,offsets,limits=limits,volumes=volumes)
        volumes['left_elbow'] = [.035,.13]
        wider = constrain_volume_rotation(18,desired,local,offsets,limits=limits,volumes=volumes)
        self.assertLess(Rotation.from_quat(wider).magnitude(),Rotation.from_quat(normal).magnitude())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'settings.json'
            path.write_text('{"other":42}',encoding='utf-8')
            save_preview_limits(limits,path,volumes=volumes)
            self.assertEqual(load_preview_volumes(path)['left_elbow'],[.035,.13])
            save_preview_limits(limits,path)
            self.assertEqual(load_preview_volumes(path)['left_elbow'],[.035,.13])
            self.assertEqual(json.loads(path.read_text())['other'],42)

    def test_viewport_collision_toggle_and_native_end_radius_fields(self):
        view = PanelViewport()
        self.addCleanup(view.deleteLater)
        panel = RigPreviewPanel(view)
        panel.sync(view.data('left_elbow'),'Elbow')
        section = panel.sections['left_elbow']
        initial = list(view.context['priormdm_rig_debug']['volumes']['left_elbow'])
        section.volume_fields[1].setValue(.11*rig_scale(view.context['priormdm_rig_debug']['offsets']))
        edited = view.context['priormdm_rig_debug']['volumes']['left_elbow']
        self.assertEqual(edited[0],initial[0])
        self.assertAlmostEqual(edited[1],.11,delta=.0001)
        section.reset_joint()
        self.assertEqual(view.context['priormdm_rig_debug']['volumes']['left_elbow'],initial)
        panel.collision_check.setChecked(False)
        self.assertFalse(view.context['priormdm_rig_debug']['collisions'])
        panel.collision_check.setChecked(True)
        self.assertTrue(view.context['priormdm_rig_debug']['collisions'])

    def test_capsules_pickable_away_from_joint_dots_and_nearest_wins(self):
        poly = QtGui.QPolygonF([QtCore.QPointF(x,y) for x,y in ((10,10),(50,10),(50,50),(10,50))])
        guide = dict(collisions=True,_pick_volumes=[(poly,.5,'left_elbow'),(poly,-.2,'right_elbow')])
        data = dict(rig_guide=guide,points=[])
        handles = [dict(name=n) for n in ('left_elbow','right_elbow')]
        for dpr in (1,1.5,2):
            self.assertEqual(pick_guide_joint(data,handles,30*dpr,30*dpr,dpr),handles[1])
        guide['collisions'] = False
        self.assertIsNone(pick_guide_joint(data,handles,30,30,1))

    def test_renderer_authors_contact_pose_and_disabled_preview_allows_rotation(self):
        view = PanelViewport()
        self.addCleanup(view.deleteLater)
        guide = view.context['priormdm_rig_debug']
        for name,angle in (('left_shoulder',-80),('right_shoulder',80),('left_elbow',-10),('right_elbow',50)):
            token = view._mgl_scene_skeleton_joint_owner_token('Rig',name)
            self.assertTrue(view._mgl_scene_skeleton_apply_timeline_keys(token,
                {0:dict(rxyz=[0,angle,0],axis_mask=[False]*3+[True]*3)},20))
        token = view._mgl_scene_skeleton_joint_owner_token('Rig','left_elbow')
        view._set_owner_rot_deg(token,(0,-130,0),False)
        evaluation = evaluate_rig_at_time(view.context['skeleton'],view.context['clip'],0)
        angle = -np.degrees(Rotation.from_quat(evaluation.local_transforms[18].rotation).as_rotvec()[1])
        self.assertGreater(angle,10)
        self.assertLess(angle,80)
        guide['collisions'] = False
        view._set_owner_rot_deg(token,(0,-130,0),False)
        evaluation = evaluate_rig_at_time(view.context['skeleton'],view.context['clip'],0)
        self.assertAlmostEqual(-np.degrees(Rotation.from_quat(evaluation.local_transforms[18].rotation).as_rotvec()[1]),130)


class RepairTests(unittest.TestCase):
    def test_collection_folder_rename_keeps_repair_linked_to_exact_bvh(self):
        from nodes.mocap_collection import spec as collection, review_data
        from echograph.motion import rig_repair_cache
        _, _, rotations, offsets = fixture(2)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            run = root / 'original_run'
            other_run = root / 'other_run'
            run.mkdir()
            other_run.mkdir()
            paths = [run / 'motion.bvh', run / 'sibling.bvh', other_run / 'motion.bvh']
            for path in paths:
                write_bvh(path, np.zeros((2, 22, 3)), 20, rotations, offsets)
            scene = Scene()
            self.addCleanup(scene.clear)
            source = scene.add_node(GraphNode('Input', kind='mocap_collection'), QtCore.QPointF())
            shared = scene.add_node(GraphNode('Shared', kind='mocap_collection'), QtCore.QPointF())
            limiter = scene.add_node(GraphNode('Limiter', kind='rig_limiter'), QtCore.QPointF())
            collection.add_paths(source.model, paths, scene)
            collection.add_paths(shared.model, paths[:2], scene)
            collection.save_state(source.model, collection.entries(source.model), 1, scene)
            scene.add_edge('Input', 'Limiter')
            scene._edges[-1].dst_port_name = 'animation'
            with patch.object(spec, 'run_repair', side_effect=lambda f, parent=None: f(lambda *args: None)):
                spec.prepare_repair(limiter)
            record = rig_repair_cache.find_repair(paths[0])
            metadata = rig_repair_cache.metadata_path(paths[0]).read_bytes()
            with patch.object(spec, 'run_repair', side_effect=AssertionError('Rename must reuse the repair')):
                review_data.rename_entry(source.model, scene, 1, 'renamed walk')
                renamed = root / 'renamed walk' / 'motion.bvh'
                self.assertFalse(run.exists())
                self.assertEqual(rig_repair_cache.metadata_path(renamed).read_bytes(), metadata)
                self.assertTrue(rig_repair_cache.repair_path(renamed, record).is_file())
                for item in (source, shared):
                    rows = collection.entries(item.model)
                    self.assertEqual(rows[0]['path'], str(renamed))
                    self.assertEqual(rows[0]['rig_limiter']['file'], record['file'])
                    self.assertNotIn('rig_limiter', rows[1])
                # Row order and matching filenames elsewhere must not redirect the badge.
                rows = collection.entries(source.model)
                collection.save_state(source.model, [rows[2], rows[1], rows[0]], 1, scene)
                for number in (1, 2):
                    collection.save_state(source.model, collection.entries(source.model), number, scene)
                    spec.resolve_context(limiter, [], [])
                    self.assertTrue(spec.repair_status(limiter.model).pending)
                collection.save_state(source.model, collection.entries(source.model), 3, scene)
                spec.resolve_context(limiter, [], [])
                spec.prepare_repair(limiter)
                self.assertFalse(spec.repair_status(limiter.model).pending)
                self.assertEqual([bool(r.get('rig_limiter')) for r in collection.entries(source.model)],
                                 [False, False, True])
                # Fresh model/clip objects also recover the renamed sidecar after reload.
                restored = scene.add_node(GraphNode('Restored', kind='rig_limiter',
                    params=json.loads(json.dumps(limiter.model.params))), QtCore.QPointF())
                scene.add_edge('Input', 'Restored')
                scene._edges[-1].dst_port_name = 'animation'
                context = spec.resolve_context(restored, [], [])
                self.assertIn('rig_limiter', context['clip'].metadata)
                self.assertFalse(spec.repair_status(restored.model).pending)

    def test_saved_repair_reloads_with_badges_and_preview_never_repairs(self):
        from nodes.mocap_collection import spec as collection
        from echograph.motion import rig_repair_cache
        from echograph.persistence import _node_to_dict
        from unittest.mock import Mock
        _, _, rotations, offsets = fixture(2)
        rotations[:, 13] = Rotation.from_euler('x', 60, degrees=True).as_matrix()
        with tempfile.TemporaryDirectory() as folder:
            source_path = Path(folder).resolve() / 'motion.bvh'
            write_bvh(source_path, np.zeros((2, 22, 3)), 20, rotations, offsets)
            original = source_path.read_bytes()
            scene = Scene()
            self.addCleanup(scene.clear)
            source = scene.add_node(GraphNode('Input', kind='mocap_collection'), QtCore.QPointF())
            limiter = scene.add_node(GraphNode('Limiter', kind='rig_limiter'), QtCore.QPointF())
            collection.add_paths(source.model, [source_path], scene)
            scene.add_edge('Input', 'Limiter')
            scene._edges[-1].dst_port_name = 'animation'
            controls = limiter._plugin_proxies[0].widget()
            window = SimpleNamespace(open_scene_assets=Mock(return_value=True), gl_view=None)
            with patch.object(scene, 'views', return_value=[SimpleNamespace(window=lambda: window)]), \
                 patch.object(spec, 'run_repair', side_effect=AssertionError('Preview must not repair')):
                controls.preview_button.click()
                window.open_scene_assets.assert_called_once()
                self.assertTrue(controls.repair_button.property('repairPending'))
                self.assertFalse(rig_repair_cache.metadata_path(source_path).exists())
            with patch.object(spec, 'run_repair', side_effect=lambda f, parent=None: f(lambda *args: None)):
                controls.repair_button.click()
            self.assertFalse(controls.repair_button.property('repairPending'), controls.status.text())
            record = rig_repair_cache.find_repair(source_path)
            self.assertIsNotNone(record)
            output = rig_repair_cache.repair_path(source_path, record)
            self.assertTrue(output.is_file())
            self.assertEqual(output.parent, source_path.parent)
            self.assertEqual(source_path.read_bytes(), original)
            self.assertEqual(collection.bvh_paths_in_folder(source_path.parent), [source_path])
            row = collection.entries(source.model)[0]
            self.assertEqual(row['path'], str(source_path))
            self.assertEqual(row['rig_limiter']['file'], output.name)
            widget = source._plugin_proxies[0].widget()
            self.assertFalse(widget.table.item(0, 2).icon().isNull())
            self.assertIn('Saved Rig Limiter repair', widget.table.item(0, 2).toolTip())
            thumbnail = QtGui.QImage(52, 32, QtGui.QImage.Format_ARGB32)
            thumbnail.fill(QtGui.QColor('#102030'))
            image = widget._thumbnail_icon(thumbnail, True).pixmap(52, 32).toImage()
            self.assertEqual(image.pixelColor(1, 1).name(), '#102030')
            self.assertTrue(any(image.pixelColor(x, y).name() != '#102030'
                                for x in range(32, 52) for y in range(12, 32)))
            first = spec.resolve_context(limiter, [], [])
            params = _node_to_dict(limiter.model)['params']
            restored_scene = Scene()
            self.addCleanup(restored_scene.clear)
            restored_source = restored_scene.add_node(GraphNode('Input', kind='mocap_collection'), QtCore.QPointF())
            collection.add_paths(restored_source.model, [source_path], restored_scene)
            restored = restored_scene.add_node(GraphNode('Restored', kind='rig_limiter',
                params=json.loads(json.dumps(params))), QtCore.QPointF())
            restored_scene.add_edge('Input', 'Restored')
            restored_scene._edges[-1].dst_port_name = 'animation'
            with patch.object(spec, 'run_repair', side_effect=AssertionError('Saved repair must be reused')):
                APP.processEvents()
                self.assertFalse(restored._plugin_proxies[0].widget().repair_button.property('repairPending'))
                current = spec.resolve_context(restored, [], [])
                spec.prepare_repair(restored)
            self.assertIn('rig_limiter', current['clip'].metadata)
            np.testing.assert_allclose(evaluate_rig_at_time(first['skeleton'], first['clip'], .05).global_matrices,
                evaluate_rig_at_time(current['skeleton'], current['clip'], .05).global_matrices, atol=1e-6)
            self.assertFalse(spec.repair_status(restored.model).pending)
            guide = spec.build_scene_asset(restored, preview=True)['fbx_rig_context']['priormdm_rig_debug']
            guide['volumes']['left_ankle'][0] += .01
            spec.refresh_repair_status(restored.model)
            self.assertFalse(spec.repair_status(restored.model).pending)  # Unsaved preview edit.
            guide['save_settings'](guide)
            self.assertTrue(spec.repair_status(restored.model).pending)
            output.unlink()
            widget.refresh()
            self.assertNotIn('rig_limiter', collection.entries(source.model)[0])
            self.assertTrue(widget.table.item(0, 2).icon().isNull())
            spec.resolve_context(limiter, [], [])
            self.assertTrue(spec.repair_status(limiter.model).pending)

    def test_collars_corrected_without_mutating_root_fingers_timing_or_source(self):
        skeleton,clip,_,offsets = fixture()
        skeleton.joints.append(Joint('left_finger',20,JointTransform(translation=(.02,0,0))))
        finger = JointAnimationTrack('left_finger',rotation_keys=[QuatKeyframe(0,(0,0,.3,.9539392014))])
        clip.tracks.append(finger)
        for j in (13,14):
            clip.tracks[j].rotation_keys = [QuatKeyframe(t,tuple(Rotation.from_euler('xz',[70,55],degrees=True).as_quat())) for t in (0,.1)]
        original = clip.to_dict()
        with patch('echograph.motion.rig_limiter.solve_constrained_ik') as solve:
            output,report = limit_clip(skeleton,clip,default_preview_limits(),volume_settings(),ik_repair=False)
        solve.assert_not_called()
        self.assertFalse(report['ik_repair_enabled'])
        self.assertEqual(clip.to_dict(),original)
        self.assertEqual(output.tracks[0],clip.tracks[0])
        self.assertEqual(output.tracks[-1],finger)
        self.assertEqual((output.start_time,output.end_time,output.sample_rate_hz),(clip.start_time,clip.end_time,clip.sample_rate_hz))
        self.assertEqual(report['corrected_frames'],3)
        for time in (0,.05,.1):
            for j in (13,14):
                local = evaluate_rig_at_time(skeleton,output,time).local_transforms
                child = next(i for i,p in enumerate(PARENTS) if p==j)
                swing,twist = rotation_parts(local[j].rotation,offsets[child])
                self.assertLessEqual(np.degrees(swing.magnitude()),15+1e-7)
                self.assertLessEqual(abs(np.degrees(twist)),15+1e-7)

    def test_collision_repair_reduces_overlap_and_keeps_limits_and_lengths(self):
        _,_,rotations,offsets = fixture(5)
        for j,a in ((16,-80),(17,80),(18,-100),(19,100)):
            rotations[:,j] = Rotation.from_euler('y',a,degrees=True).as_matrix()
        roots = np.zeros((5,3))
        limits = default_preview_limits()
        output,report = limit_rotations(rotations,offsets,roots,20,limits,volume_settings())
        self.assertGreater(report['max_proxy_overlap_before'],.03)
        self.assertLess(report['max_proxy_overlap_after'],.003*rig_scale(offsets))
        for frame in output:
            points,_ = world_pose(frame,offsets,np.zeros(3))
            for j in range(1,22):
                self.assertAlmostEqual(np.linalg.norm(points[j]-points[PARENTS[j]]),np.linalg.norm(offsets[j]))
            for j in (16,17):
                axis = offsets[18 if j==16 else 19]
                swing,twist = rotation_parts(Rotation.from_matrix(frame[j]).as_quat(),axis)
                self.assertLessEqual(np.degrees(swing.magnitude()),80+1e-7)
                self.assertLessEqual(abs(np.degrees(twist)),100+1e-7)
        changes = Rotation.from_matrix(output[1:,16]).inv()*Rotation.from_matrix(output[:-1,16])
        self.assertLess(max(changes.magnitude()),.1)

    def test_bvh_order_and_export_roundtrip_preserve_root_motion(self):
        skeleton,clip,rotations,offsets = fixture()
        rotations[:,13] = Rotation.from_euler('x',80,degrees=True).as_matrix()
        positions = np.zeros((3,22,3))
        positions[:,0] = [[0,1,0],[1,1,0],[2,1,0]]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'source.bvh'
            write_bvh(path,positions,20,rotations,offsets)
            original = path.read_bytes()
            loaded = ingest_bvh_animation_data(path)
            indices,_ = rig_layout(loaded.skeleton)
            self.assertNotEqual(indices,list(range(22)))
            output,_ = limit_clip(loaded.skeleton,loaded.clips[0],default_preview_limits(),volume_settings(),collisions=False)
            exported = Path(folder)/'limited.bvh'
            spec.export_context(dict(skeleton=loaded.skeleton,clip=output),exported)
            result = ingest_bvh_animation_data(exported)
            for time in (0,.05,.1):
                a = evaluate_rig_at_time(loaded.skeleton,output,time)
                b = evaluate_rig_at_time(result.skeleton,result.clips[0],time)
                np.testing.assert_allclose(a.global_matrices,b.global_matrices,atol=1e-7)
            self.assertEqual(path.read_bytes(),original)
            with self.assertRaises(FileExistsError):
                spec.export_context(dict(skeleton=loaded.skeleton,clip=output),path)

    def test_collection_connection_cache_invalidation_and_scene_output(self):
        from nodes.mocap_collection import spec as collection
        from nodes.scene import spec as scene_spec
        _,_,rotations,offsets = fixture()
        positions = np.zeros((3,22,3))
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder)/f'{n}.bvh' for n in range(2)]
            for n,path in enumerate(paths):
                rotations[:,13] = Rotation.from_euler('x',50+n*10,degrees=True).as_matrix()
                write_bvh(path,positions,20,rotations,offsets)
            scene = Scene()
            source = scene.add_node(GraphNode('Collection',kind='mocap_collection'),QtCore.QPointF(0,0))
            limiter = scene.add_node(GraphNode('Limiter',kind='rig_limiter'),QtCore.QPointF(300,0))
            output = scene.add_node(GraphNode('Scene',kind='scene'),QtCore.QPointF(600,0))
            collection.add_paths(source.model,paths,scene)
            scene.add_edge('Collection','Limiter')
            scene._edges[-1].dst_port_name = 'animation'
            scene.add_edge('Limiter','Scene')
            errors,warnings = [],[]
            first = retarget._context_for_source_item(limiter,'rig_limiter',errors,warnings)
            self.assertEqual(errors,[])
            again = spec.resolve_context(limiter,[],[])
            self.assertIs(first['clip'],again['clip'])
            controls = limiter._plugin_proxies[0].widget()
            self.assertTrue(controls.ik_repair.isChecked())
            asset = spec.build_scene_asset(limiter)
            self.assertIs(asset['fbx_rig_context']['clip'],first['clip'])
            assets = scene_spec._collect_assets(output)
            self.assertTrue(any(a['fbx_rig_context']['clip'] is first['clip'] for a in assets))
            controls.ik_repair.setChecked(False)
            with patch.object(spec,'run_repair',side_effect=lambda function,parent=None:function(lambda f,n:None)), \
                 patch.object(spec,'limit_clip',wraps=limit_clip) as repair:
                spec.prepare_repair(limiter)
                disabled = spec.resolve_context(limiter,[],[])
            self.assertIsNot(disabled['clip'],first['clip'])
            self.assertFalse(disabled['clip'].metadata['rig_limiter']['ik_repair_enabled'])
            repair.assert_called_once()
            self.assertFalse(repair.call_args.kwargs['ik_repair'])
            controls.ik_repair.setChecked(True)
            with patch.object(spec,'limit_clip',side_effect=AssertionError('Graph refresh must not repair')):
                pending = spec.resolve_context(limiter,[],[])
                self.assertIs(pending['clip'],disabled['clip'])
                self.assertTrue(controls.repair_button.property('repairPending'))
            with patch.object(spec,'run_repair',side_effect=lambda function,parent=None:function(lambda f,n:None)):
                spec.prepare_repair(limiter)
            enabled = spec.resolve_context(limiter,[],[])
            self.assertIsNot(enabled['clip'],disabled['clip'])
            self.assertTrue(enabled['clip'].metadata['rig_limiter']['ik_repair_enabled'])
            self.assertFalse(controls.repair_button.property('repairPending'))
            self.assertFalse(controls.thorough.isChecked())
            fast_key = limiter.model._rig_limiter_cache[0]
            controls.thorough.setChecked(True)
            self.assertTrue(controls.repair_button.property('repairPending'))
            with patch.object(spec,'run_repair',side_effect=lambda function,parent=None:function(lambda f,n:None)), \
                 patch.object(spec,'limit_clip',wraps=limit_clip) as repair:
                spec.prepare_repair(limiter)
            self.assertTrue(repair.call_args.kwargs['thorough_repair'])
            self.assertTrue(limiter.model._rig_limiter_cache[1][1]['thorough_repair'])
            self.assertNotEqual(limiter.model._rig_limiter_cache[0],fast_key)
            self.assertFalse(controls.repair_button.property('repairPending'))
            collection.save_state(source.model,collection.entries(source.model),1,scene)
            with patch.object(spec,'limit_clip',side_effect=AssertionError('Input change must not repair')):
                changed = spec.resolve_context(limiter,[],[])
            self.assertIsNot(changed['clip'],first['clip'])
            self.assertNotIn('rig_limiter',changed['clip'].metadata)
            self.assertTrue(controls.repair_button.property('repairPending'))
            self.assertIn('animation',limiter.input_port_names())
            self.assertIn('animation',limiter.output_port_names())
            proxy = limiter._plugin_proxies[0]
            self.assertGreaterEqual(limiter.height,proxy.pos().y()+proxy.size().height()+10)
            self.assertGreaterEqual(proxy.size().height(),proxy.widget().minimumSizeHint().height())

    def test_panel_saves_node_settings_and_checked_batch_keeps_collection_and_sources(self):
        from nodes.mocap_collection import spec as collection
        from echograph.persistence import _node_to_dict
        _,_,rotations,offsets = fixture(2)
        rotations[:,13] = Rotation.from_euler('x',60,degrees=True).as_matrix()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = [root/f'{n}.bvh' for n in range(3)]
            for path in paths:
                write_bvh(path,np.zeros((2,22,3)),20,rotations,offsets)
            originals = [path.read_bytes() for path in paths]
            scene = Scene()
            source = scene.add_node(GraphNode('Collection',kind='mocap_collection'),QtCore.QPointF())
            limiter = scene.add_node(GraphNode('Limiter',kind='rig_limiter'),QtCore.QPointF())
            collection.add_paths(source.model,paths,scene)
            rows = collection.entries(source.model)
            rows[1]['checked'] = False
            collection.save_state(source.model,rows,2,scene)
            source_state = json.loads(json.dumps(source.model.params))
            scene.add_edge('Collection','Limiter')
            scene._edges[-1].dst_port_name = 'animation'
            guide = spec.build_scene_asset(limiter,preview=True)['fbx_rig_context']['priormdm_rig_debug']
            guide['joint_limits'][13]['twist_max'] = 4.
            guide['joint_limits'][13]['twist_min'] = -4.
            guide['volumes']['left_elbow'] = [.045,.075]
            guide['collisions'] = False
            guide['save_settings'](guide)
            self.assertIn('rig_settings',[p['name'] for p in _node_to_dict(limiter.model)['params']])
            controls = limiter._plugin_proxies[0].widget()
            self.assertFalse(controls.collisions.isChecked())
            controls.ik_repair.setChecked(False)
            saved_params = json.loads(json.dumps(_node_to_dict(limiter.model)['params']))
            restored = scene.add_node(GraphNode('Restored',kind='rig_limiter',params=saved_params),QtCore.QPointF())
            self.assertFalse(restored._plugin_proxies[0].widget().ik_repair.isChecked())
            directory = root/'output'
            directory.mkdir()
            with patch.object(QtWidgets.QFileDialog,'getExistingDirectory',return_value=str(directory)), \
                 patch.object(spec,'run_repair',side_effect=lambda function,parent=None:function(lambda f,n:None)), \
                 patch.object(spec,'limit_clip',wraps=limit_clip) as repair:
                controls.export_collection()
            self.assertEqual(repair.call_count,2)
            self.assertTrue(all(call.kwargs['ik_repair'] is False for call in repair.call_args_list))
            exported = list(directory.glob('*.bvh'))
            self.assertEqual(len(exported),2,controls.status.text())
            self.assertEqual(source.model.params,source_state)
            self.assertEqual([path.read_bytes() for path in paths],originals)
            for path in exported:
                loaded = ingest_bvh_animation_data(path)
                index = loaded.skeleton.joint_names.index('left_collar')
                q = evaluate_rig_at_time(loaded.skeleton,loaded.clips[0],0).local_transforms[index].rotation
                self.assertLessEqual(abs(np.degrees(rotation_parts(q,[1,0,0])[1])),4.00001)

    def test_saving_posed_capsule_settings_keeps_last_repair_until_button_click(self):
        from nodes.mocap_collection import spec as collection
        from nodes.scene import spec as scene_spec
        from echograph.persistence import serialize_scene
        from unittest.mock import Mock

        _,_,rotations,offsets = fixture(3)
        rotations[:,13] = Rotation.from_euler('x',[10,40,60],degrees=True).as_matrix()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'motion.bvh'
            write_bvh(path,np.zeros((3,22,3)),20,rotations,offsets)
            scene = Scene()
            self.addCleanup(scene.clear)
            source = scene.add_node(GraphNode('Input',kind='mocap_collection'),QtCore.QPointF())
            limiter = scene.add_node(GraphNode('Limiter',kind='rig_limiter'),QtCore.QPointF())
            output = scene.add_node(GraphNode('Scene',kind='scene'),QtCore.QPointF())
            collection.add_paths(source.model,[path],scene)
            scene.add_edge('Input','Limiter')
            scene._edges[-1].dst_port_name = 'animation'
            scene.add_edge('Limiter','Scene')
            controls = limiter._plugin_proxies[0].widget()
            self.assertTrue(controls.repair_button.property('repairPending'))
            with patch.object(spec,'run_repair',side_effect=lambda function,parent=None:function(lambda f,n:None)):
                spec.prepare_repair(limiter)
            old = spec.resolve_context(limiter,[],[])
            old_pose = evaluate_rig_at_time(old['skeleton'],old['clip'],.05).global_matrices.copy()
            self.assertFalse(controls.repair_button.property('repairPending'))

            view = PanelViewport()
            self.addCleanup(view.deleteLater)
            view.context = spec.build_scene_asset(limiter,preview=True)['fbx_rig_context']
            view._mgl_fbx_context_timeline_sample_seconds = lambda *args:.05
            view._mgl_scene_skeleton_request_active = Mock()
            panel = RigPreviewPanel(view)
            panel.sync(view.data('left_ankle'),'Left foot capsule')
            guide = view.context['priormdm_rig_debug']
            self.assertTrue({'left_ankle','right_ankle'} <=
                            {s['joint'] for s in guide['shapes'] if s['kind']=='collision'})
            refreshed = []
            scene.paramChanged.connect(lambda *args:refreshed.append(scene_spec._collect_assets(output)))
            with patch.object(spec,'limit_clip',wraps=limit_clip) as repair:
                panel.sections['left_ankle'].volume_fields[1].setValue(.085*rig_scale(guide['offsets']))
                panel.save()
                saved = serialize_scene(scene)
                (Path(folder)/'workflow.json').write_text(json.dumps(saved),encoding='utf-8')
                self.assertTrue(refreshed)
                current = spec.resolve_context(limiter,[],[])
                self.assertIs(current['clip'],old['clip'])
                np.testing.assert_array_equal(evaluate_rig_at_time(current['skeleton'],current['clip'],.05).global_matrices,
                                              old_pose)
                self.assertAlmostEqual(spec.settings(limiter.model)[1]['left_ankle'][1],.085,delta=.0001)
                self.assertTrue(controls.repair_button.property('repairPending'))
                self.assertIn('Showing previous repair',controls.status.text())
                repair.assert_not_called()
                # A single-take export must not silently repair pending changes.
                with patch.object(QtWidgets.QFileDialog,'getExistingDirectory') as choose:
                    controls.export()
                    choose.assert_not_called()
                repair.assert_not_called()
                self.assertIn('Press Repair',controls.status.text())

            # Failed/cancelled work retains the previous result and red indicator.
            with patch.object(spec,'run_repair',side_effect=ValueError('cancelled')):
                with self.assertRaisesRegex(ValueError,'cancelled'):
                    spec.prepare_repair(limiter)
            self.assertIs(spec.resolve_context(limiter,[],[])['clip'],old['clip'])
            self.assertTrue(controls.repair_button.property('repairPending'))

            window = SimpleNamespace(open_scene_assets=Mock(return_value=True),gl_view=view)
            with patch.object(scene,'views',return_value=[SimpleNamespace(window=lambda:window)]), \
                 patch.object(spec,'run_repair',side_effect=lambda function,parent=None:function(lambda f,n:None)), \
                 patch.object(spec,'limit_clip',wraps=limit_clip) as repair:
                controls.repair_button.click()
                repair.assert_called_once()
                self.assertAlmostEqual(repair.call_args.args[3]['left_ankle'][1],.085,delta=.0001)
                self.assertFalse(controls.repair_button.property('repairPending'),controls.status.text())
                repaired = spec.resolve_context(limiter,[],[])
                self.assertIsNot(repaired['clip'],old['clip'])
                controls.repair_button.click()
                repair.assert_called_once()
            proxy = limiter._plugin_proxies[0]
            self.assertGreaterEqual(limiter.height,proxy.pos().y()+proxy.size().height()+10)
            self.assertGreaterEqual(proxy.size().height(),controls.minimumSizeHint().height())

            # Workflow reload restores settings without triggering computation.
            params = next(n['params'] for n in saved['nodes'] if n['name']=='Limiter')
            restored = scene.add_node(GraphNode('Restored',kind='rig_limiter',params=params),QtCore.QPointF())
            scene.add_edge('Input','Restored')
            scene._edges[-1].dst_port_name = 'animation'
            with patch.object(spec,'limit_clip',wraps=limit_clip) as repair:
                restored_context = spec.resolve_context(restored,[],[])
                self.assertIn('rig_limiter',restored_context['clip'].metadata)
                self.assertFalse(restored._plugin_proxies[0].widget().repair_button.property('repairPending'))
                repair.assert_not_called()

    def test_repair_worker_completion_and_cancellation(self):
        self.assertEqual(spec.run_repair(lambda progress: ('done',progress(1,2)))[0],'done')
        def fail(progress):
            raise ValueError('cancelled')
        with self.assertRaisesRegex(ValueError,'cancelled'):
            spec.run_repair(fail)

    def test_refresh_handles_controls_rebuilt_while_resolving_input(self):
        from shiboken6 import delete, isValid
        scene = Scene()
        limiter = scene.add_node(GraphNode('Limiter',kind='rig_limiter'),QtCore.QPointF())
        controls = limiter._plugin_proxies[0].widget()
        def rebuild(*args,**kwargs):
            delete(controls)
        with patch.object(spec,'resolve_context',side_effect=rebuild):
            controls.refresh()
        self.assertFalse(isValid(controls))
        APP.processEvents()  # The queued initial refresh must be cancelled with its widget.

    def test_worker_updates_are_queued_to_the_ui_thread_and_reentry_is_rejected(self):
        from time import sleep
        threads,stages = [],[]
        original = spec._RepairProgressDialog.update_progress
        original_stage = spec._RepairProgressDialog.update_stage
        @QtCore.Slot(int,int)
        def update_progress(dialog,frame,total):
            threads.append(QtCore.QThread.currentThread() == APP.thread())
            original(dialog,frame,total)
        @QtCore.Slot(str)
        def update_stage(dialog,label):
            threads.append(QtCore.QThread.currentThread() == APP.thread())
            stages.append(label)
            original_stage(dialog,label)
        def work(progress):
            with self.assertRaisesRegex(ValueError,'already running'):
                spec.run_repair(lambda p:None)
            progress.set_stage('Checking between-frame contacts...')
            progress(1,2)
            sleep(.06)
            progress(2,2)
            return 'done'
        with patch.object(spec._RepairProgressDialog,'update_progress',update_progress), \
             patch.object(spec._RepairProgressDialog,'update_stage',update_stage):
            self.assertEqual(spec.run_repair(work),'done')
        self.assertGreaterEqual(len(threads),3)
        self.assertTrue(all(threads))
        self.assertEqual(stages,['Checking between-frame contacts...'])

    def test_cancellation_during_refinement_and_streaming_export_keeps_source(self):
        skeleton,clip,_,_ = fixture(frames=8)
        source = clip.to_dict()
        def cancel_refinement(frame,total):
            if frame >= 650:
                raise ValueError('cancelled between frames')
        with self.assertRaisesRegex(ValueError,'cancelled between frames'):
            limit_clip(skeleton,clip,default_preview_limits(),{},progress=cancel_refinement)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'cancelled.bvh'
            observations = []
            def cancel_export(frame,total):
                observations.append(path.exists())
                if frame == 3:
                    raise ValueError('cancelled saving')
            with self.assertRaisesRegex(ValueError,'cancelled saving'):
                spec.export_context(dict(skeleton=skeleton,clip=clip),path,progress=cancel_export)
            self.assertTrue(all(observations))  # Frames are sampled while the output is streamed.
            self.assertFalse(path.exists())
            path.write_text('existing BVH',encoding='utf-8')
            with self.assertRaises(FileExistsError):
                spec.export_context(dict(skeleton=skeleton,clip=clip),path)
            self.assertEqual(path.read_text(encoding='utf-8'),'existing BVH')
        self.assertEqual(clip.to_dict(),source)

    def test_stop_button_stays_visible_until_worker_exits(self):
        from time import sleep
        observations = []
        owner = QtWidgets.QWidget()
        def stop():
            dialog = next((w for w in APP.topLevelWidgets() if isinstance(w,spec._RepairProgressDialog)
                           and w.isVisible()),None)
            if dialog is None:
                QtCore.QTimer.singleShot(5,stop)
                return
            observations.extend([dialog.parentWidget() is owner,
                                 not bool(dialog.windowFlags() & QtCore.Qt.WindowStaysOnTopHint),
                                 '#b91c1c' in dialog.stop.styleSheet()])
            dialog.stop.click()
            observations.extend([dialog.isVisible(),not dialog.stop.isEnabled()])
        def work(progress):
            for i in range(1000):
                sleep(.001)
                progress(i,1000)
            return 'should not finish'
        QtCore.QTimer.singleShot(0,stop)
        try:
            with self.assertRaisesRegex(ValueError,'cancelled'):
                spec.run_repair(work,owner)
            self.assertEqual(observations,[True]*5)
        finally:
            owner.deleteLater()

    def test_cancellation_and_bad_skeleton_are_reported(self):
        skeleton,clip,_,_ = fixture()
        def cancel(frame,total):
            raise ValueError('cancelled')
        with self.assertRaisesRegex(ValueError,'cancelled'):
            limit_clip(skeleton,clip,default_preview_limits(),volume_settings(),progress=cancel)
        skeleton.joints[13].name = 'unknown'
        with self.assertRaisesRegex(ValueError,'Missing joints'):
            limit_clip(skeleton,clip,default_preview_limits(),volume_settings())


if __name__ == '__main__':
    unittest.main()
