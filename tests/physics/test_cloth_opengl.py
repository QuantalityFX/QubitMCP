"""Exercise the actual Windows mesh loader and dynamic OpenGL buffers."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from echograph.model import GraphNode
from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.physics.cloth import ClothSettings, build_cloth_asset
from echograph.physics.cloth.placement import piece_owner
from echograph.ui.gl_view import GraphGLView
from nodes.scene import spec as scene_spec


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

    def test_panel_placement_updates_real_buffers_selection_and_gizmo(self):
        view = GraphGLView(QtWidgets.QGraphicsScene())
        self.addCleanup(view.close)
        view.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        view.resize(512, 512)
        view.show()
        self.app.processEvents()
        if not view.isValid() or view._mgl_ctx is None:
            self.skipTest("OpenGL context unavailable")
        front, back = rectangle("Front", 100, 100), rectangle("Back", 100, 100)
        back.layout[0] = 200
        asset = build_cloth_asset(PatternDocument(pieces=[front, back]), 25)
        model = GraphNode("Cloth", kind="cloth_solver")
        config = {"asset": asset, "settings": ClothSettings(device="cpu", self_collision=False).to_dict(),
                  "pins": [], "colliders": [], "cache_path": "", "node_model": model, "piece_transforms": {}}
        view._mgl_load_scene_assets([{"node": "Cloth", "kind": "mesh", "path": str(asset.rest_obj()),
                                     "ext": ".obj", "cloth": config}], frame=True)
        view.repaint()
        self.app.processEvents()
        token = piece_owner("Cloth", front.id)
        view._mgl_set_scene_asset_xform(token, pos=[.05, .05, .2], rot=[0, 180, 0])
        view.repaint()
        self.app.processEvents()
        item = next(view._mgl_scene.iter_by_tag("scene-model"))
        entry = item.payload["mesh_entry"]
        selected = np.asarray(asset.vertex_pieces) == front.id
        view.makeCurrent()
        try:
            gpu = np.frombuffer(entry["vbo"].read(), dtype=np.float32).reshape(-1, 3)
            faces = view._mgl_build_cached_mesh_selection_object_faces(token)
            self.assertIsNotNone(faces, getattr(view, "_mgl_error", ""))
            self.assertEqual(faces["count"], len(asset.pieces()[0]["faces"]) * 3)
            for resource in faces["resources"]:
                resource.release()
        finally:
            view.doneCurrent()
        np.testing.assert_allclose(gpu[selected, 2], .2, atol=1e-7)
        np.testing.assert_allclose(gpu[~selected], asset.positions[~selected], atol=1e-7)
        view._on_mesh_selection_mode_clicked("object")
        view._set_mesh_element_selection({"mode": "object", "owner": token})
        np.testing.assert_allclose(view._xform_gizmo_pos, [.05, .05, .2], atol=1e-7)
        lower, upper = view.get_scene_owner_selection_bounds(token)
        np.testing.assert_allclose((lower + upper) * .5, [.05, .05, .2], atol=1e-7)
        # Object mode routes a real triangle hit to the piece instead of the garment.
        with patch.object(view, "_mgl_pick_ray", return_value=(np.array([.04, .04, 1]), np.array([0, 0, -1]))):
            picked = view.pick_mesh_element_at(1, 1, 512, 512, mode="object")
            self.assertEqual(picked["owner"], token)

    def test_cloth_panel_mouse_gizmos_transform_only_the_selected_panel(self):
        self.exercise_panel_mouse_gizmos(placement_only=False)

    def test_pattern_panel_mouse_gizmos_transform_only_the_selected_panel(self):
        self.exercise_panel_mouse_gizmos(placement_only=True)

    def exercise_panel_mouse_gizmos(self, placement_only):
        graph = QtWidgets.QGraphicsScene()
        view = GraphGLView(graph)
        self.addCleanup(view.close)
        view.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        view.resize(512, 512)
        view.show()
        self.app.processEvents()
        if not view.isValid() or view._mgl_ctx is None:
            self.skipTest("OpenGL context unavailable")
        front, back = rectangle("Front", 100, 100), rectangle("Back", 100, 100)
        back.layout[0] = 200
        asset = build_cloth_asset(PatternDocument(pieces=[front, back]), 25)
        parent = "Pattern" if placement_only else "Cloth"
        model = GraphNode(parent, kind="pattern_design" if placement_only else "cloth_solver")
        settings = ClothSettings(device="cpu", self_collision=False, interactive=True, enabled=not placement_only)
        config = {"asset": asset, "settings": settings.to_dict(), "placement_only": placement_only,
                  "pins": [], "colliders": [], "cache_path": "", "node_model": model, "piece_transforms": {},
                  "solver_node": parent}
        assets = [{"node": parent, "kind": "mesh", "path": str(asset.rest_obj()), "ext": ".obj", "cloth": config,
                   "pattern_pieces": [{"id": p["id"], "name": p["name"]} for p in asset.pieces()]}]
        view.load_scene_assets(assets, frame=True)
        view.repaint()
        self.app.processEvents()
        token = piece_owner(parent, front.id)
        # Select the actual outliner row, which must attach the panel's gizmo
        # and enter Object mode before the real QWidget receives mouse events.
        scene_model = GraphNode("Scene", kind="scene")
        graph._node_items = {"Scene": SimpleNamespace(model=scene_model)}
        view.gl_view, view._active_scene_node = view, scene_model
        card = QtWidgets.QWidget(view)
        card._node_ref, card._graph_scene = scene_model, graph
        with patch.object(scene_spec, "_collect_assets", return_value=assets):
            scene_spec.augment_infocard_footer(card, QtWidgets.QVBoxLayout(card))
            outliner = card._scene_outliner_widget
            child = next(outliner.item(i) for i in range(outliner.count())
                         if outliner.item(i).data(QtCore.Qt.UserRole) == token)
            outliner.setCurrentItem(child)
        view.update_scene_asset_xform = card._scene_xform_refresh
        self.assertEqual(view._mesh_select_mode, "object")
        self.assertEqual(view._xform_gizmo_owner, token)
        selected = np.asarray(asset.vertex_pieces) == front.id
        root_xform = view._mgl_get_scene_asset_xform(parent)

        def mouse(event_type, position, modifiers=QtCore.Qt.NoModifier):
            button = QtCore.Qt.NoButton if event_type == QtCore.QEvent.MouseMove else QtCore.Qt.LeftButton
            buttons = QtCore.Qt.NoButton if event_type == QtCore.QEvent.MouseButtonRelease else QtCore.Qt.LeftButton
            event = QtGui.QMouseEvent(event_type, position, position, button, buttons, modifiers)
            self.app.sendEvent(view, event)

        for mode, field in (("translate", "pos"), ("rotate", "rot"), ("scale", "scl")):
            with self.subTest(mode=mode):
                view._set_xform_gizmo_mode(mode)
                view.repaint()
                self.app.processEvents()
                before = view._mgl_get_scene_asset_xform(token)
                points = next(view._mgl_scene.iter_by_tag("scene-model")).payload["mesh_entry"]["points"].copy()
                probe = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(), QtCore.QPointF(),
                                         QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                ctx = view._handle_mouse_press_moderngl_left_gizmo_build_context(probe)
                self.assertIsNotNone(ctx)
                rect = view._mgl_active_viewport_qrectf()
                center = QtCore.QPointF(ctx["p0"][0] / ctx["dpr"] + rect.left(),
                                       ctx["p0"][1] / ctx["dpr"] + rect.top())
                end = center + QtCore.QPointF(35, 20)
                mouse(QtCore.QEvent.MouseButtonPress, center)
                try:
                    self.assertIsNone(getattr(view, "_cloth_drag", None), "A gizmo click must not grab the cloth")
                    active = (getattr(view, "_xform_dragging", False) or getattr(view, "_rot_shared_arc_active", False)
                              or view._rot_shared.drag_view or view._rot_shared.drag_axis.active)
                    self.assertTrue(active, f"{mode} mouse press did not start a gizmo drag in Object mode")
                    mouse(QtCore.QEvent.MouseMove, end)
                finally:
                    mouse(QtCore.QEvent.MouseButtonRelease, end)
                view.repaint()
                self.app.processEvents()
                after = view._mgl_get_scene_asset_xform(token)
                self.assertGreater(np.linalg.norm(np.asarray(after[field]) - before[field]), 1e-5)
                self.assertEqual(view._xform_gizmo_owner, token)
                self.assertEqual(view._mgl_get_scene_asset_xform(parent), root_xform)
                entry = next(view._mgl_scene.iter_by_tag("scene-model")).payload["mesh_entry"]
                np.testing.assert_allclose(entry["points"][~selected], points[~selected], atol=1e-7)
                self.assertGreater(np.linalg.norm(entry["points"][selected] - points[selected]), 1e-5)
                saved = json.loads(next(p["value"] for p in model.params if p["name"] == "piece_transforms"))
                self.assertEqual(set(saved), {front.id})
        # Alt over an active handle still navigates without changing placement.
        before = view._mgl_get_scene_asset_xform(token)
        mouse(QtCore.QEvent.MouseButtonPress, center, QtCore.Qt.AltModifier)
        try:
            self.assertFalse(view._xform_dragging)
            self.assertIsNone(getattr(view, "_cloth_drag", None))
            self.assertTrue(view._mgl_orbit_dragging)
            mouse(QtCore.QEvent.MouseMove, end, QtCore.Qt.AltModifier)
        finally:
            mouse(QtCore.QEvent.MouseButtonRelease, end, QtCore.Qt.AltModifier)
        self.assertEqual(view._mgl_get_scene_asset_xform(token), before)

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
        # Both backends must resume a disk pose, including its velocity/time,
        # rather than restarting from the original flat panels.
        original = view._cloth_states["Cloth"]
        positions, velocity, elapsed = original.solver.x.copy(), original.solver.velocity.copy(), original.solver.time
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "resume.npz")

            def command(action):
                model._cloth_commands = [{"action": action, "path": path}]
                for _ in range(3):
                    view.repaint()
                    self.app.processEvents()
                    pending = view._cloth_states["Cloth"].pending
                    if pending is not None:
                        pending.result(timeout=10)

            command("save")
            self.assertTrue(Path(path).is_file())
            command("reset")
            command("load_cache")
            restored = view._cloth_states["Cloth"]
            np.testing.assert_allclose(restored.solver.x, positions, atol=1e-7)
            np.testing.assert_allclose(restored.solver.velocity, velocity, atol=1e-7)
            self.assertAlmostEqual(restored.solver.time, elapsed)
            command("step")
            self.assertLess(restored.solver.x[:, 1].mean(), positions[:, 1].mean())
            if device == "cuda":
                self.assertTrue(restored.solver.backend.startswith("Warp CUDA"), restored.solver.backend_detail)


if __name__ == "__main__":
    unittest.main()
