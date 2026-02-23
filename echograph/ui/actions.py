# echograph/ui/actions.py
from __future__ import annotations
import json
import time
from pathlib import Path
from echograph.qt_compat import QtCore, QtGui, QtWidgets
from echograph.constants import script_dir
from echograph.ui import hotkeys_config

def _focus_is_text_input() -> bool:
    try:
        fw = QtWidgets.QApplication.focusWidget()
        if fw is None:
            return False
        text_widgets = (
            QtWidgets.QLineEdit,
            QtWidgets.QTextEdit,
            QtWidgets.QPlainTextEdit,
            QtWidgets.QTextBrowser,
            QtWidgets.QSpinBox,
            QtWidgets.QDoubleSpinBox,
            QtWidgets.QComboBox,
        )
        if isinstance(fw, QtWidgets.QComboBox):
            return bool(fw.isEditable())
        return isinstance(fw, text_widgets)
    except Exception:
        return False


def copy_selected_nodes_from_window(win) -> bool:
    try:
        if _focus_is_text_input():
            return False  # let Qt handle normal text copy
        graph_scene = getattr(win, "scene", None)
        if graph_scene and hasattr(graph_scene, "copy_selection_to_clipboard"):
            graph_scene.copy_selection_to_clipboard()
            return True
    except Exception:
        pass
    return False


def paste_nodes_from_window(win) -> bool:
    try:
        if _focus_is_text_input():
            return False  # let Qt handle normal text paste
        graph_scene = getattr(win, "scene", None)
        if graph_scene and hasattr(graph_scene, "paste_from_clipboard"):
            graph_scene.paste_from_clipboard()
            return True
    except Exception:
        pass
    return False

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
        try:
            graph_scene = node_item.scene()
            if graph_scene:
                views = graph_scene.views()
                if views:
                    win = views[0].window()
                    if win and hasattr(win, "_register_bigedit_target"):
                        return win
        except Exception:
            pass

        try:
            win = QtWidgets.QApplication.activeWindow()
            if win and hasattr(win, "_register_bigedit_target"):
                return win
        except Exception:
            pass

        try:
            for w in QtWidgets.QApplication.topLevelWidgets():
                if hasattr(w, "_register_bigedit_target"):
                    return w
        except Exception:
            pass

        return None

    def _on_destroyed(*_):
        try:
            win = _find_main_window()
            if win and hasattr(win, "_bigedit_registry"):
                win._bigedit_registry.pop(edit, None)
                if getattr(win, "_bigedit_last", None) is edit:
                    win._bigedit_last = None
        except Exception:
            pass

    try:
        edit.destroyed.connect(_on_destroyed)
    except Exception:
        pass

    def _late_register():
        try:
            win = _find_main_window()
            if win:
                win._register_bigedit_target(edit, node_item, param_name)
        except Exception:
            pass

    QtCore.QTimer.singleShot(0, _late_register)
    QtCore.QTimer.singleShot(50, _late_register)


_HISTORY_LIMIT = 20


def _history_path() -> Path:
    try:
        root = script_dir()
    except Exception:
        root = Path.cwd()
    return Path(root) / "logs" / "history.log"


def _log_history(entry: dict) -> None:
    try:
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def init_history_log() -> None:
    try:
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")
    except Exception:
        pass


def _ensure_scene_rev(win, node) -> int | None:
    if node is None:
        return None
    rev = getattr(node, "_rev_number", None)
    if rev is None:
        rev = getattr(node, "rev_number", None)
    if rev is None:
        try:
            counter = int(getattr(win, "_scene_rev_counter", 0)) + 1
        except Exception:
            counter = 1
        try:
            setattr(win, "_scene_rev_counter", counter)
        except Exception:
            pass
        rev = counter
        try:
            setattr(node, "_rev_number", rev)
        except Exception:
            pass
    try:
        return int(rev)
    except Exception:
        return None


def _clean_xform(xf: dict) -> dict:
    try:
        pos = tuple(float(v) for v in xf.get("pos", (0.0, 0.0, 0.0)))
    except Exception:
        pos = (0.0, 0.0, 0.0)
    try:
        rot = tuple(float(v) for v in xf.get("rot", (0.0, 0.0, 0.0)))
    except Exception:
        rot = (0.0, 0.0, 0.0)
    try:
        scl = tuple(float(v) for v in xf.get("scl", (1.0, 1.0, 1.0)))
    except Exception:
        scl = (1.0, 1.0, 1.0)
    return {"pos": pos, "rot": rot, "scl": scl}


def _xform_tuple(xf: dict) -> tuple:
    try:
        clean = _clean_xform(xf)
        return tuple(clean["pos"]) + tuple(clean["rot"]) + tuple(clean["scl"])
    except Exception:
        return ()


def _push_xform_history_entry(win, entry: dict) -> None:
    undo = getattr(win, "_scene_xform_undo", None)
    redo = getattr(win, "_scene_xform_redo", None)
    if not isinstance(undo, list):
        undo = []
        setattr(win, "_scene_xform_undo", undo)
    if not isinstance(redo, list):
        redo = []
        setattr(win, "_scene_xform_redo", redo)
    undo.append(entry)
    if len(undo) > _HISTORY_LIMIT:
        del undo[:-_HISTORY_LIMIT]
    redo.clear()


def _record_xform_entry(win, history_node, owner: str, before: dict, after: dict, *, entry_type: str) -> None:
    if win is None or history_node is None:
        return
    if not owner:
        return
    before = _clean_xform(before or {})
    after = _clean_xform(after or {})
    try:
        if _xform_tuple(before) == _xform_tuple(after):
            return
    except Exception:
        pass
    try:
        if getattr(win, "_xform_history_busy", False):
            return
    except Exception:
        pass
    rev = _ensure_scene_rev(win, history_node)
    entry = {
        "ts": time.time(),
        "entry_type": str(entry_type or "scene_asset_xform"),
        "scene_rev": rev,
        "scene_name": getattr(history_node, "name", None),
        "owner": str(owner),
        "before": before,
        "after": after,
    }
    _push_xform_history_entry(win, entry)
    _log_history({"action": "record", **entry})


def record_scene_xform(win, scene_node, owner: str, before: dict, after: dict) -> None:
    _record_xform_entry(
        win,
        scene_node,
        owner,
        before,
        after,
        entry_type="scene_asset_xform",
    )


def record_transforms_node_xform(win, transform_node, owner: str, before: dict, after: dict) -> None:
    _record_xform_entry(
        win,
        transform_node,
        owner,
        before,
        after,
        entry_type="transforms_node_xform",
    )


def _find_scene_node_by_rev(win, scene_rev: int | None, scene_name: str | None):
    scene = getattr(win, "scene", None)
    if scene is None:
        return None
    try:
        for item in getattr(scene, "_node_items", {}).values():
            node = getattr(item, "model", None)
            if node is None:
                continue
            rev = getattr(node, "_rev_number", None)
            if rev is None:
                rev = getattr(node, "rev_number", None)
            if scene_rev is not None and rev is not None and int(rev) == int(scene_rev):
                return node
    except Exception:
        pass
    if scene_name:
        try:
            node = getattr(scene, "_nodes_by_name", {}).get(scene_name)
            if node is not None:
                return node
        except Exception:
            pass
    return None


def _vec3_csv(vals, default):
    try:
        x, y, z = vals
    except Exception:
        x, y, z = default
    try:
        return f"{float(x):.6f},{float(y):.6f},{float(z):.6f}"
    except Exception:
        dx, dy, dz = default
        return f"{float(dx):.6f},{float(dy):.6f},{float(dz):.6f}"


def _set_param_value(params: list, name: str, value: str) -> list:
    key = (name or "").strip().lower()
    found = False
    out = list(params or [])
    for p in out:
        try:
            if (p.get("name", "") or "").strip().lower() == key:
                p["value"] = value
                found = True
                break
        except Exception:
            continue
    if not found:
        out.append({"name": name, "value": value})
    return out


def _apply_transforms_node_xform_entry(win, node, owner: str, xf: dict) -> bool:
    kind = (getattr(node, "kind", "") or "").strip().lower()
    if kind != "transforms":
        return False

    clean = _clean_xform(xf or {})
    try:
        params = list(getattr(node, "params", None) or [])
    except Exception:
        params = []
    params = _set_param_value(params, "pos", _vec3_csv(clean.get("pos", (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)))
    params = _set_param_value(params, "rot", _vec3_csv(clean.get("rot", (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)))
    params = _set_param_value(params, "scl", _vec3_csv(clean.get("scl", (1.0, 1.0, 1.0)), (1.0, 1.0, 1.0)))

    try:
        setattr(node, "params", params)
    except Exception:
        pass

    try:
        scene = getattr(win, "scene", None)
        setp = getattr(scene, "set_node_params", None) if scene is not None else None
        node_name = str(getattr(node, "name", "") or "")
        if callable(setp) and node_name:
            setp(node_name, params, rebuild=False, emit=False)
    except Exception:
        pass

    glv = getattr(win, "gl_view", None)
    if glv is None:
        return True

    try:
        renderer = getattr(glv, "_mgl_renderer", None) or glv
        setf = getattr(renderer, "_mgl_set_scene_asset_xform", None)
        if callable(setf):
            setf(
                owner,
                pos=clean.get("pos", (0.0, 0.0, 0.0)),
                rot=clean.get("rot", (0.0, 0.0, 0.0)),
                scl=clean.get("scl", (1.0, 1.0, 1.0)),
                apply_to_scene_models=True,
                use_splat_xform=False,
            )
        try:
            if hasattr(win, "update_scene_asset_xform"):
                win.update_scene_asset_xform(owner)
        except Exception:
            pass
        glv._xform_gizmo_owner = owner
        glv._xform_gizmo_owner_kind = "mesh"
        glv._xform_gizmo_pos_locked = False
        glv._xform_gizmo_pos = tuple(clean.get("pos", (0.0, 0.0, 0.0)))
        try:
            glv.update()
        except Exception:
            pass
    except Exception:
        pass
    return True


def _apply_xform_entry(win, entry: dict, use_before: bool) -> bool:
    try:
        entry_type = str(entry.get("entry_type", "scene_asset_xform") or "scene_asset_xform").strip().lower()
        scene_rev = entry.get("scene_rev")
        scene_name = entry.get("scene_name")
        owner = entry.get("owner")
        xf = entry.get("before") if use_before else entry.get("after")
    except Exception:
        return False
    if not owner or not isinstance(xf, dict):
        return False
    node = _find_scene_node_by_rev(win, scene_rev, scene_name)
    if node is None:
        return False

    if entry_type in ("transforms_node_xform", "transform_node_xform", "transforms_node"):
        return _apply_transforms_node_xform_entry(win, node, str(owner), xf)

    # Update the node model so workflow save matches undo/redo.
    try:
        xforms = getattr(node, "_scene_xforms", None)
        if not isinstance(xforms, dict):
            xforms = {}
        xforms = dict(xforms)
        xforms[str(owner)] = {
            "pos": list(xf.get("pos", (0.0, 0.0, 0.0))),
            "rot": list(xf.get("rot", (0.0, 0.0, 0.0))),
            "scl": list(xf.get("scl", (1.0, 1.0, 1.0))),
        }
        setattr(node, "_scene_xforms", xforms)
    except Exception:
        pass

    # Apply to viewport if this scene is currently active.
    try:
        active = getattr(win, "_active_scene_node", None)
        active_rev = _ensure_scene_rev(win, active) if active is not None else None
    except Exception:
        active_rev = None
    try:
        if active_rev is not None and scene_rev is not None and int(active_rev) != int(scene_rev):
            return True
    except Exception:
        pass

    glv = getattr(win, "gl_view", None)
    if glv is None:
        return True
    try:
        renderer = getattr(glv, "_mgl_renderer", None) or glv
        is_splat = False
        try:
            splat_map = getattr(renderer, "_mgl_scene_splats", None)
            if isinstance(splat_map, dict) and owner in splat_map:
                is_splat = True
        except Exception:
            is_splat = False
        setf = getattr(renderer, "_mgl_set_scene_asset_xform", None)
        if callable(setf):
            setf(
                owner,
                pos=xf.get("pos", (0.0, 0.0, 0.0)),
                rot=xf.get("rot", (0.0, 0.0, 0.0)),
                scl=xf.get("scl", (1.0, 1.0, 1.0)),
                apply_to_scene_models=not is_splat,
                use_splat_xform=bool(is_splat),
            )
        try:
            if hasattr(win, "update_scene_asset_xform"):
                win.update_scene_asset_xform(owner)
        except Exception:
            pass
        # Keep gizmo aligned with the undone/redone asset.
        try:
            glv._xform_gizmo_owner = owner
            glv._xform_gizmo_owner_kind = "splat" if is_splat else "mesh"
            xf_pos = tuple(xf.get("pos", (0.0, 0.0, 0.0)))
            if is_splat:
                pivot = None
                try:
                    bounds_map = (
                        getattr(renderer, "_mgl_scene_splats_bounds_local", None)
                        or getattr(renderer, "_mgl_scene_splat_bounds_by_owner", None)
                    )
                    if isinstance(bounds_map, dict) and owner in bounds_map:
                        mins, maxs = bounds_map.get(owner) or (None, None)
                        if mins is not None and maxs is not None:
                            pivot = (
                                (float(mins[0]) + float(maxs[0])) * 0.5,
                                (float(mins[1]) + float(maxs[1])) * 0.5,
                                (float(mins[2]) + float(maxs[2])) * 0.5,
                            )
                except Exception:
                    pivot = None
                if pivot is not None:
                    pos = (
                        float(xf_pos[0] + pivot[0]),
                        float(xf_pos[1] + pivot[1]),
                        float(xf_pos[2] + pivot[2]),
                    )
                else:
                    pos = xf_pos
            else:
                pos = xf_pos
            glv._xform_gizmo_pos_locked = False
            glv._xform_gizmo_pos = pos
        except Exception:
            pass
        try:
            if hasattr(win, "select_scene_asset"):
                win.select_scene_asset(owner)
        except Exception:
            pass
        try:
            glv.update()
        except Exception:
            pass
    except Exception:
        pass
    return True


def undo_scene_xform(win) -> bool:
    if win is None:
        return False
    if _focus_is_text_input():
        return False
    undo = getattr(win, "_scene_xform_undo", None)
    if not isinstance(undo, list) or not undo:
        return False
    redo = getattr(win, "_scene_xform_redo", None)
    if not isinstance(redo, list):
        redo = []
        setattr(win, "_scene_xform_redo", redo)
    entry = undo.pop()
    try:
        win._xform_history_busy = True
        ok = _apply_xform_entry(win, entry, use_before=True)
    finally:
        win._xform_history_busy = False
    if ok:
        redo.append(entry)
        if len(redo) > _HISTORY_LIMIT:
            del redo[:-_HISTORY_LIMIT]
        _log_history({"action": "undo", **entry})
    return ok


def redo_scene_xform(win) -> bool:
    if win is None:
        return False
    if _focus_is_text_input():
        return False
    redo = getattr(win, "_scene_xform_redo", None)
    if not isinstance(redo, list) or not redo:
        return False
    undo = getattr(win, "_scene_xform_undo", None)
    if not isinstance(undo, list):
        undo = []
        setattr(win, "_scene_xform_undo", undo)
    entry = redo.pop()
    try:
        win._xform_history_busy = True
        ok = _apply_xform_entry(win, entry, use_before=False)
    finally:
        win._xform_history_busy = False
    if ok:
        undo.append(entry)
        if len(undo) > _HISTORY_LIMIT:
            del undo[:-_HISTORY_LIMIT]
        _log_history({"action": "redo", **entry})
    return ok
