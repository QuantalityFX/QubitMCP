import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from echograph.motion.rig_preview import preview_offsets, build_guide, create_rig_asset
from echograph.motion.conversion import PARENTS, REST_DIRECTIONS
from echograph.motion.self_collision import ARMS, body_capsules
from echograph.ui.priormdm_rig_overlay import draw_rig_guide

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class RigPreviewTests(unittest.TestCase):
    def test_guides_follow_joint_rotations_and_posed_collision_segments(self):
        from scipy.spatial.transform import Rotation
        from echograph.motion.self_collision import world_pose
        offsets = preview_offsets('standardman')
        rest, original = build_guide(offsets)
        local = np.tile(np.eye(3),(22,1,1))
        local[0] = Rotation.from_euler('y',35,degrees=True).as_matrix()
        local[16] = Rotation.from_euler('z',-60,degrees=True).as_matrix()
        local[18] = Rotation.from_euler('y',-90,degrees=True).as_matrix()
        points, rotations = world_pose(local,offsets,rest[0]+[.3,0,.2])
        matrices = np.tile(np.eye(4),(22,1,1))
        matrices[:,:3,:3], matrices[:,:3,3] = rotations,points
        _, posed = build_guide(offsets,matrices)
        from echograph.motion.conversion import JOINT_NAMES
        for before, after in zip(original['shapes'],posed['shapes']):
            if before['kind'] == 'limit':
                joint = JOINT_NAMES.index(before['joint'])
                for a,b in zip(before['wires'],after['wires']):
                    np.testing.assert_allclose(b,(a-rest[joint])@rotations[PARENTS[joint]].T+points[joint],atol=1e-8)
        starts,ends,radii = body_capsules(points,np.mean(np.linalg.norm(offsets[[4,5]],axis=1))/.44,ARMS[0])
        for a,b,r,shape in zip(starts,ends,radii,[s for s in posed['shapes'] if s['kind']=='collision']):
            vertices=np.concatenate(shape['faces'])
            direction=b-a
            t=np.clip((vertices-a)@direction/max(direction@direction,1e-12),0,1)
            np.testing.assert_allclose(np.linalg.norm(vertices-a-t[:,None]*direction,axis=1),r,atol=1e-8)

    def test_open_preview_loads_asset_and_requests_joint_inspection(self):
        from echograph.model import GraphNode
        from nodes.priormdm.rig_preview_ui import open_rig_preview
        window=QtWidgets.QMainWindow()
        self.addCleanup(window.deleteLater)
        scene=QtWidgets.QGraphicsScene()
        view=QtWidgets.QGraphicsView(scene)
        window.setCentralWidget(view)
        window.open_scene_assets=Mock(return_value=True)
        window.gl_view=SimpleNamespace(_mgl_scene_skeleton_request_active=Mock(),update=Mock())
        node=GraphNode(name='Prior',kind='priormdm',params=[
            {'name':'preview_profile','value':'standardman'},
            {'name':'rig_show_collisions','value':'false'}])
        asset={'node':'Prior_RigPreview','fbx_rig_context':{}}
        with patch('echograph.motion.rig_preview.create_rig_asset',return_value=asset) as create:
            self.assertIs(open_rig_preview(node,scene),asset)
        self.assertEqual(create.call_args.args[0],'standardman')
        self.assertFalse(create.call_args.kwargs['collisions'])
        window.open_scene_assets.assert_called_once_with([asset],frame=True)
        window.gl_view._mgl_scene_skeleton_request_active.assert_called_once_with('Prior_RigPreview')

    def test_native_offsets_use_raw_archive_not_standardman_export(self):
        with tempfile.TemporaryDirectory() as folder:
            offsets = REST_DIRECTIONS*np.linspace(.1,.3,22)[:,None]
            points = offsets.copy()
            for j in range(1,22): points[j] += points[PARENTS[j]]
            archive = Path(folder)/'motion.npz'
            np.savez(archive,positions=np.repeat(points[None],2,axis=0),fps=20,metadata=json.dumps({}))
            np.testing.assert_allclose(preview_offsets('model',archive),offsets)
        with self.assertRaisesRegex(ValueError,'Generate once'):
            preview_offsets('model')

    def test_guide_uses_solver_capsules_and_exported_rig_matches(self):
        from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
        offsets = preview_offsets('standardman')
        points, guide = build_guide(offsets)
        self.assertEqual(points[:,1].min(),0)
        starts,ends,radii = body_capsules(points,np.mean(np.linalg.norm(offsets[[4,5]],axis=1))/.44,ARMS[0])
        collision = [s for s in guide['shapes'] if s['kind']=='collision']
        for a,b,r,shape in zip(starts,ends,radii,collision):
            vertices = np.concatenate(shape['faces'])
            # All surface points lie exactly one radius from the center segment.
            direction = b-a
            t = np.clip((vertices-a)@direction/max(direction@direction,1e-12),0,1)
            np.testing.assert_allclose(np.linalg.norm(vertices-a-t[:,None]*direction,axis=1),r,atol=1e-8)
        with tempfile.TemporaryDirectory() as folder:
            asset = create_rig_asset('standardman',None,folder,'Guide')
            loaded = ingest_bvh_animation_data(Path(asset['path']))
            matrices = np.array(evaluate_rig_at_time(loaded.skeleton,loaded.clips[0],0).global_matrices).reshape(-1,4,4)
            lookup = {j.name:i for i,j in enumerate(loaded.skeleton.joints)}
            from echograph.motion.conversion import JOINT_NAMES
            np.testing.assert_allclose(matrices[[lookup[n] for n in JOINT_NAMES],:3,3],points,atol=1e-7)

    def test_projected_ghost_guides_render_and_can_be_hidden(self):
        points,guide = build_guide(preview_offsets('standardman'))
        guide.update(profile='standardman',collisions=True,limits=True)
        projection = np.eye(4)
        projection[0,0]=projection[1,1]=.85
        projection[1,3]=-.9
        data = dict(rig_guide=guide,rig_projection=projection,rig_viewport=(0,0,700,800),owner='Guide',selected='')
        renderer = SimpleNamespace(_mgl_scene_skeleton_display_positions=lambda owner,rows:(rows,{}))
        images=[]
        for enabled in (True,False):
            guide.update(collisions=enabled,limits=enabled)
            image=QtGui.QImage(700,800,QtGui.QImage.Format_RGB32)
            image.fill(QtGui.QColor('#20252c'))
            painter=QtGui.QPainter(image)
            draw_rig_guide(renderer,painter,data)
            painter.end()
            images.append(image.copy())
        self.assertNotEqual(images[0],images[1])
        self.assertEqual(images[1].pixelColor(350,400),QtGui.QColor('#20252c'))

    def test_node_border_contains_preview_button_and_popup_uses_same_profile(self):
        from echograph.model import GraphNode
        from nodes.priormdm.spec import render_node_body,MotionControls
        item=QtWidgets.QGraphicsWidget()
        item.model=GraphNode(name='Prior',kind='priormdm',params=[])
        item.width,item.height=240,70
        item._plugin_proxies=[]
        bottom=render_node_body(item,50)
        proxy=item._plugin_proxies[0]
        self.assertGreaterEqual(item.height,proxy.pos().y()+proxy.size().height()+10)
        self.assertGreaterEqual(bottom,proxy.pos().y()+proxy.size().height()+10)
        self.assertIn('Preview Rig',[b.text() for b in proxy.widget().findChildren(QtWidgets.QPushButton)])
        controls=MotionControls(item.model,None)
        self.addCleanup(controls.deleteLater)
        controls.proportions.setCurrentIndex(controls.proportions.findData('standardman'))
        self.assertIn({'name':'preview_profile','value':'standardman'},item.model.params)
        self.assertTrue(controls.rig_preview_button.isEnabled())
