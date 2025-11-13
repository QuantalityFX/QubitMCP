# nodes/note/spec.py
from __future__ import annotations

try:
    from PySide6 import QtWidgets
except Exception:
    from PySide2 import QtWidgets

from nodes.core import Spec

def augment_infocard_footer(card, footer_layout) -> bool:
    """
    Add a small button that sets which parameter is shown expanded INSIDE the node.
    This writes to `node._featured_param` so it matches the main script’s logic.
    """
    table = getattr(card, "_param_table", None)
    node  = getattr(card, "_node_ref",  None)
    if table is None or node is None:
        return False

    btn = QtWidgets.QPushButton("\N{EYE} Set display to selected param")
    btn.setToolTip("Show the selected parameter’s value inside the Note node body.")

    def _apply():
        r = table.currentRow()
        if r < 0:
            QtWidgets.QToolTip.showText(card.mapToGlobal(card.rect().center()), "Select a row first.", card, card.rect(), 1200)
            return
        name_item = table.item(r, 0)
        pname = (name_item.text() if name_item else "").strip()
        if not pname:
            return
        # Use the SAME attribute the main script reads
        setattr(node, "_featured_param", pname)

        # Ask the scene to rebuild the node item immediately
        sc = getattr(card, "_graph_scene", None)
        if sc:
            sc.refresh_node_widget(node.name)

    btn.clicked.connect(_apply)
    footer_layout.addWidget(btn)
    return True

# No render_node_body here (we keep the node body clean; inline expansion only)
NOTE_SPEC = Spec(
    stripe_color="#f59e0b",
    augment_infocard_footer=augment_infocard_footer,
)


