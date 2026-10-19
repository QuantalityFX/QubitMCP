import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6 import QtCore, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.ui.node_item import NodeItem
from nodes.util_prompt import connected_prompt, note_prompt_values, prompt_text
from nodes.minimax_h3_video import spec as local
from nodes.minimax_h3_api_video import spec as api


APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
KINDS = ("minimax_h3_video", "minimax_h3_api_video", "minimax_h3_reference_video")


class Graph(QtWidgets.QGraphicsScene):
    linksChanged = QtCore.Signal()
    paramChanged = QtCore.Signal(str, list)

    def __init__(self):
        super().__init__()
        self.edges = []

    def _ordered_in_edges(self, item):
        return [edge for edge in self.edges if edge.dst is item]

    def resolve_text_value(self, item):
        return "\n\n".join(str(entry.get("value", "")) for entry in item.model.params
                            if not str(entry.get("name", "")).startswith("__"))


class MiniMaxNotePromptTests(unittest.TestCase):
    def setUp(self):
        self.scene = Graph()
        self.addCleanup(self.close_scene)
        ready = SimpleNamespace(ready=True, detail="Test model ready")
        self.addCleanup(patch.stopall)
        patch("nodes.minimax_h3_video.spec.minimax_h3_model_status", return_value=ready).start()

    def close_scene(self):
        # Legacy H3 widgets attach to their scene again after a 120ms timer.
        # Drain those callbacks while their real graphics children still exist.
        QtTest.QTest.qWait(140)
        self.scene.clear()
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def make_item(self, kind):
        item = NodeItem(GraphNode("Generator", kind=kind))
        self.scene.addItem(item)
        APP.processEvents()
        return item, item._plugin_proxies[0].widget()

    def note(self, params, checked=()):
        item = QtWidgets.QGraphicsRectItem()
        item.model = GraphNode("Note", kind="note", params=[{"name": key, "value": value} for key, value in params])
        item.model._completed_params = set(checked)
        self.scene.addItem(item)
        return item

    def refresh(self, controls):
        controls._sync_scene()
        APP.processEvents()

    def settings(self, controls):
        return controls._collect_settings() if hasattr(controls, "_collect_settings") else controls._settings(False)

    def test_all_h3_nodes_use_checked_values_live_and_never_fallback_when_nothing_checked(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                item, controls = self.make_item(kind)
                edit = controls._prompt_edit if hasattr(controls, "_prompt_edit") else controls.prompt
                edit.setPlainText("Local fallback must not leak")
                note = self.note([("prompt", "unchecked prompt"), ("character", "selected character"),
                                  ("environment", "selected environment")], {"character", "environment"})
                self.scene.edges = [SimpleNamespace(src=note, dst=item, dst_port_name="prompt")]
                self.refresh(controls)
                self.assertEqual(self.settings(controls).prompt, "selected character\n\nselected environment")
                self.assertFalse(edit.isEnabled())
                note.model._completed_params = {"environment"}
                self.assertEqual(self.settings(controls).prompt, "selected environment")
                note.model._completed_params.clear()
                with self.assertRaisesRegex(ValueError, "Connected prompt is empty"):
                    self.settings(controls)
                # Filtering is entirely at the receiver; ordinary Note output remains complete.
                self.assertEqual(self.scene.resolve_text_value(note),
                                 "unchecked prompt\n\nselected character\n\nselected environment")
                self.scene.edges = []
                self.refresh(controls)
                self.assertTrue(edit.isEnabled())
                self.assertEqual(self.settings(controls).prompt, "Local fallback must not leak")

    def test_single_note_field_is_automatic_for_every_h3_node(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                item, controls = self.make_item(kind)
                note = self.note([("only field", "automatic prompt")])
                self.scene.edges = [SimpleNamespace(src=note, dst=item, dst_port_name="prompt")]
                self.refresh(controls)
                self.assertEqual(self.settings(controls).prompt, "automatic prompt")

    def test_empty_second_field_still_requires_selection_and_internal_fields_do_not_count(self):
        note = self.note([("only", "automatic"), ("__ui_hidden_params", "internal"), ("internal", "hidden")])
        self.assertEqual(note_prompt_values(note.model), {"only": "automatic"})
        note.model.params.append({"name": "second", "value": ""})
        self.assertEqual(note_prompt_values(note.model), {})
        note.model._completed_params = {"only", "internal"}
        self.assertEqual(note_prompt_values(note.model), {"only": "automatic"})

    def test_append_and_switch_filter_live_note_values_without_labels(self):
        note = self.note([("take", "live selected"), ("skip", "unchecked")], {"take"})
        single = self.note([("only", "single value")])
        single.model.name = "Single Note"
        self.scene._nodes_by_name = {"Note": note.model, "Single Note": single.model}
        segments = [{"node": "Merge", "param": "header", "text": "prefix"},
                    {"node": "Note", "param": "take", "text": "stale selected"},
                    {"node": "Note", "param": "skip", "text": "unchecked"},
                    {"node": "Single Note", "param": "only", "text": "single value"}]
        for kind in ("append", "switch"):
            source = SimpleNamespace(model=GraphNode("Merge", kind=kind))
            with patch.object(self.scene, "_flatten_text_segments", return_value=segments, create=True):
                self.assertEqual(prompt_text(self.scene, source), "prefix\n\nlive selected\n\nsingle value")

    def test_unchecked_named_note_output_cannot_bypass_filter_in_helpers(self):
        note = self.note([("unchecked", "do not use"), ("checked", "use this")], {"checked"})
        item = SimpleNamespace(scene=lambda: self.scene)
        self.scene.edges = [SimpleNamespace(src=note, dst=item, dst_port_name="prompt", src_port_name="unchecked")]
        self.assertEqual(connected_prompt(item), (True, ""))
        for module in (local, api):
            self.assertEqual(module._connected_value(item, {"prompt"}), "")
        self.scene.edges[0].src_port_name = "checked"
        self.assertEqual(connected_prompt(item), (True, "use this"))


if __name__ == "__main__":
    unittest.main()
