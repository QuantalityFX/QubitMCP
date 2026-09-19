from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

from PySide6 import QtCore, QtTest, QtWidgets

from echograph.model import GraphNode
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
    def test_compact_border_contains_entire_widget_and_padding(self):
        item = Item()
        build_ports(item)
        bottom = render_node_body(item, 60)
        proxy = item._plugin_proxies[0]
        self.assertGreaterEqual(item.height, proxy.pos().y() + proxy.size().height() + 10)
        self.assertEqual(item.height, bottom)
        self.assertGreaterEqual(proxy.size().height(), proxy.widget().minimumSizeHint().height())
        controls = MotionControls(item.model)
        self.assertFalse(controls.cancel_button.isEnabled())
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
