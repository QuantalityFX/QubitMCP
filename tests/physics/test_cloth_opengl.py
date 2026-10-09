"""Exercise the actual Windows mesh loader and dynamic OpenGL buffers."""
import sys
import unittest

import numpy as np
from PySide6 import QtCore, QtWidgets

from echograph.model import GraphNode
from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.physics.cloth import ClothSettings, build_cloth_asset
from echograph.ui.gl_view import GraphGLView


@unittest.skipUnless(sys.platform == "win32", "Requires Windows desktop OpenGL")
class ClothOpenGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_authored_particles_survive_import_and_deform_real_buffers(self):
        self.exercise_buffers("cpu")

    def test_cuda_worker_deforms_real_opengl_buffers(self):
        try:
            import warp as wp
        except ImportError:
            self.skipTest("Warp runtime unavailable")
        wp.init()
        if not wp.is_cuda_available():
            self.skipTest("Warp CUDA unavailable")
        self.exercise_buffers("cuda")

    def exercise_buffers(self, device):
        graph = QtWidgets.QGraphicsScene()
        view = GraphGLView(graph)
        self.addCleanup(view.close)
        view.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        view.resize(512, 512)
        view.show()
        self.app.processEvents()
        if not view.isValid() or view._mgl_ctx is None:
            self.skipTest("OpenGL context unavailable")
        # Intentionally coincident panels: an importer must not merge their particles.
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Front", 100, 100), rectangle("Back", 100, 100)]), 25)
        model = GraphNode("Cloth", kind="cloth_solver")
        config = {"asset": asset, "settings": ClothSettings(device=device, self_collision=False).to_dict(),
                  "pins": [], "collider_owner": "", "colliders": [], "cache_path": "", "node_model": model}
        view._mgl_load_scene_assets([{"node": "Cloth", "kind": "mesh", "path": str(asset.rest_obj()),
                                     "ext": ".obj", "cloth": config}], frame=True)
        view.repaint()
        self.app.processEvents()
        self.assertIn("Cloth", view._cloth_states, getattr(view, "_mgl_error", ""))
        self.assertNotIn("error", getattr(model, "_cloth_status", "").lower())
        item = next(view._mgl_scene.iter_by_tag("scene-model"))
        entry = item.payload["mesh_entry"]
        self.assertEqual(len(entry["points"]), len(asset.positions))
        np.testing.assert_array_equal(entry["vertex_ids"], np.arange(len(asset.positions)))
        before = entry["points"].copy()
        model._cloth_commands = [{"action": "step"}]
        view.repaint()
        self.app.processEvents()
        pending = view._cloth_states["Cloth"].pending
        if pending is not None:
            pending.result(timeout=10)
            view.repaint()
            self.app.processEvents()
        view.makeCurrent()
        try:
            gpu = np.frombuffer(entry["vbo"].read(), dtype=np.float32).reshape(-1, 3)
            normals = np.frombuffer(entry["nbo"].read(), dtype=np.float32).reshape(-1, 3)
        finally:
            view.doneCurrent()
        self.assertLess(gpu[:, 1].mean(), before[:, 1].mean())
        if device == "cuda":
            solver = view._cloth_states["Cloth"].solver
            self.assertTrue(solver.backend.startswith("Warp CUDA"), solver.backend_detail)
            self.assertGreater(solver.last_gpu_ms, 0)
        np.testing.assert_allclose(gpu, entry["points"])
        self.assertTrue(np.isfinite(normals).all())
        image = view.grabFramebuffer()
        self.assertFalse(image.isNull())


if __name__ == "__main__":
    unittest.main()
