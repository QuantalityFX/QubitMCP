import unittest

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.patterns.commands import DocumentHistory, rectangle
from echograph.patterns.document import PatternDocument
from echograph.patterns.elastic import elastic_for_edge
from echograph.ui.node_item import NodeItem
from echograph.ui.pattern_editor.canvas import SEW_COLORS
from echograph.ui.pattern_editor.editor import PatternEditor
from nodes.pattern_design.spec import PatternNodeWidget


class SewingEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.front, self.back = rectangle("Front", 100, 150), rectangle("Back", 100, 150)
        self.back.layout = [250, 0, 15]
        self.history = DocumentHistory(PatternDocument(pieces=[self.front, self.back]))
        self.editor = PatternEditor(history=self.history)
        self.editor.show()
        self.app.processEvents()
        self.editor.canvas.fit_pieces()

    def tearDown(self):
        self.history.mark_saved()
        self.editor.close()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def click_edge(self, canvas, owner, edge_id):
        item = next(item for item in canvas._piece_items if item.piece.id == owner)
        edge = item.piece.segment(edge_id)
        a, b = item.piece.point(edge.start), item.piece.point(edge.end)
        position = item.mapToScene(QtCore.QPointF((a.x + b.x) / 2, (a.y + b.y) / 2))
        pixel = canvas.mapFromScene(position)
        QtTest.QTest.mouseClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton,
                               QtCore.Qt.KeyboardModifier.NoModifier, pixel)
        self.app.processEvents()
        return pixel

    def test_two_edge_clicks_create_pair_draw_annotations_and_support_undo(self):
        canvas = self.editor.canvas
        before = self.history.document.to_dict()["pieces"]
        self.assertFalse(self.editor.sew_action.icon().pixmap(24, 24).isNull())
        self.editor.sew_action.trigger()
        self.assertTrue(canvas.sewing)
        first_pixel = self.click_edge(canvas, self.front.id, self.front.segments[1].id)
        self.assertEqual(canvas._sew_source, (self.front.id, self.front.segments[1].id))
        self.click_edge(canvas, self.back.id, self.back.segments[3].id)
        pair, left, right = canvas.sewing_visuals()[0]
        self.assertEqual(pair["number"], 1)
        self.assertEqual(left.elementCount(), 2)
        self.assertEqual(right.elementCount(), 2)
        self.assertEqual(self.history.document.to_dict()["pieces"], before)
        image = canvas.grab().toImage()
        self.assertTrue(any(image.pixelColor(first_pixel.x() + x, first_pixel.y() + y).name() == SEW_COLORS[0]
                            for x in range(-4, 5) for y in range(-4, 5)))
        self.editor.undo()
        self.assertEqual(canvas.sewing_visuals(), [])
        self.editor.redo()
        self.assertEqual(len(canvas.sewing_visuals()), 1)
        QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Escape)
        self.assertFalse(canvas.sewing)
        self.assertFalse(self.editor.sew_action.isChecked())

    def test_click_same_edge_cancels_pending_pair_and_double_click_never_splits(self):
        canvas = self.editor.canvas
        canvas.set_sew_mode(True)
        edge = self.front.segments[0].id
        pixel = self.click_edge(canvas, self.front.id, edge)
        self.click_edge(canvas, self.front.id, edge)
        self.assertIsNone(canvas._sew_source)
        QtTest.QTest.mouseDClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton,
                                QtCore.Qt.KeyboardModifier.NoModifier, pixel)
        self.app.processEvents()
        self.assertEqual(len(self.history.document.piece(self.front.id).segments), 4)
        self.assertFalse(self.history.document.metadata.get("sew_pairs"))
        canvas.start_outline()
        self.assertFalse(canvas.sewing)
        self.assertFalse(self.editor.sew_action.isChecked())
        canvas.cancel_outline()

    def test_annotations_follow_panel_placement(self):
        self.editor.canvas.set_sew_mode(True)
        self.click_edge(self.editor.canvas, self.front.id, self.front.segments[1].id)
        self.click_edge(self.editor.canvas, self.back.id, self.back.segments[3].id)
        old = self.editor.canvas.sewing_visuals()[0][1].pointAtPercent(.5)
        self.editor.edit("Arrange", lambda doc: setattr(doc.piece(self.front.id), "layout", [20, 30, 0]))
        current = self.editor.canvas.sewing_visuals()[0][1].pointAtPercent(.5)
        self.assertAlmostEqual(current.x() - old.x(), 20)
        self.assertAlmostEqual(current.y() - old.y(), 30)

    def test_panel_drag_repaints_old_and_new_connectors_between_panels(self):
        class PaintRegions(QtCore.QObject):
            def __init__(self, parent):
                super().__init__(parent)
                self.region = QtGui.QRegion()

            def eventFilter(self, watched, event):
                if event.type() == QtCore.QEvent.Type.Paint:
                    self.region |= event.region()
                return False

        canvas = self.editor.canvas
        canvas.set_sew_mode(True)
        self.click_edge(canvas, self.front.id, self.front.segments[1].id)
        self.click_edge(canvas, self.back.id, self.back.segments[3].id)
        canvas.set_sew_mode(False)
        QtTest.QTest.qWait(30)
        recorder = PaintRegions(canvas.viewport())
        canvas.viewport().installEventFilter(recorder)

        def connector_middle():
            _, left, right = canvas.sewing_visuals()[0]
            return canvas.mapFromScene((left.pointAtPercent(.5) + right.pointAtPercent(.5)) / 2)

        moved = next(item for item in canvas._piece_items if item.piece.id == self.front.id)
        for delta in (QtCore.QPointF(0, 30), QtCore.QPointF(20, -20), QtCore.QPointF(-10, 30)):
            old = connector_middle()
            recorder.region = QtGui.QRegion()
            moved.setPos(moved.pos() + delta)
            QtTest.QTest.qWait(30)
            new = connector_middle()
            for point in (old, new):
                required = QtGui.QRegion(QtCore.QRect(point.x() - 4, point.y() - 4, 9, 9))
                self.assertTrue(required.subtracted(recorder.region).isEmpty(),
                                "Moving a panel must repaint connectors outside the panel's bounds")
        canvas.viewport().removeEventFilter(recorder)

    def test_node_canvas_sew_button_and_border_containment(self):
        graph = QtWidgets.QGraphicsScene()
        model = GraphNode("Pattern", kind="pattern_design")
        model._pattern_history = self.history
        node = NodeItem(model)
        graph.addItem(node)
        self.app.processEvents()
        proxy = next(proxy for proxy in node._plugin_proxies if isinstance(proxy.widget(), PatternNodeWidget))
        body = proxy.widget()
        body.sew_button.click()
        self.assertTrue(body.canvas.sewing)
        body.canvas._sew_edge_clicked((self.front.id, self.front.segments[1].id))
        body.canvas._sew_edge_clicked((self.back.id, self.back.segments[3].id))
        self.assertEqual(len(self.history.document.metadata["sew_pairs"]), 1)
        body.elastic_button.click()
        self.assertTrue(body.canvas.elastic)
        self.assertFalse(body.canvas.sewing)
        body.canvas.elasticRequested.emit(self.front.id, self.front.segments[2].id)
        body.elastic_controls.strength.setValue(2500)
        self.assertEqual(elastic_for_edge(self.history.document, self.front.id, self.front.segments[2].id)["strength"], 2500)
        rect = proxy.mapRectToParent(proxy.boundingRect())
        self.assertGreaterEqual(node.height + .01, rect.bottom() + node._PADDING)
        self.assertGreaterEqual(node.width + .01, rect.right())
        self.assertLessEqual(body.sew_button.geometry().bottom(), body.height())
        self.assertLessEqual(body.elastic_controls.geometry().bottom(), body.height())
        graph.clear()

    def test_elastic_tool_edits_edge_strength_and_rest_length_without_splitting(self):
        canvas = self.editor.canvas
        edge = self.front.segments[2].id
        self.editor.elastic_action.trigger()
        self.assertTrue(canvas.elastic)
        pixel = self.click_edge(canvas, self.front.id, edge)
        self.assertEqual(len(canvas.elastic_visuals()), 1)
        self.assertIs(self.editor.property_tabs.currentWidget(), self.editor.edges_tab)
        self.editor.elastic_controls.strength.setValue(3500)
        self.editor.elastic_controls.rest_length.setValue(70)
        row = elastic_for_edge(self.history.document, self.front.id, edge)
        self.assertEqual((row["strength"], row["rest_ratio"]), (3500, .7))
        QtTest.QTest.mouseDClick(canvas.viewport(), QtCore.Qt.MouseButton.LeftButton,
                                QtCore.Qt.KeyboardModifier.NoModifier, pixel)
        self.assertEqual(len(self.history.document.piece(self.front.id).segments), 4)
        self.editor.undo()
        self.assertEqual(elastic_for_edge(self.history.document, self.front.id, edge)["rest_ratio"], .8)
        self.editor.elastic_controls.enabled.setChecked(False)
        self.assertEqual(canvas.elastic_visuals(), [])
        QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Escape)
        self.assertFalse(canvas.elastic)
        self.assertFalse(self.editor.elastic_action.isChecked())


if __name__ == "__main__":
    unittest.main()
