# echograph/ui/actions.py
from __future__ import annotations
from echograph.qt_compat import QtCore, QtGui, QtWidgets
from echograph.ui import hotkeys_config

def create_comment_group_from_window(win) -> bool:
    try:
        graph_scene = getattr(win, "scene", None)
        if graph_scene and hasattr(graph_scene, "create_comment_group_from_selection"):
            graph_scene.create_comment_group_from_selection()
            return True
    except Exception:
        pass
    return False


def delete_selected_nodes_from_window(win) -> bool:
    try:
        graph_scene = getattr(win, "scene", None)
        if graph_scene and hasattr(graph_scene, "delete_selected_nodes"):
            graph_scene.delete_selected_nodes()
            return True
    except Exception:
        pass
    return False

def open_big_editor_from_window(win) -> bool:
    """
    Called by the app-level eventFilter when the big_editor hotkey is pressed.
    Uses win._bigedit_last and win._bigedit_registry to open the BigTextEditDialog.
    """
    try:
        reg = getattr(win, "_bigedit_registry", {}) or {}
        edit = getattr(win, "_bigedit_last", None)

        # Fallback: pick any focused registered edit
        if edit is None or edit not in reg:
            for w in list(reg.keys()):
                try:
                    if w and w.hasFocus():
                        edit = w
                        break
                except Exception:
                    pass

        if edit is None or edit not in reg:
            return False

        node_item, param_name = reg.get(edit, (None, None))
        if node_item is None:
            return False

        node_item._open_big_param_editor(f"Edit: {param_name}", edit.text(), edit)
        return True
    except Exception:
        return False

def wire_big_editor_for_lineedit(node_item, edit: QtWidgets.QLineEdit, param_name: str):
    try:
        edit.setFocusPolicy(QtCore.Qt.StrongFocus)
    except Exception:
        pass

    def open_big_editor():
        node_item._open_big_param_editor(f"Edit: {param_name}", edit.text(), edit)

    seq = hotkeys_config.keyseq("big_editor", "Ctrl+B")
    act = QtGui.QAction(f"Open Big Editor ({seq})", edit)
    act.triggered.connect(open_big_editor)
    edit.addAction(act)
    edit.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)

    def _find_main_window():
        # 1) Prefer the GraphicsView -> window (best in proxy-widget setup)
        try:
            sc = node_item.scene()
            if sc:
                views = sc.views()
                if views:
                    win = views[0].window()
                    if win and hasattr(win, "_register_bigedit_target"):
                        return win
        except Exception:
            pass

        # 2) Fallback: activeWindow
        try:
            win = QtWidgets.QApplication.activeWindow()
            if win and hasattr(win, "_register_bigedit_target"):
                return win
        except Exception:
            pass

        # 3) Last resort: scan top-level widgets
        try:
            for w in QtWidgets.QApplication.topLevelWidgets():
                if hasattr(w, "_register_bigedit_target"):
                    return w
        except Exception:
            pass

        return None

    def _late_register():
        try:
            win = _find_main_window()
            if win:
                win._register_bigedit_target(edit, node_item, param_name)
        except Exception:
            pass

    QtCore.QTimer.singleShot(0, _late_register)
    QtCore.QTimer.singleShot(50, _late_register)  # extra tick for slow builds

