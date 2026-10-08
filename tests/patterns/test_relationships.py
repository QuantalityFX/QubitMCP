import tempfile
from pathlib import Path
import unittest

from echograph.patterns.commands import DocumentHistory, rectangle, move_point, set_edge_kind, resize_piece, split_segment, delete_points, duplicate_piece
from echograph.patterns.document import PatternDocument, PatternError, validate_integrity
from echograph.patterns.relationships import mirror_piece, symmetry_for_piece, set_symmetry_enabled, merge_points, world_point
from echograph.patterns.serialization import save_document, load_document


class RelationshipTests(unittest.TestCase):
    def setUp(self):
        piece = rectangle("Front")
        set_edge_kind(piece, piece.segments[0].id, "cubic")
        piece.segments[0].controls = [[50, -40], [240, 90]]
        self.left = piece.id
        self.history = DocumentHistory(PatternDocument(pieces=[piece]))
        self.history.edit("Mirror", lambda doc: mirror_piece(doc, self.left))
        self.right = self.history.document.pieces[1].id

    def assertMirrored(self):
        doc = self.history.document
        pair = symmetry_for_piece(doc, self.left)
        left, right = doc.piece(self.left), doc.piece(self.right)
        for point in left.points:
            partner = right.point(pair["points"][point.id])
            self.assertAlmostEqual(partner.x, pair["axis_sum"] - point.x)
            self.assertAlmostEqual(partner.y, point.y)
        for edge in left.segments:
            partner = right.segment(pair["edges"][edge.id])
            self.assertEqual(partner.start, pair["points"][edge.end])
            self.assertEqual(partner.end, pair["points"][edge.start])
            self.assertEqual(partner.kind, edge.kind)
            self.assertEqual(partner.controls, [[pair["axis_sum"] - x, y] for x, y in reversed(edge.controls)])
        self.assertEqual(right.closed, left.closed)
        validate_integrity(doc)

    def test_edits_from_either_side_update_points_curves_and_dimensions(self):
        self.assertMirrored()
        left = self.history.document.piece(self.left)
        self.history.edit("Move left", lambda doc: move_point(doc.piece(self.left), left.points[0].id, -12, 25))
        self.assertMirrored()
        right = self.history.document.piece(self.right)
        self.history.edit("Move right", lambda doc: move_point(doc.piece(self.right), right.points[2].id, 90, 460))
        self.assertMirrored()
        self.history.edit("Resize right", lambda doc: resize_piece(doc.piece(self.right), 380, 510))
        self.assertMirrored()
        self.history.edit("Curve handles", lambda doc: setattr(doc.piece(self.right).segments[-1], "controls", [[80, 200], [240, -80]]))
        self.assertMirrored()

    def test_topology_edits_keep_matching_ids_and_undo_as_one_step(self):
        doc = self.history.document
        edge = doc.piece(self.left).segments[0].id
        before = doc.to_dict()
        self.history.edit("Split", lambda doc: split_segment(doc.piece(self.left), edge))
        self.assertMirrored()
        self.assertEqual(len(self.history.document.piece(self.right).points), 5)
        added = self.history.document.piece(self.left).points[1].id
        self.history.edit("Delete vertex", lambda doc: delete_points(doc.piece(self.left), [added]))
        self.assertMirrored()
        self.history.undo()
        self.history.undo()
        restored = self.history.document.to_dict()
        before.pop("revision")
        restored.pop("revision")
        self.assertEqual(restored, before)

    def test_disable_edit_and_reenable_from_partner(self):
        self.history.edit("Off", lambda doc: set_symmetry_enabled(doc, self.left, False))
        left_before = self.history.document.piece(self.left).points[0].x
        right = self.history.document.piece(self.right)
        self.history.edit("Independent", lambda doc: move_point(doc.piece(self.right), right.points[0].id, 510, 15))
        self.assertEqual(self.history.document.piece(self.left).points[0].x, left_before)
        self.history.edit("On", lambda doc: set_symmetry_enabled(doc, self.right, True))
        self.assertMirrored()

    def test_link_survives_separate_pattern_file_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "symmetry.pattern.json"
            save_document(path, self.history.document)
            document, _ = load_document(path)
            self.history = DocumentHistory(document)
            point = document.piece(self.left).points[0].id
            self.history.edit("Loaded edit", lambda doc: move_point(doc.piece(self.left), point, 12.125, 24))
            self.assertMirrored()

    def test_merge_points_disables_symmetry_and_welds_in_rotated_sheet_space(self):
        target = rectangle("Another panel")
        target.layout = [600, 80, 45]
        self.history.edit("New panel", lambda doc: doc.pieces.append(target))
        source_point = self.history.document.piece(self.right).points[0].id
        target_point = target.points[1].id
        self.history.edit("Merge", lambda doc: merge_points(doc, self.right, source_point, target.id, target_point))
        doc = self.history.document
        self.assertFalse(symmetry_for_piece(doc, self.right)["enabled"])
        self.assertAlmostEqual(world_point(doc.piece(self.right), source_point)[0], world_point(doc.piece(target.id), target_point)[0])
        self.assertAlmostEqual(world_point(doc.piece(self.right), source_point)[1], world_point(doc.piece(target.id), target_point)[1])
        self.history.edit("Move welded point", lambda doc: move_point(doc.piece(target.id), target_point, 320, 30))
        doc = self.history.document
        self.assertAlmostEqual(world_point(doc.piece(self.right), source_point)[0], world_point(doc.piece(target.id), target_point)[0])
        self.assertAlmostEqual(world_point(doc.piece(self.right), source_point)[1], world_point(doc.piece(target.id), target_point)[1])
        self.history.undo()
        self.history.undo()
        self.assertFalse(self.history.document.metadata.get("point_welds"))
        self.assertTrue(symmetry_for_piece(self.history.document, self.left)["enabled"])
        self.assertMirrored()

    def test_duplicate_is_independent_and_deleting_partner_removes_link(self):
        copied = duplicate_piece(self.history.document.piece(self.left))
        self.history.edit("Copy", lambda doc: doc.pieces.append(copied))
        self.assertIsNone(symmetry_for_piece(self.history.document, copied.id))
        self.history.edit("Delete partner", lambda doc: setattr(doc, "pieces", [p for p in doc.pieces if p.id != self.right]))
        self.assertIsNone(symmetry_for_piece(self.history.document, self.left))

    def test_invalid_active_mapping_is_rejected_on_load(self):
        data = self.history.document.to_dict()
        data["metadata"]["symmetry_pairs"][0]["points"] = {}
        with self.assertRaises(PatternError):
            PatternDocument.from_dict(data)


if __name__ == "__main__":
    unittest.main()
