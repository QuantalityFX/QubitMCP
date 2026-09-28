from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6 import QtCore, QtWidgets

from echograph.model import GraphNode
from nodes.anim_retarget import spec

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class RetargetDialogTests(unittest.TestCase):
    def setUp(self):
        self.window = QtWidgets.QMainWindow()
        self.scene = QtWidgets.QGraphicsScene(self.window)
        self.window.setCentralWidget(QtWidgets.QGraphicsView(self.scene))
        self.window.open_scene_assets = Mock()
        self.body = QtWidgets.QWidget()
        self.scene.addWidget(self.body)
        self.item = SimpleNamespace(model=GraphNode("Retarget", kind="anim_retarget"), scene=lambda: self.scene)
        self.error = spec.RetargetSourceTargetResult(
            "error", animation_connected=True, animation_error="animation: rig differs from source",
            errors=["animation: rig differs from source"])
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_incompatible_animation_opens_one_unproxied_error_dialog(self):
        observed = {}

        def inspect_and_close():
            boxes = [widget for widget in APP.allWidgets()
                     if isinstance(widget, QtWidgets.QMessageBox) and widget.isVisible()]
            observed["count"] = len(boxes)
            observed["scene_proxies"] = sum(isinstance(item, QtWidgets.QGraphicsProxyWidget)
                                            for item in self.scene.items())
            if boxes:
                box = boxes[0]
                observed["owner"] = box.parentWidget()
                observed["proxy"] = box.graphicsProxyWidget()
                observed["message"] = box.text()
                observed["windows"] = sum(widget.isVisible() and widget.windowTitle() == "Anim Retarget View"
                                          for widget in APP.topLevelWidgets())
            for box in boxes:
                box.accept()

        QtCore.QTimer.singleShot(30, inspect_and_close)
        opened = spec.open_anim_retarget_preview(self.item, parent=self.body, result=self.error)
        self.assertFalse(opened)
        self.assertEqual(observed["count"], 1)
        self.assertEqual(observed["windows"], 1)
        self.assertIs(observed["owner"], self.window)
        self.assertIsNone(observed["proxy"])
        self.assertEqual(observed["scene_proxies"], 1)  # Only the original node body.
        self.assertIn("Status: ERROR", observed["message"])
        self.assertIn("rig differs", observed["message"])
        self.window.open_scene_assets.assert_not_called()

    def test_quiet_refresh_does_not_open_error_dialog(self):
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            self.assertFalse(spec.open_anim_retarget_preview(self.item, quiet=True, parent=self.body, result=self.error))
            warning.assert_not_called()

    def test_info_card_errors_use_main_window_owner(self):
        card = QtWidgets.QWidget(self.window)
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            spec._show_retarget_warning(self.item, card, "Anim Retarget Validation", "Status: ERROR")
            self.assertIs(warning.call_args.args[0], self.window)

    def test_detached_embedded_widget_is_never_a_dialog_owner(self):
        detached = SimpleNamespace(scene=lambda: None)
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            spec._show_retarget_warning(detached, self.body, "Anim Retarget View", "Status: ERROR")
            self.assertIsNone(warning.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
