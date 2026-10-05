"""Exercise selection through the Windows Qt viewport, including Qt overlays."""
import sys
import unittest
from types import SimpleNamespace

import moderngl
import numpy as np
from pyrr import Matrix44
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.ui.gl_view import GraphGLView
from echograph.ui.gl_scene import MGLSceneItem
from echograph.ui.gl_mgl_renderer import MGLRendererMixin


@unittest.skipUnless(sys.platform == 'win32', 'Requires desktop OpenGL')
class MeshViewportOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.scene = QtWidgets.QGraphicsScene()
        self.view = v = GraphGLView(self.scene)
        self.addCleanup(v.close)
        v.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        v.resize(900, 760)
        v.show()
        self.app.processEvents()
        self.assertTrue(v.isValid())
        v.makeCurrent()
        points = np.array([(x / 2.5 - 1, y / 2.5 - 1, z)
                           for z in (0, -.5) for y in range(6) for x in range(6)], dtype='f4')
        faces = [[base + y * 6 + x, base + y * 6 + x + 1,
                  base + (y + 1) * 6 + x + 1, base + (y + 1) * 6 + x]
                 for base in (0, 36) for y in range(5) for x in range(5)]
        triangles = np.array([t for a, b, c, d in faces for t in ((a, b, c), (a, c, d))])
        mesh = SimpleNamespace(update_normals=lambda: None, points=lambda: points,
                               vertex_normals=lambda: np.tile([0, 0, 1], (len(points), 1)),
                               face_vertex_indices=lambda: triangles)
        v._mgl_set_mesh(mesh)
        self.item = next(v._mgl_scene.iter_by_tag('model'))
        self.entry = self.item.payload['mesh_entry']
        self.entry.update(vertex_ids=np.arange(len(points)), topology_mesh='mesh',
                          triangle_polygons=np.repeat(np.arange(len(faces)), 2))
        model = Matrix44.from_translation([.06, .03, 0], dtype='f4')
        self.item.payload['model'] = model
        v._mgl_store_mesh_topology_for_owner('mesh', self.item.payload)
        topo = v._mgl_mesh_topology_for_owner('mesh')
        vertices = v._mgl_wide_line_vertices(topo['wire_points'])
        self.wire_buffer = v._mgl_ctx.buffer(vertices.tobytes())
        vao = v._mgl_ctx.vertex_array(v._mgl_wire_prog, [
            (self.wire_buffer, '3f 3f 3f 1f', 'in_pos', 'in_start', 'in_end', 'in_side')])
        v._mgl_scene.add(MGLSceneItem('mesh', MGLRendererMixin._mgl_draw_scene_wire,
            payload=dict(vao=vao, mode=moderngl.TRIANGLES, model=model, owner='mesh'),
            resources=[vao, self.wire_buffer], tag='model-wire', order=20))
        v._mgl_wireframe = True
        v._mgl_wire_color = (.6, .6, .6, 1)
        v._mgl_wire_edge_width = 2
        v._mgl_grid_visible = False
        v.set_gizmo_visible(False)  # Keep transform handles away from sampled components.
        self.transform = v._mgl_arcball.Transform.copy()
        self.zoom = v._mgl_camera_zoom

    def pixels(self):
        v = self.view
        v.repaint()
        self.app.processEvents()
        v.makeCurrent()
        # grabFramebuffer() redraws paintGL without the Qt navigation overlay.
        # Resolve the *already painted* multisample widget framebuffer instead.
        source = v._mgl_ctx.detect_framebuffer(v.defaultFramebufferObject())
        resolved = v._mgl_ctx.simple_framebuffer((v.width(), v.height()), components=4)
        try:
            v._mgl_ctx.copy_framebuffer(resolved, source)
            pixels = np.frombuffer(resolved.read(components=4), dtype=np.uint8)
            return pixels.reshape(v.height(), v.width(), 4)[::-1].copy()
        finally:
            resolved.release()
            v._mgl_bind_default_fbo()

    def location(self, index):
        v = self.view
        projected = v._mgl_project_owner_points_device(
            'mesh', v._mgl_mesh_topology_for_owner('mesh'), v.width(), v.height())
        return QtCore.QPoint(round(projected['x'][index]), round(projected['y'][index]))

    @staticmethod
    def orange(pixels):
        return (pixels[:, :, 0] > 180) & (pixels[:, :, 1] < 160) & (pixels[:, :, 2] < 80)

    @staticmethod
    def blue(pixels):
        rgb = pixels[:, :, :3].astype(int)
        return (rgb[:, :, 2] > rgb[:, :, 0] + 45) & (rgb[:, :, 1] > rgb[:, :, 0] + 20)

    def test_shift_a_previews_keep_selection_and_navigation_for_all_modes(self):
        v = self.view
        for mode, field, start, end in (
            ('face', 'face_index', 22, 32),
            ('point', 'vertex_index', 14, 20),
            ('edge', 'edge', (8, 14), (14, 20)),
        ):
            for button, kind in ((QtCore.Qt.LeftButton, 'path'), (QtCore.Qt.MiddleButton, 'loop')):
                with self.subTest(mode=mode, kind=kind):
                    v._mesh_select_mode = mode
                    v._set_mesh_element_selection(dict(mode=mode, owner='mesh', **{field: start}))
                    target = dict(mode=mode, owner='mesh', **{field: end})
                    v._set_mesh_element_hover(target)
                    before = self.pixels()
                    selected = v._mesh_selected_elements()
                    QtTest.QTest.keyPress(v, QtCore.Qt.Key_A, QtCore.Qt.ShiftModifier)
                    during = self.pixels()
                    self.assertEqual(v._mesh_selected_elements(), selected)
                    self.assertGreater(len(v._mesh_polygon_preview[kind]), len(selected))
                    # Bottom-left orientation indicator survives GL preview drawing.
                    np.testing.assert_array_equal(before[660:714, :65], during[660:714, :65])
                    self.assertGreater(np.count_nonzero(before[660:714, :65, 0] > 180), 5)
                    self.assertGreater(self.orange(before).sum(), 8)
                    self.assertGreaterEqual(self.orange(during).sum(), self.orange(before).sum() * .95)
                    self.assertGreater(self.blue(during).sum(), self.blue(before).sum() + 8)
                    expected = {v._mesh_element_key(e) for e in v._mesh_polygon_preview[kind]}
                    # Use the real picker and event route at the component center.
                    topo = v._mgl_mesh_topology_for_owner('mesh')
                    if mode == 'face':
                        indices = topo['triangles'][end]
                    elif mode == 'edge':
                        indices = end
                    else:
                        indices = [end]
                    locations = [self.location(i) for i in indices]
                    pos = QtCore.QPoint(round(sum(p.x() for p in locations) / len(locations)),
                                        round(sum(p.y() for p in locations) / len(locations)))
                    QtTest.QTest.mouseClick(v, button, QtCore.Qt.ShiftModifier, pos)
                    self.assertTrue(expected <= {v._mesh_element_key(e) for e in v._mesh_selected_elements()})
                    QtTest.QTest.keyRelease(v, QtCore.Qt.Key_A, QtCore.Qt.ShiftModifier)
                    self.pixels()
                    self.assertEqual(v._mesh_polygon_preview, {})
                    self.assertFalse(v._mgl_error, v._mgl_error)

    def test_point_hover_and_selection_render_over_wire_at_angles_and_close_views(self):
        v = self.view
        v._mesh_select_mode = 'point'
        for angle, zoom in ((0, 1), (.7, 1), (1.2, 1), (.7, .35), (1.2, .35)):
            with self.subTest(angle=angle, zoom=zoom):
                v._mgl_arcball.Transform[:3, :3] = Matrix44.from_y_rotation(angle)[:3, :3] @ self.transform[:3, :3]
                v._mgl_camera_zoom = self.zoom * zoom
                v._set_mesh_element_selection(dict(mode='point', owner='mesh', vertex_index=14))
                v._set_mesh_element_hover(dict(mode='point', owner='mesh', vertex_index=20))
                pixels = self.pixels()
                for index, mask in ((14, self.orange), (20, self.blue)):
                    p = self.location(index)
                    self.assertTrue(4 < p.x() < v.width() - 4 and 4 < p.y() < v.height() - 4)
                    patch = pixels[p.y()-3:p.y()+4, p.x()-3:p.x()+4]
                    self.assertGreater(mask(patch).sum(), 5, f'Point {index} is obscured')
                hover_cache = v._mesh_component_overlay_caches['hover']
                self.assertEqual(hover_cache['selected_point_sets'], {'mesh': {20}})
                self.assertFalse(v._mgl_error, v._mgl_error)

    def test_selected_point_turns_blue_on_hover_and_returns_to_orange(self):
        v = self.view
        v._mesh_select_mode = 'point'
        point = dict(mode='point', owner='mesh', vertex_index=14)
        v._set_mesh_element_selection(point)
        v._set_mesh_element_hover(None)
        before = self.pixels()
        pos = self.location(14)
        region = np.s_[pos.y()-2:pos.y()+3, pos.x()-2:pos.x()+3]
        self.assertGreater(self.orange(before[region]).sum(), 5)
        v._set_mesh_element_hover(point)
        during = self.pixels()
        self.assertGreater(self.blue(during[region]).sum(), 5)
        self.assertEqual(v._mesh_selected_elements(), [point])
        v._set_mesh_element_hover(None)
        after = self.pixels()
        np.testing.assert_array_equal(before[region], after[region])

    def test_selected_polygon_hover_is_light_blue_and_selection_survives(self):
        v = self.view
        v._mesh_select_mode = 'face'
        target = dict(mode='face', owner='mesh', face_index=22)
        v._set_mesh_element_selection(target)
        v._set_mesh_element_hover(None)
        before = self.pixels()
        selected = v._mesh_selected_elements()
        locations = [self.location(i) for i in v._mgl_mesh_topology_for_owner('mesh')['triangles'][22]]
        x = round(sum(p.x() for p in locations) / 3)
        y = round(sum(p.y() for p in locations) / 3)
        region = np.s_[y-10:y+11, x-10:x+11]
        v._set_mesh_element_hover(target)
        during = self.pixels()
        self.assertGreater(int(before[y, x, 0]), int(before[y, x, 2]) + 40)
        self.assertGreater(self.blue(during[region]).mean(), .95)
        self.assertEqual(v._mesh_selected_elements(), selected)
        v._set_mesh_element_hover(None)
        after = self.pixels()
        np.testing.assert_array_equal(before[region], after[region])

    def test_control_alone_turns_each_selected_component_red_without_selecting_a_path(self):
        v = self.view
        for mode, field, value, indices in (('point', 'vertex_index', 14, [14]),
                ('edge', 'edge', (14, 20), [14, 20]), ('face', 'face_index', 22, [13, 14, 20])):
            with self.subTest(mode=mode):
                v._mesh_select_mode = mode
                target = dict(mode=mode, owner='mesh', **{field: value})
                v._set_mesh_element_selection(target)
                v._set_mesh_element_hover(target)
                before = self.pixels()
                selected = v._mesh_selected_elements()
                locations = [self.location(i) for i in indices]
                x = round(sum(p.x() for p in locations)/len(locations))
                y = round(sum(p.y() for p in locations)/len(locations))
                QtTest.QTest.keyPress(v, QtCore.Qt.Key_Control)
                during = self.pixels()
                patch = during[y-2:y+3, x-2:x+3, :3].astype(int)
                self.assertGreater(((patch[:, :, 0] > patch[:, :, 1]+70) & (patch[:, :, 1] < 110)).sum(), 5)
                self.assertEqual(v._mesh_polygon_preview, {})
                self.assertEqual(v._mesh_selected_elements(), selected)
                QtTest.QTest.keyRelease(v, QtCore.Qt.Key_Control)
                after = self.pixels()
                np.testing.assert_array_equal(before[y-2:y+3,x-2:x+3], after[y-2:y+3,x-2:x+3])

    def test_shift_a_point_path_is_larger_and_lighter_than_regular_points(self):
        v = self.view
        v._mesh_select_mode = 'point'
        v._set_mesh_element_selection(dict(mode='point', owner='mesh', vertex_index=8))
        v._set_mesh_element_hover(dict(mode='point', owner='mesh', vertex_index=20))
        before = self.pixels()
        pos = self.location(14)
        region = np.s_[pos.y()-6:pos.y()+7,pos.x()-6:pos.x()+7]
        QtTest.QTest.keyPress(v, QtCore.Qt.Key_A, QtCore.Qt.ShiftModifier)
        during = self.pixels()
        cyan = lambda p: (p[:,:,2] > 180) & (p[:,:,1] > 140)
        self.assertGreater(cyan(during[region]).sum(), cyan(before[region]).sum()*1.7)
        self.assertGreater(int(during[pos.y(),pos.x(),0]), int(before[pos.y(),pos.x(),0])+80)
        QtTest.QTest.keyRelease(v, QtCore.Qt.Key_A, QtCore.Qt.ShiftModifier)

    def test_closed_mesh_surfaces_survive_disabled_depth_writes_from_previous_pass(self):
        v = self.view
        points = np.array([(-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1),
                           (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1)], dtype='f4')
        # Front triangles precede the back: without depth writes the inside wins.
        faces = np.array([(4,5,6), (4,6,7), (1,0,3), (1,3,2), (0,4,7), (0,7,3),
                          (5,1,2), (5,2,6), (3,7,6), (3,6,2), (0,1,5), (0,5,4)])
        v._mgl_set_mesh(SimpleNamespace(update_normals=lambda: None, points=lambda: points,
            vertex_normals=lambda: points / np.sqrt(3), face_vertex_indices=lambda: faces))
        v._mgl_scene.remove_by_tag('model-wire')
        v._mgl_wireframe = False
        v._mgl_grid_visible = True
        v._mgl_shadows_enabled = True
        v._mgl_camera_zoom *= 1.5
        transform = v._mgl_arcball.Transform.copy()
        prepare = v._paint_mgl_prepare_framebuffer

        def inherited_depth_state(**kwargs):
            v._gl.glDepthMask(False)
            return prepare(**kwargs)

        for angle in (.2, .7):
            v._mgl_arcball.Transform[:3,:3] = (Matrix44.from_x_rotation(.4)[:3,:3]
                @ Matrix44.from_y_rotation(angle)[:3,:3] @ transform[:3,:3])
            expected = self.pixels()
            self.assertTrue(v._mgl_shadow_valid)
            v._paint_mgl_prepare_framebuffer = inherited_depth_state
            try:
                for refresh_shadows in (False, True):
                    with self.subTest(angle=angle, refresh_shadows=refresh_shadows):
                        v._mgl_shadow_dirty = refresh_shadows
                        actual = self.pixels()
                        np.testing.assert_array_equal(actual[150:600,200:700], expected[150:600,200:700])
                        self.assertTrue(v._gl.glGetBooleanv(0x0B72))
            finally:
                v._paint_mgl_prepare_framebuffer = prepare

    def test_grid_restores_depth_state_before_opaque_meshes(self):
        v = self.view
        v._mgl_grid_visible = True
        for depth_test, depth_mask, depth_func in ((True, True, '<'), (False, False, '>')):
            with self.subTest(depth_test=depth_test, depth_mask=depth_mask):
                v.makeCurrent()
                v._mgl_ctx.depth_func = depth_func
                v._gl.glDepthMask(depth_mask)
                (v._mgl_ctx.enable if depth_test else v._mgl_ctx.disable)(moderngl.DEPTH_TEST)
                previous_func = v._gl.glGetIntegerv(0x0B74)
                v._paint_mgl_draw_grid_pass(mvp=Matrix44.identity(dtype='f4'))
                self.assertEqual(v._gl.glGetIntegerv(0x0B74), previous_func)
                self.assertEqual(bool(v._gl.glGetBooleanv(0x0B72)), depth_mask)
                self.assertEqual(bool(v._gl.glIsEnabled(0x0B71)), depth_test)

    def test_bvh_scene_preview_draws_bones_over_unshadowed_grid(self):
        import tempfile
        from pathlib import Path
        v = self.view
        with tempfile.TemporaryDirectory(dir='logs') as folder:
            path = Path(folder)/'grid_crossing.bvh'
            path.write_text('''HIERARCHY
ROOT Hips
{
 OFFSET 0 0 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT Knee
 {
  OFFSET 1 0 0
  CHANNELS 3 Zrotation Xrotation Yrotation
  End Site
  {
   OFFSET 1 0 0
  }
 }
}
MOTION
Frames: 2
Frame Time: 0.0333333
0 0 0 0 0 0 0 0 0
0 0 0 15 0 0 0 0 0
''')
            v.makeCurrent()
            v._mgl_scene.remove_by_tag('model')
            v._mgl_scene.remove_by_tag('model-wire')
            v._mgl_load_scene_assets([dict(node='motion', path=str(path), kind='mesh')], frame=True)
            bones = list(v._mgl_scene.iter_by_tag('scene-rig-joints'))
            self.assertTrue(bones, v._mgl_error)
            for bone in bones:
                bone.payload['color'] = (1, .1, 0, 1)
            v._mgl_arcball.Transform[:3,:3] = Matrix44.from_x_rotation(.6)[:3,:3] @ v._mgl_arcball.Transform[:3,:3]
            v._mgl_grid_visible = False
            before = self.pixels()
            bone_pixels = (before[:,:,0] > 230) & (before[:,:,1] < 40) & (before[:,:,2] < 20)
            self.assertGreater(bone_pixels.sum(), 50)
            v._mgl_grid_visible = True
            v._mgl_grid_alpha = 1.0
            v._mgl_grid_prog['UseShadows'].value = 1
            after = self.pixels()
            self.assertEqual(v._mgl_grid_prog['UseShadows'].value, 0)
            self.assertGreater(np.count_nonzero(before != after), 100)
            self.assertGreater(((after[:,:,0] > 230) & (after[:,:,1] < 60))[bone_pixels].mean(), .98)
            self.assertTrue(v._gl.glGetBooleanv(0x0B72))

    def test_final_mouse_position_is_processed_and_ctrl_a_hover_is_red(self):
        import time
        v = self.view
        for mode, field, value in (('point', 'vertex_index', 20), ('edge', 'edge', (14, 20))):
            with self.subTest(mode=mode):
                v._mesh_select_mode = mode
                target = dict(mode=mode, owner='mesh', **{field: value})
                v._set_mesh_element_selection(target)
                v._set_mesh_element_hover(None)
                self.pixels()
                pos = self.location(20)
                if mode == 'edge':
                    a = self.location(14)
                    pos = QtCore.QPoint((a.x()+pos.x())//2, (a.y()+pos.y())//2)
                # Enter the edge's new narrow margin, then stop moving.
                mouse_pos = pos + QtCore.QPoint(11, 0) if mode == 'edge' else pos
                event = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(mouse_pos),
                    QtCore.QPointF(v.mapToGlobal(mouse_pos)), QtCore.Qt.NoButton,
                    QtCore.Qt.NoButton, QtCore.Qt.ControlModifier)
                QtTest.QTest.keyPress(v, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
                v._mesh_hover_pick_last_t = time.perf_counter()
                v._update_mesh_element_hover_from_event(event)
                self.assertIsNotNone(v._mesh_hover_pending_event)
                QtTest.QTest.qWait(50)
                pixels = self.pixels()
                self.assertEqual(v._mesh_polygon_hover_target[field], value)
                self.assertEqual(v._mesh_polygon_preview_operation, 'subtract')
                patch = pixels[pos.y()-2:pos.y()+3, pos.x()-2:pos.x()+3, :3].astype(int)
                red = (patch[:, :, 0] > patch[:, :, 1] + 80) & (patch[:, :, 1] < 100)
                self.assertGreater(red.sum(), 5)
                if mode == 'edge':
                    # The closer rear edge must remain available and visibly red in X-ray,
                    # even though it belongs to a disconnected topology island.
                    v._mgl_wireframe_xray = True
                    v._mesh_hover_pick_last_t = 0
                    v._update_mesh_element_hover_from_event(event)
                    self.assertEqual(v._mesh_polygon_hover_target['edge'], (50, 56))
                    xray = self.pixels()
                    a, b = self.location(50), self.location(56)
                    x, y = (a.x()+b.x())//2, (a.y()+b.y())//2
                    patch = xray[y-2:y+3, x-2:x+3, :3].astype(int)
                    self.assertGreater(((patch[:, :, 0] > patch[:, :, 1]+80) & (patch[:, :, 1] < 100)).sum(), 5)
                    v._mgl_wireframe_xray = False
                QtTest.QTest.keyRelease(v, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)

    def test_ctrl_a_red_previews_and_deselect_path_or_loop_in_all_modes(self):
        from echograph.ui.mesh_polygons import component_selection
        v = self.view
        topo = v._mgl_mesh_topology_for_owner('mesh')
        for mode, field, start, end, unrelated in (
            ('face', 'face_index', 22, 32, 0),
            ('point', 'vertex_index', 14, 20, 0),
            ('edge', 'edge', (8, 14), (14, 20), (0, 1)),
        ):
            for button, kind in ((QtCore.Qt.LeftButton, 'path'), (QtCore.Qt.MiddleButton, 'loop')):
                with self.subTest(mode=mode, kind=kind):
                    v._mesh_select_mode = mode
                    anchor = dict(mode=mode, owner='mesh', **{field: start})
                    target = dict(mode=mode, owner='mesh', **{field: end})
                    extra = dict(mode=mode, owner='mesh', **{field: unrelated})
                    v._set_mesh_element_selection(anchor)
                    v._set_mesh_element_selection_many(
                        component_selection(topo, anchor, target, loop=True) + [extra], selection_op='add')
                    v._set_mesh_element_hover(target)
                    before = self.pixels()
                    selected = {v._mesh_element_key(e) for e in v._mesh_selected_elements()}
                    QtTest.QTest.keyPress(v, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
                    during = self.pixels()
                    self.assertEqual(v._mesh_polygon_preview_operation, 'subtract')
                    self.assertEqual(selected, {v._mesh_element_key(e) for e in v._mesh_selected_elements()})
                    red = lambda p: ((p[:, :, 0].astype(int) > p[:, :, 1].astype(int) + 60)
                                     & (p[:, :, 1].astype(int) < 100))
                    self.assertGreater(red(during).sum(), red(before).sum() + 8)
                    np.testing.assert_array_equal(before[660:714, :65], during[660:714, :65])
                    removed = {v._mesh_element_key(e) for e in v._mesh_polygon_preview[kind]}
                    indices = topo['triangles'][end] if mode == 'face' else end if mode == 'edge' else [end]
                    locations = [self.location(i) for i in indices]
                    pos = QtCore.QPoint(round(sum(p.x() for p in locations) / len(locations)),
                                        round(sum(p.y() for p in locations) / len(locations)))
                    QtTest.QTest.mouseClick(v, button, QtCore.Qt.ControlModifier, pos)
                    self.assertEqual({v._mesh_element_key(e) for e in v._mesh_selected_elements()}, selected - removed)
                    QtTest.QTest.keyRelease(v, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
                    self.pixels()
                    self.assertEqual(v._mesh_polygon_preview, {})
                    self.assertFalse(v._mgl_error, v._mgl_error)

    def test_edge_selection_follows_pose_and_covers_wire(self):
        v = self.view
        v._mesh_select_mode = 'edge'
        original = self.entry['points'].copy()
        for bend in (0, .45, -.3):
            with self.subTest(bend=bend):
                v.makeCurrent()
                points = original.copy()
                points[:, 0] += bend * (points[:, 1] + 1) ** 2
                points[:, 2] += bend * points[:, 1]
                self.entry['points'] = points
                self.entry['vbo'].write(points.tobytes())
                # Same topology/cache refresh as the FBX skin upload path.
                v._mgl_store_mesh_topology_for_owner('mesh', self.item.payload)
                v._mgl_clear_mesh_selection_overlay_cache()
                topo = v._mgl_mesh_topology_for_owner('mesh')
                self.wire_buffer.write(v._mgl_wide_line_vertices(topo['wire_points']).tobytes())
                v._clear_mesh_element_selection()
                v._set_mesh_element_hover(None)
                before = self.pixels()
                a, b = self.location(14), self.location(20)
                middle = QtCore.QPoint((a.x() + b.x()) // 2, (a.y() + b.y()) // 2)
                QtTest.QTest.mouseDClick(v, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, middle)
                after = self.pixels()
                self.assertEqual(len(v._mesh_selected_elements()), 5)
                x, y = middle.x(), middle.y()
                patch = before[y-8:y+9, x-5:x+6, :3].astype(int)
                gray = (patch.max(axis=2) - patch.min(axis=2) < 5) & (patch[:, :, 0] > 130)
                self.assertGreater(gray.sum(), 8)
                self.assertTrue(self.orange(after[y-8:y+9, x-5:x+6])[gray].all(),
                                'Gray wire splits the selected edge')


if __name__ == '__main__':
    unittest.main()
