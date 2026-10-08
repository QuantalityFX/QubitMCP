from __future__ import annotations

from copy import deepcopy
import math

from PySide6 import QtCore, QtGui, QtWidgets

from echograph.patterns.commands import move_point, translate_edges
from echograph.patterns.document import PatternError
from echograph.patterns.geometry import validate_piece


def painter_path(piece):
    path = QtGui.QPainterPath()
    if piece.contours:
        path.setFillRule(QtCore.Qt.FillRule.WindingFill)
        for part in piece.boundary_parts():
            path.addPath(painter_path(part))
        return path
    if not piece.segments:
        return path
    start = piece.point(piece.segments[0].start)
    path.moveTo(start.x, start.y)
    for edge in piece.segments:
        end = piece.point(edge.end)
        if edge.kind == "cubic":
            a, b = edge.controls
            path.cubicTo(a[0], a[1], b[0], b[1], end.x, end.y)
        else:
            path.lineTo(end.x, end.y)
    if piece.closed and piece.segments[-1].end == piece.segments[0].start:
        path.closeSubpath()
    return path


class PieceItem(QtWidgets.QGraphicsPathItem):
    def __init__(self, canvas, piece, selected):
        super().__init__(painter_path(piece))
        self.canvas, self.piece = canvas, deepcopy(piece)
        self.setPos(piece.layout[0], piece.layout[1])
        self.setRotation(piece.layout[2])
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable)
        self.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
        invalid = bool(validate_piece(piece))
        pen = QtGui.QPen(QtGui.QColor("#f08080" if invalid else "#64d7df" if selected else "#8396b0"), 2)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QtGui.QColor("#24424e" if selected else "#27313f"))
        self.setZValue(1 if selected else 0)
        label = QtWidgets.QGraphicsSimpleTextItem(piece.name, self)
        label.setBrush(QtGui.QColor("#f0f5fc"))
        label.setFlag(self.GraphicsItemFlag.ItemIgnoresTransformations)
        box = self.boundingRect()
        label.setPos(box.left() + 8, box.top() + 18)
        label.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self._start = self.pos()

    def mousePressEvent(self, event):
        self._start = self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        position = self.canvas.snap(self.pos()) if self.pos() != self._start else self.pos()
        self.setPos(position)
        if position != self._start:
            self.canvas.layoutEdited.emit(self.piece.id, position.x(), position.y())
        else:
            modifiers = event.modifiers()
            if modifiers & (QtCore.Qt.KeyboardModifier.ShiftModifier | QtCore.Qt.KeyboardModifier.ControlModifier):
                self.canvas.panelsSelected.emit(self.piece.id, True,
                                                bool(modifiers & QtCore.Qt.KeyboardModifier.ControlModifier))
            else:
                self.canvas.pieceSelected.emit(self.piece.id)


class HandleItem(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, owner, entity_id, control_index, position, selected=False):
        super().__init__(-5, -5, 10, 10, owner)
        self.owner, self.entity_id, self.control_index = owner, entity_id, control_index
        self.setFlag(self.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(self.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QtGui.QColor("#ffc773" if control_index >= 0 else "#ffd166" if selected else "#b7f2ef"))
        self.setPen(QtGui.QPen(QtGui.QColor("#17202d"), 1))
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setZValue(4)
        self._ready = False
        self.setPos(*position)
        self._ready = True

    def mousePressEvent(self, event):
        if self.control_index < 0:
            if (self.owner.canvas.selected_edges and event.modifiers() &
                    (QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.ShiftModifier)):
                event.accept()
                return
            self.owner.canvas.pointClicked.emit(self.owner.piece.id, self.entity_id,
                                                bool(event.modifiers() & (QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.ShiftModifier)))
        super().mousePressEvent(event)

    def itemChange(self, change, value):
        if change == self.GraphicsItemChange.ItemPositionChange and self._ready:
            return self.owner.canvas.snap(value)
        result = super().itemChange(change, value)
        if change == self.GraphicsItemChange.ItemPositionHasChanged and self._ready:
            piece = self.owner.piece
            if self.control_index < 0:
                move_point(piece, self.entity_id, value.x(), value.y())
            else:
                piece.segment(self.entity_id).controls[self.control_index] = [value.x(), value.y()]
            self.owner.setPath(painter_path(piece))
            self.owner.canvas.preview_symmetry(self.owner)
        return result

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.owner.canvas.handleEdited.emit(self.owner.piece.id, self.entity_id, self.control_index,
                                             self.pos().x(), self.pos().y())


class PatternCanvas(QtWidgets.QGraphicsView):
    pieceSelected = QtCore.Signal(str)
    layoutEdited = QtCore.Signal(str, float, float)
    handleEdited = QtCore.Signal(str, str, int, float, float)
    edgesMoved = QtCore.Signal(object, float, float)
    outlineCreated = QtCore.Signal(object, bool)
    drawingChanged = QtCore.Signal(bool)
    pointClicked = QtCore.Signal(str, str, bool)
    pointSelectionChanged = QtCore.Signal(str, object)
    selectionCleared = QtCore.Signal()
    edgeSelected = QtCore.Signal(str, str)
    edgesSelectionChanged = QtCore.Signal(str, str, object)
    deletePointsRequested = QtCore.Signal(str, object)
    deleteSelectionRequested = QtCore.Signal()
    splitEdgeRequested = QtCore.Signal(str, str)
    pointMergeRequested = QtCore.Signal(str, str, str, str)
    mergeModeChanged = QtCore.Signal(bool)
    panelsSelected = QtCore.Signal(str, bool, bool)
    pointsSelectionChanged = QtCore.Signal(object)
    fusePointsRequested = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QtGui.QColor("#151c27"))
        self.setTransformationAnchor(self.ViewportAnchor.AnchorUnderMouse)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setSceneRect(-3000, -3000, 6000, 6000)
        self.scale(1.0, -1.0)
        self.grid_mm = 10.0
        self.snap_enabled = True
        self.drawing = False
        self.draft_points = []
        self._draft_item = None
        self._pan_origin = None
        self._zoom_origin = None
        self._zoom_pivot_scene = None
        self._zoom_start_scale = None
        self._piece_items = []
        self._document = None
        self._merge_source = None
        self._active_piece_id = ""
        self._selected_points = {}
        self.selected_edge = ("", "")
        self.selected_edges = set()
        self._selection_additive = False
        self._selection_origin = None
        self._selection_item = None
        self._edge_drag = None
        self.pointClicked.connect(self._on_point_clicked)
        self.setMinimumSize(400, 350)

    def snap(self, point):
        if not self.snap_enabled:
            return point
        return QtCore.QPointF(round(point.x() / self.grid_mm) * self.grid_mm,
                             round(point.y() / self.grid_mm) * self.grid_mm)

    def show_document(self, document, selected_id="", selected_points=None, selected_edge=None,
                      selected_panels=None, point_selection=None, edge_selection=None):
        self._document = document
        self.scene().clear()
        self._active_piece_id = selected_id
        self._draft_item = None
        self._piece_items = []
        selected_points = set(selected_points or ())
        self._selected_panels = set(selected_panels if selected_panels is not None else [selected_id])
        selection = ({key: set(value) for key, value in point_selection.items()}
                     if point_selection is not None else {selected_id: selected_points})
        self.selected_edge = selected_edge or ("", "")
        self.selected_edges = (set(edge_selection) if edge_selection is not None else
                               {self.selected_edge} if self.selected_edge[1] else set())
        for piece in document.pieces:
            item = PieceItem(self, piece, piece.id in self._selected_panels)
            self.scene().addItem(item)
            self._piece_items.append(item)
            if piece.id in self._selected_panels:
                for edge in piece.segments:
                    if edge.kind == "cubic" and (piece.id, edge.id) in self.selected_edges:
                        for index, control in enumerate(edge.controls):
                            endpoint = piece.point(edge.start if index == 0 else edge.end)
                            line = QtWidgets.QGraphicsLineItem(endpoint.x, endpoint.y, *control, item)
                            pen = QtGui.QPen(QtGui.QColor("#826c4f"), 1, QtCore.Qt.PenStyle.DashLine)
                            pen.setCosmetic(True)
                            line.setPen(pen)
                            line.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                            HandleItem(item, edge.id, index, control)
                for edge in piece.segments:
                    if (piece.id, edge.id) in self.selected_edges:
                        path = QtGui.QPainterPath()
                        a = piece.point(edge.start)
                        path.moveTo(a.x, a.y)
                        b = piece.point(edge.end)
                        if edge.kind == "cubic":
                            path.cubicTo(*edge.controls[0], *edge.controls[1], b.x, b.y)
                        else:
                            path.lineTo(b.x, b.y)
                        overlay = QtWidgets.QGraphicsPathItem(path, item)
                        pen = QtGui.QPen(QtGui.QColor("#ffc773"), 4)
                        pen.setCosmetic(True)
                        overlay.setPen(pen)
                        overlay.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                        overlay.setZValue(3)
            if piece.id in self._selected_panels or self._merge_source:
                for point in piece.points:
                    HandleItem(item, point.id, -1, (point.x, point.y), point.id in selection.get(piece.id, set()))
        box = self.scene().itemsBoundingRect()
        self.setSceneRect(box.united(QtCore.QRectF(-1000, -1000, 2000, 2000)).adjusted(-1000, -1000, 1000, 1000))
        self._update_draft()

        self.set_points_selection(selection)

    def preview_symmetry(self, source_item):
        from echograph.patterns.relationships import symmetry_for_piece, reflect_geometry
        pair = symmetry_for_piece(self._document, source_item.piece.id) if self._document else None
        if not pair or not pair["enabled"]:
            return
        partner_id = pair["right"] if source_item.piece.id == pair["left"] else pair["left"]
        partner = next((item for item in self._piece_items if item.piece.id == partner_id), None)
        if partner:
            reflect_geometry(source_item.piece, partner.piece, deepcopy(pair))
            partner.setPath(painter_path(partner.piece))

    def start_point_merge(self, piece_id, point_id):
        if self._document is None:
            return
        self.cancel_outline()
        self._merge_source = (piece_id, point_id)
        self.show_document(self._document, self._active_piece_id,
                           self._selected_points.get(self._active_piece_id, set()), self.selected_edge,
                           self._selected_panels, self._selected_points, self.selected_edges)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.mergeModeChanged.emit(True)

    def cancel_point_merge(self):
        self._merge_source = None
        self.unsetCursor()
        self.show_document(self._document, self._active_piece_id,
                           self._selected_points.get(self._active_piece_id, set()), self.selected_edge,
                           self._selected_panels, self._selected_points, self.selected_edges)
        self.mergeModeChanged.emit(False)

    def fit_pieces(self):
        if self._piece_items:
            box = self._piece_items[0].sceneBoundingRect()
            for item in self._piece_items[1:]:
                box = box.united(item.sceneBoundingRect())
        else:
            box = QtCore.QRectF(-100, -100, 700, 600)
        self.fitInView(box.adjusted(-45, -45, 45, 45), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def start_outline(self):
        self.drawing = True
        self.draft_points = []
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setFocus()
        self.drawingChanged.emit(True)

    def finish_outline(self, closed=True):
        points = list(self.draft_points)
        minimum = 3 if closed else 1
        if len(points) < minimum:
            return
        self.cancel_outline()
        self.outlineCreated.emit(points, closed)

    def cancel_outline(self):
        self.drawing = False
        self.draft_points = []
        self.unsetCursor()
        self._update_draft()
        self.drawingChanged.emit(False)

    def _update_draft(self):
        if self._draft_item is not None:
            self.scene().removeItem(self._draft_item)
            self._draft_item = None
        if not self.draft_points:
            return
        path = QtGui.QPainterPath(QtCore.QPointF(*self.draft_points[0]))
        for point in self.draft_points[1:]:
            path.lineTo(*point)
        pen = QtGui.QPen(QtGui.QColor("#ffc773"), 2, QtCore.Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._draft_item = self.scene().addPath(path, pen)
        self._draft_item.setZValue(10)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            self._pan_origin = event.position()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self._zoom_origin = event.position()
            self._zoom_pivot_scene = self.mapToScene(event.position().toPoint())
            self._zoom_start_scale = abs(self.transform().m11())
            self.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
            event.accept()
            return
        if self._merge_source and event.button() == QtCore.Qt.MouseButton.LeftButton:
            source_piece, source_point = self._merge_source
            pixel = event.position().toPoint()
            nearest = None
            distance = 12.0
            for item in self._piece_items:
                if item.piece.id == source_piece:
                    continue
                for point in item.piece.points:
                    screen = self.mapFromScene(item.mapToScene(QtCore.QPointF(point.x, point.y)))
                    current = math.hypot(screen.x() - pixel.x(), screen.y() - pixel.y())
                    if current < distance:
                        distance, nearest = current, (item.piece.id, point.id)
            if nearest:
                self.cancel_point_merge()
                self.pointMergeRequested.emit(source_piece, source_point, *nearest)
            event.accept()
            return
        if self.drawing and event.button() == QtCore.Qt.MouseButton.LeftButton:
            point = self.snap(self.mapToScene(event.position().toPoint()))
            if len(self.draft_points) >= 3:
                origin = self.mapFromScene(QtCore.QPointF(*self.draft_points[0]))
                if (origin - event.position().toPoint()).manhattanLength() < 12:
                    self.finish_outline()
                    return
            coordinates = (point.x(), point.y())
            if not self.draft_points or coordinates != self.draft_points[-1]:
                self.draft_points.append(coordinates)
            self._update_draft()
            event.accept()
            return
        pressed_item = self.itemAt(event.position().toPoint())
        if (event.button() == QtCore.Qt.MouseButton.LeftButton and
                (event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier or pressed_item is None)):
            self._selection_origin = self.mapToScene(event.position().toPoint())
            self._selection_additive = bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)
            self._selection_item = self.scene().addRect(QtCore.QRectF(self._selection_origin, self._selection_origin),
                QtGui.QPen(QtGui.QColor("#ffc773"), 0), QtGui.QBrush(QtGui.QColor(255, 199, 115, 35)))
            self._selection_item.setZValue(20)
            event.accept()
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            is_handle = isinstance(pressed_item, HandleItem)
            edge = None if is_handle else self._edge_at(event.position().toPoint())
            if edge:
                if event.modifiers() & (QtCore.Qt.KeyboardModifier.ShiftModifier | QtCore.Qt.KeyboardModifier.ControlModifier):
                    self._add_edge_selection(edge)
                    event.accept()
                    return
                if edge in self.selected_edges:
                    self._add_edge_selection(edge)
                else:
                    self.set_points_selection({})
                    self.edgeSelected.emit(*edge)
                self._edge_drag = {
                    "document": deepcopy(self._document), "selection": set(self.selected_edges),
                    "pixel": event.position(), "origin": self.mapToScene(event.position().toPoint()),
                    "delta": QtCore.QPointF(), "moved": False,
                }
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._edge_drag is not None:
            self._preview_edge_drag(event.position())
            event.accept()
            return
        if self._zoom_origin is not None:
            delta = event.position() - self._zoom_origin
            zoom_distance = delta.x() - delta.y()
            scale = self._zoom_start_scale * math.exp(max(-20, min(20, zoom_distance * 0.01)))
            target_scale = max(0.02, min(30, scale))
            factor = target_scale / abs(self.transform().m11())
            # Hold the press position fixed instead of following the moving
            # mouse anchor used by wheel zoom.
            anchor = self.transformationAnchor()
            self.setTransformationAnchor(self.ViewportAnchor.NoAnchor)
            self.scale(factor, factor)
            center = self.mapToScene(self.viewport().rect().center())
            current_pivot = self.mapToScene(self._zoom_origin.toPoint())
            self.centerOn(center + self._zoom_pivot_scene - current_pivot)
            self.setTransformationAnchor(anchor)
            event.accept()
            return
        if self._selection_item is not None:
            current = self.mapToScene(event.position().toPoint())
            self._selection_item.setRect(QtCore.QRectF(self._selection_origin, current).normalized())
            event.accept()
            return
        if self._pan_origin is not None:
            delta = event.position() - self._pan_origin
            self._pan_origin = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._edge_drag is not None and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._preview_edge_drag(event.position())
            drag, self._edge_drag = self._edge_drag, None
            self.unsetCursor()
            if drag["moved"] and not drag["delta"].isNull():
                self.edgesMoved.emit(drag["selection"], drag["delta"].x(), drag["delta"].y())
            event.accept()
            return
        if event.button() == QtCore.Qt.MouseButton.RightButton and self._zoom_origin is not None:
            self._zoom_origin = None
            self._zoom_pivot_scene = None
            self._zoom_start_scale = None
            self.unsetCursor()
            event.accept()
            return
        if self._selection_item is not None and event.button() == QtCore.Qt.MouseButton.LeftButton:
            rect = self._selection_item.rect().normalized()
            self.scene().removeItem(self._selection_item)
            self._selection_item = None
            self._selection_origin = None
            additive = self._selection_additive
            is_click = rect.width() < 4 / abs(self.transform().m11()) and rect.height() < 4 / abs(self.transform().m11())
            if additive and is_click:
                item = self.itemAt(event.position().toPoint())
                if isinstance(item, HandleItem) and item.control_index < 0:
                    self._on_point_clicked(item.owner.piece.id, item.entity_id, True, toggle=False)
                    event.accept()
                    return
                edge = self._edge_at(event.position().toPoint())
                if edge:
                    self._add_edge_selection(edge)
                    event.accept()
                    return
            candidates = {}
            for item in self._piece_items:
                for handle in item.childItems():
                    if isinstance(handle, HandleItem) and handle.control_index < 0:
                        scene_pos = handle.mapToScene(QtCore.QPointF(0, 0))
                        if rect.contains(scene_pos):
                            candidates.setdefault(item.piece.id, set()).add(handle.entity_id)
            if candidates:
                if additive and self.selected_edges:
                    event.accept()
                    return
                if not additive:
                    self._clear_edge_selection()
                selection = deepcopy(self._selected_points) if additive else {}
                for key, ids in candidates.items():
                    selection.setdefault(key, set()).update(ids)
                piece_id = self._active_piece_id if self._active_piece_id in candidates else next(iter(candidates))
                self.set_points_selection(selection)
                self.pointSelectionChanged.emit(piece_id, sorted(selection[piece_id]))
                self.pointsSelectionChanged.emit(selection)
            else:
                if rect.width() < 4 / abs(self.transform().m11()) and rect.height() < 4 / abs(self.transform().m11()):
                    item = self.itemAt(event.position().toPoint())
                    while item is not None and not isinstance(item, PieceItem):
                        item = item.parentItem()
                    if item is not None and event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
                        self.panelsSelected.emit(item.piece.id, True, False)
                        event.accept()
                        return
                if not additive:
                    self.set_point_selection(self._active_piece_id, [])
                    self.selectionCleared.emit()
            event.accept()
            return
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            self._pan_origin = None
            self.setCursor(QtCore.Qt.CursorShape.CrossCursor if self.drawing else QtCore.Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self._edge_drag is not None and event.key() == QtCore.Qt.Key.Key_Escape:
            drag, self._edge_drag = self._edge_drag, None
            self.unsetCursor()
            self.show_document(drag["document"], self._active_piece_id,
                               self._selected_points.get(self._active_piece_id, ()), self.selected_edge,
                               self._selected_panels, self._selected_points, self.selected_edges)
            event.accept()
            return
        if self._merge_source and event.key() == QtCore.Qt.Key.Key_Escape:
            self.cancel_point_merge()
            event.accept()
            return
        if event.key() == QtCore.Qt.Key.Key_M and not self.drawing:
            if not any(self._selected_points.values()) and any(self._selected_panels):
                self.fusePointsRequested.emit({key: set() for key in self._selected_panels})
                event.accept()
                return
            if sum(len(ids) for ids in self._selected_points.values()) > 1:
                self.fusePointsRequested.emit(deepcopy(self._selected_points))
                event.accept()
                return
            ids = self._selected_points.get(self._active_piece_id, set())
            if len(ids) == 1:
                self.start_point_merge(self._active_piece_id, next(iter(ids)))
                event.accept()
                return
        if self.drawing:
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                self.finish_outline(not bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier))
                return
            if event.key() == QtCore.Qt.Key.Key_Escape:
                self.cancel_outline()
                return
            if event.key() == QtCore.Qt.Key.Key_Backspace:
                if self.draft_points:
                    self.draft_points.pop()
                self._update_draft()
                return
        if event.key() in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            self.deleteSelectionRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def set_point_selection(self, piece_id, point_ids):
        self.set_points_selection({piece_id: set(point_ids)} if point_ids else {})

    def set_points_selection(self, selection):
        self._selected_points = {key: set(ids) for key, ids in selection.items() if ids}
        for item in self._piece_items:
            selected = self._selected_points.get(item.piece.id, set())
            for handle in item.childItems():
                if isinstance(handle, HandleItem) and handle.control_index < 0:
                    handle.setBrush(QtGui.QColor("#ffd166" if handle.entity_id in selected else "#b7f2ef"))

    def _on_point_clicked(self, piece_id, point_id, additive, toggle=True):
        if additive and self.selected_edges:
            return
        if not additive:
            self._clear_edge_selection()
        selection = deepcopy(self._selected_points) if additive else {}
        selected = selection.setdefault(piece_id, set())
        if additive and toggle and point_id in selected:
            selected.remove(point_id)
        else:
            selected.add(point_id)
        self.set_points_selection(selection)
        self.pointSelectionChanged.emit(piece_id, sorted(selected))
        self.pointsSelectionChanged.emit(deepcopy(self._selected_points))

    def _add_edge_selection(self, edge):
        if any(self._selected_points.values()):
            return
        self.selected_edges.add(edge)
        self.selected_edge = edge
        self.edgesSelectionChanged.emit(*edge, set(self.selected_edges))

    def _clear_edge_selection(self):
        self.selected_edges.clear()
        self.selected_edge = (self._active_piece_id, "")
        # Remove edge decorations without rebuilding/deleting the point item
        # that is currently receiving its mouse press and may start a drag.
        for owner in self._piece_items:
            for child in owner.childItems():
                if ((isinstance(child, HandleItem) and child.control_index >= 0)
                        or isinstance(child, QtWidgets.QGraphicsLineItem)
                        or (isinstance(child, QtWidgets.QGraphicsPathItem)
                            and child.pen().color().name() == "#ffc773")):
                    self.scene().removeItem(child)

    def _preview_edge_drag(self, position):
        from echograph.patterns.relationships import reconcile_relationships
        drag = self._edge_drag
        if not drag["moved"] and (position - drag["pixel"]).manhattanLength() < QtWidgets.QApplication.startDragDistance():
            return
        drag["moved"] = True
        drag["delta"] = self.snap(self.mapToScene(position.toPoint()) - drag["origin"])
        preview = deepcopy(drag["document"])
        translate_edges(preview, drag["selection"], drag["delta"].x(), drag["delta"].y())
        try:
            reconcile_relationships(drag["document"], preview)
        except PatternError:
            # The transaction reports incompatible linked edits on release.
            return
        self.show_document(preview, self._active_piece_id,
                           self._selected_points.get(self._active_piece_id, ()), self.selected_edge,
                           self._selected_panels, self._selected_points, self.selected_edges)
        self.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)

    def _edge_at(self, pixel):
        scene_pos = self.mapToScene(pixel)
        tolerance = 8 / max(abs(self.transform().m11()), 1e-6)
        for item in reversed(self._piece_items):
            local = item.mapFromScene(scene_pos)
            for edge in item.piece.segments:
                a, b = item.piece.point(edge.start), item.piece.point(edge.end)
                path = QtGui.QPainterPath(QtCore.QPointF(a.x, a.y))
                if edge.kind == "cubic":
                    path.cubicTo(*edge.controls[0], *edge.controls[1], b.x, b.y)
                else:
                    path.lineTo(b.x, b.y)
                stroker = QtGui.QPainterPathStroker()
                stroker.setWidth(tolerance * 2)
                if stroker.createStroke(path).contains(local):
                    return item.piece.id, edge.id
        return None

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge:
                self.edgeSelected.emit(*edge)
                self.splitEdgeRequested.emit(*edge)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        # Right drag is the canvas zoom gesture; there is no canvas context menu.
        event.accept()

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        scale = abs(self.transform().m11()) * factor
        if 0.02 <= scale <= 30:
            self.scale(factor, factor)
        event.accept()

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        step = self.grid_mm
        while step * abs(self.transform().m11()) < 14:
            step *= 5
        pen = QtGui.QPen(QtGui.QColor("#263143"), 0)
        painter.setPen(pen)
        start_x, start_y = math.floor(rect.left() / step) * step, math.floor(rect.top() / step) * step
        lines = []
        for i in range(min(500, int(rect.width() / step) + 2)):
            x = start_x + step * i
            lines.append(QtCore.QLineF(x, rect.top(), x, rect.bottom()))
        for i in range(min(500, int(rect.height() / step) + 2)):
            y = start_y + step * i
            lines.append(QtCore.QLineF(rect.left(), y, rect.right(), y))
        painter.drawLines(lines)
        painter.setPen(QtGui.QPen(QtGui.QColor("#496078"), 0))
        painter.drawLine(QtCore.QLineF(0, rect.top(), 0, rect.bottom()))
        painter.drawLine(QtCore.QLineF(rect.left(), 0, rect.right(), 0))
