from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import weakref

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from echograph.patterns.document import PatternDocument, PatternError
from echograph.patterns.relationships import merge_points, combine_panels
from echograph.patterns.geometry import bounds, edge_length, validate_piece
from echograph.patterns.commands import DocumentHistory, delete_points, move_point, polygon, rectangle, set_edge_kind, split_segment
from echograph.patterns.serialization import file_hash, load_document, resolve_pattern_path, save_document
from nodes.core import Spec

PATTERN_NODE_W = 704
PATTERN_BODY_H = 520


def _param(model, key):
    return next((str(p.get("value") or "") for p in model.params
                 if p.get("name", "").lower() == key), "")


def _window(node_item):
    scene = node_item.scene()
    if scene and scene.views():
        return scene.views()[0].window()
    return None


def pattern_path(node_item):
    window = _window(node_item)
    return resolve_pattern_path(_param(node_item.model, "pattern_path"),
                                workflow_path=getattr(window, "_current_path", None))


def resolve_pattern_document(node_item) -> dict:
    """Resolve the saved file for downstream consumers; never embed it in params.

    Draft geometry is returned with explicit diagnostics. Consumers must require
    `ready` before meshing; unsaved editor state is deliberately not published.
    """
    path = pattern_path(node_item)
    document, signature = load_document(path)
    diagnostics = [asdict(d) for piece in document.pieces for d in validate_piece(piece)]
    return {"path": str(path), "document": document.to_dict(), "file_hash": signature,
            "revision": document.revision, "ready": bool(document.pieces) and not diagnostics,
            "diagnostics": diagnostics}


def build_pattern_scene_asset(node_item):
    """Publish only saved, ready panels, using the standard Scene mesh loader."""
    from echograph.patterns.mesh import cached_mesh

    try:
        resolved = resolve_pattern_document(node_item)
        if not resolved["ready"]:
            messages = [d["message"] for d in resolved["diagnostics"]]
            raise PatternError(messages[0] if messages else "Add a closed panel and save the pattern first.")
        path = cached_mesh(PatternDocument.from_dict(resolved["document"]))
        from echograph.physics.cloth import ClothSettings, build_cloth_asset
        from echograph.physics.cloth.placement import validate_placements
        placement_document = PatternDocument.from_dict(resolved["document"])
        placement_document.metadata["sew_pairs"] = []
        asset = build_cloth_asset(placement_document)
        placements = validate_placements(json.loads(str(next((p.get("value", "{}") for p in node_item.model.params
                                         if p.get("name") == "piece_transforms"), "{}"))))
    except (OSError, PatternError, ValueError, TypeError) as exc:
        node_item.model._pattern_scene_error = str(exc)
        return None
    node_item.model._pattern_scene_error = ""
    return {"node": node_item.model.name, "path": str(path), "ext": ".obj",
            "kind": "mesh", "source_kind": "pattern_design", "texture": "",
            "pattern_pieces": [{"id": p["id"], "name": p["name"]} for p in asset.pieces()],
            "cloth": {"asset": asset, "settings": ClothSettings(device="cpu", enabled=False).to_dict(),
                      "piece_transforms": placements, "placement_only": True, "pins": [], "colliders": [],
                      "cache_path": "", "node_model": node_item.model, "node_item": node_item}}


def build_ports(node_item):
    params = node_item.model.params
    if not any(p.get("name") == "pattern_path" for p in params):
        params.append({"name": "pattern_path", "value": ""})
    if not any(p.get("name") == "piece_transforms" for p in params):
        params.append({"name": "piece_transforms", "value": "{}"})
    hidden = next((p for p in params if p.get("name") == "__ui_hidden_params"), None)
    if hidden is None:
        hidden = {"name": "__ui_hidden_params", "value": ""}
        params.append(hidden)
    names = {n.strip() for n in str(hidden["value"]).split(",") if n.strip()}
    names.add("pattern_path")
    names.add("piece_transforms")
    hidden["value"] = ",".join(sorted(names))
    node_item.ensure_output("pattern")
    node_item._hide_default_output_with_named = True


class PatternNodeWidget(QtWidgets.QWidget):
    def __init__(self, node_item):
        super().__init__()
        self.node_item = node_item
        self._editor = None
        self._signature = None
        self._selected_id = ""
        self._selected_points = set()
        self._selected_panels = set()
        self._point_selection = {}
        self._selected_edge_id = ""
        self._edge_selection = set()
        self._edges_deselected = False
        self._render_key = None
        self._file_stat = None
        self._canvas_help = ""
        self._control_help = ""
        self._base_status = "Draw a panel, then save the pattern."
        raw_path = _param(node_item.model, "pattern_path")
        self._auto_fit_on_load = bool(raw_path)
        if raw_path:
            try:
                document, signature = load_document(pattern_path(node_item))
                self.history = DocumentHistory(document)
                self.history.mark_saved()
                self.node_item.model._pattern_disk_hash = signature
                self._signature = signature
                current_path = pattern_path(node_item)
                stat = current_path.stat()
                self._file_stat = (str(current_path), stat.st_mtime_ns, stat.st_size)
                self.node_item.model._pattern_file_content_hash = document.content_hash()
                self._selected_id = document.pieces[0].id if document.pieces else ""
            except (OSError, PatternError):
                # Preserve a broken/moved reference until the user chooses a
                # replacement. Opening the missing path must never make a blank file.
                self.history = DocumentHistory(PatternDocument())
                self.node_item.model._pattern_missing = True
        else:
            cached = getattr(node_item.model, "_pattern_history", None)
            self.history = cached or DocumentHistory(PatternDocument())
            self._selected_id = self.history.document.pieces[0].id if self.history.document.pieces else ""
        self.node_item.model._pattern_history = self.history
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 4)
        layout.setSpacing(4)
        row = QtWidgets.QHBoxLayout()
        for title, callback in (("New Pattern", self.new_pattern), ("Open / Locate", self.open_pattern),
                                ("Save .pattern.json", self.save_pattern), ("Open Full Editor", self.open_editor)):
            button = QtWidgets.QPushButton(title)
            self._set_help(button, {
                "New Pattern": "Start a new pattern.",
                "Open / Locate": "Open or locate a pattern file.",
                "Save .pattern.json": "Save the pattern and update Scene.",
                "Open Full Editor": "Open the full pattern editor.",
            }[title])
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.filename = QtWidgets.QLabel()
        self.filename.setMinimumWidth(0)
        self.filename.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        layout.addWidget(self.filename)

        from echograph.ui.pattern_editor.canvas import PatternCanvas, sew_icon, elastic_icon
        self.canvas = PatternCanvas()
        self.canvas.sewRequested.connect(self._sew_requested)
        self.canvas.elasticRequested.connect(self._elastic_requested)
        self.canvas.sewStatusChanged.connect(self._sew_status_changed)
        self.canvas.setMinimumSize(500, 340)
        self.canvas.setMaximumHeight(420)
        self.canvas.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                  QtWidgets.QSizePolicy.Policy.Expanding)
        self.canvas.setMouseTracking(True)
        self.canvas.viewport().installEventFilter(self)
        layout.addWidget(self.canvas, 1)
        self.canvas.pieceSelected.connect(self._piece_selected, QtCore.Qt.ConnectionType.QueuedConnection)
        self.canvas.layoutEdited.connect(self._layout_dragged, QtCore.Qt.ConnectionType.QueuedConnection)
        self.canvas.handleEdited.connect(self._handle_dragged, QtCore.Qt.ConnectionType.QueuedConnection)
        self.canvas.edgesMoved.connect(self._edges_dragged)
        self.canvas.pointsMoved.connect(self._points_dragged)
        self.canvas.outlineCreated.connect(self._outline_created)
        self.canvas.drawingChanged.connect(self._drawing_changed)
        self.canvas.mergeModeChanged.connect(lambda _active: self._update_status_text())
        self.canvas.pointSelectionChanged.connect(self._points_selected)
        self.canvas.pointsSelectionChanged.connect(self._all_points_selected)
        self.canvas.panelsSelected.connect(self._panels_selected, QtCore.Qt.ConnectionType.QueuedConnection)
        self.canvas.fusePointsRequested.connect(self._fuse_selected_points)
        self.canvas.selectionCleared.connect(self._clear_canvas_selection)
        self.canvas.edgeSelected.connect(self._canvas_edge_selected)
        self.canvas.edgesSelectionChanged.connect(self._edges_selection_changed)
        self.canvas.deletePointsRequested.connect(self._delete_points_requested)
        self.canvas.deleteSelectionRequested.connect(self.delete_selection)
        self.canvas.splitEdgeRequested.connect(self._split_edge_requested)
        self.canvas.pointMergeRequested.connect(self._point_merge_requested)

        tools = QtWidgets.QVBoxLayout()
        template_row = QtWidgets.QHBoxLayout()
        self.width_edit = QtWidgets.QDoubleSpinBox()
        self.width_edit.setRange(0.1, 100000)
        self.width_edit.setValue(300)
        self.width_edit.setSuffix(" mm W")
        self.width_edit.setMaximumWidth(148)
        self.height_edit = QtWidgets.QDoubleSpinBox()
        self.height_edit.setRange(0.1, 100000)
        self.height_edit.setValue(400)
        self.height_edit.setSuffix(" mm H")
        self.height_edit.setMaximumWidth(148)
        template_row.addWidget(self.width_edit)
        template_row.addWidget(self.height_edit)
        for label, callback in (("+ Rectangle", self.add_rectangle),
                                ("+ Trapezoid", self.add_trapezoid)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            template_row.addWidget(button)
        drawing_row = QtWidgets.QHBoxLayout()
        for label, callback in (("Draw Points", self.canvas.start_outline),
                                ("Close", lambda: self._finish_outline(True)),
                                ("Undo", self.undo), ("Redo", self.redo)):
            button = QtWidgets.QPushButton(label)
            self._set_help(button, {"Draw Points": "Click: draw · Enter: close · Esc: cancel",
                                   "Close": "Close the drawn outline.", "Undo": "Undo the last edit.",
                                   "Redo": "Redo the last edit."}[label])
            button.clicked.connect(callback)
            drawing_row.addWidget(button)
        self.delete_points_button = QtWidgets.QPushButton("Delete Point(s)")
        self._set_help(self.delete_points_button, "Delete selected points · Shift-drag: box select")
        self.delete_points_button.clicked.connect(self.delete_selected_points)
        drawing_row.addWidget(self.delete_points_button)
        self.sew_button = QtWidgets.QToolButton()
        self.sew_button.setText("Sew")
        self.sew_button.setIcon(sew_icon())
        self.sew_button.setIconSize(QtCore.QSize(22, 22))
        self.sew_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sew_button.setCheckable(True)
        self._set_help(self.sew_button, "Choose two edges to sew · Esc: exit")
        self.sew_button.toggled.connect(self.canvas.set_sew_mode)
        self.canvas.sewModeChanged.connect(self.sew_button.setChecked)
        drawing_row.addWidget(self.sew_button)
        self.elastic_button = QtWidgets.QToolButton()
        self.elastic_button.setText("Elastic")
        self.elastic_button.setIcon(elastic_icon())
        self.elastic_button.setIconSize(QtCore.QSize(22, 22))
        self.elastic_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.elastic_button.setCheckable(True)
        self._set_help(self.elastic_button, "Click an edge to make it elastic · Esc: exit")
        self.elastic_button.toggled.connect(self.canvas.set_elastic_mode)
        self.canvas.elasticModeChanged.connect(self.elastic_button.setChecked)
        drawing_row.addWidget(self.elastic_button)
        tools.addLayout(template_row)
        tools.addLayout(drawing_row)
        layout.addLayout(tools)
        curve_row = QtWidgets.QHBoxLayout()
        self.edge_combo = QtWidgets.QComboBox()
        self.edge_combo.setMinimumWidth(0)
        self.edge_combo.currentIndexChanged.connect(self._edge_selected)
        curve_row.addWidget(QtWidgets.QLabel("Selected edge"))
        curve_row.addWidget(self.edge_combo, 1)
        self.curve_button = QtWidgets.QPushButton("Make Cubic Curve")
        self._set_help(self.curve_button, "Make a curve · Drag orange handles to shape")
        self.curve_button.clicked.connect(self._make_cubic)
        curve_row.addWidget(self.curve_button)
        self.insert_point_button = QtWidgets.QPushButton("Insert Point")
        self._set_help(self.insert_point_button, "Add a point to the selected edge")
        self.insert_point_button.clicked.connect(self.insert_point_on_edge)
        curve_row.addWidget(self.insert_point_button)
        self.fit_button = QtWidgets.QPushButton("Fit")
        self._set_help(self.fit_button, "Frame all pattern panels")
        self.fit_button.clicked.connect(self.canvas.fit_pieces)
        curve_row.addWidget(self.fit_button)
        layout.addLayout(curve_row)
        from echograph.ui.pattern_editor.elastic import ElasticEdgeControls
        self.elastic_controls = ElasticEdgeControls()
        self.elastic_controls.edited.connect(self._elastic_edited)
        self._set_help(self.elastic_controls.enabled, "Enable elastic on the selected edges")
        self._set_help(self.elastic_controls.strength, "Higher strength pulls harder")
        self._set_help(self.elastic_controls.rest_length, "80% rest length tightens the edge by 20%")
        layout.addWidget(self.elastic_controls)
        self.status = QtWidgets.QLabel(self._base_status)
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll_file)
        self.timer.start()
        QtCore.QTimer.singleShot(0, self, self.refresh)

    def sizeHint(self):
        return super().sizeHint().expandedTo(QtCore.QSize(PATTERN_NODE_W, PATTERN_BODY_H))

    def _set_help(self, widget, text):
        widget.setProperty("pattern_help", text)
        widget.setToolTip("")
        widget.installEventFilter(self)

    def _canvas_hover_help(self, pixel):
        from echograph.ui.pattern_editor.canvas import HandleItem, PieceItem

        item = self.canvas.itemAt(pixel)
        if isinstance(item, HandleItem):
            return ("Drag: shape curve" if item.control_index >= 0 else
                    "Drag: move · Ctrl-click: toggle · Delete: remove")
        if self.canvas._edge_at(pixel):
            return "Drag: move edge · Double-click: add point"
        while item is not None:
            if isinstance(item, PieceItem):
                return "Drag: move panel · Shift-drag: box select"
            item = item.parentItem()
        return "Drag: box select · Middle-drag: pan · Wheel: zoom"

    def _update_status_text(self):
        if not hasattr(self, "status"):
            return
        mode_help = getattr(self, "_sew_status", "")
        if self.canvas.drawing:
            mode_help = "Click: draw · Enter: close · Shift+Enter: draft · Esc: cancel"
        elif self.canvas._merge_source:
            mode_help = "Fuse: choose another panel's point · Esc: cancel"
        text = self._control_help or mode_help or self._canvas_help or self._base_status
        if self.status.text() != text:
            self.status.setText(text)

    def eventFilter(self, watched, event):
        kind = event.type()
        canvas = getattr(self, "canvas", None)
        if canvas is not None and watched is canvas.viewport():
            if kind in (QtCore.QEvent.Type.Enter, QtCore.QEvent.Type.MouseMove, QtCore.QEvent.Type.ToolTip):
                pixel = (event.position().toPoint() if hasattr(event, "position") else
                         event.pos() if hasattr(event, "pos") else
                         watched.mapFromGlobal(QtGui.QCursor.pos()))
                self._canvas_help = self._canvas_hover_help(pixel)
                self._update_status_text()
            elif kind == QtCore.QEvent.Type.Leave:
                self._canvas_help = ""
                self._update_status_text()
        elif watched.property("pattern_help"):
            if kind in (QtCore.QEvent.Type.Enter, QtCore.QEvent.Type.ToolTip):
                self._control_help = watched.property("pattern_help")
                self._update_status_text()
            elif kind == QtCore.QEvent.Type.Leave:
                self._control_help = ""
                self._update_status_text()
        else:
            return super().eventFilter(watched, event)
        if kind == QtCore.QEvent.Type.ToolTip:
            QtWidgets.QToolTip.hideText()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _directory(self):
        try:
            return str(pattern_path(self.node_item).parent)
        except PatternError:
            window = _window(self.node_item)
            current = getattr(window, "_current_path", "")
            return str(Path(current).parent) if current else str(Path.cwd())

    def attach(self, path):
        resolved = Path(path).expanduser().resolve()
        document, signature = load_document(resolved)  # Never attach a workflow JSON or an invalid pattern.
        if self.history.dirty:
            choice = QtWidgets.QMessageBox.question(self, "Unsaved draft", "Save the current design before opening another pattern?",
                QtWidgets.QMessageBox.StandardButton.Save | QtWidgets.QMessageBox.StandardButton.Discard | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Save)
            if choice == QtWidgets.QMessageBox.StandardButton.Save:
                if not self.save_pattern():
                    return False
            elif choice != QtWidgets.QMessageBox.StandardButton.Discard:
                return False
        self.node_item._set_param_value("pattern_path", str(resolved), rebuild=False, notify_scene=True)
        self.node_item.model._pattern_history = DocumentHistory(document)
        self.node_item.model._pattern_disk_hash = signature
        self.node_item.model._pattern_file_content_hash = document.content_hash()
        self.node_item.model._pattern_missing = False
        self.history = self.node_item.model._pattern_history
        self._selected_id = document.pieces[0].id if document.pieces else ""
        self._auto_fit_on_load = True
        self._selected_points.clear()
        self._selected_edge_id = ""
        self._signature = signature
        stat = resolved.stat()
        self._file_stat = (str(resolved), stat.st_mtime_ns, stat.st_size)
        self.node_item.model._pattern_expected_file_stat = self._file_stat
        self._render_key = None
        self.refresh()
        return True

    def _param_path(self):
        return _param(self.node_item.model, "pattern_path")

    def _replace_current(self):
        if self.history.dirty:
            choice = QtWidgets.QMessageBox.question(self, "Unsaved pattern", "Save this design before starting another?",
                QtWidgets.QMessageBox.StandardButton.Save | QtWidgets.QMessageBox.StandardButton.Discard | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Save)
            if choice == QtWidgets.QMessageBox.StandardButton.Save and not self.save_pattern():
                return False
            if choice == QtWidgets.QMessageBox.StandardButton.Cancel:
                return False
        self.node_item._set_param_value("pattern_path", "", rebuild=False, notify_scene=True)
        self.history = DocumentHistory(PatternDocument())
        self.node_item.model._pattern_history = self.history
        self.node_item.model._pattern_disk_hash = None
        self.node_item.model._pattern_missing = False
        self.node_item.model._pattern_expected_file_stat = None
        self._selected_id = ""
        self._auto_fit_on_load = False
        self._selected_points.clear()
        self._selected_edge_id = ""
        self._signature = None
        self._file_stat = None
        self._render_key = None
        self.refresh()
        return True

    def new_pattern(self):
        # Creating a draft is immediate and only opens the canvas; the file
        # picker appears after the user has drawn something and chooses Save.
        self._replace_current()

    def open_pattern(self):
        from echograph.ui.pattern_editor.editor import PATTERN_FILTER
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open Pattern File", self._directory(), PATTERN_FILTER)
        if path:
            try:
                self.attach(path)
                self.open_editor()
            except (OSError, PatternError) as exc:
                QtWidgets.QMessageBox.warning(self, "Could not open pattern", str(exc))

    def open_editor(self):
        from echograph.ui.pattern_editor.editor import PatternEditor
        try:
            raw_path = self._param_path()
            path = pattern_path(self.node_item) if raw_path else None
            reference = getattr(self.node_item.model, "_pattern_editor_ref", None)
            editor = reference() if reference else None
            if editor is None or not isValid(editor) or editor.path != path or editor.history is not self.history:
                editor = PatternEditor(path, _window(self.node_item), history=self.history)
                self.node_item.model._pattern_editor_ref = weakref.ref(editor)
            if self._editor is not editor:
                editor.saved.connect(self._on_saved)
            self._editor = editor
            editor.show()
            editor.raise_()
            editor.activateWindow()
        except (OSError, PatternError) as exc:
            QtWidgets.QMessageBox.warning(self, "Could not open pattern", str(exc))

    def save_pattern(self):
        from echograph.ui.pattern_editor.editor import PatternEditor
        if self._param_path():
            try:
                path = pattern_path(self.node_item)
                expected = getattr(self.node_item.model, "_pattern_disk_hash", None)
                signature = save_document(path, self.history.document, expected_hash=expected)
                self.node_item.model._pattern_disk_hash = signature
                self.node_item.model._pattern_file_content_hash = self.history.document.content_hash()
                self.history.mark_saved()
                self._signature = signature
                stat = path.stat()
                self._file_stat = (str(path), stat.st_mtime_ns, stat.st_size)
                self._on_saved(str(path))
                return True
            except (OSError, PatternError) as exc:
                QtWidgets.QMessageBox.warning(self, "Could not save pattern", str(exc))
                return False
        reference = getattr(self.node_item.model, "_pattern_editor_ref", None)
        editor = reference() if reference else None
        if editor is None or not isValid(editor) or editor.history is not self.history:
            editor = PatternEditor(None, _window(self.node_item), history=self.history)
            editor.hide()
            self.node_item.model._pattern_editor_ref = weakref.ref(editor)
            editor.saved.connect(self._on_saved)
            self._editor = editor
        return editor.save()

    def _current_piece(self):
        return next((piece for piece in self.history.document.pieces if piece.id == self._selected_id), None)

    def _edit(self, label, operation):
        try:
            self.history.edit(label, operation)
        except (PatternError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self.node_item.model._pattern_history = self.history
        self.refresh()

    def _sew_requested(self, left_piece, left_edge, right_piece, right_edge):
        from echograph.patterns.sewing import add_sew_pair

        try:
            self.history.edit("Sew edges", lambda doc: add_sew_pair(doc, left_piece, left_edge, right_piece, right_edge))
        except PatternError as exc:
            self.canvas.sewStatusChanged.emit(str(exc))
            return
        self.node_item.model._pattern_history = self.history
        self.refresh()
        self.canvas.sewStatusChanged.emit("Sewn · Choose two more edges · Esc: exit")

    def _sew_status_changed(self, text):
        self._sew_status = text
        if hasattr(self, "status"):
            self._update_status_text()

    def _elastic_requested(self, piece_id, edge_id):
        from echograph.patterns.elastic import elastic_for_edge
        self._canvas_edge_selected(piece_id, edge_id)
        if elastic_for_edge(self.history.document, piece_id, edge_id) is None:
            self._elastic_edited(True, self.elastic_controls.strength.value(),
                                 self.elastic_controls.rest_length.value() / 100)
        self.canvas.sewStatusChanged.emit("Elastic edge · Adjust strength and rest length · Esc: exit")

    def _elastic_edited(self, enabled, strength, ratio):
        from echograph.patterns.elastic import set_elastic_edges
        selection = set(self._edge_selection)
        if selection:
            self._edit("Set elastic edges", lambda doc: set_elastic_edges(doc, selection, enabled, strength, ratio))

    def add_rectangle(self):
        width, height = self.width_edit.value(), self.height_edit.value()
        piece = rectangle(f"Panel {len(self.history.document.pieces) + 1}", width, height)
        if self.history.document.pieces:
            piece.layout[0] = max(p.layout[0] + bounds(p)[2] for p in self.history.document.pieces) + 40
        self._selected_id = piece.id
        self._edit("Add rectangle", lambda document: document.pieces.append(piece))
        self.canvas.fit_pieces()

    def add_trapezoid(self):
        width, height = self.width_edit.value(), self.height_edit.value()
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("New trapezoid")
        form = QtWidgets.QFormLayout(dialog)
        top = QtWidgets.QDoubleSpinBox()
        top.setRange(0.1, 100000)
        top.setValue(width * 0.7)
        form.addRow("Top width (mm)", top)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        piece = rectangle(f"Panel {len(self.history.document.pieces) + 1}", width, height, top_width=top.value())
        if self.history.document.pieces:
            piece.layout[0] = max(p.layout[0] + bounds(p)[2] for p in self.history.document.pieces) + 40
        self._selected_id = piece.id
        self._edit("Add trapezoid", lambda document: document.pieces.append(piece))

    def _outline_created(self, points, closed):
        if not points:
            return
        x0, y0 = min(p[0] for p in points), min(p[1] for p in points)
        piece = polygon(f"Panel {len(self.history.document.pieces) + 1}",
                        [(x-x0, y-y0) for x, y in points], closed=closed)
        piece.layout = [x0, y0, 0.0]
        self._selected_id = piece.id
        self._edit("Draw outline", lambda document: document.pieces.append(piece))

    def _finish_outline(self, close):
        if self.canvas.drawing:
            self.canvas.finish_outline(close)

    def _drawing_changed(self, drawing):
        if drawing:
            self._update_status_text()
        else:
            self.refresh()

    def _piece_selected(self, piece_id):
        self._selected_id = piece_id
        self._selected_panels = {piece_id}
        self._point_selection.clear()
        self._selected_points.clear()
        self._edge_selection.clear()
        self._selected_edge_id = ""
        self._render_key = None
        self.refresh()

    def _panels_selected(self, piece_id, additive, toggle=False):
        if not additive:
            return self._piece_selected(piece_id)
        if self._selected_id:
            self._selected_panels.add(self._selected_id)
        if toggle and piece_id in self._selected_panels and len(self._selected_panels) > 1:
            self._selected_panels.remove(piece_id)
            self._point_selection.pop(piece_id, None)
            self._selected_id = next(p.id for p in self.history.document.pieces if p.id in self._selected_panels)
        else:
            self._selected_panels.add(piece_id)
            self._selected_id = piece_id
        self._selected_points = set(self._point_selection.get(self._selected_id, ()))
        self.refresh()

    def _all_points_selected(self, selection):
        self._point_selection = {key: set(ids) for key, ids in selection.items() if ids}

    def _show_canvas(self):
        available = {piece.id for piece in self.history.document.pieces}
        self._edge_selection &= {(piece.id, edge.id) for piece in self.history.document.pieces for edge in piece.segments}
        self._selected_panels &= available
        if self._selected_id:
            self._selected_panels.add(self._selected_id)
        selection = {key: ids for key, ids in self._point_selection.items() if key in self._selected_panels}
        selection[self._selected_id] = self._selected_points
        self.canvas.show_document(self.history.document, self._selected_id, self._selected_points,
                                  (self._selected_id, self._selected_edge_id), self._selected_panels, selection, self._edge_selection)
        self.elastic_controls.show_selection(self.history.document, self._edge_selection)

    def _fuse_selected_points(self, selection):
        keys = {key for key, ids in selection.items() if ids} or self._selected_panels
        def operation(doc):
            result = combine_panels(doc, keys, fuse_selection=selection if any(selection.values()) else None,
                                    fuse_all=not any(selection.values()))
            self._selected_id = result.id
            self._selected_panels = {result.id}
            self._selected_points.clear()
            self._point_selection.clear()
        self._edit("Fuse Points", operation)

    def _layout_dragged(self, piece_id, x, y):
        self._selected_id = piece_id
        self._edit("Arrange panel", lambda doc: doc.piece(piece_id).layout.__setitem__(slice(0, 2), [x, y]))

    def _handle_dragged(self, piece_id, entity_id, index, x, y):
        self._selected_id = piece_id
        def change(doc):
            piece = doc.piece(piece_id)
            if index < 0:
                move_point(piece, entity_id, x, y)
            else:
                piece.segment(entity_id).controls[index] = [x, y]
        self._edit("Edit pattern geometry", change)

    def _edges_dragged(self, selection, dx, dy):
        from echograph.patterns.commands import translate_edges
        self._edit("Move edges", lambda doc: translate_edges(doc, selection, dx, dy))

    def _points_dragged(self, selection, dx, dy):
        from echograph.patterns.commands import translate_points
        self._edit("Move points", lambda doc: translate_points(doc, selection, dx, dy))

    def _edge_selected(self, _index=None):
        self._selected_edge_id = self.edge_combo.currentData() or ""
        if _index is not None:
            self._edge_selection = {(self._selected_id, self._selected_edge_id)} if self._selected_edge_id else set()
            if self._selected_edge_id:
                self._point_selection.clear()
                self._selected_points.clear()
        if self.edge_combo.currentData():
            self._edges_deselected = False
        self.curve_button.setEnabled(bool(self.edge_combo.currentData()))
        self.insert_point_button.setEnabled(bool(self.edge_combo.currentData()))
        if self._selected_id:
            self._show_canvas()

    def _make_cubic(self):
        if self._current_piece() and self.edge_combo.currentData():
            piece_id, edge_id = self._selected_id, self.edge_combo.currentData()
            self._selected_edge_id = edge_id
            self._edit("Make cubic edge", lambda doc: set_edge_kind(doc.piece(piece_id), edge_id, "cubic"))

    def _points_selected(self, piece_id, point_ids):
        self._edge_selection.clear()
        self._selected_edge_id = ""
        self._edges_deselected = True
        with QtCore.QSignalBlocker(self.edge_combo):
            self.edge_combo.setCurrentIndex(-1)
        self.curve_button.setEnabled(False)
        self.insert_point_button.setEnabled(False)
        self.elastic_controls.show_selection(self.history.document, ())
        self._selected_id = piece_id
        self._selected_points = set(point_ids)

    def _point_merge_requested(self, source_piece, source_point, target_piece, target_point):
        def operation(doc):
            merge_points(doc, source_piece, source_point, target_piece, target_point)
            result = combine_panels(doc, {source_piece, target_piece}, fuse_selection={
                source_piece: {source_point}, target_piece: {target_point}})
            self._selected_id = result.id
            self._selected_panels = {result.id}
            self._selected_points.clear()
            self._point_selection.clear()
        self._edit("Fuse Points", operation)

    def _clear_canvas_selection(self):
        self._edge_selection.clear()
        self._point_selection.clear()
        self._selected_points.clear()
        self._edges_deselected = True
        self.edge_combo.setCurrentIndex(-1)
        self._edge_selected()

    def _canvas_edge_selected(self, piece_id, edge_id):
        self._edge_selection = {(piece_id, edge_id)}
        self._point_selection.clear()
        self._selected_points.clear()
        self._edges_deselected = False
        if piece_id != self._selected_id:
            self._selected_points.clear()
        self._selected_id, self._selected_edge_id = piece_id, edge_id
        self.refresh()

    def _edges_selection_changed(self, piece_id, edge_id, selection):
        self._edge_selection = set(selection)
        self._selected_panels.update(key for key, _ in selection)
        self._selected_id, self._selected_edge_id = piece_id, edge_id
        self._selected_points = set(self._point_selection.get(piece_id, ()))
        self._edges_deselected = False
        self._render_key = None
        self.refresh()

    def _delete_points_requested(self, piece_id, point_ids):
        self._selected_id = piece_id
        self.delete_selected_points(point_ids)

    def delete_selected_points(self, point_ids=None):
        if isinstance(point_ids, bool):
            point_ids = None
        ids = list(point_ids if point_ids is not None else self._selected_points)
        if not ids or not self._current_piece():
            return
        self._selected_points.clear()
        self._edit("Delete point" if len(ids) == 1 else "Delete points",
                   lambda doc: delete_points(doc.piece(self._selected_id), ids))

    def _split_edge_requested(self, piece_id, edge_id):
        self._selected_id, self._selected_edge_id = piece_id, edge_id
        self.insert_point_on_edge()

    def delete_selection(self):
        from echograph.patterns.commands import delete_edges
        points = {key: set(ids) for key, ids in self._point_selection.items() if ids}
        if self._selected_points:
            points[self._selected_id] = set(self._selected_points)
        edges = set(self._edge_selection)
        panels = set(self._selected_panels)
        def operation(doc):
            if points:
                for key, ids in points.items():
                    delete_points(doc.piece(key), ids)
            elif edges:
                for key in {key for key, _ in edges}:
                    delete_edges(doc.piece(key), {edge for owner, edge in edges if owner == key})
            else:
                doc.pieces[:] = [piece for piece in doc.pieces if piece.id not in panels]
        self._point_selection.clear()
        self._selected_points.clear()
        self._edge_selection.clear()
        self._selected_edge_id = ""
        self._render_key = None
        self._edit("Delete points" if points else "Delete edges" if edges else "Delete panels", operation)

    def insert_point_on_edge(self):
        if not self._current_piece() or not self._selected_edge_id:
            return
        created = []
        def split(document):
            created.append(split_segment(document.piece(self._selected_id), self._selected_edge_id))
        self._edit("Insert point on edge", split)
        if created:
            self._selected_points = {created[0]}
            self.refresh()

    def undo(self):
        if self.history.undo():
            self.refresh()

    def redo(self):
        if self.history.redo():
            self.refresh()

    def _on_saved(self, path):
        if not isValid(self.node_item):
            return
        resolved = Path(path).resolve()
        # Notify once, after save bookkeeping is complete. Immediate notification
        # here rebuilt the connected Scene mesh inside the Save button handler,
        # then _schedule_param_emit below triggered the same refresh again.
        self.node_item._set_param_value("pattern_path", str(resolved), rebuild=False, notify_scene=False)
        self.node_item.model._pattern_history = self.history
        self.node_item.model._pattern_missing = False
        self.node_item.model._pattern_disk_hash = file_hash(resolved)
        self.node_item.model._pattern_file_content_hash = self.history.document.content_hash()
        stat = resolved.stat()
        self._file_stat = (str(resolved), stat.st_mtime_ns, stat.st_size)
        self.node_item.model._pattern_expected_file_stat = self._file_stat
        self._signature = None
        self._external_change = False
        self._render_key = None
        self.refresh()
        self.node_item._schedule_param_emit()

    @QtCore.Slot()
    def refresh(self):
        if not isValid(self.node_item) or self.graphicsProxyWidget() is None:
            self.timer.stop()
            return
        if (self.canvas._edge_drag is not None or self.canvas._point_drag is not None
                or self.canvas.scene().mouseGrabberItem() is not None):
            return
        value = self._param_path()
        try:
            if value:
                path = pattern_path(self.node_item)
                stat = path.stat()
                self._file_stat = (str(path), stat.st_mtime_ns, stat.st_size)
                self.filename.setText(path.name)
                self.filename.setToolTip(str(path))
                status = "File changed · Open / Locate to reload" if getattr(self, "_external_change", False) else "Saved pattern · Shared with full editor"
            else:
                self.filename.setText("Unsaved design; choose Save .pattern.json to create its file")
                self.filename.setToolTip("Draw now. Your separate pattern file is created when you choose Save.")
                problems = [p for piece in self.history.document.pieces for p in validate_piece(piece)]
                status = f"{len(self.history.document.pieces)} panels · {len(problems)} issues · Save to create a file"
            if self.history.dirty and value and not getattr(self, "_external_change", False):
                status = "Unsaved edits · Save to update Scene"
            scene_error = getattr(self.node_item.model, "_pattern_scene_error", "")
            if scene_error:
                status += "; Scene: " + scene_error
            self._base_status = status
            self._update_status_text()
            document = self.history.document
            render_key = (self.history.generation, self._selected_id, tuple(sorted(self._selected_panels)), self._selected_edge_id, self._file_stat,
                          bool(self.canvas.drawing), bool(getattr(self, "_external_change", False)))
            if render_key == self._render_key:
                return
            self._render_key = render_key
            self._show_canvas()
            if self._auto_fit_on_load and document.pieces:
                self._auto_fit_on_load = False
                QtCore.QTimer.singleShot(0, self.canvas, self.canvas.fit_pieces)
            self.edge_combo.blockSignals(True)
            self.edge_combo.clear()
            piece = self._current_piece()
            if piece:
                for i, edge in enumerate(piece.segments):
                    self.edge_combo.addItem(f"Edge {i + 1} ? {edge_length(piece, edge):.1f} mm ? {edge.kind}", edge.id)
                selected_index = self.edge_combo.findData(self._selected_edge_id)
                self.edge_combo.setCurrentIndex(selected_index)
            self.edge_combo.blockSignals(False)
            self._selected_edge_id = self.edge_combo.currentData() or ""
            self._edge_selected()
        except (OSError, PatternError) as exc:
            self.filename.setText("Pattern file missing or invalid; choose Open / Locate")
            self.filename.setToolTip(str(exc))
            self._base_status = "File missing · Open / Locate, or Save As"
            self._update_status_text()
            render_key = (self.history.generation, self._selected_id, None, False, True)
            if render_key != self._render_key:
                self._render_key = render_key
                self.canvas.show_document(self.history.document, self._selected_id)

    def _poll_file(self):
        if not isValid(self.node_item) or self.graphicsProxyWidget() is None:
            self.timer.stop()
            return
        if self.history.generation != getattr(self, "_rendered_generation", -1):
            self.refresh()
            self._rendered_generation = self.history.generation
        if not self._param_path():
            return
        try:
            path = pattern_path(self.node_item)
            stat = path.stat()
            observed = (str(path), stat.st_mtime_ns, stat.st_size)
            expected = getattr(self.node_item.model, "_pattern_expected_file_stat", None)
            if observed != expected:
                changed = file_hash(path) != getattr(self.node_item.model, "_pattern_disk_hash", None)
                if changed != getattr(self, "_external_change", False) or observed != self._file_stat:
                    self._external_change = changed
                    self._file_stat = observed
                    self._render_key = None
                    self.refresh()
        except OSError:
            if not getattr(self, "_external_change", False):
                self._external_change = True
                self._file_stat = None
                self._render_key = None
                self.refresh()


def render_node_body(node_item, y_cursor):
    body = PatternNodeWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setPos(0, y_cursor)
    proxy.setZValue(node_item.zValue() + 0.1)
    height = max(body.sizeHint().height(), body.minimumSizeHint().height())
    width = max(node_item.width, body.minimumSizeHint().width(), PATTERN_NODE_W)
    node_item._pattern_min_w = width
    node_item._pattern_min_h = height
    proxy.resize(width, height)
    node_item._plugin_proxies.append(proxy)
    required = y_cursor + height + node_item._PADDING
    if node_item.height < required or node_item.width < width:
        node_item.prepareGeometryChange()
        node_item.width = max(node_item.width, width)
        node_item.height = max(node_item.height, required)
    return y_cursor + height


PATTERN_DESIGN_SPEC = Spec(stripe_color="#32b9b0", build_ports=build_ports, render_node_body=render_node_body)
