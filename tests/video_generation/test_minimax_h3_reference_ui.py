import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.persistence import _node_to_dict
from echograph.ui.node_item import NodeItem
from nodes.minimax_h3_reference_video.client import Result
from nodes.minimax_h3_reference_video.spec import KIND, INPUTS, OUTPUTS, incoming, put, value


APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Graph(QtWidgets.QGraphicsScene):
    linksChanged = QtCore.Signal()
    paramChanged = QtCore.Signal(str, list)

    def __init__(self):
        super().__init__()
        self.edges = []

    def _ordered_in_edges(self, _):
        return self.edges

    def resolve_text_value(self, source):
        return value(source.model, "output")


class ReferenceVideoUITests(unittest.TestCase):
    def make_item(self, model=None):
        scene = Graph()
        item = NodeItem(model or GraphNode("Previs H3", kind=KIND))
        scene.addItem(item)
        APP.processEvents()
        self.addCleanup(scene.clear)
        return scene, item, item._plugin_proxies[0].widget()

    def test_complete_pins_render_above_background_after_raise_and_rebuild(self):
        scene, item, _ = self.make_item()
        with patch.object(item, "_wired_named_inputs", return_value={"reference_videos"}):
            for stage in ("initial", "raised", "rebuilt"):
                if stage == "raised":
                    item._bring_to_front()
                elif stage == "rebuilt":
                    item._build_widgets()
                item._plugin_proxies[0].widget().setStyleSheet("QWidget { background: #343a43; }")
                APP.processEvents()
                source = item.sceneBoundingRect().adjusted(-6, -6, 6, 6)
                image = QtGui.QImage(int(source.width() * 3), int(source.height() * 3), QtGui.QImage.Format_ARGB32)
                image.fill(QtGui.QColor("#05070b"))
                painter = QtGui.QPainter(image)
                scene.render(painter, QtCore.QRectF(image.rect()), source, QtCore.Qt.IgnoreAspectRatio)
                painter.end()
                for side, entries in (("in", item._input_port_pos), ("out", item._output_port_pos)):
                    for name, (position, _) in entries.items():
                        expected = "#facc15" if side == "in" and name == "reference_videos" else "#cbd5e1"
                        for offset in (-2, 2):
                            point = item.mapToScene(position + QtCore.QPointF(offset, 0))
                            x = round((point.x() - source.x()) * image.width() / source.width())
                            y = round((point.y() - source.y()) * image.height() / source.height())
                            self.assertEqual(image.pixelColor(x, y).name(), expected, (stage, side, name, offset))

    def test_inner_half_of_output_pin_starts_wire_drag(self):
        scene, item, _ = self.make_item()
        view = QtWidgets.QGraphicsView(scene)
        view.resize(760, 1000)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.setSceneRect(item.sceneBoundingRect().adjusted(-20, -20, 20, 20))
        view.show()
        APP.processEvents()
        spy = QtTest.QSignalSpy(item.startWireDrag)
        point = item.port_anchor("mp4_path", "out") + QtCore.QPointF(-2, 0)
        QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.LeftButton, pos=view.mapFromScene(point))
        self.assertEqual(spy.count(), 1)
        self.assertEqual(item._wire_drag_src_port_name, "mp4_path")

    def test_border_contains_controls_and_ports_after_rebuild_model_change_and_reload(self):
        scene, item, widget = self.make_item()
        self.assertEqual(item.input_port_names(), list(INPUTS))
        self.assertEqual(item.output_port_names(), list(OUTPUTS))
        for model in ("MiniMax-H3-Max", "MiniMax-H3"):
            widget.model_combo.setCurrentText(model)
            item._recompute_height()
            item._build_widgets()
            APP.processEvents()
            widget = item._plugin_proxies[0].widget()
            for proxy in item._plugin_proxies:
                self.assertGreaterEqual(item.height, proxy.pos().y() + proxy.size().height() + 13.9)
                self.assertGreaterEqual(proxy.size().height(), proxy.widget().minimumSizeHint().height())
                self.assertLessEqual(proxy.size().width(), item.width)
                bottom = widget.task.mapTo(widget, QtCore.QPoint(0, widget.task.height())).y()
                self.assertLess(bottom, proxy.size().height())
            for port in INPUTS:
                self.assertEqual(item.input_port_hit(item._input_port_pos[port][0]), port)
            for port in OUTPUTS:
                self.assertEqual(item.output_port_hit(item._output_port_pos[port][0]), port)
        saved = _node_to_dict(item.model)
        loaded_model = GraphNode("Reloaded", kind=saved["kind"], params=saved["params"])
        _, loaded, loaded_widget = self.make_item(loaded_model)
        self.assertEqual(loaded.input_port_names(), list(INPUTS))
        self.assertEqual(loaded_widget.model_combo.currentText(), "MiniMax-H3")

    def test_connected_video_and_collection_override_without_changing_local_fields(self):
        scene, item, widget = self.make_item()
        widget.prompt.setPlainText("Local prompt")
        widget.fields["reference_videos"].setPlainText("local.mp4")
        video = SimpleNamespace(model=GraphNode("Video", kind="sequence_to_mp4", params=[{"name": "mp4_path", "value": "C:/render/previs.mp4"}]))
        images = GraphNode("Designs", kind="image_collection")
        images._image_collection_state = {"paths": ["character.png", "environment.png"]}
        scene.edges = [SimpleNamespace(src=video, dst_port_name="reference_videos", src_port_name="mp4_path"),
                       SimpleNamespace(src=SimpleNamespace(model=images), dst_port_name="reference_images")]
        scene.linksChanged.emit()
        self.assertFalse(widget.fields["reference_videos"].isEnabled())
        self.assertEqual(widget.fields["reference_videos"].toPlainText(), "local.mp4")
        settings = widget._settings(False)
        self.assertEqual(settings.videos, ["C:/render/previs.mp4"])
        self.assertEqual(settings.images, ["character.png", "environment.png"])
        scene.edges = []
        scene.linksChanged.emit()
        self.assertTrue(widget.fields["reference_videos"].isEnabled())
        self.assertEqual(widget.fields["reference_videos"].toPlainText(), "local.mp4")

    def test_empty_connected_input_does_not_use_local_fallback(self):
        scene, item, widget = self.make_item()
        widget.fields["reference_videos"].setPlainText("local.mp4")
        scene.edges = [SimpleNamespace(src=SimpleNamespace(model=GraphNode("Empty", kind="sequence_to_mp4")),
                                       dst_port_name="reference_videos")]
        with self.assertRaisesRegex(ValueError, "no output"):
            widget._settings(False)

    def test_design_images_merge_all_connected_collections_in_order_and_update_on_disconnect(self):
        scene, item, widget = self.make_item()
        widget.prompt.setPlainText("Use the character sheets and environment designs.")
        first = GraphNode("Characters", kind="image_collection")
        first._image_collection_state = {"paths": ["character_front.png", "character_back.png"]}
        second = GraphNode("Environments", kind="image_collection")
        second._image_collection_state = {"paths": ["environment.png", "character_front.png", "lighting.png"]}
        scene.edges = [SimpleNamespace(src=SimpleNamespace(model=model), dst_port_name="reference_images")
                       for model in (first, second)]
        scene.linksChanged.emit()
        expected = ["character_front.png", "character_back.png", "environment.png", "lighting.png"]
        self.assertEqual(widget._settings(False).images, expected)
        self.assertFalse(widget.fields["reference_images"].isEnabled())
        first._image_collection_state["paths"].append("costume.png")
        self.assertEqual(widget._settings(False).images,
                         ["character_front.png", "character_back.png", "costume.png", "environment.png", "lighting.png"])
        scene.edges.pop(0)
        scene.linksChanged.emit()
        self.assertEqual(widget._settings(False).images, ["environment.png", "character_front.png", "lighting.png"])

    def test_design_image_limit_is_combined_across_collections_and_empty_collection_adds_no_params(self):
        from nodes.minimax_h3_reference_video.client import build_payload
        scene, item, widget = self.make_item()
        widget.prompt.setPlainText("Use these references.")
        first = GraphNode("Characters", kind="image_collection")
        first._image_collection_state = {"paths": [f"mm_file://image{i}" for i in range(5)]}
        second = GraphNode("Sets", kind="image_collection")
        second._image_collection_state = {"paths": [f"mm_file://image{i}" for i in range(5, 9)]}
        empty = GraphNode("Empty", kind="image_collection", params=[{"name": "unrelated", "value": "not an image path"}])
        empty._image_collection_state = {"paths": []}
        scene.edges = [SimpleNamespace(src=SimpleNamespace(model=model), dst_port_name="reference_images")
                       for model in (first, second, empty)]
        settings = widget._settings(False)
        self.assertEqual(len(settings.images), 9)
        self.assertEqual(len(build_payload(settings)["content"]), 10)
        second._image_collection_state["paths"].append("mm_file://image9")
        with self.assertRaisesRegex(ValueError, "at most 9"):
            build_payload(widget._settings(False))

    def test_connected_image_data_uri_preserves_the_entire_locator(self):
        scene, item, _ = self.make_item()
        uri = "data:image/png;base64,AAAA"
        source = GraphNode("Image", kind="image", params=[{"name": "output", "value": uri}])
        scene.edges = [SimpleNamespace(src=SimpleNamespace(model=source), dst_port_name="reference_images")]
        self.assertEqual(incoming(item, "reference_images"), (True, uri))

    def test_key_is_session_only_and_task_survives_workflow_serialization(self):
        _, item, widget = self.make_item()
        widget.api_key.setText("session-test-key")
        widget.session.output_dir = Path("C:/renders")
        widget.session._submitted("saved-task")
        saved = json.dumps(_node_to_dict(item.model))
        self.assertNotIn("session-test-key", saved)
        self.assertIn("saved-task", saved)
        self.assertEqual(Path(value(item.model, "task_output_dir")), Path("C:/renders"))
        item._build_widgets()
        APP.processEvents()
        controls = item._plugin_proxies[0].widget()
        self.assertEqual(controls.api_key.text(), "session-test-key")
        self.assertTrue(controls.resume.isEnabled())

    def test_max_model_limits_and_saved_output_directory_on_resume(self):
        _, item, widget = self.make_item()
        widget.resolution.setCurrentText("2K")
        widget.duration.setValue(4)
        widget.model_combo.setCurrentText("MiniMax-H3-Max")
        self.assertEqual(widget.resolution.currentText(), "768P")
        self.assertEqual(widget.duration.value(), 5)
        self.assertTrue(widget.expansion.isEnabled())
        put(item.model, "task_output_dir", "C:/previous-output")
        widget.output_dir.setText("C:/new-output")
        self.assertEqual(widget._settings(True).output_dir, Path("C:/previous-output"))

    def test_worker_is_async_and_retained_across_widget_rebuild(self):
        _, item, widget = self.make_item()
        release = threading.Event()
        entered = threading.Event()
        ui_thread = threading.get_ident()
        worker_threads = []

        def job(settings, submitted, **kwargs):
            worker_threads.append(threading.get_ident())
            submitted("active-task")
            entered.set()
            release.wait(3)
            return Result(True, "active-task", "succeeded", "C:/renders/output.mp4")

        with patch("nodes.minimax_h3_reference_video.spec.run_job", side_effect=job):
            widget._start(False)
            self.assertTrue(entered.wait(1))
            APP.processEvents()
            self.assertTrue(widget.session.busy)
            session = widget.session
            item._build_widgets()
            APP.processEvents()
            widget = item._plugin_proxies[0].widget()
            self.assertIs(widget.session, session)
            self.assertFalse(widget.generate.isEnabled())
            release.set()
            deadline = time.monotonic() + 3
            while session.busy and time.monotonic() < deadline:
                APP.processEvents()
                time.sleep(0.01)
            self.assertFalse(session.busy)
            self.assertEqual(worker_threads and worker_threads[0] != ui_thread, True)
            self.assertEqual(value(item.model, "task_id"), "active-task")
            self.assertEqual(widget.output.text(), "C:/renders/output.mp4")

    def test_legacy_frame_node_remains_registered_with_original_sockets(self):
        from nodes.minimax_h3_api_video.spec import build_ports
        inputs = []
        item = SimpleNamespace(model=GraphNode("Old H3", kind="minimax_h3_api_video"),
                               ensure_input=inputs.append, ensure_output=lambda _: None)
        build_ports(item)
        self.assertIn("first_image", inputs)
        self.assertIn("last_image", inputs)
        self.assertEqual(value(item.model, "__minimax_h3_api_mode"), "image")


if __name__ == "__main__":
    unittest.main()
