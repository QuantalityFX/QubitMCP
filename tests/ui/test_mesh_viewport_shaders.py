"""Render real viewport shaders into an offscreen OpenGL framebuffer."""
import unittest
from types import SimpleNamespace

import moderngl
import numpy as np
from pyrr import Matrix44

from echograph.ui.gl_mgl_renderer import MGLRendererMixin
from echograph.ui.gl_shaders import SHADERS
from echograph.ui.gl_scene import MGLScene, MGLSceneItem
from echograph.ui.rig_collider_mesh import draw_solid_rig_colliders, TAG
from echograph.motion.rig_preview import capsule_surfaces


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

    def solid_collider_preview(self):
        renderer=self.renderer
        renderer._mgl_prog,_=self.mesh_program()
        renderer._mgl_scene=MGLScene()
        self.addCleanup(renderer._mgl_scene.clear)
        renderer._rig_colliders_xray=False
        renderer._mgl_texture_override=False
        renderer._mgl_wireframe=False
        renderer._mgl_mesh_color=(1,1,1,1)
        faces,_=capsule_surfaces(np.array([0.,-.35,0.]),np.array([0.,.35,0.]),.3)
        guide=dict(shapes=[dict(kind='collision',faces=faces)],labels={},collisions=True)
        renderer._mgl_scene_skeleton_qt_overlay_data=lambda:dict(owner='Character',rig_guide=guide)
        renderer._mgl_scene_skeleton_display_positions=lambda owner,points:(points,{})
        return renderer

    def test_solid_colliders_have_lit_curvature_and_release_on_xray(self):
        renderer=self.solid_collider_preview()
        mvp=Matrix44.orthogonal_projection(-1,1,-1,1,-1,1,dtype='f4')
        self.fbo.clear(0,0,0,0,depth=1)
        draw_solid_rig_colliders(renderer,mvp)
        pixels=self.pixels()
        body=pixels[:,:,2]>0
        self.assertGreater(body.sum(),1000)
        self.assertTrue(np.all(pixels[:,:,3][body]==255))
        self.assertGreater(np.ptp(pixels[:,:,2][body]),40,'Curved normals must produce varying illumination')
        self.assertTrue(np.all(pixels[:,:,2][body]>pixels[:,:,0][body]))
        depth=np.frombuffer(self.fbo.read(attachment=-1,dtype='f4'),dtype='f4')
        self.assertLess(depth.min(),.5,'Solid colliders must write scene depth')
        item=next(renderer._mgl_scene.iter_by_tag(TAG))
        np.testing.assert_allclose(np.linalg.norm(item.payload['mesh_entry']['normals'],axis=1),1,atol=1e-6)
        self.fbo.clear(0,0,0,0,depth=1)
        draw_solid_rig_colliders(renderer,mvp)
        np.testing.assert_array_equal(self.pixels(),pixels)
        self.assertIs(next(renderer._mgl_scene.iter_by_tag(TAG)),item)
        renderer._rig_colliders_xray=True
        self.fbo.clear(0,0,0,0,depth=1)
        draw_solid_rig_colliders(renderer,mvp)
        self.assertFalse(renderer._mgl_scene.has_tag(TAG))
        self.assertEqual(self.pixels().sum(),0)

    def test_solid_colliders_follow_pose_and_owner_transform_with_hidden_geometry(self):
        renderer=self.solid_collider_preview()
        data=renderer._mgl_scene_skeleton_qt_overlay_data()
        # Outliner collider visibility is independent of the geometry eye.
        renderer._mgl_scene_visibility={'Character':False}
        skeleton=object()
        renderer._scene_rig_collider_overlays={'Character':(skeleton,data['rig_guide'])}
        renderer._mgl_scene_owner_fbx_rig_context=lambda owner:dict(skeleton=skeleton)
        from unittest.mock import patch
        mvp=Matrix44.orthogonal_projection(-1,1,-1,1,-1,1,dtype='f4')
        with patch('echograph.ui.rig_collider_mesh.rig_guide_shapes',return_value=(data['rig_guide']['shapes'],{})):
            self.fbo.clear(0,0,0,0,depth=1)
            draw_solid_rig_colliders(renderer,mvp)
            item=next(renderer._mgl_scene.iter_by_tag(TAG))
            original=item.payload['mesh_entry']['points'].copy()
            # An active preview and outliner listing must not duplicate capsules.
            renderer._mgl_scene_skeleton_qt_overlay_data=lambda:None
            renderer._mgl_scene_skeleton_display_positions=lambda owner,points:(points+[.5,0,0],{})
            self.fbo.clear(0,0,0,0,depth=1)
            draw_solid_rig_colliders(renderer,mvp)
            self.assertIs(next(renderer._mgl_scene.iter_by_tag(TAG)),item)
            np.testing.assert_allclose(item.payload['mesh_entry']['points'],original+[.5,0,0],atol=1e-6)
            self.assertGreater(self.pixels()[64,96,2],0)
            self.assertEqual(self.pixels()[64,64,2],0)
            for face in data['rig_guide']['shapes'][0]['faces']:
                face[:,1]+=.2
            draw_solid_rig_colliders(renderer,mvp)
            np.testing.assert_allclose(item.payload['mesh_entry']['points'],original+[.5,.2,0],atol=1e-6)

    def test_skeleton_lines_remain_visible_over_solid_colliders(self):
        renderer=self.solid_collider_preview()
        renderer._mgl_scene_skeleton_active_owner='Character'
        renderer._mgl_wire_prog=self.resource(self.ctx.program(vertex_shader=SHADERS['wire_vertex'],fragment_shader=SHADERS['wire_fragment']))
        renderer._mgl_wire_color=(1,0,0,1)
        renderer._mgl_wire_edge_width=2
        renderer._mgl_wire_viewport_size=lambda:(128,128)
        renderer._mgl_should_hide_scene_camera_proxy=lambda *args:False
        lines=renderer._mgl_wide_line_vertices([[0,-.5,-.5],[0,.5,-.5]])
        vao=self.resource(self.ctx.vertex_array(renderer._mgl_wire_prog,[(self.resource(self.ctx.buffer(lines.tobytes())),'3f 3f 3f 1f','in_pos','in_start','in_end','in_side')]))
        renderer._mgl_scene.add(MGLSceneItem('joints',MGLRendererMixin._mgl_draw_scene_wire,
            payload=dict(vao=vao,owner='Character',scene_skeleton_overlay=True,mode=moderngl.TRIANGLES,color=(1,0,0,1)),tag='scene-rig-joints'))
        mvp=Matrix44.orthogonal_projection(-1,1,-1,1,-1,1,dtype='f4')
        self.fbo.clear(0,0,0,0,depth=1)
        draw_solid_rig_colliders(renderer,mvp)
        self.assertLess(self.pixels()[64,64,0],self.pixels()[64,64,2])
        renderer._paint_mgl_draw_scene_skeleton_overlay_pass(mvp=mvp)
        pixel=self.pixels()[64,64]
        self.assertGreater(pixel[0],200,'Skeleton lines must show over the solid collider like Qt joint points and axes')
        self.assertLess(pixel[2],20)

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
