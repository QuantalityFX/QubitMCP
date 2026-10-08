import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.persistence import _node_to_dict, serialize_scene
from echograph.patterns.document import PatternDocument
from echograph.patterns.geometry import bounds, validate_piece
from echograph.patterns.serialization import load_document, save_document
from echograph.ui.node_item import NodeItem
from echograph.ui.pattern_editor.canvas import HandleItem
from echograph.ui.pattern_editor.editor import PatternEditor
from nodes.pattern_design.spec import PatternNodeWidget, resolve_pattern_document


class PatternEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = (Path(self.temp.name) / "design.pattern.json").resolve()
        save_document(self.path, PatternDocument())
        self.editor = PatternEditor(self.path)
        self.editor.show()
        self.app.processEvents()

    def tearDown(self):
        self.editor.canvas.cancel_outline()
        self.editor.history.mark_saved()
        self.editor.close()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def test_panel_reorder_preserves_symmetry_selection_and_saved_order(self):
        from echograph.patterns.relationships import symmetry_for_piece
        editor = self.editor
        editor.add_template("Front")
        front = editor.selected_id
        editor.add_template("Sleeve")
        sleeve = editor.selected_id
        editor.add_template("Back")
        back = editor.selected_id
        editor.select_piece(front)
        editor.duplicate(True)
        mirrored = editor.selected_id
        self.assertEqual(editor.pieces.panel_ids(), [front, mirrored, sleeve, back])

        # Clicking another row must leave the drag source and custom row intact.
        source = editor.pieces.item(1)
        widget = editor.pieces.itemWidget(source)
        editor.pieces.setCurrentRow(0)
        self.assertIs(editor.pieces.item(1), source)
        self.assertIs(editor.pieces.itemWidget(source), widget)
        editor.pieces.setCurrentRow(1)

        # Exercise the post-drop path with Qt's item move, including the row
        # removal selection signals that must be blocked until the drop ends.
        def move_row(event):
            item = editor.pieces.takeItem(1)
            editor.pieces.insertItem(3, item)
        with patch.object(QtWidgets.QListWidget, "dropEvent", side_effect=move_row):
            editor.pieces.dropEvent(None)
        order = [front, sleeve, back, mirrored]
        self.assertEqual(editor.pieces.panel_ids(), order)
        self.assertEqual([p.id for p in editor.document.pieces], order)
        self.assertEqual(editor.selected_id, mirrored)
        self.assertTrue(symmetry_for_piece(editor.document, mirrored)["enabled"])
        self.assertFalse(editor._symmetry_buttons[mirrored].icon().isNull())
        editor.save()
        self.assertEqual([p.id for p in load_document(self.path)[0].pieces], order)
        editor.undo()
        self.assertEqual(editor.pieces.panel_ids(), [front, mirrored, sleeve, back])
        editor.redo()
        self.assertEqual(editor.pieces.panel_ids(), order)

    def test_ctrl_s_saves_pattern_despite_host_global_shortcut(self):
        editor = self.editor
        host = QtWidgets.QWidget()
        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+S"), host)
        shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
        host_saves = []
        shortcut.activated.connect(lambda: host_saves.append(True))
        host.show()
        try:
            editor.activateWindow()
            editor.add_template("Front")
            editor.canvas.setFocus()
            self.app.processEvents()
            QtTest.QTest.keyClick(editor.canvas, QtCore.Qt.Key.Key_S,
                                 QtCore.Qt.KeyboardModifier.ControlModifier)
            self.assertFalse(editor.history.dirty)
            self.assertEqual(load_document(self.path)[0].pieces[0].name, "Front")
            editor.name_edit.setFocus()
            editor.name_edit.selectAll()
            QtTest.QTest.keyClicks(editor.name_edit, "Renamed front")
            QtTest.QTest.keyClick(editor.name_edit, QtCore.Qt.Key.Key_S,
                                 QtCore.Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(load_document(self.path)[0].pieces[0].name, "Renamed front")
            self.assertFalse(editor.history.dirty)
            self.assertEqual(host_saves, [])
        finally:
            host.close()

    def test_list_range_and_ctrl_toggle_then_canvas_adds_panels(self):
        editor = self.editor
        for name in ("Front", "Sleeve", "Back"):
            editor.add_template(name, 100, 100)
        ids = [piece.id for piece in editor.document.pieces]
        editor.select_piece(ids[0])
        view = editor.pieces.viewport()
        position = lambda row: editor.pieces.visualItemRect(editor.pieces.item(row)).center()
        QtTest.QTest.mouseClick(view, QtCore.Qt.MouseButton.LeftButton, pos=position(0))
        QtTest.QTest.mouseClick(view, QtCore.Qt.MouseButton.LeftButton,
                               QtCore.Qt.KeyboardModifier.ShiftModifier, pos=position(2))
        self.assertEqual(editor._selected_panels, set(ids))
        QtTest.QTest.mouseClick(view, QtCore.Qt.MouseButton.LeftButton,
                               QtCore.Qt.KeyboardModifier.ControlModifier, pos=position(1))
        self.assertEqual(editor._selected_panels, {ids[0], ids[2]})
        editor.select_piece(ids[0])
        editor.canvas.fit_pieces()
        piece = editor.document.piece(ids[2])
        pixel = editor.canvas.mapFromScene(QtCore.QPointF(piece.layout[0] + 50, 50))
        QtTest.QTest.mouseClick(editor.canvas.viewport(), QtCore.Qt.MouseButton.LeftButton,
                               QtCore.Qt.KeyboardModifier.ShiftModifier, pos=pixel)
        self.app.processEvents()
        self.assertEqual(editor._selected_panels, {ids[0], ids[2]})
        QtTest.QTest.mouseClick(editor.canvas.viewport(), QtCore.Qt.MouseButton.LeftButton,
                               QtCore.Qt.KeyboardModifier.ControlModifier, pos=pixel)
        self.app.processEvents()
        self.assertEqual(editor._selected_panels, {ids[0]})

    def test_multi_panel_box_selection_and_combine_fuse_actions(self):
        editor = self.editor
        editor.add_template("Left", 100, 100)
        left = editor.selected_id
        editor.add_template("Right", 100, 100)
        right = editor.selected_id
        editor.edit("Place", lambda doc: doc.piece(right).layout.__setitem__(0, 100))
        editor.select_piece(left)
        editor._canvas_panel_selected(right, True)
        canvas = editor.canvas
        canvas.fit_pieces()
        start = canvas.mapFromScene(QtCore.QPointF(-10, -10))
        end = canvas.mapFromScene(QtCore.QPointF(210, 10))
        QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
        QtTest.QTest.mouseMove(canvas.viewport(), end, 20)
        QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        self.assertEqual({key: len(ids) for key, ids in editor._point_selection.items()}, {left: 2, right: 2})
        self.assertEqual(sum(len(ids) for ids in canvas._selected_points.values()), 4)
        editor.fuse_points_action.trigger()
        self.assertEqual(len(editor.document.pieces), 1)
        self.assertEqual(len(editor.document.pieces[0].points), 7)
        editor.undo()
        editor.select_piece(left)
        editor._canvas_panel_selected(right, True)
        QtTest.QTest.mouseClick(editor.combine_button, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(len(editor.document.pieces), 1)
        self.assertEqual(len(editor.document.pieces[0].points), 8)
        self.assertEqual(len(editor.document.pieces[0].contours), 2)
        editor.fuse_points_action.trigger()
        self.assertEqual(len(editor.document.pieces[0].points), 6)
        editor.undo()
        editor.undo()
        editor.select_piece(left)
        editor._canvas_panel_selected(right, True)
        canvas.setFocus()
        QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_M)
        self.assertEqual(len(editor.document.pieces), 1)
        self.assertEqual(len(editor.document.pieces[0].points), 6)
        self.assertEqual(len(editor.document.pieces[0].segments), 6)
        self.assertEqual(len(editor.document.pieces[0].contours), 1)

    def test_history_hotkeys_follow_app_bindings_and_beat_global_shortcuts(self):
        from echograph.ui import hotkeys_config
        editor = self.editor
        host = QtWidgets.QWidget()
        host_calls = []
        shortcuts = []
        for key in ("Ctrl+Z", "Ctrl+Y", "Alt+Z", "Alt+Y"):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), host)
            shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda: host_calls.append(True))
            shortcuts.append(shortcut)
        host.show()
        try:
            with patch.object(hotkeys_config, "_KEYMAP_CACHE", dict(hotkeys_config.DEFAULT_KEYMAP)):
                editor.activateWindow()
                editor.add_template("Front")
                editor.canvas.setFocus()
                self.app.processEvents()
                QtTest.QTest.keyClick(editor.canvas, QtCore.Qt.Key.Key_Z, QtCore.Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(len(editor.document.pieces), 0)
                QtTest.QTest.keyClick(editor.canvas, QtCore.Qt.Key.Key_Y, QtCore.Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(len(editor.document.pieces), 1)
                # Apply the same live cache update used by the hotkey editor.
                hotkeys_config._KEYMAP_CACHE.update(app_undo="Alt+Z", app_redo="Alt+Y")
                editor.name_edit.setFocus()
                QtTest.QTest.keyClick(editor.name_edit, QtCore.Qt.Key.Key_Z, QtCore.Qt.KeyboardModifier.AltModifier)
                self.assertEqual(len(editor.document.pieces), 0)
                QtTest.QTest.keyClick(editor.name_edit, QtCore.Qt.Key.Key_Y, QtCore.Qt.KeyboardModifier.AltModifier)
                self.assertEqual(len(editor.document.pieces), 1)
                self.assertEqual(editor.undo_action.shortcut().toString(), "Alt+Z")
                self.assertEqual(editor.redo_action.shortcut().toString(), "Alt+Y")
                self.assertEqual(host_calls, [])
        finally:
            host.close()

    def test_shift_box_and_shift_click_add_points_without_toggling(self):
        editor = self.editor
        editor.add_template("Points", 100, 100)
        panel = editor.selected_piece()
        canvas = editor.canvas
        canvas.fit_pieces()
        def drag(a, b, modifiers=QtCore.Qt.KeyboardModifier.NoModifier):
            start = canvas.mapFromScene(QtCore.QPointF(*a))
            end = canvas.mapFromScene(QtCore.QPointF(*b))
            QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, modifiers, start)
            QtTest.QTest.mouseMove(canvas.viewport(), end, 20)
            QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, modifiers, end)
            self.app.processEvents()
        drag((-10, -10), (110, 10))
        self.assertEqual(len(canvas._selected_points[panel.id]), 2)
        drag((-10, 90), (110, 110), QtCore.Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(canvas._selected_points[panel.id], {point.id for point in panel.points})
        self.assertEqual(editor._selected_points, {point.id for point in panel.points})
        drag((-40, -40), (-20, -20), QtCore.Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(len(canvas._selected_points[panel.id]), 4)
        editor._clear_canvas_selection()
        for point in panel.points[:2]:
            pixel = canvas.mapFromScene(QtCore.QPointF(point.x, point.y))
            QtTest.QTest.mouseClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton,
                                   QtCore.Qt.KeyboardModifier.ShiftModifier, pixel)
        self.assertEqual(canvas._selected_points[panel.id], {point.id for point in panel.points[:2]})
        pixel = canvas.mapFromScene(QtCore.QPointF(panel.points[0].x, panel.points[0].y))
        QtTest.QTest.mouseClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton,
                               QtCore.Qt.KeyboardModifier.ShiftModifier, pixel)
        self.assertEqual(len(canvas._selected_points[panel.id]), 2)

    def test_shift_click_accumulates_edges_across_panels_and_empty_click_clears(self):
        editor = self.editor
        editor.add_template("Left", 100, 100)
        left = editor.selected_piece()
        editor.add_template("Right", 100, 100)
        right = editor.selected_piece()
        editor.select_piece(left.id)
        canvas = editor.canvas
        canvas.fit_pieces()
        def click(x, y, shift=False):
            pixel = canvas.mapFromScene(QtCore.QPointF(x, y))
            QtTest.QTest.mouseClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton,
                                   QtCore.Qt.KeyboardModifier.ShiftModifier if shift else QtCore.Qt.KeyboardModifier.NoModifier,
                                   pixel)
            self.app.processEvents()
        click(50, 0)
        click(100, 50, True)
        click(right.layout[0] + 50, 0, True)
        expected = {(left.id, left.segments[0].id), (left.id, left.segments[1].id), (right.id, right.segments[0].id)}
        self.assertEqual(editor._edge_selection, expected)
        self.assertEqual(canvas.selected_edges, expected)
        overlays = [item for item in canvas.scene().items() if isinstance(item, QtWidgets.QGraphicsPathItem)
                    and item.pen().color().name() == "#ffc773"]
        self.assertEqual(len(overlays), 3)
        click(100, 50, True)
        self.assertEqual(editor._edge_selection, expected)
        click(-20, -20)
        self.assertFalse(editor._edge_selection)
        self.assertFalse(canvas.selected_edges)

    def test_edge_drag_previews_curve_and_commits_one_undo_step(self):
        from echograph.patterns.commands import set_edge_kind
        editor = self.editor
        editor.add_template("Move edge", 100, 100)
        panel_id = editor.selected_id
        edge_id = editor.selected_piece().segments[0].id
        editor.edit("Cubic", lambda doc: set_edge_kind(doc.piece(panel_id), edge_id, "cubic"))
        canvas = editor.canvas
        canvas.fit_pieces()
        before = editor.document.to_dict()
        undo_count = len(editor.history.undo_stack)
        start = canvas.mapFromScene(QtCore.QPointF(50, 0))
        end = canvas.mapFromScene(QtCore.QPointF(70, 20))
        QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
        QtTest.QTest.mouseMove(canvas.viewport(), end, 20)
        self.app.processEvents()
        self.assertEqual(editor.document.to_dict(), before)
        preview = next(item.piece for item in canvas._piece_items if item.piece.id == panel_id)
        self.assertEqual((preview.points[0].x, preview.points[0].y), (20, 20))
        self.assertEqual((preview.points[1].x, preview.points[1].y), (120, 20))
        QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        self.assertEqual(len(editor.history.undo_stack), undo_count + 1)
        panel = editor.selected_piece()
        self.assertEqual((panel.points[0].x, panel.points[0].y), (20, 20))
        self.assertEqual((panel.points[1].x, panel.points[1].y), (120, 20))
        self.assertEqual((panel.points[2].x, panel.points[2].y), (100, 100))
        self.assertEqual(panel.segment(edge_id).controls[0][1], 20)
        editor.undo()
        restored = editor.document.to_dict()
        restored.pop("revision")
        before.pop("revision")
        self.assertEqual(restored, before)

    def test_edge_drag_previews_and_preserves_linked_mirror(self):
        from echograph.patterns.relationships import symmetry_for_piece
        editor = self.editor
        editor.add_template("Linked edge", 100, 100)
        original_id = editor.selected_id
        editor.duplicate(True)
        mirror_id = editor.selected_id
        editor.select_piece(original_id)
        canvas = editor.canvas
        canvas.fit_pieces()
        original = editor.selected_piece()
        point_id = original.points[0].id
        partner_id = symmetry_for_piece(editor.document, original_id)["points"][point_id]
        start = canvas.mapFromScene(QtCore.QPointF(50, 0))
        end = canvas.mapFromScene(QtCore.QPointF(70, 20))
        QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
        QtTest.QTest.mouseMove(canvas.viewport(), end, 20)
        preview = next(item.piece for item in canvas._piece_items if item.piece.id == mirror_id)
        self.assertEqual((preview.point(partner_id).x, preview.point(partner_id).y), (80, 20))
        QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
        self.assertTrue(symmetry_for_piece(editor.document, original_id)["enabled"])
        partner = editor.document.piece(mirror_id).point(partner_id)
        self.assertEqual((partner.x, partner.y), (80, 20))

    def test_edge_click_does_not_edit_and_escape_cancels_drag(self):
        editor = self.editor
        editor.add_template("Cancel edge", 100, 100)
        canvas = editor.canvas
        canvas.fit_pieces()
        start = canvas.mapFromScene(QtCore.QPointF(50, 0))
        before = editor.document.to_dict()
        count = len(editor.history.undo_stack)
        QtTest.QTest.mouseClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
        self.assertEqual(len(editor.history.undo_stack), count)
        end = canvas.mapFromScene(QtCore.QPointF(70, 20))
        QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
        QtTest.QTest.mouseMove(canvas.viewport(), end, 20)
        QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Escape)
        QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        self.assertEqual(editor.document.to_dict(), before)
        self.assertEqual(len(editor.history.undo_stack), count)
        self.assertIsNone(canvas._edge_drag)
        preview = canvas._piece_items[0].piece
        self.assertEqual((preview.points[0].x, preview.points[0].y), (0, 0))

    def test_delete_apex_through_canvas_keeps_triangle_base(self):
        from echograph.patterns.commands import polygon
        panel = polygon("Triangle", [(0, 0), (100, 0), (50, 100)])
        editor = self.editor
        editor.edit("Triangle", lambda doc: doc.pieces.append(panel))
        editor.select_piece(panel.id)
        editor.canvas._on_point_clicked(panel.id, panel.points[2].id, False)
        QtTest.QTest.keyClick(editor.canvas, QtCore.Qt.Key.Key_Delete)
        self.app.processEvents()
        self.assertEqual(len(editor.selected_piece().points), 2)
        self.assertEqual([edge.id for edge in editor.selected_piece().segments], [panel.segments[0].id])
        self.assertFalse(editor.selected_piece().closed)
        editor.undo()
        self.assertEqual(len(editor.selected_piece().points), 3)

    def test_delete_key_removes_panels_from_canvas_and_outliner_and_undoes(self):
        editor = self.editor
        editor.add_template("Front", 100, 100)
        front = editor.selected_id
        editor.add_template("Back", 100, 100)
        back = editor.selected_id
        editor.select_piece(front)
        editor._canvas_panel_selected(back, True)
        editor.canvas.setFocus()
        QtTest.QTest.keyClick(editor.canvas, QtCore.Qt.Key.Key_Delete)
        self.assertFalse(editor.document.pieces)
        editor.undo()
        self.assertEqual(len(editor.document.pieces), 2)
        editor.select_piece(front)
        # Clicking the already-selected list row must switch from point mode
        # back to panel mode, even without a Qt selection-change signal.
        point = editor.document.piece(front).points[0]
        editor.canvas._on_point_clicked(front, point.id, False)
        row = next(editor.pieces.item(index) for index in range(editor.pieces.count())
                   if editor.pieces.item(index).data(QtCore.Qt.ItemDataRole.UserRole) == front)
        QtTest.QTest.mouseClick(editor.pieces.viewport(), QtCore.Qt.MouseButton.LeftButton,
                               pos=editor.pieces.visualItemRect(row).center())
        self.assertFalse(editor._point_selection)
        QtTest.QTest.keyClick(editor.pieces, QtCore.Qt.Key.Key_Delete)
        self.assertEqual([piece.id for piece in editor.document.pieces], [back])

    def test_point_and_edge_selection_are_exclusive_and_shift_keeps_type(self):
        editor = self.editor
        editor.add_template("Exclusive", 100, 100)
        panel = editor.selected_piece()
        canvas = editor.canvas
        canvas.fit_pieces()
        def click(x, y, shift=False):
            pixel = canvas.mapFromScene(QtCore.QPointF(x, y))
            QtTest.QTest.mouseClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton,
                                   QtCore.Qt.KeyboardModifier.ShiftModifier if shift else QtCore.Qt.KeyboardModifier.NoModifier,
                                   pixel)
            self.app.processEvents()
        click(50, 0)
        self.assertEqual(canvas.selected_edges, {(panel.id, panel.segments[0].id)})
        click(0, 0, True)
        self.assertFalse(canvas._selected_points)
        self.assertEqual(len(canvas.selected_edges), 1)
        click(0, 0)
        self.assertFalse(canvas.selected_edges)
        self.assertFalse(editor._edge_selection)
        self.assertEqual(editor.edges.currentIndex(), -1)
        self.assertEqual(canvas._selected_points, {panel.id: {panel.points[0].id}})
        click(50, 0, True)
        self.assertFalse(canvas.selected_edges)
        self.assertEqual(canvas._selected_points, {panel.id: {panel.points[0].id}})
        click(100, 0, True)
        self.assertEqual(len(canvas._selected_points[panel.id]), 2)
        click(100, 50)
        self.assertFalse(canvas._selected_points)
        self.assertFalse(editor._point_selection)
        self.assertFalse(editor._selected_points)
        self.assertEqual(canvas.selected_edges, {(panel.id, panel.segments[1].id)})

    def test_delete_key_deletes_only_selected_edges_and_points_across_panels(self):
        from echograph.patterns.document import validate_integrity
        editor = self.editor
        editor.add_template("Left", 100, 100)
        left = editor.selected_id
        editor.add_template("Right", 100, 100)
        right = editor.selected_id
        editor.select_piece(left)
        editor._canvas_panel_selected(right, True)
        canvas = editor.canvas
        a, b = editor.document.piece(left), editor.document.piece(right)
        canvas._on_point_clicked(left, a.points[0].id, False)
        canvas._on_point_clicked(right, b.points[0].id, True)
        QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Delete)
        self.assertEqual([len(piece.points) for piece in editor.document.pieces], [3, 3])
        self.assertEqual(len(editor.document.pieces), 2)
        editor.undo()
        editor._canvas_edge_selected(left, a.segments[0].id)
        QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Delete)
        self.assertEqual(len(editor.document.piece(left).segments), 3)
        self.assertFalse(editor.document.piece(left).closed)
        self.assertEqual(len(editor.document.pieces), 2)
        validate_integrity(editor.document)

    def test_delete_key_in_name_field_edits_text_without_deleting_panel(self):
        editor = self.editor
        editor.add_template("Front")
        editor.name_edit.setFocus()
        editor.name_edit.setCursorPosition(0)
        QtTest.QTest.keyClick(editor.name_edit, QtCore.Qt.Key.Key_Delete)
        self.assertEqual(editor.name_edit.text(), "ront")
        self.assertEqual(len(editor.document.pieces), 1)

    def test_template_numeric_curve_mirror_undo_and_save(self):
        editor = self.editor
        editor.add_template("Front", 300, 400)
        first_id = editor.selected_id
        editor.width_edit.setValue(320)
        editor.apply_dimensions()
        self.assertEqual(bounds(editor.selected_piece())[2], 320)
        editor.edges.setCurrentIndex(0)
        editor.edge_kind.setCurrentText("cubic")
        editor.controls[1].setValue(-50)
        editor.controls[3].setValue(-50)
        editor.apply_handles()
        self.assertFalse(validate_piece(editor.selected_piece()))
        edge_id = editor.selected_piece().segments[0].id
        editor.duplicate(True)
        self.assertEqual(len(editor.document.pieces), 2)
        editor.undo()
        self.assertEqual(len(editor.document.pieces), 1)
        editor.redo()
        self.assertEqual(len(editor.document.pieces), 2)
        editor.save()
        loaded, _ = load_document(self.path)
        self.assertEqual(loaded.to_dict(), editor.document.to_dict())
        self.assertEqual(loaded.piece(first_id).segments[0].id, edge_id)
        self.assertFalse(editor.history.dirty)

    def test_mirror_icons_outliner_toggle_and_linked_dimensions(self):
        from echograph.patterns.relationships import symmetry_for_piece
        editor = self.editor
        editor.add_template("Linked front")
        original_id = editor.selected_id
        self.assertEqual(editor.mirror_button.text(), "Mirror")
        self.assertEqual(editor.duplicate_button.text(), "Duplicate")
        self.assertFalse(editor.mirror_button.icon().isNull())
        self.assertFalse(editor.duplicate_button.icon().isNull())
        QtTest.QTest.mouseClick(editor.mirror_button, QtCore.Qt.MouseButton.LeftButton)
        mirrored_id = editor.selected_id
        self.assertTrue(symmetry_for_piece(editor.document, mirrored_id)["enabled"])
        self.assertTrue(editor._symmetry_buttons[original_id].isChecked())
        self.assertFalse(editor._symmetry_buttons[mirrored_id].icon().isNull())
        original_row = next(editor.pieces.item(i) for i in range(editor.pieces.count())
                            if editor.pieces.item(i).data(QtCore.Qt.ItemDataRole.UserRole) == original_id)
        label = editor.pieces.itemWidget(original_row).findChild(QtWidgets.QLabel)
        QtTest.QTest.mouseClick(label, QtCore.Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(editor.selected_id, original_id)
        editor.select_piece(mirrored_id)
        editor.width_edit.setValue(350)
        editor.apply_dimensions()
        original = editor.document.piece(original_id)
        self.assertAlmostEqual(bounds(original)[2] - bounds(original)[0], 350)
        QtTest.QTest.mouseClick(editor._symmetry_buttons[original_id], QtCore.Qt.MouseButton.LeftButton)
        self.assertFalse(symmetry_for_piece(editor.document, mirrored_id)["enabled"])
        self.assertFalse(editor._symmetry_buttons[mirrored_id].isChecked())
        before = editor.document.piece(original_id).point(original.points[0].id).x
        point = editor.document.piece(mirrored_id).points[0].id
        from echograph.patterns.commands import move_point
        editor.edit("Independent point", lambda doc: move_point(doc.piece(mirrored_id), point, 450, 20))
        self.assertEqual(editor.document.piece(original_id).point(original.points[0].id).x, before)
        QtTest.QTest.mouseClick(editor._symmetry_buttons[mirrored_id], QtCore.Qt.MouseButton.LeftButton)
        self.assertTrue(symmetry_for_piece(editor.document, mirrored_id)["enabled"])
        editor.save()
        self.assertTrue(symmetry_for_piece(load_document(self.path)[0], mirrored_id)["enabled"])

    def test_merge_point_canvas_action_turns_symmetry_off(self):
        from echograph.patterns.relationships import symmetry_for_piece, world_point
        editor = self.editor
        editor.add_template("Front")
        editor.duplicate(True)
        mirrored_id = editor.selected_id
        source_point = editor.selected_piece().points[0].id
        editor.add_template("Other")
        target_id = editor.selected_id
        target_point = editor.selected_piece().points[0].id
        target_world = world_point(editor.selected_piece(), target_point)
        editor.select_piece(mirrored_id)
        editor.canvas._on_point_clicked(mirrored_id, source_point, False)
        editor.start_point_merge()
        self.assertIsNotNone(editor.canvas._merge_source)
        pixel = editor.canvas.mapFromScene(QtCore.QPointF(*target_world))
        QtTest.QTest.mouseClick(editor.canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=pixel)
        self.app.processEvents()
        self.assertIsNone(editor.canvas._merge_source)
        self.assertEqual(len(editor.document.pieces), 2)
        self.assertEqual(len(editor.document.piece(mirrored_id).points), 7)
        self.assertEqual(len(editor.document.piece(mirrored_id).contours), 2)
        self.assertFalse(symmetry_for_piece(editor.document, mirrored_id)["enabled"])
        self.assertFalse(editor._symmetry_buttons[mirrored_id].isChecked())
        self.assertEqual(world_point(editor.document.piece(mirrored_id), source_point), target_world)
        editor.undo()
        self.assertTrue(symmetry_for_piece(editor.document, mirrored_id)["enabled"])

    def test_mirrored_point_drag_previews_partner_and_commits_one_undo_step(self):
        from echograph.patterns.relationships import symmetry_for_piece
        editor = self.editor
        editor.add_template("Live symmetry")
        editor.duplicate(True)
        source_id = editor.selected_id
        pair = symmetry_for_piece(editor.document, source_id)
        point_id = editor.selected_piece().points[0].id
        counterpart = {v: k for k, v in pair["points"].items()}[point_id]
        handle = next(i for i in editor.canvas.scene().items() if isinstance(i, HandleItem) and i.entity_id == point_id)
        start = editor.canvas.mapFromScene(handle.mapToScene(QtCore.QPointF()))
        end = start + QtCore.QPoint(20, -15)
        count = len(editor.history.undo_stack)
        QtTest.QTest.mousePress(editor.canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
        QtTest.QTest.mouseMove(editor.canvas.viewport(), end, 10)
        partner_item = next(i for i in editor.canvas._piece_items if i.piece.id == pair["left"])
        source_item = next(i for i in editor.canvas._piece_items if i.piece.id == source_id)
        self.assertAlmostEqual(partner_item.piece.point(counterpart).x,
                               pair["axis_sum"] - source_item.piece.point(point_id).x)
        self.assertEqual(len(editor.history.undo_stack), count)
        QtTest.QTest.mouseRelease(editor.canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        self.assertEqual(len(editor.history.undo_stack), count + 1)
        self.assertAlmostEqual(editor.document.piece(pair["left"]).point(counterpart).x,
                               pair["axis_sum"] - editor.document.piece(source_id).point(point_id).x)

    def test_click_draw_outline_and_close_in_canvas(self):
        canvas = self.editor.canvas
        canvas.start_outline()
        for point in ((0, 0), (200, 0), (100, 200)):
            pixel = canvas.mapFromScene(QtCore.QPointF(*point))
            QtTest.QTest.mouseClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=pixel)
        QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(len(self.editor.document.pieces), 1)
        self.assertFalse(validate_piece(self.editor.document.pieces[0]))
        self.assertEqual(len(self.editor.document.pieces[0].points), 3)

    def test_save_in_progress_outline_preserves_draft(self):
        canvas = self.editor.canvas
        canvas.start_outline()
        canvas.draft_points = [(0, 0), (100, 50)]
        self.assertTrue(self.editor.save())
        loaded, _ = load_document(self.path)
        self.assertEqual(len(loaded.pieces), 1)
        self.assertFalse(loaded.pieces[0].closed)
        self.assertTrue(validate_piece(loaded.pieces[0]))

    def test_point_drag_is_one_undoable_edit(self):
        self.editor.add_template("Panel")
        self.editor.canvas.snap_enabled = False
        self.app.processEvents()
        piece = self.editor.selected_piece()
        point_id = piece.points[0].id
        handle = next(i for i in self.editor.canvas.scene().items()
                      if isinstance(i, HandleItem) and i.entity_id == point_id)
        start = self.editor.canvas.mapFromScene(handle.scenePos())
        end = start + QtCore.QPoint(24, 12)
        before = len(self.editor.history.undo_stack)
        QtTest.QTest.mousePress(self.editor.canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
        QtTest.QTest.mouseMove(self.editor.canvas.viewport(), end, 10)
        QtTest.QTest.mouseRelease(self.editor.canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        moved = self.editor.selected_piece().point(point_id)
        self.assertNotEqual((moved.x, moved.y), (0, 0))
        self.assertEqual(len(self.editor.history.undo_stack), before + 1)
        self.editor.undo()
        self.assertEqual(self.editor.selected_piece().point(point_id).x, 0)

    def test_canvas_click_selects_edge_and_double_click_inserts_point(self):
        self.editor.add_template("Edge test")
        canvas = self.editor.canvas
        canvas.fit_pieces()
        edge_id = self.editor.selected_piece().segments[0].id
        pixel = canvas.mapFromScene(QtCore.QPointF(150, 0))
        QtTest.QTest.mouseClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=pixel)
        self.app.processEvents()
        self.assertEqual(self.editor._edge_id, edge_id)
        QtTest.QTest.mouseDClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=pixel)
        self.app.processEvents()
        self.assertEqual(len(self.editor.selected_piece().points), 5)
        self.assertEqual(len(self.editor.selected_piece().segments), 5)

    def test_click_point_then_delete_removes_vertex(self):
        self.editor.add_template("Point test")
        canvas = self.editor.canvas
        canvas.fit_pieces()
        piece = self.editor.selected_piece()
        point_id = piece.points[0].id
        pixel = canvas.mapFromScene(QtCore.QPointF(piece.points[0].x, piece.points[0].y))
        QtTest.QTest.mouseClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=pixel)
        self.app.processEvents()
        self.assertEqual(self.editor._selected_points, {point_id})
        QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Delete)
        self.app.processEvents()
        self.assertEqual(len(self.editor.selected_piece().points), 3)
        self.assertNotIn(point_id, {point.id for point in self.editor.selected_piece().points})

    def test_drag_from_empty_canvas_selects_multiple_points_without_modifier(self):
        self.editor.add_template("Box selection")
        canvas = self.editor.canvas
        canvas.fit_pieces()
        start = canvas.mapFromScene(QtCore.QPointF(-20, -20))
        end = canvas.mapFromScene(QtCore.QPointF(320, 20))
        QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
        QtTest.QTest.mouseMove(canvas.viewport(), end, 20)
        QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        self.assertEqual(len(self.editor._selected_points), 2)
        self.assertEqual(canvas._selected_points[self.editor.selected_id], self.editor._selected_points)
        handles = [item for item in canvas.scene().items() if isinstance(item, HandleItem) and item.control_index < 0]
        self.assertEqual(sum(handle.brush().color().name() == "#ffd166" for handle in handles), 2)

    def test_empty_canvas_click_clears_point_and_edge_selection(self):
        self.editor.add_template("Selection")
        self.assertEqual(self.editor.edges.currentIndex(), -1)
        self.editor.edges.setCurrentIndex(0)
        self.editor.edge_kind.setCurrentText("cubic")
        canvas = self.editor.canvas
        self.assertEqual(sum(isinstance(i, HandleItem) and i.control_index >= 0 for i in canvas.scene().items()), 2)
        canvas._on_point_clicked(self.editor.selected_id, self.editor.selected_piece().points[0].id, False)
        self.assertFalse(self.editor._edge_id)
        pixel = canvas.mapFromScene(QtCore.QPointF(-40, -40))
        QtTest.QTest.mouseClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=pixel)
        self.app.processEvents()
        self.editor.refresh()
        self.assertFalse(self.editor._selected_points)
        self.assertFalse(canvas._selected_points)
        self.assertEqual(self.editor._edge_id, "")
        self.assertEqual(self.editor.edges.currentIndex(), -1)
        self.assertEqual(canvas.selected_edge, (self.editor.selected_id, ""))
        self.assertFalse(any(isinstance(i, HandleItem) and i.control_index >= 0 for i in canvas.scene().items()))

    def test_right_drag_up_and_down_zooms_canvas(self):
        canvas = self.editor.canvas
        center = canvas.viewport().rect().center()
        original = abs(canvas.transform().m11())
        QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MouseButton.RightButton, pos=center)
        QtTest.QTest.mouseMove(canvas.viewport(), center - QtCore.QPoint(0, 50), 20)
        QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MouseButton.RightButton,
                                  pos=center - QtCore.QPoint(0, 50))
        zoomed_in = abs(canvas.transform().m11())
        self.assertGreater(zoomed_in, original)
        end = center - QtCore.QPoint(0, 100)
        QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MouseButton.RightButton, pos=end)
        QtTest.QTest.mouseMove(canvas.viewport(), center, 20)
        QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MouseButton.RightButton, pos=center)
        self.assertLess(abs(canvas.transform().m11()), zoomed_in)

    def test_right_drag_zoom_keeps_press_pivot_fixed_without_accumulated_drift(self):
        self.editor.add_template("Zoom pivot")
        canvas = self.editor.canvas
        canvas.fit_pieces()
        press = canvas.viewport().rect().center() + QtCore.QPoint(100, 60)
        pivot = canvas.mapToScene(press)
        initial_scale = abs(canvas.transform().m11())
        QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MouseButton.RightButton, pos=press)
        for delta in (-10, -40, -80, -120, -70, -20, 30, 60, 0):
            QtTest.QTest.mouseMove(canvas.viewport(), press + QtCore.QPoint(0, delta), 5)
            screen_pivot = canvas.mapFromScene(pivot)
            self.assertLessEqual((screen_pivot - press).manhattanLength(), 3)
        QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MouseButton.RightButton, pos=press)
        self.assertAlmostEqual(abs(canvas.transform().m11()), initial_scale, places=8)

    def test_horizontal_right_drag_zooms_about_the_press_pivot(self):
        canvas = self.editor.canvas
        press = canvas.viewport().rect().center() + QtCore.QPoint(80, 40)
        pivot = canvas.mapToScene(press)
        initial_scale = abs(canvas.transform().m11())
        QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MouseButton.RightButton, pos=press)
        for dx in (30, 70, -30, -60, 0):
            QtTest.QTest.mouseMove(canvas.viewport(), press + QtCore.QPoint(dx, 0), 5)
            scale = abs(canvas.transform().m11())
            if dx > 0:
                self.assertGreater(scale, initial_scale)
            elif dx < 0:
                self.assertLess(scale, initial_scale)
            self.assertLessEqual((canvas.mapFromScene(pivot) - press).manhattanLength(), 3)
        QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MouseButton.RightButton, pos=press)
        self.assertAlmostEqual(abs(canvas.transform().m11()), initial_scale, places=8)

    def test_cancel_close_keeps_unsaved_editor(self):
        self.editor.add_template("Unsaved")
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Cancel):
            self.assertFalse(self.editor.close())
        self.assertTrue(self.editor.history.dirty)
        self.assertTrue(self.editor.isVisible())

    def test_save_as_retargets_editor_and_subsequent_save_preserves_original(self):
        self.editor.add_template("Front")
        target = self.path.with_name("copy.pattern.json")
        saved = []
        self.editor.saved.connect(saved.append)
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(target), "")):
            self.assertTrue(self.editor.save_as())
        loaded, _ = load_document(target)
        self.assertEqual(loaded.document_id, self.editor.document.document_id)
        self.assertEqual(len(loaded.pieces), 1)
        self.assertEqual(self.editor.path, target)
        self.assertFalse(self.editor.history.dirty)
        self.assertEqual(saved, [str(target)])
        self.assertEqual(self.editor.file_label.text(), str(target))
        self.editor.add_template("Back")
        self.assertTrue(self.editor.save())
        self.assertEqual(len(load_document(target)[0].pieces), 2)
        self.assertEqual(len(load_document(self.path)[0].pieces), 0)

    def test_save_as_cancel_and_declined_overwrite_leave_current_file(self):
        editor = self.editor
        editor.add_template("Front")
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.assertFalse(editor.save_as())
        target = self.path.with_name("existing.pattern.json")
        save_document(target, PatternDocument(name="Existing"))
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(target), "")), \
                patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.No):
            self.assertFalse(editor.save_as())
        self.assertEqual(editor.path, self.path)
        self.assertTrue(editor.history.dirty)
        self.assertEqual(load_document(target)[0].name, "Existing")

    def test_save_as_confirmed_overwrite_and_same_file(self):
        editor = self.editor
        editor.add_template("Front")
        target = self.path.with_name("existing.pattern.json")
        save_document(target, PatternDocument(name="Existing"))
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(target), "")), \
                patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Yes):
            self.assertTrue(editor.save_as())
            editor.add_template("Back")
            self.assertTrue(editor.save_as())
        self.assertEqual(editor.path, target)
        self.assertEqual(len(load_document(target)[0].pieces), 2)

    def test_editing_y_does_not_round_unchanged_x(self):
        self.editor.add_template("Precise")
        point_id = self.editor.selected_piece().points[0].id
        exact_x = 0.12345678901234567
        self.editor.edit("Precision", lambda d: setattr(d.pieces[0].points[0], "x", exact_x))
        self.editor.points.item(0, 2).setText("-5")
        point = self.editor.selected_piece().point(point_id)
        self.assertEqual(point.x, exact_x)
        self.assertEqual(point.y, -5)

    def test_external_change_is_not_overwritten_in_editor(self):
        self.editor.add_template("My edits")
        other, signature = load_document(self.path)
        other.name = "Edited elsewhere"
        save_document(self.path, other, expected_hash=signature)
        with patch.object(QtWidgets.QMessageBox, "warning") as message:
            self.assertFalse(self.editor.save())
        self.assertTrue(message.called)
        self.assertTrue(self.editor.history.dirty)
        self.assertEqual(load_document(self.path)[0].name, "Edited elsewhere")

    def test_parent_window_close_can_be_canceled_for_unsaved_pattern(self):
        parent = QtWidgets.QMainWindow()
        child = PatternEditor(self.path, parent)
        child.add_template("Unsaved in parent")
        parent.show()
        child.show()
        self.app.processEvents()
        try:
            with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Cancel):
                self.assertFalse(parent.close())
            self.assertTrue(child.history.dirty)
            self.assertTrue(child.isVisible())
        finally:
            child.history.mark_saved()
            child.close()
            parent.close()


class PatternNodeTests(unittest.TestCase):
    def test_save_sends_one_scene_notification_after_file_and_state_are_ready(self):
        class Graph(QtWidgets.QGraphicsScene):
            paramChanged = QtCore.Signal(str, object)

            def set_node_params(self, name, params, **kwargs):
                self.paramChanged.emit(name, params)

        graph = Graph()
        item = NodeItem(GraphNode("Pattern", kind="pattern_design",
                                 params=[{"name": "pattern_path", "value": str(self.path)}]))
        graph.addItem(item)
        self.app.processEvents()
        widget = next(proxy.widget() for proxy in item._plugin_proxies if isinstance(proxy.widget(), PatternNodeWidget))
        notifications = []
        graph.paramChanged.connect(lambda name, params: notifications.append(
            (name, load_document(self.path)[0].to_dict(), widget.history.dirty)))
        try:
            widget.add_rectangle()
            self.assertTrue(widget.save_pattern())
            self.assertEqual(notifications, [])
            QtTest.QTest.qWait(item._PARAM_EMIT_DEBOUNCE_MS + 100)
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0][0], "Pattern")
            self.assertEqual(notifications[0][1], widget.history.document.to_dict())
            self.assertFalse(notifications[0][2])
        finally:
            graph.clear()
            self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = (Path(self.temp.name) / "shared.pattern.json").resolve()
        from echograph.patterns.commands import rectangle
        save_document(self.path, PatternDocument(pieces=[rectangle("Shared front")]))
        self.scene = QtWidgets.QGraphicsScene()
        self.scene._node_items = {}
        self.scene._edges = []

    def tearDown(self):
        self.scene.clear()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def node(self, name="Pattern", path=None):
        item = NodeItem(GraphNode(name, kind="pattern_design", params=[{"name": "pattern_path", "value": str(path or self.path)}]))
        self.scene.addItem(item)
        self.scene._node_items[name] = item
        self.app.processEvents()
        return item

    def test_embedded_canvas_selects_multiple_panels_and_fuses_overlaps(self):
        from echograph.patterns.commands import rectangle
        left, right = rectangle("Left", 100, 100), rectangle("Right", 100, 100)
        right.layout = [100, 0, 0]
        save_document(self.path, PatternDocument(pieces=[left, right]), expected_hash=load_document(self.path)[1])
        item = self.node()
        widget = next(proxy.widget() for proxy in item._plugin_proxies if isinstance(proxy.widget(), PatternNodeWidget))
        widget._piece_selected(left.id)
        widget._panels_selected(right.id, True)
        self.assertEqual(widget._selected_panels, {left.id, right.id})
        self.assertEqual(len([handle for handle in widget.canvas.scene().items()
                              if isinstance(handle, HandleItem) and handle.control_index < 0]), 8)
        QtTest.QTest.keyClick(widget.canvas, QtCore.Qt.Key.Key_M)
        self.assertEqual(len(widget.history.document.pieces), 1)
        self.assertEqual(len(widget.history.document.pieces[0].points), 6)

    def test_embedded_canvas_delete_panel_and_switch_component_type(self):
        item = self.node()
        widget = next(proxy.widget() for proxy in item._plugin_proxies if isinstance(proxy.widget(), PatternNodeWidget))
        panel = widget.history.document.pieces[0]
        widget._canvas_edge_selected(panel.id, panel.segments[0].id)
        widget.canvas._on_point_clicked(panel.id, panel.points[0].id, False)
        self.assertFalse(widget._edge_selection)
        self.assertFalse(widget.canvas.selected_edges)
        self.assertEqual(widget.edge_combo.currentIndex(), -1)
        widget._piece_selected(panel.id)
        QtTest.QTest.keyClick(widget.canvas, QtCore.Qt.Key.Key_Delete)
        self.assertFalse(widget.history.document.pieces)
        widget.history.undo()
        widget.refresh()
        self.assertEqual(len(widget.history.document.pieces), 1)

    def test_save_as_switches_owning_node_and_preserves_other_node_reference(self):
        item = self.node("Original")
        other = self.node("Other reference")
        widget = next(proxy.widget() for proxy in item._plugin_proxies if isinstance(proxy.widget(), PatternNodeWidget))
        widget.open_editor()
        editor = widget._editor
        target = self.path.with_name("saved_as.pattern.json")
        try:
            editor.add_template("New back")
            raw_name = str(target).removesuffix(".pattern.json")
            with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(raw_name, "")):
                self.assertTrue(editor.save_as())
            self.assertEqual(editor.path, target)
            self.assertEqual(next(param["value"] for param in item.model.params if param["name"] == "pattern_path"), str(target))
            self.assertEqual(next(param["value"] for param in other.model.params if param["name"] == "pattern_path"), str(self.path))
            self.assertEqual(len(load_document(target)[0].pieces), 2)
            self.assertEqual(len(load_document(self.path)[0].pieces), 1)
        finally:
            editor.history.mark_saved()
            editor.close()

    def test_workflow_persists_only_file_reference_even_after_reading_geometry(self):
        item = self.node()
        payload = resolve_pattern_document(item)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["document"]["pieces"][0]["name"], "Shared front")
        workflow = serialize_scene(self.scene)
        text = json.dumps(workflow)
        self.assertNotIn("pieces", text)
        self.assertNotIn("segments", text)
        self.assertNotIn("Shared front", text)
        params = workflow["nodes"][0]["params"]
        self.assertEqual(next(p["value"] for p in params if p["name"] == "pattern_path"), str(self.path))
        self.assertIn("pattern", item.model._named_outputs)

    def test_new_pattern_opens_canvas_without_a_file_dialog(self):
        item = self.node()
        # Simulate a newly created node, which starts with no file reference.
        item._set_param_value("pattern_path", "", rebuild=False, notify_scene=False)
        widget = next(p.widget() for p in item._plugin_proxies if isinstance(p.widget(), PatternNodeWidget))
        with unittest.mock.patch.object(QtWidgets.QFileDialog, "getSaveFileName",
                                        side_effect=AssertionError("creating a draft must not open a file browser")):
            widget.new_pattern()
        self.assertEqual(item._param_value("pattern_path"), "")
        self.assertEqual(len(widget.history.document.pieces), 0)
        self.assertTrue(widget.canvas.isEnabled())

    def test_embedded_canvas_draws_points_and_edits_a_curve_before_first_save(self):
        item = self.node()
        item._set_param_value("pattern_path", "", rebuild=False, notify_scene=False)
        widget = next(p.widget() for p in item._plugin_proxies if isinstance(p.widget(), PatternNodeWidget))
        widget.new_pattern()
        widget.canvas.start_outline()
        for point in ((0, 0), (200, 0), (100, 200)):
            pixel = widget.canvas.mapFromScene(QtCore.QPointF(*point))
            QtTest.QTest.mouseClick(widget.canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=pixel)
        QtTest.QTest.keyClick(widget.canvas, QtCore.Qt.Key.Key_Return)
        self.app.processEvents()

        piece = widget.history.document.pieces[0]
        self.assertTrue(piece.closed)
        self.assertEqual(len(piece.points), 3)
        self.assertEqual(item._param_value("pattern_path"), "")
        self.assertEqual(widget.edge_combo.count(), 3)
        widget.edge_combo.setCurrentIndex(0)
        widget._make_cubic()
        self.assertEqual(widget.history.document.pieces[0].segments[0].kind, "cubic")
        self.assertEqual(item._param_value("pattern_path"), "")

    def test_canvas_drafts_then_save_creates_an_independent_pattern_file(self):
        item = self.node()
        item._set_param_value("pattern_path", "", rebuild=False, notify_scene=False)
        widget = next(p.widget() for p in item._plugin_proxies if isinstance(p.widget(), PatternNodeWidget))
        widget.new_pattern()
        widget.add_rectangle()
        self.assertTrue(widget.history.dirty)
        target = self.path.with_name("created-from-canvas.pattern.json")
        with unittest.mock.patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(target), "")):
            self.assertTrue(widget.save_pattern())
        self.assertTrue(target.is_file())
        self.assertEqual(item._param_value("pattern_path"), str(target))
        self.assertEqual(load_document(target)[0].to_dict(), widget.history.document.to_dict())
        workflow = serialize_scene(self.scene)
        self.assertEqual(workflow["nodes"][0]["params"][0]["value"], str(target))
        self.assertNotIn("Panel 1", json.dumps(workflow))

    def test_same_file_in_two_nodes_and_reconstructed_workflow(self):
        first, second = self.node("A"), self.node("B")
        self.assertEqual(resolve_pattern_document(first)["file_hash"], resolve_pattern_document(second)["file_hash"])
        saved = _node_to_dict(first.model)
        restored = NodeItem(GraphNode(saved["name"] + " restored", kind=saved["kind"], params=saved["params"]))
        self.scene.addItem(restored)
        self.assertEqual(resolve_pattern_document(restored)["document"], resolve_pattern_document(first)["document"])
        self.assertEqual(list(self.path.parent.glob("*.json")), [self.path])

    def test_missing_file_does_not_generate_blank_replacement(self):
        item = self.node()
        self.path.unlink()
        widget = next(p.widget() for p in item._plugin_proxies if isinstance(p.widget(), PatternNodeWidget))
        widget.refresh()
        self.assertTrue(any(button.text() == "Save .pattern.json" and button.isEnabled()
                            for button in widget.findChildren(QtWidgets.QPushButton)))
        self.assertIn("missing or invalid", widget.filename.text().lower())
        self.assertFalse(self.path.exists())
        self.assertEqual(item._param_value("pattern_path"), str(self.path))

    def test_locating_a_moved_pattern_keeps_document_identity(self):
        item = self.node()
        before = resolve_pattern_document(item)["document"]
        target = self.path.with_name("moved.pattern.json")
        self.path.rename(target)
        widget = next(p.widget() for p in item._plugin_proxies if isinstance(p.widget(), PatternNodeWidget))
        widget.attach(target)
        after = resolve_pattern_document(item)["document"]
        self.assertEqual(before, after)
        self.assertEqual(item._param_value("pattern_path"), str(target))

    def test_editor_remains_saveable_after_owning_node_is_removed(self):
        item = self.node()
        widget = next(p.widget() for p in item._plugin_proxies if isinstance(p.widget(), PatternNodeWidget))
        widget.open_editor()
        editor = widget._editor
        try:
            editor.add_template("Still editable")
            self.scene.clear()
            self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
            self.assertTrue(editor.isVisible())
            self.assertTrue(editor.save())
            self.assertEqual(len(load_document(self.path)[0].pieces), 2)
        finally:
            editor.history.mark_saved()
            editor.close()

    def test_border_contains_every_control_after_rebuild_and_recompute(self):
        item = self.node()
        for _ in range(3):
            item._build_widgets()
            item._recompute_height()
            self.app.processEvents()
            for proxy in item._plugin_proxies:
                rect = proxy.mapRectToParent(proxy.boundingRect())
                self.assertGreaterEqual(item.height, rect.bottom() + item._PADDING - 0.01)
                self.assertGreaterEqual(item.width, rect.right() - 0.01)
                body = proxy.widget()
                for widget in body.findChildren(QtWidgets.QPushButton):
                    point = widget.mapTo(body, widget.rect().bottomRight())
                    self.assertLessEqual(point.y(), body.height())

    def test_node_canvas_fills_middle_width_and_fit_frames_every_panel(self):
        item = self.node()
        widget = next(p.widget() for p in item._plugin_proxies if isinstance(p.widget(), PatternNodeWidget))
        self.assertGreater(widget.canvas.width(), 500)
        self.assertAlmostEqual(widget.canvas.width(), widget.width() - 16, delta=2)
        self.assertGreaterEqual(widget.canvas.height(), 340)
        self.assertLessEqual(widget.canvas.height(), 420)
        self.assertGreaterEqual(item.height, 520)
        self.assertLess(item.height, item.width)
        widget.add_rectangle()
        widget.history.edit("Move far panel", lambda doc: setattr(doc.pieces[-1], "layout", [3000, 0, 0]))
        widget.refresh()
        widget.fit_button.click()
        self.app.processEvents()
        for piece_item in widget.canvas._piece_items:
            rect = piece_item.sceneBoundingRect()
            for corner in (rect.topLeft(), rect.bottomRight()):
                pixel = widget.canvas.mapFromScene(corner)
                self.assertTrue(widget.canvas.viewport().rect().contains(pixel))

    def test_workflow_load_automatically_fits_all_pattern_panels(self):
        from echograph.patterns.commands import rectangle, set_edge_kind
        document = PatternDocument(pieces=[rectangle("Front"), rectangle("Back")])
        set_edge_kind(document.pieces[0], document.pieces[0].segments[0].id, "cubic")
        document.pieces[1].layout[0] = 4000
        target = self.path.with_name("wide-workflow-pattern.pattern.json")
        save_document(target, document)
        item = self.node(path=target)
        widget = next(p.widget() for p in item._plugin_proxies if isinstance(p.widget(), PatternNodeWidget))
        QtTest.QTest.qWait(20)
        self.assertFalse(widget._auto_fit_on_load)
        self.assertEqual(widget.edge_combo.currentIndex(), -1)
        self.assertEqual(widget._selected_edge_id, "")
        self.assertFalse(any(isinstance(i, HandleItem) and i.control_index >= 0 for i in widget.canvas.scene().items()))
        self.assertEqual(len(widget.canvas._piece_items), 2)
        for piece_item in widget.canvas._piece_items:
            rect = piece_item.sceneBoundingRect()
            for corner in (rect.topLeft(), rect.bottomRight()):
                self.assertTrue(widget.canvas.viewport().rect().contains(widget.canvas.mapFromScene(corner)))

    def test_node_empty_canvas_click_clears_points_and_edges_after_refresh(self):
        item = self.node()
        widget = next(p.widget() for p in item._plugin_proxies if isinstance(p.widget(), PatternNodeWidget))
        QtTest.QTest.qWait(20)
        piece = widget.history.document.pieces[0]
        from echograph.patterns.commands import set_edge_kind
        widget.history.edit("Curve", lambda doc: set_edge_kind(doc.pieces[0], piece.segments[0].id, "cubic"))
        widget._canvas_edge_selected(piece.id, piece.segments[0].id)
        self.assertEqual(sum(isinstance(i, HandleItem) and i.control_index >= 0 for i in widget.canvas.scene().items()), 2)
        widget.canvas._on_point_clicked(piece.id, piece.points[0].id, False)
        pixel = widget.canvas.mapFromScene(QtCore.QPointF(-40, -40))
        QtTest.QTest.mouseClick(widget.canvas.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=pixel)
        self.app.processEvents()
        widget.refresh()
        self.assertFalse(widget._selected_points)
        self.assertFalse(widget.canvas._selected_points)
        self.assertEqual(widget.edge_combo.currentIndex(), -1)
        self.assertEqual(widget._selected_edge_id, "")
        self.assertFalse(any(isinstance(i, HandleItem) and i.control_index >= 0 for i in widget.canvas.scene().items()))
