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
    result = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        entry = {"path": str(row["path"]), "checked": row.get("checked", True) is True}
        if "approved" in row:
            entry["approved"] = row["approved"] is True
        if isinstance(row.get("render"), dict):
            entry["render"] = dict(row["render"])
        result.append(entry)
    return result


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
    from . import approvals
    approvals.save(scene, rows)
    mocap._set_param_value(model, "files", json.dumps(rows, ensure_ascii=False))
    mocap._set_param_value(model, "output", str(number))
    sync_output(model, scene)
    if scene is not None:
        scene.set_node_params(model.name, list(model.params), rebuild=False, emit=True)


def schedule_live_preview(model, scene):
    item = getattr(scene, "_node_items", {}).get(model.name)
    for proxy in getattr(item, "_plugin_proxies", []):
        widget = proxy.widget()
        if isinstance(widget, MocapCollectionWidget) and widget.live_view.isChecked():
            widget._live_timer.start()
            break


def add_paths(model, paths, scene=None) -> int:
    """Append once per path; select the last added/requested file."""
    from .archive import is_archived
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
        if is_archived(resolved):
            raise ValueError(f"This BVH is archived and cannot be added to the collection: {path}")
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
    from .archive import is_archived
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Select an existing folder: {folder}")
    if is_archived(root):
        return []

    def scan_error(error):
        raise error

    paths = []
    for directory, subfolders, filenames in os.walk(root, onerror=scan_error, followlinks=False):
        if (Path(directory) / "archive.json").is_file():
            subfolders.clear()
            continue
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


def relocate_paths(model, selected_rows, root, scene=None):
    """Relink selected rows by their longest matching path suffix; never guess ties."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Choose an existing parent folder.")
    candidates = {}
    for path in bvh_paths_in_folder(root):
        candidates.setdefault(path.name.casefold(), []).append(path)
    rows = entries(model)
    updates = {}
    for index in sorted(set(selected_rows)):
        old = rows[index]["path"].replace("\\", "/").split("/")
        matches = candidates.get(old[-1].casefold(), [])
        scored = []
        for path in matches:
            score = 0
            for a, b in zip(reversed(old), reversed(path.relative_to(root).parts)):
                if a.casefold() != b.casefold():
                    break
                score += 1
            scored.append((score, path))
        best = max((score for score, path in scored), default=0)
        winners = [path for score, path in scored if score == best]
        if len(winners) != 1:
            reason = "No matching BVH" if not winners else "Multiple matching BVHs"
            raise ValueError(f"{reason} for {rows[index]['path']}. No paths were changed.")
        updates[index] = str(winners[0])
    for index, path in updates.items():
        rows[index]["path"] = path
    if updates:
        save_state(model, rows, output_number(model), scene)
    return len(updates)


class CollectionPathDelegate(QtWidgets.QStyledItemDelegate):
    def paint(self, painter, option, index):
        approved = bool(index.data(QtCore.Qt.UserRole))
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        if not selected:
            return super().paint(painter, option, index)
        # Selection uses the active-path purple; green is reserved for approval.
        styled = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.font.setBold(True)
        styled.state &= ~QtWidgets.QStyle.State_Selected
        styled.backgroundBrush = QtGui.QBrush(QtGui.QColor("#554080"))
        styled.palette.setColor(QtGui.QPalette.Text, QtGui.QColor("#ffffff"))
        style = styled.widget.style() if styled.widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, styled, painter, styled.widget)
        if not approved:
            return
        painter.save()
        painter.setPen(QtGui.QPen(QtGui.QColor("#9be8b8"), 1))
        painter.drawLine(styled.rect.topLeft(), styled.rect.topRight())
        painter.drawLine(styled.rect.bottomLeft(), styled.rect.bottomRight())
        painter.restore()


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
        self.remove_button = QtWidgets.QPushButton("Remove")
        self.remove_button.setToolTip("Remove the active path from the collection")
        toolbar.addWidget(self.load_button)
        toolbar.addWidget(self.load_folder_button)
        self.relocate_button = QtWidgets.QPushButton("Update paths")
        self.relocate_button.setToolTip("Select rows with Shift/Ctrl, then find their BVH files under a new parent folder. Checked boxes stay unchanged.")
        toolbar.addWidget(self.relocate_button)
        toolbar.addWidget(self.remove_button)
        layout.addLayout(toolbar)
        reviews = QtWidgets.QHBoxLayout()
        self.load_renders_button = QtWidgets.QPushButton("Load renders")
        self.review_button = QtWidgets.QPushButton("Review renders")
        self.rename_button = QtWidgets.QPushButton("Rename")
        self.rename_button.setToolTip("Rename the active BVH folder and its assigned render folders")
        self.view_button = QtWidgets.QPushButton("View")
        self.view_button.setMinimumWidth(100)
        self.view_button.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self.view_button.setToolTip("View the active animation")
        self.archive_button = QtWidgets.QPushButton("Archive")
        self.archive_button.setToolTip("Move selected BVH run folders and renders into a named archive")
        for button in (self.load_renders_button, self.review_button, self.rename_button, self.archive_button):
            reviews.addWidget(button)
        layout.addLayout(reviews)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setItemDelegate(CollectionPathDelegate(self.table))
        self.table.setHorizontalHeaderLabels(["Use", "#", "Render", "BVH path"])
        self.table.setIconSize(QtCore.QSize(52, 32))
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(QtCore.Qt.ElideNone)
        self.table.setFixedHeight(190)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
        self.table.setColumnWidth(2, 64)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._path_context_menu)
        layout.addWidget(self.table)
        checks = QtWidgets.QHBoxLayout()
        self.all_button = QtWidgets.QPushButton("Check all")
        self.none_button = QtWidgets.QPushButton("Uncheck all")
        self.explorer_button = QtWidgets.QPushButton("Open in Explorer")
        self.explorer_button.setToolTip("Show the selected path, or the active output, in its folder")
        self.select_all_button = QtWidgets.QPushButton("Select all")
        self.select_all_button.setToolTip("Select every path without changing its Use checkbox")
        for button in (self.select_all_button, self.all_button, self.none_button, self.explorer_button):
            button.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
            checks.addWidget(button)
        self.live_view = QtWidgets.QCheckBox("Live view")
        self.live_view.setToolTip("Update the viewport when the active output changes. Keep the current camera while sliding.")
        checks.addWidget(self.live_view)
        checks.addStretch(1)
        checks.addWidget(self.view_button)
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
        self.load_renders_button.clicked.connect(self._load_renders)
        self.review_button.clicked.connect(lambda: self._review())
        self.rename_button.clicked.connect(lambda: self._rename())
        self.relocate_button.clicked.connect(self._relocate)
        self.archive_button.clicked.connect(self._archive)
        self.table.itemSelectionChanged.connect(lambda: self.archive_button.setEnabled(bool(self.table.selectionModel().selectedRows(3))))
        self.archive_button.setEnabled(False)
        self.table.itemSelectionChanged.connect(lambda: self.relocate_button.setEnabled(bool(self.table.selectionModel().selectedRows(3))))
        self.relocate_button.setEnabled(False)
        self.remove_button.clicked.connect(self._remove)
        self.all_button.clicked.connect(lambda: self._check_all(True))
        self.none_button.clicked.connect(lambda: self._check_all(False))
        self.explorer_button.clicked.connect(self._open_selected_path)
        self.select_all_button.clicked.connect(self.table.selectAll)
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
        self.table.cellDoubleClicked.connect(lambda row, column: self._rename(row + 1) if column == 3 else None)
        self._thumbnail_cache = {}
        self._thumbnail_queue = []
        self._thumbnail_timer = QtCore.QTimer(self)
        self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.timeout.connect(self._load_thumbnail)
        self.table.verticalScrollBar().valueChanged.connect(self._queue_thumbnails)
        self.slider.valueChanged.connect(self._select)
        self.number.valueChanged.connect(self._select)
        from . import approvals
        try:
            restored = approvals.restore(scene, entries(model))
            mocap._set_param_value(model, "files", json.dumps(restored, ensure_ascii=False))
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self, "Approval metadata", str(exc))
        if scene is not None:
            scene.paramChanged.connect(self._params_changed)
        self.refresh()
        # Workflow loading may assign the project filename after node creation.
        QtCore.QTimer.singleShot(0, self._restore_project_approvals)

    def _restore_project_approvals(self):
        if not isValid(self):
            return
        from . import approvals
        try:
            rows = entries(self.model)
            restored = approvals.restore(self.graph_scene, [dict(row) for row in rows])
            if restored != rows:
                mocap._set_param_value(self.model, "files", json.dumps(restored, ensure_ascii=False))
                self.refresh()
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not load approval metadata: {exc}")

    @QtCore.Slot(str, list)
    def _params_changed(self, name, _params):
        if name == self.model.name:
            self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_thumbnail_timer"):
            self._queue_thumbnails()

    def refresh(self):
        from .archive import remove_archived_rows
        remove_archived_rows(self.model, self.graph_scene)
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
                selected_paths = {self.table.item(index.row(), 3).text()
                                  for index in self.table.selectionModel().selectedRows(3)}
                self.table.clearSelection()
                self.table.setRowCount(len(rows))
                for i, row in enumerate(rows):
                    check = QtWidgets.QTableWidgetItem()
                    check.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
                    check.setCheckState(QtCore.Qt.Checked if row["checked"] else QtCore.Qt.Unchecked)
                    check.setToolTip("Include this path in future batch processing")
                    thumbnail = QtWidgets.QTableWidgetItem("▶" if row.get("render") else "")
                    thumbnail.setTextAlignment(QtCore.Qt.AlignCenter)
                    thumbnail.setToolTip("Click to review the assigned render" if row.get("render") else "No render assigned")
                    cells = (check, QtWidgets.QTableWidgetItem(str(i + 1)), thumbnail, QtWidgets.QTableWidgetItem(row["path"]))
                    for column, cell in enumerate(cells):
                        self.table.setItem(i, column, cell)
                    if row["path"] in selected_paths:
                        self.table.selectionModel().select(self.table.model().index(i, 3),
                            QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
                self._displayed_rows = rows
            # Moving the slider changes only two row highlights, not every cell.
            for index in (range(len(rows)) if rows_changed else {getattr(self, "_displayed_number", 0) - 1, number - 1}):
                if 0 <= index < len(rows):
                    for column in range(4):
                        cell = self.table.item(index, column)
                        active = index + 1 == number
                        approved = rows[index].get("approved", False)
                        cell.setData(QtCore.Qt.UserRole, approved)
                        # Purple belongs exclusively to Qt row selection (the
                        # delegate paints it). An active output can differ from
                        # the clicked row when Checked only excludes that row.
                        color = ("#338253" if active else "#225c39") if approved else ""
                        cell.setBackground(QtGui.QBrush(QtGui.QColor(color)) if color else QtGui.QBrush())
                        cell.setForeground(QtGui.QBrush(QtGui.QColor("#ffffff")) if color else QtGui.QBrush())
                        font = cell.font()
                        # Selection emphasis is painted by the delegate, rather
                        # than left on an output row that may no longer be selected.
                        font.setBold(False)
                        cell.setFont(font)
            if number != getattr(self, "_displayed_number", number) and not rows_changed:
                selection = self.table.selectionModel()
                if number:
                    selection.setCurrentIndex(self.table.model().index(number - 1, 3),
                        QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
                elif not number:
                    selection.clear()
            self._displayed_number = number
            self._displayed_path = mocap._param_value(self.model, "path")
            for control in (self.slider, self.number):
                control.setRange(1 if eligible else 0, len(eligible))
                control.setValue(eligible.index(number) + 1 if number else 0)
                control.setEnabled(bool(eligible))
            if number:
                self.table.scrollToItem(self.table.item(number - 1, 0))
        for button in (self.select_all_button, self.all_button, self.none_button, self.explorer_button):
            button.setEnabled(bool(rows))
        self.review_button.setEnabled(bool(rows))
        self.load_renders_button.setEnabled(bool(rows))
        self.relocate_button.setEnabled(bool(self.table.selectionModel().selectedRows(3)))
        self.archive_button.setEnabled(bool(self.table.selectionModel().selectedRows(3)))
        for button in (self.remove_button, self.view_button, self.rename_button):
            button.setEnabled(bool(eligible))
        self.status.setText(f"{len(rows)} files | {len(checked_paths(self.model))} checked" if rows
                            else "Load BVH files to start a collection.")
        if rows and not eligible:
            self.status.setText(f"{len(rows)} files | No checked files — output is empty.")
        if rows_changed:
            self._queue_thumbnails()

    def _queue_thumbnails(self, *_args):
        self._thumbnail_queue = []
        for index, row in enumerate(getattr(self, "_displayed_rows", [])):
            path = row.get("render", {}).get("first_frame", "")
            key = (path, str(row.get("render", {}).get("frame_stamp", "")))
            cell = self.table.item(index, 2)
            if not path or cell is None:
                continue
            if key in self._thumbnail_cache:
                cell.setIcon(self._thumbnail_cache[key])
                cell.setText("")
            elif self.table.visualItemRect(cell).intersects(self.table.viewport().rect()):
                self._thumbnail_queue.append((index, key))
        if self._thumbnail_queue:
            self._thumbnail_timer.start(0)

    def _load_thumbnail(self):
        if not self._thumbnail_queue:
            return
        index, key = self._thumbnail_queue.pop(0)
        path = key[0]
        reader = QtGui.QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(52, 32, QtCore.Qt.KeepAspectRatio))
        image = reader.read()
        cell = self.table.item(index, 2)
        if cell is not None and not image.isNull():
            icon = QtGui.QIcon(QtGui.QPixmap.fromImage(image))
            self._thumbnail_cache[key] = icon
            with QtCore.QSignalBlocker(self.table):
                cell.setIcon(icon)
                cell.setText("")
        if self._thumbnail_queue:
            self._thumbnail_timer.start(0)

    def _load_renders(self):
        from .review import load_render_folder
        try:
            load_render_folder(self.model, self.graph_scene, self._dialog_parent())
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self._dialog_parent(), "Load renders", str(exc))

    def _review(self, number=None):
        from .review import open_review
        return open_review(self.model, self.graph_scene, self._dialog_parent(), number)

    def _rename(self, number=None):
        from .review import rename_with_dialog
        rename_with_dialog(self.model, self.graph_scene, number or output_number(self.model), self._dialog_parent())

    def _relocate(self):
        selected = [index.row() for index in self.table.selectionModel().selectedRows(3)]
        if not selected:
            return
        root = QtWidgets.QFileDialog.getExistingDirectory(self._dialog_parent(), "New parent folder for selected BVH paths", "")
        if not root:
            return
        try:
            count = relocate_paths(self.model, selected, root, self.graph_scene)
            self.refresh()
            self.status.setText(f"Updated {count} paths. Checked boxes unchanged.")
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self._dialog_parent(), "Update paths", str(exc))

    def _archive(self):
        from .archive import choose_archive, archive_rows
        from .review_data import ensure_idle
        selected = [index.row() for index in self.table.selectionModel().selectedRows(3)]
        if not selected:
            return
        try:
            ensure_idle(self.graph_scene)
            choice = choose_archive(self.graph_scene, self._dialog_parent())
            if choice is None:
                return
            review = getattr(self.model, "_collection_review_dialog", None)
            if review is not None and isValid(review):
                review.player._stop_playback()
                review.close()
            count, destination = archive_rows(self.model, self.graph_scene, selected, *choice)
            self.refresh()
            self.status.setText(f"Archived {count} paths to {destination}")
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self._dialog_parent(), "Archive paths", str(exc))
        if isValid(self):
            self.refresh()

    def _save(self, rows, number):
        previous_path = mocap._param_value(self.model, "path")
        save_state(self.model, rows, number, self.graph_scene)
        if self.graph_scene is None:
            self.refresh()
        if self.live_view.isChecked() and output_number(self.model) and previous_path != mocap._param_value(self.model, "path"):
            self._live_timer.start()

    def _select(self, number):
        eligible = output_rows(self.model)
        if 1 <= number <= len(eligible):
            self.table.selectionModel().setCurrentIndex(
                self.table.model().index(eligible[number - 1] - 1, 3),
                QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
            if eligible[number - 1] != output_number(self.model):
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

    def _explorer_path(self, row):
        rows = entries(self.model)
        if not 0 <= row < len(rows):
            return None
        scene = self.graph_scene or getattr(self.model, "_graph_scene", None)
        base = mocap._workflow_dir_for_node(SimpleNamespace(scene=lambda: scene))
        return mocap._resolve_existing_path(rows[row]["path"], base)

    def _open_selected_path(self):
        selected = sorted(index.row() for index in self.table.selectionModel().selectedRows(3))
        current = self.table.currentRow()
        row = current if current in selected else (selected[0] if selected else max(0, output_number(self.model) - 1))
        path = self._explorer_path(row)
        if path is None:
            self.status.setText("File unavailable. Update paths to locate it.")
            return
        from echograph.ui.node_item import _open_in_explorer
        _open_in_explorer(str(path))

    def _path_context_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid() or index.row() >= len(entries(self.model)):
            return
        path = self._explorer_path(index.row())
        menu = QtWidgets.QMenu(self._dialog_parent())
        open_action = menu.addAction("Open in Explorer")
        open_action.setEnabled(path is not None)
        # Embedded node widgets cannot map screen coordinates reliably through
        # the graph's zoom/perspective transform. Qt's cursor is already global.
        selected = menu.exec(QtGui.QCursor.pos())
        menu.deleteLater()
        if selected is open_action and path is not None:
            from echograph.ui.node_item import _open_in_explorer
            _open_in_explorer(str(path))

    def _clicked(self, row, column):
        if QtWidgets.QApplication.keyboardModifiers() & (QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier):
            return
        if column == 2 and entries(self.model)[row].get("render"):
            self._review(row + 1)
            return
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
