# nodes/switch/spec.py
from nodes.core import Spec

def augment_infocard_footer(card, footer_layout) -> bool:
    """
    Adds simple controls to view/change the active branch from the Info card.
    Relies on main-graph logic already present (GraphScene + NodeItem).
    """
    try:
        from PySide6 import QtWidgets
    except Exception:
        from PySide2 import QtWidgets

    node = getattr(card, "_node_ref", None)
    sc   = getattr(card, "_graph_scene", None)
    if not node or not sc:
        return False

    name = node.name

    def _get_item():
        return getattr(sc, "_node_items", {}).get(name)

    def _counts():
        it = _get_item()
        if not it: return 0, 0
        n  = len(it.model.switch_inputs or [])
        ix = max(0, min(int(it.model.switch_index or 0), max(0, n-1)))
        return n, ix

    # UI: "Active branch X/Y" + ◀ ▶ buttons
    count, idx = _counts()
    label = QtWidgets.QLabel(f"Active branch {idx+1}/{max(1,count)}")

    def _update_label():
        c, i = _counts()
        label.setText(f"Active branch {i+1}/{max(1,c)}")

    def _set_index(new_idx: int):
        it = _get_item()
        if not it: return
        n = len(it.model.switch_inputs or [])
        if n == 0: return
        it.model.switch_index = max(0, min(new_idx, n-1))
        # refresh widget + path visuals
        try: sc._refresh_switch_widget(it)
        except Exception: pass
        try:
            if sc._current_output_name:
                sc.recompute_active_path(sc._current_output_name)
        except Exception:
            pass
        _update_label()

    prev_btn = QtWidgets.QPushButton("◀")
    next_btn = QtWidgets.QPushButton("▶")
    prev_btn.setToolTip("Previous branch")
    next_btn.setToolTip("Next branch")

    def _prev():
        _, i = _counts()
        _set_index(i-1)

    def _next():
        c, i = _counts()
        _set_index(i+1 if i+1 < c else i)

    prev_btn.clicked.connect(_prev)
    next_btn.clicked.connect(_next)

    footer_layout.addWidget(label)
    footer_layout.addWidget(prev_btn)
    footer_layout.addWidget(next_btn)

    return True

SWITCH_SPEC = Spec(
    stripe_color = "#06b6d4",               # cyan
    augment_infocard_footer = augment_infocard_footer,
)
