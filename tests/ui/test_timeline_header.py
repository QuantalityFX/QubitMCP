"""Exercise the real timeline controls without requiring an OpenGL surface."""
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtWidgets

from echograph.ui.gl_view import GraphGLView


class TimelineHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.graph = QtWidgets.QGraphicsScene()
        self.view = GraphGLView(self.graph)
        self.view.resize(1600, 900)
        self.view._build_timeline_panel()
        self.panel = self.view._timeline_panel
        self.assertIsNotNone(self.panel)
        # Show only the QWidget overlay; keep the OpenGL viewport hidden.
        self.host = QtWidgets.QWidget()
        self.host.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        self.panel.setParent(self.host)
        self.host.show()
        self.panel.show()

    def tearDown(self):
        self.view.deleteLater()
        self.host.deleteLater()
        self.graph.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def resize_timeline(self, width):
        self.host.resize(width, 900)
        self.view.resize(width, 900)
        self.view._layout_timeline_panel()
        self.app.processEvents()

    def test_controls_wrap_after_loop_and_restore_without_losing_state(self):
        view = self.view
        header = self.panel._header_layout
        primary = [header.primary.itemAt(i).widget() for i in range(header.primary.count())]
        secondary = [header.secondary.itemAt(i).widget() for i in range(header.secondary.count())
                     if header.secondary.itemAt(i).widget() is not None]
        self.assertIs(primary[-1], view._timeline_loop_btn)
        self.assertEqual(primary.index(view._timeline_speed_spin),
                         primary.index(view._timeline_texture_seed_spin) + 2)
        self.assertEqual(secondary.index(view._timeline_cloth_btn),
                         secondary.index(view._timeline_fx_instances_btn) + 1)
        blocker = QtCore.QSignalBlocker(view._timeline_texture_seed_spin)
        view._timeline_texture_seed_spin.setValue(4321)
        del blocker
        margins = self.panel.layout().contentsMargins()
        outer_margin = margins.left() + margins.right()
        breakpoint = (header.primary.sizeHint().width() + header.secondary.sizeHint().width()
                      + header.horizontalSpacing() + outer_margin)
        narrow = max(header.primary.sizeHint().width(), header.secondary.sizeHint().width()) + outer_margin + 20
        wide = breakpoint + 80
        original_height = None
        for width in (wide, narrow, breakpoint - 1, breakpoint, narrow, wide):
            with self.subTest(width=width):
                self.resize_timeline(width)
                wrapped = width < breakpoint
                self.assertEqual(header.wrapped, wrapped)
                self.assertEqual(self.panel.width(), width)
                if original_height is None:
                    original_height = self.panel.height()
                if wrapped:
                    self.assertGreater(min(w.geometry().top() for w in secondary),
                                       max(w.geometry().bottom() for w in primary))
                    self.assertEqual(secondary[0].x(), primary[0].x())
                    self.assertGreater(self.panel.height(), original_height)
                else:
                    self.assertEqual(self.panel.height(), original_height)
                    self.assertGreater(secondary[0].x(), primary[-1].geometry().right())
                for i, widget in enumerate(primary + secondary):
                    self.assertTrue(self.panel.rect().contains(widget.geometry()))
                    for other in (primary + secondary)[i + 1:]:
                        self.assertFalse(widget.geometry().intersects(other.geometry()))
                self.assertEqual(view._timeline_texture_seed_spin.value(), 4321)
        # Reflow keeps the original controls and their signal connections.
        loop_enabled = view._timeline_loop_enabled
        with patch.object(view, '_timeline_range_save_to_disk') as save:
            view._timeline_loop_btn.click()
            self.assertEqual(view._timeline_loop_enabled, not loop_enabled)
            save.assert_called_once()


if __name__ == '__main__':
    unittest.main()
