import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from scipy.spatial.transform import Rotation
from echograph.motion.rig_limits import clamp_preview_rotation,rotation_parts,JOINT_LIMITS,PREVIEW_JOINT_LIMITS
from echograph.motion.rig_preview import build_guide,preview_offsets
from echograph.motion.self_collision import world_pose
from echograph.motion.conversion import JOINT_NAMES,PARENTS,REST_DIRECTIONS
from echograph.ui.priormdm_rig_overlay import pick_guide_joint


class PreviewLimitTests(unittest.TestCase):
    def test_collar_bend_and_twist_are_bounded_on_both_sides(self):
        for joint, axis in ((13,np.array([1.,0.,0.])),(14,np.array([-1.,0.,0.]))):
            for bend in (0,10,60):
                for twist in (-80,-8,0,8,80):
                    with self.subTest(joint=JOINT_NAMES[joint],bend=bend,twist=twist):
                        desired=(Rotation.from_euler('z',bend,degrees=True)*
                                 Rotation.from_rotvec(axis*np.deg2rad(twist))).as_quat()
                        actual=clamp_preview_rotation(joint,desired,axis)
                        swing,angle=rotation_parts(actual,axis)
                        self.assertAlmostEqual(np.degrees(swing.magnitude()),min(bend,15.))
                        self.assertAlmostEqual(np.degrees(angle),np.clip(twist,-15.,15.))

    def test_spine_preview_twists_90_degrees_without_changing_generation_policy(self):
        for joint in (3,6,9):
            for angle in (-120,-90,45,90,120):
                q=Rotation.from_euler('y',angle,degrees=True).as_quat()
                actual=clamp_preview_rotation(joint,q,[0,1,0])
                np.testing.assert_allclose(Rotation.from_quat(actual).as_rotvec(),[0,np.deg2rad(np.clip(angle,-90,90)),0],atol=1e-8)
        self.assertEqual(JOINT_LIMITS[3]['twist'],0)
        self.assertEqual(JOINT_LIMITS[9]['twist'],40)

    def test_combined_cones_and_twist_dials_follow_the_posed_bone_axis(self):
        offsets=preview_offsets('standardman')
        rest,_=build_guide(offsets)
        for joint in (1,2,3,6,9,7,8,12,13,14,16,17):
            child=next(i for i,p in enumerate(PARENTS) if p==joint)
            axis=offsets[child]/np.linalg.norm(offsets[child])
            transverse=np.cross(axis,np.eye(3)[np.argmin(abs(axis))])
            local=np.tile(np.eye(3),(22,1,1))
            local[0]=Rotation.from_euler('xyz',[20,30,40],degrees=True).as_matrix()
            twist=min(20.,PREVIEW_JOINT_LIMITS[joint]['twist']/2)
            local[joint]=(Rotation.from_rotvec(transverse*np.deg2rad(30))*Rotation.from_rotvec(axis*np.deg2rad(twist))).as_matrix()
            points,world=world_pose(local,offsets,rest[0])
            matrices=np.tile(np.eye(4),(22,1,1))
            matrices[:,:3,:3],matrices[:,:3,3]=world,points
            _,guide=build_guide(offsets,matrices)
            shapes=[s for s in guide['shapes'] if s['joint']==JOINT_NAMES[joint]]
            self.assertTrue(any(s['kind']=='limit' and s['faces'] for s in shapes))
            dial=next(s for s in shapes if s['kind']=='twist')
            # The dial plane remains perpendicular to the actual posed axis.
            np.testing.assert_allclose((dial['wires'][0]-points[joint])@(world[joint]@axis),0,atol=1e-8)
            pointer=next(s for s in shapes if s['kind']=='pointer' and s.get('follow_swing'))
            np.testing.assert_allclose(pointer['wires'][0][0],points[joint])

    def test_twist_pointer_reaches_both_boundaries_after_bending_and_parent_rotation(self):
        offsets=preview_offsets('standardman')
        rest,_=build_guide(offsets)
        for joint in (1,2,3,6,9,7,8,12,13,14,16,17):
            child=next(i for i,p in enumerate(PARENTS) if p==joint)
            axis=offsets[child]/np.linalg.norm(offsets[child])
            transverse=np.cross(axis,np.eye(3)[np.argmin(abs(axis))])
            transverse/=np.linalg.norm(transverse)
            for sign,endpoint in ((-1,0),(1,-1)):
                with self.subTest(joint=JOINT_NAMES[joint],sign=sign):
                    local=np.tile(np.eye(3),(22,1,1))
                    local[0]=Rotation.from_euler('xyz',[20,30,40],degrees=True).as_matrix()
                    limit=PREVIEW_JOINT_LIMITS[joint]['twist']
                    desired=(Rotation.from_rotvec(transverse*np.deg2rad(30))*
                             Rotation.from_rotvec(axis*np.deg2rad(sign*(limit+15)))).as_quat()
                    local[joint]=Rotation.from_quat(clamp_preview_rotation(joint,desired,axis)).as_matrix()
                    points,world=world_pose(local,offsets,rest[0]+[.3,.2,-.4])
                    matrices=np.tile(np.eye(4),(22,1,1))
                    matrices[:,:3,:3],matrices[:,:3,3]=world,points
                    _,guide=build_guide(offsets,matrices)
                    shapes=[s for s in guide['shapes'] if s['joint']==JOINT_NAMES[joint]]
                    dial=next(s for s in shapes if s['kind']=='twist')
                    pointer=next(s for s in shapes if s['kind']=='limit_hit' and s.get('follow_swing'))
                    np.testing.assert_allclose(pointer['wires'][0][-1],dial['wires'][0][endpoint],atol=1e-8)
                    vertices=np.concatenate(dial['faces']+dial['wires'])
                    np.testing.assert_allclose((vertices-points[joint])@(world[joint]@axis),0,atol=1e-8)
                    self.assertIn('AT LIMIT',guide['labels'][JOINT_NAMES[joint]])

    def test_rotating_either_shoulder_does_not_reuse_stale_timeline_translation(self):
        from echograph.ui.gl_mgl_renderer import MGLRendererMixin
        from echograph.ui.gl_view import GraphGLView
        from echograph.motion.conversion import reconstruct_animation
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
        offsets=preview_offsets('standardman')
        points,guide=build_guide(offsets)
        skeleton,clip,_,_=reconstruct_animation(np.repeat(points[None],2,axis=0),20,{},rest_offsets=offsets)
        context=dict(skeleton=skeleton,clip=clip,priormdm_rig_debug=guide)
        renderer=MGLRendererMixin()
        renderer._mgl_scene_skeleton_decode_joint_owner=lambda owner:('Rig',owner)
        renderer._mgl_scene_owner_fbx_rig_context=lambda owner:context
        renderer._mgl_scene_skeleton_rotation_context=lambda owner:dict(current_local=Rotation.identity().as_quat())
        renderer._mgl_scene_skeleton_invalidate_owner=Mock()
        view=SimpleNamespace(_mgl_renderer=renderer,_timeline_current_frame=lambda:0,_timeline_fps=20,
            _timeline_entry_axis_mask=lambda entry:GraphGLView._timeline_entry_axis_mask(None,entry),
            _refresh_scene_skeleton_joint_gizmo_after_rotation=Mock(),_rot_shared_owner_global_q=lambda owner:None)
        for name in ('left_shoulder','right_shoulder','left_shoulder'):
            view._timeline_keys={0:dict(xyz=[0,0,0],rxyz=[0,0,0],axis_mask=[True]*6)}
            GraphGLView._set_owner_rot_deg(view,name,(0,35,-45),False)
            self.assertIn(name,context.get('_scene_skeleton_joint_overrides',{}))
            pose=evaluate_rig_at_time(skeleton,context['clip'],0)
            joint=JOINT_NAMES.index(name)
            np.testing.assert_allclose(pose.local_transforms[joint].translation,offsets[joint],atol=1e-8)
            self.assertFalse(any(context['_scene_skeleton_joint_overrides'][name][0]['axis_mask'][:3]))

        # Existing authored positions must survive rotation as well, including
        # grouped edits where the other selected owner's timeline is active.
        for name in ('left_shoulder','right_shoulder'):
            joint=JOINT_NAMES.index(name)
            position=offsets[joint]+[.025,.015,-.01]
            keys={0:dict(xyz=position.tolist(),axis_mask=[True]*3+[False]*3),
                  20:dict(rxyz=[0,10,0],axis_mask=[False]*3+[True]*3)}
            self.assertTrue(renderer._mgl_scene_skeleton_apply_timeline_keys(name,keys,20))
            view._xform_hist_start=dict(owner='left_shoulder',joint_keys={
                name:{0:dict(xyz=[0,0,0],rxyz=[0,0,0],axis_mask=[True]*6)}})
            GraphGLView._set_owner_rot_deg(view,name,(0,35,-45),False)
            pose=evaluate_rig_at_time(skeleton,context['clip'],0)
            np.testing.assert_allclose(pose.local_transforms[joint].translation,position,atol=1e-8)
            saved=context['_scene_skeleton_joint_overrides'][name]
            self.assertEqual(saved[0]['axis_mask'],[True]*6)
            np.testing.assert_allclose(saved[20]['rxyz'],keys[20]['rxyz'],atol=1e-8)
            self.assertEqual(view._xform_hist_start['joint_keys'][name][0]['xyz'],position.tolist())

    def test_authored_preview_keys_store_clamped_rotation(self):
        from echograph.ui.gl_mgl_renderer import MGLRendererMixin
        from echograph.motion.conversion import reconstruct_animation
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
        offsets=preview_offsets('standardman')
        points,guide=build_guide(offsets)
        skeleton,clip,_,_=reconstruct_animation(np.repeat(points[None],2,axis=0),20,{},rest_offsets=offsets)
        context=dict(skeleton=skeleton,clip=clip,priormdm_rig_debug=guide)
        renderer=MGLRendererMixin()
        renderer._mgl_scene_skeleton_decode_joint_owner=lambda owner:('Rig','left_knee')
        renderer._mgl_scene_owner_fbx_rig_context=lambda owner:context
        renderer._mgl_scene_skeleton_rotation_context=lambda owner:dict(current_local=Rotation.identity().as_quat())
        renderer._mgl_scene_skeleton_invalidate_owner=Mock()
        self.assertTrue(renderer._mgl_scene_skeleton_apply_timeline_keys('knee',{0:dict(rxyz=[175,0,0],axis_mask=[False]*3+[True]*3)},20))
        pose=evaluate_rig_at_time(skeleton,context['clip'],0)
        q=pose.local_transforms[4].rotation
        np.testing.assert_allclose(Rotation.from_quat(q).as_rotvec(),[np.deg2rad(165),0,0],atol=1e-6)

    def test_hinges_stop_at_both_ends_and_do_not_flip_past_180(self):
        for j in (4,5,18,19):
            axis=np.array(JOINT_LIMITS[j]['axis'])
            previous=Rotation.identity().as_quat()
            for degrees in (-20,0,80,150,170,190):
                desired=Rotation.from_rotvec(axis*np.deg2rad(degrees)).as_quat()
                actual=clamp_preview_rotation(j,desired,axis,previous)
                expected=np.clip(degrees,0,JOINT_LIMITS[j]['flex'])
                np.testing.assert_allclose(Rotation.from_quat(actual).as_rotvec(),axis*np.deg2rad(expected),atol=1e-8)
                previous=actual

    def test_cones_twist_and_unrestricted_root(self):
        for joint,axis in ((3,[0,1,0]),(9,[0,1,0]),(12,[0,1,0]),(7,[0,0,1]),(16,[1,0,0])):
            q=Rotation.from_euler('xyz',[80,100,120],degrees=True).as_quat()
            actual=clamp_preview_rotation(joint,q,axis)
            swing,twist=rotation_parts(actual,axis)
            limits=PREVIEW_JOINT_LIMITS[joint]
            self.assertLessEqual(np.degrees(swing.magnitude()),limits['swing']+1e-7)
            self.assertLessEqual(abs(np.degrees(twist)),limits['twist']+1e-7)
            np.testing.assert_array_equal(clamp_preview_rotation(0,q,[0,1,0]),q)

    def test_fast_drag_cannot_jump_to_the_opposite_bend_or_twist_limit(self):
        for joint in (1,2,3,6,9,7,8,12,13,14,16,17):
            child=next(i for i,p in enumerate(PARENTS) if p==joint)
            axis=REST_DIRECTIONS[child]
            bend_axis=np.cross(axis,np.eye(3)[np.argmin(abs(axis))])
            for component,drag_axis in (('swing',bend_axis),('twist',axis)):
                maximum=PREVIEW_JOINT_LIMITS[joint][component]
                for sign in (-1,1):
                    with self.subTest(joint=joint,component=component,sign=sign):
                        previous=Rotation.from_rotvec(drag_axis*np.deg2rad(sign*maximum))
                        desired=Rotation.from_rotvec(drag_axis*np.deg2rad(sign*157.5))*previous
                        actual=clamp_preview_rotation(joint,desired.as_quat(),axis,previous.as_quat())
                        np.testing.assert_allclose(Rotation.from_quat(actual).as_matrix(),previous.as_matrix(),atol=1e-8)
                        # Reverse immediately, without undoing rejected mouse travel.
                        desired=Rotation.from_rotvec(drag_axis*np.deg2rad(-sign*5))*previous
                        reversed_q=clamp_preview_rotation(joint,desired.as_quat(),axis,actual)
                        np.testing.assert_allclose(Rotation.from_quat(reversed_q).as_matrix(),desired.as_matrix(),atol=1e-8)

    def test_bend_cones_face_outward_and_the_pointer_stops_on_the_rim(self):
        offsets=preview_offsets('standardman')
        rest,_=build_guide(offsets)
        for joint in (1,2,3,6,9,7,8,12,13,14,16,17):
            with self.subTest(joint=JOINT_NAMES[joint]):
                child=next(i for i,p in enumerate(PARENTS) if p==joint)
                axis=offsets[child]/np.linalg.norm(offsets[child])
                rim_direction=np.cross(axis,np.eye(3)[np.argmin(abs(axis))])
                rim_direction/=np.linalg.norm(rim_direction)
                bend_axis=np.cross(axis,rim_direction)
                local=np.tile(np.eye(3),(22,1,1))
                local[0]=Rotation.from_euler('xyz',[20,30,40],degrees=True).as_matrix()
                maximum=PREVIEW_JOINT_LIMITS[joint]['swing']
                desired=Rotation.from_rotvec(bend_axis*np.deg2rad(maximum+10)).as_quat()
                local[joint]=Rotation.from_quat(clamp_preview_rotation(joint,desired,axis)).as_matrix()
                points,world=world_pose(local,offsets,rest[0])
                matrices=np.tile(np.eye(4),(22,1,1))
                matrices[:,:3,:3],matrices[:,:3,3]=world,points
                _,guide=build_guide(offsets,matrices)
                shapes=[s for s in guide['shapes'] if s['joint']==JOINT_NAMES[joint]]
                cone=next(s for s in shapes if s['kind']=='limit')
                rim=cone['wires'][0]
                self.assertTrue(np.all((rim-points[joint])@(world[PARENTS[joint]]@axis)>0))
                self.assertTrue(all(len(side)==2 for side in cone['wires'][1:]))
                pointer=next(s for s in shapes if s['kind']=='limit_hit' and not s.get('follow_swing'))
                np.testing.assert_allclose(pointer['wires'][0][-1],rim[0],atol=1e-8)

    def test_axis_drag_stays_at_limit_for_multiple_turns_and_reverses_immediately(self):
        from PySide6 import QtGui
        from echograph.gizmo.rotate_gizmo_shared import RotateGizmoShared
        from echograph.ui.gl_mgl_renderer import MGLRendererMixin
        from echograph.ui.gl_view import GraphGLView
        from echograph.ui.gl_view_mouse import _handle_mouse_move_moderngl_rot_shared_axis_apply_qapply
        from echograph.motion.conversion import reconstruct_animation
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
        offsets=preview_offsets('standardman')
        points,guide=build_guide(offsets)
        for joint,component in ((3,'swing'),(3,'twist'),(16,'swing'),(1,'swing')):
            with self.subTest(joint=joint,component=component):
                name=JOINT_NAMES[joint]
                child=next(i for i,p in enumerate(PARENTS) if p==joint)
                axis=REST_DIRECTIONS[child]
                drag_axis=axis if component=='twist' else np.cross(axis,np.eye(3)[np.argmin(abs(axis))])
                qt_axis=QtGui.QVector3D(*drag_axis)
                start_dir=QtGui.QVector3D(*np.cross(drag_axis,np.eye(3)[np.argmin(abs(drag_axis))]))
                skeleton,clip,_,_=reconstruct_animation(np.repeat(points[None],2,axis=0),20,{},rest_offsets=offsets)
                context=dict(skeleton=skeleton,clip=clip,priormdm_rig_debug=guide)
                renderer=MGLRendererMixin()
                renderer._mgl_scene_skeleton_decode_joint_owner=lambda owner:('Rig',owner)
                renderer._mgl_scene_owner_fbx_rig_context=lambda owner:context
                def current():
                    return evaluate_rig_at_time(skeleton,context['clip'],0).local_transforms[joint].rotation
                renderer._mgl_scene_skeleton_rotation_context=lambda owner:dict(current_local=current())
                renderer._mgl_scene_skeleton_invalidate_owner=Mock()
                shared=RotateGizmoShared()
                shared.begin_axis_drag('x',QtGui.QQuaternion(),qt_axis,start_dir,(0,0,0))
                view=SimpleNamespace(_mgl_renderer=renderer,_timeline_current_frame=lambda:0,_timeline_fps=20,
                    _timeline_entry_axis_mask=lambda entry:GraphGLView._timeline_entry_axis_mask(None,entry),
                    _refresh_scene_skeleton_joint_gizmo_after_rotation=Mock(),_rot_owner_quat={},
                    _rot_shared=shared,_rot_shared_owner=name,
                    _rot_shared_owner_is_scene_skeleton_joint=lambda owner:True,
                    _rot_shared_owner_global_q=lambda owner:GraphGLView._rot_shared_qt_from_xyzw(None,current()))
                for degrees in [*range(10,731,10),729]:
                    direction=QtGui.QQuaternion.fromAxisAndAngle(qt_axis,degrees).rotatedVector(start_dir)
                    shared.drag_axis.last_dir=direction
                    raw=shared.update_axis_drag(direction)
                    desired=_handle_mouse_move_moderngl_rot_shared_axis_apply_qapply(view,rot_shared=shared,qnew=raw)
                    desired=GraphGLView._rot_shared_adjust_scene_skeleton_drag_q(view,name,desired,shared.drag_axis.start_rot)
                    euler=renderer._mgl_scene_skeleton_quat_to_euler_deg(GraphGLView._rot_shared_qt_to_xyzw(None,desired))
                    GraphGLView._set_owner_rot_deg(view,name,euler,False)
                    maximum=PREVIEW_JOINT_LIMITS[joint][component]
                    expected=min(degrees*1.75,maximum) if degrees!=729 else maximum-1.75
                    actual=Rotation.from_quat(current()).as_rotvec()@drag_axis
                    # Qt's drag quaternions are float32.
                    self.assertAlmostEqual(np.degrees(actual),expected,delta=.002)

                # Arcball feedback must also use the accepted orientation.
                shared.end_axis_drag()
                view._rot_shared_arc_active=True
                view._rot_shared_arc_current_vec=QtGui.QVector3D(.2,.3,.4)
                GraphGLView._set_owner_rot_deg(view,name,(0,0,0),False)
                self.assertEqual(view._rot_shared_arc_start_vec,view._rot_shared_arc_current_vec)
                np.testing.assert_allclose(GraphGLView._rot_shared_qt_to_xyzw(None,view._rot_shared_arc_start_q),current(),atol=1e-6)

    def test_stored_keys_are_not_swept_from_the_current_pose(self):
        from echograph.ui.gl_mgl_renderer import MGLRendererMixin
        renderer=MGLRendererMixin()
        renderer._mgl_scene_skeleton_decode_joint_owner=lambda owner:('Rig','left_shoulder')
        renderer._mgl_scene_owner_fbx_rig_context=lambda owner:dict(priormdm_rig_debug={'profile':'model'})
        current=Rotation.from_euler('x',100,degrees=True).as_quat()
        renderer._mgl_scene_skeleton_rotation_context=lambda owner:dict(current_local=current)
        stored=renderer._mgl_scene_skeleton_constrain_preview_rotation('shoulder',(-95,0,0))
        np.testing.assert_allclose(renderer._mgl_scene_skeleton_quat_from_euler_deg(stored),
                                   Rotation.from_euler('x',-95,degrees=True).as_quat(),atol=1e-8)

    def test_dial_pointer_reaches_fixed_parent_relative_boundary(self):
        offsets=preview_offsets('standardman')
        rest,base=build_guide(offsets)
        local=np.tile(np.eye(3),(22,1,1))
        local[0]=Rotation.from_euler('y',50,degrees=True).as_matrix()
        local[4]=Rotation.from_euler('x',165,degrees=True).as_matrix()
        points,world=world_pose(local,offsets,rest[0])
        matrices=np.tile(np.eye(4),(22,1,1))
        matrices[:,:3,:3],matrices[:,:3,3]=world,points
        _,guide=build_guide(offsets,matrices)
        shapes=[s for s in guide['shapes'] if s['joint']=='left_knee']
        pointer=next(s for s in shapes if s['kind']=='limit_hit')['wires'][0]
        arc=next(s for s in shapes if s['kind']=='limit')['wires'][0]
        np.testing.assert_allclose(pointer[-1],arc[-1],atol=1e-8)
        self.assertIn('165.0° / 165°',guide['labels']['left_knee'])

    def test_picking_targets_visible_dots_at_each_dpi(self):
        data=dict(points=[dict(x=100,y=200,z=0,name='left_knee')])
        handles=[dict(name='left_knee',role='scene_skeleton',owner='Rig')]
        for dpr in (1,1.5,2):
            self.assertEqual(pick_guide_joint(data,handles,105*dpr,200*dpr,dpr),handles[0])
            self.assertIsNone(pick_guide_joint(data,handles,111*dpr,200*dpr,dpr))

    def test_preview_body_hit_deselects_instead_of_selecting_root(self):
        from echograph.ui.gl_view_mouse import _handle_mouse_release_moderngl_click_pick_apply
        view=SimpleNamespace(_handle_mouse_release_moderngl_pick_empty=Mock(),_handle_mouse_release_moderngl_pick_owner=Mock())
        renderer=SimpleNamespace(_mgl_scene_owner_fbx_rig_context=lambda owner:dict(priormdm_rig_debug={'profile':'model'}))
        _handle_mouse_release_moderngl_click_pick_apply(view,owner='Rig',renderer=renderer)
        view._handle_mouse_release_moderngl_pick_empty.assert_called_once()
        view._handle_mouse_release_moderngl_pick_owner.assert_not_called()

    def test_renderer_enforces_only_preview_rigs(self):
        from echograph.ui.gl_mgl_renderer import MGLRendererMixin
        renderer=MGLRendererMixin()
        context=dict(priormdm_rig_debug={'profile':'model'})
        renderer._mgl_scene_skeleton_decode_joint_owner=lambda owner:('Rig','left_knee')
        renderer._mgl_scene_owner_fbx_rig_context=lambda owner:context
        renderer._mgl_scene_skeleton_rotation_context=lambda owner:dict(current_local=Rotation.from_euler('x',150,degrees=True).as_quat())
        actual=renderer._mgl_scene_skeleton_constrain_preview_rotation('knee',(175,0,0))
        q=renderer._mgl_scene_skeleton_quat_from_euler_deg(actual)
        np.testing.assert_allclose(Rotation.from_quat(q).as_rotvec(),[np.deg2rad(165),0,0],atol=1e-6)
        context.clear()
        self.assertEqual(renderer._mgl_scene_skeleton_constrain_preview_rotation('knee',(175,0,0)),(175,0,0))
