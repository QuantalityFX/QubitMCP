"""Hand geometry, hierarchical repair, and the reusable IK contract."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation
from PySide6 import QtCore, QtGui, QtWidgets

from echograph.motion.hands import add_preview_hands, FINGER_NAMES
from echograph.motion.hand_rig import (full_rig_layout, expanded_limits, HAND_LIMITS,
                                     hand_limits_from_fields, primary_child)
from echograph.motion.rig_limiter import limit_clip, limit_rotations
from echograph.motion.rig_limits import clamp_preview_rotation, rotation_parts
from echograph.motion.rig_preview import build_guide, create_rig_asset
from echograph.motion.rig_preview_settings import (default_preview_limits, load_preview_hand_limits,
                                                load_preview_volumes, save_preview_limits)
from echograph.motion.rig_volumes import ColliderModel, rig_scale, volume_settings, constrain_volume_rotation
from echograph.rigging.ik import forward_kinematics, solve_constrained_ik
from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
from echograph.ui.priormdm_rig_panel import RigPreviewPanel
from echograph.ui.priormdm_rig_overlay import draw_rig_guide, pick_guide_joint
from nodes.rig_limiter.spec import export_context
from tests.motion.test_rig_limiter import fixture
from tests.motion.test_rig_preview_settings import PanelViewport

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def hand_fixture(frames=2, *, fingertip_contact=False):
    skeleton,clip,_,_ = fixture(frames)
    skeleton = add_preview_hands(skeleton)
    indices,names,parents,offsets = full_rig_layout(skeleton)
    local = np.tile(np.eye(3),(len(names),1,1))
    if fingertip_contact:
        points,_ = forward_kinematics(local,offsets,parents)
        # A thigh lies just beyond the extended fingertips. The palm/arm are
        # clear, so curling the fingers alone can resolve this contact.
        offsets[1] = points[names.index('left_middle_tip')]+np.array([.035,.05,0])*rig_scale(offsets)
        skeleton.joints[indices[1]].local_bind.translation = tuple(offsets[1])
        for key in clip.tracks[1].translation_keys:
            key.value = tuple(offsets[1])
    return skeleton,clip,indices,names,parents,offsets,local


class ReusableIKTests(unittest.TestCase):
    def test_targets_and_limits_work_without_motion_nodes_or_named_humanoid_joints(self):
        offsets = np.array([[.25,.1,0],[1.,0,0],[1.,0,0]])
        parents = (-1,0,1)
        local = np.tile(np.eye(3),(3,1,1))
        before = local.copy()
        def project(j,q):
            angle = Rotation.from_quat(q).as_rotvec()[2]
            return Rotation.from_euler('z',np.clip(angle,0,np.pi/2)).as_quat()
        result = solve_constrained_ik(local,offsets,parents,joint_axes={0:[[0,0,1]],1:[[0,0,1]]},
                                      project_rotation=project,targets={2:[1.25,1.1,0]})
        self.assertTrue(result.converged)
        np.testing.assert_allclose(result.points[2],[1.25,1.1,0],atol=5e-4)
        np.testing.assert_array_equal(local,before)
        np.testing.assert_allclose(np.linalg.norm(np.diff(result.points,axis=0),axis=1),1)
        unreachable = solve_constrained_ik(local,offsets,parents,joint_axes={0:[[0,0,1]],1:[[0,0,1]]},
                                           project_rotation=project,targets={2:[-10,0,0]})
        self.assertFalse(unreachable.converged)
        self.assertGreater(unreachable.max_error,5)
        angles = Rotation.from_matrix(unreachable.rotations[:2]).as_rotvec()[:,2]
        self.assertTrue(np.all((angles >= -1e-8) & (angles <= np.pi/2+1e-8)))

    def test_custom_geometric_residual_and_fixed_joints(self):
        local = np.tile(np.eye(3),(3,1,1))
        offsets = np.array([[0.,0,0],[1.,0,0],[1.,0,0]])
        result = solve_constrained_ik(local,offsets,(-1,0,1),joint_axes={1:[[0,0,1]]},
            constraint_residual=lambda points,world,pose:np.array([max(0.,.25-points[2,1])]))
        self.assertTrue(result.converged)
        np.testing.assert_array_equal(result.rotations[[0,2]],local[[0,2]])
        self.assertGreater(result.points[2,1],.249)
        with self.assertRaisesRegex(ValueError,'sizes'):
            forward_kinematics(local,offsets,(-1,0))
        with self.assertRaisesRegex(ValueError,'previous'):
            solve_constrained_ik(local,offsets,(-1,0,1),joint_axes={1:[[0,0,1]]},
                                  targets={2:[0,0,0]},previous_parameters=[0,0])


class HandColliderTests(unittest.TestCase):
    def test_all_phalanges_and_palms_follow_actual_posed_endpoints(self):
        _,_,_,names,parents,offsets,local = hand_fixture()
        for j,name in enumerate(names):
            if name in FINGER_NAMES:
                limit = HAND_LIMITS[name]
                local[j] = Rotation.from_rotvec(np.asarray(limit['axis'])*.4).as_matrix()
        points,world = forward_kinematics(local,offsets,parents)
        model = ColliderModel(names,parents,offsets)
        starts,ends = model.endpoints(points,world)
        self.assertEqual(len(model.names),47)
        self.assertTrue({'left_ankle','right_ankle'} <= set(model.names))
        for name in FINGER_NAMES:
            row = model.names.index(name)
            j = names.index(name)
            tip = name[:-1]+'_tip' if name.endswith('3') else name[:-1]+str(int(name[-1])+1)
            np.testing.assert_allclose(starts[row],points[j])
            np.testing.assert_allclose(ends[row],points[names.index(tip)])
        for side in ('left','right'):
            row = model.names.index(side+'_wrist')
            self.assertGreater(np.linalg.norm(ends[row]-starts[row]),.04*rig_scale(offsets))
        matrices = np.tile(np.eye(4),(len(names),1,1))
        matrices[:,:3,3],matrices[:,:3,:3] = points,world
        _,guide = build_guide(offsets,matrices,names=names,parents=parents,
                              joint_limits=expanded_limits(names,default_preview_limits()))
        drawn = {s['joint'] for s in guide['shapes'] if s['kind']=='collision'}
        self.assertTrue(set(FINGER_NAMES) <= drawn)

    def test_distal_capsule_exists_without_explicit_tip_joint(self):
        _,_,_,names,parents,offsets,_ = hand_fixture()
        keep = [i for i,n in enumerate(names) if not n.endswith('_tip')]
        lookup = {old:new for new,old in enumerate(keep)}
        parents = tuple(lookup.get(parents[i],-1) for i in keep)
        names = tuple(names[i] for i in keep)
        offsets = offsets[keep]
        local = np.tile(np.eye(3),(len(names),1,1))
        model = ColliderModel(names,parents,offsets)
        starts,ends = model.endpoints(*forward_kinematics(local,offsets,parents))
        for row,name in enumerate(model.names):
            if name in FINGER_NAMES and name.endswith('3'):
                self.assertGreater(np.linalg.norm(ends[row]-starts[row]),.01)
        exact_tip = [.037, .004, .002]
        model = ColliderModel(names,parents,offsets,end_sites={'left_index3':[exact_tip]})
        starts,ends = model.endpoints(*forward_kinematics(local,offsets,parents))
        row = model.names.index('left_index3')
        np.testing.assert_allclose(ends[row]-starts[row],exact_tip)

    def test_mirrored_bend_and_base_spread_limits_are_enforced(self):
        _,_,_,names,parents,offsets,_ = hand_fixture()
        limits = expanded_limits(names,default_preview_limits())
        for j,name in enumerate(names):
            if name not in FINGER_NAMES:
                continue
            limit = limits[j]
            axis = np.asarray(limit['axis'])
            desired = Rotation.from_rotvec(axis*np.deg2rad(limit['flex']+25))
            if 'spread_axis' in limit:
                desired = Rotation.from_rotvec(np.asarray(limit['spread_axis'])*np.deg2rad(60))*desired
            q = clamp_preview_rotation(j,desired.as_quat(),offsets[primary_child(j,names,parents)],limits=limits)
            swing,flex = rotation_parts(q,axis)
            self.assertAlmostEqual(np.degrees(flex),limit['flex'],places=6)
            if 'spread_axis' in limit:
                self.assertLessEqual(abs(np.degrees(swing.as_rotvec()@np.asarray(limit['spread_axis']))),limit['twist']+1e-6)
            else:
                self.assertLess(swing.magnitude(),1e-7)

    def test_fingers_clear_contact_before_moving_the_arm(self):
        _,_,_,names,parents,offsets,local = hand_fixture(fingertip_contact=True)
        limits = expanded_limits(names,default_preview_limits())
        with patch('echograph.motion.rig_limiter.solve_constrained_ik') as solve:
            untouched,disabled_report = limit_rotations(local[None],offsets,np.zeros((1,3)),20,limits,{},
                names=names,parents=parents,ik_repair=False)
        solve.assert_not_called()
        np.testing.assert_allclose(untouched[0],local,atol=1e-7)
        self.assertFalse(disabled_report['ik_repair_enabled'])
        self.assertEqual(disabled_report['remaining_proxy_overlap_frames'],1)
        self.assertEqual(disabled_report['finger_collision_corrected_frames'],0)
        self.assertEqual(disabled_report['arm_ik_corrected_frames'],0)
        output,report = limit_rotations(local[None],offsets,np.zeros((1,3)),20,limits,{},names=names,parents=parents)
        self.assertTrue(report['ik_repair_enabled'])
        self.assertEqual(report['finger_collider_count'],30)
        self.assertEqual(report['finger_collision_corrected_frames'],1)
        self.assertEqual(report['arm_ik_corrected_frames'],0)
        self.assertEqual(report['remaining_proxy_overlap_frames'],0)
        np.testing.assert_array_equal(output[0,:22],local[:22])
        self.assertLess(report['max_proxy_overlap_after'],.001*rig_scale(offsets))

    def test_finger_limits_force_arm_ik_without_stretching_or_moving_root(self):
        _,_,_,names,parents,offsets,local = hand_fixture(fingertip_contact=True)
        limits = expanded_limits(names,default_preview_limits())
        for j,name in enumerate(names):
            if name in FINGER_NAMES:
                limits[j].update(flex=0.,flex_min=0.,twist=0.,twist_min=0.,twist_max=0.)
        output,report = limit_rotations(np.repeat(local[None],3,axis=0),offsets,np.zeros((3,3)),20,
                                        limits,{},names=names,parents=parents)
        self.assertGreater(report['arm_ik_corrected_frames'],0)
        self.assertEqual(report['finger_collision_corrected_frames'],0)
        self.assertEqual(report['remaining_proxy_overlap_frames'],0)
        finger_indices = [names.index(n) for n in FINGER_NAMES]
        np.testing.assert_allclose(output[:,finger_indices],np.tile(np.eye(3),(3,30,1,1)),atol=1e-7)
        for frame in output:
            points,_ = forward_kinematics(frame,offsets,parents)
            for j,p in enumerate(parents):
                if p >= 0:
                    self.assertAlmostEqual(np.linalg.norm(points[j]-points[p]),np.linalg.norm(offsets[j]),places=7)
            np.testing.assert_array_equal(frame[0],local[0])
            for j in (13,16,18,20):
                q = Rotation.from_matrix(frame[j]).as_quat()
                expected = clamp_preview_rotation(j,q,offsets[primary_child(j,names,parents)],limits=limits)
                np.testing.assert_allclose(Rotation.from_quat(expected).as_matrix(),frame[j],atol=1e-7)
        speed = (Rotation.from_matrix(output[:-1,16]).inv()*Rotation.from_matrix(output[1:,16])).magnitude()
        self.assertLess(max(speed),.1)

    def test_manual_finger_rotation_stops_at_contact(self):
        _,_,_,names,parents,offsets,local = hand_fixture(fingertip_contact=True)
        limits = expanded_limits(names,default_preview_limits())
        j = names.index('left_middle1')
        local[j] = Rotation.from_euler('z',-70,degrees=True).as_matrix()
        accepted = constrain_volume_rotation(j,Rotation.identity().as_quat(),local,offsets,
            limits=limits,names=names,parents=parents)
        bend = -np.degrees(Rotation.from_quat(accepted).as_rotvec()[2])
        self.assertGreater(bend,1)
        self.assertLess(bend,70)

    def test_unreachable_contacts_report_overlap_without_relaxing_limits(self):
        _,_,_,names,parents,offsets,local = hand_fixture(fingertip_contact=True)
        limits = expanded_limits(names,default_preview_limits())
        for limit in limits.values():
            if 'flex' in limit:
                limit.update(flex=0.,flex_min=0.)
            else:
                limit.update(swing=0.,swing_min=0.)
            limit.update(twist=0.,twist_min=0.,twist_max=0.)
        output,report = limit_rotations(local[None],offsets,np.zeros((1,3)),20,limits,{},names=names,parents=parents)
        self.assertEqual(report['remaining_proxy_overlap_frames'],1)
        np.testing.assert_allclose(output[0],local,atol=1e-7)

    def test_full_clip_roundtrip_reuses_finger_corrections_and_preserves_source(self):
        skeleton,clip,_,names,parents,offsets,_ = hand_fixture(fingertip_contact=True)
        source = clip.to_dict()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'original.bvh'
            export_context(dict(skeleton=skeleton,clip=clip),path)
            loaded = ingest_bvh_animation_data(path)
            output,report = limit_clip(loaded.skeleton,loaded.clips[0],default_preview_limits(),{})
            self.assertEqual(report['finger_collider_count'],30)
            saved = Path(folder)/'limited.bvh'
            export_context(dict(skeleton=loaded.skeleton,clip=output),saved)
            again = ingest_bvh_animation_data(saved)
            for time in (0,.05):
                expected = evaluate_rig_at_time(loaded.skeleton,output,time)
                actual = evaluate_rig_at_time(again.skeleton,again.clips[0],time)
                np.testing.assert_allclose(expected.global_matrices,actual.global_matrices,atol=1e-7)
            self.assertEqual(clip.to_dict(),source)
            self.assertEqual(loaded.skeleton.joint_names,again.skeleton.joint_names)

    def test_finger_editor_radii_limits_save_reset_and_preview_hands(self):
        view = PanelViewport()
        self.addCleanup(view.deleteLater)
        skeleton,clip,indices,names,parents,offsets,_ = hand_fixture()
        _,guide = build_guide(offsets,names=names,parents=parents,joint_limits=expanded_limits(names,default_preview_limits()))
        guide.update(collisions=False,limits=True,profile='hands',joint_indices=indices)
        view.context = dict(skeleton=skeleton,clip=clip,priormdm_rig_debug=guide)
        panel = RigPreviewPanel(view)
        name = 'left_index1'
        panel.sync(view.data(name),name)
        self.assertTrue(all(spin.isEnabled() for spin in panel.fields.values()))
        section = panel.sections[name]
        section.fields['bend_max'].setValue(35.)
        section.volume_fields[1].setValue(.014)
        token = view._mgl_scene_skeleton_joint_owner_token('Rig',name)
        view._set_owner_rot_deg(token,(0,0,-100),False)
        local = evaluate_rig_at_time(skeleton,view.context['clip'],0).local_transforms
        self.assertAlmostEqual(np.degrees(rotation_parts(local[names.index(name)].rotation,[0,0,-1])[1]),35.)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'settings.json'
            with patch('echograph.motion.rig_preview_settings.APP_SETTINGS_PATH',path):
                panel.save()
                asset = create_rig_asset('standardman',None,Path(folder)/'preview','Rig')
            self.assertEqual(load_preview_hand_limits(path)[name]['flex'],35.)
            self.assertAlmostEqual(load_preview_volumes(path)[name][1],.014/rig_scale(offsets),places=4)
            self.assertTrue(set(FINGER_NAMES) <= set(asset['fbx_rig_context']['skeleton'].joint_names))
            self.assertEqual(len([s for s in asset['fbx_rig_context']['priormdm_rig_debug']['shapes']
                                  if s['kind']=='collision' and s['joint'] in FINGER_NAMES]),30)
            section.reset_joint()
            self.assertEqual(section.fields['bend_max'].value(),90.)
            self.assertEqual(guide['volumes'][name],volume_settings()[name])
            panel.resize(320,panel.preferred_height())
            panel.show()
            APP.processEvents()
            self.assertLess(section.volume_fields[-1].mapTo(section,QtCore.QPoint(0,section.volume_fields[-1].height())).y(),section.height())

    def test_finger_overlays_and_capsule_picks_use_bvh_joint_order_mapping(self):
        from types import SimpleNamespace
        skeleton,clip,_,_,_,_,_ = hand_fixture()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'hands.bvh'
            export_context(dict(skeleton=skeleton,clip=clip),path)
            loaded = ingest_bvh_animation_data(path)
            indices,names,parents,offsets = full_rig_layout(loaded.skeleton)
            _,guide = build_guide(offsets,names=names,parents=parents,joint_limits=expanded_limits(names,default_preview_limits()))
            guide.update(profile='hands',collisions=True,limits=True,joint_indices=indices)
            context = dict(skeleton=loaded.skeleton,clip=loaded.clips[0])
            projection = np.eye(4)
            projection[0,0]=projection[1,1]=.7
            projection[1,3]=-.8
            data = dict(rig_guide=guide,rig_context=context,rig_projection=projection,rig_viewport=(0,0,1200,1000),
                        selected='left_index1',owner='Hands',points=[])
            renderer = SimpleNamespace(_mgl_fbx_context_timeline_sample_seconds=lambda c,o:0.,
                                        _mgl_scene_skeleton_display_positions=lambda owner,rows:(rows,{}))
            image = QtGui.QImage(1200,1000,QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(image)
            try:
                draw_rig_guide(renderer,painter,data)
            finally:
                painter.end()
            self.assertEqual(len([s for s in guide['_posed_shapes'][1]['shapes'] if s['kind']=='collision']),47)
            handles = [dict(name=name) for name in names]
            picked = None
            for poly,depth,name in guide['_pick_volumes']:
                if name == 'left_index3':
                    point = poly.boundingRect().center()
                    picked = pick_guide_joint(data,handles,point.x(),point.y(),1)
                    if picked and picked['name'] in FINGER_NAMES:
                        break
            self.assertIsNotNone(picked)
            self.assertIn(picked['name'],FINGER_NAMES)

    def test_export_preserves_real_end_sites_when_tip_joints_are_absent(self):
        from echograph.rigging.delete_joints import filter_joints
        skeleton,clip,_,_,_,_,_ = hand_fixture()
        skeleton,clips,_ = filter_joints(skeleton,[clip],[],{n for n in skeleton.joint_names if n.endswith('_tip')})
        skeleton.metadata['end_sites'] = {'left_index3':[[.037,.004,.002]]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'hand.bvh'
            export_context(dict(skeleton=skeleton,clip=clips[0]),path)
            loaded = ingest_bvh_animation_data(path)
            np.testing.assert_allclose(loaded.skeleton.metadata['end_sites']['left_index3'],[[.037,.004,.002]])


if __name__ == '__main__':
    unittest.main()
