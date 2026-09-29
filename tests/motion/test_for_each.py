import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ast
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6 import QtCore, QtGui, QtWidgets, QtTest

from echograph.model import GraphNode
from echograph.persistence import serialize_scene
from echograph.ui.node_item import NodeItem
from nodes.anim_retarget import spec as retarget
from nodes.for_each import spec, runtime
from nodes.mocap_collection import spec as collection
from nodes.render import spec as render
from nodes.render.takes import RenderResult, reserve_take_folder
from nodes.scene import spec as scene_spec

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

BVH = """HIERARCHY
ROOT Hips
{
 OFFSET 0 0 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 End Site
 {
  OFFSET 0 1 0
 }
}
MOTION
Frames: 2
Frame Time: 0.05
0 0 0 0 0 0
{x} 0 0 0 0 0
"""

# Exercise the real comment wrapper without importing the shelf's app launcher.
_shelf = Path(__file__).resolve().parents[2] / "echograph_shelf.py"
_comment_ast = next(node for node in ast.parse(_shelf.read_text(encoding="utf-8-sig")).body
                    if isinstance(node, ast.ClassDef) and node.name == "CommentGroup")
_namespace = dict(QtCore=QtCore, QtGui=QtGui, QtWidgets=QtWidgets, DEFAULT_COMMENT_COLOR="#1f2933",
                  _normalize_comment_color=lambda value: value or "#1f2933")
exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), _comment_ast], type_ignores=[])), str(_shelf), "exec"), _namespace)
CommentGroup = _namespace["CommentGroup"]


class Scene(QtWidgets.QGraphicsScene):
    paramChanged = QtCore.Signal(str, list)
    linksChanged = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._node_items, self._edges, self._comment_groups = {}, [], []
        self._scene_asset_revision = 0

    def add_node(self, model, pos):
        model._graph_scene = self
        item = NodeItem(model)
        self.addItem(item)
        item.setPos(pos)
        self._node_items[model.name] = item
        return item

    def _unique_node_name(self, base, kind):
        candidate, number = base, 1
        while candidate in self._node_items:
            number += 1
            candidate = f"{base}_{number}"
        return candidate

    def _ordered_in_edges(self, item):
        return [edge for edge in self._edges if edge.dst is item]

    def connect(self, source, target, port):
        self._edges.append(SimpleNamespace(src=source, dst=target, dst_port_name=port))
        self._scene_asset_revision += 1

    def set_node_params(self, name, params, rebuild=False, emit=True):
        self._node_items[name].model.params = params
        self._scene_asset_revision += 1
        if emit:
            self.paramChanged.emit(name, params)

    def _add_comment_group_from_data(self, data):
        group = CommentGroup(self, data["title"], data.get("body", ""), data["members"], QtCore.QRectF(*data["rect"]), data["color"])
        self.addItem(group)
        self._comment_groups.append(group)

    def _refresh_comment_group_membership(self, group):
        rect = group.mapRectToScene(group._rect)
        group._members = [name for name, item in self._node_items.items() if rect.contains(item.sceneBoundingRect().center())]

    def _move_comment_members(self, group, delta):
        for name in group.members():
            item = self._node_items.get(name)
            if item:
                item.setPos(item.pos() + delta)

    def comment_groups_data(self):
        return [group.to_dict() for group in self._comment_groups]


class ForEachTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.scene = Scene()
        self.assets = [{"kind": "camera", "node": "Camera", "aspect_width": 16, "aspect_height": 16}]
        self.asset_patch = patch.object(render, "_collect_scene_assets", return_value=(self.assets, ""))
        self.asset_patch.start()
        self.addCleanup(self.asset_patch.stop)
        self.start = self.scene.add_node(GraphNode("Start", kind="for_each"), QtCore.QPointF(0, 0))
        self.collection = self.scene.add_node(GraphNode("Collection", kind="mocap_collection"), QtCore.QPointF(-600, 0))
        self.render = self.scene.add_node(GraphNode("Render", kind="render", params=[{"name": "output", "value": str(self.root / "renders" / "frame.png")}]), QtCore.QPointF(480, 180))
        self.scene_item = self.scene.add_node(GraphNode("Scene", kind="node"), QtCore.QPointF(-600, 700))
        self.scene_item.model.kind = "scene"
        APP.processEvents()
        self.end = next(item for item in self.scene._node_items.values() if item.model.kind == "for_each_end")
        self.scene.connect(self.collection, self.start, "source")
        self.scene.connect(self.collection, self.scene_item, "")
        self.scene.connect(self.scene_item, self.render, "scene")
        self.scene.connect(self.render, self.end, "render")
        self.paths = []
        for i in range(3):
            folder = self.root / f"animation_{i}"
            folder.mkdir()
            path = folder / "preview.bvh"
            path.write_text("fixture")
            self.paths.append(path)
        collection.add_paths(self.collection.model, self.paths, self.scene)
        self.session = runtime.session_for(self.start)
        self.renderer = self.render._plugin_proxies[0].widget()

    def tearDown(self):
        if self.session.running:
            self.session.stop()
            self.finish()
        self.scene.clear()
        self.scene.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def finish(self):
        for _ in range(100):
            APP.processEvents()
            if not self.session.running:
                return
        self.fail("Loop did not settle")

    def wire_retarget_chain(self):
        for number, path in enumerate(self.paths, 1):
            path.write_text(BVH.replace("{x}", str(number)))
        reference_path = self.root / "tpose.bvh"
        reference_path.write_text(BVH.replace("{x}", "0"))
        reference = self.scene.add_node(GraphNode("Tpose", kind="mocap_import", params=[
            {"name": "path", "value": str(reference_path)}]), QtCore.QPointF(-600, 500))
        target = self.scene.add_node(GraphNode("Target", kind="mocap_import", params=[
            {"name": "path", "value": str(reference_path)}]), QtCore.QPointF(-600, 1000))
        node = self.scene.add_node(GraphNode("Retarget", kind="anim_retarget"), QtCore.QPointF(450, 0))
        self.scene._edges = [edge for edge in self.scene._edges if edge.dst is not self.scene_item]
        self.scene.connect(self.start, node, "animation")
        self.scene.connect(reference, node, "source")
        self.scene.connect(target, node, "target")
        self.scene.connect(node, self.scene_item, "")
        retarget.set_joint_map_link(node, "Hips", "Hips", notify_scene=False)
        APP.processEvents()  # Attach the newly added nodes' deferred UI callbacks.
        return node, reference_path

    def assert_active_animation(self, node, reference_path, number):
        result = retarget.resolve_anim_retarget_inputs(node, persist=True)
        self.assertEqual(result.errors, [])
        self.assertEqual(Path(result.source_context["path"]), reference_path)
        self.assertEqual(Path(result.animation_context["path"]), self.paths[number - 1])
        reference_track = next(track for track in result.source_context["clip"].tracks if track.joint_name == "Hips")
        self.assertTrue(all(key.value == (0.0, 0.0, 0.0) for key in reference_track.translation_keys))
        assets = scene_spec._collect_assets(self.scene_item)
        asset = next(asset for asset in assets if asset["kind"] == "anim_retarget")
        track = next(track for track in asset["fbx_rig_context"]["clip"].tracks if track.joint_name == "Hips")
        self.assertAlmostEqual(track.translation_keys[-1].value[0], number)

    def test_idle_output_tracks_manual_collection_changes_without_running(self):
        node, reference_path = self.wire_retarget_chain()
        rows = collection.entries(self.collection.model)
        for number in (1, 3, 2):
            collection.save_state(self.collection.model, rows, number, self.scene)
            self.assert_active_animation(node, reference_path, number)
        self.assertFalse(self.session.running)
        self.assertEqual(self.session.index, 0)
        self.assertFalse((self.root / "renders").exists())

    def test_loop_waits_for_render_with_animation_passing_through_start(self):
        node, reference_path = self.wire_retarget_chain()
        rows = collection.entries(self.collection.model)
        rows[1]["checked"] = False
        collection.mocap._set_param_value(self.collection.model, "checked_only", "1")
        collection.save_state(self.collection.model, rows, 1, self.scene)
        visited = []
        def render_take(**kwargs):
            number = collection.output_number(self.collection.model)
            self.assert_active_animation(node, reference_path, number)
            visited.append(number)
            APP.processEvents()
            self.assertEqual(collection.output_number(self.collection.model), number)
            return RenderResult("completed", written_frames=2)
        with patch.object(self.renderer, "render_sequence", side_effect=render_take):
            self.session.begin()
            self.finish()
        self.assertEqual(visited, [1, 3])
        self.assertFalse(self.session.running)
        self.assertEqual([record["source_path"] for record in self.session.records], [str(self.paths[0]), str(self.paths[2])])
        # Finishing a batch must not freeze the output at its last iteration.
        collection.save_state(self.collection.model, rows, 1, self.scene)
        self.assert_active_animation(node, reference_path, 1)

    def test_invalid_pass_through_input_does_not_reuse_previous_animation(self):
        node, reference_path = self.wire_retarget_chain()
        self.assert_active_animation(node, reference_path, 3)
        other = self.scene.add_node(GraphNode("Unsupported", kind="node"), QtCore.QPointF(-900, 0))
        cases = (([], "Connect Mocap Collection"), ([self.start], "cycle"),
                 ([other], "unsupported node kind"), ([self.collection, self.collection], "multiple inputs"))
        for inputs, message in cases:
            with self.subTest(message=message):
                self.scene._edges = [edge for edge in self.scene._edges if edge.dst is not self.start]
                for upstream in inputs:
                    self.scene.connect(upstream, self.start, "source")
                result = retarget.resolve_anim_retarget_inputs(node, persist=True)
                self.assertIn(message, result.animation_error)
                self.assertIsNone(result.animation_context)
                self.assertIsNone(retarget.build_anim_retarget_clip(node, result))

    def test_wrapper_has_start_end_tracks_dragged_nodes_and_serializes(self):
        group = runtime.wrapper_for(self.start)
        self.assertIsInstance(group, CommentGroup)
        self.assertEqual(runtime.loop_nodes(self.start), (self.end, self.render))
        old_pos = self.render.pos()
        group.setPos(group.pos() + QtCore.QPointF(40, 60))
        self.assertEqual(self.render.pos(), old_pos + QtCore.QPointF(40, 60))
        self.render.setPos(-1000, -1000)
        with self.assertRaisesRegex(ValueError, "Render node"):
            runtime.loop_nodes(self.start)
        self.render.setPos(old_pos + QtCore.QPointF(40, 60))
        group = runtime.wrapper_for(self.start)
        saved = group.to_dict()
        self.assertIn("Render", saved["members"])
        before = len(self.scene._node_items)
        spec.ensure_wrapper(self.start)
        self.assertEqual(len(self.scene._node_items), before)
        self.assertEqual(runtime.value(self.start.model, "wrapper_created"), "1")

    def test_workflow_round_trip_preserves_wrapper_members_ports_and_limit(self):
        self.wire_retarget_chain()
        runtime.wrapper_for(self.start)
        collection.mocap._set_param_value(self.start.model, "loop_limit", "2")
        data = json.loads(json.dumps(serialize_scene(self.scene)))
        restored = Scene()
        try:
            for row in data["nodes"]:
                model = GraphNode(row["name"], kind=row["kind"], params=row["params"])
                restored.add_node(model, QtCore.QPointF(*row["pos"]))
            for edge in data["edges"]:
                restored.connect(restored._node_items[edge["src"]], restored._node_items[edge["dst"]], edge.get("dst_port", ""))
            for group in data["comments"]:
                restored._add_comment_group_from_data(group)
            APP.processEvents()
            start = restored._node_items["Start"]
            end, renderer = runtime.loop_nodes(start)
            self.assertEqual(end.model.kind, "for_each_end")
            self.assertEqual(renderer.model.name, "Render")
            self.assertEqual(len(restored._comment_groups), 1)
            self.assertEqual(len(runtime.iteration_plan(start, restored._node_items["Collection"])), 2)
            restored_retarget = restored._node_items["Retarget"]
            self.assertIs(runtime.incoming(restored_retarget, "animation"), start)
            result = retarget.resolve_anim_retarget_inputs(restored_retarget, persist=True)
            self.assertEqual(result.errors, [])
            self.assertEqual(Path(result.animation_context["path"]), self.paths[-1])
        finally:
            restored.clear()
            restored.deleteLater()

    def test_checked_outputs_wait_for_render_and_record_exact_paths(self):
        rows = collection.entries(self.collection.model)
        rows[1]["checked"] = False
        collection.mocap._set_param_value(self.collection.model, "checked_only", "1")
        collection.mocap._set_param_value(self.collection.model, "live_view", "1")
        collection.save_state(self.collection.model, rows, 1, self.scene)
        visited = []
        def render_take(**kwargs):
            current = collection.output_number(self.collection.model)
            visited.append(current)
            self.assertEqual(runtime.value(self.collection.model, "live_view"), "0")
            APP.processEvents()  # Even during rendering, no next iteration may start.
            self.assertEqual(collection.output_number(self.collection.model), current)
            return RenderResult("completed", output_template=str(kwargs["output_template"]), written_frames=2, end_frame=1)
        with patch.object(self.renderer, "render_sequence", side_effect=render_take):
            self.session.begin()
            self.finish()
        self.assertEqual(visited, [1, 3])
        manifest = json.loads(self.session.manifest_path.read_text())
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual([entry["source_path"] for entry in manifest["takes"]], [str(self.paths[0]), str(self.paths[2])])
        for i, record in enumerate(manifest["takes"], 1):
            folder = Path(record["take_folder"])
            self.assertTrue(folder.name.startswith(f"Take_{i:03d}_animation_"))
            self.assertEqual(json.loads((folder / "take.json").read_text())["source_path"], record["source_path"])
        self.assertEqual(runtime.value(self.collection.model, "live_view"), "1")
        self.assertEqual(runtime.value(self.end.model, "iteration"), "2")

    def test_limit_and_rerun_preserve_previous_takes(self):
        collection.mocap._set_param_value(self.start.model, "loop_limit", "2")
        with patch.object(self.renderer, "render_sequence", return_value=RenderResult("completed")) as render_take:
            self.session.begin()
            self.finish()
            first_manifest = self.session.manifest_path
            first_data = first_manifest.read_bytes()
            self.assertEqual(render_take.call_count, 2)
            self.session.begin()
            self.finish()
            self.assertEqual(render_take.call_count, 4)
        self.assertNotEqual(first_manifest, self.session.manifest_path)
        self.assertEqual(first_manifest.read_bytes(), first_data)

    def test_failure_or_cancellation_never_advances(self):
        for status in ("error", "cancelled"):
            with patch.object(self.renderer, "render_sequence", return_value=RenderResult(status, "test outcome")) as renderer:
                self.session.begin()
                self.finish()
                self.assertEqual(renderer.call_count, 1)
                self.assertEqual(collection.output_number(self.collection.model), 1)
                self.assertEqual(json.loads(self.session.manifest_path.read_text())["status"], status)

    def test_stop_request_is_forwarded_to_current_render(self):
        def render_take(**kwargs):
            self.session.stop()
            self.assertTrue(kwargs["cancel_requested"]())
            return RenderResult("cancelled")
        with patch.object(self.renderer, "render_sequence", side_effect=render_take) as renderer:
            self.session.begin()
            self.finish()
        self.assertEqual(renderer.call_count, 1)
        self.assertIsNone(self.scene._foreach_active_session)

    def test_zero_checked_paths_does_not_create_output(self):
        collection.mocap._set_param_value(self.start.model, "mode", "checked")
        rows = collection.entries(self.collection.model)
        for row in rows:
            row["checked"] = False
        collection.save_state(self.collection.model, rows, 1, self.scene)
        with self.assertRaisesRegex(ValueError, "No eligible"):
            self.session.begin()
        self.assertFalse((self.root / "renders").exists())

    def test_parameter_range_uses_same_take_numbers(self):
        driver = self.scene.add_node(GraphNode("Driver", params=[{"name": "value", "value": "0"}]), QtCore.QPointF(-500, -500))
        self.scene._edges = [edge for edge in self.scene._edges if edge.dst is not self.start]
        self.scene.connect(driver, self.start, "source")
        self.scene.connect(driver, self.scene_item, "")
        for key, raw in (("mode", "parameter"), ("parameter", "value"), ("count", "3"), ("first", "2"), ("step", "0.5")):
            collection.mocap._set_param_value(self.start.model, key, raw)
        values = []
        def render_take(**kwargs):
            values.append(runtime.value(driver.model, "value"))
            return RenderResult("completed")
        with patch.object(self.renderer, "render_sequence", side_effect=render_take):
            self.session.begin()
            self.finish()
        self.assertEqual(values, ["2", "2.5", "3"])
        self.assertEqual([Path(record["take_folder"]).name for record in self.session.records], ["Take_001", "Take_002", "Take_003"])

    def test_node_borders_contain_all_controls(self):
        for item in (self.start, self.end, self.render):
            for proxy in item._plugin_proxies + item._param_proxies:
                self.assertLessEqual(proxy.pos().y() + proxy.size().height() + 5, item.height)
                self.assertGreaterEqual(proxy.size().height(), proxy.widget().minimumSizeHint().height())

    def test_real_render_completion_writes_one_sequence_per_animation(self):
        window = QtWidgets.QMainWindow()
        window.open_scene_assets = Mock(return_value=True)
        current = {"frame": 0}
        gl = SimpleNamespace(grabFramebuffer=Mock(), _timeline_current_frame=lambda: current["frame"],
            _timeline_set_frame_widgets=lambda frame: current.update(frame=frame),
            _timeline_apply_frame_if_keyed=Mock(), _timeline_apply_other_owner_frames=Mock(), update=Mock())
        window.gl_view = gl
        self.renderer._end_spin.setValue(1)
        image = QtGui.QImage(16, 16, QtGui.QImage.Format_RGBA8888)
        image.fill(QtGui.QColor("#457fa3"))
        captured = []
        def capture(*args):
            captured.append((collection.output_number(self.collection.model), current["frame"]))
            return image
        spy = QtTest.QSignalSpy(self.renderer.renderFinished)
        with patch.object(render, "_dialog_parent", return_value=window), \
             patch.object(self.renderer, "_grab_frame_supersampled", side_effect=capture), \
             patch.object(self.renderer, "_image_is_invalid_capture", return_value=False), \
             patch.object(self.renderer, "_process_ui_events"), \
             patch.object(QtWidgets.QMessageBox, "exec", side_effect=AssertionError("Batch must not open completion popups")):
            self.session.begin()
            self.finish()
        self.assertEqual(captured, [(1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1)])
        self.assertEqual(spy.count(), 3)
        self.assertEqual(window.open_scene_assets.call_count, 3)
        for record in self.session.records:
            self.assertEqual(record["status"], "completed")
            self.assertEqual(record["written_frames"], 2)
            folder = Path(record["take_folder"])
            self.assertEqual(len(list(folder.glob("*.png"))), 2)
        self.assertEqual(runtime.value(self.render.model, "output"), str(self.root / "renders" / "frame.png"))
        window.close()

    def test_scene_load_failure_stops_before_any_capture(self):
        window = QtWidgets.QMainWindow()
        window.gl_view = SimpleNamespace(grabFramebuffer=Mock())
        window.open_scene_assets = Mock(return_value=False)
        with patch.object(render, "_dialog_parent", return_value=window), \
             patch.object(self.renderer, "_grab_frame_supersampled") as capture:
            self.session.begin()
            self.finish()
            capture.assert_not_called()
        self.assertEqual(len(self.session.records), 1)
        self.assertEqual(self.session.records[0]["status"], "error")
        self.assertIn("update the render scene", self.session.records[0]["message"])
        window.close()

    def test_invalid_retarget_input_stops_before_render(self):
        with patch.object(runtime, "validate_retarget_inputs", side_effect=ValueError("Animation rig differs from source")), \
             patch.object(self.renderer, "render_sequence") as capture:
            self.session.begin()
            self.finish()
            capture.assert_not_called()
        self.assertIn("rig differs", self.session.status)
        self.assertFalse(self.session.records)

    def test_exception_marks_current_take_failed_and_releases_loop(self):
        with patch.object(self.renderer, "render_sequence", side_effect=RuntimeError("capture broke")):
            self.session.begin()
            self.finish()
        self.assertIsNone(self.scene._foreach_active_session)
        manifest = json.loads(self.session.manifest_path.read_text())
        self.assertEqual(manifest["status"], "error")
        self.assertEqual(manifest["takes"][0]["status"], "error")
        self.assertEqual(manifest["takes"][0]["message"], "capture broke")


class RenderTakeTests(unittest.TestCase):
    def test_take_folders_are_unique_and_keep_parent_folder_label(self):
        with tempfile.TemporaryDirectory() as folder:
            first = reserve_take_folder(Path(folder), 1, str(Path(folder) / "walk forward" / "preview.bvh"))
            second = reserve_take_folder(Path(folder), 1, str(Path(folder) / "walk forward" / "preview.bvh"))
            self.assertEqual(first.name, "Take_001_walk forward")
            self.assertNotEqual(first, second)

    def test_auto_range_includes_animation_duration(self):
        asset = {"fbx_rig_context": {"clip": SimpleNamespace(start_time=0, end_time=2.5)}}
        self.assertEqual(render._animation_max_frame_from_assets([asset], 30), 75)


if __name__ == "__main__":
    unittest.main()
