from __future__ import annotations

import json
from pathlib import Path
from typing import List

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec


_MODEL_OBJECT_CACHE: dict[tuple[str, int, int], List[str]] = {}
MODELER_WIDGET_BODY_H = 64
MODELER_NODE_BODY_H = 82


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value") or "")
    return ""


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        try:
            params = list(params)
        except Exception:
            params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    entry = None
    for param in params:
        if isinstance(param, dict) and (param.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = param
            break
    if entry is None:
        entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(entry)
    hidden = {
        part.strip().lower()
        for part in str(entry.get("value") or "").split(",")
        if part.strip()
    }
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    try:
        setattr(model, "params", params)
    except Exception:
        pass


def _name_list_from_raw(raw) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, (list, tuple, set)):
        values = parsed
    else:
        values = [part.strip() for part in text.split(",")]
    out: List[str] = []
    seen = set()
    for value in values:
        name = str(value or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _hidden_names(model) -> set[str]:
    return {name.lower() for name in _name_list_from_raw(_param_value(model, "hidden_submeshes"))}


def _submesh_name(raw_name: str, index: int) -> str:
    name = str(raw_name or "").strip()
    return name or f"mesh_{int(index)}"


def _stat_key(path_text: str) -> tuple[str, int, int] | None:
    try:
        path = Path(path_text)
        if not path.exists():
            return None
        stat = path.stat()
        return str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size)
    except Exception:
        return None


def _model_object_names(path_text: str) -> tuple[List[str], str]:
    path_text = str(path_text or "").strip()
    if not path_text:
        return [], "No model connected."
    key = _stat_key(path_text)
    if key is None:
        return [], "Model file not found."
    cached = _MODEL_OBJECT_CACHE.get(key)
    if cached is not None:
        return list(cached), ""
    path = Path(key[0])
    names: List[str] = []
    try:
        ext = path.suffix.lower()
        mesh_arrays = None
        if ext == ".fbx":
            from echograph.ui.gl_loaders import load_fbx_mesh_arrays_pyassimp

            mesh_arrays = load_fbx_mesh_arrays_pyassimp(path)
        elif ext in {".gltf", ".glb"}:
            from echograph.ui.gl_loaders import load_gltf_mesh_arrays

            mesh_arrays = load_gltf_mesh_arrays(path)
        submeshes = list(getattr(mesh_arrays, "submeshes", None) or []) if mesh_arrays is not None else []
        seen = set()
        for idx, sub in enumerate(submeshes):
            name = _submesh_name(getattr(sub, "name", ""), idx)
            key_name = name.lower()
            if key_name in seen:
                continue
            seen.add(key_name)
            names.append(name)
    except Exception as exc:
        return [], f"Could not read model names: {exc}"
    _MODEL_OBJECT_CACHE[key] = list(names)
    if not names:
        return [], "No named objects found."
    return names, ""


def _node_item_for_card(card):
    scene = getattr(card, "_graph_scene", None)
    node = getattr(card, "_node_ref", None)
    if scene is None or node is None:
        return None
    try:
        return getattr(scene, "_node_items", {}).get(getattr(node, "name", "") or "")
    except Exception:
        return None


def _resolve_connected_model(scene, item, model=None) -> tuple[object | None, str, str]:
    model = model if model is not None else getattr(item, "model", None)
    path = ""
    upstream_item = None
    upstream_kind = ""
    if scene is not None and item is not None:
        try:
            from nodes.scene import spec as scene_spec  # type: ignore

            resolver = getattr(scene_spec, "_resolve_input_item", None)
            if callable(resolver):
                upstream_item, upstream_kind, resolved = resolver(scene, item, {"mesh", "path", "source"})
                path = str(resolved or "").strip()
        except Exception:
            path = ""
    if not path:
        path = _param_value(model, "path")
    if path and model is not None:
        _set_param_direct(model, "path", path)
    return upstream_item, str(upstream_kind or "").strip().lower(), path


def _resolve_connected_model_path(card) -> str:
    scene = getattr(card, "_graph_scene", None)
    item = _node_item_for_card(card)
    node = getattr(card, "_node_ref", None)
    _upstream_item, _upstream_kind, path = _resolve_connected_model(scene, item, node)
    return path


def _modeler_scene_assets(scene, item) -> list[dict]:
    model = getattr(item, "model", None)
    upstream_item, upstream_kind, path = _resolve_connected_model(scene, item, model)
    path = str(path or "").strip()
    if not path:
        return []
    try:
        ext = Path(path).suffix.lower()
    except Exception:
        ext = ""
    if not ext:
        return []
    node_name = str(getattr(model, "name", "") or "").strip() or Path(path).stem
    asset = {
        "path": path,
        "texture": "",
        "node": node_name,
        "ext": ext,
        "visible": True,
        "xform": None,
    }
    hidden = _name_list_from_raw(_param_value(model, "hidden_submeshes"))
    if hidden:
        asset["hidden_submeshes"] = list(hidden)
    try:
        from nodes.scene import spec as scene_spec  # type: ignore

        upstream_model = getattr(upstream_item, "model", None) if upstream_item is not None else None
        if upstream_kind in getattr(scene_spec, "_FBX_KIND_ALIASES", set()):
            rig_fn = getattr(scene_spec, "_fbx_import_rig_context", None)
            rig_ctx = rig_fn(upstream_model) if callable(rig_fn) else None
            if isinstance(rig_ctx, dict):
                asset["fbx_rig_context"] = rig_ctx
        elif upstream_kind in getattr(scene_spec, "_MOCAP_KIND_ALIASES", set()):
            rig_fn = getattr(scene_spec, "_mocap_import_rig_context", None)
            rig_ctx = rig_fn(upstream_model) if callable(rig_fn) else None
            if isinstance(rig_ctx, dict):
                asset["fbx_rig_context"] = rig_ctx
    except Exception:
        pass
    return [asset]


def _window_for_scene(scene):
    if scene is None:
        return None
    try:
        views = scene.views()
        if views:
            return views[0].window()
    except Exception:
        return None
    return None


def _open_modeler_preview_for_item(item, *, frame: bool = True) -> bool:
    if item is None:
        return False
    scene = None
    try:
        scene = item.scene()
    except Exception:
        scene = None
    win = _window_for_scene(scene)
    handler = getattr(win, "open_scene_assets", None) if win is not None else None
    if not callable(handler):
        return False
    assets = _modeler_scene_assets(scene, item)
    if not assets:
        return False
    try:
        setattr(win, "_active_scene_node", getattr(item, "model", None))
        setattr(win, "_active_scene_preview_context", None)
        setattr(win, "_opening_scene_assets_from_scene_node", True)
    except Exception:
        pass
    try:
        return bool(handler(assets, frame=bool(frame)))
    except TypeError:
        try:
            return bool(handler(assets))
        except Exception:
            return False
    except Exception:
        return False
    finally:
        try:
            setattr(win, "_opening_scene_assets_from_scene_node", False)
        except Exception:
            pass


def _open_modeler_preview_for_card(card, *, frame: bool = True) -> bool:
    return _open_modeler_preview_for_item(_node_item_for_card(card), frame=frame)


def _set_param_direct(model, name: str, value: str) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value or "")
            break
    else:
        params.append({"name": name, "value": str(value or "")})
    try:
        setattr(model, "params", params)
    except Exception:
        pass


def _set_hidden_names(card, hidden: set[str]) -> None:
    node = getattr(card, "_node_ref", None)
    if node is None:
        return
    hidden_values = sorted(str(name) for name in hidden if str(name).strip())
    params = list(getattr(node, "params", None) or [])
    key = "hidden_submeshes"
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = json.dumps(hidden_values)
            break
    else:
        params.append({"name": key, "value": json.dumps(hidden_values)})
    hidden_entry = None
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == "__ui_hidden_params":
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(hidden_entry)
    hidden_ui = {
        part.strip().lower()
        for part in str(hidden_entry.get("value") or "").split(",")
        if part.strip()
    }
    hidden_ui.update({"path", "hidden_submeshes"})
    hidden_entry["value"] = ",".join(sorted(hidden_ui))

    scene = getattr(card, "_graph_scene", None)
    setter = getattr(scene, "set_node_params", None) if scene is not None else None
    if callable(setter):
        try:
            setter(getattr(node, "name", "") or "", params, rebuild=False, emit=True)
            return
        except Exception:
            pass
    try:
        setattr(node, "params", params)
    except Exception:
        pass
    try:
        if scene is not None and hasattr(scene, "paramChanged"):
            scene.paramChanged.emit(getattr(node, "name", "") or "", list(params))
    except Exception:
        pass


def _eye_icon(visible: bool) -> QtGui.QIcon:
    try:
        from nodes.scene import spec as scene_spec  # type: ignore

        icon_fn = getattr(scene_spec, "_eye_icon", None)
        if callable(icon_fn):
            return icon_fn(bool(visible))
    except Exception:
        pass
    style = QtWidgets.QApplication.style()
    if style is not None:
        return style.standardIcon(
            QtWidgets.QStyle.SP_DialogYesButton if visible else QtWidgets.QStyle.SP_DialogNoButton
        )
    return QtGui.QIcon()


def _reload_active_scene(scene, fallback_item=None) -> bool:
    if scene is None:
        return False
    try:
        views = scene.views()
        win = views[0].window() if views else None
    except Exception:
        win = None
    if win is None:
        return False
    active_model = getattr(win, "_active_scene_node", None)
    active_name = str(getattr(active_model, "name", "") or "").strip()
    if not active_name:
        if fallback_item is not None:
            return _open_modeler_preview_for_item(fallback_item, frame=False)
        return False
    try:
        scene_item = getattr(scene, "_node_items", {}).get(active_name)
    except Exception:
        scene_item = None
    if scene_item is None:
        if fallback_item is not None:
            return _open_modeler_preview_for_item(fallback_item, frame=False)
        return False
    handler = getattr(win, "open_scene_assets", None)
    if not callable(handler):
        return False
    active_kind = str(getattr(active_model, "kind", "") or "").strip().lower()
    if active_kind == "modeler":
        assets = _modeler_scene_assets(scene, scene_item)
    else:
        collector = getattr(scene_item, "_collect_scene_assets", None)
        if not callable(collector):
            if fallback_item is not None:
                return _open_modeler_preview_for_item(fallback_item, frame=False)
            return False
        try:
            assets = list(collector() or [])
        except Exception:
            if fallback_item is not None:
                return _open_modeler_preview_for_item(fallback_item, frame=False)
            return False
    if not assets:
        if fallback_item is not None:
            return _open_modeler_preview_for_item(fallback_item, frame=False)
        return False
    try:
        handler(assets, frame=False)
    except TypeError:
        try:
            handler(assets)
        except Exception:
            return False
    except Exception:
        return False
    return True


def _schedule_scene_reload(scene, fallback_item=None) -> None:
    try:
        QtCore.QTimer.singleShot(0, lambda scn=scene, item=fallback_item: _reload_active_scene(scn, item))
    except Exception:
        _reload_active_scene(scene, fallback_item)


def build_ports(node_item) -> None:
    for name, default in (
        ("path", ""),
        ("hidden_submeshes", "[]"),
    ):
        _ensure_param(node_item, name, default)
    _ensure_hidden_params(getattr(node_item, "model", None), ["path", "hidden_submeshes"])
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


class ModelerWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setMinimumWidth(0)
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(self._status, 0)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedSize(64, 24)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(0)
        button_row.addWidget(self._view_btn, 0)
        button_row.addStretch(1)
        layout.addLayout(button_row, 0)

        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._update_status)

    def sizeHint(self):
        return QtCore.QSize(220, MODELER_WIDGET_BODY_H)

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._schedule_update)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(lambda *_: self._schedule_update())
            except Exception:
                pass
        self._scene_connected = True

    def _schedule_update(self):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(60, self._update_status)

    def _update_status(self):
        self._pending = False
        self._ensure_scene()
        _upstream_item, _upstream_kind, path = _resolve_connected_model(
            self._scene,
            self._node_item,
            getattr(self._node_item, "model", None),
        )
        if not path:
            self._status.setText("No model connected.")
            self._view_btn.setEnabled(False)
            return
        names, _status = _model_object_names(path)
        label = Path(path).name
        if names:
            label = f"{label} ({len(names)} parts)"
        self._status.setText(label)
        self._view_btn.setEnabled(True)

    def _on_view_clicked(self):
        self._update_status()
        _open_modeler_preview_for_item(self._node_item, frame=True)


def render_node_body(node_item, y_cursor: int) -> int:
    body = ModelerWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    h = body.sizeHint().height()
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    return y_cursor + h + 8


def augment_infocard_footer(card, footer_layout) -> bool:
    node = getattr(card, "_node_ref", None)
    if node is None:
        return False

    panel = QtWidgets.QWidget()
    panel.setObjectName("ModelerOutlinerPanel")
    panel.setStyleSheet(
        "QWidget#ModelerOutlinerPanel{background:#111827;border:1px solid #374151;border-radius:6px;}"
        "QLabel{color:#dbeafe;background:transparent;}"
        "QListWidget{background:#0f172a;color:#e5e7eb;border:0;}"
        "QToolButton{background:transparent;border:0;padding:2px;}"
    )
    layout = QtWidgets.QVBoxLayout(panel)
    layout.setContentsMargins(8, 7, 8, 8)
    layout.setSpacing(6)

    header = QtWidgets.QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    title = QtWidgets.QLabel("Outliner")
    title_font = title.font()
    title_font.setBold(True)
    title.setFont(title_font)
    count_label = QtWidgets.QLabel("")
    count_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    count_label.setStyleSheet("color:#94a3b8;background:transparent;")
    header.addWidget(title, 0)
    header.addStretch(1)
    header.addWidget(count_label, 0)
    layout.addLayout(header)

    actions = QtWidgets.QHBoxLayout()
    actions.setContentsMargins(0, 0, 0, 0)
    actions.setSpacing(6)
    isolate_btn = QtWidgets.QPushButton("Isolate Selected")
    show_all_btn = QtWidgets.QPushButton("Show All")
    for btn in (isolate_btn, show_all_btn):
        btn.setFixedHeight(24)
        btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e5e7eb;border:1px solid #475569;"
            "border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#273449;}"
            "QPushButton:disabled{background:#111827;color:#64748b;border-color:#334155;}"
        )
    actions.addWidget(isolate_btn, 0)
    actions.addWidget(show_all_btn, 0)
    actions.addStretch(1)
    layout.addLayout(actions)

    outliner = QtWidgets.QListWidget()
    outliner.setMinimumHeight(420)
    outliner.setUniformItemSizes(False)
    outliner.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
    outliner.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    layout.addWidget(outliner, 1)
    footer_layout.addWidget(panel, 1)

    def _selected_names() -> list[str]:
        names = []
        for item in outliner.selectedItems():
            name = str(item.data(QtCore.Qt.UserRole) or "").strip()
            if name:
                names.append(name)
        return names

    def _all_names() -> list[str]:
        names = []
        for row_idx in range(outliner.count()):
            item = outliner.item(row_idx)
            name = str(item.data(QtCore.Qt.UserRole) or "").strip() if item is not None else ""
            if name:
                names.append(name)
        return names

    def _sync_selection_styles() -> None:
        selected = {name.lower() for name in _selected_names()}
        setattr(card, "_modeler_selected_names", set(selected))
        isolate_btn.setEnabled(bool(selected))
        show_all_btn.setEnabled(outliner.count() > 0)
        for row_idx in range(outliner.count()):
            item = outliner.item(row_idx)
            if item is None:
                continue
            row_widget = outliner.itemWidget(item)
            if row_widget is None:
                continue
            name = str(item.data(QtCore.Qt.UserRole) or "").strip().lower()
            row_widget.setProperty("selected", name in selected)
            try:
                row_widget.style().unpolish(row_widget)
                row_widget.style().polish(row_widget)
            except Exception:
                pass
            row_widget.update()

    def _set_current_without_selection(item) -> None:
        if item is None:
            return
        flag = getattr(QtCore.QItemSelectionModel, "NoUpdate", None)
        if flag is None:
            try:
                flag = QtCore.QItemSelectionModel.SelectionFlag.NoUpdate
            except Exception:
                flag = None
        if flag is None:
            try:
                outliner.setCurrentRow(outliner.row(item))
            except Exception:
                pass
            return
        try:
            outliner.setCurrentItem(item, flag)
            return
        except Exception:
            pass
        try:
            model_index = outliner.indexFromItem(item)
            selection_model = outliner.selectionModel()
            if selection_model is not None:
                selection_model.setCurrentIndex(model_index, flag)
                return
        except Exception:
            pass
        try:
            outliner.setCurrentItem(item)
        except Exception:
            pass

    def _select_item_from_mouse(item, event):
        try:
            if event is not None and hasattr(event, "button") and event.button() != QtCore.Qt.LeftButton:
                return
        except Exception:
            pass
        row = outliner.row(item)
        if row < 0:
            return
        try:
            mods = QtWidgets.QApplication.keyboardModifiers()
            shift = bool(mods & QtCore.Qt.ShiftModifier)
            ctrl = bool(mods & QtCore.Qt.ControlModifier)
        except Exception:
            shift = False
            ctrl = False
        if shift:
            anchor = getattr(card, "_modeler_selection_anchor_row", None)
            if not isinstance(anchor, int) or anchor < 0 or anchor >= outliner.count():
                anchor = outliner.currentRow()
            if anchor < 0:
                anchor = row
            if not ctrl:
                outliner.clearSelection()
            lo = min(anchor, row)
            hi = max(anchor, row)
            for idx in range(lo, hi + 1):
                range_item = outliner.item(idx)
                if range_item is not None:
                    range_item.setSelected(True)
            _set_current_without_selection(item)
        elif ctrl:
            item.setSelected(not item.isSelected())
            _set_current_without_selection(item)
            setattr(card, "_modeler_selection_anchor_row", row)
        else:
            outliner.clearSelection()
            item.setSelected(True)
            _set_current_without_selection(item)
            setattr(card, "_modeler_selection_anchor_row", row)
        _sync_selection_styles()
        try:
            if event is not None:
                event.accept()
        except Exception:
            pass

    def _isolate_selected() -> None:
        selected = {name.lower() for name in _selected_names()}
        if not selected:
            return
        hidden = {name for name in _all_names() if name.lower() not in selected}
        _set_hidden_names(card, hidden)
        _schedule_scene_reload(getattr(card, "_graph_scene", None), _node_item_for_card(card))

    def _show_all() -> None:
        _set_hidden_names(card, set())
        _schedule_scene_reload(getattr(card, "_graph_scene", None), _node_item_for_card(card))

    isolate_btn.clicked.connect(lambda _=False: _isolate_selected())
    show_all_btn.clicked.connect(lambda _=False: _show_all())
    outliner.itemSelectionChanged.connect(_sync_selection_styles)

    def _add_status_row(text: str) -> None:
        item = QtWidgets.QListWidgetItem()
        item.setSizeHint(QtCore.QSize(120, 28))
        outliner.addItem(item)
        label = QtWidgets.QLabel(text)
        label.setStyleSheet("color:#94a3b8;background:transparent;padding-left:4px;")
        outliner.setItemWidget(item, label)

    def _refresh(scene_override=None) -> None:
        scene = scene_override or getattr(card, "_graph_scene", None)
        previous_selection = set(getattr(card, "_modeler_selected_names", set()) or [])
        if not previous_selection:
            previous_selection = {name.lower() for name in _selected_names()}
        outliner.clear()
        path = _resolve_connected_model_path(card)
        names, status = _model_object_names(path)
        count_label.setText(str(len(names)) if names else "")
        if not names:
            isolate_btn.setEnabled(False)
            show_all_btn.setEnabled(False)
            outliner.setMinimumHeight(420)
            panel.setMinimumHeight(500)
            _add_status_row(status or "No named objects found.")
            return
        target_list_h = max(420, min(780, int(len(names) * 28 + 12)))
        outliner.setMinimumHeight(target_list_h)
        panel.setMinimumHeight(target_list_h + 82)
        hidden = _hidden_names(node)
        for idx, name in enumerate(names):
            visible = name.lower() not in hidden
            item = QtWidgets.QListWidgetItem()
            item.setSizeHint(QtCore.QSize(120, 28))
            item.setData(QtCore.Qt.UserRole, name)
            outliner.addItem(item)

            row = QtWidgets.QWidget()
            row.setObjectName("ModelerOutlinerRow")
            row.setStyleSheet(
                "QWidget#ModelerOutlinerRow{background:rgba(15,23,42,0.35);}"
                "QWidget#ModelerOutlinerRow[odd=\"true\"]{background:rgba(15,23,42,0.55);}"
                "QWidget#ModelerOutlinerRow[selected=\"true\"]{background:#334155;border-radius:4px;}"
            )
            row.setProperty("odd", bool(idx % 2))
            row.setProperty("selected", False)
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(4, 0, 4, 0)
            row_layout.setSpacing(6)

            eye_btn = QtWidgets.QToolButton()
            eye_btn.setAutoRaise(True)
            eye_btn.setCheckable(True)
            try:
                eye_btn.blockSignals(True)
                eye_btn.setChecked(visible)
            finally:
                eye_btn.blockSignals(False)
            eye_btn.setIcon(_eye_icon(visible))
            eye_btn.setToolTip("Toggle visibility")

            name_label = QtWidgets.QLabel(name)
            name_label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
            name_label.setStyleSheet("color:#e5e7eb;background:transparent;")
            name_label.setMinimumWidth(0)
            name_label.setCursor(QtCore.Qt.PointingHandCursor)

            def _on_eye_clicked(checked, mesh_name=name, button=eye_btn):
                hidden_now = _hidden_names(node)
                key = mesh_name.lower()
                if checked:
                    hidden_now.discard(key)
                else:
                    hidden_now.add(key)
                try:
                    button.setIcon(_eye_icon(bool(checked)))
                except Exception:
                    pass
                _set_hidden_names(card, hidden_now)
                _schedule_scene_reload(scene, _node_item_for_card(card))

            eye_btn.clicked.connect(_on_eye_clicked)
            row.mousePressEvent = lambda event, it=item: _select_item_from_mouse(it, event)
            name_label.mousePressEvent = lambda event, it=item: _select_item_from_mouse(it, event)
            row_layout.addWidget(eye_btn, 0)
            row_layout.addWidget(name_label, 1)
            outliner.setItemWidget(item, row)
            if name.lower() in previous_selection:
                item.setSelected(True)
        _sync_selection_styles()

    def _safe_refresh(scene_override=None):
        try:
            _refresh(scene_override)
        except RuntimeError as exc:
            if "already deleted" not in str(exc).lower():
                raise
        except Exception:
            try:
                outliner.clear()
                _add_status_row("Could not refresh outliner.")
            except Exception:
                pass

    def _connect(scene):
        if scene is None:
            return
        if getattr(card, "_modeler_outliner_connected", False):
            return
        try:
            if hasattr(scene, "linksChanged"):
                scene.linksChanged.connect(lambda *_, scn=scene: _safe_refresh(scn))
            if hasattr(scene, "paramChanged"):
                scene.paramChanged.connect(lambda *_, scn=scene: _safe_refresh(scn))
            card._modeler_outliner_connected = True
        except Exception:
            pass

    card._modeler_outliner_refresh = _safe_refresh
    card._modeler_outliner_connect = _connect
    scene = getattr(card, "_graph_scene", None)
    if scene is not None:
        _connect(scene)
    _safe_refresh(scene)
    return True


MODELER_SPEC = Spec(
    stripe_color="#0ea5e9",
    augment_infocard_footer=augment_infocard_footer,
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "MODELER_SPEC",
    "augment_infocard_footer",
    "build_ports",
    "render_node_body",
]
