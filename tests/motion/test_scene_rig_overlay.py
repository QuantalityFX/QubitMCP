import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from echograph.motion.conversion import JOINT_NAMES, PARENTS
from echograph.motion.rig_preview import preview_offsets
from echograph.ui.scene_rig_overlay import collider_guide, draw_scene_colliders
from echograph.rigging.fbx_canonical import (Joint, JointTransform, SkeletonAsset,
                                            AnimationClip, JointAnimationTrack, Vec3Keyframe)
from echograph.motion.rig_preview_mapping import preview_joint_indices

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class SceneRigOverlayTests(unittest.TestCase):
    def context(self):
        offsets = preview_offsets('standardman')
        skeleton = SkeletonAsset(name='Character', joints=[
            Joint(name=name, parent_index=PARENTS[i],
                  local_bind=JointTransform(translation=tuple(offsets[i])))
            for i, name in enumerate(JOINT_NAMES)])
        return dict(skeleton=skeleton)

    def test_preview_is_read_only_and_preserves_existing_settings(self):
        context = self.context()
        original = collider_guide(context)
        original.update(collisions=False, limits=True, read_only=False)
        context['priormdm_rig_debug'] = original
        guide = collider_guide(context)
        self.assertTrue(guide['collisions'])
        self.assertFalse(guide['limits'])
        self.assertTrue(guide['read_only'])
        self.assertFalse(original['collisions'])
        self.assertIs(guide['volumes'], original['volumes'])

    def test_missing_body_names_report_clear_error(self):
        context = self.context()
        context['skeleton'].joints[0].name = 'DifferentRoot'
        with self.assertRaisesRegex(ValueError, 'could not map these body roles: pelvis'):
            collider_guide(context)

    def renamed_context(self, names):
        context = self.context()
        for joint, name in zip(context['skeleton'].joints, names):
            joint.name = name
        return context

    def test_unreal_names_map_to_same_capsules_without_renaming(self):
        names = ('pelvis', 'thigh_l', 'thigh_r', 'spine_01', 'calf_l', 'calf_r',
                 'spine_02', 'foot_l', 'foot_r', 'spine_03', 'ball_l', 'ball_r',
                 'neck_01', 'clavicle_l', 'clavicle_r', 'head', 'upperarm_l',
                 'upperarm_r', 'lowerarm_l', 'lowerarm_r', 'hand_l', 'hand_r')
        context = self.renamed_context(names)
        guide = collider_guide(context)
        self.assertEqual(guide['joint_indices'], list(range(22)))
        self.assertEqual(tuple(j.name for j in context['skeleton'].joints), names)
        canonical = collider_guide(self.context())
        for a, b in zip(guide['shapes'], canonical['shapes']):
            # Initial guide placement differs by the canonical ground offset;
            # capsule dimensions must match exactly.
            np.testing.assert_allclose(np.ptp(np.concatenate(a['faces']), axis=0),
                                       np.ptp(np.concatenate(b['faces']), axis=0), atol=1e-8)

    def test_mixamo_namespace_and_semantic_name_conflicts(self):
        names = ('Hips', 'LeftUpLeg', 'RightUpLeg', 'Spine', 'LeftLeg', 'RightLeg',
                 'Spine1', 'LeftFoot', 'RightFoot', 'Spine2', 'LeftToeBase', 'RightToeBase',
                 'Neck', 'LeftShoulder', 'RightShoulder', 'Head', 'LeftArm', 'RightArm',
                 'LeftForeArm', 'RightForeArm', 'LeftHand', 'RightHand')
        context = self.renamed_context(['mixamorig:' + name for name in names])
        guide = collider_guide(context)
        self.assertEqual(guide['joint_indices'], list(range(22)))
        self.assertEqual(guide['joint_names'], JOINT_NAMES)

    def test_helper_root_and_twist_joint_do_not_change_mapping_or_bone_lengths(self):
        context = self.context()
        joints = context['skeleton'].joints
        joints[1].name, joints[2].name = 'thigh_l', 'thigh_r'
        # Other canonical names are kept for this mixed naming scheme.
        joints.insert(0, Joint('root', -1))
        for joint in joints[1:]:
            joint.parent_index += 1
        joints.insert(5, Joint('thigh_twist_l', 2, JointTransform(translation=(0., -.1, 0.))))
        for joint in joints[6:]:
            if joint.parent_index >= 5:
                joint.parent_index += 1
        knee = next(j for j in joints if j.name == 'left_knee')
        knee.parent_index = 5
        knee.local_bind.translation = tuple(np.asarray(knee.local_bind.translation) + [0., .1, 0.])
        guide = collider_guide(context)
        np.testing.assert_allclose(guide['offsets'][4], preview_offsets('standardman')[4])
        self.assertNotIn(0, guide['joint_indices'])
        self.assertNotIn(5, guide['joint_indices'])

    def test_ambiguous_names_are_reported_instead_of_selecting_arbitrary_joint(self):
        context = self.context()
        context['skeleton'].joints.append(Joint('other:pelvis', -1))
        with self.assertRaisesRegex(ValueError, 'cannot uniquely map pelvis'):
            preview_joint_indices(context['skeleton'])

    def test_mapped_preview_follows_original_named_animation(self):
        from echograph.ui.priormdm_rig_overlay import draw_rig_guide
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
        context = self.context()
        for joint in context['skeleton'].joints:
            joint.name = 'Character:' + joint.name
        context['clip'] = AnimationClip(name='Move', start_time=0., end_time=1., sample_rate_hz=30., tracks=[
            JointAnimationTrack(joint_name='Character:pelvis', translation_keys=[
                Vec3Keyframe(time=0., value=(0., 0., 0.)),
                Vec3Keyframe(time=1., value=(.5, 0., 0.))])])
        guide = collider_guide(context)
        renderer = SimpleNamespace(
            _mgl_fbx_context_timeline_sample_seconds=lambda *args: 1.,
            _mgl_scene_skeleton_display_positions=lambda owner, rows: (rows, {}))
        image = QtGui.QImage(100, 100, QtGui.QImage.Format_RGB32)
        painter = QtGui.QPainter(image)
        try:
            draw_rig_guide(renderer, painter, dict(owner='Character', rig_context=context,
                rig_guide=guide, rig_projection=np.eye(4), rig_viewport=(0, 0, 100, 100)))
        finally:
            painter.end()
        matrices = np.asarray(evaluate_rig_at_time(context['skeleton'], context['clip'], 1.).global_matrices).reshape(-1, 4, 4)
        from echograph.motion.rig_preview import build_guide
        _, expected = build_guide(guide['offsets'], matrices[guide['joint_indices']],
                                  joint_limits={}, volumes=guide['volumes'],
                                  names=guide['joint_names'], parents=guide['parents'])
        np.testing.assert_allclose(guide['_posed_shapes'][1]['shapes'][0]['faces'],
                                   expected['shapes'][0]['faces'])

    def test_overlay_stays_visible_when_geometry_is_hidden(self):
        context = self.context()
        guide = collider_guide(context)
        projection = np.eye(4)
        projection[0, 0] = projection[1, 1] = .85
        projection[1, 3] = -.9
        renderer = SimpleNamespace(
            _scene_rig_collider_overlays={'Character': (context['skeleton'], guide)},
            _mgl_pick_proj=projection, _mgl_pick_view=np.eye(4), _mgl_pick_model=np.eye(4),
            width=lambda: 700, height=lambda: 800,
            _mgl_render_size=lambda: (1400, 1600),
            _mgl_active_viewport_rect=(0, 0, 1400, 1600),
            _mgl_scene_owner_visible=Mock(return_value=True),
            _mgl_scene_owner_fbx_rig_context=lambda owner: context,
            _mgl_fbx_context_timeline_sample_seconds=lambda *args: .5,
            _mgl_scene_skeleton_display_positions=Mock(side_effect=lambda owner, rows: (rows, {})))
        positions = preview_offsets('standardman').copy()
        for i in range(1, len(positions)):
            positions[i] += positions[PARENTS[i]]
        matrices = np.tile(np.eye(4), (len(positions), 1, 1))
        matrices[:, :3, 3] = positions
        evaluation = SimpleNamespace(global_matrices=matrices)
        images = []
        with patch('echograph.rigging.fbx_stage5_evaluator.evaluate_rig_at_time', return_value=evaluation) as evaluate:
            for visible in (True, False):
                renderer._mgl_scene_owner_visible.return_value = visible
                image = QtGui.QImage(700, 800, QtGui.QImage.Format_RGB32)
                image.fill(QtGui.QColor('#20252c'))
                painter = QtGui.QPainter(image)
                try:
                    draw_scene_colliders(renderer, painter)
                finally:
                    painter.end()
                images.append(image)
            self.assertEqual(evaluate.call_args.args[2], .5)
            self.assertEqual(evaluate.call_count,2)
        self.assertEqual(images[0], images[1])
        renderer._scene_rig_collider_overlays.clear()
        image = QtGui.QImage(700,800,QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor('#20252c'))
        blank = image.copy()
        painter = QtGui.QPainter(image)
        try:
            draw_scene_colliders(renderer,painter)
        finally:
            painter.end()
        self.assertEqual(image,blank)
        self.assertNotEqual(images[0],blank)
        renderer._mgl_scene_skeleton_display_positions.assert_called()

    def test_xray_default_and_toggle_preserve_original_alphas(self):
        from echograph.ui.priormdm_rig_overlay import draw_rig_guide
        renderer = SimpleNamespace(_mgl_scene_skeleton_display_positions=lambda owner,rows:(rows,{}))
        collider = np.array([[-.8,-.4,0],[-.2,-.4,0],[-.2,.4,0],[-.8,.4,0]])
        limit = collider+np.array([1.,0,0])
        guide = dict(shapes=[dict(kind='collision',joint='hand',faces=[collider],wires=[]),
                             dict(kind='limit',joint='hand',faces=[limit],wires=[])],
                     labels={},collisions=True,limits=True,read_only=True)
        data = dict(owner='Character',rig_guide=guide,rig_projection=np.eye(4),rig_viewport=(0,0,100,100))
        images = []
        for xray in (None,True,False,True):
            if xray is not None:
                renderer._rig_colliders_xray = xray
            image = QtGui.QImage(100,100,QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(image)
            try:
                draw_rig_guide(renderer,painter,data)
            finally:
                painter.end()
            self.assertEqual(image.pixelColor(75,50).alpha(),32)
            self.assertTrue(guide['_pick_volumes'])
            images.append(image)
        self.assertEqual(images[0],images[1])
        self.assertEqual(images[0],images[3])
        self.assertEqual(images[0].pixelColor(25,50).alpha(),20)
        # Solid colliders belong to the lit GL pass, below every skeleton guide.
        self.assertEqual(images[2].pixelColor(25,50).alpha(),0)
