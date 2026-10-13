from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from echograph.patterns.commands import DocumentHistory, rectangle, split_segment, reverse_boundary
from echograph.patterns.document import PatternDocument, PatternError
from echograph.patterns.elastic import elastic_for_edge, set_elastic_edges
from echograph.patterns.relationships import combine_panels
from echograph.patterns.serialization import load_document, save_document


class ElasticTests(unittest.TestCase):
    def setUp(self):
        self.front, self.back = rectangle("Front"), rectangle("Back")
        self.back.layout[0] = 450
        self.history = DocumentHistory(PatternDocument(pieces=[self.front, self.back]))
        self.edge = self.front.segments[2].id

    def mark(self):
        self.history.edit("Elastic", lambda doc: set_elastic_edges(doc, [(self.front.id, self.edge)], True, 1500, .75))

    def test_persistence_undo_strength_edit_and_remove_preserve_geometry(self):
        before = deepcopy(self.history.document.to_dict()["pieces"])
        self.mark()
        self.assertEqual(self.history.document.to_dict()["pieces"], before)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "garment.pattern.json"
            save_document(path, self.history.document)
            loaded, _ = load_document(path)
        self.assertEqual(elastic_for_edge(loaded, self.front.id, self.edge)["strength"], 1500)
        self.history.edit("Tune", lambda doc: set_elastic_edges(doc, [(self.front.id, self.edge)], True, 3000, .9))
        self.history.undo()
        self.assertEqual(elastic_for_edge(self.history.document, self.front.id, self.edge)["rest_ratio"], .75)
        self.history.edit("Remove", lambda doc: set_elastic_edges(doc, [(self.front.id, self.edge)], False))
        self.assertIsNone(elastic_for_edge(self.history.document, self.front.id, self.edge))
        self.history.undo()
        self.assertIsNotNone(elastic_for_edge(self.history.document, self.front.id, self.edge))

    def test_split_inherits_elastic_and_delete_prunes_annotations(self):
        self.mark()
        self.history.edit("Split", lambda doc: split_segment(doc.piece(self.front.id), self.edge))
        rows = self.history.document.metadata["elastic_edges"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["rest_ratio"] for row in rows}, {.75})
        self.history.undo()
        self.history.edit("Delete", lambda doc: doc.pieces.remove(doc.piece(self.front.id)))
        self.assertEqual(self.history.document.metadata["elastic_edges"], [])
        self.history.undo()
        self.assertIsNotNone(elastic_for_edge(self.history.document, self.front.id, self.edge))

    def test_combining_and_reversing_keep_elastic_edge(self):
        self.mark()
        self.history.edit("Reverse", lambda doc: reverse_boundary(doc.piece(self.front.id)))
        self.history.edit("Combine", lambda doc: combine_panels(doc, {self.front.id, self.back.id}))
        row = self.history.document.metadata["elastic_edges"][0]
        self.assertEqual(row["piece"], self.history.document.pieces[0].id)
        self.assertEqual(row["edge"], self.edge)

    def test_malformed_annotations_are_rejected(self):
        self.mark()
        for field, value in (("edge", "missing"), ("strength", float("nan")), ("strength", 0),
                             ("rest_ratio", 0), ("rest_ratio", 1.5)):
            with self.subTest(field=field, value=value):
                data = self.history.document.to_dict()
                data["metadata"]["elastic_edges"][0][field] = value
                with self.assertRaises(PatternError):
                    PatternDocument.from_dict(data)
