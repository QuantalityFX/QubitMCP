import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PySide6 import QtCore, QtGui

from echograph.ui.gl_mgl_renderer import MGLRendererMixin
from echograph.ui.gl_view import GraphGLView
from echograph.ui.gl_view_mouse import _handle_mouse_double_click_moderngl, mousePressEvent
from echograph.ui.mesh_polygons import edge_loop, polygon_topology, update_polygon_preview, component_selection
from echograph.ui.mesh_visibility import visible_points


def quad_grid(width=4, height=4):
    polygons = [[y*(width+1)+x, y*(width+1)+x+1, (y+1)*(width+1)+x+1, (y+1)*(width+1)+x]
                for y in range(height) for x in range(width)]
    triangles = np.asarray([t for a,b,c,d in polygons for t in ((a,b,c),(a,c,d))])
    topo = polygon_topology(triangles, list(range((width+1)*(height+1))), np.repeat(np.arange(len(polygons)),2))
    return dict(topo, triangles=triangles)


class MeshViewportModeTests(unittest.TestCase):
    def test_disconnected_point_or_edge_anchor_still_previews_hovered_component(self):
        topo = dict(edges=np.array([[0, 1], [2, 3]]), polygons=[])
        for mode, field, start, end in (('point', 'vertex_index', 0, 2),
                                        ('edge', 'edge', (0, 1), (2, 3))):
            for loop in (False, True):
                anchor = dict(mode=mode, owner='mesh', **{field: start})
                target = dict(mode=mode, owner='mesh', **{field: end})
                result = component_selection(topo, anchor, target, loop=loop)
                self.assertEqual([element[field] for element in result], [end])

    def test_edge_pick_margin_is_small_and_scales_with_display_density(self):
        renderer = MGLRendererMixin()
        renderer.width = lambda: 100
        renderer._mgl_scene_owner_visible = lambda owner: True
        renderer._mgl_mesh_topology_for_owner = lambda owner: dict(edges=np.array([[0, 1], [2, 3], [-1, 4]]))
        for dpr in (1, 2):
            renderer._mgl_project_owner_points_device = lambda *args: dict(
                valid=np.ones(4, dtype=bool), x=np.array([10, 90, 10, 90])*dpr,
                y=np.array([40, 40, 70, 70])*dpr, z=np.zeros(4))
            hit = renderer._mgl_pick_projected_edge_for_owners(['mesh'], 50*dpr, 51*dpr, 100*dpr, 100*dpr)
            self.assertEqual(hit['edge'], (0, 1))
            self.assertAlmostEqual(hit['screen_distance'], 11*dpr)
            self.assertIsNone(renderer._mgl_pick_projected_edge_for_owners(
                ['mesh'], 50*dpr, 55*dpr, 100*dpr, 100*dpr))
        renderer._mgl_pick_ray = lambda *args: (np.zeros(3), np.ones(3))
        renderer._mgl_scene_mesh_topology_by_owner = {'mesh': {}}
        renderer._mgl_pick_triangles_for_owner = Mock(side_effect=AssertionError('Must not pick distant triangle diagonals'))
        self.assertIsNone(renderer.pick_mesh_element_at(1000, 1000, 100, 100, mode='edge'))

    def test_remapped_gestures_handle_modifiers_repeat_and_release(self):
        from echograph.ui import hotkeys_config
        from echograph.ui.mesh_polygons import handle_component_gesture_key, component_gesture_operation
        view = SimpleNamespace(_mesh_select_mode='point', update=Mock(),
            _mgl_mesh_topology_for_owner=lambda owner: quad_grid(),
            _mesh_polygon_hover_target=dict(mode='point', owner='mesh', vertex_index=16),
            _mesh_polygon_anchor=dict(mode='point', owner='mesh', vertex_index=6))
        mapping = dict(hotkeys_config.DEFAULT_KEYMAP, gl_mesh_select_path_loop='Shift+Q',
                       gl_mesh_deselect_path_loop='Ctrl+Q')
        with patch.object(hotkeys_config, '_KEYMAP_CACHE', mapping):
            def key(key, modifiers, pressed=True, repeat=False):
                event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress if pressed else QtCore.QEvent.KeyRelease,
                                       key, modifiers, '', repeat)
                return handle_component_gesture_key(view, event, pressed=pressed)
            self.assertFalse(key(QtCore.Qt.Key_A, QtCore.Qt.ControlModifier))
            self.assertTrue(key(QtCore.Qt.Key_Q, QtCore.Qt.NoModifier))
            self.assertEqual(view._mesh_polygon_preview, {})
            key(QtCore.Qt.Key_Control, QtCore.Qt.ControlModifier)
            self.assertEqual(view._mesh_polygon_preview_operation, 'subtract')
            preview = view._mesh_polygon_preview
            key(QtCore.Qt.Key_Q, QtCore.Qt.ControlModifier, pressed=False, repeat=True)
            self.assertEqual(view._mesh_polygon_preview, preview)
            key(QtCore.Qt.Key_Control, QtCore.Qt.NoModifier, pressed=False)
            self.assertEqual(view._mesh_polygon_preview, {})
            key(QtCore.Qt.Key_Shift, QtCore.Qt.ShiftModifier)
            self.assertEqual(view._mesh_polygon_preview_operation, 'add')
            self.assertEqual(component_gesture_operation(view, QtCore.Qt.ShiftModifier), 'add')
            key(QtCore.Qt.Key_Q, QtCore.Qt.ShiftModifier, pressed=False)
            self.assertEqual(view._mesh_polygon_preview, {})

    def test_xray_uses_configured_hotkey_and_leaves_h_for_normal_wireframe(self):
        from echograph.ui import hotkeys_config
        view = SimpleNamespace(_toggle_mgl_xray=Mock(), _on_mgl_wireframe_toggled=Mock())
        mapping = dict(hotkeys_config.DEFAULT_KEYMAP, gl_xray_toggle='Ctrl+J')
        with patch.object(hotkeys_config, '_KEYMAP_CACHE', mapping):
            for key, mods, expected in ((QtCore.Qt.Key_H, QtCore.Qt.ShiftModifier, False),
                                         (QtCore.Qt.Key_J, QtCore.Qt.ControlModifier, True),
                                         (QtCore.Qt.Key_H, QtCore.Qt.NoModifier, True)):
                event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, mods)
                self.assertEqual(GraphGLView._handle_viewport_hotkeys(view, event, require_no_text_focus=False), expected)
        view._toggle_mgl_xray.assert_called_once()
        view._on_mgl_wireframe_toggled.assert_called_once_with(True)

    def test_point_and_edge_paths_extend_the_same_loop_and_preview_matches_click(self):
        topo = quad_grid()
        for mode, anchor_value, target_value, field, expected_path, expected_loop in (
            ('point',6,16,'vertex_index',{6,11,16},{1,6,11,16,21}),
            ('edge',(6,11),(11,16),'edge',{(6,11),(11,16)},{(1,6),(6,11),(11,16),(16,21)}),
        ):
            anchor = dict(mode=mode,owner='mesh',**{field:anchor_value})
            target = dict(mode=mode,owner='mesh',**{field:target_value})
            view = SimpleNamespace(_mesh_select_mode=mode,_mesh_polygon_a_held=True,
                _mgl_mesh_topology_for_owner=lambda owner:topo,update=Mock(),
                _handle_mouse_release_moderngl_click_pick_viewport=lambda **kw:(50,50,100,100),
                pick_mesh_element_at=Mock(return_value=target),_set_mesh_element_hover=Mock(),
                _set_mesh_element_selection_many=Mock())
            for button,key,expected in ((QtCore.Qt.LeftButton,'path',expected_path),(QtCore.Qt.MiddleButton,'loop',expected_loop)):
                view._mesh_polygon_anchor=anchor
                update_polygon_preview(view,target,True)
                preview=view._mesh_polygon_preview[key]
                self.assertEqual({e[field] for e in preview},expected)
                event=SimpleNamespace(button=lambda:button,modifiers=lambda:QtCore.Qt.ShiftModifier,accept=Mock())
                mousePressEvent(view,event)
                self.assertEqual(view._set_mesh_element_selection_many.call_args.args[0],preview)
                self.assertEqual(view.pick_mesh_element_at.call_args.kwargs['mode'],mode)
                self.assertEqual(view._mesh_polygon_anchor,target)

    def test_point_path_uses_authored_vertex_identity_across_duplicate_render_vertices(self):
        from tests.ui.test_mesh_polygons import topology, strip
        topo=topology(strip(4))
        # Vertex 1 in the second triangle is a duplicate of the preceding corner.
        mapping=topo['vertex_representatives']
        duplicate=next(i for i,v in enumerate(mapping) if i != v)
        end=int(topo['point_indices'][-1])
        anchor=dict(mode='point',owner='mesh',vertex_index=duplicate)
        target=dict(mode='point',owner='mesh',vertex_index=end)
        path=component_selection(topo,anchor,target)
        self.assertGreater(len(path),1)
        self.assertEqual(path[0]['vertex_index'],mapping[duplicate])
        self.assertEqual(path[-1]['vertex_index'],end)

    def test_shift_h_is_distinct_from_normal_wireframe_and_ignores_repeat(self):
        view=SimpleNamespace(_use_moderngl=True,_toggle_mgl_xray=Mock())
        for repeat in (False,True):
            event=SimpleNamespace(key=lambda:QtCore.Qt.Key_H,modifiers=lambda:QtCore.Qt.ShiftModifier,
                                  isAutoRepeat=lambda:repeat,accept=Mock())
            self.assertTrue(GraphGLView._handle_viewport_hotkeys(view,event,require_no_text_focus=False))
        view._toggle_mgl_xray.assert_called_once()

    def test_edge_loop_crosses_regular_vertices_and_stops_at_boundary(self):
        topo = quad_grid()
        self.assertEqual(set(edge_loop(topo,(6,11))), {(1,6),(6,11),(11,16),(16,21)})
        self.assertEqual(len(edge_loop(topo,(0,1))),16)  # Closed border, no repeated edges.

    def test_closed_edge_loop_and_nonmanifold_stop(self):
        polygons = [[y*8+x,y*8+(x+1)%8,(y+1)*8+(x+1)%8,(y+1)*8+x] for y in range(2) for x in range(8)]
        topo = dict(polygons=polygons)
        self.assertEqual(set(edge_loop(topo,(8,9))), {tuple(sorted((8+x,8+(x+1)%8))) for x in range(8)})
        topo['polygons'] += [[8,9,30],[8,9,31]]
        self.assertEqual(edge_loop(topo,(8,9)), [(8,9)])

    def test_double_click_commits_loop_and_preserves_selection_modifiers(self):
        for mods, operation in ((QtCore.Qt.NoModifier,'replace'),(QtCore.Qt.ShiftModifier,'add')):
            view = SimpleNamespace(_use_moderngl=True,_mesh_select_mode='edge',
                _handle_mouse_release_moderngl_click_pick_viewport=lambda **kw:(50,50,100,100),
                pick_mesh_element_at=lambda *a,**kw:dict(mode='edge',owner='mesh',edge=(6,11)),
                _mgl_mesh_topology_for_owner=lambda owner:quad_grid(),
                _set_mesh_element_selection_many=Mock(),_set_mesh_element_hover=Mock(),update=Mock())
            event = SimpleNamespace(button=lambda:QtCore.Qt.LeftButton,modifiers=lambda:mods,accept=Mock())
            self.assertTrue(_handle_mouse_double_click_moderngl(view,event))
            call = view._set_mesh_element_selection_many.call_args
            self.assertEqual({e['edge'] for e in call.args[0]}, {(1,6),(6,11),(11,16),(16,21)})
            self.assertEqual(call.kwargs['selection_op'],operation)

    def test_polygon_preview_matches_each_click_without_changing_selection(self):
        topo = quad_grid(5,1)
        target = dict(mode='face',owner='mesh',face_index=4)
        view = SimpleNamespace(_mesh_select_mode='face',_mesh_polygon_a_held=True,
            _mesh_polygon_anchor=dict(mode='face',owner='mesh',face_index=2),
            _mgl_mesh_topology_for_owner=lambda owner:topo,update=Mock(),
            _handle_mouse_release_moderngl_click_pick_viewport=lambda **kw:(50,50,100,100),
            pick_mesh_element_at=lambda *a,**kw:target,_set_mesh_element_hover=Mock(),
            _set_mesh_element_selection_many=Mock())
        for button,key in ((QtCore.Qt.LeftButton,'path'),(QtCore.Qt.MiddleButton,'loop')):
            view._mesh_polygon_anchor=dict(mode='face',owner='mesh',face_index=2)
            update_polygon_preview(view,target,True)
            preview = view._mesh_polygon_preview[key]
            event = SimpleNamespace(button=lambda:button,modifiers=lambda:QtCore.Qt.ShiftModifier,accept=Mock())
            mousePressEvent(view,event)
            self.assertEqual(view._set_mesh_element_selection_many.call_args.args[0],preview)
        update_polygon_preview(view,target,False)
        self.assertEqual(view._mesh_polygon_preview,{})

    def point_renderer(self):
        renderer = MGLRendererMixin()
        points = np.array([[10,10,0],[90,10,0],[90,90,0],[10,90,0],[50,50,.5],[55,50,-.01]])
        topo = dict(points=points,triangles=np.array([[0,1,2],[0,2,3]]))
        renderer._mgl_scene_owner_visible=lambda owner:True
        renderer._mgl_mesh_topology_for_owner=lambda owner:topo
        renderer._mgl_project_owner_points_device=lambda *args:dict(x=points[:,0],y=points[:,1],z=points[:,2],valid=np.ones(6,dtype=bool))
        renderer._mgl_mesh_selection_owners=lambda:['mesh']
        renderer.width=lambda:100
        return renderer

    def test_solid_point_pick_skips_closer_hidden_point_but_xray_can_pick_it(self):
        renderer = self.point_renderer()
        for xray,index in ((False,5),(True,4),(False,5)):
            renderer._mgl_wireframe_xray=xray
            hit=renderer._mgl_pick_projected_point_for_owners(['mesh'],50,50,100,100)
            self.assertEqual(hit['vertex_index'],index)

    def test_box_selection_includes_back_points_in_both_modes(self):
        renderer = self.point_renderer()
        for xray in (False,True):
            renderer._mgl_wireframe_xray=xray
            hits=renderer.pick_mesh_elements_in_rect(40,40,60,60,100,100,mode='point')
            self.assertEqual({h['vertex_index'] for h in hits},{4,5})

    def test_surface_occlusion_includes_other_objects_and_surface_points_remain_pickable(self):
        surface=np.array([[[-1,-1,0],[1,-1,0],[0,1,0]]])
        np.testing.assert_array_equal(visible_points([[0,0,.5],[0,0,0],[0,0,-.5],[2,2,.5]],[surface]),[False,True,True,True])


if __name__ == '__main__':
    unittest.main()
