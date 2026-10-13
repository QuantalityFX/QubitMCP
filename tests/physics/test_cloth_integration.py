import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
from concurrent.futures import Future
import unittest
from unittest.mock import patch

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from echograph.model import GraphNode
from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.patterns.elastic import set_elastic_edges
from echograph.patterns.serialization import save_document
from echograph.physics.cloth import ClothSettings, build_cloth_asset
from echograph.physics.cloth.cache import read_bake, write_bake
from echograph.physics.cloth.placement import piece_owner
from echograph.persistence import _node_to_dict
from echograph.rigging.fbx_canonical import AnimationClip, JointAnimationTrack, Vec3Keyframe
from echograph.ui.gl_mgl_renderer import MGLRendererMixin
from echograph.ui.gl_view_timeline_model import GraphGLTimelineModelMixin
from echograph.ui.node_item import NodeItem
from echograph.ui.scene_outliner import ROW_MIME
from nodes.cloth_solver.spec import build_cloth_scene_asset, ColliderOwnerEdit, ClothWidget
from nodes.scene import spec as scene_spec


class Graph(QtWidgets.QGraphicsScene):
    _scene_asset_revision = 0
    _edge_index_version = 0

    def __init__(self):
        super().__init__()
        self.edges = {}
        self.changes = []
        self._node_items = {}

    def _ordered_in_edges(self, item):
        return self.edges.get(item, [])

    def _in_edges(self, item):
        return self._ordered_in_edges(item)

    def set_node_params(self, name, parameters, **kwargs):
        self.changes.append(name)


class CacheFileDialog(QtWidgets.QFileDialog):
    """Exercise real dialogs without opening an interactive Windows file picker."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOption(QtWidgets.QFileDialog.DontUseNativeDialog)
        self.setAttribute(QtCore.Qt.WA_DontShowOnScreen)


class NodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_node_ports_and_border_survive_rebuild(self):
        graph = Graph()
        node = NodeItem(GraphNode("Cloth", kind="cloth_solver"))
        graph.addItem(node)
        width = node.width
        self.assertEqual(node.input_port_names(), ["pattern", "collider"])
        self.assertEqual(node.output_port_names(), ["cloth"])
        timeline = node._plugin_proxies[0].widget().findChild(QtWidgets.QCheckBox, "Cloth_follow_timeline")
        self.assertFalse(timeline.isChecked())
        timeline.setChecked(True)
        self.app.processEvents()
        gravity = node._plugin_proxies[0].widget().findChild(QtWidgets.QCheckBox, "Cloth_gravity_enabled")
        self.assertTrue(gravity.isChecked())
        gravity.setChecked(False)
        self.app.processEvents()
        saved = _node_to_dict(node.model)
        self.assertEqual(next(p["value"] for p in saved["params"] if p["name"] == "follow_timeline"), "1")
        self.assertEqual(next(p["value"] for p in saved["params"] if p["name"] == "gravity_enabled"), "0")
        self.assertEqual(float(next(p["value"] for p in saved["params"] if p["name"] == "gravity_y")), -9.81)
        for _ in range(2):
            node._recompute_height()
            node._build_widgets()
            self.app.processEvents()
            self.assertEqual(node.width, width)
            self.assertTrue(node._plugin_proxies[0].widget().findChild(QtWidgets.QCheckBox, "Cloth_follow_timeline").isChecked())
            self.assertFalse(node._plugin_proxies[0].widget().findChild(QtWidgets.QCheckBox, "Cloth_gravity_enabled").isChecked())
            for proxy in node._plugin_proxies:
                rect = proxy.mapRectToParent(proxy.boundingRect())
                self.assertLessEqual(rect.right(), node.width + .01)
                self.assertLessEqual(rect.bottom() + 9.99, node.height)
        graph.clear()

    def test_saved_pattern_to_scene_and_outliner_collider_drop(self):
        with tempfile.TemporaryDirectory() as temp:
            pattern = Path(temp) / "garment.pattern.json"
            front, back = rectangle("Front", 100, 100), rectangle("Back", 100, 100)
            back.layout[0] = 150
            save_document(pattern, PatternDocument(pieces=[front, back]))
            mesh = Path(temp) / "body.obj"
            mesh.write_text("v -1 0 -1\nv 1 0 -1\nv 0 0 1\nf 1 3 2\n")
            graph = Graph()
            source = NodeItem(GraphNode("Pattern", kind="pattern_design", params=[{"name": "pattern_path", "value": str(pattern)}]))
            cloth = NodeItem(GraphNode("Cloth", kind="cloth_solver"))
            scene = NodeItem(GraphNode("Scene", kind="scene"))
            for item in (source, cloth, scene):
                graph.addItem(item)
                graph._node_items[item.model.name] = item
            body = SimpleNamespace(model=GraphNode("Body", kind="import"), _collect_scene_assets=lambda: [
                {"node": "Body", "path": str(mesh), "ext": ".obj", "kind": "mesh"}])
            graph.edges[cloth] = [SimpleNamespace(src=source, dst_port_name="pattern"), SimpleNamespace(src=body, dst_port_name="collider")]
            graph.edges[scene] = [SimpleNamespace(src=cloth)]
            scene.model._scene_xforms = {"Cloth": {"pos": [1, 2, 3], "rot": [0, 0, 0], "scl": [1, 1, 1]}}
            assets = scene_spec._collect_assets(scene)
            self.assertEqual([a["node"] for a in assets], ["Cloth", "Body"])
            self.assertEqual(assets[0]["xform"]["pos"], [1, 2, 3])
            self.assertEqual(assets[0]["cloth"]["pattern_owner"], "Pattern")
            self.assertTrue(Path(assets[0]["path"]).is_file())
            self.assertEqual(cloth._collect_scene_assets()[0]["source_kind"], "cloth_solver")
            card = QtWidgets.QWidget()
            card._node_ref = scene.model
            card._graph_scene = graph
            footer = QtWidgets.QVBoxLayout(card)
            with patch.object(scene_spec, "_collect_assets", return_value=assets):
                self.assertTrue(scene_spec.augment_infocard_footer(card, footer))
                self.app.processEvents()
                edits = card.findChildren(ColliderOwnerEdit)
                self.assertEqual(len(edits), 1)
                outliner = card._scene_outliner_widget
                kinds = [outliner.item(i).data(QtCore.Qt.UserRole + 1) for i in range(outliner.count())]
                self.assertIn("cloth_collider", kinds)
                mime = QtCore.QMimeData()
                mime.setText("Body")
                event = QtGui.QDropEvent(QtCore.QPointF(1, 1), QtCore.Qt.CopyAction, mime,
                                        QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                edits[0].dropEvent(event)
                self.app.processEvents()
                self.assertIn("Cloth", graph.changes)
                values = {p["name"]: p["value"] for p in cloth.model.params}
                self.assertEqual(values["collider_owner"], "Body")
                card.findChild(QtWidgets.QToolButton, "SceneAddClothColliderButton").click()
                self.app.processEvents()
                card._scene_outliner_refresh()
                self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
                edits = card.findChildren(ColliderOwnerEdit)
                self.assertEqual([edit.text() for edit in edits], ["Body", ""])
                mime.setText("Pants")
                event = QtGui.QDropEvent(QtCore.QPointF(1, 1), QtCore.Qt.CopyAction, mime,
                                        QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                edits[1].dropEvent(event)
                self.app.processEvents()
                self.assertEqual(cloth._param_value("collider_owner"), "Body,Pants")
                card._scene_outliner_refresh()
                self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
                card.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
                card.resize(750, 1000)
                card.show()
                self.app.processEvents()
                mime.setData(ROW_MIME, json.dumps({"scene": "Scene", "name": "Body", "kind": "mesh", "parent": ""}).encode())
                point = outliner.visualItemRect(outliner.item(0)).topLeft() + QtCore.QPoint(4, 1)
                event = QtGui.QDropEvent(QtCore.QPointF(point), QtCore.Qt.MoveAction, mime,
                                        QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                outliner.dropEvent(event)
                self.assertTrue(event.isAccepted())
                self.app.processEvents()
                self.assertEqual(outliner.item(0).data(QtCore.Qt.UserRole), "Body")
                self.assertEqual(outliner.item(1).data(QtCore.Qt.UserRole), "Cloth")
                self.assertEqual([outliner.item(i).data(QtCore.Qt.UserRole + 1) for i in range(2, 6)],
                                 ["cloth_collider", "cloth_collider", "pattern_piece", "pattern_piece"])
                order = next(p["value"] for p in _node_to_dict(scene.model)["params"] if p["name"] == "__outliner_order")
                self.assertEqual(json.loads(order)["roots"], ["Body", "Cloth"])
                self.assertIn("__outliner_order", scene._ui_hidden_params_set())
                front_token = outliner.item(4).data(QtCore.Qt.UserRole)
                back_token = outliner.item(5).data(QtCore.Qt.UserRole)
                mime.setData(ROW_MIME, json.dumps({"scene": "Scene", "name": back_token,
                                                "kind": "pattern_piece", "parent": "Cloth"}).encode())
                point = outliner.visualItemRect(outliner.item(4)).topLeft() + QtCore.QPoint(4, 1)
                event = QtGui.QDropEvent(QtCore.QPointF(point), QtCore.Qt.MoveAction, mime,
                                        QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                outliner.dropEvent(event)
                self.assertTrue(event.isAccepted())
                self.app.processEvents()
                self.assertEqual(outliner.item(4).data(QtCore.Qt.UserRole), back_token)
                order = json.loads(scene._param_value("__outliner_order"))
                self.assertEqual(order["children"]["Cloth"], [back_token, front_token])
                # A panel cannot be dragged out of its parent group.
                point = outliner.visualItemRect(outliner.item(0)).topLeft() + QtCore.QPoint(4, 1)
                event = QtGui.QDropEvent(QtCore.QPointF(point), QtCore.Qt.MoveAction, mime,
                                        QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                outliner.dropEvent(event)
                self.assertFalse(event.isAccepted())
                card._scene_outliner_refresh()
                self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
                self.assertEqual(outliner.item(0).data(QtCore.Qt.UserRole), "Body")
                card.findChildren(QtWidgets.QToolButton, "SceneRemoveClothColliderButton")[1].click()
                self.app.processEvents()
                self.assertEqual(cloth._param_value("collider_owner"), "Body")
            card.close()
            card.deleteLater()
            self.app.processEvents()
            graph.clear()

    def test_missing_or_unfinished_pattern_reports_diagnostic(self):
        graph = Graph()
        node = NodeItem(GraphNode("Cloth", kind="cloth_solver"))
        graph.addItem(node)
        self.assertIsNone(build_cloth_scene_asset(node))
        self.assertIn("saved Pattern", node.model._cloth_build_error)
        graph.clear()

    def cache_dialog_node(self):
        graph = Graph()
        window = QtWidgets.QMainWindow()
        window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        window.setCentralWidget(QtWidgets.QGraphicsView(graph))
        node = NodeItem(GraphNode("Cloth", kind="cloth_solver"))
        graph.addItem(node)
        window.show()
        self.app.processEvents()

        def cleanup():
            dialog = getattr(node, "_cloth_cache_dialog", None)
            if dialog is not None and isValid(dialog):
                dialog.reject()
            graph.clear()
            window.close()
            window.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

        self.addCleanup(cleanup)
        return graph, window, node

    def cache_body(self, node):
        return next(proxy.widget() for proxy in node._plugin_proxies if isinstance(proxy.widget(), ClothWidget))

    def click_cache_button(self, node, title):
        button = next(b for b in self.cache_body(node).findChildren(QtWidgets.QPushButton) if b.text() == title)
        button.click()

    def test_bake_dialog_survives_widget_rebuild_and_starts_only_after_selection(self):
        graph, window, node = self.cache_dialog_node()
        body = self.cache_body(node)
        with tempfile.TemporaryDirectory() as temp, patch("nodes.cloth_solver.spec.QtWidgets.QFileDialog", CacheFileDialog):
            self.click_cache_button(node, "Bake…")
            self.assertIsNone(getattr(node, "_cloth_cache_dialog", None))
            self.app.processEvents()
            dialog = node._cloth_cache_dialog
            self.assertIs(dialog.parentWidget(), window)
            self.assertIsNone(dialog.graphicsProxyWidget())
            self.assertTrue(dialog.isVisible())
            self.assertFalse(getattr(node.model, "_cloth_commands", []))
            node._build_widgets()
            self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            self.app.processEvents()
            self.assertFalse(isValid(body))
            self.assertTrue(isValid(dialog))
            self.assertTrue(dialog.isVisible())
            # A suffix-free selection must write a cache that Load Cache can find.
            target = Path(temp) / "garment"
            dialog.setDirectory(str(target.parent))
            dialog.findChild(QtWidgets.QLineEdit, "fileNameEdit").setText(target.name)
            self.app.processEvents()
            dialog.accept()
            self.app.processEvents()
            commands = node.model._cloth_commands
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0]["action"], "bake")
            self.assertEqual(Path(commands[0]["path"]), target.with_suffix(".npz"))
            self.assertIsNone(node._cloth_cache_dialog)

    def test_load_cache_dialog_survives_widget_rebuild(self):
        graph, window, node = self.cache_dialog_node()
        with tempfile.TemporaryDirectory() as temp, patch("nodes.cloth_solver.spec.QtWidgets.QFileDialog", CacheFileDialog):
            target = Path(temp) / "garment.npz"
            target.write_bytes(b"cache dialog selection fixture")
            self.click_cache_button(node, "Load cache…")
            self.app.processEvents()
            dialog = node._cloth_cache_dialog
            self.assertIs(dialog.parentWidget(), window)
            node._build_widgets()
            self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            self.app.processEvents()
            self.assertTrue(dialog.isVisible())
            dialog.setDirectory(str(target.parent))
            dialog.findChild(QtWidgets.QLineEdit, "fileNameEdit").setText(target.name)
            self.app.processEvents()
            dialog.accept()
            self.app.processEvents()
            self.assertEqual(Path(node._param_value("cache_path")), target)
            self.assertEqual([c["action"] for c in node.model._cloth_commands], ["pause", "load_cache"])
            self.assertIsNone(node._cloth_cache_dialog)

    def test_cancel_and_repeat_click_do_not_start_bake_or_duplicate_dialog(self):
        graph, window, node = self.cache_dialog_node()
        with patch("nodes.cloth_solver.spec.QtWidgets.QFileDialog", CacheFileDialog):
            self.click_cache_button(node, "Bake…")
            self.app.processEvents()
            dialog = node._cloth_cache_dialog
            self.click_cache_button(node, "Bake…")
            self.app.processEvents()
            self.assertIs(node._cloth_cache_dialog, dialog)
            self.assertEqual(len(window.findChildren(CacheFileDialog)), 1)
            dialog.reject()
            self.app.processEvents()
            self.assertIsNone(node._cloth_cache_dialog)
            self.assertFalse(getattr(node.model, "_cloth_commands", []))

    def test_deleting_node_closes_pending_cache_dialog(self):
        graph, window, node = self.cache_dialog_node()
        model = node.model
        with patch("nodes.cloth_solver.spec.QtWidgets.QFileDialog", CacheFileDialog):
            self.click_cache_button(node, "Bake…")
            self.app.processEvents()
            dialog = node._cloth_cache_dialog
            graph.clear()
            self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            self.app.processEvents()
            self.assertFalse(isValid(dialog))
            self.assertFalse(getattr(model, "_cloth_commands", []))

    def test_save_state_browser_pauses_and_queues_snapshot_without_baking(self):
        graph, window, node = self.cache_dialog_node()
        with tempfile.TemporaryDirectory() as temp, patch("nodes.cloth_solver.spec.QtWidgets.QFileDialog", CacheFileDialog):
            workflow = Path(temp) / "workflow.json"
            workflow.write_text("{}", encoding="utf-8")
            window._current_path = workflow
            self.click_cache_button(node, "Save state…")
            self.assertEqual([c["action"] for c in node.model._cloth_commands], ["pause"])
            self.app.processEvents()
            dialog = node._cloth_cache_dialog
            self.assertEqual(dialog.windowTitle(), "Save cloth state")
            self.assertEqual(Path(dialog.directory().absolutePath()).resolve(), workflow.parent.resolve())
            target = Path(temp) / "shirt.npz"
            dialog.findChild(QtWidgets.QLineEdit, "fileNameEdit").setText(target.name)
            dialog.accept()
            self.app.processEvents()
            self.assertEqual([c["action"] for c in node.model._cloth_commands], ["pause", "save"])
            self.assertEqual(Path(node.model._cloth_commands[-1]["path"]).resolve(), target.resolve())


class Buffer:
    def __init__(self, data):
        self.data = data
        self.size = len(data)
        self.writes = 0

    def write(self, data):
        if len(data) != self.size:
            raise ValueError("Buffer size mismatch")
        self.data = data
        self.writes += 1

    def orphan(self, size):
        self.size = size


class Scene:
    def __init__(self, items):
        self.items = items

    def iter_by_tag(self, tag):
        return [i for i in self.items if i.tag == tag]


class View(QtWidgets.QWidget, MGLRendererMixin):
    def __init__(self):
        super().__init__()
        self.resize(100, 100)
        self.frame = 0
        self.xform = {"pos": [0, 0, 0], "rot": [0, 0, 0], "scl": [1, 1, 1]}
        self._mgl_scene_bounds_by_owner = {}
        self._mgl_scene_mesh_bounds_by_owner = {}
        self._mesh_select_mode = "object"
        self._mgl_pick_proj = self._mgl_pick_view = self._mgl_pick_model = np.eye(4)
        self._mgl_active_viewport_rect = None

    def _timeline_current_frame(self):
        return self.frame

    def _mgl_get_scene_asset_xform(self, owner):
        return self.xform

    def _cloth_set_timeline(self, frame):
        self.frame = int(frame)

    def devicePixelRatioF(self):
        return 1.0

    def _mgl_update_cloth(self):
        # Existing rendering/cache assertions inspect completed frames. The real
        # viewport polls without waiting; tests explicitly wait for worker results.
        super()._mgl_update_cloth()
        for state in self._cloth_states.values():
            if state.pending is not None:
                state.pending.result(timeout=10)
                super()._mgl_update_cloth()


class ViewportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.view = View()
        self.asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)
        self.model = GraphNode("Cloth", kind="cloth_solver", params=[{"name": "cache_path", "value": ""}])
        settings = ClothSettings(device="cpu", self_collision=False, interactive=True, end_frame=3)
        self.config = {"asset": self.asset, "settings": settings.to_dict(), "pins": [], "colliders": [],
                       "collider_owner": "", "node_model": self.model, "cache_path": ""}
        points = self.asset.positions.astype("f4")
        self.entry = {"points": points, "normals": np.zeros_like(points), "indices": self.asset.triangles.ravel(),
                      "_cloth_vertices": np.arange(len(points)), "vertex_ids": np.arange(len(points)),
                      "vbo": Buffer(points.tobytes()), "nbo": Buffer(points.tobytes())}
        self.item = SimpleNamespace(payload={"owner": "Cloth", "cloth": self.config, "mesh_entry": self.entry}, tag="scene-model", visible=True)
        self.wire = SimpleNamespace(payload={"owner": "Cloth", "wire_vbo": Buffer(b"0")}, tag="scene-wire", visible=True)
        self.view._mgl_scene = Scene([self.item, self.wire])
        self.view._mgl_update_cloth()
        self.state = self.view._cloth_states["Cloth"]

    def tearDown(self):
        timer = getattr(self.view, "_cloth_timer", None)
        if timer:
            timer.stop()
        self.view.close()
        executor = getattr(self.view, "_cloth_executor", None)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    def event(self, kind, x=52, y=48, modifiers=QtCore.Qt.NoModifier):
        return QtGui.QMouseEvent(kind, QtCore.QPointF(x, y), QtCore.QPointF(x, y),
                                 QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, modifiers)

    def finish_seek(self, frame):
        self.view.frame = frame
        for _ in range(100):
            self.view._mgl_update_cloth()
            if self.view._cloth_timeline_ready(frame):
                return
        self.fail(getattr(self.model, "_cloth_status", "Seek did not finish"))

    def second_cloth(self, **overrides):
        config = {**self.config, "settings": dict(self.config["settings"]),
                  "node_model": GraphNode("Pants", kind="cloth_solver"), **overrides}
        points = self.asset.positions.astype("f4")
        entry = {"points": points.copy(), "normals": np.zeros_like(points), "indices": self.asset.triangles.ravel(),
                 "_cloth_vertices": np.arange(len(points)), "vertex_ids": np.arange(len(points)),
                 "vbo": Buffer(points.tobytes()), "nbo": Buffer(points.tobytes())}
        item = SimpleNamespace(payload={"owner": "Pants", "cloth": config, "mesh_entry": entry},
                               tag="scene-model", visible=True)
        self.view._mgl_scene.items.append(item)
        self.view._mgl_update_cloth()
        return config, self.view._cloth_states["Pants"], item

    def test_two_timeline_solvers_keep_separate_caches_and_the_same_body_frame(self):
        context = self.animated_body()
        sampled = []

        def evaluate(bridge):
            frame = self.view._mgl_timeline_frame_index()
            sampled.append(frame)
            height = -.05 + .24 * frame / 24
            return {"triangle_points": np.array([[[-1, height, -1], [0, height, 1], [1, height, -1]]])}

        with patch.object(self.view, "_mgl_groom_guide_mesh_collider", side_effect=evaluate):
            self.view._mgl_update_cloth()
            self.state = self.view._cloth_states["Cloth"]
            config, other, item = self.second_cloth()
            self.assertIsNot(self.state.cache, other.cache)
            self.finish_seek(15)
            self.assertEqual(self.state.frame, other.frame)
            self.assertEqual(sampled.count(15), 2)
            for state in (self.state, other):
                np.testing.assert_allclose(state.collider_previous["Body"][:, 1], .10, atol=1e-6)
                self.assertGreaterEqual(state.solver.x[:, 1].min(), .10 - 1e-6)
                self.assertAlmostEqual(state.solver.time, 15 / 24)
            self.assertFalse(np.shares_memory(self.state.cache.get(15)[0], other.cache.get(15)[0]))
            with patch.object(self.state.solver, "step", wraps=self.state.solver.step) as first, \
                 patch.object(other.solver, "step", wraps=other.solver.step) as second:
                for frame in (3, 15, 8, 15):
                    self.finish_seek(frame)
                first.assert_not_called()
                second.assert_not_called()

    def test_pants_loaded_at_later_playhead_simulate_from_saved_pose_after_rewind(self):
        self.animated_body()

        def evaluate(bridge):
            height = -.05 + .01 * self.view._mgl_timeline_frame_index()
            return {"triangle_points": np.array([[[-1, height, -1], [0, height, 1], [1, height, -1]]])}

        with patch.object(self.view, "_mgl_groom_guide_mesh_collider", side_effect=evaluate):
            self.view._mgl_update_cloth()
            config, pants, item = self.second_cloth()
            positions, velocity = pants.solver.x.copy(), np.zeros_like(pants.solver.x)
            self.view._cloth_restore_saved_state(pants, {"solver_time": 6}, {144: positions}, {144: velocity}, 30)
            self.assertEqual(pants.initial_pose[0], 30)
            self.finish_seek(0)
            np.testing.assert_allclose(pants.solver.x, positions, atol=1e-7)
            self.finish_seek(6)
            self.assertAlmostEqual(pants.solver.time, 6 + 6 / 24)
            self.assertGreaterEqual(pants.solver.x[:, 1].min(), .01 - 1e-6)
            self.assertEqual(pants.initial_pose[0], 0)

    def test_late_pants_collider_rebuilds_early_poses_for_direct_scrub(self):
        self.animated_body()

        def evaluate(bridge):
            height = -.05 + .01 * self.view._mgl_timeline_frame_index()
            return {"triangle_points": np.array([[[-1, height, -1], [0, height, 1], [1, height, -1]]])}

        with patch.object(self.view, "_mgl_groom_guide_mesh_collider", side_effect=evaluate):
            self.view._mgl_update_cloth()
            config, pants, item = self.second_cloth()
            positions, velocity = pants.solver.x.copy(), np.zeros_like(pants.solver.x)
            self.view._cloth_restore_saved_state(pants, {"solver_time": 6}, {144: positions}, {144: velocity}, 30)
            self.config["collider_owner"] = "Body,Pants"
            self.finish_seek(33)
            self.assertEqual(pants.initial_pose[0], 0)
            self.assertAlmostEqual(pants.solver.time, 6 + 33 / 24)
            np.testing.assert_allclose(self.state.collider_previous["Pants"], pants.cache.get(33)[0], atol=1e-7)

    def test_pants_bake_resume_frame_does_not_seed_shirt_timeline_start(self):
        config, pants, item = self.second_cloth(settings={**self.config["settings"], "follow_timeline": True})
        self.config["collider_owner"] = "Pants"
        self.config["settings"]["follow_timeline"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "pants-bake.npz")
            positions, velocity = pants.solver.x.copy(), pants.solver.velocity.copy()
            write_bake(path, {"signature": pants.signature, "geometry_signature": pants.geometry_signature,
                             "cache_type": "bake", "resume_frame": 30, "solver_time": 30 / 24},
                       [0, 30], [positions, positions], [velocity, velocity])
            config["cache_path"] = path
            self.view._mgl_update_cloth()
            self.assertEqual(self.view.frame, 0)  # File resume is deferred until the event ends.
            self.assertEqual(self.state.initial_pose[0], 0)
            self.assertEqual(self.state.frame, 0)
            self.assertIsNotNone(self.view._cloth_collider_pose(item, 0))

    def test_cloth_parent_collider_uses_cached_simulation_not_held_preview(self):
        self.config["settings"].update(follow_timeline=True, gravity_y=0, damping=0)
        self.view._mgl_update_cloth()
        self.state.solver.x[:, 2] = .04
        self.state.display_positions = self.state.solver.x.copy()
        self.state.cache.put(0, self.state.solver.x, self.state.solver.velocity, simulation_time=0)
        self.view._cloth_mark_initial_pose(self.state)
        config, other, item = self.second_cloth(colliders=[], collider_owner="")
        other.solver.velocity[:, 2] = .24
        other.cache.put(0, other.solver.x, other.solver.velocity, simulation_time=0)
        self.view._cloth_mark_initial_pose(other)
        initial = self.state.initial_pose[1].copy()
        self.config["collider_owner"] = "Pants"
        seen = []
        original = self.view._cloth_colliders_current_frame

        def colliders(cfg, state, items):
            result = original(cfg, state, items)
            if cfg is self.config and result:
                frame = self.view._mgl_timeline_frame_index()
                np.testing.assert_allclose(result[0].vertices, other.cache.get(frame)[0], atol=1e-7)
                if frame:
                    seen.append(frame)
            return result

        with patch.object(self.view, "_cloth_colliders_current_frame", side_effect=colliders):
            self.finish_seek(15)
        self.assertIs(self.view._cloth_states["Cloth"], self.state)
        np.testing.assert_allclose(self.state.initial_pose[1], initial)
        np.testing.assert_allclose(self.state.collider_previous["Pants"], other.cache.get(15)[0], atol=1e-7)
        self.assertEqual(seen, list(range(1, 16)))
        self.assertGreaterEqual(self.state.solver.x[:, 2].min(), other.solver.x[:, 2].min() - 1e-6)
        self.finish_seek(4)
        self.finish_seek(15)
        self.assertTrue(self.view._cloth_timeline_ready(15))

    def test_reciprocal_cloth_colliders_report_loop_without_starting_workers(self):
        config, other, item = self.second_cloth()
        self.config["collider_owner"] = "Pants"
        config["collider_owner"] = "Cloth"
        MGLRendererMixin._mgl_update_cloth(self.view)
        self.assertIn("Cloth collider loop", self.model._cloth_status)
        self.assertIsNone(self.state.pending)
        self.assertIsNone(other.pending)

    def test_evicted_cloth_collider_frames_rebuild_without_publishing_internal_poses(self):
        self.config["settings"].update(follow_timeline=True, gravity_y=0, damping=0)
        self.view._mgl_update_cloth()
        self.state.solver.x[:, 2] = .04
        self.state.display_positions = self.state.solver.x.copy()
        self.state.cache.put(0, self.state.solver.x, self.state.solver.velocity, simulation_time=0)
        self.view._cloth_mark_initial_pose(self.state)
        config, other, item = self.second_cloth()
        other.solver.velocity[:, 2] = .24
        other.cache.put(0, other.solver.x, other.solver.velocity, simulation_time=0)
        self.view._cloth_mark_initial_pose(other)
        self.finish_seek(8)
        displayed = other.display_positions.copy()
        cached = other.cache.get(8)
        other.cache.budget = sum(value.nbytes for value in cached)
        other.cache.clear()
        other.cache.put(8, *cached, simulation_time=other.solver.time)
        self.config["collider_owner"] = "Pants"
        original = self.view._cloth_follow_frame
        internal = []

        def follow(cfg, state, items, frame, **kwargs):
            result = original(cfg, state, items, frame, **kwargs)
            if cfg is config and not kwargs.get("publish", True):
                internal.append(frame)
                np.testing.assert_allclose(other.display_positions, displayed, atol=1e-7)
            return result

        with patch.object(self.view, "_cloth_follow_frame", side_effect=follow):
            self.finish_seek(8)
        self.assertTrue(internal)
        self.assertTrue(self.view._cloth_timeline_ready(8))
        np.testing.assert_allclose(self.state.collider_previous["Pants"], other.cache.get(8)[0], atol=1e-7)
        self.assertGreaterEqual(self.state.solver.x[:, 2].min(), other.solver.x[:, 2].min() - 1e-6)

    def test_backend_failure_reason_is_visible_while_calculating(self):
        self.state.seek_start, self.state.seek_target = 0, 33
        self.state.note = "Calculating cloth: frame 15 / 33"
        self.state.solver.backend_detail = "Warp failed; CPU fallback: test driver failure"
        self.view._cloth_status(self.model, self.state)
        self.assertIn("test driver failure", self.model._cloth_status)
        self.assertIn("frame 15 / 33", self.model._cloth_status)

    def test_bake_waits_for_timeline_cloth_collider_before_each_frame(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        config, other, item = self.second_cloth()
        self.config["collider_owner"] = "Pants"
        self.view._mgl_update_cloth()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "cloth-bake.npz")
            self.model._cloth_commands = [{"action": "bake", "path": path}]
            for _ in range(100):
                self.view._mgl_update_cloth()
                self.app.processEvents()
                state = self.view._cloth_states["Cloth"]
                if Path(path).is_file() and state.bake is None and state.pending is None:
                    break
            self.assertTrue(Path(path).is_file(), self.model._cloth_status)
            self.assertEqual(state.error, "", self.model._cloth_status)
            self.assertEqual(list(state.disk_frames), [0, 1, 2, 3])
            self.assertEqual(other.frame, 3)

    def test_adding_cloth_collider_keeps_loaded_starting_state_and_legacy_reload(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        config, other, item = self.second_cloth()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "sewn-state.npz")
            positions = self.state.solver.x + [0, 0, .04]
            metadata = {"signature": self.state.signature, "geometry_signature": self.state.geometry_signature,
                        "cache_type": "state", "solver_time": 2.5}
            write_bake(path, metadata, [0], [positions], [self.state.solver.velocity])
            original = Path(path).read_bytes()
            self.config["cache_path"] = path
            self.view._mgl_update_cloth()
            self.config["collider_owner"] = "Pants"
            self.finish_seek(8)
            np.testing.assert_allclose(self.state.initial_pose[1], positions, atol=1e-7)
            self.assertAlmostEqual(self.state.solver.time, 2.5 + 8 / 24)
            self.finish_seek(0)
            self.model._cloth_commands = [{"action": "load_cache", "path": path}]
            self.view._mgl_update_cloth()
            self.assertEqual(self.state.error, "", self.model._cloth_status)
            np.testing.assert_allclose(self.state.solver.x, positions, atol=1e-7)
            self.assertAlmostEqual(self.state.solver.time, 2.5)
            self.assertEqual(Path(path).read_bytes(), original)

    @staticmethod
    def body_clip(height):
        return AnimationClip("Body motion", 0, 1, 24, [JointAnimationTrack("Root",
            translation_keys=[Vec3Keyframe(0, (0, 0, 0)), Vec3Keyframe(1, (0, height, 0))])])

    def animated_body(self, height=.24):
        context = {"skeleton": object(), "clip": self.body_clip(height)}
        self.view._mgl_scene.items.append(SimpleNamespace(
            payload={"owner": "Body", "fbx_rig_context": context}, tag="scene-model", visible=True))
        self.config["colliders"] = [{"node": "Body"}]
        self.config["settings"]["follow_timeline"] = True
        return context

    def test_scrub_samples_every_collider_frame_without_moving_visible_playhead(self):
        context = self.animated_body()
        sampled, visible = [], []

        def evaluate(bridge):
            frame = self.view._mgl_timeline_frame_index()
            sampled.append(frame)
            visible.append(self.view.frame)
            height = -.05 + context["clip"].tracks[0].translation_keys[-1].value[1] * frame / 24
            return {"triangle_points": np.array([[[-1, height, -1], [0, height, 1], [1, height, -1]]])}

        with patch.object(self.view, "_mgl_groom_guide_mesh_collider", side_effect=evaluate):
            self.view._mgl_update_cloth()
            self.state = self.view._cloth_states["Cloth"]
            sampled.clear()
            visible.clear()
            self.finish_seek(8)
            self.assertEqual(sampled, list(range(1, 9)))
            self.assertEqual(visible, [8] * 8)
            self.assertAlmostEqual(self.state.solver.time, 8 / 24)
            np.testing.assert_allclose(self.state.collider_previous["Body"][:, 1], .03)
            self.assertGreaterEqual(self.state.solver.x[:, 1].min(), .03 - 1e-6)
            self.assertIsNone(self.model._cloth_seek_progress)
            with patch.object(self.state.solver, "step", wraps=self.state.solver.step) as solve:
                for frame in (3, 8, 0, 5):
                    self.finish_seek(frame)
                solve.assert_not_called()

    def test_animation_change_recalculates_from_saved_pose_and_identical_clip_reuses_cache(self):
        context = self.animated_body()
        with patch.object(self.view, "_mgl_groom_guide_mesh_collider", return_value={
                "triangle_points": np.array([[[-1, -2, -1], [0, -2, 1], [1, -2, -1]]])}):
            self.view._mgl_update_cloth()
            self.state = self.view._cloth_states["Cloth"]
            with tempfile.TemporaryDirectory() as temp:
                path = str(Path(temp) / "sewn.npz")
                positions = self.state.solver.x + [0, .4, 0]
                velocity = np.zeros_like(positions)
                write_bake(path, {"signature": self.state.signature, "geometry_signature": self.state.geometry_signature,
                                  "cache_type": "state", "resume_frame": 144, "solver_time": 6},
                           [144], [positions], [velocity])
                stored = Path(path).read_bytes()
                self.config["cache_path"] = path
                self.view._mgl_update_cloth()
                self.finish_seek(6)
                context["clip"] = self.body_clip(.24)  # An identical rebuilt graph is still the same animation.
                with patch.object(self.state.solver, "step", wraps=self.state.solver.step) as solve:
                    self.finish_seek(2)
                    self.finish_seek(6)
                    solve.assert_not_called()
                    context["clip"] = self.body_clip(.48)
                    self.finish_seek(6)
                    self.assertEqual(solve.call_count, 6)
                    self.assertAlmostEqual(self.state.solver.time, 6 + 6 / 24)
                    self.finish_seek(0)
                    np.testing.assert_allclose(self.state.solver.x, positions, atol=1e-7)
                    np.testing.assert_allclose(self.state.solver.velocity, velocity, atol=1e-7)
                    self.assertEqual(self.state.solver.time, 6)
                    self.finish_seek(6)
                    self.assertEqual(solve.call_count, 6)
                self.assertEqual(self.state.cache_path, path)
                self.assertEqual(Path(path).read_bytes(), stored)

    def test_cached_frames_survive_settings_edits_and_timeline_toggle(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        self.finish_seek(6)
        positions = self.state.solver.x.copy()
        self.finish_seek(2)
        self.config["settings"].update(gravity_y=0, substeps=6, friction=.9, bend=.3)
        with patch.object(self.state.solver, "step", wraps=self.state.solver.step) as solve:
            self.view._mgl_update_cloth()
            self.config["settings"]["follow_timeline"] = False
            self.view._mgl_update_cloth()
            self.view.frame = 6
            self.config["settings"]["follow_timeline"] = True
            self.view._mgl_update_cloth()
            self.finish_seek(6)
            np.testing.assert_allclose(self.state.solver.x, positions, atol=1e-7)
            solve.assert_not_called()
            self.finish_seek(7)
            self.assertEqual(solve.call_count, 1)

    def test_scrub_retargets_active_worker_and_displays_only_completed_target(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        started, release = threading.Event(), threading.Event()
        original = self.state.solver.step
        displayed = self.state.display_positions.copy()

        def slow_step(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test worker timed out")
            return original(*args, **kwargs)

        try:
            with patch.object(self.state.solver, "step", side_effect=slow_step) as solve:
                self.view.frame = 20
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertTrue(started.wait(1))
                self.assertEqual(self.model._cloth_seek_progress, (0, 0, 20))
                self.view.frame = 3
                MGLRendererMixin._mgl_update_cloth(self.view)
                heartbeat = []
                QtCore.QTimer.singleShot(0, lambda: heartbeat.append(True))
                self.app.processEvents()
                self.assertEqual(heartbeat, [True])
                np.testing.assert_array_equal(self.state.display_positions, displayed)
                release.set()
                self.state.pending.result(timeout=10)
                self.finish_seek(3)
                self.assertEqual(solve.call_count, 3)
                self.assertEqual(self.view.frame, 3)
                self.assertIsNone(self.state.pending)
                self.assertIsNone(self.model._cloth_seek_progress)
                np.testing.assert_array_equal(self.state.display_positions, self.state.solver.x)
                self.finish_seek(0)
                self.finish_seek(3)
                self.assertEqual(solve.call_count, 3)
        finally:
            release.set()

    def test_preview_eviction_keeps_initial_pose_for_recalculation(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        initial = self.state.solver.x.copy()
        self.state.cache.budget = 1  # Force eviction of all but the newest frame.
        self.finish_seek(4)
        self.assertIsNone(self.state.cache.get(0))
        self.finish_seek(0)
        np.testing.assert_allclose(self.state.solver.x, initial, atol=1e-7)
        self.assertEqual(self.state.solver.time, 0)

    def test_timeline_progress_fits_node_and_reports_requested_frame(self):
        graph = Graph()
        node = NodeItem(GraphNode("Cloth", kind="cloth_solver"))
        graph.addItem(node)
        body = node._plugin_proxies[0].widget()
        node.model._cloth_seek_progress = (5, 10, 25)
        body._status()
        progress = body.findChild(QtWidgets.QProgressBar, "ClothTimelineProgress")
        self.assertEqual(progress.maximum(), 20)
        self.assertEqual(progress.value(), 5)
        self.assertIn("11 / 25", progress.format())
        proxy = node._plugin_proxies[0]
        self.assertLessEqual(proxy.mapRectToParent(proxy.boundingRect()).bottom() + 9.99, node.height)
        self.assertLessEqual(progress.geometry().bottom(), body.height())
        node.model._cloth_seek_progress = None
        node.model._cloth_status = "Timeline frame 25\nPreview cache"
        body._status()
        self.assertEqual(progress.value(), progress.maximum())
        self.assertEqual(progress.format(), "Frame cached")
        graph.clear()

    def test_scrub_evaluates_keyed_collider_transforms_and_restores_visible_pose(self):
        self.animated_body(0)
        body_xform = {"pos": [0, 0, 0], "rot": [0, 0, 0], "scl": [1, 1, 1]}
        applied = []

        def apply_frame(frame):
            applied.append(frame)
            body_xform["pos"] = [0, .02 * frame, 0]

        with patch.object(self.view, "_mgl_get_scene_asset_xform", side_effect=lambda owner:
                body_xform if owner == "Body" else self.view.xform), \
             patch.object(self.view, "_timeline_apply_scene_frame", side_effect=apply_frame, create=True), \
             patch.object(self.view, "_timeline_keys_map_for_owner", return_value={
                 0: {"xyz": [0, 0, 0]}, 6: {"xyz": [0, .12, 0]}}, create=True), \
             patch.object(self.view, "_mgl_groom_guide_mesh_collider", return_value={
                 "triangle_points": np.array([[[-1, -.05, -1], [0, -.05, 1], [1, -.05, -1]]])}):
            self.view._mgl_update_cloth()
            self.state = self.view._cloth_states["Cloth"]
            self.view.frame = 6
            apply_frame(6)
            self.finish_seek(6)
            self.assertEqual(self.view.frame, 6)
            self.assertEqual(body_xform["pos"], [0, .12, 0])
            np.testing.assert_allclose(self.state.collider_previous["Body"][:, 1], .07, atol=1e-7)
            self.assertGreaterEqual(self.state.solver.x[:, 1].min(), .07 - 1e-6)
            self.assertTrue(all(frame in applied for frame in range(1, 7)))
            self.assertEqual(applied[-1], 6)

    def test_saved_snapshot_waits_until_requested_scrub_pose_is_complete(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "scrub_state.npz")
            self.view.frame = 8
            self.model._cloth_commands = [{"action": "save", "path": path}]
            self.view._mgl_update_cloth()
            self.assertFalse(Path(path).exists())
            self.finish_seek(8)
            self.view._mgl_update_cloth()
            metadata, frames, velocity = read_bake(path, self.state.signature, include_velocities=True)
            self.assertEqual(metadata["resume_frame"], 8)
            self.assertAlmostEqual(metadata["solver_time"], 8 / 24)
            np.testing.assert_allclose(frames[8], self.state.solver.x, atol=1e-7)
            np.testing.assert_allclose(velocity[8], self.state.solver.velocity, atol=1e-7)

    def test_step_updates_buffers_normals_wire_and_picking(self):
        before = self.entry["points"].copy()
        self.model._cloth_commands = [{"action": "step"}]
        self.view._mgl_update_cloth()
        self.assertLess(self.entry["points"][:, 1].mean(), before[:, 1].mean())
        self.assertTrue(np.isfinite(self.entry["normals"]).all())
        self.assertGreater(self.wire.payload["wire_vbo"].size, 1)
        topo = self.view._mgl_mesh_topology_for_owner("Cloth")
        np.testing.assert_allclose(topo["points"], self.entry["points"])
        writes = self.entry["vbo"].writes
        self.view._mgl_update_cloth()
        self.assertEqual(self.entry["vbo"].writes, writes)

    def test_transform_reset_and_render_do_not_double_translate(self):
        self.view.xform["pos"] = [1, 2, 3]
        self.view._mgl_update_cloth()
        state = self.view._cloth_states["Cloth"]
        np.testing.assert_allclose(state.solver.rest, self.asset.positions + [1, 2, 3])
        np.testing.assert_allclose(self.entry["points"], self.asset.positions, atol=1e-7)

    def test_centimeter_scene_scales_gravity_and_mesh_consistently(self):
        self.config["settings"]["scene_units_per_meter"] = 100
        self.view._mgl_update_cloth()
        np.testing.assert_allclose(self.entry["points"], self.asset.positions * 100, atol=1e-6)
        self.model._cloth_commands = [{"action": "step"}]
        self.view._mgl_update_cloth()
        self.assertLess(self.entry["points"][:, 1].mean(), (self.asset.positions * 100)[:, 1].mean())

    def test_output_fps_cannot_retime_timeline_cloth(self):
        self.view._timeline_fps = 24
        self.view._mgl_render_fps_override = 30
        self.config["settings"].update(follow_timeline=True, fps=48)
        self.view._mgl_update_cloth()
        before = self.state.solver.time
        self.view.frame = 1
        self.view._mgl_update_cloth()
        self.assertEqual(self.view._mgl_timeline_fps_value(), 24)
        self.assertAlmostEqual(self.state.solver.time - before, 1 / 24)
        self.assertTrue(self.view._cloth_render_ready(1))

    def test_render_waits_for_uncached_seek_and_can_preroll_from_available_pose(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        self.assertEqual(self.view._cloth_render_preroll_start(5), 0)
        self.view.frame = 5
        self.view._mgl_update_cloth()
        self.assertFalse(self.view._cloth_render_ready(5))
        self.assertEqual(self.view._cloth_render_error(5), "")
        self.assertIsNotNone(self.state.seek_target)
        self.view.frame = 1
        self.view._mgl_update_cloth()
        self.assertLess(self.view._cloth_render_preroll_start(5), 5)
        self.assertEqual(self.view._cloth_render_preroll_start(1), 1)
        self.finish_seek(5)
        self.assertTrue(self.view._cloth_render_ready(5))

    def test_render_holds_realtime_cloth_pose_without_changing_live_controls(self):
        self.state.playing = True
        self.state.next_tick = 0
        self.view._mgl_render_output_active = True
        positions, velocity = self.state.solver.x.copy(), self.state.solver.velocity.copy()
        clock = self.state.solver.time
        for _ in range(3):
            self.view._mgl_update_cloth()
        np.testing.assert_array_equal(self.state.solver.x, positions)
        np.testing.assert_array_equal(self.state.solver.velocity, velocity)
        self.assertEqual(self.state.solver.time, clock)
        self.assertTrue(self.state.playing)
        self.assertTrue(self.view._cloth_render_ready(0))
        self.view._mgl_render_output_active = False
        self.view._mgl_update_cloth()
        self.assertGreater(self.state.solver.time, clock)

    def test_timeline_cache_backward_and_uncached_jump(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        self.view.frame = 1
        self.view._mgl_update_cloth()
        first = self.state.solver.x.copy()
        self.view.frame = 2
        self.view._mgl_update_cloth()
        self.view.frame = 1
        self.view._mgl_update_cloth()
        np.testing.assert_allclose(self.state.solver.x, first, atol=1e-7)
        with patch.object(self.state.solver, "step", wraps=self.state.solver.step) as solve:
            self.finish_seek(20)
            self.assertEqual(solve.call_count, 18)  # Reuse frame 2, rather than restarting.
            positions = self.state.solver.x.copy()
            self.finish_seek(0)
            self.finish_seek(20)
            self.assertEqual(solve.call_count, 18)
            np.testing.assert_allclose(self.state.solver.x, positions, atol=1e-7)

    def test_timeline_mode_keeps_settled_pose_and_uses_animation_frame_rate(self):
        self.config["settings"]["start_frame"] = 10
        self.config["settings"]["end_frame"] = 20
        self.view._mgl_update_cloth()
        self.state = self.view._cloth_states["Cloth"]
        for _ in range(2):
            self.model._cloth_commands = [{"action": "step"}]
            self.view._mgl_update_cloth()
        settled = self.state.solver.x.copy()
        settled_velocity = self.state.solver.velocity.copy()
        settled_time = self.state.solver.time
        self.view._timeline_fps = 60
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        self.assertIs(self.state, self.view._cloth_states["Cloth"])
        np.testing.assert_array_equal(self.state.solver.x, settled)
        np.testing.assert_array_equal(self.state.solver.velocity, settled_velocity)
        self.assertEqual(self.state.frame, 0)
        self.assertEqual(self.state.solver.time, settled_time)
        self.assertEqual(list(self.state.cache.frames), [0])
        self.view.frame = 1
        self.view._mgl_update_cloth()
        self.assertAlmostEqual(self.state.solver.time, settled_time + 1 / 60)
        version = self.state.version
        for _ in range(3):
            self.view._mgl_update_cloth()
        self.assertEqual(self.state.version, version)
        self.view.frame = 0
        self.view._mgl_update_cloth()
        np.testing.assert_allclose(self.state.solver.x, settled, atol=1e-7)
        self.assertEqual(self.state.solver.time, settled_time)
        self.view.frame = 1
        self.view._mgl_update_cloth()
        self.assertAlmostEqual(self.state.solver.time, settled_time + 1 / 60)
        self.config["settings"]["follow_timeline"] = False
        self.view._mgl_update_cloth()
        held = self.state.solver.x.copy()
        self.view.frame = 2
        self.view._mgl_update_cloth()
        np.testing.assert_array_equal(self.state.solver.x, held)

    def test_timeline_step_advances_animation_and_disabled_cloth_does_not_solve(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        self.model._cloth_commands = [{"action": "step"}]
        self.view._mgl_update_cloth()
        self.assertEqual(self.view.frame, 0)  # Deferred until paint/event handling ends.
        self.app.processEvents()
        self.assertEqual(self.view.frame, 1)
        self.view._mgl_update_cloth()
        self.assertEqual(self.state.frame, 1)
        self.assertFalse(self.state.playing)
        self.config["settings"]["enabled"] = False
        held = self.state.solver.x.copy()
        self.view.frame = 2
        self.view._mgl_update_cloth()
        np.testing.assert_array_equal(self.state.solver.x, held)
        self.assertTrue(self.view._cloth_timeline_ready(2))

    def test_timeline_can_continue_after_loading_a_legacy_saved_drape(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "draped.npz")
            positions = self.state.solver.x + [0, .1, 0]
            velocity = np.zeros_like(positions)
            # Real-time settling advances the solver clock, not the animation.
            write_bake(path, {"signature": self.state.signature, "resume_frame": 144, "solver_time": 6},
                       [144], [positions], [velocity])
            self.config["cache_path"] = path
            self.view._mgl_update_cloth()
            self.app.processEvents()
            self.assertEqual(self.view.frame, 0)
            self.config["settings"]["follow_timeline"] = True
            self.view._mgl_update_cloth()
            self.assertEqual(self.state.frame, 0)
            np.testing.assert_allclose(self.state.solver.x, positions, atol=1e-7)
            self.view.frame = 1
            self.view._mgl_update_cloth()
            self.assertEqual(self.state.frame, 1)
            self.assertAlmostEqual(self.state.solver.time, 6 + 1 / 24)
            self.assertLess(self.state.solver.x[:, 1].mean(), positions[:, 1].mean())
            self.view.frame = 0
            self.view._mgl_update_cloth()
            np.testing.assert_allclose(self.state.solver.x, positions, atol=1e-7)
            self.assertAlmostEqual(self.state.solver.time, 6)

    def test_loading_state_with_timeline_enabled_uses_current_animation_frame(self):
        self.config["settings"]["follow_timeline"] = True
        self.view.frame = 5
        self.view._mgl_update_cloth()
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.npz")
            positions = self.state.solver.x + [0, .2, 0]
            velocity = np.full_like(positions, .01)
            write_bake(path, {"signature": self.state.signature, "cache_type": "state",
                              "resume_frame": 120, "solver_time": 5},
                       [120], [positions], [velocity])
            self.model._cloth_commands = [{"action": "load_cache", "path": path}]
            self.view._mgl_update_cloth()
            self.app.processEvents()
            self.assertEqual(self.view.frame, 5)
            self.assertEqual(self.state.frame, 5)
            np.testing.assert_allclose(self.state.solver.x, positions, atol=1e-7)
            np.testing.assert_allclose(self.state.solver.velocity, velocity, atol=1e-7)
            self.assertAlmostEqual(self.state.solver.time, 5)
            self.assertTrue(self.view._cloth_timeline_ready(5))
            self.view.frame = 6
            self.view._mgl_update_cloth()
            self.assertEqual(self.state.frame, 6)
            self.assertAlmostEqual(self.state.solver.time, 5 + 1 / 24)
            self.assertLess(self.state.solver.x[:, 1].mean(), positions[:, 1].mean())

    def test_saving_timeline_state_keeps_simulating_from_current_pose(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        self.view.frame = 1
        self.view._mgl_update_cloth()
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.npz")
            self.model._cloth_commands = [{"action": "save", "path": path}]
            self.view._mgl_update_cloth()
            self.assertTrue(Path(path).is_file())
            positions = self.state.solver.x.copy()
            self.view.frame = 2
            self.view._mgl_update_cloth()
            self.assertEqual(self.state.frame, 2)
            self.assertLess(self.state.solver.x[:, 1].mean(), positions[:, 1].mean())
            # Preview rewind restores the initial state instead of the pattern's rest pose.
            self.view.frame = 1
            self.view._mgl_update_cloth()
            np.testing.assert_allclose(self.state.solver.x, positions, atol=1e-7)

    def test_editing_gravity_after_loading_state_preserves_pose_and_history(self):
        self.config["settings"].update(follow_timeline=True, gravity_y=0)
        self.view._mgl_update_cloth()
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "sewn.npz")
            positions = self.state.solver.x + [0, .2, 0]
            write_bake(path, {"signature": self.state.signature, "geometry_signature": self.state.geometry_signature,
                              "cache_type": "state", "resume_frame": 120, "solver_time": 5},
                       [120], [positions], [np.zeros_like(positions)])
            original_file = Path(path).read_bytes()
            self.config["cache_path"] = path
            self.view._mgl_update_cloth()
            for frame in (1, 2):
                self.view.frame = frame
                self.view._mgl_update_cloth()
            self.view.frame = 1
            self.view._mgl_update_cloth()
            held = self.state.solver.x.copy()
            clock = self.state.solver.time
            self.config["settings"]["gravity_y"] = -9.81
            self.view._mgl_update_cloth()
            self.assertIs(self.state, self.view._cloth_states["Cloth"])
            np.testing.assert_array_equal(self.state.solver.x, held)
            self.assertEqual(self.state.solver.time, clock)
            self.assertIsNotNone(self.state.cache.get(0))
            self.assertEqual(self.state.cache_path, path)
            self.view.frame = 2
            self.view._mgl_update_cloth()
            np.testing.assert_allclose(self.state.solver.x, held, atol=1e-7)
            self.view.frame = 3  # New frames use the edited gravity; old previews stay cached.
            self.view._mgl_update_cloth()
            self.assertLess(self.state.solver.x[:, 1].mean(), held[:, 1].mean())
            self.assertEqual(Path(path).read_bytes(), original_file)
            self.config["settings"]["gravity_enabled"] = False
            held = self.state.solver.x.copy()
            velocity = self.state.solver.velocity.copy()
            self.view._mgl_update_cloth()
            np.testing.assert_array_equal(self.state.solver.x, held)
            np.testing.assert_array_equal(self.state.solver.velocity, velocity)
            self.assertEqual(self.state.solver.settings.gravity_y, -9.81)
            # Reload at a different gravity setting and keep the saved drape.
            self.model._cloth_commands = [{"action": "load_cache", "path": path}]
            self.view._mgl_update_cloth()
            np.testing.assert_allclose(self.state.solver.x, positions, atol=1e-7)
            self.assertAlmostEqual(self.state.solver.time, 5)

    def test_tuning_solver_controls_preserves_loaded_pose_velocity_and_cache(self):
        changes = {"stretch": 0, "bend": 3, "seam_stiffness": 0, "seam_seconds": 2, "seam_gap": .002,
                   "gravity_y": 0, "gravity_enabled": False, "damping": 2, "density": .8, "thickness": .003,
                   "friction": .8, "substeps": 8, "iterations": 12, "collision_quality": 4,
                   "self_collision": True, "ground": True, "ground_height": -.1, "fps": 30,
                   "start_frame": 5, "end_frame": 60, "device": "auto"}
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.npz")
            positions = self.state.solver.x + [0, .3, 0]
            velocity = np.full_like(positions, .02)
            write_bake(path, {"signature": self.state.signature, "cache_type": "state",
                              "resume_frame": 72, "solver_time": 3}, [72], [positions], [velocity])
            self.config["cache_path"] = path
            self.view._mgl_update_cloth()
            held = self.state.solver.x.copy()
            motion = self.state.solver.velocity.copy()
            for key, value in changes.items():
                with self.subTest(control=key):
                    self.config["settings"][key] = value
                    self.view._mgl_update_cloth()
                    self.assertIs(self.state, self.view._cloth_states["Cloth"])
                    np.testing.assert_array_equal(self.state.solver.x, held)
                    np.testing.assert_array_equal(self.state.solver.velocity, motion)
                    self.assertEqual(self.state.solver.time, 3)
                    self.assertEqual(self.state.frame, 72)
                    self.assertEqual(self.state.cache_path, path)
                    self.assertIsNotNone(self.state.cache.get(72))
                    self.assertEqual(getattr(self.state.solver.settings, key), value)

    def test_settings_edit_waits_for_active_worker_without_resetting_cloth(self):
        started, release = threading.Event(), threading.Event()
        original = self.state.solver.step
        previous = self.state.solver.settings

        def slow_step(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test worker timed out")
            return original(*args, **kwargs)

        try:
            with patch.object(self.state.solver, "step", side_effect=slow_step):
                self.model._cloth_commands = [{"action": "step"}]
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertTrue(started.wait(1))
                pending = self.state.pending
                self.config["settings"].update(gravity_enabled=False, density=.8, stretch=1000)
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertIs(self.view._cloth_states["Cloth"], self.state)
                self.assertIs(self.state.pending, pending)
                self.assertEqual(self.state.solver.settings, previous)
                heartbeat = []
                QtCore.QTimer.singleShot(0, lambda: heartbeat.append(True))
                self.app.processEvents()
                self.assertEqual(heartbeat, [True])
                release.set()
                pending.result(timeout=10)
                solved = self.state.solver.x.copy()
                MGLRendererMixin._mgl_update_cloth(self.view)
            self.assertEqual(self.state.frame, 1)
            np.testing.assert_array_equal(self.state.solver.x, solved)
            self.assertFalse(self.state.solver.settings.gravity_enabled)
            self.assertEqual(self.state.solver.settings.density, .8)
            self.assertAlmostEqual(self.state.solver.time, 1 / 24)
        finally:
            release.set()

    def test_editing_baked_cloth_settings_continues_from_displayed_cache_frame(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "baked.npz")
            positions = self.state.solver.x + [0, .3, 0]
            write_bake(path, {"signature": self.state.signature, "geometry_signature": self.state.geometry_signature,
                              "cache_type": "bake", "resume_frame": 1, "solver_time": 2},
                       [0, 1, 2], [positions, positions, positions], [np.zeros_like(positions)] * 3)
            self.config["cache_path"] = path
            self.view._mgl_update_cloth()
            self.app.processEvents()
            self.assertEqual(self.view.frame, 1)
            self.config["settings"]["gravity_y"] = -5
            self.view._mgl_update_cloth()
            np.testing.assert_allclose(self.state.solver.x, positions, atol=1e-7)
            self.view.frame = 2
            self.view._mgl_update_cloth()
            self.assertAlmostEqual(self.state.solver.time, 2 + 1 / 24)
            self.assertLess(self.state.solver.x[:, 1].mean(), positions[:, 1].mean())
            self.assertEqual(self.state.cache_path, path)
            self.assertEqual(set(self.state.disk_frames), {0, 1, 2})

    def test_timeline_playback_waits_for_worker_and_never_skips_collider_poses(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        started, release = threading.Event(), threading.Event()
        original = self.state.solver.step
        sampled, applied = [], []

        def slow_step(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test worker timed out")
            return original(*args, **kwargs)

        def apply_frame(frame):
            self.view.frame = frame
            applied.append(frame)

        def colliders(*args, **kwargs):
            sampled.append(self.view.frame)
            return []

        try:
            with patch.object(self.state.solver, "step", side_effect=slow_step), \
                 patch.object(self.view, "_cloth_colliders", side_effect=colliders), \
                 patch.object(self.view, "_timeline_max_known_frame", return_value=100, create=True), \
                 patch.object(self.view, "_timeline_loop_is_enabled", return_value=False, create=True), \
                 patch.object(self.view, "_timeline_playback_bounds", return_value=(0, 100), create=True), \
                 patch.object(self.view, "_timeline_set_frame_widgets", create=True), \
                 patch.object(self.view, "_timeline_apply_scene_frame", side_effect=apply_frame, create=True):
                tick = lambda: GraphGLTimelineModelMixin._timeline_on_play_tick(self.view)
                tick()
                self.assertEqual(self.view.frame, 1)
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertTrue(started.wait(1))
                for _ in range(3):
                    tick()
                self.assertEqual(self.view.frame, 1)
                heartbeat = []
                QtCore.QTimer.singleShot(0, lambda: heartbeat.append(True))
                self.app.processEvents()
                self.assertEqual(heartbeat, [True])
                release.set()
                self.state.pending.result(timeout=5)
                tick()  # A completed result must be collected and published first.
                self.assertEqual(self.view.frame, 1)
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.view._cloth_timeline_play_toggled(False)
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertEqual(self.state.frame, 1)
                self.assertIsNone(self.state.pending)
                tick()
                self.view._mgl_update_cloth()
                self.assertEqual(self.state.frame, 2)
                self.assertEqual(applied, [1, 2])
                self.assertEqual(sampled, [1, 2])
        finally:
            release.set()

    def test_timeline_waits_for_every_enabled_cloth(self):
        self.config["settings"]["follow_timeline"] = True
        self.view._mgl_update_cloth()
        other_config = dict(self.config)
        other_item = SimpleNamespace(payload={"owner": "Other Cloth", "cloth": other_config}, tag="scene-model")
        other = self.view._cloth_reset("Other Cloth", other_config, np.eye(4), 0)
        self.view._mgl_scene.items.append(other_item)
        self.view._cloth_states["Other Cloth"] = other
        self.assertTrue(self.view._cloth_timeline_ready(0))
        pending = Future()
        other.pending = pending
        self.assertFalse(self.view._cloth_timeline_ready(0))
        pending.set_result(None)
        self.assertFalse(self.view._cloth_timeline_ready(0))
        other.pending = None
        other.last_timeline = 1
        self.assertFalse(self.view._cloth_timeline_ready(0))
        other_config["settings"] = {**other_config["settings"], "enabled": False}
        self.assertTrue(self.view._cloth_timeline_ready(0))

    def test_mouse_grab_moves_cloth_and_restores_pause(self):
        self.assertTrue(self.view._cloth_mouse_press(self.event(QtCore.QEvent.MouseButtonPress)))
        self.assertTrue(self.state.playing)
        before = self.state.solver.x.copy()
        self.assertTrue(self.view._cloth_mouse_move(self.event(QtCore.QEvent.MouseMove, 54, 46)))
        self.view._mgl_update_cloth()
        self.assertGreater(np.max(abs(self.state.solver.x - before)), .001)
        self.assertTrue(self.view._cloth_mouse_release(self.event(QtCore.QEvent.MouseButtonRelease)))
        self.assertFalse(self.state.playing)
        self.assertIsNone(self.state.solver.grab)

    def test_alt_and_mesh_editing_do_not_start_grab(self):
        self.assertFalse(self.view._cloth_mouse_press(self.event(QtCore.QEvent.MouseButtonPress, modifiers=QtCore.Qt.AltModifier)))
        self.view._mesh_select_mode = "face"
        self.assertFalse(self.view._cloth_mouse_press(self.event(QtCore.QEvent.MouseButtonPress)))

    def test_interaction_toggle_preserves_draped_state(self):
        self.model._cloth_commands = [{"action": "step"}]
        self.view._mgl_update_cloth()
        previous = self.state.solver.x.copy()
        self.config["settings"]["interactive"] = False
        self.view._mgl_update_cloth()
        self.assertIs(self.state, self.view._cloth_states["Cloth"])
        np.testing.assert_array_equal(previous, self.state.solver.x)

    def test_loading_cache_at_same_frame_updates_display(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "draped.npz")
            positions = self.state.solver.x + [0, 0, .1]
            write_bake(path, {"signature": self.state.signature}, [0], [positions])
            self.config["cache_path"] = path
            self.view._mgl_update_cloth()
            np.testing.assert_allclose(self.entry["points"], positions, atol=1e-7)

    def test_missing_collider_stops_simulation_with_diagnostic(self):
        self.config["collider_owner"] = "Missing body"
        self.view._mgl_update_cloth()
        self.assertIn("Collider not in Scene", self.model._cloth_status)
        self.assertFalse(self.view._cloth_states["Cloth"].playing)

    def test_pin_commands_persist_authored_particle_indices(self):
        graph = Graph()
        node = NodeItem(GraphNode("Cloth", kind="cloth_solver"))
        graph.addItem(node)
        self.config["node_item"] = node
        self.view._mesh_selected_elements = lambda: [{"mode": "point", "owner": "Cloth", "vertex_index": 0}]
        self.view._cloth_command({"action": "pin"}, "Cloth", self.config, self.state, 0)
        self.app.processEvents()
        self.assertEqual(json.loads(node._param_value("pins")), [0])
        self.config["pins"] = [0]
        self.view._cloth_command({"action": "unpin"}, "Cloth", self.config, self.state, 0)
        self.app.processEvents()
        self.assertEqual(json.loads(node._param_value("pins")), [])
        graph.clear()

    def test_pinning_loaded_state_keeps_pose_file_and_completed_preview_history(self):
        self.config["settings"].update(gravity_y=0, follow_timeline=True)
        self.view._mgl_update_cloth()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "settled.npz")
            pose = self.state.solver.x + [0, .5, .1]
            write_bake(path, {"signature": self.state.signature, "placement_signature": self.state.placement_signature,
                              "cache_type": "state", "solver_time": 6}, [0], [pose], [np.zeros_like(pose)])
            original_file = Path(path).read_bytes()
            self.config["cache_path"] = path
            self.view._mgl_update_cloth()
            self.finish_seek(3)
            self.finish_seek(1)
            current = self.state.solver.x.copy()
            rest = self.state.solver.rest.copy()
            initial = tuple(np.copy(value) if isinstance(value, np.ndarray) else value for value in self.state.initial_pose)
            clock = self.state.solver.time
            self.view._mesh_selected_elements = lambda: [{"mode": "point", "owner": "Cloth", "vertex_index": 0}]
            self.model._cloth_commands = [{"action": "pin"}]
            self.view._mgl_update_cloth()
            self.app.processEvents()
            self.assertIs(self.view._cloth_states["Cloth"], self.state)
            np.testing.assert_array_equal(self.state.solver.x, current)
            np.testing.assert_array_equal(self.state.solver.rest, rest)
            np.testing.assert_array_equal(self.state.solver.pin_positions[0], current[0])
            np.testing.assert_array_equal(self.state.initial_pose[1], initial[1])
            self.assertEqual(self.state.solver.time, clock)
            self.assertIsNotNone(self.state.cache.get(0))
            self.assertIsNotNone(self.state.cache.get(1))
            self.assertIsNone(self.state.cache.get(2))
            self.assertEqual(self.state.cache_path, path)
            self.assertEqual(Path(path).read_bytes(), original_file)
            self.config["settings"]["gravity_y"] = -9.81
            self.finish_seek(2)
            np.testing.assert_array_equal(self.state.solver.x[0], current[0])
            self.assertLess(self.state.solver.x[1:, 1].mean(), current[1:, 1].mean())
            values = {p["name"]: p["value"] for p in self.model.params}
            self.assertEqual(json.loads(values["pins"]), [0])
            np.testing.assert_array_equal(json.loads(values["pin_positions"])["0"], current[0])

    def test_panel_and_expanded_parent_point_selections_map_to_solver_particles(self):
        front, back = rectangle("Front", 100, 100), rectangle("Back", 100, 100)
        back.layout[0] = 200
        asset = build_cloth_asset(PatternDocument(pieces=[front, back]), 25)
        self.config["asset"] = asset
        self.entry.update(points=asset.positions.astype("f4"), normals=np.zeros_like(asset.positions, dtype="f4"),
                          indices=asset.triangles.ravel(), _cloth_vertices=np.arange(len(asset.positions)),
                          vertex_ids=np.arange(len(asset.positions)), vbo=Buffer(asset.positions.astype("f4").tobytes()),
                          nbo=Buffer(np.zeros_like(asset.positions, dtype="f4").tobytes()))
        self.view._mgl_update_cloth()
        state = self.view._cloth_states["Cloth"]
        target = int(asset.pieces()[1]["vertices"][0])
        self.view._mesh_selected_elements = lambda: [
            {"mode": "point", "owner": piece_owner("Cloth", back.id), "vertex_index": 0}]
        self.model._cloth_commands = [{"action": "pin"}]
        self.view._mgl_update_cloth()
        self.assertEqual(state.solver.pins.tolist(), [target])
        topology = self.view._mgl_mesh_topology_for_owner("Cloth")
        topology["vertex_ids"] = np.r_[target, np.arange(1, len(asset.positions))]
        self.view._mesh_selected_elements = lambda: [{"mode": "point", "owner": "Cloth", "vertex_index": 0}]
        self.model._cloth_commands = [{"action": "unpin"}]
        self.view._mgl_update_cloth()
        self.assertEqual(state.solver.pins.tolist(), [])

    def test_pin_edit_waits_for_active_worker_and_anchors_the_displayed_pose(self):
        started, release = threading.Event(), threading.Event()
        original = self.state.solver.step
        displayed = self.state.display_positions.copy()

        def slow_step(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test worker timed out")
            return original(*args, **kwargs)

        try:
            with patch.object(self.state.solver, "step", side_effect=slow_step):
                self.model._cloth_commands = [{"action": "step"}]
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertTrue(started.wait(1))
                pending = self.state.pending
                self.view._mesh_selected_elements = lambda: [{"mode": "point", "owner": "Cloth", "vertex_index": 0}]
                self.model._cloth_commands = [{"action": "pin"}]
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertEqual(self.state.solver.pins.tolist(), [])
                self.assertIs(self.state.pending, pending)
                self.assertIs(self.view._cloth_states["Cloth"], self.state)
                release.set()
                pending.result(timeout=10)
                MGLRendererMixin._mgl_update_cloth(self.view)
            np.testing.assert_array_equal(self.state.solver.x[0], displayed[0])
            self.assertEqual(self.state.solver.pins.tolist(), [0])
            self.assertAlmostEqual(self.state.solver.time, 1 / 24)
        finally:
            release.set()

    def test_elastic_material_edits_keep_loaded_initial_pose_and_current_clock(self):
        doc = PatternDocument(pieces=[rectangle("Panel", 100, 100)])
        self.config["asset"] = build_cloth_asset(doc, 25)
        self.view._mgl_update_cloth()
        state = self.view._cloth_states["Cloth"]
        state.solver.x[:] += [0, .5, .1]
        state.solver.time = 6
        state.display_positions = state.solver.x.copy()
        self.view._cloth_mark_initial_pose(state)
        current = state.solver.x.copy()
        initial = state.initial_pose[1].copy()
        set_elastic_edges(doc, [(doc.pieces[0].id, doc.pieces[0].segments[2].id)], True, 3500, .8)
        self.config["asset"] = build_cloth_asset(doc, 25)
        self.view._mgl_update_cloth()
        self.assertIs(self.view._cloth_states["Cloth"], state)
        np.testing.assert_array_equal(state.solver.x, current)
        np.testing.assert_array_equal(state.initial_pose[1], initial)
        self.assertEqual(state.solver.time, 6)
        self.assertTrue(any(group["kind"] == "elastic" for group in state.solver.groups))

    def test_bake_runs_frames_and_keeps_final_pose(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "cloth.npz")
            self.model._cloth_commands = [{"action": "bake", "path": path}]
            self.view._mgl_update_cloth()
            for _ in range(5):
                self.app.processEvents()
                self.view._mgl_update_cloth()
            self.assertTrue(Path(path).is_file(), self.model._cloth_status)
            self.assertEqual(self.view.frame, 3)
            self.assertEqual(len(self.view._cloth_states["Cloth"].disk_frames), 4)
            self.assertEqual(self.model.params[0]["value"], path)

    def test_stop_bake_writes_partial_cache_and_blocks_queued_frames(self):
        self.config["settings"]["end_frame"] = 30
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "partial.npz")
            self.model._cloth_commands = [{"action": "bake", "path": path}]
            self.view._mgl_update_cloth()
            self.app.processEvents()
            self.view._mgl_update_cloth()
            self.app.processEvents()
            self.view._mgl_update_cloth()
            state = self.view._cloth_states["Cloth"]
            final_frame = state.frame
            self.assertGreater(final_frame, 0)
            self.model._cloth_commands = [{"action": "toggle"}]
            self.view._mgl_update_cloth()
            for _ in range(2):
                self.app.processEvents()
                self.view._mgl_update_cloth()
            metadata, frames, velocities = read_bake(path, state.signature, include_velocities=True)
            self.assertEqual(max(frames), final_frame)
            self.assertEqual(metadata["resume_frame"], final_frame)
            self.assertEqual(metadata["cache_type"], "bake")
            self.assertEqual(self.view.frame, final_frame)
            self.assertFalse(state.playing)
            self.assertIsNone(state.bake)
            np.testing.assert_allclose(frames[final_frame], state.solver.x, atol=1e-7)
            np.testing.assert_allclose(velocities[final_frame], state.solver.velocity, atol=1e-7)
            final = state.solver.x.copy()
            self.model._cloth_commands = [{"action": "reset"}]
            self.view.frame = 0
            self.view._mgl_update_cloth()
            self.model._cloth_commands = [{"action": "load_cache", "path": path}]
            self.view._mgl_update_cloth()
            self.app.processEvents()
            self.assertEqual(self.view.frame, final_frame)
            np.testing.assert_allclose(self.view._cloth_states["Cloth"].display_positions, final, atol=1e-7)

    def test_stop_during_pending_bake_waits_without_blocking_and_saves_completed_solve(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "pending.npz")
            self.model._cloth_commands = [{"action": "bake", "path": path}]
            self.view._mgl_update_cloth()
            state = self.view._cloth_states["Cloth"]
            state.bake["primed"] = True
            pending = Future()
            state.pending, state.pending_kind, state.pending_frame = pending, "bake", 1
            self.view._cloth_command({"action": "stop"}, "Cloth", self.config, state, 0)
            self.assertIs(state.pending, pending)
            self.assertFalse(Path(path).exists())
            state.solver.step()
            final = state.solver.x.copy()
            pending.set_result(None)
            self.view._cloth_collect("Cloth", self.config, state)
            self.assertEqual(state.pending_kind, "save")
            state.pending.result(timeout=10)
            self.view._cloth_collect("Cloth", self.config, state)
            _, frames = read_bake(path, state.signature)
            self.assertEqual(set(frames), {0, 1})
            np.testing.assert_allclose(frames[1], final, atol=1e-7)

    def test_timeline_pause_saves_partial_bake(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "timeline.npz")
            self.model._cloth_commands = [{"action": "bake", "path": path}]
            self.view._mgl_update_cloth()
            state = self.view._cloth_states["Cloth"]
            self.view._cloth_timeline_play_toggled(False)
            self.view._mgl_update_cloth()
            self.assertTrue(Path(path).is_file())
            self.assertFalse(state.playing)
            self.assertIsNone(state.bake)

    def test_save_state_load_keeps_animation_playhead_and_can_continue(self):
        for _ in range(3):
            self.model._cloth_commands = [{"action": "step"}]
            self.view._mgl_update_cloth()
        final = self.state.solver.x.copy()
        velocity = self.state.solver.velocity.copy()
        solver_time = self.state.solver.time
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.npz")
            self.model._cloth_commands = [{"action": "save", "path": path}]
            self.view._mgl_update_cloth()
            self.view._mgl_update_cloth()
            self.assertTrue(Path(path).is_file())
            self.app.processEvents()
            self.assertEqual(self.view.frame, 0)
            metadata, _ = read_bake(path, self.state.signature)
            self.assertEqual(metadata["cache_type"], "state")
            self.model._cloth_commands = [{"action": "reset"}]
            self.view.frame = 0
            self.view._mgl_update_cloth()
            self.model._cloth_commands = [{"action": "load_cache", "path": path}]
            self.view._mgl_update_cloth()
            self.app.processEvents()
            state = self.view._cloth_states["Cloth"]
            self.assertEqual(self.view.frame, 0)
            np.testing.assert_allclose(state.solver.x, final, atol=1e-7)
            np.testing.assert_allclose(state.solver.velocity, velocity, atol=1e-7)
            self.assertAlmostEqual(state.solver.time, solver_time)
            self.model._cloth_commands = [{"action": "step"}]
            self.view._mgl_update_cloth()
            self.assertEqual(state.frame, 4)
            self.assertGreater(state.solver.time, solver_time)
            self.assertLess(state.solver.x[:, 1].mean(), final[:, 1].mean())

    def test_save_failure_can_be_retried_without_staying_in_saving_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.npz")
            with patch("echograph.ui.gl_view_cloth.write_bake", side_effect=OSError("disk full")):
                self.model._cloth_commands = [{"action": "save", "path": path}]
                # Poll an intentionally failed write without the fixture's result()
                # helper, which propagates worker exceptions before Qt can report them.
                MGLRendererMixin._mgl_update_cloth(self.view)
                pending = self.state.pending
                try:
                    pending.result(timeout=10)
                except OSError:
                    pass
                MGLRendererMixin._mgl_update_cloth(self.view)
            self.assertIsNone(self.state.saving)
            self.assertIn("disk full", self.model._cloth_status)
            self.assertFalse(Path(path).exists())
            self.model._cloth_commands = [{"action": "save", "path": path}]
            self.view._mgl_update_cloth()
            self.assertTrue(Path(path).is_file())

    def test_one_frame_bake_and_reset_exit_cache_playback(self):
        self.config["settings"]["end_frame"] = 0
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "single.npz")
            self.model._cloth_commands = [{"action": "bake", "path": path}]
            self.view._mgl_update_cloth()
            self.app.processEvents()
            self.view._mgl_update_cloth()
            self.assertTrue(Path(path).is_file(), self.model._cloth_status)
            metadata, _ = read_bake(path, self.view._cloth_states["Cloth"].signature)
            self.assertEqual(metadata["cache_type"], "bake")
            # A one-frame Bake still targets its recorded animation frame.
            self.model._cloth_commands = [{"action": "reset"}]
            self.view.frame = 5
            self.view._mgl_update_cloth()
            self.model._cloth_commands = [{"action": "load_cache", "path": path}]
            self.view._mgl_update_cloth()
            self.app.processEvents()
            self.assertEqual(self.view.frame, 0)
            self.model._cloth_commands = [{"action": "reset"}]
            self.view._mgl_update_cloth()
            state = self.view._cloth_states["Cloth"]
            self.assertFalse(state.disk_frames)
            self.assertEqual(self.model.params[0]["value"], "")

    def test_animated_collider_is_sampled_at_start_before_first_step(self):
        self.config["settings"]["follow_timeline"] = True
        body = SimpleNamespace(payload={"owner": "Body", "fbx_rig_context": {"skeleton": object()}},
                               tag="scene-model", visible=True)
        self.view._mgl_scene.items.append(body)
        self.config["colliders"] = [{"node": "Body"}]
        sampled = []

        def evaluate(bridge):
            sampled.append(self.view.frame)
            height = -.05 if self.view.frame == 0 else .2
            points = np.array([[-1, height, -1], [0, height, 1], [1, height, -1]])
            return {"triangle_points": points.reshape(1, 3, 3)}

        with patch.object(self.view, "_mgl_groom_guide_mesh_collider", side_effect=evaluate):
            self.view._mgl_update_cloth()
            self.view.frame = 1
            self.view._mgl_update_cloth()
        self.assertEqual(sampled, [0, 1])
        state = self.view._cloth_states["Cloth"]
        self.assertTrue(np.isfinite(state.solver.x).all())
        self.assertGreaterEqual(state.solver.x[:, 1].min(), .2)
        np.testing.assert_allclose(state.collider_previous["Body"][:, 1], .2)

    def test_reloading_scene_buffers_keeps_simulation_state(self):
        self.model._cloth_commands = [{"action": "step"}]
        self.view._mgl_update_cloth()
        positions = self.state.solver.x.copy()
        self.item.payload.pop("_cloth_publish_key", None)
        self.entry["points"] = self.asset.positions.astype("f4")
        self.view._mgl_update_cloth()
        self.assertIs(self.state, self.view._cloth_states["Cloth"])
        np.testing.assert_allclose(self.entry["points"], positions, atol=1e-7)

    def test_slow_solve_keeps_qt_responsive_and_pause_works(self):
        started, release = threading.Event(), threading.Event()
        original = self.state.solver.step

        def slow_step(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test worker timed out")
            return original(*args, **kwargs)

        before = self.entry["points"].copy()
        try:
            with patch.object(self.state.solver, "step", side_effect=slow_step):
                self.model._cloth_commands = [{"action": "toggle"}]
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertTrue(started.wait(1))
                pending = self.state.pending
                self.assertFalse(pending.done())
                heartbeat = []
                QtCore.QTimer.singleShot(0, lambda: heartbeat.append(True))
                self.app.processEvents()
                self.assertEqual(heartbeat, [True])
                np.testing.assert_array_equal(self.entry["points"], before)
                self.model._cloth_commands = [{"action": "toggle"}]
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertFalse(self.state.playing)
                self.assertIs(self.state.pending, pending)
                release.set()
                pending.result(timeout=5)
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertIsNone(self.state.pending)
                self.assertLess(self.entry["points"][:, 1].mean(), before[:, 1].mean())
        finally:
            release.set()

    def test_reset_discards_pending_frame_without_waiting_or_overwriting_rest(self):
        started, release = threading.Event(), threading.Event()
        original = self.state.solver.step

        def slow_step(*args, **kwargs):
            started.set()
            release.wait(5)
            return original(*args, **kwargs)

        try:
            with patch.object(self.state.solver, "step", side_effect=slow_step):
                self.model._cloth_commands = [{"action": "step"}]
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertTrue(started.wait(1))
                pending = self.state.pending
                self.model._cloth_commands = [{"action": "reset"}]
                MGLRendererMixin._mgl_update_cloth(self.view)
                reset = self.view._cloth_states["Cloth"]
                self.assertIsNot(reset, self.state)
                self.assertFalse(pending.done())
                release.set()
                pending.result(timeout=5)
                MGLRendererMixin._mgl_update_cloth(self.view)
                self.assertIs(self.view._cloth_states["Cloth"], reset)
                np.testing.assert_allclose(self.entry["points"], self.asset.positions, atol=1e-7)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
