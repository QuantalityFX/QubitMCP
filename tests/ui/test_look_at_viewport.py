"""Exercise the Scene handoff, real camera pose, animation order, and Qt controls."""
import ast
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from echograph.rigging.fbx_canonical import (
    AnimationClip, Joint, JointAnimationTrack, JointTransform, QuatKeyframe,
    SkeletonAsset, Vec3Keyframe,
)
from echograph.ui.gl_scene import MGLScene, MGLSceneItem
from echograph.ui.gl_view import GraphGLView
from echograph.ui.skeleton_joint_picker import SkeletonJointPicker
from nodes.look_at.spec import constraint_config
from nodes.scene import spec as scene_spec


def _shelf_open_scene_assets():
    # Run the production handoff without importing the entire application/launchers.
    path = Path(__file__).resolve().parents[2] / "echograph_shelf.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EchoGraphWindow")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "open_scene_assets")
    namespace = {"Path": Path, "os": os, "QtCore": QtCore, "__file__": str(path)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["open_scene_assets"]


def _xform(pos=(0, 0, 0), rot=(0, 0, 0)):
    return {"pos": list(pos), "rot": list(rot), "scl": [1, 1, 1]}


class LookAtViewportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.open_scene_assets = staticmethod(_shelf_open_scene_assets())

    def setUp(self):
        self.window = QtWidgets.QWidget()
        self.graph = QtWidgets.QGraphicsScene()
        self.view = GraphGLView(self.graph, self.window)
        self.window.gl_view = self.view
        self.window._view_mode = "split"
        self.window._opening_scene_assets_from_scene_node = True
        self.window._active_scene_node = SimpleNamespace(name="Scene", kind="scene", params=[])
        self.window._timeline_controller = SimpleNamespace(sync_timeline_context=Mock())
        self.window._card_by_node = {}
        self.keys = {}
        self.view._timeline_keys_map_for_owner = lambda owner: self.keys.get(owner, {})
        self.view._timeline_collect_other_owner_keys = lambda: list(self.keys.items())
        self.view._timeline_ensure_composition_blocks_cached = lambda: None
        self.view._on_fly_mode_toggled = Mock()
        self.view._mgl_load_scene_assets = self._load_geometry
        self.assets = [
            {"node": "ShotCam", "kind": "camera", "ext": ".camera", "path": "",
             "xform": _xform((1, 2, 5), (10, 20, 30)),
             "look_at_constraint": {"node_name": "LookAt", "target_owner": "Target", "live_update": False}},
            {"node": "Target", "kind": "curve", "path": "", "xform": _xform((4, 0, -3))},
        ]

    def tearDown(self):
        self.view._set_scene_look_at_constraints([])
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def _load_geometry(self, assets, frame=True):
        # Only GPU allocation is replaced. Production load, transforms, keys,
        # constraints and camera-view synchronization all run normally.
        self.view._mgl_scene = MGLScene()
        self.view._mgl_scene_mesh_bounds_by_owner = {"Target": ((-1, 0, -1), (1, 4, 1))}
        for asset in assets:
            self.view._mgl_scene.add(MGLSceneItem(
                asset["node"], lambda *args: None,
                payload={"owner": asset["node"], "model": np.eye(4)},
                tag="scene-camera" if asset["kind"] == "camera" else "scene-model",
            ))

    def _open(self):
        pending = []
        with patch.object(QtCore.QTimer, "singleShot", side_effect=lambda delay, fn: pending.append(fn)):
            self.assertTrue(self.open_scene_assets(self.window, deepcopy(self.assets)))
            # Also exercise the delayed timeline restores that used to undo Look At.
            for _ in range(30):
                if not pending:
                    break
                pending.pop(0)()
            self.assertFalse(pending)

    def assert_camera_aims_at(self, point):
        pos, forward, _up = self.view._build_scene_camera_pose("ShotCam")
        expected = np.array(point, dtype=float) - pos
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(forward, expected, atol=1e-6)
        np.testing.assert_allclose(self.view._fps_camera.forward, expected, atol=1e-6)
        np.testing.assert_allclose(self.view._fps_camera.position, pos, atol=1e-6)
        proxy = next(item for item in self.view._mgl_scene.items() if item.payload.get("owner") == "ShotCam")
        proxy_forward = (np.array([0, 0, -1, 0]) @ proxy.payload["model"])[:3]
        np.testing.assert_allclose(proxy_forward, expected, atol=1e-6)

    def _install_joint_rig(self):
        skeleton = SkeletonAsset("Character", joints=[
            Joint("Root", -1),
            Joint("Spine", 0, JointTransform(translation=(0, 1, 0))),
            Joint("Head", 1, JointTransform(translation=(0, 2, 0))),
            Joint("Hand", 1, JointTransform(translation=(1, .5, 0))),
        ])
        clip = AnimationClip("Move", 0, 1, 24, tracks=[
            JointAnimationTrack("Root", translation_keys=[
                Vec3Keyframe(0, (0, 0, 0)), Vec3Keyframe(1, (2, 0, 0)),
            ]),
            JointAnimationTrack("Spine", rotation_keys=[
                QuatKeyframe(0, (0, 0, 0, 1)),
                QuatKeyframe(1, (0, 0, np.sqrt(.5), np.sqrt(.5))),
            ]),
        ])
        self.assets[1]["fbx_rig_context"] = {"skeleton": skeleton, "clip": clip, "loop": False}
        self.assets[0]["look_at_constraint"]["target_owner"] = "Target/Skeleton/Head"

    def test_joint_target_samples_animation_without_skeleton_overlay(self):
        self._install_joint_rig()
        self._open()
        self.assert_camera_aims_at((4, 3, -3))
        self.view._mgl_scene_skeleton_active_owner = ""
        self.view._mgl_scene_skeleton_handles_by_owner = {
            "Target": [{"name": "Head", "position": (999, 999, 999)}],
        }
        for frame, expected in ((12, (5 - np.sqrt(2), 1 + np.sqrt(2), -3)), (24, (4, 1, -3))):
            self.view._timeline_on_frame_slider_changed(frame)
            self.assert_camera_aims_at(expected)

    def test_joint_live_tracks_rig_transform_and_joint_edits(self):
        self._install_joint_rig()
        self._open()
        self.view.update_scene_look_at_constraint("LookAt", {"live_update": True})
        self.view._mgl_set_scene_asset_xform("Target", pos=(2, 3, -6))
        self.assert_camera_aims_at((2, 6, -6))
        self.assertTrue(self.view._mgl_scene_skeleton_apply_timeline_keys(
            "Target::skeleton_joint::Spine", {0: {"rxyz": [0, 0, 90]}}, 24,
        ))
        self.assert_camera_aims_at((0, 4, -6))
        context = self.view._mgl_scene_owner_fbx_rig_context("Target")
        context["skeleton"].joints[2].local_bind.translation = (0, 3, 0)
        with patch.object(self.view, "isVisible", return_value=True):
            self.view._scene_look_at_live_timer.timeout.emit()
        self.assert_camera_aims_at((-1, 4, -6))
        self.view.update_scene_look_at_constraint("LookAt", {"live_update": False}, evaluate=False)
        before = tuple(self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])
        self.view._mgl_scene_skeleton_apply_timeline_keys(
            "Target::skeleton_joint::Spine", {0: {"rxyz": [0, 0, 0]}}, 24,
        )
        self.assertEqual(before, self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])
        self.view._timeline_apply_scene_frame(0)
        self.assert_camera_aims_at((2, 7, -6))

    def test_joint_names_are_disambiguated_by_rig_and_missing_joint_keeps_pose(self):
        self._install_joint_rig()
        self._open()
        self.view.update_scene_look_at_constraint("LookAt", {"target_owner": "Head"})
        self.assert_camera_aims_at((4, 3, -3))
        other = deepcopy(self.view._timeline_scene_assets[1])
        other["node"] = "OtherCharacter"
        self.view._timeline_scene_assets.append(other)
        self.view._mgl_set_scene_asset_xform("OtherCharacter", pos=(-4, 0, -3))
        before = tuple(self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])
        self.view._mgl_apply_look_at_constraints()
        self.assertEqual(before, self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])
        self.view.update_scene_look_at_constraint("LookAt", {"target_owner": "OtherCharacter/Skeleton/Head"})
        self.assert_camera_aims_at((-4, 3, -3))
        before = tuple(self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])
        self.view.update_scene_look_at_constraint("LookAt", {"target_owner": "OtherCharacter/Skeleton/Missing"})
        self.assertEqual(before, self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])

    def test_skeleton_picker_hierarchy_drag_and_copy_into_look_at(self):
        self._install_joint_rig()
        self._open()
        model = SimpleNamespace(name="LookAt", params=[{"name": "target_owner", "value": "Target"}])
        graph = SimpleNamespace(_node_items={
            "LookAt": SimpleNamespace(model=model),
            "Scene": SimpleNamespace(model=self.window._active_scene_node),
        })
        card = QtWidgets.QWidget(self.window)
        card._node_ref = self.window._active_scene_node
        card._graph_scene = graph
        assets = [dict(self.assets[0], look_at_constraint=dict(constraint_config(model), node_name="LookAt")),
                  dict(self.assets[1], _quick=True)]
        with patch.object(scene_spec, "_collect_assets", return_value=assets), \
                patch.object(SkeletonJointPicker, "show") as show:
            scene_spec.augment_infocard_footer(card, QtWidgets.QVBoxLayout(card))
            outliner = card._scene_outliner_widget
            skeleton_row = next(outliner.item(i) for i in range(outliner.count())
                                if outliner.item(i).data(QtCore.Qt.UserRole + 1) == "skeleton")
            button = outliner.itemWidget(skeleton_row).findChild(QtWidgets.QToolButton, "SceneSkeletonJointsButton")
            self.assertIsNotNone(button)
            self.assertEqual(button.arrowType(), QtCore.Qt.RightArrow)
            self.assertGreaterEqual(skeleton_row.sizeHint().height(), button.height())
            row_widget = outliner.itemWidget(skeleton_row)
            label = row_widget.findChild(QtWidgets.QLineEdit)
            row_widget.resize(500, skeleton_row.sizeHint().height())
            row_widget.layout().activate()
            self.assertLessEqual(button.x() - label.geometry().right(), 8)
            self.assertLess(button.geometry().right(), row_widget.width() - 100)
            button.click()
            show.assert_called_once()
            picker = card._scene_joint_pickers["Target"]
            self.assertIs(picker.parentWidget(), self.window)
            self.assertEqual(picker.windowModality(), QtCore.Qt.NonModal)
            self.assertFalse(picker.windowFlags() & QtCore.Qt.WindowStaysOnTopHint)
            root = picker.tree.topLevelItem(0)
            self.assertEqual(root.text(0), "Root")
            spine = root.child(0)
            self.assertEqual(spine.text(0), "Spine")
            head, hand = spine.child(0), spine.child(1)
            self.assertEqual((head.text(0), hand.text(0)), ("Head", "Hand"))
            picker.search.setText("head")
            self.assertFalse(root.isHidden())
            self.assertFalse(head.isHidden())
            self.assertTrue(hand.isHidden())
            picker.tree.itemDoubleClicked.emit(head, 0)
            self.assertEqual(QtWidgets.QApplication.clipboard().text(), "Target/Skeleton/Head")
            target_row = next(outliner.item(i) for i in range(outliner.count())
                              if outliner.item(i).data(QtCore.Qt.UserRole + 1) == "look_at_target")
            edit = outliner.itemWidget(target_row).findChild(QtWidgets.QLineEdit)
            edit.setText(QtWidgets.QApplication.clipboard().text())
            edit.editingFinished.emit()
            self.assert_camera_aims_at((4, 3, -3))
            picker.search.clear()
            mime = picker.tree.mimeData([hand])
            self.assertEqual(mime.text(), "Target/Skeleton/Hand")
            drop = QtGui.QDropEvent(QtCore.QPointF(5, 5), QtCore.Qt.CopyAction, mime,
                                   QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
            edit.dropEvent(drop)
            self.assertTrue(drop.isAccepted())
            self.assertEqual(constraint_config(model)["target_owner"], "Target/Skeleton/Hand")
            self.assert_camera_aims_at((5, 1.5, -3))
            picker.close()

    def test_scene_handoff_and_rebuild_override_saved_camera_keys(self):
        self.keys["ShotCam"] = {0: {"xyz": [1, 2, 5], "rxyz": [65, 12, 29]}}
        before = deepcopy(self.keys)
        self._open()
        self.assertIn("ShotCam", self.view._mgl_scene_look_at_constraints_by_owner)
        self.assert_camera_aims_at((4, 2, -3))
        self.assertEqual(before, self.keys)

    def test_constraint_only_change_is_not_dropped_by_load_debounce(self):
        self._open()
        with patch.object(self.view, "load_scene_assets", wraps=self.view.load_scene_assets) as load:
            self.assets[0]["look_at_constraint"]["live_update"] = True
            self._open()
            load.assert_called_once()
        self.assertTrue(self.view._scene_look_at_live_timer.isActive())

    def test_gray_waits_for_frame_even_when_nothing_has_keys(self):
        self._open()
        before = tuple(self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])
        self.view._mgl_set_scene_asset_xform("Target", pos=(-4, 3, -6))
        self.assertEqual(before, self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])
        with patch.object(self.view, "isVisible", return_value=True):
            self.view._on_scene_look_at_live_tick()
        self.assertEqual(before, self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])
        self.view._timeline_on_frame_spin_changed(1)
        self.assert_camera_aims_at((-4, 5, -6))

    def test_every_frame_uses_all_owner_keys_before_constraint(self):
        self._open()
        # Deliberately list the target before the camera: the camera rotation key
        # must not undo the constraint, regardless of owner evaluation order.
        self.keys.update({
            "Target": {0: {"xyz": [4, 0, -3]}, 10: {"xyz": [-4, 3, -6]}},
            "ShotCam": {0: {"xyz": [1, 2, 5], "rxyz": [65, 12, 29]},
                        10: {"xyz": [2, 1, 4], "rxyz": [-30, 10, 70]}},
        })
        before = deepcopy(self.keys)
        self.view._timeline_frame_slider.setSliderDown(True)
        for frame in (0, 5, 10, 3):
            self.view._timeline_on_frame_slider_changed(frame)
            target = self.view._mgl_get_scene_asset_xform("Target")["pos"]
            self.assert_camera_aims_at((target[0], target[1] + 2, target[2]))
        self.assertEqual(before, self.keys)

    def test_playback_applies_constraint_without_any_keys(self):
        self._open()
        self.view._mgl_set_scene_asset_xform("Target", pos=(7, 1, -2))
        self.view._timeline_on_play_tick()
        self.assert_camera_aims_at((7, 3, -2))

    def test_red_follows_gizmo_and_timer_then_stops_when_gray(self):
        self._open()
        self.view.update_scene_look_at_constraint("LookAt", {"live_update": True})
        self.assertTrue(self.view._scene_look_at_live_timer.isActive())
        self.view._mgl_set_scene_asset_xform("target", pos=(-4, 3, -6))
        self.assert_camera_aims_at((-4, 5, -6))
        # A producer that writes a transform directly is still caught by the tick.
        self.view._mgl_scene_xforms_by_owner["Target"]["pos"] = (2, 4, -1)
        with patch.object(self.view, "isVisible", return_value=True):
            self.view._scene_look_at_live_timer.timeout.emit()
        self.assert_camera_aims_at((2, 6, -1))
        self.view.update_scene_look_at_constraint("LookAt", {"live_update": False}, evaluate=False)
        self.assertFalse(self.view._scene_look_at_live_timer.isActive())
        before = tuple(self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])
        self.view._mgl_set_scene_asset_xform("Target", pos=(8, 5, -2))
        self.assertEqual(before, self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])

    def test_idle_tick_does_not_repaint_or_move_unrelated_camera(self):
        self._open()
        self.view.update_scene_look_at_constraint("LookAt", {"live_update": True})
        self.view._mgl_set_scene_asset_xform("OtherCam", pos=(8, 4, 7), rot=(12, 23, 34))
        other = deepcopy(self.view._mgl_get_scene_asset_xform("OtherCam"))
        with patch.object(self.view, "update") as update:
            self.view._mgl_apply_look_at_constraints(live_only=True)
            update.assert_not_called()
        self.assertEqual(other, self.view._mgl_get_scene_asset_xform("OtherCam"))

    def test_target_center_matches_rendered_rotation_and_scale(self):
        self._open()
        for space in ("world", "local"):
            with self.subTest(space=space):
                self.view._mgl_xform_space = space
                self.view._mgl_set_scene_asset_xform("Target", rot=(12, 45, 27), scl=(2, 3, 1))
                self.view._timeline_apply_scene_frame(1)
                target = next(self.view._mgl_scene.iter_by_tag("scene-model"))
                center = (np.array([0, 2, 0, 1]) @ target.payload["model"])[:3]
                self.assert_camera_aims_at(center)

    def test_loading_another_scene_clears_constraints_and_live_timer(self):
        self._open()
        self.view.update_scene_look_at_constraint("LookAt", {"live_update": True})
        self.view.load_scene_assets([])
        self.assertEqual({}, self.view._mgl_scene_look_at_constraints_by_owner)
        self.assertFalse(self.view._scene_look_at_live_timer.isActive())

    def test_missing_or_coincident_target_keeps_finite_pose(self):
        self._open()
        before = deepcopy(self.view._mgl_get_scene_asset_xform("ShotCam"))
        self.view.update_scene_look_at_constraint("LookAt", {"target_owner": "missing"})
        self.assertEqual(before, self.view._mgl_get_scene_asset_xform("ShotCam"))
        pos = before["pos"]
        self.view._mgl_set_scene_asset_xform("Target", pos=(pos[0], pos[1] - 2, pos[2]))
        self.view.update_scene_look_at_constraint("LookAt", {"target_owner": "Target"})
        np.testing.assert_allclose(before["rot"], self.view._mgl_get_scene_asset_xform("ShotCam")["rot"])

    def test_outliner_button_persists_mode_and_updates_existing_view(self):
        self._open()
        model = SimpleNamespace(name="LookAt", kind="look_at", params=[{"name": "target_owner", "value": "Target"}])
        constraint_item = SimpleNamespace(model=model)
        scene_model = self.window._active_scene_node
        scene_item = SimpleNamespace(model=scene_model)
        class Graph(QtCore.QObject):
            paramChanged = QtCore.Signal(str, list)

        graph = Graph()
        graph._node_items = {"LookAt": constraint_item, "Scene": scene_item}

        def set_param(name, value, **kwargs):
            scene_spec._set_param_value(model, name, value)
            graph.paramChanged.emit(model.name, model.params)

        constraint_item._set_param_value = set_param
        card = QtWidgets.QWidget(self.window)
        card._node_ref = scene_model
        card._graph_scene = graph
        layout = QtWidgets.QVBoxLayout(card)
        self.window._card_by_node["Scene"] = card

        def live_button():
            outliner = card._scene_outliner_widget
            row = next(outliner.item(i) for i in range(outliner.count())
                       if outliner.item(i).data(QtCore.Qt.UserRole + 1) == "look_at_target")
            widget = outliner.itemWidget(row)
            button = widget.findChild(QtWidgets.QToolButton, "SceneLookAtLiveButton")
            self.assertGreaterEqual(row.sizeHint().height(), button.height())
            return button

        with patch.object(scene_spec, "_collect_assets", side_effect=lambda _: [
            dict(self.assets[0], look_at_constraint=dict(constraint_config(model), node_name="LookAt")),
            self.assets[1],
        ]):
            scene_spec.augment_infocard_footer(card, layout)
            button = live_button()
            self.assertIsNotNone(button)
            self.assertFalse(button.isChecked())
            self.assertEqual(button.icon().pixmap(16, 16).toImage().pixelColor(8, 8).name(), "#808080")
            button.click()
            self.assertTrue(constraint_config(model)["live_update"])
            self.assertTrue(self.view._scene_look_at_live_timer.isActive())
            button = live_button()
            self.assertEqual(button.icon().pixmap(16, 16).toImage().pixelColor(8, 8).name(), "#ef4444")
            card._scene_outliner_refresh(graph)
            button = live_button()
            self.assertTrue(button.isChecked())
            button.click()
            self.assertFalse(constraint_config(model)["live_update"])
            self.assertFalse(self.view._scene_look_at_live_timer.isActive())
            # Assign a different target through the actual line-edit signal.
            self.view._mgl_set_scene_asset_xform("OtherTarget", pos=(2, 4, -2))
            button = live_button()
            editor = button.parentWidget().findChild(QtWidgets.QLineEdit)
            editor.setText("OtherTarget")
            editor.editingFinished.emit()
            self.assertEqual(constraint_config(model)["target_owner"], "OtherTarget")
            self.assert_camera_aims_at((2, 4, -2))

    @unittest.skipUnless(sys.platform == "win32", "Requires desktop OpenGL")
    def test_scene_collect_to_real_opengl_camera(self):
        del self.view._mgl_load_scene_assets
        self.window.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        self.window.resize(640, 480)
        self.view.resize(640, 480)
        self.view.show()
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.view.isValid())
        with tempfile.TemporaryDirectory() as td:
            mesh = Path(td) / "target.obj"
            mesh.write_text("v -1 0 -1\nv 1 4 -1\nv 1 0 1\nf 1 2 3\n", encoding="utf-8")
            class Item:
                pass

            def item(name, kind, params=None):
                result = Item()
                result.model = SimpleNamespace(name=name, kind=kind, params=params or [])
                result.scene = lambda: self.graph
                return result
            camera = item("ShotCam", "camera")
            target = item("Target", "mesh", [{"name": "path", "value": str(mesh)}])
            constraint = item("LookAt", "look_at", [{"name": "target_owner", "value": "Target"}])
            scene = item("Scene", "scene")
            scene.model._scene_xforms = {asset["node"]: deepcopy(asset["xform"]) for asset in self.assets}
            edges = {id(scene): [SimpleNamespace(src=obj) for obj in (camera, constraint, target)],
                     id(constraint): [SimpleNamespace(src=camera)]}
            self.graph._ordered_in_edges = lambda obj: edges.get(id(obj), [])
            self.graph._in_edges = self.graph._ordered_in_edges
            self.graph._node_items = {obj.model.name: obj for obj in (camera, target, constraint, scene)}
            self.window._active_scene_node = scene.model
            self.assets = scene_spec._collect_assets(scene)
            self.keys["ShotCam"] = {0: {"xyz": [1, 2, 5], "rxyz": [65, 12, 29]}}
            self._open()
            self.app.processEvents()
            self.assert_camera_aims_at((4, 2, -3))
            self.view.update_scene_look_at_constraint("LookAt", {"live_update": True})
            self.view._mgl_set_scene_asset_xform("Target", pos=(-4, 3, -6))
            self.app.processEvents()
            self.assert_camera_aims_at((-4, 5, -6))
        self.view._clear_scene_asset_state()


if __name__ == "__main__":
    unittest.main()
