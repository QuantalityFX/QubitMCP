import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from echograph.model import GraphNode
from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.patterns.serialization import save_document
from echograph.physics.cloth import ClothSettings, build_cloth_asset
from echograph.physics.cloth.cache import write_bake
from echograph.ui.gl_mgl_renderer import MGLRendererMixin
from echograph.ui.node_item import NodeItem
from nodes.cloth_solver.spec import build_cloth_scene_asset, ColliderOwnerEdit
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
        for _ in range(2):
            node._recompute_height()
            node._build_widgets()
            self.app.processEvents()
            self.assertEqual(node.width, width)
            for proxy in node._plugin_proxies:
                rect = proxy.mapRectToParent(proxy.boundingRect())
                self.assertLessEqual(rect.right(), node.width + .01)
                self.assertLessEqual(rect.bottom() + 9.99, node.height)
        graph.clear()

    def test_saved_pattern_to_scene_and_outliner_collider_drop(self):
        with tempfile.TemporaryDirectory() as temp:
            pattern = Path(temp) / "garment.pattern.json"
            save_document(pattern, PatternDocument(pieces=[rectangle("Front", 100, 100)]))
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

    def test_timeline_cache_backward_and_uncached_jump(self):
        self.view.frame = 1
        self.view._mgl_update_cloth()
        first = self.state.solver.x.copy()
        self.view.frame = 2
        self.view._mgl_update_cloth()
        self.view.frame = 1
        self.view._mgl_update_cloth()
        np.testing.assert_allclose(self.state.solver.x, first, atol=1e-7)
        self.view.frame = 20
        self.view._mgl_update_cloth()
        self.assertIn("Uncached", self.state.note)

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

    def test_bake_runs_frames_and_restores_timeline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "cloth.npz")
            self.model._cloth_commands = [{"action": "bake", "path": path}]
            self.view._mgl_update_cloth()
            for _ in range(5):
                self.app.processEvents()
                self.view._mgl_update_cloth()
            self.assertTrue(Path(path).is_file(), self.model._cloth_status)
            self.assertEqual(self.view.frame, 0)
            self.assertEqual(len(self.view._cloth_states["Cloth"].disk_frames), 4)
            self.assertEqual(self.model.params[0]["value"], path)

    def test_one_frame_bake_and_reset_exit_cache_playback(self):
        self.config["settings"]["end_frame"] = 0
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "single.npz")
            self.model._cloth_commands = [{"action": "bake", "path": path}]
            self.view._mgl_update_cloth()
            self.app.processEvents()
            self.view._mgl_update_cloth()
            self.assertTrue(Path(path).is_file(), self.model._cloth_status)
            self.model._cloth_commands = [{"action": "reset"}]
            self.view._mgl_update_cloth()
            state = self.view._cloth_states["Cloth"]
            self.assertFalse(state.disk_frames)
            self.assertEqual(self.model.params[0]["value"], "")

    def test_animated_collider_is_sampled_at_start_before_first_step(self):
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
