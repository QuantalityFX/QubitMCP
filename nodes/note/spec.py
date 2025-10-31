# nodes/note/spec.py
from types import SimpleNamespace

# We’ll import Qt lazily inside the augment so PySide6/PySide2 differences are handled by your host.
def _get_param(params: list, name: str, default: str = "") -> str:
    nm = (name or "").strip().lower()
    for p in (params or []):
        if (p.get("name", "") or "").strip().lower() == nm:
            return p.get("value", "") or ""
    return default

def _set_param(card, name: str, value: str):
    """Use the GraphScene public API to persist param updates and refresh the card."""
    sc = getattr(card, "_graph_scene", None)
    node = getattr(card, "_node_ref", None)
    if not sc or not node:
        return
    params = list(node.params or [])
    nm = (name or "").strip().lower()

    # upsert
    found = False
    for p in params:
        if (p.get("name","") or "").strip().lower() == nm:
            p["value"] = value
            found = True
            break
    if not found:
        params.append({"name": name, "value": value})

    sc.set_node_params(node.name, params)   # updates model + NodeItem
    node.params = params                     # keep local ref in sync
    try:
        card.refresh_params_from_model()     # refresh InfoCard's table
    except Exception:
        pass

def augment_infocard_footer(card, footer_layout) -> bool:
    """
    Add Note-specific buttons into the InfoCard footer.
    Return True to indicate we've added real controls.
    """
    # Late-import Qt to play nice with PySide6/PySide2 host
    try:
        from PySide6 import QtWidgets, QtGui, QtCore
    except Exception:
        from PySide2 import QtWidgets, QtGui, QtCore

    node = getattr(card, "_node_ref", None)
    if not node or (node.kind or "").lower() != "note":
        return False

    # --- Edit Note… button ---
    btn_edit = QtWidgets.QPushButton("Edit Note…")
    btn_edit.setToolTip("Open a simple editor for the note text")

    def _edit_note():
        # pull current value
        current = _get_param(node.params, "text", "")
        # tiny, local dialog (keeps everything self-contained)
        dlg = QtWidgets.QDialog(card)
        dlg.setWindowTitle(f"Edit Note — {node.name}")
        dlg.setModal(True)
        dlg.resize(520, 360)
        v = QtWidgets.QVBoxLayout(dlg)
        edit = QtWidgets.QPlainTextEdit()
        edit.setPlainText(current)
        v.addWidget(edit, 1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        v.addWidget(bb)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        if dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec():
            _set_param(card, "text", edit.toPlainText())

    btn_edit.clicked.connect(_edit_note)
    footer_layout.addWidget(btn_edit)

    # --- Copy to Clipboard button ---
    btn_copy = QtWidgets.QPushButton("Copy")
    btn_copy.setToolTip("Copy the note text to the clipboard")

    def _copy():
        txt = _get_param(node.params, "text", "")
        QtWidgets.QApplication.clipboard().setText(txt)

        # tiny toast-y tooltip near cursor
        try:
            QtWidgets.QToolTip.showText(
                QtGui.QCursor.pos(), "Note copied to clipboard", card, card.rect(), 1000
            )
        except Exception:
            pass

    btn_copy.clicked.connect(_copy)
    footer_layout.addWidget(btn_copy)

    return True

Spec = SimpleNamespace(
    kind="note",
    stripe_color="#f59e0b",  # amber
    augment_infocard_footer=augment_infocard_footer,
)
