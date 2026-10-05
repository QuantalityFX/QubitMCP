"""Render real viewport shaders into an offscreen OpenGL framebuffer."""
import unittest
from types import SimpleNamespace

import moderngl
import numpy as np
from pyrr import Matrix44

from echograph.ui.gl_mgl_renderer import MGLRendererMixin
from echograph.ui.gl_shaders import SHADERS


class MeshViewportShaderTests(unittest.TestCase):
    def setUp(self):
        self.ctx=moderngl.create_standalone_context()
        self.addCleanup(self.ctx.release)
        self.fbo=self.ctx.simple_framebuffer((128,128),components=4)
        self.addCleanup(self.fbo.release)
        self.fbo.use()
        self.ctx.viewport=(0,0,128,128)
        self.ctx.enable(moderngl.BLEND | moderngl.DEPTH_TEST)
        self.ctx.blend_func=(moderngl.SRC_ALPHA,moderngl.ONE_MINUS_SRC_ALPHA)
        self.renderer=MGLRendererMixin()
        self.renderer._mgl_ctx=self.ctx
        self.renderer._mgl_cull_enabled=False

    def resource(self, resource):
        self.addCleanup(resource.release)
        return resource

    def pixels(self):
        return np.frombuffer(self.fbo.read(components=4),dtype=np.uint8).reshape(128,128,4)

    def test_adaptive_wire_thins_and_fades_as_projected_edges_shrink(self):
        for instanced in (False,True):
            program=self.resource(self.ctx.program(vertex_shader=SHADERS['wire_instanced_vertex' if instanced else 'wire_vertex'],fragment_shader=SHADERS['wire_fragment']))
            if instanced:
                corners=np.array([[0,-1],[0,1],[1,1],[0,-1],[1,1],[1,-1]],dtype='f4')
                segments=np.array([[-.8,0,0,.8,0,0]],dtype='f4')
                vao=self.resource(self.ctx.vertex_array(program,[(self.resource(self.ctx.buffer(corners.tobytes())),'2f','in_corner'),
                    (self.resource(self.ctx.buffer(segments.tobytes())),'3f 3f/i','in_start','in_end')]))
            else:
                data=self.renderer._mgl_wide_line_vertices([[-.8,0,0],[.8,0,0]])
                vao=self.resource(self.ctx.vertex_array(program,[(self.resource(self.ctx.buffer(data.tobytes())),'3f 3f 3f 1f','in_pos','in_start','in_end','in_side')]))
            program['Color'].value=(1,1,1,1)
            program['Viewport'].value=(128,128)
            program['LineWidth'].value=2
            program['AdaptiveWidth'].value=1
            measured=[]
            for zoom in (1.,.25,.0625):
                matrix=Matrix44.from_scale([zoom,zoom,1],dtype='f4')
                program['Mvp'].write(matrix.tobytes())
                self.fbo.clear(0,0,0,0,depth=1)
                vao.render(moderngl.TRIANGLES)
                # Integrated brightness / line length measures effective thickness.
                measured.append(self.pixels()[:,:,0].sum()/255/(102.4*zoom))
            self.assertGreater(measured[0],measured[1])
            self.assertGreater(measured[1],measured[2])
            self.assertGreater(measured[2],0)

    def mesh_program(self):
        program=self.resource(self.ctx.program(vertex_shader=SHADERS['mesh_vertex'],fragment_shader=SHADERS['mesh_fragment']))
        program['Mvp'].write(Matrix44.identity(dtype='f4').tobytes())
        program['Model'].write(Matrix44.identity(dtype='f4').tobytes())
        program['Color'].value=(1,1,1,1)
        points=np.array([[-1,-1,0],[1,-1,0],[1,1,0],[-1,-1,0],[1,1,0],[-1,1,0]],dtype='f4')
        vao=self.resource(self.ctx.vertex_array(program,[(self.resource(self.ctx.buffer(points.tobytes())),'3f','in_position')]))
        return program,vao

    def test_xray_ghost_shader_is_translucent_and_resets_after_mesh_draw(self):
        program,vao=self.mesh_program()
        renderer=self.renderer
        renderer._mgl_prog=program
        renderer._mgl_draw_scene_mesh_surface=lambda *args:vao.render(moderngl.TRIANGLES)
        samples=[]
        for xray in (False,True,False):
            renderer._mgl_wireframe_xray=xray
            self.fbo.clear(0,0,0,0,depth=1)
            renderer._mgl_draw_scene_mesh(SimpleNamespace(tag='scene-model'),Matrix44.identity(dtype='f4'))
            samples.append(self.pixels()[64,64,:3].copy())
            self.assertEqual(program['GhostMode'].value,0)
        self.assertTrue(np.all(samples[1]<samples[0]*.5))
        self.assertGreater(samples[1].sum(),0)
        np.testing.assert_array_equal(samples[0],samples[2])

    def test_back_points_are_depth_hidden_in_solid_and_visible_in_xray(self):
        mesh,vao=self.mesh_program()
        renderer=self.renderer
        renderer._mgl_overlay_point_prog=self.resource(self.ctx.program(vertex_shader=SHADERS['overlay_point_vertex'],fragment_shader=SHADERS['overlay_point_fragment']))
        renderer._mgl_mesh_topology_for_owner=lambda owner:dict(points=np.array([[0,0,.5]],dtype='f4'))
        renderer._mgl_overlay_mvp_for_owner=lambda *args:(Matrix44.identity(dtype='f4'),None)
        samples=[]
        for xray in (False,True,False):
            renderer._mgl_wireframe_xray=xray
            self.fbo.clear(0,0,0,0,depth=1)
            mesh['Color'].value=(0,0,0,1)
            vao.render(moderngl.TRIANGLES)
            self.assertTrue(renderer._mgl_draw_mesh_overlay_points('mesh',[0],(0,1,1,1),Matrix44.identity(dtype='f4'),8))
            samples.append(self.pixels()[:,:,:3].sum())
        self.assertEqual(samples[0],0)
        self.assertGreater(samples[1],0)
        self.assertEqual(samples[2],0)

    def test_back_wire_edges_are_only_visible_in_xray(self):
        mesh,plane=self.mesh_program()
        renderer=self.renderer
        renderer._mgl_wire_prog=self.resource(self.ctx.program(vertex_shader=SHADERS['wire_vertex'],fragment_shader=SHADERS['wire_fragment']))
        renderer._mgl_wire_color=(1,1,1,1)
        renderer._mgl_wire_edge_width=2
        renderer._mgl_wire_viewport_size=lambda:(128,128)
        renderer._mgl_should_hide_scene_camera_proxy=lambda *args:False
        lines=renderer._mgl_wide_line_vertices([[-.8,0,.5],[.8,0,.5]])
        vao=self.resource(self.ctx.vertex_array(renderer._mgl_wire_prog,[(self.resource(self.ctx.buffer(lines.tobytes())),'3f 3f 3f 1f','in_pos','in_start','in_end','in_side')]))
        item=SimpleNamespace(tag='model-wire',payload=dict(vao=vao,mode=moderngl.TRIANGLES),name='mesh')
        samples=[]
        for xray in (False,True,False):
            renderer._mgl_wireframe_xray=xray
            self.fbo.clear(0,0,0,0,depth=1)
            mesh['Color'].value=(0,0,0,1)
            plane.render(moderngl.TRIANGLES)
            renderer._mgl_draw_scene_wire(item,Matrix44.identity(dtype='f4'))
            samples.append(self.pixels()[:,:,:3].sum())
            self.assertEqual(renderer._mgl_wire_prog['DepthBias'].value,0)
        self.assertEqual(samples[0],0)
        self.assertGreater(samples[1],0)
        self.assertEqual(samples[2],0)

    def test_selected_edge_covers_wire_center_in_current_pose_and_owner_transform(self):
        mesh,plane=self.mesh_program()
        renderer=self.renderer
        renderer._mgl_wire_prog=self.resource(self.ctx.program(vertex_shader=SHADERS['wire_vertex'],fragment_shader=SHADERS['wire_fragment']))
        renderer._mgl_wire_color=(.6,.6,.6,1)
        renderer._mgl_wire_edge_width=2
        renderer._mgl_wire_viewport_size=lambda:(128,128)
        renderer._mgl_should_hide_scene_camera_proxy=lambda *args:False
        model=Matrix44.from_translation([.1,.25,0],dtype='f4')
        renderer._mgl_scene_model_matrix_for_owner=lambda *args:model
        # These are the current deformed positions, shared by skin wires and selection.
        points=np.array([[-.6,-.125,0],[.6,.125,0]],dtype='f4')
        renderer._mgl_mesh_topology_for_owner=lambda owner:dict(points=points)
        lines=renderer._mgl_wide_line_vertices(points)
        vao=self.resource(self.ctx.vertex_array(renderer._mgl_wire_prog,[(self.resource(self.ctx.buffer(lines.tobytes())),'3f 3f 3f 1f','in_pos','in_start','in_end','in_side')]))
        item=SimpleNamespace(tag='scene-wire',payload=dict(vao=vao,mode=moderngl.TRIANGLES,model=model),name='mesh')
        entry=renderer._mgl_build_cached_mesh_selection_edges('mesh',[(0,1)])
        for resource in entry['resources']: self.resource(resource)
        for cached in (False,True):
            self.fbo.clear(0,0,0,0,depth=1)
            mesh['Color'].value=(0,0,0,1)
            plane.render(moderngl.TRIANGLES)
            surface_depth = self.fbo.read(attachment=-1, dtype='f4')
            renderer._mgl_draw_scene_wire(item,Matrix44.identity(dtype='f4'))
            wire_mask=self.pixels()[:,:,0]>50
            self.assertTrue(np.any(wire_mask))
            self.assertEqual(self.fbo.read(attachment=-1, dtype='f4'), surface_depth,
                             'Wire overlays must not become depth occluders')
            if cached:
                renderer._mgl_draw_cached_mesh_selection_lines([entry],(1,.4,0,1),Matrix44.identity(dtype='f4'),5)
            else:
                renderer._mgl_draw_mesh_overlay_lines('mesh',points,(1,.4,0,1),Matrix44.identity(dtype='f4'),5)
            pixels=self.pixels()
            self.assertTrue(np.all(pixels[:,:,0][wire_mask]>pixels[:,:,1][wire_mask]*1.5),'Gray wire must not split the selected orange edge')
            self.assertEqual(renderer._mgl_wire_prog['DepthBias'].value,0)
            self.assertEqual(self.fbo.read(attachment=-1, dtype='f4'), surface_depth)
            self.assertTrue(self.fbo.depth_mask)


if __name__ == '__main__':
    unittest.main()
