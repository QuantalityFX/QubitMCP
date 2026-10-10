import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6 import QtCore, QtWidgets

from echograph.model import GraphNode
from echograph.persistence import _node_to_dict
from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.patterns.serialization import save_document
from echograph.patterns.sewing import add_sew_pair
from echograph.physics.cloth import ClothSettings, build_cloth_asset
from echograph.physics.cloth.placement import piece_owner, transform_points, xform_matrix
from echograph.physics.cloth.cache import write_bake
from echograph.ui.gl_mgl_renderer import MGLRendererMixin
from echograph.ui.gl_view_cloth import cloth_signature
from echograph.ui.node_item import NodeItem
from nodes.scene import spec as scene_spec
from nodes.cloth_solver.spec import build_cloth_scene_asset
from test_cloth_integration import Buffer, Scene, Graph


class PlacementView(QtWidgets.QWidget, MGLRendererMixin):
    def __init__(self):
        super().__init__()
        self.frame = 0
        self._mgl_scene_xforms_by_owner = {"Cloth": {"pos": [0, 0, 0], "rot": [0, 0, 0], "scl": [1, 1, 1]}}
        self._mgl_scene_bounds_by_owner = {}
        self._mgl_scene_mesh_bounds_by_owner = {}

    def _timeline_current_frame(self):
        return self.frame

    def _cloth_set_timeline(self, frame):
        self.frame = frame


class PlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.front = rectangle("Front", 100, 100)
        self.back = rectangle("Back", 100, 100)
        self.back.layout[0] = 200
        self.document = PatternDocument(pieces=[self.front, self.back])
        add_sew_pair(self.document, self.front.id, self.front.segments[0].id,
                     self.back.id, self.back.segments[0].id)
        self.asset = build_cloth_asset(self.document, 25)
        self.model = GraphNode("Cloth", kind="cloth_solver")
        self.config = {"asset": self.asset, "settings": ClothSettings(device="cpu", self_collision=False).to_dict(),
                       "piece_transforms": {}, "pins": [], "colliders": [], "cache_path": "", "node_model": self.model}
        self.view = PlacementView()
        points = self.asset.positions.astype("f4")
        self.entry = {"points": points, "normals": np.zeros_like(points), "indices": self.asset.triangles.ravel(),
                      "_cloth_vertices": np.arange(len(points)), "vertex_ids": np.arange(len(points)),
                      "vbo": Buffer(points.tobytes()), "nbo": Buffer(points.tobytes())}
        self.item = SimpleNamespace(payload={"owner": "Cloth", "cloth": self.config, "mesh_entry": self.entry},
                                    tag="scene-model", visible=True)
        self.view._mgl_scene = Scene([self.item])
        self.view._mgl_update_cloth()
        self.front_owner = piece_owner("Cloth", self.front.id)
        self.back_owner = piece_owner("Cloth", self.back.id)
        self.addCleanup(self.view.close)

    def test_named_panels_preserve_topology_and_export_object_names(self):
        pieces = self.asset.pieces()
        self.assertEqual([p["name"] for p in pieces], ["Front", "Back"])
        self.assertEqual(sum(len(p["vertices"]) for p in pieces), len(self.asset.positions))
        obj = self.asset.rest_obj().read_text(encoding="utf-8")
        self.assertIn("o Front\ng " + self.front.id, obj)
        self.assertIn("o Back\ng " + self.back.id, obj)
        front = self.view._mgl_mesh_topology_for_owner(self.front_owner)
        back = self.view._mgl_mesh_topology_for_owner(self.back_owner)
        self.assertEqual(front["pattern_piece_name"], "Front")
        self.assertFalse(set(front["vertex_ids"]) & set(back["vertex_ids"]))
        face = int(pieces[1]["faces"][0])
        self.assertEqual(self.view._cloth_piece_for_face("Cloth", face), self.back_owner)

    def test_move_rotate_and_scale_one_panel_preserves_other_and_sewing(self):
        before = self.entry["points"].copy()
        pieces = self.asset.pieces()
        self.view._mgl_set_scene_asset_xform(self.front_owner, pos=[.05, .05, .2], rot=[0, 180, 0], scl=[1, 1, 1])
        self.view._mgl_update_cloth()
        front, back = pieces
        expected = transform_points(before[front["vertices"]] - front["center"],
                                    xform_matrix({"pos": [.05, .05, .2], "rot": [0, 180, 0]}))
        np.testing.assert_allclose(self.entry["points"][front["vertices"]], expected, atol=1e-7)
        np.testing.assert_array_equal(self.entry["points"][back["vertices"]], before[back["vertices"]])
        self.view._mgl_set_scene_asset_xform(self.front_owner, scl=[1.5, 1.5, 1.5])
        self.view._mgl_update_cloth()
        self.assertGreater(np.ptp(self.entry["points"][front["vertices"], 0]), .14)
        self.assertIs(self.view._cloth_states["Cloth"].solver.asset, self.asset)
        self.assertGreater(len(self.asset.seam_indices), 0)

    def test_placement_uses_world_gizmo_and_persists_in_parent_space_and_meters(self):
        self.config["settings"]["scene_units_per_meter"] = 100
        self.view._mgl_set_scene_asset_xform("Cloth", pos=[10, 20, 30], rot=[0, 45, 0], scl=[2, 2, 2])
        self.view._mgl_update_cloth()
        parent = self.view._cloth_matrix("Cloth")
        target = transform_points(np.asarray([[15, 5, 0]]), parent)[0]
        self.view._mgl_set_scene_asset_xform(self.front_owner, pos=target)
        self.view._mgl_update_cloth()
        np.testing.assert_allclose(self.view._mgl_get_scene_asset_xform(self.front_owner)["pos"], target, atol=1e-6)
        saved = json.loads(next(p["value"] for p in _node_to_dict(self.model)["params"] if p["name"] == "piece_transforms"))
        np.testing.assert_allclose(saved[self.front.id]["pos"], [.15, .05, 0], atol=1e-7)
        placed = self.view._cloth_states["Cloth"].display_positions.copy()
        self.view._mgl_set_scene_asset_xform("Cloth", pos=[20, 20, 30])
        self.view._mgl_update_cloth()
        np.testing.assert_allclose(self.view._cloth_states["Cloth"].display_positions, placed + [.1, 0, 0], atol=1e-7)
        # A workflow rebuild uses serialized placements rather than transient geometry.
        self.config["piece_transforms"] = saved
        self.view._cloth_states.clear()
        self.view._mgl_update_cloth()
        np.testing.assert_allclose(self.view._mgl_get_scene_asset_xform(self.front_owner)["pos"], target + [10, 0, 0], atol=1e-6)

    def test_pattern_and_solver_outliner_list_named_children_and_inherit_placement(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "shirt.pattern.json"
            save_document(path, self.document)
            placements = {self.front.id: {"pos": [.05, .05, .2], "rot": [0, 180, 0], "scl": [1, 1, 1]}}
            graph = Graph()
            source = NodeItem(GraphNode("Pattern", kind="pattern_design", params=[
                {"name": "pattern_path", "value": str(path)}, {"name": "piece_transforms", "value": json.dumps(placements)}]))
            solver = NodeItem(self.model)
            scene = NodeItem(GraphNode("Scene", kind="scene"))
            for item in (source, solver, scene):
                graph.addItem(item)
                graph._node_items[item.model.name] = item
            self.addCleanup(graph.clear)
            graph.edges[solver] = [SimpleNamespace(src=source, dst_port_name="pattern")]
            built = build_cloth_scene_asset(solver)
            self.assertEqual(built["cloth"]["piece_transforms"], placements)
            for upstream in (source, solver):
                graph.edges[scene] = [SimpleNamespace(src=upstream)]
                scene._scene_collect_assets_cache = None
                assets = scene_spec._collect_assets(scene)
                card = QtWidgets.QWidget()
                self.addCleanup(card.deleteLater)
                card._node_ref, card._graph_scene = scene.model, graph
                footer = QtWidgets.QVBoxLayout(card)
                with patch.object(scene_spec, "_collect_assets", return_value=assets):
                    scene_spec.augment_infocard_footer(card, footer)
                    outliner = card._scene_outliner_widget
                    children = [outliner.item(i) for i in range(outliner.count())
                                if outliner.item(i).data(QtCore.Qt.UserRole + 1) == "pattern_piece"]
                    self.assertEqual(len(children), 2)
                    self.assertEqual([c.data(QtCore.Qt.UserRole) for c in children],
                                     [piece_owner(upstream.model.name, self.front.id), piece_owner(upstream.model.name, self.back.id)])
                    self.assertEqual([a["name"] for a in assets[0]["pattern_pieces"]], ["Front", "Back"])

    def test_world_rotation_under_nonuniform_parent_keeps_exact_affine_placement(self):
        self.view._mgl_set_scene_asset_xform("Cloth", pos=[1, 2, 3], rot=[15, 45, 10], scl=[2, 1, .5])
        self.view._mgl_update_cloth()
        before = self.view._mgl_get_scene_asset_xform(self.front_owner)
        target = {**before, "rot": [35, 80, 12]}
        self.view._mgl_set_scene_asset_xform(self.front_owner, rot=target["rot"])
        self.view._mgl_update_cloth()
        front = self.asset.pieces()[0]
        expected = transform_points(self.asset.positions[front["vertices"]] - front["center"], xform_matrix(target))
        np.testing.assert_allclose(self.view._cloth_states["Cloth"].display_positions[front["vertices"]], expected, atol=1e-7)
        # A subsequent world translation retains that exact orientation.
        self.view._mgl_set_scene_asset_xform(self.front_owner, pos=np.asarray(target["pos"]) + [.2, 0, 0])
        self.view._mgl_update_cloth()
        np.testing.assert_allclose(self.view._cloth_states["Cloth"].display_positions[front["vertices"]], expected + [.2, 0, 0], atol=1e-7)

    def test_workflow_load_outliner_keeps_panels_without_building_geometry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "shirt.pattern.json"
            save_document(path, self.document)
            graph = Graph()
            window = QtWidgets.QWidget()
            self.addCleanup(window.deleteLater)
            window._workflow_load_in_progress = True
            window._current_path = str(Path(temp) / "shirt.qubit")
            graph_view = QtWidgets.QGraphicsView(graph, window)
            source = NodeItem(GraphNode("Pattern", kind="pattern_design", params=[
                {"name": "pattern_path", "value": str(path)}]))
            solver = NodeItem(GraphNode("Cloth", kind="cloth_solver"))
            scene = NodeItem(GraphNode("Scene", kind="scene"))
            for item in (source, solver, scene):
                graph.addItem(item)
                graph._node_items[item.model.name] = item
            self.addCleanup(graph.clear)
            next(p for p in source.model.params if p["name"] == "pattern_path")["value"] = path.name
            graph.edges[solver] = [SimpleNamespace(src=source, dst_port_name="pattern")]
            for upstream in (source, solver):
                with self.subTest(upstream=upstream.model.kind):
                    graph.edges[scene] = [SimpleNamespace(src=upstream)]
                    card = QtWidgets.QWidget(window)
                    card._node_ref, card._graph_scene = scene.model, graph
                    footer = QtWidgets.QVBoxLayout(card)
                    with patch.object(scene_spec, "_collect_assets") as collect:
                        scene_spec.augment_infocard_footer(card, footer)
                        collect.assert_not_called()
                    outliner = card._scene_outliner_widget
                    children = [outliner.item(i) for i in range(outliner.count())
                                if outliner.item(i).data(QtCore.Qt.UserRole + 1) == "pattern_piece"]
                    self.assertEqual([child.data(QtCore.Qt.UserRole) for child in children],
                                     [piece_owner(upstream.model.name, self.front.id),
                                      piece_owner(upstream.model.name, self.back.id)])
                    self.assertEqual(len(card.findChildren(QtWidgets.QLineEdit, "SceneClothColliderEdit")),
                                     int(upstream is solver))

    def test_editing_placement_unloads_stale_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "old.npz")
            state = self.view._cloth_states["Cloth"]
            write_bake(path, {"signature": state.signature}, [0], [state.solver.x + [0, 0, .1]])
            self.config["cache_path"] = path
            self.view._mgl_update_cloth()
            self.view._mgl_set_scene_asset_xform(self.front_owner, pos=[.05, .05, .3])
            self.view._mgl_update_cloth()
            self.assertEqual(self.config["cache_path"], "")
            self.assertNotIn("error", getattr(self.model, "_cloth_status", "").lower())
            front = self.asset.pieces()[0]
            np.testing.assert_allclose(self.entry["points"][front["vertices"], 2], .3, atol=1e-7)
            self.assertTrue(Path(path).is_file())

    def test_restoring_original_placement_keeps_cache_signature_compatible(self):
        initial = cloth_signature(self.config, np.eye(4))
        self.view._mgl_set_scene_asset_xform(self.front_owner, pos=[.05, .05, .2])
        self.assertNotEqual(cloth_signature(self.config, np.eye(4)), initial)
        self.view._mgl_set_scene_asset_xform(self.front_owner, pos=[.05, .05, 0])
        self.assertEqual(cloth_signature(self.config, np.eye(4)), initial)


if __name__ == "__main__":
    unittest.main()
