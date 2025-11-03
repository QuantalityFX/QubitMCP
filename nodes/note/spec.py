# nodes/note/spec.py
from nodes.core import NodeKindSpec

# --- helpers preserved from your version ---
def _get_param(params: list, name: str, default: str = "") -> str:
    nm = (name or "").strip().lower()
    for p in (params or []):
        if (p.get("name", "") or "").strip().lower() == nm:
            return p.get("value", "") or ""
    return default

def _set_param(card, name: str, value: str):
    sc = getattr(card, "_graph_scene", None)
    node = getattr(card, "_node_ref", None)
    if not sc or not node:
        return
    params = list(node.params or [])
    nm = (name or "").strip().lower()

    # upsert
    for p in params:
        if (p.get("name","") or "").strip().lower() == nm:
            p["value"] = value
            break
    else:
        params.append({"name": name, "value": value})

    sc.set_node_params(node.name, params)
    node.params = params
    try:
        card.refresh_params_from_model()
    except Exception:
        pass

# --- InfoCard footer augment (unchanged behavior) ---
def augment_infocard_footer(card, footer_layout) -> bool:
    try:
        from PySide6 import QtWidgets, QtGui, QtCore
    except Exception:
        from PySide2 import QtWidgets, QtGui, QtCore

    node = getattr(card, "_node_ref", None)
    if not node or (node.kind or "").lower() != "note":
        return False

    # Edit Note…
    btn_edit = QtWidgets.QPushButton("Edit Note…")
    btn_edit.setToolTip("Open a simple editor for the note text")

    def _edit_note():
        current = _get_param(node.params, "text", "")
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

    # Copy
    btn_copy = QtWidgets.QPushButton("Copy")
    btn_copy.setToolTip("Copy the note text to the clipboard")

    def _copy():
        txt = _get_param(node.params, "text", "")
        QtWidgets.QApplication.clipboard().setText(txt)
        try:
            QtWidgets.QToolTip.showText(
                QtGui.QCursor.pos(), "Note copied to clipboard", card, card.rect(), 1000
            )
        except Exception:
            pass

    btn_copy.clicked.connect(_copy)
    footer_layout.addWidget(btn_copy)

    return True  # we added real controls

# --- final spec object in the new format ---
NOTE_SPEC = NodeKindSpec(
    stripe_color="#f59e0b",                # amber
    augment_infocard_footer=augment_infocard_footer,
)
