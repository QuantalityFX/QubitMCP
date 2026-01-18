# echograph/ui/actions.py
from __future__ import annotations

from echograph.qt_compat import QtCore, QtGui, QtWidgets
from echograph.ui import hotkeys_config


def wire_big_editor_for_lineedit(node_item, edit: QtWidgets.QLineEdit, param_name: str):
    # Ensure it can take focus inside GraphicsProxyWidget setups
    try:
        edit.setFocusPolicy(QtCore.Qt.StrongFocus)
    except Exception:
        pass

    def open_big_editor():
        node_item._open_big_param_editor(f"Edit: {param_name}", edit.text(), edit)

    # Context menu item showing current key
    seq = hotkeys_config.keyseq("big_editor", "Ctrl+B")
    act = QtGui.QAction(f"Open Big Editor ({seq})", edit)
    act.triggered.connect(open_big_editor)
    edit.addAction(act)
    edit.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)

    # Register with main window (delayed so view/window exists)
    def _late_register():
        try:
            win = edit.window()
            if win and hasattr(win, "_register_bigedit_target"):
                win._register_bigedit_target(edit, node_item, param_name)
        except Exception:
            pass

    QtCore.QTimer.singleShot(0, _late_register)
