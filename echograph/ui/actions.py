# echograph/ui/actions.py
from __future__ import annotations

from echograph.qt_compat import QtCore, QtWidgets


def open_big_editor_from_window(win) -> bool:
    """
    Uses win._bigedit_registry and win._bigedit_last.
    Returns True if it opened an editor.
    """
    reg = getattr(win, "_bigedit_registry", {})
    target = getattr(win, "_bigedit_last", None)
    if not (target and target in reg):
        return False

    node_item, param_name = reg[target]
    try:
        node_item._open_big_param_editor(f"Edit: {param_name}", target.text(), target)
        return True
    except Exception:
        return False
