"""GPU regression for Qt painting over ModernGL's persistent gizmo VAOs."""
from __future__ import annotations

import sys
import unittest

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from echograph.ui.gl_view import GraphGLView


class _GizmoViewport(GraphGLView):
    def paintGL(self):
        if self._mgl_ctx is None:
            return
        self._mgl_bind_default_fbo()
        self._mgl_ctx.viewport = (0, 0, self.width(), self.height())
        self._mgl_ctx.scissor = None
        self._gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        self._gl.glClear(0x4000 | 0x0100)
        self.draw_succeeded = self._mgl_draw_gizmo_tips_test_overlay(
            self.test_mvp, mode=self.test_mode,
        )


@unittest.skipUnless(sys.platform == "win32", "Requires the Windows desktop OpenGL backend")
class GizmoQtOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.scene = QtWidgets.QGraphicsScene()
        self.view = _GizmoViewport(self.scene)
        self.addCleanup(self.view.close)
        self.view.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        self.view.resize(512, 512)
        projection = QtGui.QMatrix4x4()
        projection.ortho(-1.3, 1.3, -1.3, 1.3, 0.1, 10.0)
        camera = QtGui.QMatrix4x4()
        camera.lookAt(QtGui.QVector3D(3, 2, 4), QtGui.QVector3D(), QtGui.QVector3D(0, 1, 0))
        self.normal_mvp = np.array((projection * camera).data(), dtype="f4").reshape(4, 4).T
        self.view.test_mvp = self.normal_mvp
        self.view.test_mode = "translate"
        self.view._gizmo_rot3 = np.eye(3)
        self.view.show()
        self.app.processEvents()
        if not self.view.isValid() or self.view._mgl_ctx is None:
            self.skipTest("OpenGL context unavailable")

    def pixels(self):
        image = self.view.grabFramebuffer().convertToFormat(QtGui.QImage.Format_RGBA8888)
        self.assertTrue(self.view.draw_succeeded)
        return np.frombuffer(image.bits(), dtype=np.uint8).copy()

    def check_tips_after_qt_paint(self, mode):
        self.view.test_mode = mode
        # Begin with fresh buffers even if Qt painted during widget creation.
        self.view._mgl_gizmo_tips_test_version = 0
        reference = self.pixels()
        self.assertGreater(np.count_nonzero(reference.reshape(-1, 4)[:, :3]), 100)

        close_mvp = self.normal_mvp.copy()
        close_mvp[:2, :3] *= 20.0  # All endpoints move outside the viewport.
        for matrix in (close_mvp, self.normal_mvp, close_mvp, self.normal_mvp):
            self.view.test_mvp = matrix
            # Exercise paintEvent as well as paintGL: Qt paints its own overlays
            # after the gizmo and used to overwrite its vertex attribute arrays.
            self.view.repaint()
            self.app.processEvents()
        self.assertTrue(
            np.array_equal(reference, self.pixels()),
            f"{mode} tips changed permanently after zooming and Qt overlay painting",
        )

    def test_translation_cones_survive_zoom_and_qt_overlays(self):
        self.check_tips_after_qt_paint("translate")

    def test_scale_cubes_survive_zoom_and_qt_overlays(self):
        self.check_tips_after_qt_paint("scale")


if __name__ == "__main__":
    unittest.main()
