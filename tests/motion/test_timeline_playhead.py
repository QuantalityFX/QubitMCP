import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import unittest

from PySide6 import QtCore, QtTest, QtWidgets

from echograph.ui.gl_view import GraphGLView


class TimelinePlayheadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.view = GraphGLView(QtWidgets.QGraphicsScene())
        self.view.resize(900, 600)
        self.view.show()
        self.view._build_timeline_panel()
        self.view._timeline_panel.show()
        self.slider = self.view._timeline_frame_slider
        self.app.processEvents()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_click_focuses_playhead_and_arrows_step_one_frame(self):
        self.slider.setValue(20)
        QtTest.QTest.mouseClick(
            self.slider,
            QtCore.Qt.LeftButton,
            pos=QtCore.QPoint(self.slider.width() // 2, self.slider.height() // 2),
        )
        self.assertTrue(self.slider.hasFocus())
        clicked_frame = self.slider.value()
        initial_handle = self.slider._focus_icon.geometry()
        QtTest.QTest.keyClick(self.slider, QtCore.Qt.Key_Right)
        self.assertEqual(self.slider.value(), clicked_frame + 1)
        moved_handle = self.slider._focus_icon.geometry()
        self.assertGreater(moved_handle.center().x(), initial_handle.center().x())
        image = self.slider.grab().toImage()
        red_pixels = [
            (x, y)
            for y in range(image.height())
            for x in range(image.width())
            if (lambda c: c.red() > 180 and c.red() > c.green() * 1.5 and c.red() > c.blue() * 1.5)(
                image.pixelColor(x, y)
            )
        ]
        self.assertGreater(len(red_pixels), 0)
        mean_red_x = sum(x for x, _ in red_pixels) / len(red_pixels)
        self.assertAlmostEqual(mean_red_x, moved_handle.center().x(), delta=2)
        self.assertTrue(all(moved_handle.contains(x, y) for x, y in red_pixels))
        QtTest.QTest.keyClick(self.slider, QtCore.Qt.Key_Left)
        self.assertEqual(self.slider.value(), clicked_frame)
        self.assertEqual(self.slider._focus_icon.geometry(), initial_handle)
        self.assertTrue(self.slider._focus_icon.isVisible())
        active_icon = Path(__file__).resolve().parents[2] / 'icons' / 'KeyframeHandle_Active_Icon.png'
        self.assertTrue(active_icon.is_file())

    def test_arrow_steps_pan_viewport_past_visible_end(self):
        self.view._timeline_total_max = 240
        self.view._timeline_in_frame = 0
        self.view._timeline_view_start = 0
        self.view._timeline_view_span = 120
        self.view._timeline_frame_spin.setValue(120)
        self.assertEqual(self.view._timeline_view_start, 0)
        self.slider.setFocus()
        QtTest.QTest.keyClick(self.slider, QtCore.Qt.Key_Right)
        self.assertEqual(self.view._timeline_current_frame(), 121)
        self.assertEqual(self.view._timeline_view_start, 1)
        self.assertEqual(self.slider.value(), self.slider.maximum())

    def test_timeline_buttons_after_speed_share_wrapped_row(self):
        header = self.view._timeline_panel._header_layout
        self.assertGreaterEqual(header.primary.indexOf(self.view._timeline_speed_spin), 0)
        self.assertLess(header.primary.indexOf(self.view._timeline_play_btn), 0)
        self.assertGreaterEqual(header.secondary.indexOf(self.view._timeline_play_btn), 0)
        self.assertGreaterEqual(header.secondary.indexOf(self.view._timeline_loop_btn), 0)
        self.assertGreaterEqual(header.secondary.indexOf(self.view._timeline_fx_instances_btn), 0)
        header.reflow(10000)
        self.assertFalse(header.wrapped)
        required = header.primary.sizeHint().width() + header.secondary.sizeHint().width() + header.horizontalSpacing()
        header.reflow(required - 1)
        self.assertTrue(header.wrapped)

    def test_visible_timeline_keeps_both_toolbar_rows_from_overlapping(self):
        panel = self.view._timeline_panel
        panel.show()
        self.view._layout_timeline_panel()
        header = panel._header_layout
        margins = panel.layout().contentsMargins()
        expected = (max(header.primary.sizeHint().width(), header.secondary.sizeHint().width(),
                        self.view._timeline_range_controls.minimumSizeHint().width())
                    + margins.left() + margins.right())
        self.assertGreaterEqual(self.view.minimumWidth(), expected)
        self.view.resize(400, 600)
        self.view._layout_timeline_panel()
        self.app.processEvents()
        self.assertGreaterEqual(self.view.width(), expected)
        self.assertEqual(panel.width(), self.view.width())
        self.assertTrue(header.wrapped)
        visible = [row.itemAt(i).widget() for row in (header.primary, header.secondary)
                   for i in range(row.count())
                   if row.itemAt(i).widget() is not None and row.itemAt(i).widget().isVisible()]
        for i, widget in enumerate(visible):
            self.assertTrue(panel.rect().contains(widget.geometry()))
            for other in visible[i+1:]:
                self.assertFalse(widget.geometry().intersects(other.geometry()))
        panel.hide()
        self.view._layout_timeline_panel()
        self.assertEqual(self.view.minimumWidth(), 400)


if __name__ == '__main__':
    unittest.main()
