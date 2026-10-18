"""Exercise two lights and independent shadows in a real offscreen GL context."""
import unittest
from unittest.mock import Mock
import moderngl
import numpy as np

from echograph.ui.gl_mgl_renderer import MGLRendererMixin
from echograph.ui.gl_scene import MGLScene, MGLSceneItem
from echograph.ui.gl_scene_lighting import SHADOW_DEFAULTS
from echograph.ui.gl_shaders import SHADERS


def light(scene, owner, kind="directional", intensity=.25):
    item = MGLSceneItem(owner, lambda *args: None, tag="scene-light",
                        payload=dict(owner=owner, light=dict(type=kind, intensity=intensity, range=100)))
    scene.add(item)
    return item


class SceneLightingStateTests(unittest.TestCase):
    def test_render_reuses_owner_and_shadow_bounds_and_refreshes_on_the_next_pose(self):
        renderer = MGLRendererMixin()
        renderer._mgl_scene = scene = MGLScene()
        context = dict(skeleton=object(), meshes=[])
        for index in range(4):
            scene.add(MGLSceneItem(f'rig-part-{index}',lambda *args: None,tag='scene-wire',
                                  payload=dict(owner='Rig',fbx_rig_context=context)))
        points = np.array([[1.,2.,3.],[3.,4.,5.]],dtype='f4')
        renderer._mgl_retarget_world_bounds = lambda owner: None
        renderer._mgl_scene_owner_visible = lambda owner: True
        renderer._mgl_scene_owner_fbx_rig_context = lambda owner: context
        renderer._mgl_retarget_joint_positions_for_context = positions = Mock(
            side_effect=lambda *args,**kwargs: points.tolist())
        renderer._mgl_scene_skeleton_display_positions = lambda owner,rows: (rows,None)
        rendered = []
        def paint_frame():
            bounds = renderer._mgl_shadow_scene_bounds()
            for _ in range(4):
                renderer.get_scene_owner_bounds('Rig')
                again = renderer._mgl_shadow_scene_bounds()
                np.testing.assert_array_equal(again[0],bounds[0])
                self.assertEqual(again[1],bounds[1])
            rendered.append(bounds)
        renderer._paint_mgl_frame = paint_frame
        renderer._paint_mgl()
        self.assertEqual(positions.call_count,1)
        np.testing.assert_array_equal(rendered[-1][0],[2.,3.,4.])
        self.assertIsNone(renderer._mgl_scene_bounds_read_cache)
        points += 10.
        renderer._paint_mgl()
        self.assertEqual(positions.call_count,2)
        np.testing.assert_array_equal(rendered[-1][0],[12.,13.,14.])
        self.assertIsNone(renderer._mgl_scene_bounds_read_cache)
        renderer._mgl_scene_visibility = {'Rig':False}
        renderer._paint_mgl()
        np.testing.assert_array_equal(rendered[-1][0],np.zeros(3))
        self.assertEqual(positions.call_count,3)  # Only the explicit owner query needs its bounds.

    def test_render_sequence_hides_light_proxies_in_viewport_capture(self):
        renderer = MGLRendererMixin()
        renderer._mgl_render_output_active = True
        renderer._mgl_scene_owner_kind_by_owner = {"Lamp": "light", "Cube": "mesh"}
        scene = MGLScene()
        drawn = []
        for name, tag, owner in (("guide", "scene-light", "Lamp"),
                                 ("proxy", "scene-volume", "lamp"),
                                 ("mesh", "scene-model", "Cube")):
            scene.add(MGLSceneItem(name, lambda r, item, mvp: drawn.append(item.name),
                                   tag=tag, payload={"owner": owner}))
        scene.draw(renderer, None)
        self.assertEqual(drawn, ["mesh"])
        self.assertTrue(all(item.visible for item in scene.items()))
        renderer._mgl_render_output_active = False
        drawn.clear()
        scene.draw(renderer, None)
        self.assertEqual(drawn, ["guide", "proxy", "mesh"])

    def test_render_excludes_light_guides_but_preserves_lights_and_viewport(self):
        ctx = moderngl.create_standalone_context()
        self.addCleanup(ctx.release)
        renderer = MGLRendererMixin()
        renderer._mgl_ctx = ctx
        renderer._gl = None
        renderer._mgl_wire_color = (1.0, 1.0, 0.0, 1.0)
        renderer._mgl_wire_prog = ctx.program(
            vertex_shader=SHADERS["wire_vertex"], fragment_shader=SHADERS["wire_fragment"])
        self.addCleanup(renderer._mgl_wire_prog.release)
        renderer._mgl_wire_viewport_size = lambda: (32.0, 32.0)
        renderer._mgl_scene = scene = MGLScene()
        self.addCleanup(scene.clear)
        for owner, kind in (("key", "directional"), ("fill", "area")):
            item = renderer._mgl_add_wire_item_from_points(
                owner, renderer._mgl_light_guide_line_points(kind), True, "scene-light", owner)
            item.payload["light"] = dict(type=kind, intensity=2.0)
            scene.add(item)
        shadows = []
        renderer._mgl_render_shadow_map = lambda: shadows.append(renderer._mgl_scene_light_owner)
        identity = np.eye(4, dtype="f4")

        def paint():
            ctx.clear(0, 0, 0, 1)
            renderer._mgl_render_scene_light_shadows()
            scene.draw(renderer, identity)

        renderer._paint_mgl = paint
        image = renderer._mgl_render_to_image(32, 32)
        self.assertIsNotNone(image)
        self.assertTrue(all(image.pixelColor(x, y).red() == 0 for y in range(32) for x in range(32)))
        self.assertEqual(shadows, ["key", "fill"])
        self.assertTrue(all(item.visible for item in scene.items()))
        self.assertEqual(renderer._mgl_scene_light_intensity, 2.0)
        self.assertEqual(renderer._mgl_render_target_fbo_id, 0)
        # The same guides must still draw after capture in the editing viewport.
        fbo = ctx.simple_framebuffer((32, 32), components=4)
        self.addCleanup(fbo.release)
        fbo.use()
        ctx.viewport = (0, 0, 32, 32)
        paint()
        pixels = np.frombuffer(fbo.read(components=4), dtype=np.uint8).reshape(-1, 4)
        self.assertGreater(int(pixels[:, :3].max()), 0)

    def test_area_light_guide_includes_front_arrow(self):
        points = MGLRendererMixin._mgl_light_guide_line_points("area")
        self.assertIsNotNone(points)
        self.assertLess(float(points[:, 2].min()), -0.55)

    def test_area_light_guide_uses_width_and_height(self):
        points = MGLRendererMixin._mgl_light_guide_line_points("area", area_size=(4.0, 2.0))
        self.assertAlmostEqual(float(points[:, 0].max()), 2.0)
        self.assertAlmostEqual(float(points[:, 0].min()), -2.0)
        self.assertAlmostEqual(float(points[:, 1].max()), 1.0)
        self.assertAlmostEqual(float(points[:, 1].min()), -1.0)

    def test_area_shadow_tile_size_uses_higher_quality_setting(self):
        renderer = MGLRendererMixin()
        renderer._mgl_scene_light_type = "area"
        renderer._mgl_shadow_quality = "high"
        renderer._mgl_ctx = type("Ctx", (), {"info": {"GL_MAX_TEXTURE_SIZE": 8192}})()
        self.assertGreaterEqual(renderer._mgl_shadow_tile_size_value(), 2048)
        renderer._mgl_shadow_quality = "ultra"
        self.assertEqual(renderer._mgl_shadow_map_size_value(), 4096)
        renderer._mgl_scene_light_shadow_resolution = 8192
        self.assertEqual(renderer._mgl_shadow_map_size_value(), 8192)

    def test_visibility_and_removal_promote_remaining_light(self):
        renderer = MGLRendererMixin()
        renderer._mgl_scene = scene = MGLScene()
        first = light(scene, "key")
        second = light(scene, "fill", "area")
        owners = []
        renderer._mgl_render_shadow_map = lambda: owners.append(renderer._mgl_scene_light_owner)
        renderer._mgl_render_scene_light_shadows()
        self.assertEqual(owners, ["key", "fill"])
        self.assertEqual(renderer._mgl_scene_light_owner, "key")
        first.visible = False
        renderer._mgl_render_scene_light_shadows()
        self.assertEqual(owners[-1], "fill")
        self.assertIsNone(renderer._mgl_second_light_state)
        second.visible = False
        renderer._mgl_render_scene_light_shadows()
        self.assertEqual(renderer._mgl_scene_light_intensity, 0)
        first.visible = second.visible = True
        renderer._mgl_render_scene_light_shadows()
        self.assertIsNotNone(renderer._mgl_second_light_state)

    def test_secondary_failure_restores_primary_state(self):
        renderer = MGLRendererMixin()
        renderer._mgl_set_light_config("primary", dict(type="spot", intensity=3))
        renderer._mgl_shadow_valid = True
        renderer._mgl_shadow_depth_tex = primary = object()
        renderer._mgl_second_light_state = dict(SHADOW_DEFAULTS, owner="fill", config=dict(type="area"))
        with self.assertRaises(RuntimeError):
            with renderer._mgl_second_light_context():
                self.assertEqual(renderer._mgl_scene_light_owner, "fill")
                self.assertIsNone(renderer._mgl_shadow_depth_tex)
                raise RuntimeError("test")
        self.assertEqual(renderer._mgl_scene_light_owner, "primary")
        self.assertEqual(renderer._mgl_scene_light_intensity, 3)
        self.assertTrue(renderer._mgl_shadow_valid)
        self.assertIs(renderer._mgl_shadow_depth_tex, primary)


class TwoLightShaderTests(unittest.TestCase):
    def setUp(self):
        self.ctx = moderngl.create_standalone_context()
        self.addCleanup(self.ctx.release)
        self.fbo = self.resource(self.ctx.simple_framebuffer((32, 32), components=4))
        self.fbo.use()
        self.ctx.viewport = (0, 0, 32, 32)
        self.program = self.resource(self.ctx.program(vertex_shader=SHADERS["mesh_vertex"], fragment_shader=SHADERS["mesh_fragment"]))
        identity = np.eye(4, dtype="f4").tobytes()
        for name in ("Mvp", "Model", "LightMvp", "LightMvp2"):
            self.program[name].write(identity)
        points = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, -1, 0], [1, 1, 0], [-1, 1, 0]], dtype="f4")
        normals = np.tile([0, 0, 1], (6, 1)).astype("f4")
        self.vao = self.resource(self.ctx.vertex_array(self.program, [
            (self.resource(self.ctx.buffer(points.tobytes())), "3f", "in_position"),
            (self.resource(self.ctx.buffer(normals.tobytes())), "3f", "in_normal"),
        ]))
        self.program["Color"].value = (1, 1, 1, 1)
        self.program["UseLighting"].value = 1
        self.program["ProcLightMix"].value = 1
        self.program["AmbientLight"].value = .1
        for suffix in ("", "2"):
            self.program["LightDir" + suffix].value = (0, 0, 1)
            self.program["LightPos" + suffix].value = (0, 0, 3)
            self.program["LightIntensity" + suffix].value = .25
            self.program["LightRange" + suffix].value = 100
            self.program["AreaAxisU" + suffix].value = (1, 0, 0)
            self.program["AreaAxisV" + suffix].value = (0, 1, 0)
            self.program["AreaSize" + suffix].value = (1, 1)
            if "UseAreaShadowAtlas" + suffix in self.program:
                self.program["UseAreaShadowAtlas" + suffix].value = 0
            self.program["SpotCosInner" + suffix].value = .95
            self.program["SpotCosOuter" + suffix].value = .8
            self.program["ShadowDarkness" + suffix].value = 1
            self.program["ShadowMapSize" + suffix].value = (4, 4)
            self.program["UseSelfShadows" + suffix].value = 1
            for idx in range(5):
                name = f"AreaShadowMvp{idx}{suffix}"
                if name in self.program:
                    self.program[name].write(identity)

    def resource(self, resource):
        self.addCleanup(resource.release)
        return resource

    def sample(self):
        self.fbo.use()
        self.fbo.clear()
        self.vao.render(moderngl.TRIANGLES)
        return np.frombuffer(self.fbo.read(components=4), np.uint8).reshape(32, 32, 4)[16, 16, 0] / 255

    def test_two_light_types_add_and_disabling_restores_single_light(self):
        for kind in range(4):
            with self.subTest(kind=kind):
                self.program["LightType2"].value = kind
                self.program["UseSecondLight"].value = 0
                single = self.sample()
                self.program["UseSecondLight"].value = 1
                both = self.sample()
                self.assertGreater(both, single + .15)
                self.program["UseSecondLight"].value = 0
                self.assertEqual(self.sample(), single)

    def test_shadowing_one_light_does_not_remove_other_light(self):
        self.program["UseSecondLight"].value = 1
        for unit, suffix in ((7, ""), (9, "2")):
            texture = self.resource(self.ctx.texture((4, 4), 1, np.zeros((4, 4), dtype="f4").tobytes(), dtype="f4"))
            texture.use(unit)
            self.program["ShadowMap" + suffix].value = unit
        unshadowed = self.sample()
        self.program["UseShadows"].value = 1
        primary_shadow = self.sample()
        self.program["UseShadows"].value = 0
        self.program["UseShadows2"].value = 1
        secondary_shadow = self.sample()
        self.assertAlmostEqual(primary_shadow, secondary_shadow, places=2)
        self.assertGreater(primary_shadow, .25)
        self.assertLess(primary_shadow, unshadowed - .15)
        self.program["UseShadows"].value = 1
        self.assertAlmostEqual(self.sample(), .1, delta=.01)

    def test_splat_shader_compiles_with_second_light(self):
        program = self.resource(self.ctx.program(vertex_shader=SHADERS["splatq_vertex"], fragment_shader=SHADERS["splatq_fragment"]))
        self.assertIn("LightIntensity2", program)
        self.assertIn("ShadowMap2", program)

    def test_point_light_does_not_stripe_a_sloped_self_shadow_receiver(self):
        renderer = MGLRendererMixin()
        renderer._mgl_ctx = self.ctx
        renderer._mgl_scene = scene = MGLScene()
        light(scene, "point", "point", intensity=.7)
        renderer._mgl_get_scene_asset_xform = lambda owner: dict(pos=(0, 0, 2), rot=(0, 0, 0))
        renderer._mgl_shadow_prog = self.resource(self.ctx.program(
            vertex_shader=SHADERS["shadow_vertex"], fragment_shader=SHADERS["shadow_fragment"]))
        renderer._mgl_shadow_tile_size_value = lambda: 64
        renderer._mgl_shadow_scene_bounds = lambda: (np.zeros(3, dtype="f4"), 2.0)
        renderer._mgl_timeline_frame_index = lambda: 0
        renderer._mgl_bind_default_fbo = self.fbo.use
        renderer._mgl_target_framebuffer_id = lambda: 1
        points = np.array([[-1, -1, -.8], [1, -1, .8], [1, 1, .8],
                           [-1, -1, -.8], [1, 1, .8], [-1, 1, -.8]], dtype="f4")
        normals = np.tile([-.8, 0, 1], (6, 1)).astype("f4")
        vbo = self.resource(self.ctx.buffer(points.tobytes()))
        self.vao = self.resource(self.ctx.vertex_array(self.program, [
            (vbo, "3f", "in_position"),
            (self.resource(self.ctx.buffer(normals.tobytes())), "3f", "in_normal"),
        ]))
        caster = MGLSceneItem("plane", lambda *args: None, tag="scene-model",
                              payload=dict(owner="plane", mesh_entry=dict(vbo=vbo)))
        scene.add(caster)
        self.addCleanup(caster.release)
        renderer._mgl_render_scene_light_shadows()
        self.resource(renderer._mgl_shadow_fbo)
        self.resource(renderer._mgl_shadow_depth_tex)
        renderer._mgl_apply_shadow_uniforms(self.program)
        self.assertEqual(self.program["UsePointShadowAtlas"].value, 1)
        self.assertTrue(renderer._mgl_shadow_valid)
        self.sample()
        shadowed = np.frombuffer(self.fbo.read(components=4), np.uint8).reshape(32, 32, 4).copy()
        self.program["UseShadows"].value = 0
        self.sample()
        reference = np.frombuffer(self.fbo.read(components=4), np.uint8).reshape(32, 32, 4)
        # Crop the outer edge, where PCF legitimately straddles the mesh edge.
        error = np.abs(shadowed[4:-4, 4:-4, 0].astype(float) - reference[4:-4, 4:-4, 0])
        self.assertLess(error.max(), 3, "A flat receiver should not shadow itself in bands")
        # Correcting receiver depth must not hide shadows from another surface.
        blocker_points = np.array([[-.5, -.5, 1], [.5, -.5, 1], [.5, .5, 1],
                                   [-.5, -.5, 1], [.5, .5, 1], [-.5, .5, 1]], dtype="f4")
        blocker = MGLSceneItem("blocker", lambda *args: None, tag="scene-model",
                               payload=dict(owner="blocker", mesh_entry=dict(
                                   vbo=self.resource(self.ctx.buffer(blocker_points.tobytes())))))
        scene.add(blocker)
        self.addCleanup(blocker.release)
        renderer._mgl_shadow_dirty = True
        renderer._mgl_render_scene_light_shadows()
        renderer._mgl_apply_shadow_uniforms(self.program)
        self.assertLess(self.sample(), reference[16, 16, 0] / 255 - .2)

    def test_renderer_binds_distinct_maps_and_live_transforms(self):
        renderer = MGLRendererMixin()
        renderer._mgl_ctx = self.ctx
        renderer._mgl_scene = scene = MGLScene()
        light(scene, "key")
        light(scene, "fill", "area")
        positions = {"key": (0, 0, 2), "fill": (2, 0, 3)}
        renderer._mgl_get_scene_asset_xform = lambda owner: dict(pos=positions[owner], rot=(0, 0, 0))
        renderer._mgl_shadow_prog = self.resource(self.ctx.program(vertex_shader=SHADERS["shadow_vertex"], fragment_shader=SHADERS["shadow_fragment"]))
        # Use tiny maps here: production light types select their own sizes.
        renderer._mgl_shadow_texture_dimensions = lambda: (16, 16)
        def render_map():
            self.assertTrue(renderer._mgl_ensure_shadow_resources())
            renderer._mgl_shadow_valid = True
            renderer._mgl_shadow_light_mvp = np.eye(4, dtype="f4")
        renderer._mgl_render_shadow_map = render_map
        renderer._mgl_render_scene_light_shadows()
        self.addCleanup(renderer._mgl_release_second_light)
        self.resource(renderer._mgl_shadow_fbo)
        self.resource(renderer._mgl_shadow_depth_tex)
        secondary = renderer._mgl_second_light_state["depth_tex"]
        self.assertIsNot(secondary, renderer._mgl_shadow_depth_tex)
        renderer._mgl_apply_shadow_uniforms(self.program)
        self.assertEqual(self.program["ShadowMap"].value, 7)
        self.assertEqual(self.program["ShadowMap2"].value, 9)
        self.assertEqual(self.program["LightPos2"].value, positions["fill"])
        positions["fill"] = (4, 5, 6)
        renderer._mgl_apply_shadow_uniforms(self.program)
        self.assertEqual(self.program["LightPos2"].value, (4, 5, 6))
        renderer._mgl_work_light_enabled = True
        renderer._mgl_apply_shadow_uniforms(self.program)
        self.assertEqual(self.program["UseSecondLight"].value, 0)

    def test_two_real_shadow_passes_render_and_reuse_unchanged_maps(self):
        renderer = MGLRendererMixin()
        renderer._mgl_ctx = self.ctx
        renderer._mgl_scene = scene = MGLScene()
        light(scene, "key")
        light(scene, "fill", "area")
        renderer._mgl_get_scene_asset_xform = lambda owner: dict(
            pos=(0 if owner == "key" else 1, 0, 3), rot=(0, 0, 0))
        renderer._mgl_shadow_prog = self.resource(self.ctx.program(
            vertex_shader=SHADERS["shadow_vertex"], fragment_shader=SHADERS["shadow_fragment"]))
        renderer._mgl_shadow_tile_size_value = lambda: 32
        renderer._mgl_shadow_texture_dimensions = lambda: (96, 64)
        renderer._mgl_shadow_scene_bounds = lambda: (np.zeros(3, dtype="f4"), 1.5)
        renderer._mgl_timeline_frame_index = lambda: 0
        renderer._mgl_bind_default_fbo = self.fbo.use
        renderer._mgl_target_framebuffer_id = lambda: 1
        points = np.array([[-.8, -.8, .5], [.8, -.8, .5], [0, .8, .5]], dtype="f4")
        vbo = self.resource(self.ctx.buffer(points.tobytes()))
        caster = MGLSceneItem("caster", lambda *args: None, tag="scene-model",
                              payload=dict(owner="caster", mesh_entry=dict(vbo=vbo)))
        scene.add(caster)
        self.addCleanup(caster.release)
        renderer._mgl_render_scene_light_shadows()
        self.addCleanup(renderer._mgl_release_second_light)
        self.resource(renderer._mgl_shadow_fbo)
        self.resource(renderer._mgl_shadow_depth_tex)
        self.assertTrue(renderer._mgl_shadow_valid)
        state = renderer._mgl_second_light_state
        self.assertTrue(state["valid"])
        first = np.frombuffer(renderer._mgl_shadow_depth_tex.read(), dtype="f4")
        second = np.frombuffer(state["depth_tex"].read(), dtype="f4")
        self.assertTrue(np.any(first < .99))
        self.assertTrue(np.any(second < .99))
        self.assertFalse(np.array_equal(first, second))
        timestamps = (renderer._mgl_shadow_last_update_ts, state["last_update_ts"])
        renderer._mgl_render_scene_light_shadows()
        self.assertEqual(timestamps, (renderer._mgl_shadow_last_update_ts, state["last_update_ts"]))
