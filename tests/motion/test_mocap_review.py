import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import time
import sys
import unittest
from unittest.mock import Mock, patch

from PySide6 import QtCore, QtGui, QtWidgets, QtTest

from echograph.model import GraphNode
from echograph.persistence import _node_to_dict
from nodes.mocap_collection import spec, review_data as data, review
from test_mocap_collection import APP, Scene, BVH


class ReviewTests(unittest.TestCase):
    def test_archive_moves_run_and_render_folders_and_appends(self):
        from nodes.mocap_collection.archive import archive_rows
        self.assign()
        self.scene._filename = str(self.root / "workflow.json")
        rows = spec.entries(self.model)
        rows[0]["approved"] = True
        rows[0]["checked"] = False
        spec.save_state(self.model, rows, 2, self.scene)
        parent = self.root / "archives"
        count, destination = archive_rows(self.model, self.scene, [0], parent, "Accepted")
        self.assertEqual(count, 1)
        self.assertFalse(self.paths[0].parent.exists())
        self.assertFalse(self.takes[0].exists())
        self.assertTrue(self.paths[1].is_file())
        self.assertEqual(len(spec.entries(self.model)), 1)
        archived = json.loads((destination / "archive.json").read_text())["entries"][0]
        self.assertTrue(Path(archived["path"]).is_file())
        self.assertTrue(archived["approved"])
        self.assertFalse(archived["checked"])
        take = Path(archived["render"]["folder"])
        self.assertTrue((take / "frame_0001.png").is_file())
        self.assertEqual(json.loads((take / "take.json").read_text())["source_path"], archived["path"])
        self.assertEqual(json.loads(self.manifest.read_text())["takes"][0]["source_path"], archived["path"])
        archive_rows(self.model, self.scene, [0], parent, "Accepted")
        self.assertEqual(len(json.loads((destination / "archive.json").read_text())["entries"]), 2)
        self.assertEqual(spec.entries(self.model), [])

    def test_archive_rolls_back_when_second_folder_move_fails(self):
        from nodes.mocap_collection import archive
        self.assign()
        before = spec.entries(self.model)
        original = archive.shutil.move
        calls = []
        def move(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError("test disk failure")
            return original(source, target)
        with patch.object(archive.shutil, "move", side_effect=move):
            with self.assertRaisesRegex(OSError, "test disk failure"):
                archive.archive_rows(self.model, self.scene, [0], self.root / "archives", "Test")
        self.assertTrue(self.paths[0].is_file())
        self.assertTrue(self.takes[0].is_dir())
        self.assertEqual(spec.entries(self.model), before)

    def test_archive_removes_shared_run_from_all_collections(self):
        from nodes.mocap_collection.archive import archive_rows
        sibling = self.paths[0].with_name("second.bvh")
        sibling.write_text(BVH.replace("{x}", "1"))
        spec.add_paths(self.model, [sibling], self.scene)
        other = GraphNode("Other", kind="mocap_collection")
        self.scene.add_node(other, QtCore.QPointF(700, 0))
        spec.add_paths(other, [self.paths[0]], self.scene)
        count, destination = archive_rows(self.model, self.scene, [0], self.root / "archives", "Test")
        self.assertFalse(self.paths[0].exists())
        remaining = spec.entries(self.model)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(Path(remaining[0]["path"]), self.paths[1])
        self.assertEqual(spec.entries(other), [])
        self.assertEqual(spec.mocap._param_value(other, "path"), "")
        self.assertEqual(count, 2)
        archived = json.loads((destination / "archive.json").read_text())["entries"]
        self.assertEqual({Path(row["path"]).name for row in archived}, {"animation.bvh", "second.bvh"})
        self.assertTrue(all(Path(row["path"]).is_file() for row in archived))

    def test_archive_metadata_excludes_scans_and_cleans_previously_saved_rows(self):
        from nodes.mocap_collection.archive import archive_rows
        _, destination = archive_rows(self.model, self.scene, [0], self.root / "archives", "Extras")
        archived = json.loads((destination / "archive.json").read_text())["entries"][0]
        path = Path(archived["path"])
        self.assertNotIn(path, spec.bvh_paths_in_folder(self.root))
        self.assertEqual(spec.bvh_paths_in_folder(path.parent), [])
        with self.assertRaisesRegex(ValueError, "archived"):
            spec.add_paths(self.model, [path], self.scene)
        # Simulate a graph saved by the old version with archived paths still listed.
        spec.mocap._set_param_value(self.model, "files", json.dumps([archived, {
            "path": str(self.paths[1]), "checked": True}]))
        spec.mocap._set_param_value(self.model, "output", "1")
        self.widget.refresh()
        self.assertEqual([row["path"] for row in spec.entries(self.model)], [str(self.paths[1])])
        self.assertEqual(self.widget.table.rowCount(), 1)
        self.assertEqual(spec.mocap._param_value(self.model, "path"), str(self.paths[1]))
        self.assertTrue(path.is_file())
        self.assertTrue((destination / "archive.json").is_file())

    def test_folder_scan_uses_archive_marker_not_folder_name(self):
        unmarked = self.root / "archives" / "ordinary.bvh"
        unmarked.parent.mkdir()
        unmarked.write_text(BVH.replace("{x}", "1"))
        self.assertIn(unmarked, spec.bvh_paths_in_folder(self.root))
        (unmarked.parent / "archive.json").write_text(json.dumps({"schema_version": 1, "entries": []}))
        self.assertNotIn(unmarked, spec.bvh_paths_in_folder(self.root))
        self.assertEqual(spec.bvh_paths_in_folder(unmarked.parent), [])

    def test_archive_restores_files_and_model_when_collection_update_fails(self):
        from nodes.mocap_collection.archive import archive_rows
        self.assign()
        before = spec.entries(self.model)
        old_manifest = self.manifest.read_bytes()
        def fail_save(model, rows, number, scene):
            spec.mocap._set_param_value(model, "files", "[]")
            raise OSError("test save failure")
        with patch.object(spec, "save_state", side_effect=fail_save):
            with self.assertRaisesRegex(OSError, "test save failure"):
                archive_rows(self.model, self.scene, [0], self.root / "archives", "Test")
        self.assertEqual(spec.entries(self.model), before)
        self.assertTrue(self.paths[0].is_file())
        self.assertTrue(self.takes[0].is_dir())
        self.assertEqual(self.manifest.read_bytes(), old_manifest)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.scene = Scene()
        self.model = GraphNode("Collection", kind="mocap_collection")
        self.item = self.scene.add_node(self.model, QtCore.QPointF())
        self.widget = self.item._plugin_proxies[0].widget()
        self.paths, self.takes = [], []
        for number, name in enumerate(("walk", "run"), 1):
            folder = self.root / "bvh" / name
            folder.mkdir(parents=True)
            path = folder / "animation.bvh"
            path.write_text(BVH.replace("{x}", str(number)))
            self.paths.append(path)
            take = self.root / "renders" / f"Take_{number + 3:03d}_{name}"
            self.make_take(take, path)
            self.takes.append(take)
        self.manifest = self.root / "renders" / "batch_manifest.json"
        self.manifest.write_text(json.dumps({"takes": [data.read_json(take / "take.json") for take in self.takes]}))
        spec.add_paths(self.model, self.paths, self.scene)

    def tearDown(self):
        job = getattr(self.model, "_collection_render_scan", None)
        if job is not None:
            job.progress.cancel()
            job.shutdown()
        dialog = getattr(self.model, "_collection_review_dialog", None)
        if dialog is not None:
            dialog.close()
        APP.processEvents()
        self.scene.clear()
        self.scene.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def make_take(self, folder, source, status="completed"):
        folder.mkdir(parents=True)
        for number, color in ((2, "blue"), (1, "red")):
            image = QtGui.QImage(80, 40, QtGui.QImage.Format_RGB32)
            image.fill(QtGui.QColor(color))
            self.assertTrue(image.save(str(folder / f"frame_{number:04d}.png")))
        (folder / "take.json").write_text(json.dumps({"source_path": str(source), "status": status,
            "take_folder": str(folder), "output_template": str(folder / "frame.png"), "frame_rate": 12}))

    def assign(self):
        result = data.scan_renders(spec.entries(self.model), None, self.root / "renders")
        self.assertEqual(data.apply_assignments(self.model, self.scene, result), 2)
        return result

    def dialog(self, number=1):
        dialog = review.open_review(self.model, self.scene, None, number)
        APP.processEvents()
        return dialog

    def test_exact_path_matching_first_frame_and_persisted_metadata(self):
        self.assign()
        rows = spec.entries(self.model)
        self.assertEqual(rows[0]["render"]["folder"], str(self.takes[0]))
        self.assertEqual(Path(rows[0]["render"]["first_frame"]).name, "frame_0001.png")
        self.assertEqual(rows[0]["render"]["frame_rate"], 12)
        rows[0]["approved"] = True
        spec.save_state(self.model, rows, 1, self.scene)
        self.widget._check_all(False)
        self.assign()  # Reloading the same renders preserves approvals.
        restored = GraphNode("Reloaded", kind="mocap_collection", params=_node_to_dict(self.model)["params"])
        self.assertTrue(spec.entries(restored)[0]["approved"])
        self.assertFalse(spec.entries(restored)[0]["checked"])
        self.assertEqual(spec.entries(restored)[0]["render"], rows[0]["render"])

    def test_unique_name_fallback_rejects_ambiguous_and_conflicting_metadata(self):
        other = self.root / "other" / "walk" / "different.bvh"
        other.parent.mkdir(parents=True)
        other.write_text(BVH.replace("{x}", "0"))
        spec.add_paths(self.model, [other], self.scene)
        (self.takes[0] / "take.json").unlink()
        result = data.scan_renders(spec.entries(self.model), None, self.root / "renders")
        self.assertEqual(len(result["assignments"]), 1)
        self.assertEqual(result["skipped"], 1)
        (self.takes[0] / "take.json").write_text(json.dumps({"source_path": str(other), "status": "completed"}))
        result = data.scan_renders(spec.entries(self.model), None, self.root / "renders")
        self.assertIn(data.path_key(other), result["assignments"])
        (self.takes[1] / "take.json").write_text(json.dumps({"source_path": str(self.root / "missing.bvh")}))
        result = data.scan_renders(spec.entries(self.model), None, self.root / "renders")
        self.assertNotIn(data.path_key(self.paths[1]), result["assignments"])

    def test_newest_completed_take_selected_and_cancel_preserves_assignments(self):
        self.assign()
        old = spec.entries(self.model)
        cancelled = data.scan_renders(old, None, self.root / "renders", cancelled=lambda: True)
        self.assertEqual(data.apply_assignments(self.model, self.scene, cancelled), 0)
        self.assertEqual(spec.entries(self.model), old)
        newest = self.root / "renders" / "Take_001_walk_again"
        self.make_take(newest, self.paths[0])
        os.utime(newest / "take.json", (2_000_000_000, 2_000_000_000))
        incomplete = self.root / "renders" / "Take_009_walk"
        self.make_take(incomplete, self.paths[0], "cancelled")
        result = data.scan_renders(old, None, self.root / "renders")
        self.assertEqual(result["assignments"][data.path_key(self.paths[0])]["folder"], str(newest))

    def test_review_controls_output_approval_playback_and_fullscreen(self):
        self.assign()
        dialog = self.dialog()
        self.assertEqual(spec.output_number(self.model), 1)
        self.assertEqual(dialog.player._mode, "sequence")
        self.assertTrue(dialog.player._seq_timer.isActive())
        self.assertEqual(dialog.player._frame_interval_ms(), 83)
        dialog.player._stop_playback()
        dialog.player._on_sequence_tick()
        self.assertEqual(dialog.player._seq_index, 1)
        dialog.approve.click()
        self.assertTrue(spec.entries(self.model)[0]["approved"])
        self.assertTrue(spec.entries(self.model)[0]["checked"])
        self.assertEqual(self.widget.table.item(0, 3).background().color().name(), "#338253")
        dialog.next.click()
        self.assertEqual(spec.output_number(self.model), 2)
        self.assertEqual(dialog.player._seq_paths[0].parent, self.takes[1])
        self.widget.slider.setValue(1)
        self.assertEqual(dialog.row_number, 1)
        for flag in (QtCore.Qt.WindowMinimizeButtonHint, QtCore.Qt.WindowMaximizeButtonHint,
                     QtCore.Qt.WindowCloseButtonHint):
            self.assertTrue(dialog.windowFlags() & flag)
        dialog.showMaximized()
        self.assertTrue(dialog.isMaximized())
        self.assertFalse(dialog.isFullScreen())
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_F11)
        self.assertTrue(dialog.isFullScreen())
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
        # Windows restores maximization through a queued native window message.
        QtTest.QTest.qWait(30)
        self.assertFalse(dialog.isFullScreen())
        self.assertTrue(dialog.isMaximized())
        dialog.showMinimized()
        self.assertTrue(dialog.isMinimized())
        self.assertIs(review.open_review(self.model, self.scene), dialog)
        self.assertFalse(dialog.isMinimized())
        dialog.close()
        self.assertFalse(dialog.player._seq_timer.isActive())

    def test_checked_only_navigation_and_unchecked_thumbnail_preview(self):
        self.assign()
        rows = spec.entries(self.model)
        rows[0]["checked"] = False
        spec.mocap._set_param_value(self.model, "checked_only", "1")
        spec.save_state(self.model, rows, 2, self.scene)
        dialog = self.dialog(1)
        self.assertEqual(dialog.row_number, 1)
        self.assertEqual(spec.output_number(self.model), 2)
        self.assertIn("Preview only", dialog.message.text())
        dialog.approve.click()
        self.assertTrue(spec.entries(self.model)[0]["approved"])
        self.assertFalse(spec.entries(self.model)[0]["checked"])
        dialog.checked_only.setChecked(False)
        self.assertEqual(spec.output_number(self.model), 1)

    def test_review_rebinds_lost_window_owner_without_restarting_playback_or_focus(self):
        self.assign()
        window = QtWidgets.QMainWindow()
        window.setCentralWidget(QtWidgets.QGraphicsView(self.scene))
        self.addCleanup(window.deleteLater)
        window.show()
        dialog = self.widget._review(1)
        APP.processEvents()
        player = dialog.player
        self.assertTrue(player._seq_timer.isActive())
        player._stop_playback()
        player._show_sequence_frame(1)
        player._on_play_clicked()
        dialog.showMaximized()
        flags = dialog.windowFlags()
        handle = dialog.windowHandle()
        handle.setTransientParent(None)
        with patch.object(dialog, "activateWindow") as activate, \
             patch.object(player, "_load_media", wraps=player._load_media) as load:
            APP.sendEvent(window, QtCore.QEvent(QtCore.QEvent.WinIdChange))
            APP.processEvents()
            self.assertIs(handle.transientParent(), window.windowHandle())
            self.assertTrue(dialog.isVisible())
            self.assertTrue(dialog.isMaximized())
            self.assertTrue(player._seq_timer.isActive())
            self.assertEqual(player._seq_index, 1)
            self.assertEqual(dialog.windowFlags(), flags)
            activate.assert_not_called()
            load.assert_not_called()
        # A deliberate minimize must also survive a native handle change.
        dialog.showMinimized()
        handle.setTransientParent(None)
        APP.sendEvent(window, QtCore.QEvent(QtCore.QEvent.WinIdChange))
        APP.processEvents()
        self.assertTrue(dialog.isMinimized())
        self.assertIs(handle.transientParent(), window.windowHandle())
        window.close()

    def test_review_uses_main_window_owner_and_survives_launching_widget_rebuild(self):
        self.assign()
        window = QtWidgets.QMainWindow()
        controls = QtWidgets.QWidget(window)
        window.setCentralWidget(controls)
        self.addCleanup(window.deleteLater)
        dialog = review.open_review(self.model, self.scene, controls, 1)
        APP.processEvents()
        self.assertIs(dialog.parentWidget(), window)
        controls.deleteLater()
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        APP.processEvents()
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog.player._seq_timer.isActive())
        self.assertIs(self.model._collection_review_dialog, dialog)

    @unittest.skipUnless(sys.platform == "win32", "Windows native window ownership")
    def test_native_opengl_viewport_recreation_keeps_review_owned_and_visible(self):
        if APP.platformName() != "windows":
            self.skipTest("Run with QT_QPA_PLATFORM=windows for native OpenGL regression")
        import ctypes
        from PySide6 import QtOpenGLWidgets
        from shiboken6 import isValid
        user32 = ctypes.windll.user32
        user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetWindow.restype = ctypes.c_void_p
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        self.assign()
        window = QtWidgets.QMainWindow()
        window.resize(500, 400)
        # Exercise real Windows handles without putting test windows on the desktop.
        window.move(-20000, -20000)
        window.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        graph = QtWidgets.QGraphicsView(self.scene)
        window.setCentralWidget(graph)
        self.addCleanup(window.deleteLater)
        window.show()
        APP.processEvents()
        dialog = self.widget._review(1)
        dialog.move(-20000, -20000)
        APP.processEvents()
        original_owner = int(window.winId())
        self.assertEqual(user32.GetWindow(int(dialog.winId()), 4), original_owner)
        flags = dialog.windowFlags()
        try:
            viewport = QtOpenGLWidgets.QOpenGLWidget(window)
            window.setCentralWidget(viewport)
            QtTest.QTest.qWait(150)
            if not viewport.isValid():
                self.skipTest("Native OpenGL context unavailable")
            self.assertNotEqual(int(window.winId()), original_owner)
            self.assertIs(dialog.windowHandle().transientParent(), window.windowHandle())
            self.assertEqual(user32.GetWindow(int(dialog.winId()), 4), int(window.winId()))
            self.assertTrue(user32.IsWindowVisible(int(dialog.winId())))
            self.assertTrue(dialog.isVisible())
            self.assertTrue(dialog.player._seq_timer.isActive())
            self.assertEqual(dialog.windowFlags(), flags)
            dialog.player._stop_playback()
            dialog.player._show_sequence_frame(1)
            dialog.approve.click()
            APP.processEvents()
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog.player._seq_index, 1)
        finally:
            if isValid(dialog):
                dialog.close()
            window.close()

    def test_review_hotkeys_with_focused_controls_and_saved_approval_colors(self):
        self.assign()
        rows = spec.entries(self.model)
        rows[0]["approved"] = True
        spec.save_state(self.model, rows, 1, self.scene)
        dialog = self.dialog(1)

        def button_color():
            APP.processEvents()
            image = dialog.approve.grab().toImage()
            return image.pixelColor(6, image.height() // 2).name()

        self.assertTrue(dialog.approve.isChecked())
        self.assertIn(button_color(), ("#246b3f", "#2e804d"))
        dialog.approve.setFocus()
        QtTest.QTest.keyClick(dialog.approve, QtCore.Qt.Key_Space)
        self.assertFalse(dialog.player._seq_timer.isActive())
        self.assertTrue(spec.entries(self.model)[0]["approved"])
        QtTest.QTest.keyClick(dialog.approve, QtCore.Qt.Key_Space)
        self.assertTrue(dialog.player._seq_timer.isActive())

        # The arrows navigate animations even while the output/FPS editors have focus.
        dialog.output.setFocus()
        QtTest.QTest.keyClick(dialog.output.lineEdit(), QtCore.Qt.Key_Down)
        self.assertEqual(spec.output_number(self.model), 2)
        self.assertFalse(dialog.approve.isChecked())
        self.assertIn(button_color(), ("#4b5563", "#596474"))
        QtTest.QTest.keyClick(dialog.output, QtCore.Qt.Key_Down)
        self.assertEqual(spec.output_number(self.model), 2)
        dialog.fps.setFocus()
        fps = dialog.fps.value()
        QtTest.QTest.keyClick(dialog.fps.lineEdit(), QtCore.Qt.Key_Up)
        self.assertEqual(spec.output_number(self.model), 1)
        self.assertEqual(dialog.fps.value(), fps)
        self.assertTrue(dialog.approve.isChecked())
        self.assertIn(button_color(), ("#246b3f", "#2e804d"))
        dialog.approve.click()
        self.assertFalse(spec.entries(self.model)[0]["approved"])
        self.assertIn(button_color(), ("#4b5563", "#596474"))

        # A child rename dialog still accepts spaces as normal text input.
        rename = QtWidgets.QInputDialog(dialog)
        rename.setInputMode(QtWidgets.QInputDialog.TextInput)
        rename.show()
        edit = rename.findChild(QtWidgets.QLineEdit)
        QtTest.QTest.keyClicks(edit, "walk forward")
        self.assertEqual(rename.textValue(), "walk forward")
        self.assertTrue(dialog.player._seq_timer.isActive())
        rename.close()

    def test_thumbnails_and_sizing(self):
        self.assign()
        view = QtWidgets.QGraphicsView(self.scene)
        view.resize(1000, 700)
        view.show()
        APP.processEvents()
        self.widget._queue_thumbnails()
        for _ in range(5):
            APP.processEvents()
        self.assertEqual(self.widget.table.columnCount(), 4)
        self.assertFalse(self.widget.table.item(0, 2).icon().isNull())
        for proxy in self.item._plugin_proxies:
            self.assertLessEqual(proxy.geometry().bottom() + 5, self.item.height)
            self.assertGreaterEqual(proxy.size().height(), proxy.widget().minimumSizeHint().height())
        view.close()
        view.deleteLater()

    def test_load_renders_background_scan_survives_body_rebuild(self):
        def pick_folder(*args):
            self.item._build_widgets()
            self.widget = self.item._plugin_proxies[0].widget()
            return str(self.root / "renders")
        with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", side_effect=pick_folder), \
             patch.object(QtWidgets.QMessageBox, "information") as message:
            self.widget._load_renders()
            for _ in range(100):
                time.sleep(0.01)  # Let the Python scan worker acquire the GIL.
                APP.processEvents()
                if getattr(self.model, "_collection_render_scan", None) is None:
                    break
            self.assertIsNone(self.model._collection_render_scan)
            self.assertTrue(spec.entries(self.model)[0].get("render"))
            message.assert_called_once()

    def test_review_output_updates_live_view_once_with_multiple_collection_widgets(self):
        self.assign()
        window = QtWidgets.QMainWindow()
        window.setCentralWidget(QtWidgets.QGraphicsView(self.scene))
        window.open_scene_assets = Mock(return_value=True)
        self.addCleanup(window.deleteLater)
        other_controls = spec.MocapCollectionWidget(self.model, self.scene)
        self.addCleanup(other_controls.deleteLater)
        dialog = self.dialog(1)
        self.widget.live_view.setChecked(True)
        QtTest.QTest.qWait(130)
        window.open_scene_assets.reset_mock()
        dialog.next.click()
        QtTest.QTest.qWait(130)
        window.open_scene_assets.assert_called_once()
        self.assertEqual(Path(window.open_scene_assets.call_args.args[0][0]["path"]), self.paths[1])

    def test_review_and_rename_do_not_change_running_batch(self):
        self.assign()
        dialog = self.dialog(1)
        original = spec.entries(self.model)
        self.scene._foreach_active_session = object()
        try:
            dialog.approve.click()
            self.assertEqual(spec.entries(self.model), original)
            self.assertFalse(dialog.approve.isChecked())
            self.assertIn("Stop", dialog.message.text())
            with self.assertRaisesRegex(ValueError, "Stop"):
                data.rename_entry(self.model, self.scene, 1, "busy")
        finally:
            self.scene._foreach_active_session = None

    def test_rename_moves_folders_and_updates_all_metadata(self):
        self.assign()
        rows = spec.entries(self.model)
        rows[0]["approved"] = True
        spec.save_state(self.model, rows, 1, self.scene)
        data.rename_entry(self.model, self.scene, 1, "good_walk")
        updated = spec.entries(self.model)[0]
        self.assertTrue(updated["approved"])
        self.assertEqual(Path(updated["path"]).name, "animation.bvh")
        self.assertEqual(Path(updated["path"]).parent.name, "good_walk")
        self.assertTrue(Path(updated["path"]).is_file())
        take = Path(updated["render"]["folder"])
        self.assertEqual(take.name, "Take_004_good_walk")
        self.assertTrue(Path(updated["render"]["first_frame"]).is_file())
        record = data.read_json(take / "take.json")
        self.assertEqual(record["source_path"], updated["path"])
        self.assertEqual(Path(record["output_template"]).parent, take)
        manifest = data.read_json(self.manifest)
        self.assertEqual(manifest["takes"][0]["source_path"], updated["path"])
        self.assertEqual(manifest["takes"][0]["take_folder"], str(take))
        self.assertEqual(manifest["takes"][1]["source_path"], str(self.paths[1]))
        self.assertEqual(spec.mocap._param_value(self.model, "path"), updated["path"])
        self.assertEqual(len(data.scan_renders(spec.entries(self.model), None, self.root / "renders")["assignments"]), 2)

    def test_collision_invalid_names_and_move_failure_preserve_originals(self):
        self.assign()
        original = spec.entries(self.model)
        collision = self.root / "renders" / "Take_004_existing"
        collision.mkdir()
        with self.assertRaisesRegex(ValueError, "already"):
            data.rename_entry(self.model, self.scene, 1, "existing")
        for name in ("../escape", "CON", "bad/name", "bad:name", ""):
            with self.assertRaises(ValueError):
                data.rename_entry(self.model, self.scene, 1, name)
        rename = Path.rename
        def fail_second(path, target):
            if path == self.takes[0]:
                raise OSError("folder busy")
            return rename(path, target)
        with patch.object(Path, "rename", fail_second), self.assertRaisesRegex(OSError, "folder busy"):
            data.rename_entry(self.model, self.scene, 1, "new_walk")
        self.assertTrue(self.paths[0].is_file())
        self.assertTrue(self.takes[0].is_dir())
        self.assertEqual(spec.entries(self.model), original)

    def test_metadata_failure_rolls_back_files_and_folders(self):
        self.assign()
        before = self.manifest.read_bytes()
        write = Path.write_text
        def fail_metadata(path, text, *args, **kwargs):
            if path.name == "take.json":
                raise OSError("metadata locked")
            return write(path, text, *args, **kwargs)
        with patch.object(Path, "write_text", fail_metadata), self.assertRaisesRegex(OSError, "metadata locked"):
            data.rename_entry(self.model, self.scene, 1, "new_walk")
        self.assertTrue(self.paths[0].is_file())
        self.assertTrue(self.takes[0].is_dir())
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_shared_bvh_folder_updates_other_open_collections(self):
        sibling = self.paths[0].with_name("other.bvh")
        sibling.write_text(BVH.replace("{x}", "0"))
        other = GraphNode("Other", kind="mocap_collection")
        self.scene.add_node(other, QtCore.QPointF(700, 0))
        spec.add_paths(other, [sibling], self.scene)
        data.rename_entry(self.model, self.scene, 1, "shared")
        self.assertEqual(Path(spec.entries(other)[0]["path"]).parent.name, "shared")
        self.assertTrue(Path(spec.entries(other)[0]["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
