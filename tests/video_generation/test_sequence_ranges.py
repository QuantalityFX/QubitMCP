import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.persistence import _node_to_dict
from echograph.ui.node_item import NodeItem
from nodes.sequence_to_mp4 import spec as export
from nodes.video_player import spec as video


APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Graph(QtWidgets.QGraphicsScene):
    linksChanged = QtCore.Signal()
    paramChanged = QtCore.Signal(str, list)

    def __init__(self):
        super().__init__()
        self.edges = []

    def _ordered_in_edges(self, item):
        return [edge for edge in self.edges if edge.dst is item]

    def resolve_text_value(self, item):
        return video._param_value(item.model, "output") or video._param_value(item.model, "path")


class SequenceRangeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = []
        for number in range(1001, 1007):
            path = self.root / f"shot_{number:04d}.png"
            Image.new("RGB", (320, 180), (number - 1000, 50, 100)).save(path)
            self.paths.append(path)
        self.scene = Graph()
        self.addCleanup(self.scene.clear)

    def make_node(self, kind, **params):
        model = GraphNode(kind, kind=kind, params=[{"name": name, "value": str(value)} for name, value in params.items()])
        item = NodeItem(model)
        self.scene.addItem(item)
        APP.processEvents()
        return item, item._plugin_proxies[0].widget()

    def connect(self, source, destination, port="source"):
        self.scene.edges.append(SimpleNamespace(src=source, dst=destination, dst_port_name=port))
        self.scene.linksChanged.emit()
        APP.processEvents()

    def test_external_open_available_for_mp4_without_preview_and_resets_for_sequences(self):
        path = self.root / "clip.MP4"
        path.write_bytes(b"test")
        with patch.object(video.VideoPlayerWidget, "_load_video_source", return_value=False):
            _, controls = self.make_node("video_player", path=path)
        self.assertTrue(controls._open_btn.isEnabled())
        with patch.object(QtGui.QDesktopServices, "openUrl", return_value=True) as launch:
            controls._open_btn.click()
            launch.assert_called_once_with(QtCore.QUrl.fromLocalFile(str(path.resolve())))
            path.unlink()
            controls._open_external_video()
            self.assertEqual(launch.call_count, 1)
        controls._load_media(str(self.paths[0]))
        self.assertFalse(controls._open_btn.isEnabled())
        controls._load_media(str(path))
        self.assertFalse(controls._open_btn.isEnabled())

    def test_external_open_uses_connected_mp4(self):
        path = self.root / "connected.mp4"
        path.write_bytes(b"test")
        source = QtWidgets.QGraphicsRectItem()
        source.model = GraphNode("source", params=[{"name": "mp4_path", "value": str(path)}])
        self.scene.addItem(source)
        item, controls = self.make_node("video_player", path=self.paths[0])
        with patch.object(video.VideoPlayerWidget, "_load_video_source", return_value=False):
            self.connect(source, item)
        self.assertTrue(controls._open_btn.isEnabled())
        with patch.object(QtGui.QDesktopServices, "openUrl", return_value=True) as launch:
            controls._open_btn.click()
            launch.assert_called_once_with(QtCore.QUrl.fromLocalFile(str(path.resolve())))

    def test_large_player_returns_above_reactivated_owner_only_while_open(self):
        item, _ = self.make_node("video_player", path=self.paths[0])
        owner = QtWidgets.QWidget()
        self.addCleanup(owner.deleteLater)
        dialog = video.FullscreenPlayerDialog(item, owner)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        with patch.object(owner, "isActiveWindow", return_value=True), \
                patch.object(dialog, "raise_") as raise_player, \
                patch.object(dialog, "activateWindow") as activate:
            QtWidgets.QApplication.sendEvent(owner, QtCore.QEvent(QtCore.QEvent.WindowActivate))
            APP.processEvents()
            raise_player.assert_called_once()
            activate.assert_called_once()
            dialog.hide()
            QtWidgets.QApplication.sendEvent(owner, QtCore.QEvent(QtCore.QEvent.WindowActivate))
            APP.processEvents()
            self.assertEqual(raise_player.call_count, 1)
            dialog.show()
            with patch.object(owner, "isActiveWindow", return_value=False):
                dialog._raise_above_owner()
            self.assertEqual(raise_player.call_count, 1)

    def test_auto_full_range_and_inclusive_custom_boundaries(self):
        item, controls = self.make_node("sequence_to_mp4", source=self.paths[0])
        info, _, _ = export._effective_sequence_info(item, str(self.paths[0]))
        self.assertEqual((info.start_number, info.frame_count), (1001, 6))
        self.assertEqual(controls._start_spin.text(), "Auto")
        self.assertEqual(controls._end_spin.text(), "Auto")
        controls._start_spin.setValue(1002)
        controls._end_spin.setValue(1004)
        info, _, _ = export._effective_sequence_info(item, str(self.paths[0]))
        self.assertEqual((info.start_number, info.frame_count), (1002, 3))
        self.assertEqual(info.example_path, self.paths[1])
        command = export._build_ffmpeg_command(info, self.root / "selected.mp4", 24, "h264", "12M")
        self.assertEqual(command[command.index("-start_number") + 1], "1002")
        self.assertEqual(command[command.index("-frames:v") + 1], "3")
        controls._refresh_status()
        self.assertIn("1002-1004", controls._status.text())

    def test_invalid_inverted_outside_and_missing_frame_ranges_rejected(self):
        item, controls = self.make_node("sequence_to_mp4", source=self.paths[0])
        for start, end in ((1004, 1002), (1000, 1003), (1002, 1007)):
            controls._start_spin.setValue(start)
            controls._end_spin.setValue(end)
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                export._effective_sequence_info(item, str(self.paths[0]))
        controls._start_spin.setValue(1002)
        controls._end_spin.setValue(1004)
        self.paths[2].unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            export._effective_sequence_info(item, str(self.paths[0]))

    def test_markers_inherit_live_and_export_boundary_override_wins(self):
        player, playback = self.make_node("video_player", path=self.paths[0])
        item, controls = self.make_node("sequence_to_mp4")
        self.connect(player, item)
        playback._seek_to_scrub(1)
        playback._start_btn.click()
        playback._seek_to_scrub(3)
        playback._end_btn.click()
        self.assertEqual(video._param_value(player.model, "start_frame"), "1002")
        self.assertEqual(video._param_value(player.model, "end_frame"), "1004")
        info, label, connected = export._effective_sequence_info(item, "")
        self.assertTrue(connected)
        self.assertEqual(label, player.model.name)
        self.assertEqual((info.start_number, info.frame_count), (1002, 3))
        controls._start_spin.setValue(1003)
        info, _, _ = export._effective_sequence_info(item, "")
        self.assertEqual((info.start_number, info.frame_count), (1003, 2))
        controls._start_spin.setValue(-1)
        playback._range_reset.click()
        info, _, _ = export._effective_sequence_info(item, "")
        self.assertEqual((info.start_number, info.frame_count), (1001, 6))

    def test_player_connected_to_source_uses_driven_path_and_its_own_markers(self):
        driver, _ = self.make_node("sequence_to_mp4", source=self.paths[0])
        driver.model.params.append({"name": "output_pattern", "value": str(self.root / "shot_%04d.png")})
        source = QtWidgets.QGraphicsRectItem()
        source.model = GraphNode("Render source", kind="render", params=[{"name": "output", "value": str(self.paths[0])}])
        self.scene.addItem(source)
        player, playback = self.make_node("video_player", path="obsolete.png")
        self.connect(source, player, "path")
        playback._seek_to_scrub(2)
        playback._mark_start()
        playback._seek_to_scrub(4)
        playback._mark_end()
        self.connect(player, driver)
        info, _, _ = export._effective_sequence_info(driver, "")
        self.assertEqual((info.start_number, info.frame_count), (1003, 3))

    def test_post_process_keeps_video_markers_with_cached_and_generated_sources(self):
        player, playback = self.make_node("video_player", path=self.paths[0])
        playback._seek_to_scrub(1)
        playback._mark_start()
        playback._seek_to_scrub(2)
        playback._mark_end()
        post, _ = self.make_node("post_process")
        item, _ = self.make_node("sequence_to_mp4")
        self.connect(player, post)
        self.connect(post, item)
        for apply in (False, True):
            with self.subTest(apply=apply):
                if apply:
                    from nodes.post_process.spec import PostProcessResult
                    result = PostProcessResult(self.root / "shot_%04d.png", self.paths[0], self.root, 6, "test")
                    with patch("nodes.post_process.spec.resolve_post_process_sequence", return_value=result):
                        info, _, _ = export._effective_sequence_info(item, "", apply_postprocess=True)
                else:
                    info, _, _ = export._effective_sequence_info(item, "")
                self.assertEqual((info.start_number, info.frame_count), (1002, 2))

    def test_marked_playback_loops_and_controls_are_left_except_audio(self):
        _, controls = self.make_node("video_player", path=self.paths[0])
        controls._seek_to_scrub(1)
        controls._mark_start()
        controls._seek_to_scrub(3)
        controls._mark_end()
        self.assertEqual((controls._scrub_slider._marked_start, controls._scrub_slider._marked_end), (1, 3))
        controls._on_sequence_tick()
        self.assertEqual(controls._seq_index, 1)
        controls._on_sequence_tick()
        self.assertEqual(controls._seq_index, 2)
        APP.processEvents()
        slider_y = controls._scrub_slider.mapTo(controls, QtCore.QPoint()).y()
        transport_y = controls._play_btn.mapTo(controls, QtCore.QPoint()).y()
        self.assertGreater(transport_y, slider_y)
        buttons = (controls._play_btn, controls._start_btn, controls._end_btn,
                   controls._range_reset, controls._fullscreen_btn, controls._open_btn, controls._sound_btn)
        self.assertEqual([button.x() for button in buttons], sorted(button.x() for button in buttons))
        self.assertEqual(controls._range_reset.text(), "[X]")
        self.assertFalse(any(label.text() == "Auto" for label in controls.findChildren(QtWidgets.QLabel)))
        self.assertGreater(controls._sound_btn.x() - controls._fullscreen_btn.geometry().right(), 20)
        controls._range_reset.click()
        self.assertEqual((controls._marker("start_frame"), controls._marker("end_frame")), (-1, -1))

    def test_scrubber_darkens_only_regions_outside_markers(self):
        _, controls = self.make_node("video_player", path=self.paths[0])
        controls._seek_to_scrub(1)
        controls._mark_start()
        controls._seek_to_scrub(4)
        controls._mark_end()
        controls._seek_to_scrub(2)
        slider = controls._scrub_slider
        image = slider.grab().toImage()
        option = QtWidgets.QStyleOptionSlider()
        slider.initStyleOption(option)
        groove = slider.style().subControlRect(QtWidgets.QStyle.CC_Slider, option, QtWidgets.QStyle.SC_SliderGroove, slider)
        for fraction in (0.04, 0.96):
            self.assertEqual(image.pixelColor(round(image.width() * fraction), groove.center().y()).name(), "#171e29")
        self.assertNotEqual(image.pixelColor(image.width() // 2, groove.center().y()).name(), "#171e29")

    def test_video_flags_use_frame_numbers_and_do_not_mark_past_final_frame(self):
        _, controls = self.make_node("video_player")
        controls._mode = "video"
        controls._set_scrub_range(0, 1000)
        with patch.object(controls, "_video_fps", return_value=24):
            controls._set_scrub_value(1000)
            controls._mark_end()
            self.assertEqual(controls._marker("end_frame"), 23)
            controls._set_scrub_value(210)
            controls._mark_start()
            self.assertEqual(controls._marker("start_frame"), 5)

    def test_large_player_outside_track_stays_visible_in_maximized_fullscreen_and_resized_views(self):
        _, controls = self.make_node("video_player", path=self.paths[0])
        controls._seek_to_scrub(1)
        controls._mark_start()
        controls._seek_to_scrub(4)
        controls._mark_end()
        controls._open_fullscreen()
        dialog = controls._fullscreen_dialog
        self.addCleanup(dialog.close)
        dialog.player.setStyleSheet("QWidget { background: #171e29; }")
        for mode in ("maximized", "fullscreen", "resized"):
            if mode == "fullscreen":
                dialog.showFullScreen()
            elif mode == "resized":
                dialog.showNormal()
                dialog.resize(900, 600)
            dialog.player._seek_to_scrub(2)
            APP.processEvents()
            slider = dialog.player._scrub_slider
            image = slider.grab().toImage()
            option = QtWidgets.QStyleOptionSlider()
            slider.initStyleOption(option)
            groove = slider.style().subControlRect(QtWidgets.QStyle.CC_Slider, option, QtWidgets.QStyle.SC_SliderGroove, slider)
            for fraction in (0.04, 0.96):
                with self.subTest(mode=mode, side=fraction):
                    self.assertEqual(image.pixelColor(round(image.width() * fraction), groove.center().y()).name(), "#334155")
            self.assertNotEqual(image.pixelColor(image.width() // 2, groove.center().y()).name(), "#334155")

    def test_fullscreen_reads_original_images_preserves_position_and_shared_markers(self):
        item, controls = self.make_node("video_player", path=self.paths[0])
        controls._seek_to_scrub(2)
        controls._open_fullscreen()
        dialog = controls._fullscreen_dialog
        self.addCleanup(dialog.close)
        APP.processEvents()
        self.assertTrue(dialog.isMaximized())
        self.assertFalse(dialog.isFullScreen())
        for flag in (QtCore.Qt.WindowMinimizeButtonHint, QtCore.Qt.WindowMaximizeButtonHint, QtCore.Qt.WindowCloseButtonHint):
            self.assertTrue(dialog.windowFlags() & flag)
        for button in (dialog.player._play_btn, dialog.player._start_btn, dialog.player._end_btn,
                       dialog.player._range_reset, dialog.player._fullscreen_btn, dialog.player._sound_btn):
            self.assertTrue(button.isVisible())
            self.assertTrue(dialog.player.rect().contains(button.mapTo(dialog.player, button.rect().bottomRight())))
        self.assertIsNone(dialog.graphicsProxyWidget())
        self.assertEqual(dialog.player._preview_image.size(), QtCore.QSize(320, 180))
        self.assertEqual(dialog.player._seq_index, 2)
        dialog.player._mark_start()
        dialog.player._seek_to_scrub(4)
        dialog.player._mark_end()
        self.assertEqual(video._param_value(item.model, "end_frame"), "1005")
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
        APP.processEvents()
        self.assertIsNone(controls._fullscreen_dialog)
        self.assertEqual(controls._seq_index, 4)
        self.assertFalse(dialog.player._seq_timer.isActive())

    def test_selected_node_space_toggles_without_repeat_and_leaves_path_typing_alone(self):
        from echograph.ui.view import GraphView
        item, controls = self.make_node("video_player", path=self.paths[0])
        view = GraphView(self.scene)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.resize(800, 600)
        view.show()
        view.activateWindow()
        self.scene.setFocusItem(None)
        item.setSelected(True)
        view.setFocus()
        APP.processEvents()
        QtTest.QTest.keyClick(view.viewport(), QtCore.Qt.Key_Space)
        self.assertTrue(controls._seq_timer.isActive())
        repeat = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Space, QtCore.Qt.NoModifier, " ", True)
        view.keyPressEvent(repeat)
        self.assertTrue(controls._seq_timer.isActive())
        QtTest.QTest.keyClick(view.viewport(), QtCore.Qt.Key_Space)
        self.assertFalse(controls._seq_timer.isActive())
        item.setSelected(False)
        QtTest.QTest.keyClick(view.viewport(), QtCore.Qt.Key_Space)
        self.assertFalse(controls._seq_timer.isActive())
        item.setSelected(True)
        controls._path_edit.setFocus()
        controls._path_edit.setCursorPosition(len(controls._path_edit.text()))
        before = controls._path_edit.text()
        APP.processEvents()
        QtTest.QTest.keyClick(view.viewport(), QtCore.Qt.Key_Space)
        self.assertEqual(controls._path_edit.text(), before + " ")
        self.assertFalse(controls._seq_timer.isActive())

    def test_large_window_space_works_with_focused_marker_controls_and_f11_restores_titlebar(self):
        _, controls = self.make_node("video_player", path=self.paths[0])
        controls._open_fullscreen()
        dialog = controls._fullscreen_dialog
        self.addCleanup(dialog.close)
        dialog.activateWindow()
        dialog.player._end_btn.setFocus()
        APP.processEvents()
        QtTest.QTest.keyClick(dialog.player._end_btn, QtCore.Qt.Key_Space)
        self.assertTrue(dialog.player._seq_timer.isActive())
        self.assertEqual(dialog.player._marker("end_frame"), -1)
        QtTest.QTest.keyClick(dialog.player._end_btn, QtCore.Qt.Key_Space)
        self.assertFalse(dialog.player._seq_timer.isActive())
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_F11)
        self.assertTrue(dialog.isFullScreen())
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
        self.assertTrue(dialog.isMaximized())
        self.assertTrue(dialog.isVisible())

    def test_frame_loop_uses_cached_bounds_and_preserves_original_playback_rate(self):
        _, controls = self.make_node("video_player", path=self.paths[0])
        self.assertEqual(controls._frame_interval_ms(), round(1000 / 24))
        self.assertEqual(controls._seq_timer.timerType(), QtCore.Qt.PreciseTimer)
        self.assertEqual(controls._status.minimumHeight(), controls._status.maximumHeight())
        with patch.object(controls, "_marker", side_effect=AssertionError("per-frame parameter lookup")), \
             patch.object(controls, "_range_indices", side_effect=AssertionError("per-frame bounds lookup")):
            controls._on_sequence_tick()
            self.assertEqual(controls._seq_index, 1)
        with patch.object(controls._preview, "setPixmap", wraps=controls._preview.setPixmap) as draw:
            controls._refresh_preview_pixmap()
            controls._refresh_preview_pixmap()
            draw.assert_not_called()

    def test_markers_persist_reload_reset_for_new_media_and_cannot_invert(self):
        item, controls = self.make_node("video_player", path=self.paths[0])
        controls._seek_to_scrub(3)
        controls._mark_end()
        controls._seek_to_scrub(5)
        controls._mark_start()
        self.assertEqual((controls._marker("start_frame"), controls._marker("end_frame")), (1006, 1006))
        saved = _node_to_dict(item.model)
        loaded = GraphNode("Loaded player", kind="video_player", params=saved["params"])
        copy = NodeItem(loaded)
        self.scene.addItem(copy)
        APP.processEvents()
        copy_widget = copy._plugin_proxies[0].widget()
        self.assertEqual(copy_widget._marker("start_frame"), 1006)
        different = self.root / "other_0001.png"
        Image.new("RGB", (320, 180)).save(different)
        controls._path_edit.setText(str(different))
        controls._on_path_committed()
        self.assertEqual((controls._marker("start_frame"), controls._marker("end_frame")), (-1, -1))

    def test_controls_fit_borders_after_rebuild_and_connection_recompute(self):
        for kind in ("video_player", "sequence_to_mp4"):
            item, controls = self.make_node(kind)
            for _ in range(2):
                item._recompute_height()
                for proxy in item._plugin_proxies:
                    self.assertLessEqual(proxy.pos().y() + proxy.size().height() + 9.9, item.height)
                    self.assertLessEqual(proxy.size().width(), item.width)
                    self.assertGreaterEqual(proxy.size().height(), proxy.widget().minimumSizeHint().height())
                item._build_widgets()
                APP.processEvents()

    def test_real_ffmpeg_export_contains_exact_selected_frames(self):
        item, controls = self.make_node("sequence_to_mp4", source=self.paths[0], start_frame=1002, end_frame=1004)
        info, _, _ = export._effective_sequence_info(item, str(self.paths[0]))
        output = self.root / "selected.mp4"
        result = export._run_sequence_to_mp4(info, output, 24, "h264", "")
        self.assertEqual(result.returncode, 0, result.stderr)
        import imageio_ffmpeg
        frames = imageio_ffmpeg.read_frames(str(output), pix_fmt="rgb24")
        next(frames)
        self.assertEqual(sum(1 for _ in frames), 3)


if __name__ == "__main__":
    unittest.main()
