"""Exercise shelf popup persistence and close protection with the real graph scene."""
import ast
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph import persistence
from echograph.constants import DEFAULT_STRIPE_HEX, LLM_SCALE_DEFAULT, LLM_NODE_W_BASE, LLM_NODE_H_BASE
from echograph.model import GraphNode
from echograph.services.profiler import profiled, profile_scope
from echograph.services.shelf_tools_store import ShelfToolsStore, normalize_tool
from echograph.ui import hotkeys_config
from echograph.ui.graph_items import EdgeItem, TempWire, _gi_flag
from echograph.ui.node_item import NodeItem
from echograph.ui.shelf_bar import ShelfBar
from echograph.ui.shelf_snippet_window import ShelfSnippetWindow
from echograph.ui.shelf_tool_dialogs import ShelfToolDialog


def real_graph_scene():
    # Load production graph classes without executing the module's app launcher.
    path = Path(__file__).resolve().parents[2] / "echograph_shelf.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    names = {"GraphScene", "CommentGroup", "set_global_llm_scale", "_as_pointf",
             "_normalize_comment_color", "_llm_dims"}
    definitions = [node for node in tree.body
                   if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    namespace = dict(globals(), DEFAULT_COMMENT_COLOR="#1f2933", COMMENT_MEMBER_PAD=16.0,
                     LLM_SCALE=LLM_SCALE_DEFAULT)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *definitions], type_ignores=[])),
                 str(path), "exec"), namespace)
    return namespace["GraphScene"]


class ShelfSnippetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.Scene = real_graph_scene()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ShelfToolsStore(Path(directory.name) / "shelf.json")
        self.payload = {
            "format": "EchoGraphClipboard", "version": 1, "centroid": [200, 80],
            "nodes": [
                {"name": "Source", "kind": "node", "pos": [0, 0], "code": "original",
                 "params": [{"name": "value", "value": "before"}]},
                {"name": "Target", "kind": "node", "pos": [400, 160], "params": []},
            ],
            "edges": [{"src": "Source", "dst": "Target"}],
            "comments": [{"title": "Network", "body": "original comment", "members": ["Source", "Target"],
                          "rect": [-50, -50, 900, 600], "color": "#1f2933"}],
        }
        self.tool = self.store.add_tool({"type": "graph_snippet", "label": "My Network", "open_mode": "popup_window",
                                         "payload": self.payload})
        self.parent = QtWidgets.QMainWindow()
        self.parent.scene = self.Scene()
        self.parent.scene.add_node(GraphNode("MainGraph", kind="node"), (100, 100))
        self.windows = []
        self.addCleanup(self.cleanup_windows)

    def cleanup_windows(self):
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Discard):
            self.parent.close()
            for window in self.windows:
                window.close()
        self.parent.scene.deleteLater()
        self.parent.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def popup(self):
        window = ShelfSnippetWindow(self.parent, scene_factory=self.Scene, store=self.store,
                                    tool=self.store.tool_by_id(self.tool["id"]))
        # Keep C++ objects alive until cleanup so tests can inspect accepted closes.
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.windows.append(window)
        window.show()
        self.app.processEvents()
        return window

    def edit(self, window):
        window.scene.set_node_params("Source", [{"name": "value", "value": "after"}])

    def test_open_mode_dialog_and_legacy_default(self):
        legacy = dict(self.tool)
        legacy.pop("open_mode")
        self.assertEqual(normalize_tool(legacy)["open_mode"], "current_graph")
        dialog = ShelfToolDialog(tool=legacy)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.result_tool()["open_mode"], "current_graph")
        dialog.snippet_open_mode_combo.setCurrentIndex(dialog.snippet_open_mode_combo.findData("popup_window"))
        result = dialog.result_tool()
        self.assertEqual(result["open_mode"], "popup_window")
        self.assertEqual(result["payload"], self.payload)

    def test_unchanged_selection_and_navigation_close_without_prompt(self):
        before = deepcopy(self.parent.scene.to_dict())
        window = self.popup()
        window.scene._node_items["Source"].setSelected(True)
        window.view.scale(1.2, 1.2)
        window.view.centerOn(250, 100)
        self.app.processEvents()
        with patch.object(QtWidgets.QMessageBox, "question") as question:
            self.assertTrue(window.close())
        question.assert_not_called()
        self.assertEqual(self.parent.scene.to_dict(), before)
        self.assertEqual(self.store.tool_by_id(self.tool["id"])["payload"], self.payload)

    def test_gantt_startup_updates_do_not_count_as_user_edits(self):
        from nodes import note, gantt_chart
        note.register()
        gantt_chart.register()
        payload = {
            "nodes": [
                {"name": "Tasks", "kind": "note", "pos": [0, 0],
                 "params": [{"name": "One", "value": "A task"}]},
                {"name": "Chart", "kind": "gantt_chart", "pos": [800, 0],
                 "params": [{"name": "progress_data", "value": '{"One": 50}'}]},
            ],
            "edges": [{"src": "Tasks", "dst": "Chart"}],
            "comments": [],
        }
        self.store.update_tool(self.tool["id"], {"payload": payload})
        path = self.store.snippet_workflow_path(self.tool)
        before = path.read_bytes()
        window = self.popup()
        QtTest.QTest.qWait(20)
        self.assertFalse(window._initializing)
        self.assertFalse(window._refresh_dirty())
        progress = {p["name"]: p["value"] for p in window.scene._nodes_by_name["Chart"].params}
        self.assertNotEqual(progress["progress_data"], '{"One": 50}')
        with patch.object(QtWidgets.QMessageBox, "question") as question:
            self.assertTrue(window.close())
        question.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_first_input_preserves_edits_before_initialization_finishes(self):
        window = ShelfSnippetWindow(self.parent, scene_factory=self.Scene, store=self.store, tool=self.tool)
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.windows.append(window)
        window.show()
        self.app.sendEvent(window.view, QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, QtCore.Qt.Key_F, QtCore.Qt.NoModifier))
        window.scene._nodes_by_name["Source"].code = "first edit"
        QtTest.QTest.qWait(10)
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Cancel):
            self.assertFalse(window.close())
        self.assertTrue(window._refresh_dirty())

    def test_save_on_close_round_trips_all_nodes_links_comments_and_layout(self):
        window = self.popup()
        self.edit(window)
        window.scene._nodes_by_name["Source"].code = "edited code"
        window.scene._node_items["Source"].setPos(50, 60)
        window.scene._comment_groups[0]._body = "edited comment"
        new = window.scene.add_node(GraphNode("New", kind="node"), (900, 600))
        window.scene.add_edge("Target", new.model.name)
        expected = window._network_state()
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save) as question:
            self.assertTrue(window.close())
        question.assert_called_once()
        reloaded = ShelfToolsStore(self.store.path)
        reloaded.load()
        saved = reloaded.tool_by_id(self.tool["id"])
        for key in ("nodes", "edges", "comments"):
            self.assertEqual(saved["payload"][key], expected[key])
        self.assertEqual(saved["open_mode"], "popup_window")
        self.assertEqual(self.popup()._network_state(), expected)
        self.assertEqual(list(self.parent.scene._node_items), ["MainGraph"])

    def test_cancel_then_discard_keeps_saved_network(self):
        window = self.popup()
        before = self.store.path.read_bytes()
        workflow_before = self.store.snippet_workflow_path(self.tool).read_bytes()
        self.edit(window)
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Cancel):
            self.assertFalse(window.close())
        self.assertTrue(window.isVisible())
        self.assertTrue(window._refresh_dirty())
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Discard):
            self.assertTrue(window.close())
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.snippet_workflow_path(self.tool).read_bytes(), workflow_before)
        self.assertEqual(self.store.tool_by_id(self.tool["id"])["payload"], self.payload)

    def test_failed_save_does_not_close_or_overwrite_stored_data(self):
        window = self.popup()
        before = self.store.path.read_bytes()
        workflow_before = self.store.snippet_workflow_path(self.tool).read_bytes()
        self.edit(window)
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save), \
                patch.object(QtWidgets.QMessageBox, "warning") as warning, \
                patch.object(Path, "replace", side_effect=PermissionError("file is locked")):
            self.assertFalse(window.close())
        warning.assert_called_once()
        self.assertTrue(window.isVisible())
        self.assertTrue(window._refresh_dirty())
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.snippet_workflow_path(self.tool).read_bytes(), workflow_before)
        self.assertEqual(self.store.tool_by_id(self.tool["id"])["payload"], self.payload)
        self.assertEqual({path.resolve() for path in self.store.path.parent.iterdir()},
                         {self.store.path.resolve(), self.store.snippet_workflow_path(self.tool)})

    def test_deleting_every_node_can_be_saved_and_reopened(self):
        window = self.popup()
        for item in window.scene._node_items.values():
            item.setSelected(True)
        window.scene.delete_selected_nodes()
        self.assertTrue(window.save_snippet())
        self.assertEqual(self.popup()._network_state()["nodes"], [])

    def test_popup_save_shortcut_saves_the_snippet(self):
        main_save = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+S"), self.parent)
        main_save.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        main_calls = []
        main_save.activated.connect(lambda: main_calls.append(True))
        self.parent.show()
        window = self.popup()
        self.edit(window)
        window.activateWindow()
        window.view.setFocus()
        self.app.processEvents()
        QtTest.QTest.keyClick(window.view, QtCore.Qt.Key_S, QtCore.Qt.ControlModifier)
        self.app.processEvents()
        self.assertFalse(window._refresh_dirty())
        self.assertFalse(main_calls)
        self.assertEqual(self.store.tool_by_id(self.tool["id"])["payload"]["nodes"][0]["params"][0]["value"], "after")

    def test_shelf_opens_one_popup_and_protects_main_app_close(self):
        with patch("echograph.ui.shelf_bar.ShelfToolsStore", return_value=self.store):
            shelf = ShelfBar(self.parent)
        self.parent.show()
        before = deepcopy(self.parent.scene.to_dict())
        shelf._run_tool_by_id(self.tool["id"])
        window = shelf._snippet_windows[self.tool["id"]]
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.windows.append(window)
        self.app.processEvents()
        window.showMinimized()
        shelf._run_tool_by_id(self.tool["id"])
        self.assertIs(shelf._snippet_windows[self.tool["id"]], window)
        self.assertFalse(window.isMinimized())
        self.assertEqual(self.parent.scene.to_dict(), before)
        self.edit(window)
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Cancel):
            self.assertFalse(self.parent.close())
        self.assertTrue(self.parent.isVisible())
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save):
            self.assertTrue(self.parent.close())
        self.assertFalse(shelf._snippet_windows)
        self.assertEqual(self.store.tool_by_id(self.tool["id"])["payload"]["nodes"][0]["params"][0]["value"], "after")

    def test_popup_returns_with_main_window_and_preserves_main_editor_focus(self):
        main_edit = QtWidgets.QLineEdit(self.parent)
        self.parent.setCentralWidget(main_edit)
        with patch("echograph.ui.shelf_bar.ShelfToolsStore", return_value=self.store):
            shelf = ShelfBar(self.parent)
        self.parent.show()
        shelf._run_tool_by_id(self.tool["id"])
        window = shelf._snippet_windows[self.tool["id"]]
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.windows.append(window)
        self.app.processEvents()
        self.assertEqual(window.windowType(), QtCore.Qt.Dialog)
        self.assertEqual(window.windowFlags() & QtCore.Qt.WindowMinMaxButtonsHint, QtCore.Qt.WindowMinMaxButtonsHint)
        self.assertFalse(window.isModal())
        self.assertIs(window.windowHandle().transientParent(), self.parent.windowHandle())
        self.assertFalse(window.windowFlags() & QtCore.Qt.WindowStaysOnTopHint)

        other_app = QtWidgets.QWidget()
        self.addCleanup(other_app.close)
        other_app.show()
        other_app.activateWindow()
        self.app.processEvents()
        self.parent.showMinimized()
        self.app.processEvents()
        self.parent.showNormal()
        self.parent.activateWindow()
        main_edit.setFocus()
        self.app.processEvents()

        self.assertTrue(window.isVisible())
        self.assertFalse(window.isMinimized())
        self.assertIs(self.app.activeWindow(), self.parent)
        self.assertIs(self.app.focusWidget(), main_edit)
        self.assertEqual(window.scene._nodes_by_name["Source"].code, "original")

    def test_legacy_snippet_still_pastes_into_main_graph(self):
        self.store.update_tool(self.tool["id"], {"open_mode": "current_graph"})
        with patch("echograph.ui.shelf_bar.ShelfToolsStore", return_value=self.store):
            shelf = ShelfBar(self.parent)
        shelf._run_tool_by_id(self.tool["id"])
        self.assertFalse(shelf._snippet_windows)
        self.assertEqual(set(self.parent.scene._node_items), {"MainGraph", "Source", "Target"})

    def test_index_links_to_an_ordinary_workflow_in_shelf_directory(self):
        index = json.loads(self.store.path.read_text(encoding="utf-8"))
        entry = index["tools"][0]
        self.assertNotIn("payload", entry)
        self.assertEqual(entry["path_mode"], "shelf_relative")
        path = self.store.snippet_workflow_path(entry)
        self.assertEqual(path.parent, self.store.path.parent.resolve())
        workflow = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("format", workflow)
        self.assertNotIn("centroid", workflow)
        for key in ("nodes", "edges", "comments"):
            self.assertEqual(workflow[key], self.payload[key])
        self.assertIn("settings", workflow)

    def test_legacy_index_migration_preserves_all_tools_and_backs_up_original(self):
        old_path = self.store.path.parent / "old_shelf_tools.json"
        old = {"schema_version": 1, "tools": [
            {"id": "script", "type": "python_code", "label": "Script", "code": "print('keep')"},
            {"id": "network", "type": "graph_snippet", "label": "Subnet", "payload": self.payload},
        ]}
        old_path.write_text(json.dumps(old), encoding="utf-8")
        new_path = self.store.path.parent / "shelf" / "shelf_tools.json"
        migrated = ShelfToolsStore(new_path, legacy_path=old_path)
        migrated.load()
        self.assertFalse(migrated.load_error)
        self.assertFalse(old_path.exists())
        self.assertEqual(json.loads((new_path.parent / "shelf_tools.legacy.json").read_text(encoding="utf-8")), old)
        self.assertEqual(migrated.tool_by_id("script")["code"], "print('keep')")
        network = migrated.tool_by_id("network")
        self.assertEqual(network["open_mode"], "current_graph")
        workflow = migrated.load_snippet_workflow(network)
        for key in ("nodes", "edges", "comments"):
            self.assertEqual(workflow[key], self.payload[key])
        reloaded = ShelfToolsStore(new_path)
        reloaded.load()
        self.assertFalse(reloaded.load_error)
        self.assertEqual(reloaded.tool_by_id("network")["payload"]["nodes"], self.payload["nodes"])

    def test_failed_migration_preserves_original_index(self):
        old_path = self.store.path.parent / "old_shelf_tools.json"
        old_path.write_text(json.dumps({"schema_version": 1, "tools": [self.tool | {"workflow_path": ""}]}), encoding="utf-8")
        before = old_path.read_bytes()
        new_path = self.store.path.parent / "shelf" / "shelf_tools.json"
        migrated = ShelfToolsStore(new_path, legacy_path=old_path)
        with patch.object(Path, "replace", side_effect=PermissionError("locked")):
            migrated.load()
        self.assertTrue(migrated.load_error)
        self.assertEqual(old_path.read_bytes(), before)
        self.assertFalse(new_path.exists())

    def test_duplicate_has_its_own_workflow_and_does_not_overwrite_original(self):
        duplicate = self.store.duplicate_tool(self.tool["id"])
        self.assertNotEqual(duplicate["workflow_path"], self.tool["workflow_path"])
        changed = deepcopy(self.payload)
        changed["nodes"][0]["code"] = "duplicate only"
        self.store.update_tool(duplicate["id"], {"payload": changed})
        self.assertEqual(self.store.load_snippet_workflow(self.tool)["nodes"][0]["code"], "original")
        self.assertEqual(self.store.load_snippet_workflow(duplicate)["nodes"][0]["code"], "duplicate only")

    def test_linked_file_edits_are_loaded_and_metadata_saves_do_not_overwrite_them(self):
        path = self.store.snippet_workflow_path(self.tool)
        workflow = self.store.load_snippet_workflow(self.tool)
        workflow["nodes"][0]["code"] = "edited in standard workflow editor"
        workflow["custom_workflow_metadata"] = {"preserved": True}
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.store.update_tool(self.tool["id"], {"label": "Renamed"})
        window = self.popup()
        self.assertEqual(window.scene._nodes_by_name["Source"].code, workflow["nodes"][0]["code"])
        self.edit(window)
        self.assertTrue(window.save_snippet())
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["custom_workflow_metadata"], {"preserved": True})

    def test_missing_link_reports_error_and_does_not_paste_an_empty_network(self):
        self.store.snippet_workflow_path(self.tool).unlink()
        with patch("echograph.ui.shelf_bar.ShelfToolsStore", return_value=self.store), \
                patch.object(QtWidgets.QMessageBox, "warning"):
            shelf = ShelfBar(self.parent)
            self.assertTrue(self.store.load_error)
            self.store.update_tool(self.tool["id"], {"open_mode": "current_graph"})
            shelf._run_tool_by_id(self.tool["id"])
        self.assertEqual(list(self.parent.scene._node_items), ["MainGraph"])


if __name__ == "__main__":
    unittest.main()
