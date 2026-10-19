"""Edit and delete Gantt tasks through the UI and their connected Note nodes."""
from copy import deepcopy
from datetime import date, timedelta
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.ui.view import GraphView
from nodes import note, gantt_chart
from nodes.gantt_chart import spec
from tests.ui.test_shelf_snippet_window import real_graph_scene


class GanttNoteEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        note.register()
        gantt_chart.register()
        cls.Scene = real_graph_scene()

    def setUp(self):
        self.scene = self.Scene()
        self.scene.add_node(GraphNode("Planning", kind="note", params=[
            {"name": "Task", "value": "First line\nSecond line"},
            {"name": "Keep", "value": "Keep this note"}]), (0, 0))
        self.scene.add_node(GraphNode("Delivery", kind="note", params=[
            {"name": "Task", "value": "Other group's note"}]), (100, 400))
        self.scene.add_node(GraphNode("Chart", kind="gantt_chart"), (800, 0))
        self.scene.add_edge("Planning", "Chart")
        self.scene.add_edge("Delivery", "Chart")
        self.view = GraphView(self.scene)
        self.view.resize(1500, 800)
        self.view.show()
        self.view.centerOn(self.scene._node_items["Chart"])
        self.pump()
        self.chart = next(proxy.widget() for proxy in self.scene._node_items["Chart"]._plugin_proxies
                          if isinstance(proxy.widget(), spec.GanttChartWidget))
        self.header = self.chart._table.verticalHeader()
        self.source = self.scene._node_items["Planning"]
        self.task_id = spec._task_storage_key("Planning", "Task")
        self.addCleanup(self.cleanup)

    def pump(self):
        for _ in range(4):
            self.app.processEvents()

    def cleanup(self):
        self.view.close()
        self.scene.clear()
        self.view.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def row(self, task_id=None):
        return self.chart._row_for_task(task_id or self.task_id)

    def label_pos(self, row):
        return QtCore.QPoint(40, self.header.sectionViewportPosition(row) + self.header.sectionSize(row) // 2)

    def with_dialog(self, trigger, edit):
        errors = []
        visited = []

        def interact():
            dialog = self.app.activeModalWidget()
            try:
                self.assertIsInstance(dialog, spec._GanttAddNoteDialog)
                visited.append(True)
                edit(dialog)
            except BaseException as exc:
                errors.append(exc)
                if dialog is not None:
                    dialog.reject()

        QtCore.QTimer.singleShot(0, interact)
        trigger()
        self.pump()
        if errors:
            raise errors[0]
        self.assertTrue(visited, "The note editor did not open")

    def test_double_click_edits_name_body_and_preserves_schedule(self):
        start = date.today()
        span = (start.isoformat(), (start + timedelta(days=3)).isoformat())
        self.chart._assignments = {self.task_id: span}
        self.chart._task_progress = {self.task_id: 40}
        self.chart._task_notifications = {self.task_id: {"notify_at": "2099-01-01T12:00:00", "message": "Reminder"}}
        self.source.model._featured_params = {"Task"}
        self.source.model._featured_heights = {"Task": 160}
        notification = deepcopy(self.chart._task_notifications[self.task_id])

        def edit(dialog):
            self.assertEqual(dialog.name_edit.text(), "Task")
            self.assertEqual(dialog.body_edit.toPlainText(), "First line\nSecond line")
            self.assertTrue(dialog.apply_button.isDefault())
            dialog.name_edit.setText("Keep")
            self.assertFalse(dialog.apply_button.isEnabled())
            dialog.name_edit.setText("Renamed task")
            dialog.body_edit.setPlainText("Edited body\nStill multiline")
            QtTest.QTest.keyClick(dialog.name_edit, QtCore.Qt.Key_Return)

        self.with_dialog(lambda: QtTest.QTest.mouseDClick(
            self.header.viewport(), QtCore.Qt.LeftButton, pos=self.label_pos(self.row())), edit)
        new_id = spec._task_storage_key("Planning", "Renamed task")
        self.assertEqual(self.source.model.params[0], {"name": "Renamed task", "value": "Edited body\nStill multiline"})
        self.assertEqual(self.chart._assignments[new_id], span)
        self.assertEqual(self.chart._task_progress[new_id], 40)
        self.assertEqual(self.chart._task_notifications[new_id], notification)
        self.assertEqual(self.source.model._featured_params, {"Renamed task"})
        self.assertEqual(self.source.model._featured_heights, {"Renamed task": 160})
        self.assertEqual(self.scene._nodes_by_name["Delivery"].params[0]["name"], "Task")

    def test_enter_opens_selected_task_and_cancel_keeps_values(self):
        QtTest.QTest.mouseClick(self.header.viewport(), QtCore.Qt.LeftButton, pos=self.label_pos(self.row()))
        self.assertTrue(self.chart.hasFocus())
        before = deepcopy(self.scene.to_dict())

        def cancel(dialog):
            dialog.name_edit.setText("Canceled rename")
            dialog.body_edit.setPlainText("Canceled body")
            dialog.reject()

        self.with_dialog(lambda: QtTest.QTest.keyClick(self.view, QtCore.Qt.Key_Enter), cancel)
        self.assertEqual(self.scene.to_dict(), before)

    def test_calendar_double_click_uses_the_same_editor(self):
        def edit(dialog):
            dialog.body_edit.setPlainText("Body from the calendar")
            dialog.apply_button.click()

        rect = self.chart._table.visualItemRect(self.chart._table.item(self.row(), 2))
        self.with_dialog(lambda: QtTest.QTest.mouseDClick(
            self.chart._table.viewport(), QtCore.Qt.LeftButton, pos=rect.center()), edit)
        self.assertEqual(self.source.model.params[0]["value"], "Body from the calendar")

    def test_delete_menu_cleans_only_the_corresponding_note_and_schedule(self):
        other_id = spec._task_storage_key("Delivery", "Task")
        span = (date.today().isoformat(), date.today().isoformat())
        self.chart._assignments = {self.task_id: span, other_id: span}
        self.chart._task_progress = {self.task_id: 100, other_id: 20}
        self.source.model._completed_params = {"Task"}
        self.source.model._featured_params = {"Task"}
        self.source.model._featured_heights = {"Task": 160}
        self.chart._task_notifications = {self.task_id: {"notify_at": "2099-01-01T12:00:00", "message": "Reminder"}}
        seen = []

        def choose_delete(menu, _pos):
            self.assertEqual([action.text() for action in menu.actions()], ["Delete Task"])
            seen.append(True)
            return menu.actions()[0]

        menu_type = QtWidgets.QMenu

        def make_menu(parent):
            menu = menu_type(parent)
            menu.exec = lambda pos: choose_delete(menu, pos)
            return menu

        pos = self.label_pos(self.row())
        event = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, pos, self.header.viewport().mapToGlobal(pos))
        with patch.object(spec.QtWidgets, "QMenu", side_effect=make_menu):
            self.app.sendEvent(self.header.viewport(), event)
        self.pump()
        self.assertTrue(seen)
        self.assertEqual(self.source.model.params, [{"name": "Keep", "value": "Keep this note"}])
        self.assertEqual(self.chart._assignments, {other_id: span})
        self.assertEqual(self.chart._task_progress[other_id], 20)
        self.assertNotIn(self.task_id, self.chart._task_progress)
        self.assertNotIn(self.task_id, self.chart._task_notifications)
        self.assertEqual(self.source.model._featured_params, set())
        self.assertEqual(self.source.model._featured_heights, {})
        self.assertEqual(self.source.model._completed_params, set())
        self.assertEqual(self.scene._nodes_by_name["Delivery"].params[0]["value"], "Other group's note")

    def test_dismiss_menu_and_group_context_do_not_delete(self):
        before = deepcopy(self.scene.to_dict())
        menu = QtWidgets.QMenu(self.chart)
        menu.exec = lambda pos: None
        with patch.object(spec.QtWidgets, "QMenu", return_value=menu) as factory:
            self.chart._on_task_context_menu_requested(self.row(), QtCore.QPoint())
            self.assertEqual(factory.call_count, 1)
            self.chart._on_task_context_menu_requested(0, QtCore.QPoint())
            self.assertEqual(factory.call_count, 1)
        self.assertEqual(self.scene.to_dict(), before)

    def test_right_click_task_in_the_graph_canvas_opens_delete_menu(self):
        proxy = next(proxy for proxy in self.scene._node_items["Chart"]._plugin_proxies
                     if proxy.widget() is self.chart)
        local = self.header.viewport().mapTo(self.chart, self.label_pos(self.row()))
        viewport_pos = self.view.mapFromScene(proxy.mapToScene(QtCore.QPointF(local)))
        menu = QtWidgets.QMenu(self.chart)
        menu.exec = lambda pos: menu.actions()[0]
        with patch.object(spec.QtWidgets, "QMenu", return_value=menu) as factory, \
                patch.object(self.scene, "show_create_dialog_at") as create:
            QtTest.QTest.mouseClick(self.view.viewport(), QtCore.Qt.RightButton, pos=viewport_pos)
        self.pump()
        factory.assert_called_once()
        create.assert_not_called()
        self.assertEqual(spec._note_task_names(self.source.model), ["Keep"])

    def test_deletion_does_not_transfer_data_in_another_connected_chart(self):
        self.scene.add_node(GraphNode("Second Chart", kind="gantt_chart"), (800, 500))
        self.scene.add_edge("Planning", "Second Chart")
        self.scene.add_edge("Delivery", "Second Chart")
        self.pump()
        chart_item = self.scene._node_items["Second Chart"]
        chart = next(proxy.widget() for proxy in chart_item._plugin_proxies
                     if isinstance(proxy.widget(), spec.GanttChartWidget))
        other_id = spec._task_storage_key("Delivery", "Task")
        start = date.today()
        deleted_span = (start.isoformat(), start.isoformat())
        kept_span = ((start + timedelta(days=5)).isoformat(), (start + timedelta(days=7)).isoformat())
        spec._write_assignments(chart_item, {other_id: kept_span, self.task_id: deleted_span})
        spec._write_progress_map(chart_item, {other_id: 20, self.task_id: 60})
        self.pump()
        self.chart._delete_task(self.task_id)
        self.pump()
        self.assertEqual(chart._assignments, {other_id: kept_span})
        self.assertEqual(chart._task_progress[other_id], 20)
        self.assertNotIn(self.task_id, chart._task_progress)

    def test_delete_final_note_stays_empty_after_reload_and_can_be_added_to(self):
        task_id = spec._task_storage_key("Delivery", "Task")
        self.chart._delete_task(task_id)
        self.pump()
        self.assertEqual(spec._note_task_names(self.scene._nodes_by_name["Delivery"]), [])
        restored = self.Scene()
        self.addCleanup(restored.clear)
        restored.from_dict(self.scene.to_dict())
        self.pump()
        self.assertEqual(spec._note_task_names(restored._nodes_by_name["Delivery"]), [])
        group_row = next(row for row, entry in enumerate(self.chart._row_entries)
                         if entry == {"kind": "group", "group": "Delivery"})

        def add(dialog):
            dialog.name_edit.setText("Replacement")
            dialog.body_edit.setPlainText("New body")
            dialog.apply_button.click()

        self.with_dialog(lambda: self.chart._on_add_note_requested(group_row), add)
        self.assertEqual(spec._note_task_names(self.scene._nodes_by_name["Delivery"]), ["Replacement"])
        item = self.scene._node_items["Delivery"]
        for proxy in item._param_proxies:
            self.assertGreaterEqual(item.height, proxy.mapRectToParent(proxy.boundingRect()).bottom() + item._PADDING - .01)


if __name__ == "__main__":
    unittest.main()
