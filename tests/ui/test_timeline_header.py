"""Exercise the real timeline controls without requiring an OpenGL surface."""
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

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

    def test_controls_wrap_after_speed_and_restore_without_losing_state(self):
        view = self.view
        header = self.panel._header_layout
        primary = [header.primary.itemAt(i).widget() for i in range(header.primary.count())]
        secondary = [header.secondary.itemAt(i).widget() for i in range(header.secondary.count())
                     if header.secondary.itemAt(i).widget() is not None]
        self.assertIs(primary[-1], view._timeline_speed_spin)
        self.assertIn(view._timeline_loop_btn, secondary)
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
                    if widget.isHidden():
                        continue
                    self.assertTrue(self.panel.rect().contains(widget.geometry()))
                    for other in (primary + secondary)[i + 1:]:
                        if not other.isHidden():
                            self.assertFalse(widget.geometry().intersects(other.geometry()))
                self.assertEqual(view._timeline_texture_seed_spin.value(), 4321)
        # Reflow keeps the original controls and their signal connections.
        loop_enabled = view._timeline_loop_enabled
        with patch.object(view, '_timeline_range_save_to_disk') as save:
            view._timeline_loop_btn.click()
            self.assertEqual(view._timeline_loop_enabled, not loop_enabled)
            save.assert_called_once()

    def test_every_toolbar_button_highlights_on_mouse_entry_and_clears_on_exit(self):
        self.resize_timeline(1600)
        self.panel.move(0, 0)
        self.panel.show()
        header = self.panel._header_layout
        buttons = [row.itemAt(i).widget()
                   for row in (header.primary, header.secondary)
                   for i in range(row.count())
                   if isinstance(row.itemAt(i).widget(), QtWidgets.QPushButton)]
        self.assertEqual(len(buttons), 16)
        buttons.append(self.view._timeline_back_btn)
        self.view._timeline_back_btn.show()
        self.app.processEvents()
        window = self.host.windowHandle()
        empty = QtCore.QPoint(self.host.width() - 2, self.host.height() - 2)

        def move_to(point):
            # Window-level input dispatches the real enter/leave sequence.
            QtTest.QTest.mouseMove(window, point)
            self.app.processEvents()

        for button in buttons:
            checked_states = (False, True) if button.isCheckable() else (False,)
            for enabled in (True, False):
                for checked in checked_states:
                    with self.subTest(button=button.objectName(), enabled=enabled, checked=checked):
                        blocker = QtCore.QSignalBlocker(button)
                        button.setEnabled(enabled)
                        button.setChecked(checked)
                        del blocker
                        move_to(empty)
                        idle = button.grab().toImage()
                        move_to(button.mapTo(self.host, button.rect().center()))
                        hover = button.grab().toImage()
                        self.assertNotEqual(idle, hover)
                        move_to(empty)
                        self.assertEqual(idle, button.grab().toImage())

    def test_bracket_glyphs_keep_optical_alignment_and_their_background(self):
        self.resize_timeline(1600)
        self.panel.move(0, 0)
        self.panel.show()
        for button in (self.view._timeline_mark_in_btn, self.view._timeline_mark_out_btn):
            with self.subTest(button=button.text()):
                image = button.grab().toImage()
                self.assertEqual(image.pixelColor(2, image.height() // 2).name(), '#1f2937')
                if not QtGui.QFontMetrics(button.font()).inFont(button.text()):
                    self.skipTest('This Qt platform has no font for bracket glyphs')
                ink = [(x, y) for x in range(image.width()) for y in range(image.height())
                       if min(image.pixelColor(x, y).red(), image.pixelColor(x, y).green(),
                              image.pixelColor(x, y).blue()) > 150]
                self.assertTrue(ink)
                left, right = min(x for x, y in ink), max(x for x, y in ink)
                top, bottom = min(y for x, y in ink), max(y for x, y in ink)
                self.assertLessEqual(abs((left + right) - (image.width() - 1)), 1)
                self.assertLessEqual(abs((top + bottom) - (image.height() + 1)), 1)


if __name__ == '__main__':
    unittest.main()
