import tempfile
from pathlib import Path
import unittest

from echograph.patterns.commands import DocumentHistory, rectangle, set_edge_kind, split_segment, delete_points, move_point
from echograph.patterns.document import PatternDocument, PatternError, validate_integrity
from echograph.patterns.geometry import validate_piece
from echograph.patterns.relationships import combine_panels, world_point, mirror_piece
from echograph.patterns.serialization import save_document, load_document


class CombineTests(unittest.TestCase):
    def history(self):
        left, right = rectangle("Left", 100, 100), rectangle("Right", 100, 100)
        right.layout = [100, 0, 0]
        return DocumentHistory(PatternDocument(pieces=[left, right])), left.id, right.id

    def test_combine_keeps_coincident_points_and_separate_outlines(self):
        history, left, right = self.history()
        history.edit("Combine", lambda doc: combine_panels(doc, {left, right}))
        panel = history.document.piece(left)
        self.assertEqual(len(history.document.pieces), 1)
        self.assertEqual(len(panel.points), 8)
        self.assertEqual(len(panel.contours), 2)
        self.assertEqual(validate_piece(panel), [])
        history.undo()
        self.assertEqual(len(history.document.pieces), 2)
        history.redo()
        self.assertEqual(len(history.document.pieces), 1)

    def test_delete_entire_outline_keeps_other_combined_outline(self):
        history, left, right = self.history()
        history.edit("Combine", lambda doc: combine_panels(doc, {left, right}))
        panel = history.document.piece(left)
        removed = {point.id for point in panel.boundary_parts()[0].points}
        original_other = [edge.id for edge in panel.boundary_parts()[1].segments]
        history.edit("Delete lobe", lambda doc: delete_points(doc.piece(left), removed))
        panel = history.document.piece(left)
        self.assertEqual(len(panel.points), 4)
        self.assertEqual([edge.id for edge in panel.segments], original_other)
        self.assertEqual(validate_piece(panel), [])
        validate_integrity(history.document)
        history.undo()
        self.assertEqual(len(history.document.piece(left).points), 8)

    def test_delete_lobe_retains_shared_edge_only_in_surviving_square(self):
        history, left, right = self.history()
        a, b = history.document.pieces
        bottom = {left: {a.points[1].id}, right: {b.points[0].id}}
        history.edit("Fuse bottom", lambda doc: combine_panels(doc, {left, right}, fuse_selection=bottom))
        panel = history.document.piece(left)
        top = {left: {p.id for p in panel.points if p.x == 100 and p.y == 100}}
        history.edit("Fuse top", lambda doc: combine_panels(doc, {left}, fuse_selection=top))
        panel = history.document.piece(left)
        self.assertEqual(len(panel.contours), 2)
        removed = {point.id for point in panel.points if point.x == 0}
        surviving_edges = [edge.id for edge in panel.boundary_parts()[1].segments]
        history.edit("Delete left side", lambda doc: delete_points(doc.piece(left), removed))
        panel = history.document.piece(left)
        self.assertEqual(len(panel.points), 4)
        self.assertEqual([edge.id for edge in panel.segments], surviving_edges)
        self.assertEqual(len(panel.contours), 1)
        self.assertEqual(validate_piece(panel), [])
        validate_integrity(history.document)

    def test_triangle_can_be_reduced_to_original_edge_then_point_then_empty(self):
        from echograph.patterns.commands import polygon
        panel = polygon("Triangle", [(0, 0), (100, 0), (50, 100)])
        history = DocumentHistory(PatternDocument(pieces=[panel]))
        bottom_edge = panel.segments[0].id
        history.edit("Delete apex", lambda doc: delete_points(doc.piece(panel.id), {panel.points[2].id}))
        reduced = history.document.piece(panel.id)
        self.assertEqual([edge.id for edge in reduced.segments], [bottom_edge])
        self.assertFalse(reduced.closed)
        history.edit("Delete next", lambda doc: delete_points(doc.piece(panel.id), {panel.points[1].id}))
        self.assertEqual(len(history.document.piece(panel.id).points), 1)
        self.assertFalse(history.document.piece(panel.id).segments)
        history.edit("Delete final", lambda doc: delete_points(doc.piece(panel.id), {panel.points[0].id}))
        self.assertFalse(history.document.piece(panel.id).points)
        validate_integrity(history.document)

    def test_translate_shared_edge_endpoints_once_and_keep_curve_controls(self):
        from echograph.patterns.commands import translate_edges
        history, left, right = self.history()
        panel = history.document.piece(left)
        panel.layout[2] = 90
        set_edge_kind(panel, panel.segments[0].id, "cubic")
        before = {point.id: world_point(panel, point.id) for point in panel.points}
        controls = [list(control) for control in panel.segments[0].controls]
        selection = {(left, panel.segments[0].id), (left, panel.segments[1].id)}
        history.edit("Move edges", lambda doc: translate_edges(doc, selection, 20, 10))
        moved = history.document.piece(left)
        for point in moved.points[:3]:
            actual = world_point(moved, point.id)
            self.assertAlmostEqual(actual[0], before[point.id][0] + 20)
            self.assertAlmostEqual(actual[1], before[point.id][1] + 10)
        self.assertEqual(world_point(moved, moved.points[3].id), before[moved.points[3].id])
        for control, original in zip(moved.segments[0].controls, controls):
            self.assertAlmostEqual(control[0], original[0] + 10)
            self.assertAlmostEqual(control[1], original[1] - 20)

    def test_fuse_all_shares_only_overlapping_vertices_and_roundtrips(self):
        history, left, right = self.history()
        history.edit("Fuse", lambda doc: combine_panels(doc, {left, right}, fuse_all=True))
        panel = history.document.piece(left)
        self.assertEqual(len(panel.points), 6)
        self.assertEqual(len(panel.contours), 1)
        self.assertEqual(len(panel.segments), 6)
        self.assertEqual(validate_piece(panel), [])
        self.assertFalse(any(panel.point(edge.start).x == panel.point(edge.end).x == 100
                             for edge in panel.segments))
        shared_id = next(point.id for point in panel.points if point.x == 100 and point.y == 0)
        history.edit("Move shared vertex", lambda doc: move_point(doc.piece(left), shared_id, 105, 5))
        self.assertEqual(len(history.document.piece(left).points), 6)
        validate_integrity(history.document)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "combined.pattern.json"
            save_document(path, history.document)
            restored, _ = load_document(path)
            self.assertEqual(restored.to_dict(), history.document.to_dict())

    def test_selected_points_limit_fuse_and_do_not_move_distant_points(self):
        history, left, right = self.history()
        a, b = history.document.pieces
        selection = {left: {a.points[1].id, a.points[0].id}, right: {b.points[0].id}}
        history.edit("Fuse selected", lambda doc: combine_panels(doc, {left, right}, fuse_selection=selection))
        panel = history.document.piece(left)
        self.assertEqual(len(panel.points), 7)
        self.assertEqual(world_point(panel, a.points[0].id), (0, 0))
        self.assertEqual(world_point(panel, a.points[1].id), (100, 0))

    def test_rotated_combine_preserves_world_vertices_and_cubic_controls(self):
        from echograph.patterns.relationships import _world_position
        history, left, right = self.history()
        a, b = history.document.pieces
        a.layout = [80, -20, 35]
        b.layout = [-90, 140, -70]
        set_edge_kind(b, b.segments[0].id, "cubic")
        expected = {p.id: world_point(piece, p.id) for piece in (a, b) for p in piece.points}
        edge_id = b.segments[0].id
        controls = [_world_position(b, control) for control in b.segments[0].controls]
        history.edit("Combine", lambda doc: combine_panels(doc, {left, right}))
        result = history.document.piece(left)
        for point in result.points:
            actual = world_point(result, point.id)
            self.assertAlmostEqual(actual[0], expected[point.id][0])
            self.assertAlmostEqual(actual[1], expected[point.id][1])
        for actual, target in zip(result.segment(edge_id).controls, controls):
            position = _world_position(result, actual)
            self.assertAlmostEqual(position[0], target[0])
            self.assertAlmostEqual(position[1], target[1])

    def test_combined_outlines_remain_editable_and_mirrorable(self):
        history, left, right = self.history()
        history.edit("Combine", lambda doc: combine_panels(doc, {left, right}))
        edge = history.document.piece(left).segments[4].id
        inserted = []
        history.edit("Insert", lambda doc: inserted.append(split_segment(doc.piece(left), edge)))
        self.assertEqual(len(history.document.piece(left).contours[1]), 5)
        history.edit("Delete", lambda doc: delete_points(doc.piece(left), inserted))
        self.assertEqual(len(history.document.piece(left).contours[1]), 4)
        history.edit("Mirror", lambda doc: mirror_piece(doc, left))
        validate_integrity(history.document)
        source = history.document.piece(left)
        history.edit("Move", lambda doc: move_point(doc.piece(left), source.points[0].id, -5, 0))
        self.assertEqual(len(history.document.pieces[1].contours), 2)
        validate_integrity(history.document)

    def test_legacy_document_and_malformed_contours(self):
        history, left, right = self.history()
        data = history.document.to_dict()
        for panel in data["pieces"]:
            panel.pop("contours", None)
        self.assertEqual(len(PatternDocument.from_dict(data).pieces), 2)
        panel = history.document.piece(left)
        panel.contours = [[panel.segments[0].id]]
        with self.assertRaises(PatternError):
            validate_integrity(history.document)

    def test_fully_coincident_fused_outlines_keep_one_exterior_when_mirrored(self):
        history, left, right = self.history()
        history.document.piece(right).layout = [0, 0, 0]
        history.edit("Fuse", lambda doc: combine_panels(doc, {left, right}, fuse_all=True))
        self.assertEqual(len(history.document.piece(left).points), 4)
        self.assertEqual(len(history.document.piece(left).segments), 4)
        self.assertEqual(len(history.document.piece(left).contours), 1)
        history.edit("Mirror", lambda doc: mirror_piece(doc, left))
        validate_integrity(history.document)
        pair = history.document.metadata["symmetry_pairs"][0]
        self.assertEqual(len(set(pair["edges"].values())), 4)

    def test_matching_reversed_cubic_seam_dissolves_and_outer_curve_survives(self):
        history, left, right = self.history()
        a, b = history.document.pieces
        set_edge_kind(a, a.segments[1].id, "cubic")
        a.segments[1].controls = [[120, 30], [80, 70]]
        set_edge_kind(b, b.segments[3].id, "cubic")
        b.segments[3].controls = [[-20, 70], [20, 30]]
        set_edge_kind(a, a.segments[0].id, "cubic")
        outer_id = a.segments[0].id
        outer_controls = [list(control) for control in a.segments[0].controls]
        history.edit("Fuse", lambda doc: combine_panels(doc, {left, right}, fuse_all=True))
        result = history.document.piece(left)
        self.assertEqual(len(result.segments), 6)
        self.assertEqual(len(result.contours), 1)
        self.assertEqual(result.segment(outer_id).controls, outer_controls)
        self.assertEqual(validate_piece(result), [])
        history.undo()
        self.assertEqual(len(history.document.pieces), 2)
        history.redo()
        self.assertEqual(len(history.document.piece(left).contours), 1)

    def test_different_cubic_shapes_with_shared_endpoints_are_not_dissolved(self):
        history, left, right = self.history()
        a, b = history.document.pieces
        set_edge_kind(a, a.segments[1].id, "cubic")
        a.segments[1].controls = [[120, 30], [120, 70]]
        history.edit("Fuse", lambda doc: combine_panels(doc, {left, right}, fuse_all=True))
        panel = history.document.piece(left)
        self.assertEqual(len(panel.segments), 8)
        self.assertEqual(len(panel.contours), 2)

    def test_single_point_contact_keeps_two_outlines(self):
        history, left, right = self.history()
        history.document.piece(right).layout = [100, 100, 0]
        history.edit("Fuse", lambda doc: combine_panels(doc, {left, right}, fuse_all=True))
        panel = history.document.piece(left)
        self.assertEqual(len(panel.points), 7)
        self.assertEqual(len(panel.segments), 8)
        self.assertEqual(len(panel.contours), 2)
        self.assertEqual(validate_piece(panel), [])

    def test_collapsed_line_is_removed_and_boundary_remains_ordered(self):
        from echograph.patterns.commands import polygon
        panel = polygon("Repeated point", [(0, 0), (100, 0), (100, 0), (100, 100), (0, 100)])
        history = DocumentHistory(PatternDocument(pieces=[panel]))
        history.edit("Fuse", lambda doc: combine_panels(doc, {panel.id}, fuse_all=True))
        result = history.document.piece(panel.id)
        self.assertEqual(len(result.points), 4)
        self.assertEqual(len(result.segments), 4)
        self.assertEqual(validate_piece(result), [])

    def test_multiple_seams_preserve_an_inner_hole(self):
        from echograph.patterns.geometry import signed_area
        panels = []
        for x in range(3):
            for y in range(3):
                if (x, y) == (1, 1):
                    continue
                panel = rectangle(f"Tile {x},{y}", 100, 100)
                panel.layout = [x * 100, y * 100, 0]
                panels.append(panel)
        history = DocumentHistory(PatternDocument(pieces=panels))
        history.edit("Fuse ring", lambda doc: combine_panels(doc, {p.id for p in panels}, fuse_all=True))
        result = history.document.pieces[0]
        self.assertEqual(sorted(len(run) for run in result.contours), [4, 12])
        self.assertEqual(len(result.segments), 16)
        self.assertAlmostEqual(signed_area(result), 80000)
        self.assertEqual(validate_piece(result), [])
        validate_integrity(history.document)

    def test_selected_points_do_not_dissolve_an_unselected_existing_seam(self):
        history, left, right = self.history()
        # First merge the join vertices without asking cleanup to consider
        # both ends of the shared edge in the same operation.
        a, b = history.document.pieces
        bottom = {left: {a.points[1].id}, right: {b.points[0].id}}
        history.edit("Fuse bottom", lambda doc: combine_panels(doc, {left, right}, fuse_selection=bottom))
        panel = history.document.piece(left)
        top = {left: {p.id for p in panel.points if p.x == 100 and p.y == 100}}
        history.edit("Fuse top", lambda doc: combine_panels(doc, {left}, fuse_selection=top))
        self.assertEqual(len(history.document.piece(left).segments), 8)
        history.edit("Fuse all", lambda doc: combine_panels(doc, {left}, fuse_all=True))
        self.assertEqual(len(history.document.piece(left).segments), 6)
