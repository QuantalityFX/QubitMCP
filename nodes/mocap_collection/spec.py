from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from nodes.core import Spec
from nodes.mocap_import import spec as mocap


def entries(model) -> list[dict]:
    """Ordered, serializable BVH paths and their future batch inclusion flags."""
    try:
        rows = json.loads(mocap._param_value(model, "files") or "[]")
    except (ValueError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    return [{"path": str(row["path"]), "checked": row.get("checked", True) is True}
            for row in rows if isinstance(row, dict) and row.get("path")]


def output_rows(model) -> list[int]:
    """Original, one-based row numbers eligible for the active output."""
    checked_only = mocap._param_bool(model, "checked_only")
    return [i + 1 for i, row in enumerate(entries(model)) if row["checked"] or not checked_only]


def output_number(model) -> int:
    eligible = output_rows(model)
    try:
        number = int(mocap._param_value(model, "output") or "1")
    except ValueError:
        number = 1
    return next((row for row in eligible if row >= number), eligible[-1]) if eligible else 0


def checked_paths(model) -> list[str]:
    """Batch consumers can iterate this list without changing the active output."""
    return [row["path"] for row in entries(model) if row["checked"]]


def sync_output(model, scene=None) -> str:
    """Expose the selected file through the normal Mocap Import path contract."""
    rows = entries(model)
    number = output_number(model)
    path = rows[number - 1]["path"] if number else ""
    scene = scene if scene is not None else getattr(model, "_graph_scene", None)
    base = mocap._workflow_dir_for_node(SimpleNamespace(scene=lambda: scene))
    resolved = mocap._resolve_existing_path(path, base)
    resolved_text = str(resolved) if resolved is not None else ""
    token = (path, resolved_text)
    if getattr(model, "_collection_output_token", None) != token:
        for name in ("_mocap_animation_result", "_mocap_skeleton", "_mocap_clip",
                     "_mocap_rig_context_cache"):
            setattr(model, name, None)
        model._collection_output_token = token
    mocap._set_param_value(model, "output", str(number))
    mocap._set_param_value(model, "path", path)
    mocap._set_param_value(model, "resolved_path", resolved_text)
    model._mocap_resolved_path = resolved_text
    return path


def save_state(model, rows, number, scene=None):
    mocap._set_param_value(model, "files", json.dumps(rows, ensure_ascii=False))
    mocap._set_param_value(model, "output", str(number))
    sync_output(model, scene)
    if scene is not None:
        scene.set_node_params(model.name, list(model.params), rebuild=False, emit=True)


def add_paths(model, paths, scene=None) -> int:
    """Append once per path; select the last added/requested file."""
    base = mocap._workflow_dir_for_node(SimpleNamespace(scene=lambda: scene))
    def path_key(raw):
        resolved = mocap._resolve_existing_path(raw, base)
        return os.path.normcase(os.path.abspath(str(resolved or raw)))

    # Validate the whole selection before changing the collection.
    paths = [str(Path(str(path).strip().strip('"')).expanduser()) for path in paths]
    for path in paths:
        resolved = mocap._resolve_existing_path(path, base)
        if Path(path).suffix.lower() != ".bvh" or resolved is None or not resolved.is_file():
            raise ValueError(f"Select an existing BVH file: {path}")
    rows = entries(model)
    known = {path_key(row["path"]): i for i, row in enumerate(rows)}
    number, added = output_number(model), 0
    for path in paths:
        key = path_key(path)
        if key not in known:
            known[key] = len(rows)
            rows.append({"path": path, "checked": True})
            added += 1
        number = known[key] + 1
    if paths:
        save_state(model, rows, number, scene)
    return added


def bvh_paths_in_folder(folder) -> list[Path]:
    """Collect BVHs recursively in a repeatable order, including uppercase extensions."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Select an existing folder: {folder}")

    def scan_error(error):
        raise error

    paths = []
    for directory, _subfolders, filenames in os.walk(root, onerror=scan_error, followlinks=False):
        for filename in filenames:
            if Path(filename).suffix.lower() == ".bvh":
                paths.append(Path(directory) / filename)
    return sorted(paths, key=lambda path: (path.relative_to(root).as_posix().casefold(), str(path)))


def build_ports(item):
    for key, default in (("files", "[]"), ("output", "0"), ("path", ""),
                         ("resolved_path", ""), ("debug_log", "0"), ("live_view", "0"),
                         ("checked_only", "0"),
                         ("__mocap_collection_size", "")):
        mocap._ensure_param(item, key, default)
    mocap._ensure_hidden_params(item.model, ["files", "output", "path", "resolved_path", "debug_log", "live_view",
                                           "__mocap_collection_size", "checked_only"])
    sync_output(item.model)


class MocapCollectionWidget(QtWidgets.QWidget):
    def __init__(self, model, scene=None, parent=None):
        super().__init__(parent)
        self.model = model
        self.graph_scene = scene
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        toolbar = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("Load BVH files")
        self.load_folder_button = QtWidgets.QPushButton("Load BVH folder")
        self.load_folder_button.setToolTip("Add all BVH files in a folder and its subfolders. Existing paths are skipped.")
        self.remove_button = QtWidgets.QPushButton("Remove active")
        toolbar.addWidget(self.load_button)
        toolbar.addWidget(self.load_folder_button)
        toolbar.addWidget(self.remove_button)
        layout.addLayout(toolbar)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Use", "#", "BVH path"])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(QtCore.Qt.ElideNone)
        self.table.setFixedHeight(190)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setToolTip("Check files for batch use or Checked only output. Click an eligible number or path to make it active.")
        layout.addWidget(self.table)
        checks = QtWidgets.QHBoxLayout()
        self.all_button = QtWidgets.QPushButton("Check all")
        self.none_button = QtWidgets.QPushButton("Uncheck all")
        self.view_button = QtWidgets.QPushButton("View active")
        for button in (self.all_button, self.none_button, self.view_button):
            checks.addWidget(button)
        self.live_view = QtWidgets.QCheckBox("Live view")
        self.live_view.setToolTip("Update the viewport when the active output changes. Keep the current camera while sliding.")
        checks.addWidget(self.live_view)
        layout.addLayout(checks)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        output = QtWidgets.QHBoxLayout()
        output.addWidget(QtWidgets.QLabel("Output"))
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setTracking(False)
        self.slider.setToolTip("Select the active BVH by its path number, including unchecked files.")
        self.number = QtWidgets.QSpinBox()
        output.addWidget(self.slider, 1)
        output.addWidget(self.number)
        self.checked_only = QtWidgets.QCheckBox("Checked only")
        self.checked_only.setToolTip("Restrict the output slider to checked files, in list order.")
        output.addWidget(self.checked_only)
        layout.addLayout(output)
        for button in self.findChildren(QtWidgets.QPushButton):
            button.setAutoDefault(False)
        self.load_button.clicked.connect(self._browse)
        self.load_folder_button.clicked.connect(self._browse_folder)
        self.remove_button.clicked.connect(self._remove)
        self.all_button.clicked.connect(lambda: self._check_all(True))
        self.none_button.clicked.connect(lambda: self._check_all(False))
        self.view_button.clicked.connect(self._view)
        self._live_timer = QtCore.QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(100)
        self._live_timer.timeout.connect(self._update_live_view)
        self._live_frame = False
        self.live_view.toggled.connect(self._toggle_live_view)
        self.checked_only.toggled.connect(self._toggle_checked_only)
        self.table.itemChanged.connect(self._checked)
        self.table.cellClicked.connect(self._clicked)
        self.slider.valueChanged.connect(self._select)
        self.number.valueChanged.connect(self._select)
        if scene is not None:
            scene.paramChanged.connect(self._params_changed)
        self.refresh()

    @QtCore.Slot(str, list)
    def _params_changed(self, name, _params):
        if name == self.model.name:
            self.refresh()

    def refresh(self):
        sync_output(self.model, self.graph_scene)
        rows, number = entries(self.model), output_number(self.model)
        eligible = output_rows(self.model)
        with QtCore.QSignalBlocker(self.checked_only):
            self.checked_only.setChecked(mocap._param_bool(self.model, "checked_only"))
        self.slider.setToolTip("Output index among checked files; the highlighted # is the original path number."
                               if self.checked_only.isChecked() else "Select the active BVH by its path number, including unchecked files.")
        self.number.setToolTip(self.slider.toolTip())
        enabled = mocap._param_bool(self.model, "live_view")
        with QtCore.QSignalBlocker(self.live_view):
            self.live_view.setChecked(enabled)
        self.slider.setTracking(enabled)
        if not enabled or not eligible:
            self._live_timer.stop()
        with QtCore.QSignalBlocker(self.table), QtCore.QSignalBlocker(self.slider), QtCore.QSignalBlocker(self.number):
            rows_changed = rows != getattr(self, "_displayed_rows", None)
            if rows_changed:
                self.table.setRowCount(len(rows))
                for i, row in enumerate(rows):
                    check = QtWidgets.QTableWidgetItem()
                    check.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
                    check.setCheckState(QtCore.Qt.Checked if row["checked"] else QtCore.Qt.Unchecked)
                    check.setToolTip("Include this path in future batch processing")
                    cells = (check, QtWidgets.QTableWidgetItem(str(i + 1)), QtWidgets.QTableWidgetItem(row["path"]))
                    for column, cell in enumerate(cells):
                        if column == 2:
                            cell.setToolTip(row["path"])
                        self.table.setItem(i, column, cell)
                self._displayed_rows = rows
            # Moving the slider changes only two row highlights, not every cell.
            for index in {getattr(self, "_displayed_number", 0) - 1, number - 1}:
                if 0 <= index < len(rows):
                    for column in range(3):
                        cell = self.table.item(index, column)
                        active = index + 1 == number
                        cell.setBackground(QtGui.QBrush(QtGui.QColor("#554080")) if active else QtGui.QBrush())
                        cell.setForeground(QtGui.QBrush(QtGui.QColor("#ffffff")) if active else QtGui.QBrush())
            self._displayed_number = number
            for control in (self.slider, self.number):
                control.setRange(1 if eligible else 0, len(eligible))
                control.setValue(eligible.index(number) + 1 if number else 0)
                control.setEnabled(bool(eligible))
            if number:
                self.table.scrollToItem(self.table.item(number - 1, 0))
        for button in (self.all_button, self.none_button):
            button.setEnabled(bool(rows))
        for button in (self.remove_button, self.view_button):
            button.setEnabled(bool(eligible))
        self.status.setText(f"{len(rows)} files | {len(checked_paths(self.model))} checked" if rows
                            else "Load BVH files to start a collection.")
        if rows and not eligible:
            self.status.setText(f"{len(rows)} files | No checked files — output is empty.")

    def _save(self, rows, number):
        previous_path = mocap._param_value(self.model, "path")
        save_state(self.model, rows, number, self.graph_scene)
        if self.graph_scene is None:
            self.refresh()
        if self.live_view.isChecked() and output_number(self.model) and previous_path != mocap._param_value(self.model, "path"):
            self._live_timer.start()

    def _select(self, number):
        eligible = output_rows(self.model)
        if 1 <= number <= len(eligible) and eligible[number - 1] != output_number(self.model):
            self._save(entries(self.model), eligible[number - 1])

    def _toggle_checked_only(self, enabled):
        number = output_number(self.model)
        mocap._set_param_value(self.model, "checked_only", "1" if enabled else "0")
        self._save(entries(self.model), number)

    def _toggle_live_view(self, enabled):
        mocap._set_param_value(self.model, "live_view", "1" if enabled else "0")
        self._save(entries(self.model), output_number(self.model))
        if enabled:
            self._live_frame = True
            self._live_timer.start()

    def _update_live_view(self):
        if self.live_view.isChecked() and output_number(self.model):
            frame, self._live_frame = self._live_frame, False
            self._view(live=True, frame=frame)

    def _clicked(self, row, column):
        eligible = output_rows(self.model)
        if column != 0 and row + 1 in eligible:
            self._select(eligible.index(row + 1) + 1)

    def _checked(self, item):
        if item.column() == 0:
            rows = entries(self.model)
            rows[item.row()]["checked"] = item.checkState() == QtCore.Qt.Checked
            # Defer the refresh until Qt finishes handling the clicked table item.
            mocap._set_param_value(self.model, "files", json.dumps(rows, ensure_ascii=False))
            QtCore.QTimer.singleShot(0, self._commit_checks)

    def _commit_checks(self):
        self._save(entries(self.model), output_number(self.model))

    def _check_all(self, checked):
        rows = entries(self.model)
        for row in rows:
            row["checked"] = checked
        self._save(rows, output_number(self.model))

    def _remove(self):
        rows, number = entries(self.model), output_number(self.model)
        if number:
            rows.pop(number - 1)
            self._save(rows, min(number, len(rows)))

    def _dialog_parent(self):
        # Native dialogs need a real window owner, not an embedded node body.
        scene = self.graph_scene
        views = scene.views() if scene is not None else []
        parent = views[0].window() if views else self.window()
        if parent.graphicsProxyWidget() is not None:
            from echograph.ui.node_item import _top_level_parent_for_dialog
            parent = _top_level_parent_for_dialog()
        return parent

    def _browse(self):
        # Keep the model/scene even if the body is rebuilt during the dialog.
        model, scene = self.model, self.graph_scene
        parent = self._dialog_parent()
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(parent, "Add BVH animations", "", "BVH motion (*.bvh)")
        if paths:
            try:
                add_paths(model, paths, scene)
                if isValid(self):
                    self.refresh()
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(parent, "Mocap Collection", str(exc))

    def _browse_folder(self):
        model, scene = self.model, self.graph_scene
        parent = self._dialog_parent()
        folder = QtWidgets.QFileDialog.getExistingDirectory(parent, "Load BVH folder (including subfolders)", "")
        if not folder:
            return
        try:
            paths = bvh_paths_in_folder(folder)
            if not paths:
                QtWidgets.QMessageBox.information(parent, "Mocap Collection", "No BVH files found in this folder or its subfolders.")
                return
            added = add_paths(model, paths, scene)
            if isValid(self):
                self.refresh()
                self.status.setText(f"Added {added} of {len(paths)} BVH files found.")
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(parent, "Mocap Collection", f"Could not load BVH folder: {exc}")

    def _view(self, *, live=False, frame=True):
        self._live_timer.stop()
        item = SimpleNamespace(model=self.model, scene=lambda: self.graph_scene)
        result = mocap.resolve_mocap_import_source(item, persist=True, validate_animation=True)
        if result.status == "error":
            if live:
                self.status.setText("Cannot preview output; click View active for details.")
            else:
                QtWidgets.QMessageBox.warning(self, "Mocap Collection", "\n".join(result.message_lines()))
            return
        asset = mocap._build_preview_asset(self.model, result)
        views = self.graph_scene.views() if self.graph_scene is not None else []
        window = views[0].window() if views else self.window()
        if asset and callable(getattr(window, "open_scene_assets", None)):
            window.open_scene_assets([asset], frame=frame)
        elif live:
            self.status.setText("3D view is not available.")
        else:
            QtWidgets.QMessageBox.warning(self, "Mocap Collection", "3D view is not available.")


def render_node_body(item, y_cursor):
    scene = item.scene() or getattr(item.model, "_graph_scene", None)
    body = MocapCollectionWidget(item.model, scene)
    body.table.setMinimumHeight(100)
    body.table.setMaximumHeight(16777215)
    body.table.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
    body.layout().setStretch(body.layout().indexOf(body.table), 1)
    body.ensurePolished()
    # Keep the border exposed so embedded widgets don't consume resize drags.
    gutter = 8
    minimum_width = max(460, body.minimumSizeHint().width() + 2 * gutter)
    item.prepareGeometryChange()
    item.width = max(item.width, minimum_width)
    body.setFixedWidth(int(item.width - 2 * gutter))
    minimum_height = body.minimumSizeHint().height()
    item._mocap_collection_min_w = minimum_width
    item._mocap_collection_min_h = y_cursor + minimum_height + 10
    custom_size = item._size_tuple_from_param("__mocap_collection_size")
    height = max(minimum_height, custom_size[1] - y_cursor - 10) if custom_size else max(body.sizeHint().height(), minimum_height)
    body.setFixedHeight(int(height))
    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(body)
    proxy.setPos(gutter, y_cursor)
    proxy.resize(item.width - 2 * gutter, height)
    item._plugin_proxies.append(proxy)
    bottom = y_cursor + height + 10
    item.prepareGeometryChange()
    item.height = bottom
    return bottom


def augment_infocard_footer(card, footer_layout):
    node = getattr(card, "_node_ref", None)
    if node is None or node.kind.lower() != "mocap_collection":
        return False
    body = MocapCollectionWidget(node, getattr(card, "_graph_scene", None), card)
    body.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    footer_layout.addWidget(body, 1)
    return True


MOCAP_COLLECTION_SPEC = Spec(stripe_color="#7c3aed", build_ports=build_ports,
                             render_node_body=render_node_body,
                             augment_infocard_footer=augment_infocard_footer)
