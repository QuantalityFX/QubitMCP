import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest

from PySide6 import QtCore, QtGui, QtWidgets

from echograph.persistence import _node_to_dict
from echograph.model import GraphNode
from echograph.ui.node_item import NodeItem
from echograph.ui.view import GraphView


APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class DropScene(QtWidgets.QGraphicsScene):
    def __init__(self):
        super().__init__()
        self.nodes = []
        self.setSceneRect(-2000, -2000, 4000, 4000)

    def add_node(self, node, position):
        node.name += str(len(self.nodes) + 1)
        item = NodeItem(node)
        item.setPos(position)
        self.addItem(item)
        self.nodes.append(item)
        return item


class GraphImageDropTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.paths = []
        for name in ("image one.png", "image two.png"):
            path = Path(self.directory.name) / name
            image = QtGui.QImage(64, 48, QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.red)
            self.assertTrue(image.save(str(path)))
            self.paths.append(str(path))
        self.scene = DropScene()
        self.view = GraphView(self.scene)
        self.view.resize(800, 600)
        self.view.show()
        APP.processEvents()
        self.addCleanup(self.scene.clear)
        self.addCleanup(self.view.close)

    def drop(self, urls, point=None):
        mime = QtCore.QMimeData()
        mime.setUrls(urls)
        point = QtCore.QPoint(230, 170) if point is None else point
        enter = QtGui.QDragEnterEvent(point, QtCore.Qt.CopyAction, mime,
                                      QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(self.view.viewport(), enter)
        drop = QtGui.QDropEvent(QtCore.QPointF(point), QtCore.Qt.CopyAction, mime,
                               QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(self.view.viewport(), drop)
        return enter, drop, self.view.mapToScene(point)

    def test_explorer_drop_creates_visible_collection_at_zoomed_drop_position(self):
        self.view.scale(1.7, 1.7)
        self.view.centerOn(450, -200)
        enter, drop, position = self.drop([QtCore.QUrl.fromLocalFile(self.paths[0])])
        self.assertTrue(enter.isAccepted())
        self.assertTrue(drop.isAccepted())
        self.assertEqual(drop.dropAction(), QtCore.Qt.CopyAction)
        item, = self.scene.nodes
        self.assertEqual(item.model.kind, "image_collection")
        self.assertEqual(item.pos(), position)
        self.assertTrue(item.isSelected())
        self.assertEqual(_node_to_dict(item.model)["image_collection_paths"], self.paths[:1])
        canvases = [widget for proxy in item._plugin_proxies
                    for widget in proxy.widget().findChildren(QtWidgets.QWidget)
                    if hasattr(widget, "pixmaps")]
        self.assertTrue(any(canvas.pixmaps for canvas in canvases))
        for proxy in item._plugin_proxies:
            self.assertGreaterEqual(item.height, proxy.pos().y() + proxy.size().height())

    def test_multiple_files_filter_invalid_urls_and_deduplicate_in_one_collection(self):
        text = Path(self.directory.name) / "not an image.txt"
        text.write_text("text", encoding="utf-8")
        urls = [QtCore.QUrl.fromLocalFile(path) for path in self.paths + self.paths[:1]]
        urls += [QtCore.QUrl.fromLocalFile(str(text)), QtCore.QUrl("https://example.com/a.png"),
                 QtCore.QUrl.fromLocalFile(self.directory.name)]
        _, drop, _ = self.drop(urls)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(len(self.scene.nodes), 1)
        self.assertEqual(self.scene.nodes[0].model._image_collection_state["paths"], self.paths)

    def test_invalid_files_and_3d_mode_do_not_create_collections(self):
        enter, _, _ = self.drop([QtCore.QUrl("https://example.com/a.png")])
        self.assertFalse(enter.isAccepted())
        self.view._mode_3d = True
        enter, _, _ = self.drop([QtCore.QUrl.fromLocalFile(self.paths[0])])
        self.assertFalse(enter.isAccepted())
        self.assertEqual(self.scene.nodes, [])

    def test_drop_over_collection_image_appends_without_creating_another_node(self):
        self.drop([QtCore.QUrl.fromLocalFile(self.paths[0])])
        item, = self.scene.nodes
        self.view.scale(0.7, 0.7)
        self.view.centerOn(item)
        APP.processEvents()
        canvas = item._image_collection_canvas
        proxy = canvas.window().graphicsProxyWidget()
        point_in_body = canvas.mapTo(canvas.window(), canvas.rect().center())
        point = self.view.mapFromScene(proxy.mapToScene(QtCore.QPointF(point_in_body)))
        self.assertIsNot(self.view.itemAt(point), item)
        position = QtCore.QPointF(item.pos())
        text = Path(self.directory.name) / "not an image.txt"
        text.write_text("text", encoding="utf-8")
        urls = [QtCore.QUrl.fromLocalFile(path) for path in self.paths]
        urls += [QtCore.QUrl.fromLocalFile(str(text)), QtCore.QUrl.fromLocalFile(self.paths[1])]
        enter, drop, _ = self.drop(urls, point)
        self.assertTrue(enter.isAccepted())
        self.assertTrue(drop.isAccepted())
        self.assertEqual(self.scene.nodes, [item])
        self.assertEqual(item.pos(), position)
        self.assertEqual(item.model._image_collection_state["paths"], self.paths)
        self.assertEqual(canvas.paths, self.paths)
        self.assertEqual(len(canvas.pixmaps), 2)
        self.assertEqual(_node_to_dict(item.model)["image_collection_paths"], self.paths)
        for proxy in item._plugin_proxies:
            self.assertGreaterEqual(item.height, proxy.pos().y() + proxy.size().height() + item._PADDING)

    def test_drop_over_empty_collection_header_loads_that_collection(self):
        item = self.scene.add_node(GraphNode("Existing", kind="image_collection"), QtCore.QPointF(50, 50))
        self.view.centerOn(item)
        APP.processEvents()
        point = self.view.mapFromScene(item.mapToScene(QtCore.QPointF(item.width / 2, 12)))
        _, drop, _ = self.drop([QtCore.QUrl.fromLocalFile(self.paths[0])], point)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(self.scene.nodes, [item])
        self.assertEqual(item._image_collection_canvas.paths, self.paths[:1])
        self.drop([QtCore.QUrl.fromLocalFile(self.paths[0])], point)
        self.assertEqual(item.model._image_collection_state["paths"], self.paths[:1])

    def test_drop_on_other_node_keeps_creating_a_collection(self):
        other = self.scene.add_node(GraphNode("Other", kind="node"), QtCore.QPointF(50, 50))
        self.view.centerOn(other)
        point = self.view.mapFromScene(other.mapToScene(QtCore.QPointF(other.width / 2, 12)))
        _, drop, position = self.drop([QtCore.QUrl.fromLocalFile(self.paths[0])], point)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(len(self.scene.nodes), 2)
        self.assertEqual(self.scene.nodes[-1].model.kind, "image_collection")
        self.assertEqual(self.scene.nodes[-1].pos(), position)


if __name__ == "__main__":
    unittest.main()
