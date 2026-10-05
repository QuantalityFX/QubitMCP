import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from scipy.spatial.transform import Rotation
from PySide6 import QtCore, QtGui, QtWidgets, QtTest

from echograph.motion.conversion import JOINT_NAMES, reconstruct_animation
from echograph.motion.rig_limits import (JOINT_LIMITS, PREVIEW_JOINT_LIMITS, clamp_preview_rotation,
    rotation_parts, preview_limit_fields, preview_limit_from_fields)
from echograph.motion.rig_preview import preview_offsets, build_guide, create_rig_asset
from echograph.motion.rig_preview_settings import default_preview_limits, load_preview_limits, save_preview_limits
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
from echograph.ui.gl_mgl_renderer import MGLRendererMixin
from echograph.ui.gl_view import GraphGLView
from echograph.ui.priormdm_rig_panel import RigPreviewPanel, show_rig_panel

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class PanelViewport(QtWidgets.QWidget, MGLRendererMixin):
    _set_owner_rot_deg = GraphGLView._set_owner_rot_deg
    _timeline_entry_axis_mask = GraphGLView._timeline_entry_axis_mask

    def __init__(self):
        super().__init__()
        self.resize(800,700)
        self.setAttribute(QtCore.Qt.WA_DontShowOnScreen,True)
        offsets = preview_offsets('standardman')
        points,guide = build_guide(offsets,joint_limits=default_preview_limits())
        guide.update(profile='standardman',collisions=True,limits=True)
        skeleton,clip,_,_ = reconstruct_animation(np.repeat(points[None],2,axis=0),20,{},rest_offsets=offsets)
        self.context = dict(skeleton=skeleton,clip=clip,priormdm_rig_debug=guide)
        self._timeline_fps = 20
        self._mgl_scene_skeleton_invalidate_owner = Mock()
        self._refresh_scene_skeleton_joint_gizmo_after_rotation = Mock()
        self._rot_shared_owner_global_q = lambda owner:None
        self._bottom_overlay_height = lambda:100
        self.clicks = 0

    def _mgl_scene_owner_fbx_rig_context(self, owner): return self.context
    def _mgl_fbx_context_timeline_sample_seconds(self, context, owner=None): return 0.
    def _timeline_current_frame(self): return 0
    def _mgl_timeline_fps_value(self): return 20.
    def _mgl_scene_skeleton_asset_rotation(self, owner): return (0.,0.,0.,1.)
    def mousePressEvent(self, event): self.clicks += 1

    def data(self, selected='left_ankle'):
        return dict(owner='Rig',selected=selected,rig_context=self.context,
                    rig_guide=self.context['priormdm_rig_debug'],rig_viewport=(0,0,800,700))


class PreviewSettingsTests(unittest.TestCase):
    def test_ankle_defaults_are_narrower_without_changing_generation(self):
        for joint in (7,8):
            self.assertEqual(preview_limit_fields(joint)['bend_max'],25.)
            self.assertEqual(JOINT_LIMITS[joint]['swing'],85.)
            for axis in ([1,0,0],[0,1,0]):
                q=clamp_preview_rotation(joint,Rotation.from_rotvec(np.array(axis)*np.deg2rad(70)).as_quat(),[0,0,1])
                self.assertAlmostEqual(np.degrees(rotation_parts(q,[0,0,1])[0].magnitude()),25.)

    def test_custom_minima_and_asymmetric_twist_are_enforced_and_drawn(self):
        limits=default_preview_limits()
        limits[7]=preview_limit_from_fields(7,dict(bend_min=10,bend_max=20,twist_min=-12,twist_max=27))
        axis=np.array([0.,0.,1.])
        for bend in (0,15,70):
            for twist in (-60,0,60):
                desired=(Rotation.from_euler('x',bend,degrees=True)*Rotation.from_euler('z',twist,degrees=True)).as_quat()
                actual=clamp_preview_rotation(7,desired,axis,limits=limits)
                swing,angle=rotation_parts(actual,axis)
                self.assertAlmostEqual(np.degrees(swing.magnitude()),np.clip(bend,10,20))
                self.assertAlmostEqual(np.degrees(angle),np.clip(twist,-12,27))
        points,guide=build_guide(preview_offsets('standardman'),joint_limits=limits)
        shapes=[s for s in guide['shapes'] if s['joint']=='left_ankle']
        dial=next(s for s in shapes if s['kind']=='twist')['wires'][0]-points[7]
        # _basis for +Z uses +Y as zero and -X as its positive dial direction.
        angles=np.rad2deg(np.arctan2(-dial[:,0],dial[:,1]))
        np.testing.assert_allclose(angles[[0,-1]],[-12,27],atol=1e-8)
        self.assertIn('10–20',guide['labels']['left_ankle'])
        self.assertEqual(PREVIEW_JOINT_LIMITS[7]['swing'],25.)

    def test_hinge_minimum_and_invalid_saved_values(self):
        limits=default_preview_limits()
        limits[4]=preview_limit_from_fields(4,dict(bend_min=15,bend_max=100))
        q=clamp_preview_rotation(4,[0,0,0,1],[1,0,0],limits=limits)
        self.assertAlmostEqual(np.degrees(Rotation.from_quat(q).magnitude()),15.)
        for fields in (dict(bend_min=30,bend_max=10),dict(bend_max=float('nan')),dict(bend_min=None)):
            self.assertEqual(preview_limit_from_fields(7,fields)['swing'],25.)

    def test_save_reload_and_topbar_save_preserve_separate_category(self):
        # The shelf launches a window at import time. Compile only its actual
        # settings functions/constants to exercise the writer without launching.
        path=Path(__file__).resolve().parents[2]/'echograph_shelf.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        names={'_load_app_settings','_save_app_settings','_coerce_bool',
               '_normalize_panel_layout_preset','_normalize_view_mode_preset','_normalize_voice_audio_mode',
               '_normalize_voice_mic_device_index','_normalize_shadow_quality',
               '_normalize_ambient_light_strength','_normalize_create_node_dialog_size'}
        namespace=dict(json=json,Dict=dict,Any=object,List=list)
        for node in tree.body:
            if isinstance(node,ast.Assign) and node.lineno < 100:
                try:
                    value=namespace[node.value.id] if isinstance(node.value,ast.Name) else ast.literal_eval(node.value)
                    for target in node.targets:
                        if isinstance(target,ast.Name): namespace[target.id]=value
                except (ValueError,TypeError,KeyError): pass
        module=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[])
        exec(compile(module,str(path),'exec'),namespace)
        with tempfile.TemporaryDirectory() as folder:
            settings=Path(folder)/'app_settings.json'
            settings.write_text(json.dumps(dict(shadow_quality='ultra',save_layout=False,create_node_dialog_size=dict(width=800,height=600))))
            limits=default_preview_limits()
            limits[7]=preview_limit_from_fields(7,dict(bend_max=18,twist_min=-10,twist_max=23))
            save_preview_limits(limits,settings)
            self.assertEqual(json.loads(settings.read_text())['shadow_quality'],'ultra')
            self.assertEqual(load_preview_limits(settings),limits)
            namespace['_APP_SETTINGS_PATH']=settings
            stale=namespace['_load_app_settings']()
            limits[7]=preview_limit_from_fields(7,dict(bend_max=16))
            save_preview_limits(limits,settings)
            namespace['_save_app_settings'](stale)
            self.assertEqual(load_preview_limits(settings),limits)
            self.assertEqual(json.loads(settings.read_text())['create_node_dialog_size'],dict(width=800,height=600))

    def test_malformed_settings_are_not_overwritten_and_initial_pose_uses_saved_minima(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'app_settings.json'
            path.write_text('{broken')
            self.assertEqual(load_preview_limits(path),default_preview_limits())
            with self.assertRaises(ValueError): save_preview_limits(default_preview_limits(),path)
            self.assertEqual(path.read_text(),'{broken')
            path.write_text('{}')
            limits=default_preview_limits()
            limits[7]=preview_limit_from_fields(7,dict(bend_min=12,bend_max=20,twist_min=5,twist_max=15))
            save_preview_limits(limits,path)
            with patch('echograph.motion.rig_preview_settings.APP_SETTINGS_PATH',path):
                asset=create_rig_asset('standardman',None,Path(folder)/'rig','Rig')
            context=asset['fbx_rig_context']
            pose=evaluate_rig_at_time(context['skeleton'],context['clip'],0)
            swing,twist=rotation_parts(pose.local_transforms[7].rotation,[0,0,1])
            self.assertAlmostEqual(np.degrees(swing.magnitude()),12.)
            self.assertAlmostEqual(np.degrees(twist),5.)
            self.assertEqual(context['priormdm_rig_debug']['joint_limits'],limits)

    def test_panel_edit_reclamps_pose_reset_save_and_selection(self):
        view=PanelViewport()
        self.addCleanup(view.deleteLater)
        data=view.data()
        panel=RigPreviewPanel(view)
        panel.sync(data,'Ankle')
        token=view._mgl_scene_skeleton_joint_owner_token('Rig','left_ankle')
        view._set_owner_rot_deg(token,(24,0,0),False)
        panel.fields['bend_max'].setValue(15)
        pose=evaluate_rig_at_time(view.context['skeleton'],view.context['clip'],0)
        self.assertAlmostEqual(np.degrees(rotation_parts(pose.local_transforms[7].rotation,[0,0,1])[0].magnitude()),15.,places=4)
        self.assertEqual(data['rig_guide']['joint_limits'][8]['swing'],25.)
        self.assertIn('Unsaved',panel.status.text())
        self.assertFalse(panel.reset_button.icon().isNull())
        self.assertFalse(panel.save_button.icon().isNull())
        panel.fields['twist_min'].setValue(-8)
        panel.fields['twist_max'].setValue(19)
        with tempfile.TemporaryDirectory() as folder:
            settings=Path(folder)/'app_settings.json'
            with patch('echograph.motion.rig_preview_settings.APP_SETTINGS_PATH',settings):
                panel.save_button.click()
            self.assertEqual(preview_limit_fields(7,load_preview_limits(settings)[7]),dict(bend_min=0,bend_max=15,twist_min=-8,twist_max=19))
        self.assertIn('Saved',panel.status.text())
        panel.sync(view.data('right_ankle'),'Right ankle')
        self.assertEqual(panel.fields['bend_max'].value(),25.)
        panel.sync(data,'Left ankle')
        panel.reset_button.click()
        self.assertEqual(panel.fields['bend_max'].value(),25.)
        self.assertIn('Unsaved',panel.status.text())
        panel.sync(view.data('pelvis'),'Root is unrestricted')
        self.assertFalse(panel.fields['bend_max'].isEnabled())
        panel.sync(view.data('left_knee'),'Hinge')
        self.assertTrue(panel.fields['bend_max'].isEnabled())
        self.assertFalse(panel.fields['twist_max'].isEnabled())

    def test_panel_stays_above_timeline_and_intercepts_background_clicks(self):
        view=PanelViewport()
        self.addCleanup(view.deleteLater)
        view.show()
        self.assertTrue(show_rig_panel(view,view.data(),'Current bend 0°'))
        APP.processEvents()
        panel=view._priormdm_rig_panel
        self.assertLessEqual(panel.geometry().bottom(),view.height()-100)
        self.assertTrue(panel.rect().contains(panel.save_button.mapTo(panel,QtCore.QPoint(0,0))))
        QtTest.QTest.mouseClick(panel,QtCore.Qt.LeftButton,pos=QtCore.QPoint(2,2))
        self.assertEqual(view.clicks,0)
        view._mgl_scene_skeleton_qt_overlay_data=lambda:None
        image=QtGui.QImage(800,700,QtGui.QImage.Format_RGB32)
        painter=QtGui.QPainter(image)
        try: view._draw_scene_skeleton_qt_overlay(painter)
        finally: painter.end()
        self.assertTrue(panel.isHidden())


if __name__ == '__main__':
    unittest.main()
