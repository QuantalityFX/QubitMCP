import unittest

from PySide6 import QtWidgets

from echograph.model import GraphNode
from echograph.ui.node_item import NodeItem


class LightNodeLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_type_switch_resizes_border_and_keeps_every_wrapper_inside(self):
        scene = QtWidgets.QGraphicsScene()
        node = NodeItem(GraphNode("light", kind="light"))
        scene.addItem(node)
        heights = {}
        for kind in ("directional", "spot", "point", "area", "directional", "spot"):
            combo = next(widget for proxy in node._param_proxies
                         for widget in proxy.widget().findChildren(QtWidgets.QComboBox))
            combo.setCurrentIndex(combo.findData(kind))
            self.app.processEvents()
            self.app.processEvents()
            rectangles = [proxy.mapRectToParent(proxy.boundingRect()) for proxy in node._param_proxies]
            for rect in rectangles:
                self.assertGreaterEqual(node.height, rect.bottom() + node._PADDING - .01)
            for previous, current in zip(rectangles, rectangles[1:]):
                self.assertLessEqual(previous.bottom(), current.top() + .01)
            if kind in heights:
                self.assertEqual(node.height, heights[kind])
            heights[kind] = node.height
        self.assertGreater(heights["spot"], heights["point"])
        self.assertGreater(heights["point"], heights["directional"])
        self.assertGreater(heights["area"], heights["spot"])
        scene.clear()
