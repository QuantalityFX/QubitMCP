import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from echograph.patterns.commands import (DocumentHistory, close_outline, duplicate_piece, move_point,
                                         delete_points, polygon, rectangle, resize_piece, set_edge_kind,
                                         split_segment)
from echograph.patterns.document import PatternDocument, PatternError, validate_integrity
from echograph.patterns.geometry import bounds, edge_length, signed_area, validate_piece
from echograph.patterns.serialization import (PatternConflictError, load_document, resolve_pattern_path,
                                              save_document)


class PatternGeometryTests(unittest.TestCase):
    def test_split_cubic_edge_preserves_curve_and_delete_point_keeps_curve_type(self):
        piece = rectangle("Front")
        edge = piece.segments[0]
        set_edge_kind(piece, edge.id, "cubic")
        edge.controls = [[50, -40], [240, 90]]
        original_controls = [list(c) for c in edge.controls]
        point_id = split_segment(piece, edge.id)
        left, right = piece.segments[:2]
        self.assertEqual(left.kind, "cubic")
        self.assertEqual(right.kind, "cubic")
        self.assertEqual(left.end, point_id)
        self.assertEqual(right.start, point_id)
        # Deleting the inserted knot reconnects the boundary with an editable cubic.
        delete_points(piece, [point_id])
        self.assertEqual(len(piece.points), 4)
        self.assertEqual(len(piece.segments), 4)
        joined = piece.segments[0]
        self.assertEqual(joined.kind, "cubic")
        self.assertEqual(joined.controls, original_controls)
        validate_integrity(PatternDocument(pieces=[piece]))

    def test_delete_open_outline_endpoint_and_preserve_chain(self):
        piece = polygon("Open", [(0, 0), (100, 0), (120, 50)], closed=False)
        delete_points(piece, [piece.points[-1].id])
        self.assertEqual(len(piece.points), 2)
        self.assertEqual(len(piece.segments), 1)
        self.assertEqual(piece.segments[0].start, piece.points[0].id)
        self.assertEqual(piece.segments[0].end, piece.points[1].id)

    def test_delete_open_outline_interior_reconnects_neighbors(self):
        piece = polygon("Open", [(0, 0), (100, 0), (120, 50), (200, 50)], closed=False)
        removed = piece.points[1].id
        delete_points(piece, [removed])
        self.assertEqual(len(piece.points), 3)
        self.assertEqual(len(piece.segments), 2)
        self.assertEqual(piece.segments[0].end, piece.points[1].id)
        self.assertEqual(piece.segments[1].start, piece.points[1].id)
        validate_integrity(PatternDocument(pieces=[piece]))

    def test_rectangle_scale_and_shared_endpoint_edit(self):
        piece = rectangle("Front", 300, 400)
        self.assertEqual(bounds(piece), (0, 0, 300, 400))
        self.assertEqual(signed_area(piece), 120000)
        self.assertEqual([edge_length(piece, s) for s in piece.segments], [300, 400, 300, 400])
        ids = [s.id for s in piece.segments]
        move_point(piece, piece.points[0].id, -10.125, 0)
        self.assertEqual(piece.segments[0].start, piece.segments[-1].end)
        self.assertEqual(ids, [s.id for s in piece.segments])
        self.assertFalse(validate_piece(piece))
        self.assertNotIn("template", piece.metadata)

    def test_cubic_geometry_and_mirror_preserve_shape_winding(self):
        piece = rectangle("Curved")
        edge = piece.segments[2]
        set_edge_kind(piece, edge.id, "cubic")
        edge.controls = [[240, 480], [60, 480]]
        self.assertGreater(edge_length(piece, edge), 300)
        self.assertFalse(validate_piece(piece))
        mirrored = duplicate_piece(piece, mirror=True)
        self.assertAlmostEqual(signed_area(piece), signed_area(mirrored), places=6)
        self.assertEqual(bounds(piece), bounds(mirrored))
        self.assertTrue({p.id for p in piece.points}.isdisjoint(p.id for p in mirrored.points))
        self.assertTrue({s.id for s in piece.segments}.isdisjoint(s.id for s in mirrored.segments))
        validate_integrity(PatternDocument(pieces=[piece, mirrored]))
        self.assertFalse(validate_piece(mirrored))

    def test_resize_and_layout_leave_identities_intact(self):
        piece = rectangle("Front")
        old_ids = [p.id for p in piece.points]
        resize_piece(piece, 150, 200)
        self.assertEqual(bounds(piece), (0, 0, 150, 200))
        piece.layout = [1000, -200, 45]
        self.assertEqual(bounds(piece), (0, 0, 150, 200))
        self.assertEqual(old_ids, [p.id for p in piece.points])

    def test_small_panel_at_large_local_coordinates_retains_area(self):
        offset = 900000000
        piece = polygon("Offset", [(offset, offset), (offset + 1, offset),
                                    (offset + 1, offset + 1), (offset, offset + 1)])
        self.assertEqual(signed_area(piece), 1)
        self.assertFalse(validate_piece(piece))

    def test_invalid_and_concave_outlines(self):
        cases = [
            ([(0, 0), (100, 100), (0, 100), (100, 0)], "Self-intersecting"),
            ([(0, 0), (100, 0), (100, 0), (0, 100)], "Zero-length"),
            ([(0, 0), (100, 0), (50, 0), (50, 100), (0, 100)], "Overlapping"),
        ]
        for points, message in cases:
            with self.subTest(message=message):
                errors = validate_piece(polygon("Invalid", points))
                self.assertTrue(any(message in error.message for error in errors), errors)
        concave = polygon("Concave", [(0, 0), (100, 0), (100, 100), (50, 50), (0, 100)])
        self.assertFalse(validate_piece(concave))

    def test_open_draft_can_close_without_replacing_edges(self):
        piece = polygon("Draft", [(0, 0), (100, 0), (0, 100)], closed=False)
        validate_integrity(PatternDocument(pieces=[piece]))
        self.assertTrue(validate_piece(piece))
        ids = [s.id for s in piece.segments]
        close_outline(piece)
        self.assertFalse(validate_piece(piece))
        self.assertEqual(ids, [s.id for s in piece.segments[:2]])

    def test_transaction_rejection_and_undo_saved_state(self):
        doc = PatternDocument(pieces=[rectangle("Front")])
        history = DocumentHistory(doc)
        original_hash = history.document.content_hash()
        history.edit("Move", lambda d: move_point(d.pieces[0], d.pieces[0].points[0].id, -5, 0))
        self.assertTrue(history.dirty)
        generation = history.generation
        history.undo()
        self.assertFalse(history.dirty)
        self.assertEqual(history.document.content_hash(), original_hash)
        self.assertGreater(history.generation, generation)
        history.redo()
        self.assertTrue(history.dirty)
        before = history.document.to_dict()
        with self.assertRaises(PatternError):
            history.edit("Invalid", lambda d: setattr(d.pieces[0].points[0], "x", float("nan")))
        self.assertEqual(before, history.document.to_dict())


class PatternFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "design.pattern.json"
        self.doc = PatternDocument(pieces=[rectangle("Front")])

    def test_roundtrip_curves_precision_ids_and_metadata(self):
        piece = self.doc.pieces[0]
        set_edge_kind(piece, piece.segments[0].id, "cubic")
        piece.segments[0].controls[0][1] = -12.345678901234567
        self.doc.metadata = {"notes": "測定", "measurements": {"bust_mm": 920.125}}
        signature = save_document(self.path, self.doc)
        actual, loaded_signature = load_document(self.path)
        self.assertEqual(actual.to_dict(), self.doc.to_dict())
        self.assertEqual(signature, loaded_signature)

    def test_separate_readers_see_shared_file_and_stale_writer_is_rejected(self):
        save_document(self.path, self.doc)
        first, first_hash = load_document(self.path)
        second, second_hash = load_document(self.path)
        first.pieces[0].name = "Changed by workflow A"
        new_hash = save_document(self.path, first, expected_hash=first_hash)
        second.pieces[0].name = "Older workflow B"
        with self.assertRaises(PatternConflictError):
            save_document(self.path, second, expected_hash=second_hash)
        reloaded, signature = load_document(self.path)
        self.assertEqual(reloaded.pieces[0].name, first.pieces[0].name)
        self.assertEqual(signature, new_hash)

    def test_missing_file_is_not_recreated_by_old_editor(self):
        signature = save_document(self.path, self.doc)
        self.path.unlink()
        with self.assertRaises(PatternConflictError):
            save_document(self.path, self.doc, expected_hash=signature)
        self.assertFalse(self.path.exists())

    def test_failed_replace_preserves_previous_file_and_cleans_temp(self):
        signature = save_document(self.path, self.doc)
        original = self.path.read_bytes()
        self.doc.pieces[0].name = "Updated"
        with patch("echograph.patterns.serialization.os.replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                save_document(self.path, self.doc, expected_hash=signature)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_existing_workflow_json_cannot_be_overwritten_as_new_pattern(self):
        original = '{"nodes": [], "edges": []}'
        self.path.write_text(original, encoding="utf-8")
        with self.assertRaises(PatternConflictError):
            save_document(self.path, self.doc)
        with self.assertRaises(PatternError):
            load_document(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_structurally_valid_draft_is_saveable(self):
        self.doc.pieces = [polygon("Draft", [(0, 0)], closed=False)]
        save_document(self.path, self.doc)
        loaded, _ = load_document(self.path)
        self.assertTrue(validate_piece(loaded.pieces[0]))

    def test_strict_schema_and_broken_references(self):
        for mutation in (
            lambda d: d.update(schema="qubit.pattern_document.v999"),
            lambda d: d.update(units="cm"),
            lambda d: d["pieces"][0]["points"][0].update(x=True),
            lambda d: d["pieces"][0]["points"][0].update(x=10**400),
            lambda d: d["pieces"][0]["segments"][0].update(start="missing"),
            lambda d: d["pieces"][0]["points"][0].update(id=d["document_id"]),
            lambda d: d["pieces"][0].update(holes=[]),
        ):
            data = self.doc.to_dict()
            mutation(data)
            self.path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(PatternError):
                load_document(self.path)
        for text in ('{"schema": 1, "schema": 2}', '{"schema": NaN}', '[1, 2]', 'bad json'):
            self.path.write_text(text, encoding="utf-8")
            with self.assertRaises(PatternError):
                load_document(self.path)

    def test_relative_references_use_workflow_directory_not_cwd(self):
        workflow = Path(self.temp.name) / "workflows" / "look.json"
        result = resolve_pattern_path("../design.pattern.json", workflow_path=workflow)
        self.assertEqual(result, self.path.resolve())
        with self.assertRaises(PatternError):
            resolve_pattern_path("design.pattern.json")
