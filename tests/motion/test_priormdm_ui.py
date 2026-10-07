from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6 import QtCore, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.persistence import _node_to_dict
from echograph.motion.qt_job import MotionJob
from echograph.motion.types import GenerationPlan
from nodes.priormdm.spec import build_ports, render_node_body, MotionControls

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Item(QtWidgets.QGraphicsWidget):
    def __init__(self):
        super().__init__()
        self.model = GraphNode(name="Motion", kind="priormdm", params=[])
        self.width, self.height = 240, 70
        self._plugin_proxies = []


def await_signal(spy, timeout=7000):
    if spy.count() == 0:
        spy.wait(timeout)
    return spy.count()


class MotionUITests(unittest.TestCase):
    def test_output_resolves_after_new_node_is_attached_to_project(self):
        from nodes.priormdm.spec import value, resolve_output_default
        for attribute, path, expected in (
            ("_current_path", "C:/Projects/Motion/workflow.json", "C:/Projects/Motion"),
            ("_current_project_dir", "C:/Projects/Motion", "C:/Projects/Motion"),
            ("_pending_project_save_dir", "C:/Projects/NewProject", "C:/Projects/NewProject"),
        ):
            window = QtWidgets.QMainWindow()
            scene = QtWidgets.QGraphicsScene(window)
            view = QtWidgets.QGraphicsView(scene)
            window.setCentralWidget(view)
            setattr(window, attribute, path)
            item = Item()
            with patch("nodes.priormdm.setup_ui.schedule_setup_offer"):
                build_ports(item)  # Actual construction order: no scene yet.
            self.assertEqual(Path(value(item.model, "output_root")), Path(tempfile.gettempdir()) / "EchoGraph")
            scene.addItem(item)
            APP.processEvents()
            self.assertEqual(Path(value(item.model, "output_root")), Path(expected) / "priormdm")
            next(p for p in item.model.params if p["name"] == "output_root")["value"] = expected
            resolve_output_default(item)
            self.assertEqual(Path(value(item.model, "output_root")), Path(expected) / "priormdm")
            next(p for p in item.model.params if p["name"] == "output_root")["value"] = "C:/Custom/Motions"
            resolve_output_default(item)
            self.assertEqual(Path(value(item.model, "output_root")), Path("C:/Custom/Motions"))
            window.close()
            window.deleteLater()

    def test_output_defaults_use_project_or_temp_and_preserve_custom_paths(self):
        from nodes.priormdm.spec import value
        from echograph.motion.backends.priormdm.backend import PROJECT_ROOT
        for project, existing, expected in (
            (None, None, Path(tempfile.gettempdir()) / "EchoGraph"),
            (Path("C:/Projects/Motion"), None, Path("C:/Projects/Motion/priormdm")),
            (Path("C:/Projects/Motion"), str(PROJECT_ROOT / "logs/motion/priormdm"), Path("C:/Projects/Motion/priormdm")),
            (Path("C:/Projects/Motion"), "C:/Custom/Motions", Path("C:/Custom/Motions")),
        ):
            item = Item()
            if existing is not None:
                item.model.params.append({"name": "output_root", "value": existing})
            with patch("nodes.mocap_import.spec._workflow_dir_for_node", return_value=project), \
                    patch("nodes.priormdm.setup_ui.schedule_setup_offer"):
                build_ports(item)
            self.assertEqual(Path(value(item.model, "output_root")), expected)

    def test_missing_git_has_actionable_setup_error(self):
        from echograph.motion.backends.priormdm import installer
        with tempfile.TemporaryDirectory() as folder, patch.object(installer.shutil, "which", return_value=None), \
                patch.object(installer, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Git was not found.*restart Qubit"):
                installer.perform_setup(SimpleNamespace(dataset=None, clone=True), Mock(), Path(folder) / "PriorMDM")
            run.assert_not_called()

    def test_typing_with_pauses_waits_until_the_editor_loses_focus(self):
        for field in ("prompt", "hand_description"):
            with self.subTest(field=field):
                node = GraphNode(name="Motion", kind="priormdm", params=[
                    {"name": "animate_hands", "value": "true"},
                ])
                scene = SimpleNamespace(set_node_params=Mock())
                controls = MotionControls(node, scene)
                self.addCleanup(controls.deleteLater)
                self.addCleanup(controls.close)
                controls.show()
                controls.activateWindow()
                editor = getattr(controls, field)
                editor.setFocus()
                APP.processEvents()
                self.assertTrue(editor.hasFocus())
                editor.clear()

                for text in ("Walk", " forward"):
                    QtTest.QTest.keyClicks(editor, text)
                    # A normal pause between words must not trigger a graph refresh.
                    QtTest.QTest.qWait(450)
                    scene.set_node_params.assert_not_called()
                saved = {p["name"]: p["value"] for p in _node_to_dict(node)["params"]}
                self.assertEqual(saved[field], "Walk forward")

                controls.generate_button.setFocus()
                QtTest.QTest.qWait(450)
                scene.set_node_params.assert_called_once_with(node.name, node.params, rebuild=False, emit=True)
                controls.close()

    def test_closing_controls_flushes_pending_prompt_after_a_typing_pause(self):
        node = GraphNode(name="Motion", kind="priormdm", params=[])
        scene = SimpleNamespace(set_node_params=Mock())
        controls = MotionControls(node, scene)
        self.addCleanup(controls.deleteLater)
        self.addCleanup(controls.close)
        controls.show()
        controls.activateWindow()
        controls.prompt.setFocus()
        APP.processEvents()
        self.assertTrue(controls.prompt.hasFocus())
        controls.prompt.setPlainText("Turn around")
        QtTest.QTest.qWait(450)
        scene.set_node_params.assert_not_called()

        controls.close()
        QtTest.QTest.qWait(450)
        scene.set_node_params.assert_called_once_with(node.name, node.params, rebuild=False, emit=True)
        self.assertIn({"name": "prompt", "value": "Turn around"}, node.params)

    def test_typing_saves_immediately_but_batches_graph_updates(self):
        node = GraphNode(name="Motion", kind="priormdm", params=[])
        scene = SimpleNamespace(set_node_params=Mock())
        controls = MotionControls(node, scene)
        self.addCleanup(controls.deleteLater)
        controls.prompt.clear()
        prompt = "A person walks forward and turns left."
        hands = "Slowly open both hands."
        QtTest.QTest.keyClicks(controls.prompt, prompt)
        QtTest.QTest.keyClicks(controls.hand_description, hands)

        self.assertEqual(controls.prompt.toPlainText(), prompt)
        saved = {p["name"]: p["value"] for p in _node_to_dict(node)["params"]}
        self.assertEqual(saved["prompt"], prompt)
        self.assertEqual(saved["hand_description"], hands)
        scene.set_node_params.assert_not_called()

        QtTest.QTest.qWait(450)
        scene.set_node_params.assert_called_once_with(node.name, node.params, rebuild=False, emit=True)

    def test_setting_change_includes_pending_text_without_duplicate_update(self):
        node = GraphNode(name="Motion", kind="priormdm", params=[])
        scene = SimpleNamespace(set_node_params=Mock())
        controls = MotionControls(node, scene)
        self.addCleanup(controls.deleteLater)
        controls.prompt.setPlainText("Turn around")
        controls.duration.setValue(4.0)

        scene.set_node_params.assert_called_once_with(node.name, node.params, rebuild=False, emit=True)
        params = {p["name"]: p["value"] for p in scene.set_node_params.call_args.args[1]}
        self.assertEqual(params["prompt"], "Turn around")
        self.assertEqual(float(params["duration"]), 4.0)
        QtTest.QTest.qWait(450)
        self.assertEqual(scene.set_node_params.call_count, 1)

    def test_compact_border_contains_entire_widget_and_padding(self):
        item = Item()
        build_ports(item)
        bottom = render_node_body(item, 60)
        proxy = item._plugin_proxies[0]
        self.assertGreaterEqual(item.height, proxy.pos().y() + proxy.size().height() + 10)
        self.assertEqual(item.height, bottom)
        self.assertGreaterEqual(proxy.size().height(), proxy.widget().minimumSizeHint().height())
        controls = MotionControls(item.model)
        self.assertFalse(hasattr(controls, "cancel_button"))
        self.assertTrue(controls.generate_button.isEnabled())
        self.assertFalse(controls.preview_button.isEnabled())
        controls.prompt.setPlainText("turn around")
        self.assertIn({"name": "prompt", "value": "turn around"}, item.model.params)
        controls.deleteLater()

    def test_actual_node_item_sizes_and_hides_parameters(self):
        from echograph.ui.node_item import NodeItem
        item = NodeItem(GraphNode(name="PriorMDM", kind="priormdm", params=[]))
        self.assertTrue(item._plugin_proxies)
        for proxy in item._plugin_proxies:
            self.assertGreaterEqual(item.height, proxy.pos().y() + proxy.size().height() + 5)

    def test_job_success_failure_and_cancel(self):
        for script, expected in (("print('inference log')", "complete"),
                                 ("raise RuntimeError('fixture failure')", "failed"),
                                 ("import time; time.sleep(30)", "cancelled")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder:
                plan = GenerationPlan((sys.executable, "-u", "-c", script), Path(folder), Path(folder))
                service = SimpleNamespace(collect=lambda _: "result")
                job = MotionJob(service, plan)
                ok = QtTest.QSignalSpy(job.completed)
                bad = QtTest.QSignalSpy(job.failed)
                job.start()
                if expected == "cancelled":
                    QtCore.QTimer.singleShot(50, job.cancel)
                self.assertEqual(await_signal(ok if expected == "complete" else bad), 1)
                self.assertFalse(job.running)
                self.assertIn(expected, (Path(folder) / "status.json").read_text())
                self.assertEqual(ok.count() + bad.count(), 1)
                job.deleteLater()

    def test_failed_to_start_settles_once(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = GenerationPlan((str(Path(folder) / "missing.exe"),), Path(folder), Path(folder))
            job = MotionJob(SimpleNamespace(collect=lambda _: self.fail("unexpected collection")), plan)
            failures = QtTest.QSignalSpy(job.failed)
            job.start()
            self.assertEqual(await_signal(failures), 1)
            self.assertFalse(job.running)


if __name__ == "__main__":
    unittest.main()
