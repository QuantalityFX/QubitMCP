from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6 import QtWidgets

from echograph.model import GraphNode
from echograph.ui.node_item import NodeItem


class RenderNodeLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_scene_connection_recompute_and_rebuild_preserve_creation_width(self):
        class Graph(QtWidgets.QGraphicsScene):
            def _ordered_in_edges(self, item):
                return self.edges

        graph = Graph()
        graph.edges = []
        render = NodeItem(GraphNode("Render", kind="render"))
        graph.addItem(render)
        self.app.processEvents()
        width = render.width
        source = QtWidgets.QGraphicsRectItem()
        source.model = GraphNode("Scene", kind="scene")
        graph.addItem(source)
        connected_edge = SimpleNamespace(src=source, dst_port_name="scene")
        with patch("nodes.scene.spec._collect_assets", return_value=[]):
            for connected in (True, False, True):
                graph.edges = [connected_edge] if connected else []
                render._recompute_height()
                self.assertEqual(render.width, width)
                for proxy in render._plugin_proxies:
                    rect = proxy.mapRectToParent(proxy.boundingRect())
                    self.assertLessEqual(rect.bottom() + 9.99, render.height)
                render._build_widgets()
                self.app.processEvents()
                self.assertEqual(render.width, width)
                for proxy in render._plugin_proxies:
                    rect = proxy.mapRectToParent(proxy.boundingRect())
                    self.assertLessEqual(rect.right(), render.width + .01)
                    self.assertLessEqual(rect.bottom() + 9.99, render.height)
            # Workflow loading constructs fresh controls with the link present.
            loaded = NodeItem(GraphNode("Loaded Render", kind="render"))
            graph.addItem(loaded)
            loaded._recompute_height()
            loaded._build_widgets()
            self.app.processEvents()
            self.assertEqual(loaded.width, width)
        graph.clear()


if __name__ == "__main__":
    unittest.main()
