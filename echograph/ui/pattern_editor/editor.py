from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from echograph.patterns import commands
from echograph.patterns import relationships
from echograph.patterns.document import PatternError
from echograph.patterns.geometry import bounds, edge_length, validate_piece
from echograph.patterns.serialization import load_document, save_document
from echograph.ui import hotkeys_config
from .canvas import PatternCanvas, sew_icon

PATTERN_FILTER = "Pattern documents (*.pattern.json);;JSON files (*.json)"
# Keep standalone editors alive across node rebuilds and workflow switches.
_EDITORS = set()


def pattern_filename(path: str) -> str:
    return path if path.lower().endswith(".json") else path + ".pattern.json"


class _CloseGuard(QtCore.QObject):
    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Type.Close:
            for editor in list(_EDITORS):
                if editor.parent() is watched and not editor.close():
                    event.ignore()
                    return True
        return False


def _number(value=0, minimum=-1e8, maximum=1e8):
    widget = QtWidgets.QDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(4)
    widget.setSingleStep(1.0)
    widget.setValue(value)
    widget.setKeyboardTracking(False)
    return widget


def _icon(filename):
    return QtGui.QIcon(str(Path(__file__).resolve().parents[3] / "icons" / filename))


class _PanelList(QtWidgets.QListWidget):
    orderChanged = QtCore.Signal()
    panelsClicked = QtCore.Signal()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.itemAt(event.position().toPoint()):
            self.panelsClicked.emit()

    def panel_ids(self):
        return [self.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
                for row in range(self.count())]

    def dropEvent(self, event):
        before = self.panel_ids()
        # Selection changes during a move must not rebuild the list mid-drop.
        with QtCore.QSignalBlocker(self):
            super().dropEvent(event)
        if self.panel_ids() != before:
            self.orderChanged.emit()


class PatternEditor(QtWidgets.QMainWindow):
    saved = QtCore.Signal(str)

    def __init__(self, path=None, parent=None, history=None):
        document, signature = load_document(path) if path else (None, None)
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.path = Path(path).expanduser().resolve() if path else None
        self.disk_hash = signature
        self.history = history or commands.DocumentHistory(document)
        document = self.history.document
        self.selected_id = document.pieces[0].id if document.pieces else ""
        self._refreshing = False
        self._edge_id = ""
        self._edge_selection = set()
        self._edges_deselected = False
        self._selected_points = set()
        self._selected_panels = {self.selected_id} if self.selected_id else set()
        self._point_selection = {}
        self.resize(1160, 800)
        self.setMinimumSize(850, 600)
        self._build_ui()
        QtWidgets.QApplication.instance().installEventFilter(self)
        self.refresh()
        self.canvas.fit_pieces()
        _EDITORS.add(self)
        self.destroyed.connect(lambda: _EDITORS.discard(self))
        if parent is not None and not hasattr(parent, "_pattern_close_guard"):
            parent._pattern_close_guard = _CloseGuard(parent)
            parent.installEventFilter(parent._pattern_close_guard)

    @property
    def document(self):
        return self.history.document

    def eventFilter(self, watched, event):
        if watched is self and event.type() == QtCore.QEvent.Type.WindowActivate:
            self._sync_history_shortcuts()
        # Claim this editor's shortcuts before competing application-wide
        # shortcuts or text controls can consume them.
        if (event.type() in (QtCore.QEvent.Type.ShortcutOverride, QtCore.QEvent.Type.KeyPress)
                and isinstance(watched, QtWidgets.QWidget) and watched.window() is self):
            selection_view = (watched is self.canvas or self.canvas.isAncestorOf(watched)
                              or watched is self.pieces or self.pieces.isAncestorOf(watched))
            if (selection_view and not self.canvas.drawing and event.key() == QtCore.Qt.Key.Key_Delete
                    and event.modifiers() == QtCore.Qt.KeyboardModifier.NoModifier):
                event.accept()
                if event.type() == QtCore.QEvent.Type.KeyPress:
                    self.delete_selection()
                return True
            self._sync_history_shortcuts()
            if event.matches(QtGui.QKeySequence.StandardKey.Save):
                event.accept()
                if event.type() == QtCore.QEvent.Type.KeyPress:
                    self.save()
                return True
            pressed = QtGui.QKeySequence(event.keyCombination())
            for action in (self.undo_action, self.redo_action):
                if not action.shortcut().isEmpty() and pressed == action.shortcut():
                    event.accept()
                    if event.type() == QtCore.QEvent.Type.KeyPress and action.isEnabled():
                        action.trigger()
                    return True
        return super().eventFilter(watched, event)

    def _sync_history_shortcuts(self):
        for action, key, fallback in ((self.undo_action, "app_undo", "Ctrl+Z"),
                                      (self.redo_action, "app_redo", "Ctrl+Y")):
            sequence = QtGui.QKeySequence(hotkeys_config.keyseq(key, fallback))
            if action.shortcut() != sequence:
                action.setShortcut(sequence)

    def _action(self, toolbar, text, callback, shortcut=None):
        action = toolbar.addAction(text)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        return action

    def _build_ui(self):
        filebar = self.addToolBar("File and history")
        filebar.setMovable(False)
        self._action(filebar, "Save Pattern", self.save, QtGui.QKeySequence.StandardKey.Save)
        self._action(filebar, "Save As…", self.save_as)
        self.reload_action = self._action(filebar, "Reload", self.reload)
        self.reload_action.setEnabled(self.path is not None)
        filebar.addSeparator()
        self.undo_action = self._action(filebar, "Undo", self.undo, hotkeys_config.keyseq("app_undo", "Ctrl+Z"))
        self.redo_action = self._action(filebar, "Redo", self.redo, hotkeys_config.keyseq("app_redo", "Ctrl+Y"))
        filebar.addSeparator()
        self._action(filebar, "Fit", lambda: self.canvas.fit_pieces(), "F")
        self.addToolBarBreak()
        tools = self.addToolBar("Drafting")
        tools.setMovable(False)
        tools.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for toolbar, background in ((filebar, "#1b2027"), (tools, "#262d36")):
            toolbar.setStyleSheet(
                f"QToolBar {{ background: {background}; border: 0; border-bottom: 1px solid #303742; }}"
                "QToolBar::separator { background: #303742; width: 1px; margin: 6px 4px; }"
            )
        self._action(tools, "+ Rectangle", lambda: self.template_dialog(False))
        self._action(tools, "+ Trapezoid", lambda: self.template_dialog(True))
        self._action(tools, "Draw Outline", lambda: self.canvas.start_outline())
        self.sew_action = self._action(tools, "Sew", lambda enabled: self.canvas.set_sew_mode(enabled))
        self.sew_action.setIcon(sew_icon())
        self.sew_action.setCheckable(True)
        self.sew_action.setToolTip("Sew two edges: click the first edge, then its partner. Esc exits.")
        self._action(tools, "Close Outline", self.close_outline)
        self._action(tools, "Finish Open", lambda: self.canvas.finish_outline(False))
        self._action(tools, "Cancel Draw", lambda: self.canvas.cancel_outline())
        self._action(tools, "Insert Point on Edge", self.insert_point_on_edge)
        self._action(tools, "Delete Selected Points", self.delete_selected_points)
        self.fuse_points_action = QtGui.QAction(_icon("fuse_Icon.png"), "Fuse Points", self)
        self.fuse_points_action.setShortcut("M")
        self.fuse_points_action.triggered.connect(self.start_point_merge)
        self.addAction(self.fuse_points_action)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(10, 8, 10, 8)
        self.file_label = QtWidgets.QLabel(str(self.path) if self.path else "Unsaved design · Save Pattern to create a shareable .pattern.json file")
        self.file_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)
        splitter = QtWidgets.QSplitter()
        layout.addWidget(splitter, 1)
        self.canvas = PatternCanvas()
        self.canvas.sewModeChanged.connect(self.sew_action.setChecked)
        self.canvas.sewRequested.connect(self._sew_requested)
        self.canvas.sewStatusChanged.connect(lambda text: self.statusBar().showMessage(text))
        splitter.addWidget(self.canvas)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)
        panel = QtWidgets.QWidget()
        scroll.setWidget(panel)
        splitter.addWidget(scroll)
        splitter.setSizes([760, 380])
        side = QtWidgets.QVBoxLayout(panel)
        side.addWidget(QtWidgets.QLabel("Pieces · millimeters"))
        self.pieces = _PanelList()
        self.pieces.setStyleSheet(
            "QListWidget { selection-background-color: #15552f; selection-color: #e1e8e3; }"
            "QListWidget::item:selected { background: #15552f; color: #e1e8e3; }"
        )
        self.pieces.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.pieces.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)
        self.pieces.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.pieces.setDropIndicatorShown(True)
        self.pieces.setMaximumHeight(145)
        self.pieces.itemSelectionChanged.connect(self._list_selection_changed)
        self.pieces.panelsClicked.connect(self._list_selection_changed)
        self.pieces.currentItemChanged.connect(self._piece_changed)
        self.pieces.orderChanged.connect(self._panel_order_changed)
        side.addWidget(self.pieces)
        buttons = QtWidgets.QHBoxLayout()
        for label, callback in (("Duplicate", lambda: self.duplicate(False)),
                                ("Mirror", lambda: self.duplicate(True)),
                                ("Fuse Points", self.fuse_points_action.trigger),
                                ("Combine", self.combine_selected_panels), ("Delete", self.delete_piece)):
            button = QtWidgets.QPushButton(label)
            if label == "Mirror":
                button.setIcon(_icon("Mirror_Icon.png"))
                self.mirror_button = button
            elif label == "Duplicate":
                button.setIcon(_icon("duplicate_icon.png"))
                self.duplicate_button = button
            elif label == "Combine":
                button.setIcon(_icon("Combine_Icon.png"))
                self.combine_button = button
                button.setToolTip("Make one panel; keep overlapping points separate")
            elif label == "Fuse Points":
                button.setIcon(self.fuse_points_action.icon())
                self.fuse_points_button = button
                button.setToolTip("Combine panels and fuse overlapping points (M)")
            button.clicked.connect(callback)
            buttons.addWidget(button)
        side.addLayout(buttons)
        tabs = QtWidgets.QTabWidget()
        side.addWidget(tabs)
        properties = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(properties)
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.editingFinished.connect(self._rename)
        form.addRow("Name", self.name_edit)
        self.width_edit, self.height_edit = _number(minimum=0.001), _number(minimum=0.001)
        form.addRow("Width (mm)", self.width_edit)
        form.addRow("Height (mm)", self.height_edit)
        size_button = QtWidgets.QPushButton("Apply Dimensions")
        size_button.clicked.connect(self.apply_dimensions)
        form.addRow(size_button)
        self.layout_x, self.layout_y, self.rotation = _number(), _number(), _number(minimum=-3600, maximum=3600)
        form.addRow("Sheet X", self.layout_x)
        form.addRow("Sheet Y", self.layout_y)
        form.addRow("Rotation (°)", self.rotation)
        placement = QtWidgets.QPushButton("Apply Placement")
        placement.clicked.connect(self.apply_placement)
        form.addRow(placement)
        tabs.addTab(properties, "Piece")

        self.points = QtWidgets.QTableWidget(0, 3)
        self.points.setHorizontalHeaderLabels(["Point", "X (mm)", "Y (mm)"])
        self.points.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.points.verticalHeader().hide()
        self.points.setMinimumHeight(220)
        self.points.itemChanged.connect(self._point_changed)
        tabs.addTab(self.points, "Points")
        edges = QtWidgets.QWidget()
        edge_form = QtWidgets.QFormLayout(edges)
        self.edges = QtWidgets.QComboBox()
        self.edges.currentIndexChanged.connect(self._edge_selected)
        edge_form.addRow("Edge", self.edges)
        self.edge_kind = QtWidgets.QComboBox()
        self.edge_kind.addItems(["line", "cubic"])
        self.edge_kind.currentTextChanged.connect(self._edge_kind_changed)
        edge_form.addRow("Type", self.edge_kind)
        self.controls = [_number() for _ in range(4)]
        for name, widget in zip(("Handle 1 X", "Handle 1 Y", "Handle 2 X", "Handle 2 Y"), self.controls):
            edge_form.addRow(name, widget)
        self.controls_button = QtWidgets.QPushButton("Apply Handles")
        self.controls_button.clicked.connect(self.apply_handles)
        edge_form.addRow(self.controls_button)
        tabs.addTab(edges, "Edges")

        self.diagnostics = QtWidgets.QLabel()
        self.diagnostics.setWordWrap(True)
        self.diagnostics.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        side.addWidget(self.diagnostics)
        side.addStretch()
        options = QtWidgets.QHBoxLayout()
        self.snap_box = QtWidgets.QCheckBox("Snap to grid")
        self.snap_box.setChecked(True)
        self.snap_box.toggled.connect(lambda enabled: setattr(self.canvas, "snap_enabled", enabled))
        options.addWidget(self.snap_box)
        grid = _number(10, 0.1, 1000)
        grid.setSuffix(" mm")
        grid.valueChanged.connect(self._grid_changed)
        options.addWidget(grid)
        options.addStretch()
        layout.addLayout(options)
        self.hint = QtWidgets.QLabel("Wheel or right-drag: zoom (right/up in, left/down out) · Middle drag: pan · Drag empty canvas: box-select points · Shift-drag: box-select anywhere · Ctrl-click: add/toggle points · Delete: remove points · Click edge: select · Double-click edge: insert point")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        queued = QtCore.Qt.ConnectionType.QueuedConnection
        self.canvas.pieceSelected.connect(self.select_piece, queued)
        self.canvas.panelsSelected.connect(self._canvas_panel_selected, queued)
        self.canvas.pointsSelectionChanged.connect(self._all_points_selected)
        self.canvas.fusePointsRequested.connect(self._fuse_selected_points)
        self.canvas.layoutEdited.connect(self._layout_dragged, queued)
        self.canvas.handleEdited.connect(self._handle_dragged, queued)
        self.canvas.edgesMoved.connect(self._edges_dragged)
        self.canvas.outlineCreated.connect(self._outline_created)
        self.canvas.drawingChanged.connect(self._drawing_changed)
        self.canvas.pointSelectionChanged.connect(self._points_selected)
        self.canvas.selectionCleared.connect(self._clear_canvas_selection)
        self.canvas.edgeSelected.connect(self._canvas_edge_selected)
        self.canvas.edgesSelectionChanged.connect(self._edges_selection_changed)
        self.canvas.deletePointsRequested.connect(self._delete_points_requested)
        self.canvas.deleteSelectionRequested.connect(self.delete_selection)
        self.canvas.splitEdgeRequested.connect(self._split_edge_requested)
        self.canvas.pointMergeRequested.connect(self._point_merge_requested)
        self.canvas.mergeModeChanged.connect(self._merge_mode_changed)

    def selected_piece(self):
        return next((p for p in self.document.pieces if p.id == self.selected_id), None)

    def edit(self, label, operation):
        try:
            changed = self.history.edit(label, operation)
        except (PatternError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self, "Pattern edit", str(exc))
            return False
        self.refresh()
        return changed

    def _sew_requested(self, left_piece, left_edge, right_piece, right_edge):
        from echograph.patterns.sewing import add_sew_pair

        try:
            self.history.edit("Sew edges", lambda doc: add_sew_pair(doc, left_piece, left_edge, right_piece, right_edge))
        except PatternError as exc:
            self.canvas.sewStatusChanged.emit(str(exc))
            return
        self.refresh()
        self.canvas.sewStatusChanged.emit("Edges sewn. Click two more edges to sew another pair; Esc exits.")

    def refresh(self, *, rebuild_pieces=True):
        self._refreshing = True
        piece = self.selected_piece()
        if piece is None:
            self.selected_id = self.document.pieces[0].id if self.document.pieces else ""
            piece = self.selected_piece()
        available = {p.id for p in self.document.pieces}
        existing_edges = {(piece.id, edge.id) for piece in self.document.pieces for edge in piece.segments}
        self._edge_selection &= existing_edges
        self._selected_panels &= available
        if self.selected_id:
            self._selected_panels.add(self.selected_id)
        self._point_selection = {key: ids & {p.id for p in self.document.piece(key).points}
                                 for key, ids in self._point_selection.items() if key in available}
        if rebuild_pieces:
            self._refresh_piece_list()
        else:
            for row in range(self.pieces.count()):
                item = self.pieces.item(row)
                if item.data(QtCore.Qt.ItemDataRole.UserRole) == self.selected_id:
                    self.pieces.setCurrentItem(item, QtCore.QItemSelectionModel.SelectionFlag.NoUpdate)
                item.setSelected(item.data(QtCore.Qt.ItemDataRole.UserRole) in self._selected_panels)
        diagnostics = [f"{entry.name}: {problem.message}" for entry in self.document.pieces
                       for problem in validate_piece(entry)]
        self._refresh_properties(piece, diagnostics)

    def _refresh_piece_list(self):
        self.pieces.clear()
        self._symmetry_buttons = {}
        for entry in self.document.pieces:
            problems = validate_piece(entry)
            item = QtWidgets.QListWidgetItem(entry.name + ("  [draft]" if problems else ""))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, entry.id)
            self.pieces.addItem(item)
            pair = relationships.symmetry_for_piece(self.document, entry.id)
            if pair:
                row = QtWidgets.QWidget()
                row_layout = QtWidgets.QHBoxLayout(row)
                row_layout.setContentsMargins(4, 1, 4, 1)
                label = QtWidgets.QLabel(item.text())
                item.setText("")
                item.setData(QtCore.Qt.ItemDataRole.AccessibleTextRole, entry.name)
                label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                row_layout.addWidget(label)
                toggle = QtWidgets.QToolButton()
                toggle.setIcon(_icon("Symmetry_Icon.png"))
                toggle.setIconSize(QtCore.QSize(18, 18))
                toggle.setFixedSize(24, 24)
                toggle.setAccessibleName(f"Symmetry for {entry.name}")
                toggle.setStyleSheet("QToolButton:checked { background: #24424e; border: 1px solid #32b9b0; border-radius: 4px; }")
                toggle.setCheckable(True)
                toggle.setChecked(pair["enabled"])
                if not pair["enabled"]:
                    opacity = QtWidgets.QGraphicsOpacityEffect(toggle)
                    opacity.setOpacity(0.4)
                    toggle.setGraphicsEffect(opacity)
                toggle.setToolTip("Linked symmetry: click to disable" if pair["enabled"] else
                                  "Symmetry disabled: click to enable using this panel's shape")
                toggle.clicked.connect(lambda checked, key=entry.id: self.toggle_symmetry(key, checked))
                row_layout.addWidget(toggle)
                row_layout.addStretch()
                item.setSizeHint(row.sizeHint())
                self.pieces.setItemWidget(item, row)
                self._symmetry_buttons[entry.id] = toggle
            if entry.id == self.selected_id:
                self.pieces.setCurrentItem(item, QtCore.QItemSelectionModel.SelectionFlag.NoUpdate)
            item.setSelected(entry.id in self._selected_panels)

        # Reserve the symmetry control's height on every row, including plain
        # panels, so multi-selection has a consistent highlight and spacing.
        row_height = max(28, self.pieces.fontMetrics().height() + 6)
        for index in range(self.pieces.count()):
            row_height = max(row_height, self.pieces.item(index).sizeHint().height())
        for index in range(self.pieces.count()):
            item = self.pieces.item(index)
            width = max(item.sizeHint().width(), self.pieces.fontMetrics().horizontalAdvance(
                item.text() or item.data(QtCore.Qt.ItemDataRole.AccessibleTextRole) or "") + 8)
            item.setSizeHint(QtCore.QSize(width, row_height))

    def _refresh_properties(self, piece, diagnostics):
        self.name_edit.setText(piece.name if piece else "")
        self.points.setRowCount(len(piece.points) if piece else 0)
        self.edges.clear()
        if piece:
            x0, y0, x1, y1 = bounds(piece)
            self.width_edit.setValue(x1 - x0)
            self.height_edit.setValue(y1 - y0)
            for widget, value in zip((self.layout_x, self.layout_y, self.rotation), piece.layout):
                widget.setValue(value)
            for row, point in enumerate(piece.points):
                for column, text in enumerate((str(row + 1), format(point.x, ".12g"), format(point.y, ".12g"))):
                    item = QtWidgets.QTableWidgetItem(text)
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, point.id)
                    if column == 0:
                        item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                    self.points.setItem(row, column, item)
            for index, edge in enumerate(piece.segments):
                self.edges.addItem(f"{index + 1}: {edge_length(piece, edge):.2f} mm", edge.id)
            index = self.edges.findData(self._edge_id)
            self.edges.setCurrentIndex(index)
        self.undo_action.setEnabled(bool(self.history.undo_stack))
        self.redo_action.setEnabled(bool(self.history.redo_stack))
        dirty = " *" if self.history.dirty else ""
        title = self.path.name if self.path else "Unsaved pattern"
        self.setWindowTitle(f"{title}{dirty} — Pattern Design")
        if diagnostics:
            self.diagnostics.setText("Drafts can be saved. Resolve before meshing:\n" + "\n".join(diagnostics[:10]))
            self.diagnostics.setStyleSheet("color: #da8e6d;")
        else:
            self.diagnostics.setText(f"{len(self.document.pieces)} valid panel(s)." if piece else "Add a template or draw an outline to begin.")
            self.diagnostics.setStyleSheet("color: #59b5a6;")
        self._refreshing = False
        self._edge_selected()
        self._show_canvas()

    def _show_canvas(self):
        selection = dict(self._point_selection)
        selection[self.selected_id] = self._selected_points
        self.canvas.show_document(self.document, self.selected_id, self._selected_points,
                                  (self.selected_id, self._edge_id), self._selected_panels, selection, self._edge_selection)

    def select_piece(self, piece_id):
        self.selected_id = piece_id
        self._edge_selection.clear()
        self._edge_id = ""
        self._selected_panels = {piece_id}
        self._point_selection.clear()
        self._selected_points.clear()
        self.refresh(rebuild_pieces=False)

    def _canvas_panel_selected(self, piece_id, additive, toggle=False):
        if not additive:
            return self.select_piece(piece_id)
        if toggle and piece_id in self._selected_panels and len(self._selected_panels) > 1:
            self._selected_panels.remove(piece_id)
            self._point_selection.pop(piece_id, None)
            self.selected_id = next(p.id for p in self.document.pieces if p.id in self._selected_panels)
        else:
            self._selected_panels.add(piece_id)
            self.selected_id = piece_id
        self._selected_points = set(self._point_selection.get(self.selected_id, ()))
        self.refresh(rebuild_pieces=False)

    def _list_selection_changed(self):
        if self._refreshing:
            return
        selected = {item.data(QtCore.Qt.ItemDataRole.UserRole) for item in self.pieces.selectedItems()}
        if not selected:
            return
        current = self.pieces.currentItem()
        self.selected_id = (current.data(QtCore.Qt.ItemDataRole.UserRole) if current and
                            current.data(QtCore.Qt.ItemDataRole.UserRole) in selected else next(iter(selected)))
        self._selected_panels = selected
        self._edge_selection.clear()
        self._edge_id = ""
        self._selected_points.clear()
        self._point_selection.clear()
        self.refresh(rebuild_pieces=False)

    def _all_points_selected(self, selection):
        self._point_selection = {key: set(ids) for key, ids in selection.items() if ids}
        self._selected_points = set(self._point_selection.get(self.selected_id, ()))

    def _panel_order_changed(self):
        ordered_ids = self.pieces.panel_ids()
        def reorder(doc):
            panels = {piece.id: piece for piece in doc.pieces}
            doc.pieces[:] = [panels[key] for key in ordered_ids]
        self.edit("Reorder panels", reorder)

    def _points_selected(self, piece_id, point_ids):
        self._edge_selection.clear()
        self._edge_id = ""
        self._edges_deselected = True
        with QtCore.QSignalBlocker(self.edges):
            self.edges.setCurrentIndex(-1)
        self.edge_kind.setEnabled(False)
        self.controls_button.setEnabled(False)
        for control in self.controls:
            control.setEnabled(False)
        self.selected_id = piece_id
        self._selected_points = set(point_ids)

    def _clear_canvas_selection(self):
        self._edge_selection.clear()
        self._point_selection.clear()
        self._selected_points.clear()
        self._edges_deselected = True
        self.edges.setCurrentIndex(-1)
        self._edge_selected()

    def _canvas_edge_selected(self, piece_id, edge_id):
        self._edge_selection = {(piece_id, edge_id)}
        self._point_selection.clear()
        self._selected_points.clear()
        self._edges_deselected = False
        if piece_id != self.selected_id:
            self._selected_points.clear()
        self.selected_id, self._edge_id = piece_id, edge_id
        self.refresh()

    def _edges_selection_changed(self, piece_id, edge_id, selection):
        self._edge_selection = set(selection)
        self._selected_panels.update(key for key, _ in selection)
        self.selected_id, self._edge_id = piece_id, edge_id
        self._selected_points = set(self._point_selection.get(piece_id, ()))
        self._edges_deselected = False
        self.refresh()

    def _delete_points_requested(self, piece_id, point_ids):
        self.selected_id = piece_id
        self.delete_selected_points(point_ids)

    def delete_selected_points(self, point_ids=None):
        piece = self.selected_piece()
        if isinstance(point_ids, bool):
            point_ids = None
        point_ids = list(point_ids if point_ids is not None else self._selected_points)
        if not piece or not point_ids:
            return
        self._selected_points.clear()
        self.edit("Delete point" if len(point_ids) == 1 else "Delete points",
                  lambda doc: commands.delete_points(doc.piece(self.selected_id), point_ids))

    def delete_selection(self):
        points = {key: set(ids) for key, ids in self._point_selection.items() if ids}
        if self._selected_points:
            points[self.selected_id] = set(self._selected_points)
        edges = set(self._edge_selection)
        panels = set(self._selected_panels)
        def operation(doc):
            if points:
                for key, ids in points.items():
                    commands.delete_points(doc.piece(key), ids)
            elif edges:
                for key in {key for key, _ in edges}:
                    commands.delete_edges(doc.piece(key), {edge for owner, edge in edges if owner == key})
            else:
                doc.pieces[:] = [piece for piece in doc.pieces if piece.id not in panels]
        self._point_selection.clear()
        self._selected_points.clear()
        self._edge_selection.clear()
        self._edge_id = ""
        self.edit("Delete points" if points else "Delete edges" if edges else "Delete panels", operation)

    def _split_edge_requested(self, piece_id, edge_id):
        self.selected_id, self._edge_id = piece_id, edge_id
        self.insert_point_on_edge()

    def insert_point_on_edge(self):
        if not self.selected_piece() or not self._edge_id:
            return
        created = []
        def split(doc):
            created.append(commands.split_segment(doc.piece(self.selected_id), self._edge_id))
        if self.edit("Insert point on edge", split) and created:
            self._selected_points = {created[0]}
            self.refresh()

    def _piece_changed(self, current, _previous):
        if not self._refreshing and current and current.isSelected():
            self.selected_id = current.data(QtCore.Qt.ItemDataRole.UserRole)
            self._selected_points = set(self._point_selection.get(self.selected_id, ()))
            self.refresh(rebuild_pieces=False)

    def template_dialog(self, trapezoid=False):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("New Trapezoid" if trapezoid else "New Rectangle")
        form = QtWidgets.QFormLayout(dialog)
        name = QtWidgets.QLineEdit(f"Panel {len(self.document.pieces) + 1}")
        width, height, top = _number(300, 0.001), _number(400, 0.001), _number(200, 0.001)
        form.addRow("Name", name)
        form.addRow("Width (mm)", width)
        form.addRow("Height (mm)", height)
        if trapezoid:
            form.addRow("Top width (mm)", top)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.add_template(name.text(), width.value(), height.value(), top.value() if trapezoid else None)

    def add_template(self, name, width=300, height=400, top_width=None):
        piece = commands.rectangle(name.strip() or "Panel", width, height, top_width=top_width)
        if self.document.pieces:
            piece.layout[0] = max(p.layout[0] + bounds(p)[2] for p in self.document.pieces) + 40
        self.selected_id = piece.id
        self.edit("Add template", lambda doc: doc.pieces.append(piece))
        self.canvas.fit_pieces()

    def _outline_created(self, coordinates, closed):
        x0, y0 = min(p[0] for p in coordinates), min(p[1] for p in coordinates)
        piece = commands.polygon(f"Panel {len(self.document.pieces) + 1}",
                                 [(x - x0, y - y0) for x, y in coordinates], closed=closed)
        piece.layout = [x0, y0, 0.0]
        self.selected_id = piece.id
        self.edit("Draw outline", lambda doc: doc.pieces.append(piece))

    def close_outline(self):
        if self.canvas.drawing:
            self.canvas.finish_outline()
        elif self.selected_piece():
            self.edit("Close outline", lambda doc: commands.close_outline(doc.piece(self.selected_id)))

    def duplicate(self, mirror=False):
        original = self.selected_piece()
        if original is None:
            return
        def duplicate(doc):
            if mirror:
                piece = relationships.mirror_piece(doc, original.id)
            else:
                piece = commands.duplicate_piece(doc.piece(original.id))
                doc.pieces.append(piece)
            self.selected_id = piece.id
        self.edit("Mirror piece" if mirror else "Duplicate piece", duplicate)
        self.canvas.fit_pieces()

    def toggle_symmetry(self, piece_id, enabled):
        self.edit("Enable symmetry" if enabled else "Disable symmetry",
                  lambda doc: relationships.set_symmetry_enabled(doc, piece_id, enabled))

    def start_point_merge(self):
        selection = dict(self._point_selection)
        selection[self.selected_id] = self._selected_points
        if sum(len(ids) for ids in selection.values()) > 1:
            self._fuse_selected_points(selection)
            return
        if not any(selection.values()) and self._selected_panels:
            self._combine_panels(self._selected_panels, fuse_all=True)
            return
        if len(self._selected_points) != 1:
            self.hint.setText("Select panels to fuse all overlapping points, or select points to limit the fuse.")
            return
        self.canvas.start_point_merge(self.selected_id, next(iter(self._selected_points)))

    def _fuse_selected_points(self, selection):
        keys = {key for key, ids in selection.items() if ids}
        if keys:
            self._combine_panels(keys, fuse_selection=selection)
        else:
            self._combine_panels(self._selected_panels, fuse_all=True)

    def combine_selected_panels(self):
        if len(self._selected_panels) < 2:
            self.hint.setText("Select at least two panels, then choose Combine.")
            return
        self._combine_panels(self._selected_panels)

    def _combine_panels(self, keys, *, fuse_selection=None, fuse_all=False):
        def operation(doc):
            result = relationships.combine_panels(doc, keys, fuse_selection=fuse_selection, fuse_all=fuse_all)
            self.selected_id = result.id
            self._selected_panels = {result.id}
            self._selected_points.clear()
            self._point_selection.clear()
        self.edit("Fuse Points" if fuse_all or fuse_selection else "Combine panels", operation)

    def _merge_mode_changed(self, active):
        if active:
            self.hint.setText("Click a point on another panel to fuse. Symmetry will turn off; Esc cancels.")
        else:
            self._drawing_changed(self.canvas.drawing)

    def _point_merge_requested(self, source_piece, source_point, target_piece, target_point):
        def operation(doc):
            relationships.merge_points(doc, source_piece, source_point, target_piece, target_point)
            result = relationships.combine_panels(doc, {source_piece, target_piece}, fuse_selection={
                source_piece: {source_point}, target_piece: {target_point}})
            self.selected_id = result.id
            self._selected_panels = {result.id}
            self._selected_points.clear()
            self._point_selection.clear()
        self.edit("Fuse Points", operation)

    def delete_piece(self):
        if self.selected_piece():
            key = self.selected_id
            self.edit("Delete piece", lambda doc: setattr(doc, "pieces", [p for p in doc.pieces if p.id != key]))

    def _rename(self):
        if not self._refreshing and self.selected_piece():
            name = self.name_edit.text().strip() or "Panel"
            self.edit("Rename piece", lambda doc: setattr(doc.piece(self.selected_id), "name", name))

    def apply_dimensions(self):
        if self.selected_piece():
            width, height = self.width_edit.value(), self.height_edit.value()
            self.edit("Resize piece", lambda doc: commands.resize_piece(doc.piece(self.selected_id), width, height))

    def apply_placement(self):
        if self.selected_piece():
            layout = [self.layout_x.value(), self.layout_y.value(), self.rotation.value()]
            self.edit("Arrange piece", lambda doc: setattr(doc.piece(self.selected_id), "layout", layout))

    def _layout_dragged(self, piece_id, x, y):
        self.selected_id = piece_id
        def change(doc):
            doc.piece(piece_id).layout[:2] = [x, y]
        self.edit("Arrange piece", change)

    def _handle_dragged(self, piece_id, entity_id, index, x, y):
        self.selected_id = piece_id
        def change(doc):
            piece = doc.piece(piece_id)
            if index < 0:
                commands.move_point(piece, entity_id, x, y)
            else:
                piece.segment(entity_id).controls[index] = [x, y]
                piece.metadata.pop("template", None)
        self.edit("Move point" if index < 0 else "Edit curve", change)

    def _edges_dragged(self, selection, dx, dy):
        self.edit("Move edges", lambda doc: commands.translate_edges(doc, selection, dx, dy))

    def _point_changed(self, item):
        if self._refreshing or not self.selected_piece():
            return
        point_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        point = self.selected_piece().point(point_id)
        x, y = point.x, point.y
        try:
            if item.column() == 1:
                x = float(item.text())
            elif item.column() == 2:
                y = float(item.text())
            else:
                return
        except ValueError:
            self.refresh()
            return
        self.edit("Set point coordinates", lambda doc: commands.move_point(doc.piece(self.selected_id), point_id, x, y))

    def _edge_selected(self, _index=None):
        if self._refreshing:
            return
        self._refreshing = True
        piece = self.selected_piece()
        self._edge_id = self.edges.currentData() or ""
        if _index is not None:
            self._edge_selection = {(self.selected_id, self._edge_id)} if self._edge_id else set()
            if self._edge_id:
                self._point_selection.clear()
                self._selected_points.clear()
        if self._edge_id:
            self._edges_deselected = False
        edge = piece.segment(self._edge_id) if piece and self._edge_id else None
        self.edge_kind.setEnabled(edge is not None)
        self.edge_kind.setCurrentText(edge.kind if edge else "line")
        cubic = bool(edge and edge.kind == "cubic")
        values = edge.controls[0] + edge.controls[1] if cubic else [0, 0, 0, 0]
        for widget, value in zip(self.controls, values):
            widget.setValue(value)
            widget.setEnabled(cubic)
        self.controls_button.setEnabled(cubic)
        self._refreshing = False
        self._show_canvas()

    def _edge_kind_changed(self, kind):
        if not self._refreshing and self.selected_piece() and self._edge_id:
            self.edit("Change edge type", lambda doc: commands.set_edge_kind(doc.piece(self.selected_id), self._edge_id, kind))

    def apply_handles(self):
        if self.selected_piece() and self._edge_id:
            values = [c.value() for c in self.controls]
            def change(doc):
                piece = doc.piece(self.selected_id)
                piece.segment(self._edge_id).controls = [values[:2], values[2:]]
                piece.metadata.pop("template", None)
            self.edit("Set curve handles", change)

    def _grid_changed(self, value):
        self.canvas.grid_mm = value
        self.canvas.viewport().update()

    def _drawing_changed(self, drawing):
        self.hint.setText("Click points · Enter or click first point: close · Shift+Enter: keep open · Backspace: remove point · Esc: cancel"
                          if drawing else "Wheel or right-drag: zoom (right/up in, left/down out) · Middle drag: pan · Drag empty canvas: box-select points · Shift-drag: box-select anywhere · Ctrl-click: add/toggle points · Delete: remove points · Click edge: select · Double-click edge: insert point")

    def undo(self):
        self.history.undo()
        self.refresh()

    def redo(self):
        self.history.redo()
        self.refresh()

    def save(self) -> bool:
        focused = self.focusWidget()
        if focused is not None:
            focused.clearFocus()
        self._flush_draft()
        target = self.path
        expected = self.disk_hash
        if target is None:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Pattern File", "design.pattern.json", PATTERN_FILTER
            )
            if not path:
                return False
            target = Path(pattern_filename(path)).expanduser().resolve()
        return self._save_to(target, expected)

    def _save_to(self, target, expected) -> bool:
        try:
            signature = save_document(target, self.document, expected_hash=expected)
        except (OSError, PatternError) as exc:
            QtWidgets.QMessageBox.warning(self, "Could not save pattern", str(exc))
            return False
        self.path = target
        self.disk_hash = signature
        self.history.mark_saved()
        self.refresh()
        self.file_label.setText(str(self.path))
        self.file_label.setToolTip(str(self.path))
        self.reload_action.setEnabled(True)
        self.saved.emit(str(self.path))
        self.statusBar().showMessage("Pattern saved. Workflows reference this separate file.", 5000)
        return True

    def save_as(self) -> bool:
        focused = self.focusWidget()
        if focused is not None:
            focused.clearFocus()
        self._flush_draft()
        initial = str(self.path) if self.path else "design.pattern.json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Pattern As", initial, PATTERN_FILTER,
            options=QtWidgets.QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return False
        target = Path(pattern_filename(path)).expanduser().resolve()
        expected = self.disk_hash if target == self.path else None
        try:
            if target != self.path and target.exists():
                # Validate the final filename, including an appended extension,
                # before confirming replacement of an existing pattern.
                _, expected = load_document(target)
                choice = QtWidgets.QMessageBox.question(
                    self, "Replace Pattern File", f"Replace the existing pattern file {target.name}?",
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No)
                if choice != QtWidgets.QMessageBox.StandardButton.Yes:
                    return False
        except (OSError, PatternError) as exc:
            QtWidgets.QMessageBox.warning(self, "Could not save pattern as", str(exc))
            return False
        return self._save_to(target, expected)

    def _flush_draft(self):
        if self.canvas.drawing and self.canvas.draft_points:
            self.canvas.finish_outline(False)

    def _confirm_discard(self):
        self._flush_draft()
        if not self.history.dirty:
            return True
        name = self.path.name if self.path else "this new pattern"
        choice = QtWidgets.QMessageBox.question(self, "Unsaved pattern changes", f"Save changes to {name}?",
            QtWidgets.QMessageBox.StandardButton.Save | QtWidgets.QMessageBox.StandardButton.Discard | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Save)
        if choice == QtWidgets.QMessageBox.StandardButton.Save:
            return self.save()
        return choice == QtWidgets.QMessageBox.StandardButton.Discard

    def reload(self):
        if self.path is None:
            return
        if not self._confirm_discard():
            return
        try:
            document, signature = load_document(self.path)
        except (OSError, PatternError) as exc:
            QtWidgets.QMessageBox.warning(self, "Could not reload pattern", str(exc))
            return
        self.disk_hash = signature
        self.history.undo_stack.clear()
        self.history.redo_stack.clear()
        self.history.document = document
        self.history.mark_saved()
        self.refresh()
        self.saved.emit(str(self.path))

    def closeEvent(self, event):
        if self._confirm_discard():
            _EDITORS.discard(self)
            event.accept()
        else:
            event.ignore()
