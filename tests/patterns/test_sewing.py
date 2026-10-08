from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from echograph.patterns.commands import DocumentHistory, rectangle, split_segment, reverse_boundary
from echograph.patterns.document import PatternDocument, PatternError
from echograph.patterns.relationships import combine_panels
from echograph.patterns.serialization import load_document, save_document
from echograph.patterns.sewing import add_sew_pair, validate_sewing


class SewingTests(unittest.TestCase):
    def setUp(self):
        self.front, self.back = rectangle("Front"), rectangle("Back")
        self.back.layout[0] = 450
        self.history = DocumentHistory(PatternDocument(pieces=[self.front, self.back]))

    def sew(self):
        return self.history.edit("Sew", lambda doc: add_sew_pair(doc, self.front.id, self.front.segments[1].id,
                                                               self.back.id, self.back.segments[3].id))

    def test_pair_persists_and_undo_redo_preserve_geometry(self):
        before = deepcopy(self.history.document.to_dict()["pieces"])
        self.sew()
        doc = self.history.document
        self.assertEqual(doc.to_dict()["pieces"], before)
        pair = doc.metadata["sew_pairs"][0]
        self.assertEqual(pair["number"], 1)
        self.assertTrue(pair["reverse"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "shirt.pattern.json"
            save_document(path, doc)
            loaded, _ = load_document(path)
        self.assertEqual(loaded.metadata["sew_pairs"], [pair])
        self.history.undo()
        self.assertFalse(self.history.document.metadata.get("sew_pairs"))
        self.history.redo()
        self.assertEqual(self.history.document.metadata["sew_pairs"], [pair])

    def test_same_or_already_paired_edge_rejected_without_history_change(self):
        with self.assertRaises(PatternError):
            add_sew_pair(self.history.document, self.front.id, self.front.segments[0].id,
                         self.front.id, self.front.segments[0].id)
        self.sew()
        before = self.history.document.to_dict()
        with self.assertRaises(PatternError):
            self.sew()
        self.assertEqual(self.history.document.to_dict(), before)

    def test_multiple_pairs_have_stable_numbers(self):
        self.sew()
        self.history.edit("Sew another", lambda doc: add_sew_pair(doc, self.front.id, self.front.segments[0].id,
                                                                   self.back.id, self.back.segments[0].id))
        self.assertEqual([p["number"] for p in self.history.document.metadata["sew_pairs"]], [1, 2])

    def test_deleted_panel_and_split_edge_remove_stale_pair_and_undo_restores(self):
        self.sew()
        self.history.edit("Split", lambda doc: split_segment(doc.piece(self.front.id), self.front.segments[1].id))
        self.assertEqual(self.history.document.metadata["sew_pairs"], [])
        self.history.undo()
        self.assertEqual(len(self.history.document.metadata["sew_pairs"]), 1)
        self.history.edit("Delete panel", lambda doc: doc.pieces.pop())
        self.assertEqual(self.history.document.metadata["sew_pairs"], [])
        self.history.undo()
        self.assertEqual(len(self.history.document.metadata["sew_pairs"]), 1)

    def test_combined_panels_keep_pair_using_new_owner(self):
        self.sew()
        self.history.edit("Combine", lambda doc: combine_panels(doc, {self.front.id, self.back.id}))
        doc = self.history.document
        pair = doc.metadata["sew_pairs"][0]
        self.assertEqual(pair["left_piece"], pair["right_piece"])
        validate_sewing(doc)

    def test_reversing_boundary_retains_correct_sewing_direction(self):
        self.sew()
        self.history.edit("Reverse", lambda doc: reverse_boundary(doc.piece(self.front.id)))
        self.assertFalse(self.history.document.metadata["sew_pairs"][0]["reverse"])

    def test_invalid_saved_relationship_is_rejected(self):
        self.sew()
        data = self.history.document.to_dict()
        data["metadata"]["sew_pairs"][0]["left_edge"] = "missing"
        with self.assertRaises(PatternError):
            PatternDocument.from_dict(data)
