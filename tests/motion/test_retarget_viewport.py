from __future__ import annotations

import math
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from echograph.ui.gl_mgl_renderer import MGLRendererMixin, moderngl
from echograph.ui.gl_scene import MGLScene, MGLSceneItem


class Renderer(MGLRendererMixin):
    def __init__(self):
        self._mgl_scene = MGLScene()
        self._mgl_retarget_preview_active = True
        self._mgl_retarget_role_owners = {"source": "source", "target": "target"}
        self._mgl_retarget_joint_handles_by_owner = {
            role: [{"name": "root", "index": 0, "role": role, "owner": role,
                    "position": (0, 0, 0), "radius": 0.04},
                   {"name": "tip", "index": 1, "role": role, "owner": role,
                    "position": (0, 200 if role == "target" else 2, 0), "radius": 0.04}]
            for role in ("source", "target")}
        self._mgl_scene_visibility = {}
        self._mgl_scene_xforms_by_owner = {}
        self._mgl_scene_splats = {}
        self._mgl_scene_splats_bounds_local = {}
        self._mgl_scene_splat_bounds_by_owner = {}
        self._mgl_scene_splats_world = {}
        self._mgl_retarget_joint_map = {}
        self._mgl_retarget_node_model = None
        self._mgl_retarget_node_item = None
        self._mgl_retarget_last_pick_mode = ""
        self._mgl_arcball = SimpleNamespace(Transform=np.eye(4, dtype=np.float32))
        self._mgl_fov = 60.0
        self._mgl_scale_multiplier = 1.0
        self._use_moderngl = True
        self.update = Mock()
        self._mgl_log = Mock()
        self._mgl_scene_owner_fbx_rig_context = Mock(return_value=None)
        self._mgl_scene_skeleton_refresh_dynamic_handles = Mock()

    def add_handles(self, owner, scale=1.0, position=(0, 0, 0)):
        self._mgl_scene.add(MGLSceneItem(owner, lambda *args: None,
            payload={"owner": owner}, tag="retarget-handles"))
        self._mgl_set_scene_asset_xform(owner, pos=position, scl=(scale,) * 3)


class RetargetViewportTests(unittest.TestCase):
    def test_scene_reload_owns_context_before_cleanup_and_releases_on_failure(self):
        view = Renderer()
        view._mgl_ctx = object()
        calls = []
        view.makeCurrent = lambda: calls.append("current")
        view.doneCurrent = lambda: calls.append("done")
        def load(*args, **kwargs):
            self.assertEqual(calls, ["current"])
            raise RuntimeError("fixture failure during scene cleanup")
        view._mgl_load_scene_assets_current = load
        with self.assertRaises(RuntimeError):
            view._mgl_load_scene_assets([], frame=True)
        self.assertEqual(calls, ["current", "done"])

    def test_bounds_and_framing_use_scaled_translated_joints_not_stale_splats(self):
        view = Renderer()
        view.add_handles("source")
        view.add_handles("target", 0.01, (3, 0, 0))
        # The legacy cache can contain raw centimetres, even though the GPU model uses metres.
        view._mgl_scene_splat_bounds_by_owner["target"] = (np.zeros(3), np.array([0, 200, 0]))
        minimum, maximum = view.get_scene_owner_bounds("target")
        np.testing.assert_allclose(minimum, [3, 0, 0])
        np.testing.assert_allclose(maximum, [3, 2, 0])
        view._mgl_base_center = np.array([0, 100, 0])
        view._mgl_base_zoom = 500
        view._mgl_frame_camera()
        np.testing.assert_allclose(view._mgl_center, [1.5, 1, 0])
        self.assertAlmostEqual(view._mgl_camera_zoom, 1.5 / math.tan(math.radians(30)) * 1.2)
        view._mgl_scene_visibility["source"] = False
        view._mgl_frame_camera()
        np.testing.assert_allclose(view._mgl_center, [3, 1, 0])

    def test_mapping_and_unmapping_defer_gpu_work_to_render_pass(self):
        view = Renderer()
        view._mgl_retarget_refresh_link_item = Mock()
        source = view._mgl_retarget_joint_handles_by_owner["source"][0]
        target = view._mgl_retarget_joint_handles_by_owner["target"][0]
        self.assertTrue(view._mgl_retarget_handle_click(source))
        self.assertTrue(view._mgl_retarget_handle_click(target))
        self.assertEqual(view._mgl_retarget_joint_map, {"root": "root"})
        view._mgl_retarget_refresh_link_item.assert_not_called()
        view._mgl_retarget_refresh_selection_item = Mock()
        view._mgl_retarget_refresh_dynamic_handles()
        view._mgl_retarget_refresh_link_item.assert_called_once()
        view._mgl_retarget_refresh_link_item.reset_mock()
        view._mgl_retarget_remove_joint_link("root", "root")
        view._mgl_retarget_refresh_link_item.assert_not_called()
        self.assertTrue(view._mgl_retarget_links_dirty)

    def test_pose_edit_updates_root_zero_without_gpu_upload_until_paint(self):
        view = Renderer()
        view._mgl_retarget_joint_positions_for_context = Mock(return_value=[(5, 0, 0), (5, 200, 0)])
        view._mgl_retarget_rebuild_handle_mesh_for_owner = Mock()
        view._mgl_retarget_refresh_target_pose_handles()
        root = view._mgl_retarget_joint_handles_by_owner["target"][0]
        self.assertEqual(root["position"], (5.0, 0.0, 0.0))
        view._mgl_retarget_rebuild_handle_mesh_for_owner.assert_not_called()
        self.assertTrue(view._mgl_retarget_selection_dirty)
        view._mgl_retarget_selected_joint = {"role": "target", "name": "root"}
        view._mgl_retarget_refresh_selection_item()
        view._mgl_retarget_rebuild_handle_mesh_for_owner.assert_called_once()

    def test_grid_toggle_does_not_release_resources_in_mouse_event(self):
        view = Renderer()
        resource = Mock()
        view._mgl_scene.add(MGLSceneItem("legacy grid", lambda *args: None,
                                        resources=[resource], tag="grid-model"))
        view._on_mgl_grid_toggled(False)
        resource.release.assert_not_called()
        self.assertTrue(view._mgl_grid_model_clear_pending)
        view._paint_mgl_draw_grid_pass(mvp=np.eye(4))
        resource.release.assert_called_once()
        self.assertFalse(view._mgl_grid_model_clear_pending)


class RetargetGPUResourceTests(unittest.TestCase):
    def test_repeated_mapping_keeps_grid_vao_and_pixels_intact(self):
        if moderngl is None:
            self.skipTest("ModernGL unavailable")
        try:
            ctx = moderngl.create_standalone_context()
        except Exception as exc:
            self.skipTest(f"Offscreen OpenGL unavailable: {exc}")
        self.addCleanup(ctx.release)
        view = Renderer()
        view._mgl_ctx = ctx
        view._mgl_prog = ctx.program(vertex_shader='''#version 330
            in vec3 in_position; in vec3 in_normal; in vec2 in_uv; in vec4 in_color;
            in vec4 in_instance_col0; in vec4 in_instance_col1;
            in vec4 in_instance_col2; in vec4 in_instance_col3;
            out vec4 color;
            void main() {
                gl_Position = mat4(in_instance_col0,in_instance_col1,in_instance_col2,in_instance_col3)*vec4(in_position,1);
                color = in_color + vec4(in_normal*0.0001,0) + vec4(in_uv*0.0001,0,0);
            }''', fragment_shader='''#version 330
            in vec4 color; out vec4 frag; void main() { frag=color; }''')
        view._mgl_wire_prog = ctx.program(vertex_shader='''#version 330
            in vec3 in_pos; in vec3 in_start; in vec3 in_end; in float in_side;
            void main() { gl_Position=vec4(in_pos + (in_start+in_end)*0.0001 + vec3(in_side*0.0001),1); }
            ''', fragment_shader='''#version 330
            out vec4 frag; void main() { frag=vec4(0,1,0,1); }''')
        view._mgl_wire_color = (0, 1, 0, 1)
        grid_program = ctx.program(vertex_shader='''#version 330
            in vec3 in_position; void main() { gl_Position=vec4(in_position,1); }''',
            fragment_shader='''#version 330
            out vec4 frag; void main() { frag=vec4(0.5,0.5,0.5,1); }''')
        grid_data = np.array([[-1,0,0], [1,0,0], [0,-1,0], [0,1,0]], dtype="f4").tobytes()
        grid_buffer = ctx.buffer(grid_data)
        grid_vao = ctx.simple_vertex_array(grid_program, grid_buffer, "in_position")
        view._mgl_grid_vao = grid_vao
        framebuffer = ctx.simple_framebuffer((64, 64))
        def grid_pixels():
            framebuffer.use()
            framebuffer.clear()
            grid_vao.render(mode=moderngl.LINES)
            return framebuffer.read()
        expected = grid_pixels()
        self.assertTrue(any(expected), "Grid must actually draw pixels")
        for owner, role in (("source", "source"), ("target", "target")):
            view._mgl_retarget_rebuild_handle_mesh_for_owner(owner, view._mgl_retarget_joint_handles_by_owner[owner], role)
        view._mgl_set_scene_asset_xform("target", pos=(0.5, 0, 0), scl=(0.01, 0.01, 0.01))
        try:
            for _ in range(30):
                view._mgl_retarget_handle_click(view._mgl_retarget_joint_handles_by_owner["source"][0])
                view._mgl_retarget_handle_click(view._mgl_retarget_joint_handles_by_owner["target"][0])
                view._mgl_retarget_refresh_dynamic_handles()
                self.assertEqual(len(list(view._mgl_scene.iter_by_tag("retarget-handles"))), 2)
                self.assertEqual(len(list(view._mgl_scene.iter_by_tag("retarget-links"))), 1)
                self.assertEqual(grid_buffer.read(), grid_data)
                self.assertEqual(grid_pixels(), expected)
                self.assertEqual(ctx.error, "GL_NO_ERROR")
                view._mgl_retarget_remove_joint_link("root", "root")
                view._mgl_retarget_refresh_dynamic_handles()
        finally:
            view._mgl_scene.clear()
            for resource in (grid_vao, grid_buffer, grid_program, framebuffer, view._mgl_prog, view._mgl_wire_prog,
                             *getattr(view, "_mgl_default_mesh_instance_buffers", ())):
                resource.release()


if __name__ == "__main__":
    unittest.main()
