"""Viewport view chords, axis poses, and perspective restoration without a GPU."""
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from echograph.ui.gl_arcball import _ArcBallUtil
from echograph.ui.gl_view import GraphGLView
from echograph.ui.gl_view_math import gizmo_screen_scale, project_local
from echograph.ui import hotkeys_config
from echograph.ui.hotkeys import CameraViewHotkeyEditor


class CameraViewHotkeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        cache = patch.object(hotkeys_config, "_KEYMAP_CACHE", dict(hotkeys_config.DEFAULT_KEYMAP))
        cache.start()
        self.addCleanup(cache.stop)
        self.scene = QtWidgets.QGraphicsScene()
        self.view = GraphGLView(self.scene)
        self.view._mgl_arcball = _ArcBallUtil(800, 600)
        self.view._mgl_center = np.array([2, 3, 4], dtype="f4")
        self.view._mgl_camera_zoom = 12.0

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def press(self, key, repeat=False):
        event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier, "", repeat)
        return self.view._handle_camera_view_key(event)

    def test_pairs_and_camera_directions(self):
        self.press(QtCore.Qt.Key_Space)
        for key, names, directions in (
            (QtCore.Qt.Key_2, ("Top", "Bottom"), ((0, 1, 0), (0, -1, 0))),
            (QtCore.Qt.Key_3, ("Front", "Back"), ((0, 0, 1), (0, 0, -1))),
            (QtCore.Qt.Key_4, ("Left", "Right"), ((-1, 0, 0), (1, 0, 0))),
        ):
            for name, direction in zip(names, directions):
                self.assertTrue(self.press(key))
                self.assertEqual(self.view._view_combo.currentText(), name)
                self.assertEqual(self.view._mgl_projection, "orthographic")
                rotation = self.view._mgl_arcball.Transform[:3, :3]
                np.testing.assert_allclose(rotation[:, 2], direction, atol=1e-6)
                np.testing.assert_allclose(rotation.T @ rotation, np.eye(3), atol=1e-6)
                np.testing.assert_allclose(self.view._mgl_center, [2, 3, 4])
                self.press(key, repeat=True)
                self.assertEqual(self.view._view_combo.currentText(), name)

    def test_editor_round_trip_and_remapping(self):
        editor = CameraViewHotkeyEditor()
        self.addCleanup(editor.deleteLater)
        editor.set_value("Space+5")
        self.assertTrue(editor.hold_space.isChecked())
        self.assertEqual(editor.value(), "Space+5")
        hotkeys_config._KEYMAP_CACHE["gl_view_top_bottom"] = editor.value()
        self.press(QtCore.Qt.Key_Space)
        self.assertFalse(self.press(QtCore.Qt.Key_2))
        self.assertTrue(self.press(QtCore.Qt.Key_5))
        self.assertEqual(self.view._view_combo.currentText(), "Top")
        self.view._view_space_held = False
        editor.set_value("F6")
        self.assertFalse(editor.hold_space.isChecked())
        hotkeys_config._KEYMAP_CACHE["gl_view_top_bottom"] = editor.value()
        self.assertTrue(self.press(QtCore.Qt.Key_F6))
        self.assertEqual(self.view._view_combo.currentText(), "Bottom")
        editor.key_edit.clear()
        hotkeys_config._KEYMAP_CACHE["gl_view_top_bottom"] = editor.value()
        self.assertFalse(self.press(QtCore.Qt.Key_F6))

    def test_perspective_restores_pose_and_zoom(self):
        self.view._mgl_orbit_yaw = 0.7
        self.view._mgl_orbit_pitch = 0.4
        self.view._apply_locked_orbit()
        original = self.view._mgl_arcball.Transform.copy()
        self.press(QtCore.Qt.Key_Space)
        self.press(QtCore.Qt.Key_2)
        self.view._mgl_camera_zoom = 20
        self.press(QtCore.Qt.Key_4)
        self.press(QtCore.Qt.Key_1)
        self.assertEqual(self.view._view_combo.currentText(), "Persp")
        self.assertEqual(self.view._mgl_projection, "perspective")
        self.assertEqual(self.view._mgl_camera_zoom, 12)
        np.testing.assert_allclose(self.view._mgl_arcball.Transform[:3, :3], original[:3, :3], atol=1e-6)
        np.testing.assert_allclose(self.view._mgl_center, [2, 3, 4])

    def test_release_and_focus_loss_clear_chord(self):
        self.assertFalse(self.press(QtCore.Qt.Key_2))
        self.press(QtCore.Qt.Key_Space)
        self.view.keyReleaseEvent(QtGui.QKeyEvent(
            QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Space, QtCore.Qt.NoModifier))
        self.assertFalse(self.press(QtCore.Qt.Key_2))
        self.press(QtCore.Qt.Key_Space)
        self.view.focusOutEvent(QtGui.QFocusEvent(QtCore.QEvent.FocusOut))
        self.assertFalse(self.press(QtCore.Qt.Key_2))

    def test_perspective_survives_navigation_between_axis_views(self):
        self.view._mgl_orbit_yaw = .65
        self.view._mgl_orbit_pitch = .3
        self.view._apply_locked_orbit()
        original = self.view._mgl_arcball.Transform.copy()
        self.view._set_camera_view(2)
        # Orbiting an axis view switches its projection to perspective. That
        # temporary pose must not replace the saved default camera on Space+3.
        self.view._leave_axis_view()
        self.view._mgl_orbit_yaw = -1.2
        self.view._mgl_orbit_pitch = .8
        self.view._apply_locked_orbit()
        self.view._mgl_center = np.array([20.,-3.,1.],dtype="f4")
        self.view._mgl_camera_zoom = 30.
        self.view._set_camera_view(3)
        self.view._set_camera_view(1)
        np.testing.assert_allclose(self.view._mgl_arcball.Transform[:3,:3],original[:3,:3],atol=1e-6)
        np.testing.assert_allclose(self.view._mgl_center,[2,3,4])
        self.assertEqual(self.view._mgl_camera_zoom,12.)
        self.assertAlmostEqual(self.view._mgl_orbit_yaw,.65,places=5)
        self.assertAlmostEqual(self.view._mgl_orbit_pitch,.3,places=5)

    def test_fly_camera_restores_full_rendered_pose_without_scale_reset(self):
        self.view._mgl_orbit_yaw = .9
        self.view._mgl_orbit_pitch = -.25
        self.view._apply_locked_orbit()
        self.view._on_fly_mode_toggled(True)
        self.view._fps_camera.move(3,2)
        self.view._fps_camera.apply_look(12,-8)
        original = np.asarray(self.view._fps_camera.view_matrix()).copy()
        self.view._mgl_scale_multiplier = 1.7
        self.view._set_camera_view(4)
        self.view._mgl_center = np.array([9.,1.,6.],dtype="f4")
        self.view._set_camera_view(1)
        self.assertTrue(self.view._fly_mode_enabled)
        self.assertTrue(self.view._fps_camera_active)
        np.testing.assert_allclose(np.asarray(self.view._fps_camera.view_matrix()),original,atol=1e-5)
        self.assertEqual(self.view._mgl_scale_multiplier,1.7)

    def test_viewport_space_never_plays_timeline(self):
        calls = []
        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Space"), self.view)
        shortcut.activated.connect(lambda: calls.append(True))
        self.view._shortcut_timeline_play_toggle = shortcut
        for chord in (False, True):
            self.press(QtCore.Qt.Key_Space)
            if chord:
                self.press(QtCore.Qt.Key_2)
            self.view.keyReleaseEvent(QtGui.QKeyEvent(
                QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Space, QtCore.Qt.NoModifier))
        self.assertEqual(calls, [])

    def test_orthographic_render_and_pick_projection(self):
        self.view._mgl_prog = {"Mvp": Mock()}
        self.view._set_camera_view(2)
        renderer = getattr(self.view, "_mgl_renderer", None) or self.view
        renderer._paint_mgl_build_matrices(dbg=False)
        projection = self.view._mgl_pick_proj
        np.testing.assert_allclose(projection[3], [0, 0, 0, 1])
        self.view._set_camera_view(1)
        renderer._paint_mgl_build_matrices(dbg=False)
        self.assertEqual(self.view._mgl_pick_proj[3, 3], 0)

    def test_dropdown_selects_exact_view_and_matches_hotkeys(self):
        combo = self.view._view_combo
        for name in ("Bottom", "Top", "Right", "Left", "Back", "Front", "Persp"):
            index = combo.findText(name)
            combo.setCurrentIndex(index)
            combo.activated.emit(index)
            self.assertEqual(self.view._camera_view_name, name)
            self.assertEqual(combo.currentText(), name)
            combo.activated.emit(index)
            self.assertEqual(self.view._camera_view_name, name)
        self.view.resize(800, 600)
        self.view.resizeEvent(QtGui.QResizeEvent(QtCore.QSize(800, 600), QtCore.QSize(640, 480)))
        view_rect = self.view._view_select_frame.geometry()
        camera_rect = self.view._cam_select_frame.geometry()
        self.assertLess(view_rect.right(), camera_rect.left())
        self.assertEqual(view_rect.top(), camera_rect.top())
        self.assertEqual(view_rect.height(), camera_rect.height())

    def test_timeline_chord_only_in_panel(self):
        panel = QtWidgets.QFrame(self.view)
        self.view._timeline_panel = panel
        self.view.timeline_toggle_playback = Mock()

        def send(key, kind=QtCore.QEvent.KeyPress, repeat=False):
            return self.view._handle_timeline_play_key(QtGui.QKeyEvent(
                kind, key, QtCore.Qt.NoModifier, "", repeat))

        with patch.object(panel, "isVisible", return_value=True), patch.object(
            QtWidgets.QApplication, "widgetAt", return_value=panel
        ) as target:
            self.assertFalse(send(QtCore.Qt.Key_P))
            self.assertTrue(send(QtCore.Qt.Key_Space))
            self.view.timeline_toggle_playback.assert_not_called()
            self.assertTrue(send(QtCore.Qt.Key_P))
            self.assertTrue(send(QtCore.Qt.Key_P, repeat=True))
            self.view.timeline_toggle_playback.assert_called_once()
            send(QtCore.Qt.Key_Space, QtCore.QEvent.KeyRelease)
            self.assertFalse(send(QtCore.Qt.Key_P))
            target.return_value = self.view
            self.assertFalse(send(QtCore.Qt.Key_Space))
            self.assertFalse(send(QtCore.Qt.Key_P))
            self.view.timeline_toggle_playback.assert_called_once()


def _gizmo_matrix(value):
    return np.asarray(value.data(),dtype=np.float32).reshape(4,4).T


class GizmoScreenScaleTests(unittest.TestCase):
    def radius(self,projection,view,*,scene_scale=1.,multiplier=1.,height=800):
        model = np.diag([scene_scale,scene_scale,scene_scale,1.]).astype(np.float32)
        identity = np.eye(4,dtype=np.float32)
        scale = gizmo_screen_scale(projection,view,model,identity,identity,height,multiplier,143.,.9)
        scaled = np.diag([scale,scale,scale,1.]).astype(np.float32)
        mvp = projection@view@model@scaled
        center = np.asarray(project_local((0,0,0),mvp,height*1.5,height))
        return max(np.linalg.norm(np.asarray(project_local(axis*.9,mvp,height*1.5,height))-center)
                   for axis in np.eye(3))

    def test_all_six_orthographic_views_keep_pixel_size_during_zoom_and_depth_changes(self):
        directions = {
            'Front':((0,0,1),(0,1,0)), 'Back':((0,0,-1),(0,1,0)),
            'Left':((-1,0,0),(0,1,0)), 'Right':((1,0,0),(0,1,0)),
            'Top':((0,1,0),(0,0,-1)), 'Bottom':((0,-1,0),(0,0,1)),
        }
        for name,(direction,up) in directions.items():
            for half_height in (.5,2.,25.):
                projection = QtGui.QMatrix4x4()
                projection.ortho(-half_height*1.5,half_height*1.5,-half_height,half_height,.001,10000.)
                for distance in (1.,20.,2000.):
                    view = QtGui.QMatrix4x4()
                    view.lookAt(QtGui.QVector3D(*(np.asarray(direction)*distance)),QtGui.QVector3D(),QtGui.QVector3D(*up))
                    with self.subTest(view=name,zoom=half_height,distance=distance):
                        self.assertAlmostEqual(self.radius(_gizmo_matrix(projection),_gizmo_matrix(view)),143.,places=3)

    def test_orthographic_size_compensates_for_scene_units_and_viewport_height(self):
        projection = QtGui.QMatrix4x4()
        projection.ortho(-6.,6.,-4.,4.,.001,1000.)
        view = QtGui.QMatrix4x4()
        view.lookAt(QtGui.QVector3D(0,0,30),QtGui.QVector3D(),QtGui.QVector3D(0,1,0))
        for scene_scale in (.1,1.,100.):
            for multiplier in (.25,1.,8.):
                for height in (400,800,1600):
                    with self.subTest(scene_scale=scene_scale,multiplier=multiplier,height=height):
                        radius = self.radius(_gizmo_matrix(projection),_gizmo_matrix(view),scene_scale=scene_scale,
                                             multiplier=multiplier,height=height)
                        self.assertAlmostEqual(radius,143.,places=3)

    def test_perspective_size_matches_orthographic_and_stays_stable_with_distance(self):
        projection = QtGui.QMatrix4x4()
        projection.perspective(60.,1.5,.001,10000.)
        for distance in (1.,10.,100.):
            view = QtGui.QMatrix4x4()
            view.lookAt(QtGui.QVector3D(0,0,distance),QtGui.QVector3D(),QtGui.QVector3D(0,1,0))
            for scene_scale in (.1,1.,10.):
                with self.subTest(distance=distance,scene_scale=scene_scale):
                    self.assertAlmostEqual(self.radius(_gizmo_matrix(projection),_gizmo_matrix(view),scene_scale=scene_scale),143.,places=3)


if __name__ == "__main__":
    unittest.main()
