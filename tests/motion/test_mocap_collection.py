from __future__ import annotations

import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.persistence import _node_to_dict, serialize_scene
from echograph.ui.node_item import NodeItem
from nodes.mocap_collection import spec
from nodes.priormdm.spec import add_to_collection, MotionControls, set_value

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


class Scene(QtWidgets.QGraphicsScene):
    paramChanged = QtCore.Signal(str, list)

    def __init__(self):
        super().__init__()
        self._node_items = {}
        self._edges = []
        self._scene_asset_revision = 0

    def add_node(self, node, pos):
        node._graph_scene = self
        item = NodeItem(node)
        self.addItem(item)
        item.setPos(pos)
        self._node_items[node.name] = item
        return item

    def add_edge(self, src, dst):
        self._edges.append(SimpleNamespace(src=self._node_items[src], dst=self._node_items[dst]))

    def _unique_node_name(self, base, _kind):
        name, index = base, 1
        while name in self._node_items:
            name, index = f"{base}{index}", index + 1
        return name

    def set_node_params(self, name, params, rebuild=False, emit=True):
        self._node_items[name].model.params = params
        self._scene_asset_revision += 1
        if emit:
            self.paramChanged.emit(name, params)

    def _ordered_in_edges(self, item):
        return [edge for edge in self._edges if edge.dst is item]


class CollectionTests(unittest.TestCase):
    def test_selection_actions_disabled_until_paths_selected(self):
        spec.add_paths(self.model, self.paths, self.scene)
        APP.processEvents()
        for button in (self.widget.relocate_button, self.widget.archive_button):
            self.assertFalse(button.isEnabled())
        self.widget.table.selectRow(0)
        for button in (self.widget.relocate_button, self.widget.archive_button):
            self.assertTrue(button.isEnabled())
        self.widget.table.clearSelection()
        self.widget.refresh()
        for button in (self.widget.relocate_button, self.widget.archive_button):
            self.assertFalse(button.isEnabled())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = [Path(self.temp.name).resolve() / name for name in ("walk.bvh", "turn.BVH", "dance.bvh")]
        for i, path in enumerate(self.paths):
            path.write_text(BVH.replace("{x}", str(i + 1)), encoding="utf-8")
        self.model = GraphNode("Collection", kind="mocap_collection")
        self.scene = Scene()
        self.item = self.scene.add_node(self.model, QtCore.QPointF())
        self.widget = self.item._plugin_proxies[0].widget()

    def tearDown(self):
        self.scene.clear()
        self.scene.deleteLater()
        APP.processEvents()

    def test_multiselect_order_duplicates_and_saved_state(self):
        with patch.object(QtWidgets.QFileDialog, "getOpenFileNames", return_value=([str(p) for p in self.paths], "")):
            self.widget.load_button.click()
        self.assertEqual(spec.checked_paths(self.model), [str(p) for p in self.paths])
        self.widget.table.item(1, 0).setCheckState(QtCore.Qt.Unchecked)
        APP.processEvents()
        self.widget.slider.setValue(2)
        # An unchecked file can still be the active preview/output.
        self.assertEqual(spec.mocap._param_value(self.model, "path"), str(self.paths[1]))
        self.assertEqual(spec.checked_paths(self.model), [str(self.paths[0]), str(self.paths[2])])
        self.assertEqual(spec.add_paths(self.model, [self.paths[1]], self.scene), 0)
        self.assertFalse(spec.entries(self.model)[1]["checked"])
        saved = json.loads(json.dumps(_node_to_dict(self.model)))
        restored = GraphNode(saved["name"], kind=saved["kind"], params=saved["params"])
        self.assertEqual(spec.entries(restored), spec.entries(self.model))
        self.assertEqual(spec.output_number(restored), 2)

    def test_floating_icon_fits_bounds_when_zoomed_out(self):
        from echograph.ui import node_icons
        icon = node_icons._mocap_import_icon() or node_icons._import_icon()
        self.assertIsNotNone(icon)
        for zoom in (1.0, 0.5, 0.25, 0.1):
            size = int(max(40, min(128, 38 / zoom)))
            scaled = icon.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            icon_rect = QtCore.QRectF((self.item.width - scaled.width()) / 2,
                                     -scaled.height() * 0.6, scaled.width(), scaled.height())
            self.assertTrue(self.item.boundingRect().contains(icon_rect), f"Icon clipped at zoom {zoom}")

    def test_project_approvals_restore_and_unapprove_across_reload(self):
        from nodes.mocap_collection import approvals
        self.scene._filename = str(Path(self.temp.name) / "workflow.json")
        rows = [{"path": str(self.paths[0]), "checked": False, "approved": True}]
        spec.save_state(self.model, rows, 1, self.scene)
        path = Path(self.temp.name) / "mocap_approvals.json"
        metadata = json.loads(path.read_text())
        self.assertEqual(metadata["files"]["walk.bvh"], {"approved": True})
        # Even an older workflow snapshot recovers the independent decision.
        stale = [{"path": str(self.paths[0]), "checked": False}]
        restored = approvals.restore(self.scene, stale)
        self.assertTrue(restored[0]["approved"])
        self.assertFalse(restored[0]["checked"])
        restored[0]["approved"] = False
        spec.save_state(self.model, restored, 1, self.scene)
        self.assertFalse(approvals.restore(self.scene, rows)[0]["approved"])
        # A second collection adds records without erasing the first.
        approvals.save(self.scene, [{"path": str(self.paths[1]), "approved": True}])
        self.assertEqual(len(json.loads(path.read_text())["files"]), 2)

    def test_render_assignment_refresh_preserves_approval(self):
        from nodes.mocap_collection import review_data
        spec.save_state(self.model, [{"path": str(self.paths[0]), "checked": True, "approved": True}], 1, self.scene)
        result = {"assignments": {review_data.path_key(self.paths[0]): {"folder": "new-render"}}}
        review_data.apply_assignments(self.model, self.scene, result)
        self.assertTrue(spec.entries(self.model)[0]["approved"])

    def test_relocate_selected_rows_preserves_checks_and_metadata(self):
        root = Path(self.temp.name).resolve() / "new"
        for folder in ("walk", "dance"):
            (root / folder).mkdir(parents=True)
            (root / folder / "preview.bvh").write_text("motion")
        rows = [{"path": "Z:/old/walk/preview.bvh", "checked": False, "approved": True},
                {"path": str(self.paths[1]), "checked": True},
                {"path": "Z:/old/dance/preview.bvh", "checked": True}]
        spec.save_state(self.model, rows, 2, self.scene)
        self.assertEqual(spec.relocate_paths(self.model, [0, 2], root, self.scene), 2)
        updated = spec.entries(self.model)
        self.assertEqual([r["checked"] for r in updated], [False, True, True])
        self.assertTrue(updated[0]["approved"])
        self.assertEqual(updated[1], rows[1])
        self.assertEqual(Path(updated[0]["path"]), root / "walk/preview.bvh")
        self.assertEqual(Path(updated[2]["path"]), root / "dance/preview.bvh")
        self.assertEqual(spec.output_number(self.model), 2)
        (root / "walk/preview.bvh").unlink()
        (root / "other").mkdir()
        (root / "other/preview.bvh").write_text("motion")
        before = spec.entries(self.model)
        with self.assertRaisesRegex(ValueError, "Multiple matching"):
            spec.relocate_paths(self.model, [2, 0], root, self.scene)
        self.assertEqual(spec.entries(self.model), before)

    def test_shift_ctrl_selection_does_not_change_checks(self):
        spec.add_paths(self.model, self.paths, self.scene)
        table = self.widget.table
        selection = table.selectionModel()
        selection.select(table.model().index(0, 3), QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
        selection.select(QtCore.QItemSelection(table.model().index(0, 3), table.model().index(2, 3)),
                         QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
        self.assertEqual(len(selection.selectedRows(3)), 3)
        selection.select(table.model().index(1, 3), QtCore.QItemSelectionModel.Toggle | QtCore.QItemSelectionModel.Rows)
        self.assertEqual([i.row() for i in selection.selectedRows(3)], [0, 2])
        self.assertTrue(all(row["checked"] for row in spec.entries(self.model)))
        self.assertTrue(self.widget.relocate_button.isEnabled())
        self.assertEqual(table.selectionMode(), QtWidgets.QAbstractItemView.ExtendedSelection)

    def test_selected_approved_path_is_purple_with_green_approval_edge(self):
        spec.save_state(self.model, [{"path": str(self.paths[0]), "checked": True, "approved": True}], 1, self.scene)
        table = self.widget.table
        index = table.model().index(0, 3)
        self.assertTrue(index.data(QtCore.Qt.UserRole))
        image = QtGui.QImage(300, 38, QtGui.QImage.Format_ARGB32)
        image.fill(QtCore.Qt.black)
        option = QtWidgets.QStyleOptionViewItem()
        option.rect = QtCore.QRect(0, 0, 300, 38)
        option.state = QtWidgets.QStyle.State_Enabled | QtWidgets.QStyle.State_Selected
        painter = QtGui.QPainter(image)
        table.itemDelegate().paint(painter, option, index)
        painter.end()
        color = image.pixelColor(290, 30)
        self.assertEqual(color.name(), "#554080")
        self.assertEqual(image.pixelColor(150, 0).name(), "#9be8b8")

    def test_navigation_clears_previous_selection_after_approval(self):
        spec.add_paths(self.model, self.paths, self.scene)
        table = self.widget.table
        spec.save_state(self.model, spec.entries(self.model), 1, self.scene)
        table.selectRow(0)
        rows = spec.entries(self.model)
        rows[0]["approved"] = True
        spec.save_state(self.model, rows, 1, self.scene)
        # Review navigation updates the same output parameter.
        for number in (2, 3, 2):
            spec.save_state(self.model, spec.entries(self.model), number, self.scene)
            APP.processEvents()
            selected = table.selectionModel().selectedIndexes()
            self.assertEqual({index.row() for index in selected}, {number - 1})
            self.assertEqual({index.column() for index in selected}, {1, 2, 3})
            for column in range(4):
                self.assertEqual(table.item(0, column).background().color().name(), "#225c39")

    def test_click_unchecked_path_without_render_clears_old_purple(self):
        spec.save_state(self.model, [
            {"path": str(self.paths[0]), "checked": True, "render": {"folder": "render"}},
            {"path": str(self.paths[1]), "checked": False},
            {"path": str(self.paths[2]), "checked": True}], 1, self.scene)
        spec.mocap._set_param_value(self.model, "checked_only", "1")
        widget = spec.MocapCollectionWidget(self.model)
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.close)
        widget.show()
        APP.processEvents()
        table = widget.table
        def click(row, modifiers=QtCore.Qt.NoModifier):
            point = table.visualItemRect(table.item(row, 3)).center()
            QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, modifiers, point)
        click(0)
        click(1)
        self.assertEqual({i.row() for i in table.selectionModel().selectedRows(3)}, {1})
        self.assertEqual(spec.output_number(self.model), 1)
        self.assertEqual(table.item(0, 3).background().style(), QtCore.Qt.NoBrush)
        click(2, QtCore.Qt.ControlModifier)
        self.assertEqual({i.row() for i in table.selectionModel().selectedRows(3)}, {1, 2})
        click(1)
        self.assertEqual({i.row() for i in table.selectionModel().selectedRows(3)}, {1})

    def test_browse_uses_graph_window_and_survives_body_rebuild(self):
        window = QtWidgets.QMainWindow()
        window.setCentralWidget(QtWidgets.QGraphicsView(self.scene))
        self.addCleanup(window.deleteLater)

        def choose(parent, *_args):
            self.assertIs(parent, window)
            self.assertIsNone(parent.graphicsProxyWidget())
            self.item._build_widgets()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            self.assertFalse(spec.isValid(self.widget))
            return [str(path) for path in self.paths[:2]], ""

        with patch.object(QtWidgets.QFileDialog, "getOpenFileNames", side_effect=choose):
            self.widget._browse()
        self.widget = self.item._plugin_proxies[0].widget()
        self.assertEqual(self.widget.table.rowCount(), 2)
        self.assertEqual(spec.checked_paths(self.model), [str(path) for path in self.paths[:2]])

    def test_browse_from_info_card_uses_window_and_cancel_keeps_files(self):
        window = QtWidgets.QMainWindow()
        self.addCleanup(window.deleteLater)
        card = spec.MocapCollectionWidget(self.model, parent=window)
        spec.add_paths(self.model, self.paths[:1], self.scene)
        with patch.object(QtWidgets.QFileDialog, "getOpenFileNames", return_value=([], "")) as picker:
            card._browse()
        self.assertIs(picker.call_args.args[0], window)
        self.assertEqual(spec.checked_paths(self.model), [str(self.paths[0])])

    def test_slider_highlight_sync_and_node_bounds(self):
        spec.add_paths(self.model, self.paths, self.scene)
        rows = spec.entries(self.model)
        rows[0]["approved"] = True
        spec.save_state(self.model, rows, 3, self.scene)
        card_widget = spec.MocapCollectionWidget(self.model, self.scene)
        self.addCleanup(card_widget.deleteLater)
        spy = QtTest.QSignalSpy(self.scene.paramChanged)
        self.widget.slider.setValue(1)
        self.assertEqual(spy.count(), 1)
        self.assertEqual(card_widget.number.value(), 1)
        self.assertTrue(self.widget.table.item(0, 2).isSelected())
        self.assertTrue(card_widget.table.item(0, 3).isSelected())
        self.assertTrue(card_widget.table.item(0, 3).data(QtCore.Qt.UserRole))
        self.assertEqual(card_widget.table.item(0, 0).background().color().name(), "#338253")
        # Re-selecting the active output restores the same manual-click highlight.
        self.widget.table.clearSelection()
        self.widget._select(1)
        self.assertTrue(self.widget.table.item(0, 3).isSelected())
        self.assertEqual(spy.count(), 1)
        self.assertNotEqual(self.widget.table.item(2, 2).background().color(), QtGui.QColor("#554080"))
        self.item._recompute_height()
        self.item._build_widgets()
        proxy = self.item._plugin_proxies[0]
        self.assertGreaterEqual(self.item.height, proxy.pos().y() + proxy.size().height() + 10)
        self.assertGreaterEqual(proxy.size().height(), proxy.widget().minimumSizeHint().height())
        self.assertGreaterEqual(self.item.width, proxy.widget().minimumSizeHint().width())
        self.assertTrue(proxy.widget().rect().contains(proxy.widget().number.geometry()))

    def test_folder_load_includes_nested_bvhs_in_order_without_duplicates(self):
        nested = Path(self.temp.name).resolve() / "nested" / "more"
        nested.mkdir(parents=True)
        nested_bvh = nested / "jump.BVH"
        nested_bvh.write_text(BVH.replace("{x}", "4"))
        (nested / "ignore.txt").write_text("not motion")
        (nested / "ignore.bvh.bak").write_text("backup")
        spec.add_paths(self.model, self.paths[:1], self.scene)
        self.widget.none_button.click()
        with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", return_value=self.temp.name):
            self.widget.load_folder_button.click()
        self.assertEqual([row["path"] for row in spec.entries(self.model)],
                         [str(self.paths[0]), str(self.paths[2]), str(nested_bvh), str(self.paths[1])])
        self.assertFalse(spec.entries(self.model)[0]["checked"])
        self.assertEqual(self.widget.table.rowCount(), 4)
        self.assertEqual(self.widget.slider.maximum(), 4)
        with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", return_value=self.temp.name):
            self.widget.load_folder_button.click()
        self.assertEqual(self.widget.table.rowCount(), 4)

    def test_folder_picker_survives_rebuild_and_is_available_in_info_card(self):
        window = QtWidgets.QMainWindow()
        window.setCentralWidget(QtWidgets.QGraphicsView(self.scene))
        self.addCleanup(window.deleteLater)
        card = QtWidgets.QWidget(window)
        card._node_ref, card._graph_scene = self.model, self.scene
        footer = QtWidgets.QVBoxLayout(card)
        self.assertTrue(spec.augment_infocard_footer(card, footer))
        controls = card.findChild(spec.MocapCollectionWidget)
        self.assertEqual(controls.load_folder_button.text(), "Load BVH folder")
        self.assertEqual(controls.load_button.text(), "Load BVH files")

        def choose(parent, *_args):
            self.assertIs(parent, window)
            self.item._build_widgets()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            return self.temp.name

        with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", side_effect=choose):
            self.widget._browse_folder()
        self.widget = self.item._plugin_proxies[0].widget()
        self.assertEqual(self.widget.table.rowCount(), 3)
        self.assertEqual(controls.table.rowCount(), 3)

    def test_empty_cancelled_and_unreadable_folder_keep_collection(self):
        spec.add_paths(self.model, self.paths[:1], self.scene)
        original = list(spec.entries(self.model))
        empty = Path(self.temp.name) / "empty"
        empty.mkdir()
        with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", return_value=""), \
             patch.object(spec, "bvh_paths_in_folder") as scan:
            self.widget._browse_folder()
            scan.assert_not_called()
        with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", return_value=str(empty)), \
             patch.object(QtWidgets.QMessageBox, "information") as message:
            self.widget._browse_folder()
            message.assert_called_once()
        with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", return_value=str(empty)), \
             patch.object(spec.os, "walk", side_effect=PermissionError("Access denied")), \
             patch.object(QtWidgets.QMessageBox, "warning") as warning:
            self.widget._browse_folder()
            warning.assert_called_once()
        self.assertEqual(spec.entries(self.model), original)

    def test_clear_checks_and_remove_last_file(self):
        spec.add_paths(self.model, self.paths[:1], self.scene)
        self.widget.none_button.click()
        self.assertEqual(spec.checked_paths(self.model), [])
        self.assertEqual(spec.output_number(self.model), 1)
        self.widget.all_button.click()
        self.assertEqual(len(spec.checked_paths(self.model)), 1)
        self.widget.remove_button.click()
        self.assertEqual(spec.output_number(self.model), 0)
        self.assertEqual(spec.mocap._param_value(self.model, "path"), "")
        self.assertFalse(self.widget.slider.isEnabled())
        self.assertFalse(self.widget.view_button.isEnabled())

    def test_sliding_reuses_table_cells_and_updates_highlight(self):
        spec.add_paths(self.model, self.paths, self.scene)
        cells = [[self.widget.table.item(row, column) for column in range(3)] for row in range(len(self.paths))]
        self.widget.slider.setValue(2)
        self.widget.slider.setValue(3)
        for row, row_cells in enumerate(cells):
            for column, cell in enumerate(row_cells):
                self.assertIs(self.widget.table.item(row, column), cell)
        self.assertTrue(cells[2][2].isSelected())
        self.assertEqual(cells[1][2].background().style(), QtCore.Qt.NoBrush)

    def test_checked_only_skips_unchecked_rows_and_preserves_original_highlight(self):
        spec.add_paths(self.model, self.paths, self.scene)
        card = spec.MocapCollectionWidget(self.model, self.scene)
        self.addCleanup(card.deleteLater)
        self.widget.table.item(1, 0).setCheckState(QtCore.Qt.Unchecked)
        APP.processEvents()
        self.widget.slider.setValue(2)  # Start on a file excluded by the filter.
        self.widget.checked_only.setChecked(True)
        self.assertTrue(card.checked_only.isChecked())
        self.assertEqual(self.widget.slider.maximum(), 2)
        self.assertEqual(self.widget.number.value(), 2)
        self.assertEqual(spec.output_number(self.model), 3)
        self.assertEqual(spec.mocap._param_value(self.model, "path"), str(self.paths[2]))
        self.widget.slider.setValue(1)
        self.assertEqual(spec.output_number(self.model), 1)
        self.widget.number.setValue(2)
        self.assertEqual(spec.output_number(self.model), 3)
        self.assertTrue(self.widget.table.item(2, 2).isSelected())
        self.widget._clicked(1, 2)  # Clicking an unchecked path cannot bypass the filter.
        self.assertEqual(spec.output_number(self.model), 3)
        self.widget.checked_only.setChecked(False)
        self.assertEqual(self.widget.slider.maximum(), 3)
        self.assertEqual(self.widget.slider.value(), 3)
        self.widget.slider.setValue(2)
        self.assertEqual(spec.mocap._param_value(self.model, "path"), str(self.paths[1]))

    def test_checked_only_empty_output_recovers_and_persists(self):
        spec.add_paths(self.model, self.paths, self.scene)
        self.widget.checked_only.setChecked(True)
        self.widget.none_button.click()
        self.assertEqual(spec.output_number(self.model), 0)
        self.assertEqual(spec.mocap._param_value(self.model, "path"), "")
        self.assertFalse(self.widget.slider.isEnabled())
        self.assertFalse(self.widget.view_button.isEnabled())
        self.assertTrue(self.widget.all_button.isEnabled())
        self.widget.table.item(1, 0).setCheckState(QtCore.Qt.Checked)
        APP.processEvents()
        self.assertEqual(self.widget.number.maximum(), 1)
        self.assertEqual(spec.output_number(self.model), 2)
        saved = _node_to_dict(self.model)
        restored = GraphNode("Restored", kind="mocap_collection", params=saved["params"])
        self.assertTrue(spec.mocap._param_bool(restored, "checked_only"))
        self.assertEqual(spec.sync_output(restored), str(self.paths[1]))
        self.widget.remove_button.click()
        self.assertEqual(spec.output_number(self.model), 0)
        spec.add_paths(self.model, [self.paths[1]], self.scene)
        self.assertEqual(spec.output_number(self.model), 3)
        self.assertEqual(self.widget.number.value(), 1)

    def test_checked_only_checkbox_changes_update_live_output(self):
        spec.add_paths(self.model, self.paths, self.scene)
        self.widget.checked_only.setChecked(True)
        self.widget.slider.setValue(1)
        self.widget.live_view.setChecked(True)
        self.widget._live_timer.stop()
        self.widget.table.item(0, 0).setCheckState(QtCore.Qt.Unchecked)
        APP.processEvents()
        self.assertEqual(spec.output_number(self.model), 2)
        self.assertTrue(self.widget._live_timer.isActive())
        self.widget.none_button.click()
        self.assertEqual(spec.output_number(self.model), 0)
        self.assertFalse(self.widget._live_timer.isActive())

    def test_live_view_number_typing_waits_for_enter_or_focus_loss(self):
        paths = [Path(self.temp.name)/f'take_{i:03d}.bvh' for i in range(128)]
        for path in paths:
            path.write_text(BVH.replace('{x}', '1'), encoding='utf-8')
        spec.add_paths(self.model, paths, self.scene)
        self.widget.number.setValue(1)
        with patch.object(self.widget, '_view') as preview:
            self.widget.live_view.setChecked(True)
            QtTest.QTest.qWait(140)
            preview.reset_mock()
            changes = QtTest.QSignalSpy(self.scene.paramChanged)
            edit = self.widget.number.lineEdit()
            edit.selectAll()
            for digit in '128':
                QtTest.QTest.keyClicks(edit, digit)
                QtTest.QTest.qWait(140)
                self.assertEqual(spec.output_number(self.model), 1)
                self.assertEqual(changes.count(), 0)
                preview.assert_not_called()
            self.assertEqual(edit.text(), '128')
            QtTest.QTest.keyClick(self.widget.number, QtCore.Qt.Key_Return)
            QtTest.QTest.qWait(140)
            self.assertEqual(spec.output_number(self.model), 128)
            self.assertEqual(self.widget.slider.value(), 128)
            self.assertEqual(changes.count(), 1)
            preview.assert_called_once_with(live=True, frame=False)

            preview.reset_mock()
            edit.selectAll()
            QtTest.QTest.keyClick(edit, QtCore.Qt.Key_Backspace)
            QtTest.QTest.qWait(140)
            self.assertEqual(spec.output_number(self.model), 128)
            self.assertEqual(changes.count(), 1)
            preview.assert_not_called()
            QtTest.QTest.keyClicks(edit, '42')
            QtTest.QTest.qWait(140)
            self.assertEqual(spec.output_number(self.model), 128)
            preview.assert_not_called()
            APP.sendEvent(self.widget.number, QtGui.QFocusEvent(QtCore.QEvent.FocusOut))
            QtTest.QTest.qWait(140)
            self.assertEqual(spec.output_number(self.model), 42)
            self.assertEqual(self.widget.slider.value(), 42)
            self.assertEqual(changes.count(), 2)
            preview.assert_called_once_with(live=True, frame=False)

    def test_live_view_updates_selected_clip_and_preserves_camera(self):
        window = QtWidgets.QMainWindow()
        window.setCentralWidget(QtWidgets.QGraphicsView(self.scene))
        window.open_scene_assets = Mock(return_value=True)
        self.addCleanup(window.deleteLater)
        card_controls = spec.MocapCollectionWidget(self.model, self.scene)
        self.addCleanup(card_controls.deleteLater)
        spec.add_paths(self.model, self.paths, self.scene)
        self.widget.slider.setValue(1)
        QtTest.QTest.qWait(140)
        window.open_scene_assets.assert_not_called()
        self.widget.live_view.setChecked(True)
        QtTest.QTest.qWait(140)
        self.assertTrue(card_controls.live_view.isChecked())
        self.assertTrue(self.widget.slider.hasTracking())
        self.assertEqual(window.open_scene_assets.call_count, 1)
        self.assertTrue(window.open_scene_assets.call_args.kwargs["frame"])
        window.open_scene_assets.reset_mock()
        self.widget.slider.setValue(2)
        self.widget.slider.setValue(3)
        QtTest.QTest.qWait(140)
        self.assertEqual(window.open_scene_assets.call_count, 1)
        asset = window.open_scene_assets.call_args.args[0][0]
        self.assertEqual(Path(asset["path"]), self.paths[2])
        self.assertIsNotNone(asset["fbx_rig_context"]["clip"])
        self.assertFalse(window.open_scene_assets.call_args.kwargs["frame"])
        saved = _node_to_dict(self.model)
        restored = GraphNode("Restored", kind="mocap_collection", params=saved["params"])
        self.assertTrue(spec.mocap._param_bool(restored, "live_view"))
        window.open_scene_assets.reset_mock()
        self.widget.slider.setValue(2)
        card_controls.live_view.setChecked(False)
        QtTest.QTest.qWait(140)
        window.open_scene_assets.assert_not_called()
        self.assertFalse(self.widget.live_view.isChecked())
        self.widget.view_button.click()
        self.assertEqual(window.open_scene_assets.call_count, 1)
        self.assertTrue(window.open_scene_assets.call_args.kwargs["frame"])

    def test_live_view_missing_file_does_not_interrupt_slider_with_dialog(self):
        spec.add_paths(self.model, self.paths, self.scene)
        self.paths[2].unlink()
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            self.widget.live_view.setChecked(True)
            QtTest.QTest.qWait(140)
            warning.assert_not_called()
        self.assertIn("Cannot preview", self.widget.status.text())

    def test_info_card_path_list_fills_available_width(self):
        from echograph.ui.infocard import InfoCard
        card = InfoCard(self.model)
        self.addCleanup(card.deleteLater)
        controls = card.findChild(spec.MocapCollectionWidget)
        card.show()
        for width in (900, 1200):
            card.resize(width, 600)
            APP.processEvents()
            margins = card.layout().contentsMargins()
            self.assertGreaterEqual(controls.width(), card.width() - margins.left() - margins.right() - 2)
            inner = controls.layout().contentsMargins()
            self.assertEqual(controls.table.width(), controls.width() - inner.left() - inner.right())

    def test_border_drag_resizes_list_and_persists_dimensions(self):
        spec.add_paths(self.model, self.paths, self.scene)
        view = QtWidgets.QGraphicsView(self.scene)
        self.addCleanup(view.deleteLater)
        view.setSceneRect(0, 0, 1600, 1200)
        view.resize(1200, 900)
        view.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.item.setPos(40, 40)
        self.item.setSelected(True)
        view.show()
        APP.processEvents()
        width, height = self.item.width, self.item.height
        old_table_height = self.widget.table.height()
        start = view.mapFromScene(self.item.mapToScene(QtCore.QPointF(width - 2, height - 2)))
        end = start + QtCore.QPoint(170, 120)
        QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
        self.assertEqual(self.item._note_resize_mode, "bottom-right")
        QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
        APP.processEvents()
        self.assertAlmostEqual(self.item.width, width + 170)
        self.assertAlmostEqual(self.item.height, height + 120)
        self.widget = self.item._plugin_proxies[0].widget()
        self.assertGreater(self.widget.table.height(), old_table_height)
        self.assertGreaterEqual(self.item.height, self.item._plugin_proxies[0].geometry().bottom() + 10)
        saved = json.loads(json.dumps(_node_to_dict(self.model)))
        restored = NodeItem(GraphNode("Restored", kind="mocap_collection", params=saved["params"]))
        self.assertEqual((restored.width, restored.height), (self.item.width, self.item.height))
        self.assertEqual(spec.checked_paths(restored.model), [str(path) for path in self.paths])

    def test_resize_left_top_and_minimum_bounds(self):
        self.item.setSelected(True)
        self.item.setPos(80, 70)
        right = self.item.pos().x() + self.item.width
        bottom = self.item.pos().y() + self.item.height
        self.assertEqual(self.item._note_hit_test(QtCore.QPointF(1, 1)), "top-left")
        self.item._begin_note_resize("top-left", QtCore.QPointF(1, 1))
        self.item._apply_note_resize(QtCore.QPointF(-39, -29))
        self.item._finish_note_resize()
        APP.processEvents()
        self.assertAlmostEqual(self.item.pos().x() + self.item.width, right)
        self.assertAlmostEqual(self.item.pos().y() + self.item.height, bottom)
        self.assertEqual(self.item.pos(), QtCore.QPointF(40, 40))
        self.item._begin_note_resize("bottom-right", QtCore.QPointF(self.item.width, self.item.height))
        self.item._apply_note_resize(QtCore.QPointF(-1000, -1000))
        self.item._finish_note_resize()
        APP.processEvents()
        self.assertGreaterEqual(self.item.width, self.item._mocap_collection_min_w)
        self.assertGreaterEqual(self.item.height, self.item._mocap_collection_min_h)
        proxy = self.item._plugin_proxies[0]
        self.assertGreaterEqual(proxy.pos().x(), 8)
        self.assertGreaterEqual(self.item.width - proxy.geometry().right(), 8)
        self.assertGreaterEqual(proxy.widget().table.height(), 100)
        self.assertTrue(proxy.widget().rect().contains(proxy.widget().number.geometry()))

    def test_invalid_add_is_atomic_and_missing_output_does_not_reuse_clip(self):
        spec.add_paths(self.model, self.paths[:1], self.scene)
        with self.assertRaises(ValueError):
            spec.add_paths(self.model, [self.paths[1], Path(self.temp.name) / "missing.bvh"], self.scene)
        self.assertEqual(len(spec.entries(self.model)), 1)
        from nodes.scene.spec import _mocap_import_rig_context
        self.assertIsNotNone(_mocap_import_rig_context(self.model))
        self.paths[0].unlink()
        result = spec.mocap.resolve_mocap_import_source(self.item, persist=True, validate_animation=True)
        self.assertEqual(result.status, "error")
        self.assertIsNone(self.model._mocap_animation_result)
        self.assertIsNone(_mocap_import_rig_context(self.model))

    def test_selected_bvh_reaches_scene_transforms_and_retarget(self):
        from nodes.scene.spec import _collect_assets
        from nodes.transforms.spec import _mocap_import_resolved_path
        from nodes.anim_retarget.spec import _context_for_source_item
        spec.add_paths(self.model, self.paths, self.scene)
        scene_item = SimpleNamespace(model=GraphNode("Scene", kind="scene"), scene=lambda: self.scene)
        self.scene._edges.append(SimpleNamespace(src=self.item, dst=scene_item, dst_port_name=None))
        old_clip = None
        for number in (1, 3, 2):
            self.widget.slider.setValue(number)
            errors, warnings = [], []
            context = _context_for_source_item(self.item, "mocap_collection", errors, warnings)
            self.assertEqual(errors, [])
            self.assertEqual(Path(context["path"]), self.paths[number - 1])
            self.assertIsNot(context["clip"], old_clip)
            old_clip = context["clip"]
            self.assertEqual(Path(_mocap_import_resolved_path(self.item)), self.paths[number - 1])
            assets = _collect_assets(scene_item)
            self.assertEqual(len(assets), 1)
            self.assertEqual(Path(assets[0]["path"]), self.paths[number - 1])
            self.assertIsNotNone(assets[0]["fbx_rig_context"]["clip"])

    def test_relative_path_resolves_from_workflow(self):
        self.scene._filename = str(Path(self.temp.name) / "workflow.json")
        spec.add_paths(self.model, ["walk.bvh"], self.scene)
        result = spec.mocap.resolve_mocap_import_source(self.item, validate_animation=True)
        self.assertEqual(result.status, "ok")
        self.assertEqual(Path(result.resolved_path), self.paths[0])

    def test_priormdm_creates_connects_reuses_and_saves_collection(self):
        prior = GraphNode("Generator", kind="priormdm")
        with patch("nodes.priormdm.setup_ui.schedule_setup_offer"):
            self.scene.add_node(prior, QtCore.QPointF(100, 100))
        set_value(prior, self.scene, "last_bvh", self.paths[0])
        created = add_to_collection(prior, self.scene)[0]
        self.assertEqual(created.kind, "mocap_collection")
        self.assertEqual(len(self.scene._edges), 1)
        self.assertIs(self.scene._edges[0].src.model, prior)
        self.assertIs(self.scene._edges[0].dst.model, created)
        set_value(prior, self.scene, "last_bvh", self.paths[1])
        self.assertIs(add_to_collection(prior, self.scene)[0], created)
        add_to_collection(prior, self.scene)
        self.assertEqual(len(spec.entries(created)), 2)
        self.assertEqual(len(self.scene._edges), 1)
        self.assertEqual(spec.entries(self.model), [])  # Unconnected collections stay independent.
        controls = MotionControls(prior, self.scene)
        self.addCleanup(controls.deleteLater)
        self.assertTrue(controls.collection_button.isEnabled())
        set_value(prior, self.scene, "last_bvh", self.paths[2])
        controls.collection_button.click()
        self.assertEqual(len(spec.entries(created)), 3)
        saved = json.loads(json.dumps(serialize_scene(self.scene)))
        self.assertIn({"src": prior.name, "dst": created.name}, saved["edges"])
        collection_data = next(row for row in saved["nodes"] if row["name"] == created.name)
        restored = GraphNode(created.name, kind=created.kind, params=collection_data["params"])
        self.assertEqual(spec.checked_paths(restored), [str(p) for p in self.paths])
        self.assertEqual(spec.output_number(restored), 3)

    def test_priormdm_uses_manually_connected_collection_and_rejects_missing_result(self):
        prior = GraphNode("Generator", kind="priormdm")
        with patch("nodes.priormdm.setup_ui.schedule_setup_offer"):
            self.scene.add_node(prior, QtCore.QPointF())
        with self.assertRaises(ValueError):
            add_to_collection(prior, self.scene)
        self.assertEqual(len(self.scene._edges), 0)
        self.scene.add_edge(prior.name, self.model.name)
        set_value(prior, self.scene, "last_bvh", self.paths[0])
        self.assertIs(add_to_collection(prior, self.scene)[0], self.model)
        self.assertEqual(len(self.scene._node_items), 2)
        self.assertEqual(spec.checked_paths(self.model), [str(self.paths[0])])


if __name__ == "__main__":
    unittest.main()
