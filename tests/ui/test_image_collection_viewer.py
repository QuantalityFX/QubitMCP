import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.persistence import _node_to_dict, deserialize_scene
from echograph.ui.node_item import NodeItem
from echograph.ui.view import GraphView


APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class ImageCollectionViewerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.paths = []
        for name, size, color in (("large image.png", (2048, 1536), "red"),
                                  ("second.png", (900, 700), "green"),
                                  ("third.png", (800, 600), "blue")):
            path = Path(directory.name) / name
            image = QtGui.QImage(*size, QtGui.QImage.Format_RGB32)
            image.fill(QtGui.QColor(color))
            self.assertTrue(image.save(str(path)))
            self.paths.append(str(path))

        model = GraphNode("Images", kind="image_collection")
        model._image_collection_state = {"paths": self.paths.copy()}
        self.scene = QtWidgets.QGraphicsScene()
        self.scene.setSceneRect(-4000, -4000, 8000, 8000)
        self.item = NodeItem(model)
        self.scene.addItem(self.item)
        self.view = GraphView(self.scene)
        self.view.setMouseTracking(True)
        self.view.resize(1100, 900)
        self.view.show()
        self.view.activateWindow()
        self.view.centerOn(self.item)
        self.body = self.item._plugin_proxies[0].widget()
        self.canvas = next(widget for widget in self.body.findChildren(QtWidgets.QWidget)
                           if hasattr(widget, "rects") and hasattr(widget, "pixmaps"))
        self.addCleanup(self.view.close)
        self.addCleanup(self.scene.clear)
        self.addCleanup(self.close_viewer)
        APP.processEvents()
        QtTest.QTest.mouseMove(self.view.viewport(), QtCore.QPoint(2, 2))

    def close_viewer(self):
        if self.canvas._viewer is not None:
            self.canvas._viewer.close()

    def drag_border(self, point, delta, mode):
        start = self.view.mapFromScene(self.item.mapToScene(point))
        end = self.view.mapFromScene(self.item.mapToScene(point + delta))
        QtTest.QTest.mousePress(self.view.viewport(), QtCore.Qt.LeftButton, pos=start)
        self.assertEqual(self.item._note_resize_mode, mode)
        event = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(end),
                                 QtCore.QPointF(self.view.viewport().mapToGlobal(end)),
                                 QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(self.view.viewport(), event)
        QtTest.QTest.mouseRelease(self.view.viewport(), QtCore.Qt.LeftButton, pos=end)
        APP.processEvents()
        self.assertIsNone(self.item._note_resize_mode)

    def assert_images_fit(self):
        self.assertEqual(len(self.canvas.rects), len(self.paths))
        for index, rect in enumerate(self.canvas.rects):
            self.assertTrue(self.canvas.rect().contains(rect))
            for other in self.canvas.rects[index + 1:]:
                self.assertFalse(rect.intersects(other))
        for proxy in self.item._plugin_proxies:
            self.assertGreaterEqual(self.item.height,
                                    proxy.pos().y() + proxy.size().height() + self.item._PADDING)

    def image_position(self, index):
        proxy = self.canvas.graphicsProxyWidget() or self.body.graphicsProxyWidget()
        point = self.canvas.mapTo(self.body, self.canvas.rects[index].center())
        return self.view.mapFromScene(proxy.mapToScene(QtCore.QPointF(point)))

    def double_click_image(self, index):
        position = self.image_position(index)
        QtTest.QTest.mouseClick(self.view.viewport(), QtCore.Qt.LeftButton, pos=position)
        QtTest.QTest.mouseDClick(self.view.viewport(), QtCore.Qt.LeftButton, pos=position)
        QtTest.QTest.mouseRelease(self.view.viewport(), QtCore.Qt.LeftButton, pos=position)
        APP.processEvents()

    def open_viewer(self, index=0):
        self.double_click_image(index)
        viewer = self.canvas._viewer
        self.assertIsNotNone(viewer)
        self.assertTrue(viewer.isVisible())
        return viewer

    def swipe(self, viewer, delta):
        viewport = viewer.image_view.viewport()
        start = viewport.rect().center()
        end = start + delta
        QtTest.QTest.mousePress(viewport, QtCore.Qt.LeftButton, pos=start)
        event = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(end),
                                 QtCore.QPointF(viewport.mapToGlobal(end)),
                                 QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(viewport, event)
        QtTest.QTest.mouseRelease(viewport, QtCore.Qt.LeftButton, pos=end)

    def scroll_viewer(self, viewer, delta):
        viewport = viewer.image_view.viewport()
        pos = viewport.rect().center()
        event = QtGui.QWheelEvent(QtCore.QPointF(pos), QtCore.QPointF(viewport.mapToGlobal(pos)),
                                 QtCore.QPoint(), QtCore.QPoint(0, delta), QtCore.Qt.NoButton,
                                 QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(viewport, event)

    def test_mouse_side_buttons_navigate_and_respect_pinned_comparison(self):
        viewer = self.open_viewer()
        viewport = viewer.image_view.viewport()
        def click(button):
            QtTest.QTest.mouseClick(viewport, button, pos=viewport.rect().center())
        click(QtCore.Qt.ForwardButton)
        self.assertEqual(viewer._index, 1)
        click(QtCore.Qt.BackButton)
        self.assertEqual(viewer._index, 0)
        click(QtCore.Qt.BackButton)
        self.assertEqual(viewer._index, 2)
        viewer.pin_button.click()
        viewer.actual_size_button.click()
        click(QtCore.Qt.ForwardButton)
        self.assertEqual(viewer._index, 0)
        for _ in range(2):
            click(QtCore.Qt.BackButton)
            self.assertEqual(viewer._index, 2)
            click(QtCore.Qt.ForwardButton)
            self.assertEqual(viewer._index, 0)
        click(QtCore.Qt.ForwardButton)
        self.assertEqual(viewer._index, 1)

    def test_mouse_wheel_steps_forward_up_backward_down_and_accumulates_notches(self):
        viewer = self.open_viewer(1)
        self.scroll_viewer(viewer, 60)
        self.assertEqual(viewer._index, 1)
        self.scroll_viewer(viewer, 60)
        self.assertEqual(viewer._index, 2)
        self.scroll_viewer(viewer, 120)
        self.assertEqual(viewer._index, 0)
        self.scroll_viewer(viewer, -120)
        self.assertEqual(viewer._index, 2)
        self.scroll_viewer(viewer, 240)
        self.assertEqual(viewer._index, 1)
        viewer.set_collection(self.paths[:1])
        self.scroll_viewer(viewer, 120)
        self.assertEqual(viewer._index, 0)

    def test_mouse_wheel_respects_pinned_comparison_and_works_at_actual_size(self):
        viewer = self.open_viewer()
        viewer.pin_button.click()
        viewer.actual_size_button.click()
        self.scroll_viewer(viewer, 120)
        self.assertEqual(viewer._index, 1)
        for _ in range(2):
            self.scroll_viewer(viewer, -120)
            self.assertEqual(viewer._index, 0)
            self.scroll_viewer(viewer, 120)
            self.assertEqual(viewer._index, 1)
        self.scroll_viewer(viewer, 120)
        self.assertEqual(viewer._index, 2)
        self.scroll_viewer(viewer, -120)
        viewer.pin_button.click()
        self.scroll_viewer(viewer, -120)
        self.assertEqual(viewer._index, 2)

    def test_swiping_left_and_right_changes_one_image_and_wraps(self):
        viewer = self.open_viewer(1)
        self.swipe(viewer, QtCore.QPoint(-120, 5))
        self.assertEqual(viewer._index, 2)
        self.swipe(viewer, QtCore.QPoint(-120, 0))
        self.assertEqual(viewer._index, 0)
        self.swipe(viewer, QtCore.QPoint(120, 0))
        self.assertEqual(viewer._index, 2)
        self.assertIn("3 / 3", viewer.caption.text())

    def test_small_or_vertical_drags_do_not_navigate_and_single_image_is_safe(self):
        viewer = self.open_viewer(1)
        for delta in (QtCore.QPoint(20, 0), QtCore.QPoint(50, 150), QtCore.QPoint(0, -120)):
            self.swipe(viewer, delta)
            self.assertEqual(viewer._index, 1)
        viewer.set_collection(self.paths[:1])
        self.swipe(viewer, QtCore.QPoint(-120, 0))
        self.assertEqual(viewer._index, 0)

    def test_pin_toggles_reference_and_comparison_until_next_is_pressed_again(self):
        viewer = self.open_viewer()
        self.assertFalse(viewer.pin_button.isChecked())
        viewer.pin_button.click()
        self.assertTrue(viewer.pin_button.isChecked())
        viewer.next_button.click()
        self.assertEqual(viewer._index, 1)
        self.assertFalse(viewer.pin_button.isChecked())
        viewer.previous_button.click()
        self.assertEqual(viewer._index, 0)
        self.assertTrue(viewer.pin_button.isChecked())
        viewer.previous_button.click()
        viewer.next_button.click()
        self.assertEqual(viewer._index, 1)
        QtTest.QTest.keyClick(viewer.image_view, QtCore.Qt.Key_Left)
        self.assertEqual(viewer._index, 0)
        QtTest.QTest.keyClick(viewer.image_view, QtCore.Qt.Key_Right)
        self.assertEqual(viewer._index, 1)
        viewer.next_button.click()
        self.assertEqual(viewer._index, 2)
        self.swipe(viewer, QtCore.QPoint(120, 0))
        self.assertEqual(viewer._index, 0)
        self.swipe(viewer, QtCore.QPoint(-120, 0))
        self.assertEqual(viewer._index, 2)
        self.swipe(viewer, QtCore.QPoint(120, 0))
        self.assertEqual(viewer._index, 0)
        self.swipe(viewer, QtCore.QPoint(-120, 0))
        self.assertEqual(viewer._index, 2)
        viewer.next_button.click()
        self.assertEqual(viewer._index, 0)
        viewer.next_button.click()
        self.assertEqual(viewer._index, 1)

    def test_pin_can_be_replaced_and_unpinned_restoring_normal_navigation(self):
        viewer = self.open_viewer()
        viewer.pin_button.click()
        viewer.next_button.click()
        viewer.pin_button.click()
        self.assertEqual(viewer._pinned_path, self.paths[1])
        viewer.next_button.click()
        viewer.previous_button.click()
        self.assertEqual(viewer._index, 1)
        viewer.pin_button.click()
        self.assertIsNone(viewer._pinned_path)
        self.assertFalse(viewer.pin_button.isChecked())
        viewer.previous_button.click()
        self.assertEqual(viewer._index, 0)
        viewer.previous_button.click()
        self.assertEqual(viewer._index, 2)

    def test_pin_tracks_path_when_collection_reorders_and_clears_if_removed(self):
        viewer = self.open_viewer()
        viewer.pin_button.click()
        viewer.set_collection(list(reversed(self.paths)), 0)
        viewer.previous_button.click()
        self.assertEqual(viewer._index, 2)
        self.assertTrue(viewer.pin_button.isChecked())
        viewer.set_collection(self.paths[1:])
        self.assertIsNone(viewer._pinned_path)
        self.assertFalse(viewer.pin_button.isChecked())
        viewer.previous_button.click()
        self.assertEqual(viewer._index, 1)
        viewer.set_collection([])
        self.assertFalse(viewer.pin_button.isEnabled())

    def test_pin_icons_use_supplied_blue_artwork_and_gray_preserves_transparency(self):
        viewer = self.open_viewer()
        source = QtGui.QPixmap(str(Path(__file__).resolve().parents[2] / "icons" / "Pin_b_Icon.png"))
        self.assertFalse(source.isNull())
        blue = viewer._blue_pin_icon.pixmap(source.size()).toImage()
        gray = viewer._gray_pin_icon.pixmap(source.size()).toImage()
        self.assertEqual(blue.size(), gray.size())
        colored = False
        for y in range(blue.height()):
            for x in range(blue.width()):
                original, inactive = blue.pixelColor(x, y), gray.pixelColor(x, y)
                self.assertEqual(original.alpha(), inactive.alpha())
                self.assertEqual(inactive.red(), inactive.green())
                self.assertEqual(inactive.green(), inactive.blue())
                colored |= original.blue() > original.red()
        self.assertTrue(colored)
        self.assertEqual(viewer.pin_button.icon().cacheKey(), viewer._gray_pin_icon.cacheKey())
        viewer.pin_button.click()
        self.assertEqual(viewer.pin_button.icon().cacheKey(), viewer._blue_pin_icon.cacheKey())

    def test_click_advances_in_fit_mode_respects_pin_and_does_nothing_at_actual_size(self):
        viewer = self.open_viewer()
        viewport = viewer.image_view.viewport()
        def click():
            QtTest.QTest.mouseClick(viewport, QtCore.Qt.LeftButton, pos=viewport.rect().center())
        viewer.pin_button.click()
        click()
        self.assertEqual(viewer._index, 1)
        viewer.previous_button.click()
        click()
        self.assertEqual(viewer._index, 1)
        click()
        self.assertEqual(viewer._index, 2)
        viewer.actual_size_button.click()
        click()
        self.assertEqual(viewer._index, 2)
        viewer.actual_size_button.click()
        click()
        self.assertEqual(viewer._index, 0)

    def test_actual_size_drag_still_pans_without_switching_images(self):
        viewer = self.open_viewer()
        viewer.actual_size_button.click()
        scrollbar = viewer.image_view.horizontalScrollBar()
        before = scrollbar.value()
        self.swipe(viewer, QtCore.QPoint(-120, 0))
        self.assertGreater(scrollbar.value(), before)
        self.assertEqual(viewer._index, 0)
        viewer.actual_size_button.click()
        self.swipe(viewer, QtCore.QPoint(-120, 0))
        self.assertEqual(viewer._index, 1)

    def test_double_click_opens_clicked_original_and_supports_fit_and_actual_pixels(self):
        viewer = self.open_viewer()
        image_view = viewer.image_view
        self.assertEqual(image_view._image.pixmap().size(), QtCore.QSize(2048, 1536))
        self.assertLess(image_view.transform().m11(), 1.0)
        self.assertIn("2048 × 1536", viewer.caption.text())
        viewer.actual_size_button.click()
        self.assertEqual(image_view.transform().m11(), 1.0)
        self.assertGreater(image_view.horizontalScrollBar().maximum(), 0)
        viewer.actual_size_button.click()
        self.assertLess(image_view.transform().m11(), 1.0)
        self.assertEqual(self.item.model._image_collection_state["paths"], self.paths)

    def test_arrows_and_keyboard_navigate_collection_and_wrap_with_button_focus(self):
        viewer = self.open_viewer(1)
        self.assertEqual(viewer._index, 1)
        viewer.next_button.setFocus()
        QtTest.QTest.keyClick(viewer.next_button, QtCore.Qt.Key_Right)
        self.assertEqual(viewer._index, 2)
        viewer.next_button.click()
        self.assertEqual(viewer._index, 0)
        QtTest.QTest.keyClick(viewer.image_view, QtCore.Qt.Key_Left)
        self.assertEqual(viewer._index, 2)
        viewer.previous_button.click()
        self.assertEqual(viewer._index, 1)
        self.assertIn("2 / 3", viewer.caption.text())

    def test_edit_mode_selects_without_opening_viewer_and_border_contains_controls(self):
        edit = next(button for button in self.body.findChildren(QtWidgets.QPushButton)
                    if button.text() == "Edit")
        edit.click()
        APP.processEvents()
        self.double_click_image(1)
        self.assertIsNone(self.canvas._viewer)
        self.assertEqual(self.canvas.selected_path(), self.paths[1])
        for proxy in self.item._plugin_proxies:
            self.assertGreaterEqual(self.item.height,
                                    proxy.pos().y() + proxy.size().height() + self.item._PADDING)
        edit.click()
        APP.processEvents()
        self.assertEqual(self.canvas.selected_index, -1)
        self.open_viewer(1)

    def test_slideshow_advances_pauses_and_stops_on_close_then_can_reopen(self):
        viewer = self.open_viewer()
        viewer._timer.setInterval(30)
        ticks = QtTest.QSignalSpy(viewer._timer.timeout)
        viewer.slideshow_button.click()
        self.assertTrue(viewer._timer.isActive())
        self.assertTrue(ticks.wait(500))
        self.assertEqual(viewer._index, 1)
        QtTest.QTest.keyClick(viewer.image_view, QtCore.Qt.Key_Space)
        self.assertFalse(viewer._timer.isActive())
        viewer.slideshow_button.click()
        QtTest.QTest.keyClick(viewer.image_view, QtCore.Qt.Key_Escape)
        self.assertFalse(viewer._timer.isActive())
        self.assertIsNone(self.canvas._viewer)
        APP.processEvents()
        reopened = self.open_viewer(2)
        self.assertEqual(reopened._index, 2)

    def test_one_image_disables_navigation_and_slideshow(self):
        self.canvas.load_images(self.paths[:1], append=False)
        viewer = self.open_viewer()
        self.assertFalse(viewer.previous_button.isEnabled())
        self.assertFalse(viewer.next_button.isEnabled())
        self.assertFalse(viewer.slideshow_button.isEnabled())
        QtTest.QTest.keyClick(viewer.image_view, QtCore.Qt.Key_Space)
        QtTest.QTest.keyClick(viewer.image_view, QtCore.Qt.Key_Right)
        self.assertFalse(viewer._timer.isActive())
        self.assertFalse(viewer.slideshow_button.isChecked())
        self.assertEqual(viewer._index, 0)

    def test_deleted_image_shows_error_and_allows_navigation_to_continue(self):
        viewer = self.open_viewer()
        Path(self.paths[1]).unlink()
        viewer.next_button.click()
        self.assertEqual(viewer.pages.currentWidget(), viewer.error_label)
        self.assertIn(self.paths[1], viewer.error_label.text())
        self.assertFalse(viewer.actual_size_button.isEnabled())
        viewer.next_button.click()
        self.assertEqual(viewer.pages.currentWidget(), viewer.image_view)
        self.assertEqual(viewer.image_view._image.pixmap().size(), QtCore.QSize(800, 600))

    def test_hover_paints_thin_gray_border_and_clears_on_gaps_and_leave(self):
        QtTest.QTest.mouseMove(self.view.viewport(), self.image_position(1))
        APP.processEvents()
        self.assertEqual(self.canvas.hovered_index, 1)
        rect = self.canvas.rects[1]
        snapshot = self.canvas.grab().toImage()
        self.assertEqual(snapshot.pixelColor(rect.center().x(), rect.top()), QtGui.QColor("#9ca3af"))
        self.assertEqual(self.canvas.selected_index, -1)
        point = self.canvas.mapTo(self.body, self.canvas.rect().bottomRight())
        proxy = self.body.graphicsProxyWidget()
        blank = self.view.mapFromScene(proxy.mapToScene(QtCore.QPointF(point)))
        QtTest.QTest.mouseMove(self.view.viewport(), blank)
        self.assertEqual(self.canvas.hovered_index, -1)
        QtTest.QTest.mouseMove(self.view.viewport(), self.image_position(0))
        self.assertEqual(self.canvas.hovered_index, 0)
        QtTest.QTest.mouseMove(self.view.viewport(), QtCore.QPoint(2, 2))
        self.assertEqual(self.canvas.hovered_index, -1)

    def test_border_drag_reflows_images_and_loads_sharper_previews_when_growing(self):
        self.view.scale(0.5, 0.5)
        self.view.centerOn(self.item)
        original_width, original_height = self.item.width, self.item.height
        original_area = sum(pm.width() * pm.height() for pm in self.canvas.pixmaps)
        cached_size = self.canvas._thumbnail_cache[self.paths[0]].size()
        self.drag_border(QtCore.QPointF(original_width - 1, original_height - 1),
                         QtCore.QPointF(360, 300), "bottom-right")
        self.assertAlmostEqual(self.item.width, original_width + 360, delta=2)
        self.assertAlmostEqual(self.item.height, original_height + 300, delta=2)
        self.assertGreater(sum(pm.width() * pm.height() for pm in self.canvas.pixmaps), original_area)
        self.assertGreater(self.canvas._thumbnail_cache[self.paths[0]].width(), cached_size.width())
        self.assert_images_fit()
        wide_positions = list(self.canvas.offsets)
        self.drag_border(QtCore.QPointF(self.item.width - 1, self.item.height - 1),
                         QtCore.QPointF(-700, 100), "bottom-right")
        self.assertNotEqual(self.canvas.offsets, wide_positions)
        self.assertGreater(len({rect.top() for rect in self.canvas.rects}), 1)
        self.assert_images_fit()
        self.open_viewer(2)

    def test_left_and_top_border_drags_keep_opposite_edges_anchored_and_clamp_size(self):
        self.view.scale(0.5, 0.5)
        self.view.centerOn(self.item)
        right = self.item.pos().x() + self.item.width
        bottom = self.item.pos().y() + self.item.height
        self.drag_border(QtCore.QPointF(1, self.item.height / 2), QtCore.QPointF(-100, 0), "left")
        self.assertAlmostEqual(self.item.pos().x() + self.item.width, right, delta=2)
        self.drag_border(QtCore.QPointF(self.item.width / 2, 1), QtCore.QPointF(0, -100), "top")
        self.assertAlmostEqual(self.item.pos().y() + self.item.height, bottom, delta=2)
        self.drag_border(QtCore.QPointF(self.item.width - 1, self.item.height - 1),
                         QtCore.QPointF(-2000, -2000), "bottom-right")
        self.assertGreaterEqual(self.item.width, self.item._image_collection_min_w)
        self.assertGreaterEqual(self.item.height, self.item._image_collection_min_h)
        self.assert_images_fit()
        edit = next(button for button in self.body.findChildren(QtWidgets.QPushButton)
                    if button.text() == "Edit")
        edit.click()
        APP.processEvents()
        self.assert_images_fit()

    def test_resized_size_and_image_paths_round_trip_through_graph_persistence(self):
        self.view.scale(0.5, 0.5)
        self.drag_border(QtCore.QPointF(self.item.width - 1, self.item.height - 1),
                         QtCore.QPointF(180, 200), "bottom-right")
        size = self.item.model._image_collection_size
        saved = _node_to_dict(self.item.model)
        self.assertEqual(saved["image_collection_size"], list(size))
        scene = MagicMock()
        scene._node_items = {}
        deserialize_scene(scene, {"nodes": [saved]}, GraphNode_ctor=GraphNode, set_scale_cb=lambda _scale: None)
        restored_model = scene.add_node.call_args.args[0]
        self.assertEqual(restored_model._image_collection_state["paths"], self.paths)
        restored_item = NodeItem(restored_model)
        self.scene.addItem(restored_item)
        self.assertAlmostEqual(restored_item.width, size[0], delta=1)
        self.assertAlmostEqual(restored_item.height, size[1], delta=1)
        restored_item._recompute_height()
        restored_item._build_widgets()
        self.assertAlmostEqual(restored_item.width, size[0], delta=1)
        self.assertAlmostEqual(restored_item.height, size[1], delta=1)

    def test_many_mixed_aspect_images_remain_inside_wide_tall_and_compact_nodes(self):
        for index, (width, height) in enumerate(((200, 800), (800, 200), (400, 400)) * 3):
            path = Path(self.paths[0]).parent / f"mixed {index}.png"
            image = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
            image.fill(QtCore.Qt.gray)
            self.assertTrue(image.save(str(path)))
            self.paths.append(str(path))
        self.canvas.load_images(self.paths, append=False)
        self.view.scale(0.5, 0.5)
        self.view.centerOn(self.item)
        for width, height in ((1200, 450), (500, 900), (360, 260)):
            with self.subTest(size=(width, height)):
                self.drag_border(QtCore.QPointF(self.item.width - 1, self.item.height - 1),
                                 QtCore.QPointF(width - self.item.width, height - self.item.height), "bottom-right")
                self.assert_images_fit()

    def test_resizing_keeps_open_viewer_and_deleting_node_closes_it(self):
        viewer = self.open_viewer(1)
        self.view.activateWindow()
        self.view.scale(0.5, 0.5)
        self.view.centerOn(self.item)
        self.drag_border(QtCore.QPointF(self.item.width - 1, self.item.height - 1),
                         QtCore.QPointF(100, 100), "bottom-right")
        self.assertIs(self.canvas._viewer, viewer)
        self.assertEqual(viewer._index, 1)
        viewer.slideshow_button.click()
        finished = QtTest.QSignalSpy(viewer.finished)
        self.scene.clear()
        self.assertEqual(finished.count(), 1)
        self.assertFalse(viewer._timer.isActive())

    def test_dragging_an_image_moves_collection_without_opening_viewer(self):
        initial_pos = QtCore.QPointF(self.item.pos())
        start = self.image_position(0)
        end = start + QtCore.QPoint(70, 45)
        QtTest.QTest.mousePress(self.view.viewport(), QtCore.Qt.LeftButton, pos=start)
        event = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(end),
                                 QtCore.QPointF(self.view.viewport().mapToGlobal(end)),
                                 QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(self.view.viewport(), event)
        QtTest.QTest.mouseRelease(self.view.viewport(), QtCore.Qt.LeftButton, pos=end)
        APP.processEvents()
        self.assertEqual(self.item.pos(), initial_pos + QtCore.QPointF(70, 45))
        self.assertIsNone(self.canvas._viewer)
        self.assertIsNone(getattr(self.item.model, "_image_collection_size", None))
        self.open_viewer(0)

    def test_single_image_default_height_follows_original_image_aspect(self):
        heights = []
        for width, height in ((800, 200), (400, 400), (200, 800)):
            path = Path(self.paths[0]).parent / f"aspect {width} {height}.png"
            image = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
            image.fill(QtCore.Qt.red)
            self.assertTrue(image.save(str(path)))
            self.canvas.load_images([str(path)], append=False)
            rect, = self.canvas.rects
            self.assertEqual(rect.topLeft(), QtCore.QPoint(4, 4))
            self.assertAlmostEqual(rect.width() / rect.height(), width / height, delta=0.03)
            self.assertAlmostEqual(rect.width(), self.canvas.width() - 8, delta=2)
            self.assertGreaterEqual(self.canvas.height(), rect.bottom() + 4)
            heights.append(self.item.height)
        self.assertLess(heights[0], heights[1])
        self.assertLess(heights[1], heights[2])
        self.assertIsNone(getattr(self.item.model, "_image_collection_size", None))

    def test_default_collection_preserves_packed_rows_without_cell_gaps(self):
        rows = {}
        for rect in self.canvas.rects:
            rows.setdefault(rect.top(), []).append(rect)
        for rects in rows.values():
            self.assertEqual(rects[0].left(), 4)
            for previous, current in zip(rects, rects[1:]):
                self.assertLessEqual(current.left() - previous.right() - 1, 6)
                self.assertLessEqual(abs(previous.height() - current.height()), 2)
            self.assertLessEqual(self.canvas.width() - 4 - rects[-1].right() - 1, 6)


if __name__ == "__main__":
    unittest.main()
