import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PySide6 import QtCore

from echograph.ui.gl_mgl_renderer import MGLRendererMixin
from echograph.ui.gl_view import GraphGLView
from echograph.ui.gl_view_mouse import mousePressEvent, _handle_mouse_double_click_moderngl, _mesh_connected_selection_elements
from echograph.ui.mesh_polygons import polygon_path, expand_face_elements


def topology(faces):
    ids, polygons = [], []
    for i, face in enumerate(faces):
        for j in range(1, len(face) - 1):
            ids.extend([face[0], face[j], face[j + 1]])
            polygons.append(i)
    ids = np.asarray(ids)
    points = np.asarray([(i % 10, i // 10, 0) for i in ids], dtype='f4')
    entry = dict(points=points, topology_mesh='mesh', vertex_ids=ids, triangle_polygons=polygons)
    renderer = object.__new__(MGLRendererMixin)
    return renderer._mgl_mesh_topology_from_entries([entry])


def strip(count):
    return [[i, i + 1, i + 11, i + 10] for i in range(count)]


class PolygonTests(unittest.TestCase):
    def test_double_click_selects_connected_polygons_with_expanded_render_vertices(self):
        topo = topology(strip(3) + [[6, 7, 17, 16]])
        view = SimpleNamespace(_use_moderngl=True, _mesh_select_mode='face',
            _handle_mouse_release_moderngl_click_pick_viewport=lambda **kwargs: (10, 10, 100, 100),
            pick_mesh_element_at=lambda *args, **kwargs: dict(mode='face', owner='mesh', face_index=3),
            _mgl_mesh_topology_for_owner=lambda owner: topo, _set_mesh_element_selection_many=Mock(),
            _set_mesh_element_hover=Mock(), update=Mock())
        view._mesh_connected_selection_elements = lambda elem, **kwargs: _mesh_connected_selection_elements(view, elem, **kwargs)
        for mods, operation in ((QtCore.Qt.NoModifier, 'replace'), (QtCore.Qt.ShiftModifier, 'add'),
                                (QtCore.Qt.ControlModifier, 'subtract')):
            event = SimpleNamespace(button=lambda: QtCore.Qt.LeftButton, modifiers=lambda: mods, accept=Mock())
            self.assertTrue(_handle_mouse_double_click_moderngl(view, event))
            args = view._set_mesh_element_selection_many.call_args
            self.assertEqual([elem['face_index'] for elem in args.args[0]], list(range(6)))
            self.assertEqual(args.kwargs['selection_op'], operation)
            self.assertIsNone(view._mgl_pick_press_pos)
            event.accept.assert_called_once()

    def test_double_click_triangles_stops_at_disconnected_island(self):
        topo = topology([[0, 1, 11], [0, 11, 10], [5, 6, 16]])
        view = SimpleNamespace(_mgl_mesh_topology_for_owner=lambda owner: topo)
        selected = _mesh_connected_selection_elements(view,
            dict(mode='face', owner='mesh', face_index=1), mode='face')
        self.assertEqual([elem['face_index'] for elem in selected], [0, 1])

    def test_quad_and_ngon_preserve_boundary_and_expand_selection(self):
        topo = topology([[0, 1, 11, 10], [1, 2, 3, 13, 12, 11]])
        self.assertEqual([len(p) for p in topo['polygons']], [4, 6])
        self.assertEqual(len(topo['edges']), 9)
        elem = dict(mode='face', owner='mesh', face_index=3)
        self.assertEqual(len(expand_face_elements(topo, elem)), 4)
        self.assertEqual(len(topo['point_indices']), 8)

    def test_authored_triangles_remain_separate(self):
        topo = topology([[0, 1, 11], [0, 11, 10]])
        self.assertEqual(len(topo['polygons']), 2)
        self.assertEqual(len(topo['edges']), 5)
        self.assertEqual(len(expand_face_elements(topo, dict(mode='face', face_index=0))), 1)

    def test_path_includes_both_ends(self):
        topo = topology(strip(6))
        self.assertEqual(polygon_path(topo, 2, 7), list(range(2, 8)))

    def test_loop_extends_both_ends_without_branching(self):
        topo = topology(strip(6) + [[11, 12, 22, 21]])
        self.assertEqual(polygon_path(topo, 4, 6, loop=True), list(range(12)))

    def test_loop_stops_at_ngon(self):
        topo = topology(strip(3) + [[3, 4, 5, 15, 14, 13], [5, 6, 16, 15]])
        self.assertEqual(polygon_path(topo, 0, 2, loop=True), list(range(10)))

    def test_disconnected_islands_have_no_path(self):
        self.assertEqual(polygon_path(topology([[0, 1, 11, 10], [5, 6, 16, 15]]), 0, 2), [])

    def test_closed_loop_terminates_without_duplicate_faces(self):
        topo = topology([[0, 1, 11, 10], [1, 2, 12, 11], [2, 3, 13, 12], [3, 0, 10, 13]])
        selected = polygon_path(topo, 0, 2, loop=True)
        self.assertEqual(set(selected), set(range(8)))
        self.assertEqual(len(selected), 8)

    def test_deformation_reuses_polygon_connectivity_with_current_positions(self):
        renderer = object.__new__(MGLRendererMixin)
        entry = dict(points=np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0],
                                     [0, 0, 0], [1, 1, 0], [0, 1, 0]], dtype='f4'),
                     topology_mesh='mesh', vertex_ids=np.array([0, 1, 2, 0, 2, 3]), triangle_polygons=[0, 0])
        first = renderer._mgl_mesh_topology_from_entries([entry])
        entry['points'] = entry['points'] + [0, 0, 5]
        second = renderer._mgl_mesh_topology_from_entries([entry])
        self.assertIs(first['polygons'], second['polygons'])
        np.testing.assert_allclose(second['wire_points'][:, 2], 5)

    def test_material_subsets_share_authored_adjacency(self):
        renderer = object.__new__(MGLRendererMixin)
        entries = []
        for polygon, face in enumerate(strip(2)):
            ids = np.asarray([face[i] for i in [0, 1, 2, 0, 2, 3]])
            entries.append(dict(points=np.zeros((6, 3)), vertex_ids=ids,
                                topology_mesh='mesh', triangle_polygons=[polygon, polygon]))
        topo = renderer._mgl_mesh_topology_from_entries(entries)
        self.assertEqual(polygon_path(topo, 0, 2), [0, 1, 2, 3])
        self.assertEqual(len(topo['point_indices']), 6)

    def test_selection_tools_expand_any_triangle_to_whole_polygon(self):
        topo = topology(strip(2))
        view = SimpleNamespace(_mgl_mesh_topology_for_owner=lambda owner: topo,
                               _mesh_element_key=lambda e: (e['mode'], e['owner'], e['face_index']))
        candidates, keys = GraphGLView._mesh_selection_candidate_list_and_keys(view, [
            dict(mode='face', owner='mesh', face_index=1),
            dict(mode='face', owner='mesh', face_index=0)])
        self.assertEqual([c['face_index'] for c in candidates], [0, 1])
        self.assertEqual(len(keys), 2)

    def test_shift_a_middle_click_is_consumed_before_camera_controls(self):
        topo = topology(strip(5))
        view = SimpleNamespace(_mesh_select_mode='face', _mesh_polygon_a_held=True,
            _mesh_polygon_anchor=dict(owner='mesh', face_index=2),
            _handle_mouse_release_moderngl_click_pick_viewport=lambda **kwargs: (10, 10, 100, 100),
            pick_mesh_element_at=lambda *args, **kwargs: dict(mode='face', owner='mesh', face_index=4),
            _mgl_mesh_topology_for_owner=lambda owner: topo, _set_mesh_element_selection_many=Mock(),
            _set_mesh_element_hover=Mock())
        event = SimpleNamespace(modifiers=lambda: QtCore.Qt.ShiftModifier,
                                button=lambda: QtCore.Qt.MiddleButton, accept=Mock())
        mousePressEvent(view, event)
        args = view._set_mesh_element_selection_many.call_args
        self.assertEqual(len(args.args[0]), 10)
        event.accept.assert_called_once()

    def test_all_mesh_points_draw_without_hover_in_point_mode(self):
        renderer = object.__new__(MGLRendererMixin)
        renderer._mesh_select_mode = 'point'
        renderer._mgl_scene_mesh_topology_by_owner = {'mesh': topology(strip(2))}
        renderer._mgl_scene_owner_visible = lambda owner: True
        renderer._mgl_draw_mesh_overlay_points = Mock(return_value=True)
        self.assertTrue(renderer._mgl_draw_curve_control_points_overlay(mvp=None))
        self.assertEqual(len(renderer._mgl_draw_mesh_overlay_points.call_args.args[1]), 6)


if __name__ == '__main__':
    unittest.main()
