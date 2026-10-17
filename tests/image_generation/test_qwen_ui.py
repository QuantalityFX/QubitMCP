import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import PropertyMock, patch

from PIL import Image
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.image_generation.backend import ImageResult
from echograph.image_generation.qt_job import ImageTask
from echograph.model import GraphNode
from echograph.persistence import _node_to_dict
from echograph.ui.node_item import NodeItem
from nodes.qwen_image.controller import incoming, output_directory_for, put, session_for, value

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Graph(QtWidgets.QGraphicsScene):
    linksChanged = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.edges = []

    def _ordered_in_edges(self, item):
        return self.edges


class QwenUITests(unittest.TestCase):
    def test_connected_prompt_fields_disable_independently_and_restore_local_text(self):
        graph, item, controls = self.make_item()
        controls.backend.setCurrentIndex(controls.backend.findData("native_diffusers"))
        controls.prompt.setPlainText("Local positive")
        controls.negative_prompt.setText("Local negative")
        for ports in (("prompt",), ("negative_prompt",), ("prompt", "negative_prompt"), ()):
            graph.edges = [SimpleNamespace(dst_port_name=port) for port in ports]
            graph.linksChanged.emit()
            self.assertEqual(controls.prompt.isEnabled(), "prompt" not in ports)
            self.assertEqual(controls.negative_prompt.isEnabled(), "negative_prompt" not in ports)
            self.assertEqual(controls.prompt.toPlainText(), "Local positive")
            self.assertEqual(controls.negative_prompt.text(), "Local negative")

    def make_item(self, model=None):
        graph = Graph()
        with patch("nodes.qwen_image.setup_ui.schedule_setup_offer"):
            item = NodeItem(model or GraphNode("Qwen", kind="qwen_image"))
        graph.addItem(item)
        APP.processEvents()
        self.addCleanup(graph.clear)
        return graph, item, item._plugin_proxies[0].widget()

    def test_real_node_border_contains_controls_after_rebuild_and_mode_change(self):
        graph, item, controls = self.make_item()
        self.assertEqual(item.input_port_names(), ["prompt", "negative_prompt", "image", "reference_image"])
        self.assertIn("image_path", item.output_port_names())
        for mode in (0, 1):
            controls.mode.setCurrentIndex(mode)
            item._recompute_height()
            for proxy in item._plugin_proxies:
                self.assertGreaterEqual(item.height, proxy.pos().y() + proxy.size().height() + 10)
            item._build_widgets()
            APP.processEvents()
            controls = item._plugin_proxies[0].widget()
            for proxy in item._plugin_proxies:
                self.assertGreaterEqual(item.height, proxy.pos().y() + proxy.size().height() + 10)
                self.assertGreaterEqual(proxy.size().height(), proxy.widget().minimumSizeHint().height())
                self.assertLessEqual(proxy.size().width(), item.width)
            for name in item.input_port_names():
                self.assertIn(name, item._input_port_pos)
                point = item._input_port_pos[name][0]
                self.assertEqual(item.input_port_hit(point), name)
            for name in item.output_port_names():
                self.assertIn(name, item._output_port_pos)
                point = item._output_port_pos[name][0]
                self.assertEqual(item.output_port_hit(point), name)

    def test_full_pin_circles_render_above_the_parameter_background_after_raise_and_rebuild(self):
        from echograph.ui import node_icons
        from echograph.ui.dialogs import _kind_icon
        graph, item, controls = self.make_item()
        self.assertFalse(_kind_icon("qwen_image").isNull())
        supplied_icon = QtGui.QImage(str(Path(__file__).resolve().parents[2] / "icons/ImageGen_Icon.png"))
        displayed_icon = node_icons._qwen_image_icon().toImage()
        self.assertEqual(displayed_icon, supplied_icon.convertToFormat(displayed_icon.format()))
        with patch.object(item, "_wired_named_inputs", return_value={"image"}):
            for stage in ("initial", "raised", "rebuilt"):
                if stage == "raised":
                    item._bring_to_front()
                elif stage == "rebuilt":
                    item._build_widgets()
                controls = item._plugin_proxies[0].widget()
                controls.setStyleSheet("QWidget { background: #343a43; }")
                APP.processEvents()
                source = item.sceneBoundingRect().adjusted(-5, -5, 5, 5)
                scale = 3
                image = QtGui.QImage(int(source.width() * scale), int(source.height() * scale),
                                     QtGui.QImage.Format_ARGB32)
                image.fill(QtGui.QColor("#05070b"))
                painter = QtGui.QPainter(image)
                graph.render(painter, QtCore.QRectF(image.rect()), source, QtCore.Qt.IgnoreAspectRatio)
                painter.end()
                for side, entries in (("in", item._input_port_pos), ("out", item._output_port_pos)):
                    for name, (position, _) in entries.items():
                        expected = "#facc15" if side == "in" and name == "image" else "#cbd5e1"
                        for offset in (-2, 2):
                            point = item.mapToScene(position + QtCore.QPointF(offset, 0))
                            x = round((point.x() - source.x()) * image.width() / source.width())
                            y = round((point.y() - source.y()) * image.height() / source.height())
                            self.assertEqual(image.pixelColor(x, y).name(), expected, (stage, side, name, offset))

    def test_inner_half_of_output_pin_starts_wire_drag_above_embedded_widget(self):
        graph, item, controls = self.make_item()
        view = QtWidgets.QGraphicsView(graph)
        view.resize(720, 1000)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.setSceneRect(item.sceneBoundingRect().adjusted(-20, -20, 20, 20))
        view.show()
        APP.processEvents()
        spy = QtTest.QSignalSpy(item.startWireDrag)
        point = item.port_anchor("image", "out") + QtCore.QPointF(-2, 0)
        QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.LeftButton, pos=view.mapFromScene(point))
        self.assertEqual(spy.count(), 1)
        self.assertEqual(item._wire_drag_src_port_name, "image")

    def test_result_preview_seed_and_paths_survive_serialization(self):
        with tempfile.TemporaryDirectory() as folder:
            graph, item, controls = self.make_item()
            path = Path(folder) / "image.png"
            image = Image.new("RGBA", (48, 32), (10, 50, 90, 64))
            image.save(path)
            session_for(item)._result(ImageResult(image, path, 123, "my-job", {"seed_used": 123}))
            self.assertFalse(controls.preview.pixmap.isNull())
            self.assertTrue(controls.preview.pixmap.hasAlphaChannel())
            saved = _node_to_dict(item.model)
            _, loaded, loaded_controls = self.make_item(GraphNode("Loaded", kind="qwen_image", params=saved["params"]))
            self.assertEqual(value(loaded.model, "seed_used"), "123")
            self.assertEqual(value(loaded.model, "job_id"), "my-job")
            self.assertFalse(loaded_controls.preview.pixmap.isNull())
            self.assertEqual(value(loaded.model, "image_path"), str(path))

    def test_connected_in_memory_image_and_collection_path_are_accepted(self):
        graph, item, controls = self.make_item()
        source = QtWidgets.QGraphicsRectItem()
        source.model = GraphNode("Source", params=[])
        source.model._qwen_outputs = {"image": Image.new("RGBA", (16, 16))}
        graph.addItem(source)
        graph.edges = [SimpleNamespace(src=source, dst_port_name="image", src_port_name="image")]
        self.assertIsInstance(incoming(item, "image"), Image.Image)
        del source.model._qwen_outputs
        source.model._image_collection_state = {"paths": ["one.png", "two.png"]}
        self.assertEqual(incoming(item, "image"), "one.png")

    def test_note_prompt_uses_live_checked_values_and_single_field_needs_no_check(self):
        graph, item, controls = self.make_item()
        source = NodeItem(GraphNode("Prompt Note", kind="note", params=[
            {"name": "subject", "value": "a red cube"},
            {"name": "lighting", "value": "soft light"}]))
        graph.addItem(source)
        graph.edges = [SimpleNamespace(src=source, dst_port_name="prompt")]
        put(item.model, prompt="local text must not leak")
        self.assertEqual(incoming(item, "prompt"), "")
        with self.assertRaisesRegex(ValueError, "Connected positive prompt is empty"):
            controls.session.request()
        checkboxes = [button for proxy in source._param_proxies
                      for button in proxy.widget().findChildren(QtWidgets.QToolButton)
                      if button.toolTip() == "Mark this note item complete"]
        checkboxes[1].setChecked(True)
        self.assertEqual(controls.session.request().prompt, "soft light")
        checkboxes[0].setChecked(True)
        self.assertEqual(controls.session.request().prompt, "a red cube\n\nsoft light")
        checkboxes[1].setChecked(False)
        self.assertEqual(controls.session.request().prompt, "a red cube")
        saved = _node_to_dict(source.model)
        self.assertEqual(saved["completed_params"], ["subject"])
        source.model.params = [{"name": "only field", "value": "the only note"},
                               {"name": "__ui_hidden_params", "value": ""}]
        source.model._completed_params = set()
        self.assertEqual(controls.session.request().prompt, "the only note")
        source.model.params.insert(1, {"name": "second field", "value": ""})
        self.assertEqual(incoming(item, "prompt"), "")

    def test_note_selection_is_kept_through_append_prompt_segments(self):
        graph, item, controls = self.make_item()
        note = GraphNode("Multi Note", kind="note", params=[
            {"name": "keep", "value": "selected note"}, {"name": "skip", "value": "unchecked note"}])
        note._completed_params = {"keep"}
        single = GraphNode("Single Note", kind="note", params=[{"name": "only", "value": "automatic note"}])
        append = QtWidgets.QGraphicsRectItem()
        append.model = GraphNode("Append", kind="append")
        graph.addItem(append)
        graph.edges = [SimpleNamespace(src=append, dst_port_name="prompt")]
        segments = [{"node": "Append", "param": "header", "text": "prefix"},
                    {"node": note.name, "param": "keep", "text": "selected note"},
                    {"node": note.name, "param": "skip", "text": "unchecked note"},
                    {"node": single.name, "param": "only", "text": "automatic note"}]
        with patch.object(graph, "_nodes_by_name", {note.name: note, single.name: single}, create=True), \
                patch.object(graph, "_flatten_text_segments", return_value=segments, create=True):
            self.assertEqual(controls.session.request().prompt, "prefix\n\nselected note\n\nautomatic note")

    def test_negative_prompt_connection_overrides_local_text_and_filters_notes(self):
        graph, item, controls = self.make_item()
        controls.backend.setCurrentIndex(controls.backend.findData("native_diffusers"))
        controls.cfg.setValue(2.0)
        put(item.model, prompt="a red cube", negative_prompt="local negative must not leak")
        source = QtWidgets.QGraphicsRectItem()
        source.model = GraphNode("Negative Note", kind="note", params=[
            {"name": "avoid", "value": "blurry"}, {"name": "unused", "value": "unchecked text"}])
        source.model._completed_params = {"avoid"}
        graph.addItem(source)
        graph.edges = [SimpleNamespace(src=source, dst_port_name="negative_prompt")]
        request = controls.session.request()
        self.assertEqual(request.prompt, "a red cube")
        self.assertEqual(request.negative_prompt, "blurry")
        source.model._completed_params = set()
        self.assertEqual(controls.session.request(validate=False).negative_prompt, "")
        with self.assertRaisesRegex(ValueError, "requires a negative prompt"):
            controls.session.request()
        source.model.params = [{"name": "only", "value": "distorted hands"}]
        self.assertEqual(controls.session.request().negative_prompt, "distorted hands")
        graph.edges = []
        self.assertEqual(controls.session.request().negative_prompt, "local negative must not leak")

    def test_negative_prompt_connection_reports_unsupported_comfy_backend(self):
        graph, item, controls = self.make_item()
        source = QtWidgets.QGraphicsRectItem()
        source.model = GraphNode("Negative", kind="note", params=[{"name": "only", "value": "blurry"}])
        graph.addItem(source)
        graph.edges = [SimpleNamespace(src=source, dst_port_name="negative_prompt")]
        with self.assertRaisesRegex(ValueError, "require the native Diffusers backend"):
            controls.session.request()

    def test_image_browse_pickers_survive_real_modal_dialog_node_rebuild(self):
        graph, item, controls = self.make_item()
        controls.mode.setCurrentIndex(controls.mode.findData("image_edit"))
        window = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(window)
        view = QtWidgets.QGraphicsView(graph)
        layout.addWidget(view)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.show()
        APP.processEvents()
        with tempfile.TemporaryDirectory() as directory:
            for key in ("image", "reference_image"):
                chosen = Path(directory) / (key + " selected.png")
                Image.new("RGB", (16, 16)).save(chosen)
                def choose(parent, caption, initial, filters):
                    from shiboken6 import isValid
                    self.assertIs(parent, window)
                    dialog = QtWidgets.QFileDialog(parent, caption, directory, filters)
                    self.addCleanup(dialog.deleteLater)
                    dialog.setOption(QtWidgets.QFileDialog.DontUseNativeDialog)
                    dialog.setFileMode(QtWidgets.QFileDialog.ExistingFile)
                    stayed_open = []
                    def rebuild():
                        item._build_widgets()
                        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
                        stayed_open.append(isValid(dialog) and dialog.isVisible())
                        if isValid(dialog):
                            dialog.selectFile(str(chosen))
                            dialog.accept()
                    guard = QtCore.QTimer(dialog)
                    guard.setSingleShot(True)
                    guard.timeout.connect(dialog.reject)
                    guard.start(2000)
                    QtCore.QTimer.singleShot(0, rebuild)
                    self.assertEqual(dialog.exec(), QtWidgets.QDialog.Accepted)
                    self.assertEqual(stayed_open, [True])
                    return dialog.selectedFiles()[0], filters
                controls = item._plugin_proxies[0].widget()
                with patch.object(QtWidgets.QFileDialog, "getOpenFileName", side_effect=choose) as picker:
                    controls._image_browse_buttons[key].click()
                    QtTest.QTest.qWait(10)
                    picker.assert_called_once()
                self.assertEqual(Path(value(item.model, key)), chosen)
                live = item._plugin_proxies[0].widget()
                self.assertEqual(Path((live.image if key == "image" else live.reference).text()), chosen)

    def test_image_picker_cancel_and_node_removal_do_not_replace_paths(self):
        graph, item, controls = self.make_item()
        put(item.model, image="keep.png", reference_image="reference.png")
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=("", "")):
            controls.session.choose_image("image", "Input image")
        self.assertEqual(value(item.model, "image"), "keep.png")
        def deleted(parent, caption, initial, filters):
            controls.session.choose_image("reference_image", "Duplicate picker")
            graph.removeItem(item)
            return "discard.png", filters
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", side_effect=deleted) as picker:
            controls.session.choose_image("image", "Input image")
            picker.assert_called_once()
        self.assertEqual(value(item.model, "image"), "keep.png")
        self.assertFalse(controls.session._image_picker_active)

    def test_scene_snapshot_connection_uses_selected_png(self):
        graph, item, controls = self.make_item()
        source = QtWidgets.QGraphicsRectItem()
        source.model = GraphNode("Scene", kind="scene", params=[
            {"name": "thumbnail", "value": "C:/project/snapshots/first.png"},
            {"name": "thumbnail_choice", "value": "selected.png"}])
        graph.addItem(source)
        graph.edges = [SimpleNamespace(src=source, dst_port_name="image")]
        self.assertEqual(incoming(item, "image"), Path("C:/project/snapshots/selected.png"))

    def test_native_edit_keeps_input_and_reference_pins_distinct(self):
        graph, item, controls = self.make_item()
        controls.backend.setCurrentIndex(controls.backend.findData("native_diffusers"))
        controls.mode.setCurrentIndex(controls.mode.findData("image_edit"))
        put(item.model, prompt="Use the second image for clothing; keep the first image's pose.")
        base = QtWidgets.QGraphicsRectItem()
        base.model = GraphNode("Base render")
        base.model._qwen_outputs = {"image": Image.new("RGB", (160, 90), "red")}
        reference = QtWidgets.QGraphicsRectItem()
        reference.model = GraphNode("Outfit reference")
        reference.model._qwen_outputs = {"image": Image.new("RGB", (90, 160), "blue")}
        graph.addItem(base)
        graph.addItem(reference)
        graph.edges = [SimpleNamespace(src=reference, dst_port_name="reference_image", src_port_name="image"),
                       SimpleNamespace(src=base, dst_port_name="image", src_port_name="image")]
        request = controls.session.request()
        self.assertEqual(request.image.size, (160, 90))
        self.assertEqual(request.reference_image.size, (90, 160))
        self.assertEqual(request.image.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(request.reference_image.getpixel((0, 0)), (0, 0, 255))

    def test_deleted_node_ignores_late_result(self):
        graph, item, controls = self.make_item()
        session = session_for(item)
        graph.removeItem(item)
        session._result(ImageResult(Image.new("RGB", (1, 1)), Path("late.png"), 1, "late", {}))
        self.assertEqual(value(item.model, "image_path"), "")

    def test_heavy_job_runs_off_gui_thread_and_delivers_signal_on_gui_thread(self):
        worker_threads = []
        receiver_threads = []
        class Receiver(QtCore.QObject):
            @QtCore.Slot(object)
            def receive(self, value):
                receiver_threads.append(threading.get_ident())
        receiver = Receiver()
        task = ImageTask(lambda cancel, progress: worker_threads.append(threading.get_ident()))
        task.result.connect(receiver.receive)
        spy = QtTest.QSignalSpy(task.finished)
        task.start()
        deadline = time.monotonic() + 5
        # Short Qt waits release the GIL for a Python QThread.run implementation.
        while not spy.count() and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertEqual(spy.count(), 1)
        APP.processEvents()
        self.assertNotEqual(worker_threads, [threading.get_ident()])
        self.assertEqual(receiver_threads, [threading.get_ident()])

    def test_completed_worker_does_not_leave_shared_runtime_controls_blocked_before_queued_cleanup(self):
        from echograph.image_generation.runtime import RuntimeManager
        manager = RuntimeManager()
        self.addCleanup(manager.deleteLater)
        task = ImageTask(lambda cancel, progress: None)
        manager.track_job(task)
        task.start()
        self.assertTrue(task.wait(3000))
        # The GUI has not delivered the queued finished callback yet.
        self.assertFalse(manager.generating)
        APP.processEvents()

    def test_setup_acceptance_is_not_automatic_and_commercial_selection_resets_it(self):
        from nodes.qwen_image.setup_ui import SetupDialog
        graph, item, controls = self.make_item()
        dialog = SetupDialog(item)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.accept.isChecked())
        self.assertFalse(dialog.install.isEnabled())
        dialog.accept.setChecked(True)
        self.assertEqual(value(item.model, "license_accepted"), "true")
        dialog.license_use.setCurrentIndex(1)
        self.assertFalse(dialog.accept.isChecked())
        self.assertEqual(value(item.model, "license_accepted"), "false")

    def test_setup_retry_replaces_old_failure_status_on_dialog_and_node(self):
        from nodes.qwen_image.setup_ui import SetupDialog
        for backend in ("comfyui", "native_diffusers"):
            with self.subTest(backend=backend):
                graph, item, controls = self.make_item()
                if backend == "native_diffusers":
                    controls.backend.setCurrentIndex(controls.backend.findData(backend))
                dialog = SetupDialog(item)
                self.addCleanup(dialog.deleteLater)
                self.addCleanup(dialog.close)
                controls.session.message = "Setup failed. Read the earlier log."
                controls.session.changed.emit()
                self.assertIn("failed", dialog.status.text())
                self.assertIn("failed", controls.status.text())
                with patch.object(type(controls.manager), "installing", new_callable=PropertyMock, return_value=True):
                    controls.manager.changed.emit()
                    self.assertIn("Installing", dialog.status.text())
                    self.assertIn("Installing", controls.status.text())
                    self.assertNotIn("failed", dialog.status.text())
                    self.assertNotIn("failed", controls.status.text())
                dialog._installed(True)
                self.assertIn("Setup complete", dialog.status.text())
                self.assertIn("Setup complete", controls.status.text())

    def test_license_button_opens_before_install_and_remains_clickable_during_setup(self):
        from echograph.image_generation.schema import LICENSE_URL
        from nodes.qwen_image.setup_ui import SetupDialog
        graph, item, controls = self.make_item()
        dialog = SetupDialog(item)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        with patch.object(type(dialog.manager), "installing", new_callable=PropertyMock, return_value=False) as installing, \
                patch.object(type(dialog.manager), "server_running", new_callable=PropertyMock, return_value=False), \
                patch.object(dialog.manager, "install") as install, \
                patch.object(QtGui.QDesktopServices, "openUrl", return_value=True) as open_url:
            dialog.show()
            APP.processEvents()
            dialog.resize(780, 670)
            APP.processEvents()
            for widget in (dialog.license_link, dialog.accept):
                rect = QtCore.QRect(widget.mapTo(dialog, QtCore.QPoint()), widget.size())
                self.assertTrue(dialog.rect().contains(rect))
            self.assertFalse(dialog.accept.isChecked())
            self.assertFalse(dialog.install.isEnabled())
            for busy in (False, True):
                installing.return_value = busy
                dialog.refresh()
                self.assertTrue(dialog.license_link.isEnabled())
                QtTest.QTest.mouseClick(dialog.license_link, QtCore.Qt.LeftButton,
                                       pos=dialog.license_link.rect().center())
                self.assertEqual(open_url.call_args.args[0].toString(), LICENSE_URL)
            self.assertEqual(open_url.call_count, 2)
            install.assert_not_called()
            self.assertEqual(value(item.model, "license_accepted"), "false")
            open_url.return_value = False
            dialog.license_link.click()
            self.assertIn(LICENSE_URL, dialog.status.text())

    def test_output_folder_opens_explorer_at_generation_destination_without_a_picker(self):
        graph, item, controls = self.make_item()
        with tempfile.TemporaryDirectory() as folder, \
                patch("nodes.qwen_image.controller.workflow_directory", return_value=Path(folder)), \
                patch.object(QtGui.QDesktopServices, "openUrl", return_value=True) as open_url, \
                patch.object(QtWidgets.QFileDialog, "getExistingDirectory") as picker:
            cases = (("", "", Path(folder) / "qwen_image"),
                     ("relative outputs", "", Path(folder) / "relative outputs"),
                     (str(Path(folder) / "custom outputs"), "", Path(folder) / "custom outputs"),
                     ("future outputs", str(Path(folder) / "last result/image.png"), Path(folder) / "last result"))
            for configured, last_image, expected in cases:
                with self.subTest(configured=configured, last_image=last_image):
                    put(item.model, output_directory=configured, image_path=last_image)
                    controls.output_folder.click()
                    controls.output_folder.click()
                    self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), expected)
                    self.assertTrue(expected.is_dir())
                    self.assertEqual(value(item.model, "output_directory"), configured)
            picker.assert_not_called()

    def test_output_folder_open_failure_is_reported_on_node(self):
        graph, item, controls = self.make_item()
        with tempfile.TemporaryDirectory() as folder, patch.object(QtGui.QDesktopServices, "openUrl", return_value=False):
            put(item.model, output_directory=folder)
            controls.output_folder.click()
            self.assertIn("Could not open the output folder", controls.status.text())
            self.assertIn(folder, controls.status.text())

    def test_output_destination_chooser_survives_embedded_node_rebuild(self):
        from nodes.qwen_image.setup_ui import SetupDialog
        graph, item, controls = self.make_item()
        dialog = SetupDialog(item)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        with tempfile.TemporaryDirectory() as folder:
            def choose(parent, title, initial):
                self.assertIs(parent, dialog)
                item._build_widgets()
                APP.processEvents()
                return folder
            with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", side_effect=choose):
                dialog.output_browse.click()
            self.assertEqual(value(item.model, "output_directory"), folder)
            self.assertEqual(output_directory_for(item), Path(folder))

    def test_node_stop_button_respects_jobs_from_all_nodes_and_resets_when_stopped(self):
        from nodes.qwen_image.setup_ui import SetupDialog
        graph, item, controls = self.make_item()
        graph2, item2, controls2 = self.make_item()
        dialog = SetupDialog(item)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        class ActiveJob(QtCore.QObject):
            finished = QtCore.Signal()
        job = ActiveJob()
        manager = controls.manager
        with patch.object(type(manager), "server_running", new_callable=PropertyMock, return_value=True) as running, \
                patch.object(manager, "stop_server") as stop:
            manager.track_job(job)
            try:
                self.assertEqual(controls.start.text(), "Stop Local Backend")
                self.assertFalse(controls.start.isEnabled())
                self.assertFalse(controls2.start.isEnabled())
                self.assertFalse(dialog.stop.isEnabled())
                controls.start.click()
                stop.assert_not_called()
            finally:
                job.finished.emit()
            self.assertTrue(controls.start.isEnabled())
            self.assertTrue(controls2.start.isEnabled())
            self.assertTrue(dialog.stop.isEnabled())
            controls.start.click()
            stop.assert_called_once()
            manager.stopping = True
            try:
                manager.changed.emit()
                self.assertEqual(controls.start.text(), "Stopping Backend...")
                self.assertFalse(controls.start.isEnabled())
                self.assertFalse(controls.generate.isEnabled())
            finally:
                manager.stopping = False
            running.return_value = False
            session_for(item).inventory = {"old": {}}
            manager.server_stopped.emit()
            manager.changed.emit()
            self.assertEqual(controls.start.text(), "Start Local Backend")
            self.assertIsNone(session_for(item).inventory)
            self.assertIn("backend stopped", controls.status.text())

    def test_stop_terminates_owned_process_and_does_not_kill_restarted_or_unrelated_process(self):
        from echograph.image_generation.runtime import RuntimeManager
        manager = RuntimeManager()
        unrelated = QtCore.QProcess()
        args = ["-c", "import sys; sys.stdin.read()"]
        def wait_stopped():
            deadline = time.monotonic() + 3
            while manager.server_running and time.monotonic() < deadline:
                QtTest.QTest.qWait(10)
            self.assertFalse(manager.server_running)
        try:
            unrelated.start(sys.executable, args)
            self.assertTrue(unrelated.waitForStarted(3000))
            manager.server.start(sys.executable, args)
            self.assertTrue(manager.server.waitForStarted(3000))
            with patch.object(type(manager), "generating", new_callable=PropertyMock, return_value=True):
                with self.assertRaisesRegex(RuntimeError, "active Qwen Image jobs"):
                    manager.stop_server()
                self.assertTrue(manager.server_running)
                self.assertFalse(manager.stopping)
            with patch.object(QtCore.QTimer, "singleShot") as fallback:
                manager.stop_server()
                delayed_kill = fallback.call_args.args[1]
                delayed_kill()
                wait_stopped()
                self.assertFalse(manager.stopping)
                manager.server.start(sys.executable, args)
                self.assertTrue(manager.server.waitForStarted(3000))
                delayed_kill()
                APP.processEvents()
                self.assertTrue(manager.server_running)
                manager.stop_server()
                fallback.call_args.args[1]()
                wait_stopped()
            self.assertEqual(unrelated.state(), QtCore.QProcess.Running)
            self.assertIn("releasing its memory", manager.log_text)
        finally:
            manager.shutdown()
            unrelated.kill()
            unrelated.waitForFinished(3000)
            manager.deleteLater()

    def test_native_selector_setup_and_quality_preset_keep_controls_inside_node(self):
        from nodes.qwen_image.setup_ui import SetupDialog
        graph, item, controls = self.make_item()
        controls.backend.setCurrentIndex(controls.backend.findData("native_diffusers"))
        self.assertEqual(value(item.model, "backend"), "native_diffusers")
        self.assertEqual(value(item.model, "model_variant"), "bf16")
        self.assertEqual(controls.variant.count(), 1)
        self.assertTrue(controls.negative_prompt.isEnabled())
        controls.preset.setCurrentIndex(controls.preset.findData("quality"))
        controls._preset()
        self.assertEqual((controls.width.value(), controls.height.value(), controls.steps.value()), (2048, 2048, 40))
        controls.mode.setCurrentIndex(controls.mode.findData("image_edit"))
        self.assertTrue(controls.additional_references.isEnabled())
        item._build_widgets()
        APP.processEvents()
        for proxy in item._plugin_proxies:
            self.assertGreaterEqual(item.height, proxy.pos().y() + proxy.size().height() + 10)
            self.assertGreaterEqual(proxy.size().height(), proxy.widget().minimumSizeHint().height())
        dialog = SetupDialog(item)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.native)
        self.assertTrue(dialog.repository.isHidden())
        self.assertEqual(dialog.variant.currentData(), "bf16")
        self.assertFalse(dialog.install.isEnabled())

    def test_edit_guidance_and_framing_are_optional_persist_and_fit_inside_node(self):
        graph, item, controls = self.make_item()
        self.assertFalse(controls.edit_guidance.isEnabled())
        controls.backend.setCurrentIndex(controls.backend.findData("native_diffusers"))
        self.assertFalse(controls.edit_guidance.isEnabled())
        controls.mode.setCurrentIndex(controls.mode.findData("image_edit"))
        self.assertEqual(controls.edit_guidance.currentData(), "flexible")
        self.assertEqual(controls.edit_framing.currentData(), "auto")
        self.assertTrue(controls.edit_guidance.isEnabled())
        self.assertFalse(controls.width.isEnabled())
        controls.edit_guidance.setCurrentIndex(controls.edit_guidance.findData("pose_guide"))
        for framing in ("input", "reference", "custom"):
            controls.edit_framing.setCurrentIndex(controls.edit_framing.findData(framing))
            self.assertEqual(controls.width.isEnabled(), framing == "custom")
            self.assertEqual(controls.height.isEnabled(), framing == "custom")
        controls.width.setValue(640)
        controls.height.setValue(352)
        put(item.model, prompt="Use the render's pose and the reference outfit.")
        request = controls.session.request(validate=False)
        self.assertEqual((request.edit_guidance, request.edit_framing), ("pose_guide", "custom"))
        self.assertEqual((request.width, request.height), (640, 352))
        with patch.object(type(controls.session), "running", new_callable=PropertyMock, return_value=True):
            controls.refresh()
            self.assertFalse(controls.edit_guidance.isEnabled())
            self.assertFalse(controls.edit_framing.isEnabled())
            self.assertFalse(controls.width.isEnabled())
        controls.refresh()
        saved = _node_to_dict(item.model)
        _, loaded, loaded_controls = self.make_item(GraphNode("Loaded options", kind="qwen_image", params=saved["params"]))
        for test_item, test_controls in ((item, controls), (loaded, loaded_controls)):
            test_item._build_widgets()
            APP.processEvents()
            test_controls = test_item._plugin_proxies[0].widget()
            self.assertEqual(test_controls.edit_guidance.currentData(), "pose_guide")
            self.assertEqual(test_controls.edit_framing.currentData(), "custom")
            self.assertEqual(test_controls.width.value(), 640)
            proxy = test_item._plugin_proxies[0]
            self.assertGreaterEqual(test_item.height, proxy.pos().y() + proxy.size().height() + 10)
            self.assertGreaterEqual(proxy.size().height(), test_controls.minimumSizeHint().height())
            self.assertEqual(test_controls.generate.height(), test_controls.setup.height())
        controls = item._plugin_proxies[0].widget()
        controls.backend.setCurrentIndex(controls.backend.findData("comfyui"))
        self.assertFalse(controls.edit_guidance.isEnabled())
        self.assertFalse(controls.edit_framing.isEnabled())
        request = controls.session.request(validate=False)
        self.assertEqual((request.edit_guidance, request.edit_framing), ("flexible", "auto"))

    def test_native_model_folder_browse_persists_separately_and_survives_node_rebuild(self):
        from nodes.qwen_image.setup_ui import SetupDialog
        from echograph.image_generation.native_config import NativeConfig, saved_native_config
        graph, item, controls = self.make_item()
        controls.backend.setCurrentIndex(controls.backend.findData("native_diffusers"))
        manager = controls.manager
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            chosen = Path(directory) / "chosen-models"
            chosen.mkdir()
            other_model = chosen / "OtherModel.bin"
            other_model.write_bytes(b"keep this model")
            with patch.object(manager, "config", NativeConfig(runtime)), patch(
                    "echograph.image_generation.native_config.storage_root", return_value=Path(directory) / "settings"):
                dialog = SetupDialog(item)
                self.addCleanup(dialog.deleteLater)
                self.addCleanup(dialog.close)
                self.assertEqual(Path(dialog.model_directory.text()), runtime / "models")
                def choose(parent, title, initial):
                    self.assertIs(parent, dialog)
                    item._build_widgets()
                    APP.processEvents()
                    return str(chosen)
                with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", side_effect=choose):
                    dialog.model_browse.click()
                    dialog.model_browse.click()
                self.assertEqual(manager.config.runtime, runtime.resolve())
                expected = chosen.resolve() / "Qwen-Image-2.1"
                self.assertEqual(manager.config.model, expected)
                self.assertEqual(saved_native_config().model, expected)
                self.assertEqual(Path(dialog.model_destination.text()), expected)
                self.assertFalse(runtime.exists())
                self.assertFalse(expected.exists())
                self.assertEqual(other_model.read_bytes(), b"keep this model")
                reopened = SetupDialog(item)
                self.addCleanup(reopened.deleteLater)
                self.addCleanup(reopened.close)
                self.assertEqual(Path(reopened.model_directory.text()), chosen.resolve())
                with patch.object(type(manager), "server_running", new_callable=PropertyMock, return_value=True):
                    dialog.refresh()
                    self.assertFalse(dialog.model_directory.isEnabled())
                    self.assertFalse(dialog.model_browse.isEnabled())

    def test_backend_switch_is_blocked_while_existing_runtime_is_running(self):
        graph, item, controls = self.make_item()
        with patch.object(type(controls.manager), "server_running", new_callable=PropertyMock, return_value=True):
            controls.backend.setCurrentIndex(controls.backend.findData("native_diffusers"))
        self.assertEqual(value(item.model, "backend"), "comfyui")
        self.assertEqual(controls.backend.currentData(), "comfyui")
        self.assertIn("Stop the current backend", controls.status.text())

    def test_native_subprocess_result_updates_real_node_preview_and_serialized_metadata(self):
        from echograph.image_generation.native_backend import NativeService
        from echograph.image_generation.native_config import NativeConfig
        graph, item, controls = self.make_item()
        controls.backend.setCurrentIndex(controls.backend.findData("native_diffusers"))
        controls.cfg.setValue(2.0)
        positive = QtWidgets.QGraphicsRectItem()
        positive.model = GraphNode("Positive Note", kind="note", params=[
            {"name": "selected", "value": "Synthetic positive prompt"},
            {"name": "unused", "value": "Unchecked positive must not leak"}])
        positive.model._completed_params = {"selected"}
        negative = QtWidgets.QGraphicsRectItem()
        negative.model = GraphNode("Negative Note", kind="note", params=[
            {"name": "only field", "value": "Synthetic negative prompt"}])
        graph.addItem(positive)
        graph.addItem(negative)
        graph.edges = [SimpleNamespace(src=positive, dst_port_name="prompt"),
                       SimpleNamespace(src=negative, dst_port_name="negative_prompt")]
        manager = controls.manager
        with tempfile.TemporaryDirectory() as directory:
            config = NativeConfig(Path(directory))
            service = NativeService(command=[sys.executable, "-u", str(Path(__file__).with_name("native_worker_fixture.py"))])
            service.start(config)
            try:
                with patch.object(manager, "service", service):
                    check_messages = []
                    controls.session.changed.connect(lambda: check_messages.append(controls.session.message))
                    controls.session.check_backend()
                    deadline = time.monotonic() + 5
                    while controls.session.check_task and time.monotonic() < deadline:
                        QtTest.QTest.qWait(10)
                    self.assertIsNone(controls.session.check_task, controls.session.message)
                    self.assertIn("bf16", controls.session.inventory["variants"])
                    self.assertIn("Native backend ready", controls.status.text())
                    self.assertTrue(any("Checking native Python" in message for message in check_messages))
                    put(item.model, prompt="Synthetic UI integration test", license_accepted="true",
                        output_directory=directory, random_seed="false", seed="42")
                    controls.session.generate()
                    deadline = time.monotonic() + 5
                    while controls.session.running and time.monotonic() < deadline:
                        QtTest.QTest.qWait(10)
                    self.assertFalse(controls.session.running)
                    self.assertEqual(value(item.model, "status"), "success", controls.session.message)
                    self.assertFalse(controls.preview.pixmap.isNull())
                    self.assertTrue(item.model._qwen_outputs["image"].mode == "RGBA")
                    metadata = json.loads(value(item.model, "metadata"))
                    self.assertEqual(metadata["backend"], "native_diffusers")
                    self.assertEqual(metadata["prompt"], "Synthetic positive prompt")
                    self.assertEqual(metadata["negative_prompt"], "Synthetic negative prompt")
                    self.assertEqual(metadata["cfg"], 2.0)
                    self.assertEqual(value(item.model, "seed_used"), "42")
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()
