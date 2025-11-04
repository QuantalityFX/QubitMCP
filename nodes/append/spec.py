# nodes/append/spec.py
from nodes.core import Spec

try:
    from PySide6 import QtWidgets, QtGui, QtCore
except Exception:
    from PySide2 import QtWidgets, QtGui, QtCore  # type: ignore

def augment_infocard_footer(card, footer_layout) -> bool:
    """
    Adds Preview + Save JSON buttons to the Append node's InfoCard footer.
    This version HONORS the Append UI order (switch_inputs) exactly.
    """

    def _collect_append_texts_in_order() -> list[str]:
        sc   = getattr(card, "_graph_scene", None)
        node = getattr(card, "_node_ref", None)
        if not sc or not node:
            return []

        # Get the live NodeItem for this Append node
        try:
            append_item = sc._node_items.get(node.name)
        except Exception:
            append_item = None
        if append_item is None:
            return []

        texts = []

        # Use the scene's Append-aware ordering for inputs
        try:
            ordered_in_edges = sc._ordered_in_edges(append_item)  # already respects switch_inputs
        except Exception:
            ordered_in_edges = []

        for e in ordered_in_edges:
            try:
                t = sc.resolve_text_value(e.src)
            except Exception:
                t = ""
            if t:
                texts.append(t.strip())

        return texts

    # ---- UI callbacks ----
    def _preview():
        parts = _collect_append_texts_in_order()
        QtWidgets.QMessageBox.information(
            card, "Preview (Append order)",
            "No upstream text found." if not parts else "\n\n---\n\n".join(parts)[:5000]
        )

    def _save_json():
        import json
        from pathlib import Path

        parts = _collect_append_texts_in_order()
        if not parts:
            QtWidgets.QMessageBox.warning(card, "Append", "No upstream text found.")
            return

        payload = {
            "node": getattr(card, "_node_ref", None).name if hasattr(card, "_node_ref") else "append",
            "combined_text": "\n\n".join(parts),
            "parts": parts,
        }

        # suggest next to current graph path if available
        win = card.window()
        suggested = "append_result.json"
        try:
            cur = getattr(win, "_current_path", "") or ""
            if cur:
                suggested = str(Path(cur).resolve().parent / suggested)
        except Exception:
            pass

        path, _ = QtWidgets.QFileDialog.getSaveFileName(card, "Save Combined JSON", suggested, "JSON Files (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"Saved:\n{Path(path).name}", card, card.rect(), 1500)
        except Exception as e:
            QtWidgets.QMessageBox.critical(card, "Append", f"Failed to save:\n{e}")

    # ---- footer buttons ----
    b1 = QtWidgets.QPushButton("Preview Merge (Append order)")
    b1.clicked.connect(_preview)
    footer_layout.addWidget(b1)

    b2 = QtWidgets.QPushButton("Save JSON")
    b2.clicked.connect(_save_json)
    footer_layout.addWidget(b2)

    return True

APPEND_SPEC = Spec(
    stripe_color="#b45309",                    # amber-700 stripe
    augment_infocard_footer=augment_infocard_footer,
)
