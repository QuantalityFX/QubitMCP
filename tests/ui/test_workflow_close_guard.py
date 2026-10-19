"""Protect real graph edits through the main window's close and save methods."""
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.ui.workflow_close_guard import WorkflowCloseGuard
from tests.ui.test_shelf_snippet_window import real_graph_scene


def main_window_methods():
    # Exercise production methods without running the shelf module's app launcher.
    path = Path(__file__).resolve().parents[2] / "echograph_shelf.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    window = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "EchoGraphWindow")
    wanted = {"closeEvent", "_save_graph", "_export_graph", "_save_workflow_file", "_workflow_data_for_save"}
    window.body = [node for node in window.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = dict(QtWidgets=QtWidgets, QtGui=QtGui, Path=Path, json=json,
                     APP_TITLE="QubitField", Dict=dict, Any=object)
    exec(compile(ast.Module(body=[window], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["EchoGraphWindow"]


class WorkflowCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.Window = main_window_methods()
        cls.Scene = real_graph_scene()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "workflow.json"
        self.path.write_text('{"original": true}', encoding="utf-8")
        self.window = self.Window()
        self.window._current_path = str(self.path)
        self.window._current_project_dir = ""
        self.window._pending_project_save_dir = ""
        self.window._workflow_load_in_progress = False
        self.scene = self.Scene()
        self.window.scene = self.scene
        self.scene.add_node(GraphNode("Note", kind="note", params=[{"name": "Task", "value": "Before"}]), (0, 0))
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.window.setCentralWidget(self.view)
        self.window._sync_data_nexus_sidecars_for_save = Mock()
        self.window._clone_timeline_sidecars_for_save_as = Mock()
        self.window._remember_recent = Mock()
        self.window._update_window_title = Mock()
        self.window._timeline_controller = SimpleNamespace(sync_timeline_context=Mock())
        self.window._project_dir_from_state = lambda name: Path(value) if (value := getattr(self.window, name)) else None
        self.window._inject_panel_layout_into_workflow_data = lambda data: data["settings"].update(
            {"panel_layout": {"timeline": False}, "view_mode": "2d"})
        self.window._inject_scene_restore_into_workflow_data = lambda data: None
        self.window._inject_timeline_frame_into_workflow_data = lambda data: data["settings"].update({"timeline_frame": 24})
        self.guard = WorkflowCloseGuard(self.window, self.scene, self.window._save_graph)
        self.window._workflow_close_guard = self.guard
        self.window.show()
        for _ in range(4):
            self.app.processEvents()
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Discard):
            self.window.close()
        self.scene.clear()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def edit(self, value="After"):
        self.scene.set_node_params("Note", [{"name": "Task", "value": value}])

    def test_clean_navigation_and_reverted_edits_close_without_prompt(self):
        self.scene._node_items["Note"].setSelected(True)
        self.view.scale(1.2, 1.2)
        self.view.centerOn(200, 100)
        self.edit()
        self.edit("Before")
        with patch.object(QtWidgets.QMessageBox, "question") as question:
            self.assertTrue(self.window.close())
        question.assert_not_called()

    def test_cancel_then_discard_preserves_saved_file(self):
        original = self.path.read_bytes()
        self.edit()
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Cancel) as question:
            self.assertFalse(self.window.close())
        self.assertEqual(question.call_args.args[-1], QtWidgets.QMessageBox.Save)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.guard.is_dirty())
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Discard):
            self.assertTrue(self.window.close())
        self.assertEqual(self.path.read_bytes(), original)

    def test_save_on_close_writes_changes_to_existing_workflow(self):
        self.edit("A multiline\nnote")
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save):
            self.assertTrue(self.window.close())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["nodes"][0]["params"][0]["value"], "A multiline\nnote")
        self.assertEqual(saved["settings"]["timeline_frame"], 24)
        self.assertFalse(self.guard.is_dirty())

    def test_save_untitled_workflow_and_cancel_save_dialog(self):
        self.window._current_path = None
        self.edit()
        original = self.path.read_bytes()
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save), \
                patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.assertFalse(self.window.close())
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.guard.is_dirty())
        self.assertEqual(self.path.read_bytes(), original)
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save), \
                patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(self.path), "")):
            self.assertTrue(self.window.close())
        self.assertEqual(json.loads(self.path.read_text())["nodes"][0]["params"][0]["value"], "After")
        self.window._clone_timeline_sidecars_for_save_as.assert_called_once_with(None, str(self.path))

    def test_failed_save_keeps_window_open_and_dirty(self):
        self.edit()
        original = self.path.read_bytes()
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save), \
                patch.object(QtWidgets.QMessageBox, "critical") as critical, \
                patch("builtins.open", side_effect=PermissionError("file is locked")):
            self.assertFalse(self.window.close())
        critical.assert_called_once()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.guard.is_dirty())
        self.assertEqual(self.path.read_bytes(), original)

    def test_manual_save_and_export_reset_saved_state(self):
        for export in (False, True):
            self.edit(f"Saved via {'export' if export else 'save'}")
            if export:
                with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(self.path), "")):
                    self.assertTrue(self.window._export_graph())
            else:
                self.assertTrue(self.window._save_graph())
            self.assertFalse(self.guard.is_dirty())
        with patch.object(QtWidgets.QMessageBox, "question") as question:
            self.assertTrue(self.window.close())
        question.assert_not_called()

    def test_unexpected_save_error_keeps_window_open(self):
        self.edit()
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save), \
                patch.object(QtWidgets.QMessageBox, "warning") as warning, \
                patch.object(self.guard, "_save_workflow", side_effect=RuntimeError("save dialog failed")):
            self.assertFalse(self.window.close())
        warning.assert_called_once()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.guard.is_dirty())

    def test_pending_project_save_and_cancellation(self):
        self.window._pending_project_save_dir = str(self.path.parent)
        self.window._prompt_project_workflow_path = Mock(return_value=None)
        self.edit()
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save):
            self.assertFalse(self.window.close())
        self.window._prompt_project_workflow_path.return_value = str(self.path)
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save):
            self.assertTrue(self.window.close())
        self.assertEqual(self.window._pending_project_save_dir, "")
        self.assertFalse(self.guard.is_dirty())

    def test_load_baseline_and_edits_before_initialization_settles(self):
        self.scene.from_dict({"nodes": [{"name": "Note", "kind": "note",
                                        "params": [{"name": "Task", "value": "Loaded"}]}]})
        self.guard.reset_after_load()
        self.assertFalse(self.guard.is_dirty())
        self.guard.reset_after_load()
        self.edit("Edited immediately")
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Cancel):
            self.assertFalse(self.window.close())
        self.assertTrue(self.guard.is_dirty())

    def test_focused_editor_commits_before_close_check(self):
        editor = QtWidgets.QLineEdit("Before", self.window)
        self.window.setCentralWidget(editor)
        editor.editingFinished.connect(lambda: self.edit(editor.text()))
        editor.setFocus()
        self.window.activateWindow()
        self.app.processEvents()
        editor.setText("Pending field edit")
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Cancel) as question:
            self.assertFalse(self.window.close())
        question.assert_called_once()
        self.assertEqual(self.scene._nodes_by_name["Note"].params[0]["value"], "Pending field edit")

    def test_failed_dirty_check_keeps_window_open(self):
        with patch.object(self.scene, "to_dict", side_effect=RuntimeError("unavailable")), \
                patch.object(QtWidgets.QMessageBox, "warning") as warning:
            self.assertFalse(self.window.close())
        warning.assert_called_once()
        self.assertTrue(self.window.isVisible())

    def test_node_moves_deletion_and_settings_are_detected(self):
        self.scene._node_items["Note"].setPos(200, 300)
        self.assertTrue(self.guard.is_dirty())
        self.guard.mark_saved(self.window._workflow_data_for_save())
        self.scene._view_settings = {"light_intensity": 2.0}
        self.assertTrue(self.guard.is_dirty())
        self.guard.mark_saved(self.window._workflow_data_for_save())
        self.scene._node_items["Note"].setSelected(True)
        self.scene.delete_selected_nodes()
        self.assertTrue(self.guard.is_dirty())

    def test_gantt_initialization_does_not_prompt(self):
        from nodes import note, gantt_chart
        note.register()
        gantt_chart.register()
        self.scene.from_dict({"nodes": [
            {"name": "Note", "kind": "note", "params": [{"name": "One", "value": "Body"}]},
            {"name": "Chart", "kind": "gantt_chart", "pos": [800, 0],
             "params": [{"name": "progress_data", "value": '{"One": 50}'}]},
        ], "edges": [{"src": "Note", "dst": "Chart"}]})
        self.guard.reset_after_load()
        QtTest.QTest.qWait(20)
        with patch.object(QtWidgets.QMessageBox, "question") as question:
            self.assertTrue(self.window.close())
        question.assert_not_called()


if __name__ == "__main__":
    unittest.main()
