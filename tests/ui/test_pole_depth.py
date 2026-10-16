"""Framebuffer regressions for IK poles behind geometry and rig bones."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import moderngl
import numpy as np
from pyrr import Matrix44
from PySide6 import QtCore, QtGui, QtWidgets

from echograph.ui.gl_view import GraphGLView


class _PoleViewport(GraphGLView):
    def paintGL(self):
        if self._mgl_ctx is None:
            return
        self._mgl_bind_default_fbo()
        self._mgl_ctx.viewport = (0, 0, self.width(), self.height())
        self._mgl_ctx.scissor = None
        self._mgl_mesh_overlay_depth_mask(True)
        self._gl.glClearColor(0, 0, 0, 1)
        self._gl.glClear(0x4000 | 0x0100)
        if not hasattr(self, 'test_items'):
            return
        self._mgl_ctx.enable(moderngl.DEPTH_TEST)
        self._mgl_ctx.disable(moderngl.CULL_FACE)
        self._mgl_ctx.depth_func = '<='
        if self.show_occluder:
            self.occluder.render(moderngl.TRIANGLES)
        if self.bone is not None:
            self.bone.draw(self, Matrix44.identity(dtype='f4'))
        for item in self.test_items:
            item.draw(self, Matrix44.identity(dtype='f4'))


@unittest.skipUnless(sys.platform == 'win32', 'Requires desktop OpenGL')
class PoleDepthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.scene = QtWidgets.QGraphicsScene()
        self.view = _PoleViewport(self.scene)
        self.addCleanup(self.view.close)
        self.view.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        self.view.resize(512, 512)
        self.view.show()
        self.app.processEvents()
        if not self.view.isValid() or self.view._mgl_ctx is None:
            self.skipTest('OpenGL context unavailable')
        view = self.view
        view.makeCurrent()
        program = view._mgl_ctx.program(
            vertex_shader='#version 330\nin vec3 position; void main() { gl_Position=vec4(position,1); }',
            fragment_shader='#version 330\nout vec4 color; void main() { color=vec4(1,0,0,1); }')
        vertices = np.array([[-.5,-.5,0],[.5,-.5,0],[.5,.5,0],
                             [-.5,-.5,0],[.5,.5,0],[-.5,.5,0]], dtype='f4')
        buffer = view._mgl_ctx.buffer(vertices.tobytes())
        view.occluder = view._mgl_ctx.vertex_array(program, [(buffer,'3f','position')])
        view.show_occluder = True
        view.bone = None
        view.doneCurrent()

    def poles(self, depth, selected=False):
        view = self.view
        handle = dict(name='wrist', ik_pole=True, radius=.01,
                      position=(0,.25,depth), bend_position=(-.8,.25,depth))
        session = SimpleNamespace(ik_enabled=True, endpoints={'wrist'},
                                  pole_handles=[handle], selected_pole='wrist' if selected else None)
        view._mgl_scene_owner_fbx_rig_context = Mock(return_value={'_animation_correction_session':session})
        view.makeCurrent()
        view._mgl_scene_skeleton_rebuild_handle_mesh_for_owner('rig', [])
        view.test_items = sorted(view._mgl_scene.iter_by_tag('scene-skeleton-handles'), key=lambda item:item.order)
        view.doneCurrent()
        self.assertEqual(len(view.test_items), 3 if selected else 2)

    def pixel(self, x, y):
        image = self.view.grabFramebuffer().convertToFormat(QtGui.QImage.Format_RGBA8888)
        return image.pixelColor(round((x*.5+.5)*image.width()), round((.5-y*.5)*image.height()))

    def test_pole_line_and_selected_circle_are_occluded_by_foreground_mesh(self):
        self.poles(.4, selected=True)
        for x in (0, .04, -.25):
            color = self.pixel(x, .25)
            self.assertGreater(color.red(), 240)
            self.assertLess(color.blue(), 10)
            self.assertLess(color.green(), 10)
        self.assertGreater(self.pixel(-.7,.25).blue(), 50)  # Uncovered line remains visible.

    def test_poles_in_front_remain_visible(self):
        self.poles(-.4)
        self.assertGreater(self.pixel(0,.25).blue(), 240)
        self.assertGreater(self.pixel(-.25,.25).blue(), 50)
        self.assertLess(self.pixel(-.25,.25).red(), 100)

    def test_rig_bones_occlude_poles_without_painting_over_nearer_poles(self):
        view = self.view
        self.poles(.4)
        view.show_occluder = False
        view.makeCurrent()
        view.bone = view._mgl_add_wire_item_from_points(
            name='bone', line_points=np.array([[0,-.8,0],[0,.8,0]],dtype='f4'),
            visible=True, tag='scene-rig-joints', owner='rig')
        view.bone.payload.update(scene_skeleton_overlay=True, color=(0,1,0,1), line_width=8)
        view.doneCurrent()
        self.assertGreater(self.pixel(0,.25).green(), 240)
        self.assertLess(self.pixel(0,.25).blue(), 10)
        self.poles(-.4)
        self.assertGreater(self.pixel(0,.25).blue(), 240)


if __name__ == '__main__':
    unittest.main()
