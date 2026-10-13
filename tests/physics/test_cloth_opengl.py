"""Exercise the actual Windows mesh loader and dynamic OpenGL buffers."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from echograph.model import GraphNode
from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.patterns.sewing import add_sew_pair
from echograph.physics.cloth import ClothSettings, build_cloth_asset
from echograph.physics.cloth.placement import piece_owner
from echograph.rigging.fbx_canonical import (
    AnimationClip, Joint, JointAnimationTrack, SkeletalMeshAsset, SkeletonAsset,
    Vec3Keyframe, VertexInfluence, VertexSkin,
)
from echograph.ui.gl_view import GraphGLView
from nodes.scene import spec as scene_spec
from nodes.render.spec import RenderNodeWidget


@unittest.skipUnless(sys.platform == "win32", "Requires Windows desktop OpenGL")
class ClothOpenGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_authored_particles_survive_import_and_deform_real_buffers(self):
        self.exercise_buffers("cpu")

    def test_panel_point_pin_keeps_settled_pose_and_red_markers_stay_out_of_render(self):
        view = GraphGLView(QtWidgets.QGraphicsScene())
        self.addCleanup(view.close)
        view.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        view.resize(512, 512)
        view.show()
        self.app.processEvents()
        if not view.isValid() or view._mgl_ctx is None:
            self.skipTest("OpenGL context unavailable")
        front, back = rectangle("Front", 100, 100), rectangle("Back", 100, 100)
        back.layout[0] = 200
        asset = build_cloth_asset(PatternDocument(pieces=[front, back]), 25)
        model = GraphNode("Cloth", kind="cloth_solver")
        config = {"asset": asset, "settings": ClothSettings(device="cpu", self_collision=False, gravity_y=0).to_dict(),
                  "pins": [], "collider_owner": "", "colliders": [], "cache_path": "", "node_model": model}
        view._mgl_load_scene_assets([{"node": "Cloth", "kind": "mesh", "path": str(asset.rest_obj()),
                                     "ext": ".obj", "cloth": config}], frame=True)
        view.repaint()
        self.app.processEvents()
        state = view._cloth_states["Cloth"]
        state.solver.x[:] += [0, .025, .02]
        state.solver.time = 6
        state.display_positions = state.solver.x.copy()
        state.version += 1
        view._cloth_mark_initial_pose(state)
        view.repaint()
        self.app.processEvents()

        def red_pixels(image):
            image = image.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
            rgba = np.frombuffer(image.bits(), dtype=np.uint8).reshape(image.height(), image.width(), 4)
            return int(np.count_nonzero((rgba[:, :, 0] > 200) & (rgba[:, :, 1] < 70) & (rgba[:, :, 2] < 70)))

        baseline = red_pixels(view.grabFramebuffer())
        token = piece_owner("Cloth", back.id)
        topology = view._mgl_mesh_topology_for_owner(token)
        local_index = int(np.argmin(np.linalg.norm(asset.positions[topology["vertex_ids"]] - [.25, .05, 0], axis=1)))
        target = int(topology["vertex_ids"][local_index])
        view._on_mesh_selection_mode_clicked("point")
        view._set_mesh_element_selection({"mode": "point", "owner": token, "vertex_index": local_index})
        before, rest = state.solver.x.copy(), state.solver.rest.copy()
        model._cloth_commands = [{"action": "pin"}]
        view.repaint()
        self.app.processEvents()
        self.assertIs(view._cloth_states["Cloth"], state)
        np.testing.assert_array_equal(state.solver.x, before)
        np.testing.assert_array_equal(state.solver.rest, rest)
        self.assertEqual(state.solver.time, 6)
        self.assertEqual(state.solver.pins.tolist(), [target])
        view._set_mesh_element_selection_many([])
        view.repaint()
        self.app.processEvents()
        self.assertGreater(red_pixels(view.grabFramebuffer()), baseline + 5)
        self.assertEqual(len(view._cloth_pinned_elements()), 1)
        view._mgl_render_output_active = True
        try:
            view.repaint()
            self.app.processEvents()
            self.assertLessEqual(red_pixels(view.grabFramebuffer()), baseline)
        finally:
            view._mgl_render_output_active = False
        # The pin also survives the next solve, including its authored rest shape.
        state.solver.update_settings(ClothSettings(device="cpu", self_collision=False, stretch=0, bend=0))
        state.solver.step()
        np.testing.assert_array_equal(state.solver.x[target], before[target])
        self.assertLess(state.solver.x[:, 1].mean(), before[:, 1].mean())

    def test_two_timeline_solvers_and_cloth_collider_in_cpu_viewport(self):
        self.exercise_two_solvers("cpu")

    def test_late_loaded_pants_rewind_and_render_from_saved_pose_on_cpu(self):
        self.exercise_two_solvers("cpu", late_saved_pants=True)

    def test_late_loaded_pants_rewind_and_render_from_saved_pose_on_cuda(self):
        try:
            import warp as wp
        except ImportError:
            self.skipTest("Warp runtime unavailable")
        wp.init()
        if not wp.is_cuda_available():
            self.skipTest("Warp CUDA unavailable")
        self.exercise_two_solvers("cuda", late_saved_pants=True)

    def test_two_timeline_solvers_and_cloth_collider_in_cuda_viewport(self):
        try:
            import warp as wp
        except ImportError:
            self.skipTest("Warp runtime unavailable")
        wp.init()
        if not wp.is_cuda_available():
            self.skipTest("Warp CUDA unavailable")
        self.exercise_two_solvers("cuda")

    def exercise_two_solvers(self, device, *, late_saved_pants=False):
        view = GraphGLView(QtWidgets.QGraphicsScene())
        self.addCleanup(view.close)
        view.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        view.resize(512, 512)
        view.show()
        self.app.processEvents()
        if not view.isValid() or view._mgl_ctx is None:
            self.skipTest("OpenGL context unavailable")
        skeleton = SkeletonAsset("Body Rig", [Joint("Root", -1, inverse_bind_matrix=tuple(np.eye(4).ravel()))])
        points = [(-1, -.05, -1), (0, -.05, 1), (1, -.05, -1), (0, -1.05, 0)]
        faces = [0, 1, 2, 0, 2, 3, 0, 3, 1, 1, 3, 2]
        mesh = SkeletalMeshAsset("Body", skeleton.name, 4, faces,
            [VertexSkin(i, [VertexInfluence(0, 1)]) for i in range(4)], metadata={"bind_positions": points})
        rig = {"skeleton": skeleton, "meshes": [mesh], "loop": False, "mesh_skinning_enabled": True,
               "clip": AnimationClip("Raise Body", 0, 32 / 24, 24, [JointAnimationTrack("Root",
                   translation_keys=[Vec3Keyframe(0, (0, 0, 0)), Vec3Keyframe(32 / 24, (0, .25, 0))])])}
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)
        configs = {name: {"asset": asset, "settings": ClothSettings(device=device, follow_timeline=True,
                         self_collision=False, gravity_y=-9.81 if late_saved_pants else 0, substeps=4, iterations=4).to_dict(),
                         "pins": [], "colliders": [{"node": "Body"}], "collider_owner": "Body", "cache_path": "",
                         "node_model": GraphNode(name, kind="cloth_solver")} for name in ("Shirt", "Pants")}

        def wait(frame):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.app.processEvents()
                if view._cloth_timeline_ready(frame):
                    return
                time.sleep(.001)
            self.fail("\n".join(config["node_model"]._cloth_status for config in configs.values()))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "body.obj"
            path.write_text("v -1 -0.05 -1\nv 0 -0.05 1\nv 1 -0.05 -1\nv 0 -1.05 0\n"
                            "f 1 2 3\nf 1 3 4\nf 1 4 2\nf 2 4 3\n", encoding="utf-8")
            # Put the dependent garment first to verify solver order is separate
            # from scene connections and the user's outliner display ordering.
            assets = [{"node": name, "kind": "mesh", "path": str(asset.rest_obj()), "ext": ".obj", "cloth": config,
                       "xform": {"pos": [0, .04 if name == "Shirt" else 0, 0], "rot": [-90, 0, 0], "scl": [1, 1, 1]}}
                      for name, config in configs.items()]
            assets.append({"node": "Body", "kind": "mesh", "path": str(path), "ext": ".obj", "fbx_rig_context": rig})
            view._mgl_load_scene_assets(assets, frame=True)
            body = next(item for item in view._mgl_scene.iter_by_tag("scene-model") if item.payload["owner"] == "Body")
            body.payload["fbx_rig_context"] = rig
            for item in view._cloth_items():
                if item.payload.get("owner") in configs:
                    configs[item.payload["owner"]] = item.payload["cloth"]
            view.repaint()
            wait(0)
            if late_saved_pants:
                pants = view._cloth_states["Pants"]
                positions, velocity = pants.solver.x.copy(), pants.solver.velocity.copy()
                pants.cache.clear()
                view._cloth_restore_saved_state(pants, {"solver_time": 6}, {144: positions}, {144: velocity}, 30)
                pants.version += 1
                view.update()
                wait(0)
                self.assertEqual(pants.initial_pose[0], 0)
                np.testing.assert_allclose(pants.solver.x, positions, atol=1e-7)
                self.assertAlmostEqual(pants.solver.time, 6)
            view._timeline_play_btn.setChecked(True)
            view._timeline_play_timer.stop()
            for frame in range(1, 33):
                view._timeline_on_play_tick()
                self.assertEqual(view._timeline_current_frame(), frame)
                view._timeline_on_play_tick()
                self.assertEqual(view._timeline_current_frame(), frame)
                wait(frame)
                for name, state in view._cloth_states.items():
                    self.assertEqual(state.frame, frame, configs[name]["node_model"]._cloth_status)
                    np.testing.assert_allclose(state.collider_previous["Body"][:, 1],
                                               np.asarray(points)[faces, 1] + .25 * frame / 32, atol=1e-6)
                    self.assertGreaterEqual(state.solver.x[:, 1].min(), -.05 + .25 * frame / 32 - 1e-5)
                    self.assertAlmostEqual(state.solver.time, (6 if late_saved_pants and name == "Pants" else 0) + frame / 24)
                    if device == "cuda":
                        self.assertTrue(state.solver.backend.startswith("Warp CUDA"), state.solver.backend_detail)
            view._timeline_stop_playback()
            shirt, pants = view._cloth_states["Shirt"], view._cloth_states["Pants"]
            self.assertIsNot(shirt.cache, pants.cache)
            cached_pants = pants.cache.get(32)[0].copy()
            initial_shirt = shirt.initial_pose[1].copy()
            configs["Shirt"]["collider_owner"] = "Body,Pants"
            view.update()
            self.app.processEvents()
            wait(32)
            self.assertIs(view._cloth_states["Shirt"], shirt)
            np.testing.assert_allclose(shirt.initial_pose[1], initial_shirt)
            np.testing.assert_allclose(pants.cache.get(32)[0], cached_pants)
            np.testing.assert_allclose(shirt.collider_previous["Pants"], cached_pants, atol=1e-6)
            self.assertGreaterEqual(shirt.solver.x[:, 1].min(), pants.solver.x[:, 1].min() - 1e-5)
            for frame in (3, 32, 12, 32):
                view._timeline_on_frame_slider_changed(frame)
                wait(frame)
                for state in (shirt, pants):
                    self.assertEqual(state.frame, frame)
                    self.assertAlmostEqual(state.solver.time, (6 if late_saved_pants and state is pants else 0) + frame / 24)
                    if device == "cuda":
                        self.assertTrue(state.solver.backend.startswith("Warp CUDA"), state.solver.backend_detail)
            controls = SimpleNamespace(_cancel_check=lambda: False, _frame_wait_seconds=15,
                _process_ui_events=lambda ms: self.app.processEvents(QtCore.QEventLoop.AllEvents, ms))
            self.assertTrue(RenderNodeWidget._prepare_scene_frame(controls, view, 34, ""))
            self.assertTrue(view._cloth_render_ready(34))
            self.assertEqual(shirt.frame, pants.frame)
            np.testing.assert_allclose(shirt.collider_previous["Pants"], pants.cache.get(34)[0], atol=1e-6)
            view.close()
            view._cloth_executor.shutdown(wait=True, cancel_futures=True)

    def test_cuda_worker_deforms_real_opengl_buffers(self):
        try:
            import warp as wp
        except ImportError:
            self.skipTest("Warp runtime unavailable")
        wp.init()
        if not wp.is_cuda_available():
            self.skipTest("Warp CUDA unavailable")
        self.exercise_buffers("cuda")

    def test_timeline_cpu_follows_animated_skin_and_pauses_with_playhead(self):
        self.exercise_timeline("cpu")

    def test_timeline_cpu_resumes_saved_sewn_state_with_animated_collider(self):
        self.exercise_timeline("cpu", saved_state=True)

    def test_timeline_cuda_resumes_saved_sewn_state_with_animated_collider(self):
        try:
            import warp as wp
        except ImportError:
            self.skipTest("Warp runtime unavailable")
        wp.init()
        if not wp.is_cuda_available():
            self.skipTest("Warp CUDA unavailable")
        self.exercise_timeline("cuda", saved_state=True)

    def test_timeline_cuda_follows_animated_skin_and_pauses_with_playhead(self):
        try:
            import warp as wp
        except ImportError:
            self.skipTest("Warp runtime unavailable")
        wp.init()
        if not wp.is_cuda_available():
            self.skipTest("Warp CUDA unavailable")
        self.exercise_timeline("cuda")

    def test_render_preparation_keeps_cpu_cloth_and_skin_at_scene_fps(self):
        self.exercise_timeline("cpu", saved_state=True, render_frames=True)

    def test_render_preparation_keeps_cuda_cloth_and_skin_at_scene_fps(self):
        try:
            import warp as wp
        except ImportError:
            self.skipTest("Warp runtime unavailable")
        wp.init()
        if not wp.is_cuda_available():
            self.skipTest("Warp CUDA unavailable")
        self.exercise_timeline("cuda", saved_state=True, render_frames=True)

    def test_scrub_cpu_reuses_saved_state_and_recalculates_for_changed_animation(self):
        self.exercise_timeline("cpu", saved_state=True, scrub_frames=True)

    def test_scrub_cuda_reuses_saved_state_and_recalculates_for_changed_animation(self):
        try:
            import warp as wp
        except ImportError:
            self.skipTest("Warp runtime unavailable")
        wp.init()
        if not wp.is_cuda_available():
            self.skipTest("Warp CUDA unavailable")
        self.exercise_timeline("cuda", saved_state=True, scrub_frames=True)

    def exercise_timeline(self, device, *, saved_state=False, render_frames=False, scrub_frames=False):
        view = GraphGLView(QtWidgets.QGraphicsScene())
        self.addCleanup(view.close)
        view.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        view.resize(512, 512)
        view.show()
        self.app.processEvents()
        if not view.isValid() or view._mgl_ctx is None:
            self.skipTest("OpenGL context unavailable")
        skeleton = SkeletonAsset("Body Rig", [Joint("Root", -1, inverse_bind_matrix=tuple(np.eye(4).ravel()))])
        body_points = [(-1, -.05, -1), (0, -.05, 1), (1, -.05, -1), (0, -1.05, 0)]
        body_faces = [0, 1, 2, 0, 2, 3, 0, 3, 1, 1, 3, 2]
        mesh = SkeletalMeshAsset("Body", skeleton.name, 4, body_faces,
            [VertexSkin(i, [VertexInfluence(0, 1)]) for i in range(4)], metadata={"bind_positions": body_points})
        bind_heights = np.asarray(body_points)[body_faces, 1]
        clip = AnimationClip("Raise body", 0, 4 / 24, 24, [JointAnimationTrack("Root",
            translation_keys=[Vec3Keyframe(0, (0, 0, 0)), Vec3Keyframe(4 / 24, (0, .25, 0))])])
        rig = {"skeleton": skeleton, "meshes": [mesh], "clip": clip, "loop": False,
               "mesh_skinning_enabled": True}
        pattern = PatternDocument(pieces=[rectangle("Panel", 100, 100)])
        if saved_state:
            other = rectangle("Back", 100, 100)
            other.layout[0] = 150
            front = pattern.pieces[0]
            pattern.pieces.append(other)
            add_sew_pair(pattern, front.id, front.segments[1].id, other.id, other.segments[3].id)
        asset = build_cloth_asset(pattern, 25)
        model = GraphNode("Cloth", kind="cloth_solver")
        settings = ClothSettings(device=device, follow_timeline=not saved_state, self_collision=False,
                                 substeps=4, end_frame=4, seam_seconds=.05, gravity_y=0 if saved_state else -9.81,
                                 fps=48 if render_frames else 24)
        config = {"asset": asset, "settings": settings.to_dict(), "pins": [], "colliders": [{"node": "Body"}],
                  "cache_path": "", "node_model": model}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "body.obj"
            path.write_text("v -1 -0.05 -1\nv 0 -0.05 1\nv 1 -0.05 -1\nv 0 -1.05 0\n"
                            "f 1 2 3\nf 1 3 4\nf 1 4 2\nf 2 4 3\n", encoding="utf-8")
            view._mgl_load_scene_assets([
                {"node": "Cloth", "kind": "mesh", "path": str(asset.rest_obj()), "ext": ".obj", "cloth": config},
                {"node": "Body", "kind": "mesh", "path": str(path), "ext": ".obj", "fbx_rig_context": rig},
            ], frame=True)
            # OBJ supplies the test buffers; a real FBX import also attaches its
            # rig context to the draw item, enabling animation of those buffers.
            body_item = next(item for item in view._mgl_scene.iter_by_tag("scene-model")
                             if item.payload["owner"] == "Body")
            body_item.payload["fbx_rig_context"] = rig
            view.repaint()
            self.app.processEvents()
            state = view._cloth_states["Cloth"]
            self.assertIn("Body", state.collider_previous, getattr(model, "_cloth_status", ""))
            np.testing.assert_allclose(state.collider_previous["Body"][:, 1], bind_heights, atol=1e-6)
            initial_time = 0
            seam_distance = lambda: np.linalg.norm(np.einsum("ni,nij->nj", asset.seam_weights,
                                                             state.solver.x[asset.seam_indices]), axis=1).mean()
            if saved_state:
                # Settle/sew against the bind pose while the animation remains at frame 0.
                for _ in range(3):
                    model._cloth_commands = [{"action": "step"}]
                    view.repaint()
                    if state.pending is not None:
                        state.pending.result(timeout=10)
                    view.repaint()
                    self.app.processEvents()
                self.assertEqual(state.frame, 3)
                self.assertLess(seam_distance(), .003)
                initial_positions = state.solver.x.copy()
                initial_velocity = state.solver.velocity.copy()
                initial_time = state.solver.time
                cache_path = str(Path(directory) / "sewn_state.npz")
                model._cloth_commands = [{"action": "save", "path": cache_path}]
                view.repaint()
                if state.pending is not None:
                    state.pending.result(timeout=10)
                view.repaint()
                self.app.processEvents()
                self.assertEqual(view._timeline_current_frame(), 0)
                self.assertTrue(Path(cache_path).is_file(), model._cloth_status)
                config["settings"]["follow_timeline"] = True
                model._cloth_commands = [{"action": "reset"}]
                view.repaint()
                state = view._cloth_states["Cloth"]
                model._cloth_commands = [{"action": "load_cache", "path": cache_path}]
                view.repaint()
                self.app.processEvents()
                self.assertEqual(view._timeline_current_frame(), 0)
                self.assertEqual(state.frame, 0)
                np.testing.assert_allclose(state.solver.x, initial_positions, atol=1e-7)
                np.testing.assert_allclose(state.solver.velocity, initial_velocity, atol=1e-7)
                self.assertAlmostEqual(state.solver.time, initial_time)
                np.testing.assert_allclose(state.collider_previous["Body"][:, 1], bind_heights, atol=1e-6)
                # Enable gravity and tune quality/material after loading the sewn snapshot.
                stored = Path(cache_path).read_bytes()
                config["settings"].update(gravity_y=-9.81, gravity_enabled=False, stretch=25000,
                                          bend=.1, density=.4, substeps=6, iterations=10, collision_quality=3)
                view.repaint()
                np.testing.assert_allclose(state.solver.x, initial_positions, atol=1e-7)
                config["settings"]["gravity_enabled"] = True
                view.repaint()
                self.assertIs(view._cloth_states["Cloth"], state)
                np.testing.assert_allclose(state.solver.x, initial_positions, atol=1e-7)
                np.testing.assert_allclose(state.solver.velocity, initial_velocity, atol=1e-7)
                self.assertAlmostEqual(state.solver.time, initial_time)
                self.assertEqual(state.cache_path, cache_path)
                self.assertEqual(Path(cache_path).read_bytes(), stored)
            if render_frames:
                # Legacy output overrides cannot change the scene or a saved cloth pose.
                view._mgl_render_fps_override = 30
                view._mgl_render_output_active = True
                controls = SimpleNamespace(_cancel_check=lambda: False, _frame_wait_seconds=10,
                    _process_ui_events=lambda ms: self.app.processEvents(QtCore.QEventLoop.AllEvents, ms))
                self.assertEqual(view._mgl_timeline_fps_value(), 24)
            elif not scrub_frames:
                view._timeline_play_btn.setChecked(True)
                view._timeline_play_timer.stop()  # Drive actual ticks explicitly for deterministic assertions.
            if scrub_frames:
                view._timeline_frame_slider.setSliderDown(True)
                view._timeline_scrub_aux_last_t = float("inf")  # Exercise the fast-drag throttle path.
            for frame in ((4, 2, 0, 3, 4) if scrub_frames else range(1, 5)):
                if render_frames:
                    self.assertTrue(RenderNodeWidget._prepare_scene_frame(controls, view, frame, ""))
                    self.assertTrue(view._cloth_render_ready(frame))
                    image = view._mgl_render_to_image(32, 32)
                    self.assertIsNotNone(image)
                    self.assertFalse(RenderNodeWidget._image_is_invalid_capture(controls, image))
                    body_item = next(item for item in view._mgl_scene.iter_by_tag("scene-model")
                                     if item.payload["owner"] == "Body")
                    # The actual character draw buffer matches the collision mesh.
                    body_entry = body_item.payload["submeshes"][0]
                    np.testing.assert_allclose(body_entry["points"][:, 1], bind_heights + .25 * frame / 4, atol=1e-6)
                elif scrub_frames:
                    view._timeline_on_frame_slider_changed(frame)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        # The slider must wake an idle viewport on its own;
                        # process normal Qt events rather than forcing paint.
                        self.app.processEvents()
                        if view._cloth_timeline_ready(frame):
                            break
                        time.sleep(.001)  # Yield the GIL as QApplication.exec() does while idle.
                    self.assertTrue(view._cloth_timeline_ready(frame), model._cloth_status)
                    self.assertEqual(view._timeline_current_frame(), frame)
                    body_entry = body_item.payload["submeshes"][0]
                    np.testing.assert_allclose(body_entry["points"][:, 1], bind_heights + .25 * frame / 4, atol=1e-6)
                else:
                    view._timeline_on_play_tick()
                    self.assertEqual(view._timeline_current_frame(), frame)
                    view._timeline_on_play_tick()
                    self.assertEqual(view._timeline_current_frame(), frame)
                    view.repaint()
                    self.app.processEvents()
                    if state.pending is not None:
                        state.pending.result(timeout=10)
                    view.repaint()
                    self.app.processEvents()
                self.assertEqual(state.frame, frame, model._cloth_status)
                np.testing.assert_allclose(state.collider_previous["Body"][:, 1], bind_heights + .25 * frame / 4, atol=1e-6)
                self.assertGreaterEqual(state.solver.x[:, 1].min(), -.05 + .25 * frame / 4 - 1e-5)
                self.assertAlmostEqual(state.solver.time, initial_time + frame / 24)
                if saved_state:
                    self.assertLess(seam_distance(), .003)
            if scrub_frames:
                cached_positions = state.solver.x.copy()
                with patch.object(state.solver, "step", wraps=state.solver.step) as solve:
                    for frame in (1, 4, 2, 4):
                        view._timeline_on_frame_slider_changed(frame)
                        view.repaint()
                        self.app.processEvents()
                        self.assertTrue(view._cloth_timeline_ready(frame), model._cloth_status)
                    solve.assert_not_called()
                    np.testing.assert_allclose(state.solver.x, cached_positions, atol=1e-6)
                    # Change the actual clip on the existing context at the same
                    # playhead. Both draw buffers and collider query must refresh.
                    rig["clip"] = AnimationClip("Changed Body", 0, 4 / 24, 24, [JointAnimationTrack("Root",
                        translation_keys=[Vec3Keyframe(0, (0, 0, 0)), Vec3Keyframe(4 / 24, (0, .4, 0))])])
                    for _ in range(20):
                        view.repaint()
                        self.app.processEvents()
                        if state.pending is not None:
                            state.pending.result(timeout=10)
                        if view._cloth_timeline_ready(4):
                            break
                    self.assertTrue(view._cloth_timeline_ready(4), model._cloth_status)
                    self.assertEqual(solve.call_count, 4)
                    self.assertAlmostEqual(state.solver.time, initial_time + 4 / 24)
                    np.testing.assert_allclose(state.collider_previous["Body"][:, 1], bind_heights + .4, atol=1e-6)
                    np.testing.assert_allclose(body_item.payload["submeshes"][0]["points"][:, 1], bind_heights + .4, atol=1e-6)
                    self.assertLess(seam_distance(), .003)
                    view._timeline_on_frame_slider_changed(0)
                    view.repaint()
                    np.testing.assert_allclose(state.solver.x, initial_positions, atol=1e-7)
                    np.testing.assert_allclose(state.solver.velocity, initial_velocity, atol=1e-7)
                    self.assertAlmostEqual(state.solver.time, initial_time)
                    self.assertEqual(solve.call_count, 4)
                view._timeline_frame_slider.setSliderDown(False)
            view._timeline_play_btn.setChecked(False)
            version, positions = state.version, state.solver.x.copy()
            for _ in range(3):
                view.repaint()
                self.app.processEvents()
            self.assertEqual(state.version, version)
            np.testing.assert_array_equal(state.solver.x, positions)
            self.assertNotIn("error", model._cloth_status.lower())
            if device == "cuda":
                self.assertTrue(state.solver.backend.startswith("Warp CUDA"), state.solver.backend_detail)
                self.assertGreater(state.solver.last_gpu_ms, 0)
            item = next(item for item in view._mgl_scene.iter_by_tag("scene-model") if item.payload["owner"] == "Cloth")
            view.makeCurrent()
            try:
                points = np.frombuffer(item.payload["mesh_entry"]["vbo"].read(), dtype=np.float32).reshape(-1, 3)
            finally:
                view.doneCurrent()
            np.testing.assert_allclose(points, positions, atol=1e-7)

    def test_panel_placement_updates_real_buffers_selection_and_gizmo(self):
        view = GraphGLView(QtWidgets.QGraphicsScene())
        self.addCleanup(view.close)
        view.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        view.resize(512, 512)
        view.show()
        self.app.processEvents()
        if not view.isValid() or view._mgl_ctx is None:
            self.skipTest("OpenGL context unavailable")
        front, back = rectangle("Front", 100, 100), rectangle("Back", 100, 100)
        back.layout[0] = 200
        asset = build_cloth_asset(PatternDocument(pieces=[front, back]), 25)
        model = GraphNode("Cloth", kind="cloth_solver")
        config = {"asset": asset, "settings": ClothSettings(device="cpu", self_collision=False).to_dict(),
                  "pins": [], "colliders": [], "cache_path": "", "node_model": model, "piece_transforms": {}}
        view._mgl_load_scene_assets([{"node": "Cloth", "kind": "mesh", "path": str(asset.rest_obj()),
                                     "ext": ".obj", "cloth": config}], frame=True)
        view.repaint()
        self.app.processEvents()
        token = piece_owner("Cloth", front.id)
        view._mgl_set_scene_asset_xform(token, pos=[.05, .05, .2], rot=[0, 180, 0])
        view.repaint()
        self.app.processEvents()
        item = next(view._mgl_scene.iter_by_tag("scene-model"))
        entry = item.payload["mesh_entry"]
        selected = np.asarray(asset.vertex_pieces) == front.id
        view.makeCurrent()
        try:
            gpu = np.frombuffer(entry["vbo"].read(), dtype=np.float32).reshape(-1, 3)
            faces = view._mgl_build_cached_mesh_selection_object_faces(token)
            self.assertIsNotNone(faces, getattr(view, "_mgl_error", ""))
            self.assertEqual(faces["count"], len(asset.pieces()[0]["faces"]) * 3)
            for resource in faces["resources"]:
                resource.release()
        finally:
            view.doneCurrent()
        np.testing.assert_allclose(gpu[selected, 2], .2, atol=1e-7)
        np.testing.assert_allclose(gpu[~selected], asset.positions[~selected], atol=1e-7)
        view._on_mesh_selection_mode_clicked("object")
        view._set_mesh_element_selection({"mode": "object", "owner": token})
        np.testing.assert_allclose(view._xform_gizmo_pos, [.05, .05, .2], atol=1e-7)
        lower, upper = view.get_scene_owner_selection_bounds(token)
        np.testing.assert_allclose((lower + upper) * .5, [.05, .05, .2], atol=1e-7)
        # Object mode routes a real triangle hit to the piece instead of the garment.
        with patch.object(view, "_mgl_pick_ray", return_value=(np.array([.04, .04, 1]), np.array([0, 0, -1]))):
            picked = view.pick_mesh_element_at(1, 1, 512, 512, mode="object")
            self.assertEqual(picked["owner"], token)

    def test_cloth_panel_mouse_gizmos_transform_only_the_selected_panel(self):
        self.exercise_panel_mouse_gizmos(placement_only=False)

    def test_pattern_panel_mouse_gizmos_transform_only_the_selected_panel(self):
        self.exercise_panel_mouse_gizmos(placement_only=True)

    def exercise_panel_mouse_gizmos(self, placement_only):
        graph = QtWidgets.QGraphicsScene()
        view = GraphGLView(graph)
        self.addCleanup(view.close)
        view.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        view.resize(512, 512)
        view.show()
        self.app.processEvents()
        if not view.isValid() or view._mgl_ctx is None:
            self.skipTest("OpenGL context unavailable")
        front, back = rectangle("Front", 100, 100), rectangle("Back", 100, 100)
        back.layout[0] = 200
        asset = build_cloth_asset(PatternDocument(pieces=[front, back]), 25)
        parent = "Pattern" if placement_only else "Cloth"
        model = GraphNode(parent, kind="pattern_design" if placement_only else "cloth_solver")
        settings = ClothSettings(device="cpu", self_collision=False, interactive=True, enabled=not placement_only)
        config = {"asset": asset, "settings": settings.to_dict(), "placement_only": placement_only,
                  "pins": [], "colliders": [], "cache_path": "", "node_model": model, "piece_transforms": {},
                  "solver_node": parent}
        assets = [{"node": parent, "kind": "mesh", "path": str(asset.rest_obj()), "ext": ".obj", "cloth": config,
                   "pattern_pieces": [{"id": p["id"], "name": p["name"]} for p in asset.pieces()]}]
        view.load_scene_assets(assets, frame=True)
        view.repaint()
        self.app.processEvents()
        token = piece_owner(parent, front.id)
        # Select the actual outliner row, which must attach the panel's gizmo
        # and enter Object mode before the real QWidget receives mouse events.
        scene_model = GraphNode("Scene", kind="scene")
        graph._node_items = {"Scene": SimpleNamespace(model=scene_model)}
        view.gl_view, view._active_scene_node = view, scene_model
        card = QtWidgets.QWidget(view)
        card._node_ref, card._graph_scene = scene_model, graph
        with patch.object(scene_spec, "_collect_assets", return_value=assets):
            scene_spec.augment_infocard_footer(card, QtWidgets.QVBoxLayout(card))
            outliner = card._scene_outliner_widget
            child = next(outliner.item(i) for i in range(outliner.count())
                         if outliner.item(i).data(QtCore.Qt.UserRole) == token)
            outliner.setCurrentItem(child)
        view.update_scene_asset_xform = card._scene_xform_refresh
        self.assertEqual(view._mesh_select_mode, "object")
        self.assertEqual(view._xform_gizmo_owner, token)
        selected = np.asarray(asset.vertex_pieces) == front.id
        root_xform = view._mgl_get_scene_asset_xform(parent)

        def mouse(event_type, position, modifiers=QtCore.Qt.NoModifier):
            button = QtCore.Qt.NoButton if event_type == QtCore.QEvent.MouseMove else QtCore.Qt.LeftButton
            buttons = QtCore.Qt.NoButton if event_type == QtCore.QEvent.MouseButtonRelease else QtCore.Qt.LeftButton
            event = QtGui.QMouseEvent(event_type, position, position, button, buttons, modifiers)
            self.app.sendEvent(view, event)

        for mode, field in (("translate", "pos"), ("rotate", "rot"), ("scale", "scl")):
            with self.subTest(mode=mode):
                view._set_xform_gizmo_mode(mode)
                view.repaint()
                self.app.processEvents()
                before = view._mgl_get_scene_asset_xform(token)
                points = next(view._mgl_scene.iter_by_tag("scene-model")).payload["mesh_entry"]["points"].copy()
                probe = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(), QtCore.QPointF(),
                                         QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                ctx = view._handle_mouse_press_moderngl_left_gizmo_build_context(probe)
                self.assertIsNotNone(ctx)
                rect = view._mgl_active_viewport_qrectf()
                center = QtCore.QPointF(ctx["p0"][0] / ctx["dpr"] + rect.left(),
                                       ctx["p0"][1] / ctx["dpr"] + rect.top())
                end = center + QtCore.QPointF(35, 20)
                mouse(QtCore.QEvent.MouseButtonPress, center)
                try:
                    self.assertIsNone(getattr(view, "_cloth_drag", None), "A gizmo click must not grab the cloth")
                    active = (getattr(view, "_xform_dragging", False) or getattr(view, "_rot_shared_arc_active", False)
                              or view._rot_shared.drag_view or view._rot_shared.drag_axis.active)
                    self.assertTrue(active, f"{mode} mouse press did not start a gizmo drag in Object mode")
                    mouse(QtCore.QEvent.MouseMove, end)
                finally:
                    mouse(QtCore.QEvent.MouseButtonRelease, end)
                view.repaint()
                self.app.processEvents()
                after = view._mgl_get_scene_asset_xform(token)
                self.assertGreater(np.linalg.norm(np.asarray(after[field]) - before[field]), 1e-5)
                self.assertEqual(view._xform_gizmo_owner, token)
                self.assertEqual(view._mgl_get_scene_asset_xform(parent), root_xform)
                entry = next(view._mgl_scene.iter_by_tag("scene-model")).payload["mesh_entry"]
                np.testing.assert_allclose(entry["points"][~selected], points[~selected], atol=1e-7)
                self.assertGreater(np.linalg.norm(entry["points"][selected] - points[selected]), 1e-5)
                saved = json.loads(next(p["value"] for p in model.params if p["name"] == "piece_transforms"))
                self.assertEqual(set(saved), {front.id})
        # Alt over an active handle still navigates without changing placement.
        before = view._mgl_get_scene_asset_xform(token)
        mouse(QtCore.QEvent.MouseButtonPress, center, QtCore.Qt.AltModifier)
        try:
            self.assertFalse(view._xform_dragging)
            self.assertIsNone(getattr(view, "_cloth_drag", None))
            self.assertTrue(view._mgl_orbit_dragging)
            mouse(QtCore.QEvent.MouseMove, end, QtCore.Qt.AltModifier)
        finally:
            mouse(QtCore.QEvent.MouseButtonRelease, end, QtCore.Qt.AltModifier)
        self.assertEqual(view._mgl_get_scene_asset_xform(token), before)

    def exercise_buffers(self, device):
        graph = QtWidgets.QGraphicsScene()
        view = GraphGLView(graph)
        self.addCleanup(view.close)
        view.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        view.resize(512, 512)
        view.show()
        self.app.processEvents()
        if not view.isValid() or view._mgl_ctx is None:
            self.skipTest("OpenGL context unavailable")
        # Intentionally coincident panels: an importer must not merge their particles.
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Front", 100, 100), rectangle("Back", 100, 100)]), 25)
        model = GraphNode("Cloth", kind="cloth_solver")
        config = {"asset": asset, "settings": ClothSettings(device=device, self_collision=False).to_dict(),
                  "pins": [], "collider_owner": "", "colliders": [], "cache_path": "", "node_model": model}
        view._mgl_load_scene_assets([{"node": "Cloth", "kind": "mesh", "path": str(asset.rest_obj()),
                                     "ext": ".obj", "cloth": config}], frame=True)
        view.repaint()
        self.app.processEvents()
        self.assertIn("Cloth", view._cloth_states, getattr(view, "_mgl_error", ""))
        self.assertNotIn("error", getattr(model, "_cloth_status", "").lower())
        item = next(view._mgl_scene.iter_by_tag("scene-model"))
        entry = item.payload["mesh_entry"]
        self.assertEqual(len(entry["points"]), len(asset.positions))
        np.testing.assert_array_equal(entry["vertex_ids"], np.arange(len(asset.positions)))
        before = entry["points"].copy()
        model._cloth_commands = [{"action": "step"}]
        view.repaint()
        self.app.processEvents()
        pending = view._cloth_states["Cloth"].pending
        if pending is not None:
            pending.result(timeout=10)
            view.repaint()
            self.app.processEvents()
        view.makeCurrent()
        try:
            gpu = np.frombuffer(entry["vbo"].read(), dtype=np.float32).reshape(-1, 3)
            normals = np.frombuffer(entry["nbo"].read(), dtype=np.float32).reshape(-1, 3)
        finally:
            view.doneCurrent()
        self.assertLess(gpu[:, 1].mean(), before[:, 1].mean())
        if device == "cuda":
            solver = view._cloth_states["Cloth"].solver
            self.assertTrue(solver.backend.startswith("Warp CUDA"), solver.backend_detail)
            self.assertGreater(solver.last_gpu_ms, 0)
        np.testing.assert_allclose(gpu, entry["points"])
        self.assertTrue(np.isfinite(normals).all())
        image = view.grabFramebuffer()
        self.assertFalse(image.isNull())
        # Both backends must resume a disk pose, including its velocity/time,
        # rather than restarting from the original flat panels.
        original = view._cloth_states["Cloth"]
        positions, velocity, elapsed = original.solver.x.copy(), original.solver.velocity.copy(), original.solver.time
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "resume.npz")

            def command(action):
                model._cloth_commands = [{"action": action, "path": path}]
                for _ in range(3):
                    view.repaint()
                    self.app.processEvents()
                    pending = view._cloth_states["Cloth"].pending
                    if pending is not None:
                        pending.result(timeout=10)

            command("save")
            self.assertTrue(Path(path).is_file())
            command("reset")
            command("load_cache")
            restored = view._cloth_states["Cloth"]
            np.testing.assert_allclose(restored.solver.x, positions, atol=1e-7)
            np.testing.assert_allclose(restored.solver.velocity, velocity, atol=1e-7)
            self.assertAlmostEqual(restored.solver.time, elapsed)
            command("step")
            self.assertLess(restored.solver.x[:, 1].mean(), positions[:, 1].mean())
            if device == "cuda":
                self.assertTrue(restored.solver.backend.startswith("Warp CUDA"), restored.solver.backend_detail)


if __name__ == "__main__":
    unittest.main()
