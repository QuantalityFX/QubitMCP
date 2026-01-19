# echograph/ui/graph_items.py
import math
from echograph.qt_compat import QtCore, QtGui, QtWidgets


def _rounded_polyline_path(points, radius: float) -> QtGui.QPainterPath:
    if not points:
        return QtGui.QPainterPath()
    if len(points) == 1:
        return QtGui.QPainterPath(points[0])
    r_base = max(0.0, float(radius))
    path = QtGui.QPainterPath(points[0])
    last = points[0]
    for i in range(1, len(points) - 1):
        corner = points[i]
        next_pt = points[i + 1]
        dx1 = corner.x() - last.x()
        dy1 = corner.y() - last.y()
        dx2 = next_pt.x() - corner.x()
        dy2 = next_pt.y() - corner.y()
        len1 = math.hypot(dx1, dy1)
        len2 = math.hypot(dx2, dy2)
        if len1 <= 1e-6 or len2 <= 1e-6:
            path.lineTo(corner)
            last = corner
            continue
        r = min(r_base, len1 * 0.5, len2 * 0.5)
        if r <= 1e-6:
            path.lineTo(corner)
            last = corner
            continue
        p1 = QtCore.QPointF(
            corner.x() - (dx1 / len1) * r,
            corner.y() - (dy1 / len1) * r,
        )
        p2 = QtCore.QPointF(
            corner.x() + (dx2 / len2) * r,
            corner.y() + (dy2 / len2) * r,
        )
        path.lineTo(p1)
        path.quadTo(corner, p2)
        last = p2
    path.lineTo(points[-1])
    return path


def _lead_for_dx(total_dx: float) -> float:
    abs_dx = abs(total_dx)
    lead_base = 24.0
    min_lead = 8.0
    lead = lead_base
    if abs_dx < lead_base * 2.0:
        lead = max(min_lead, abs_dx * 0.35)
    if total_dx > 0.0:
        min_mid = max(10.0, lead_base * 0.5)
        max_lead = (total_dx - min_mid) * 0.5
        if max_lead < lead:
            lead = max(min_lead, max_lead)
    return max(min_lead, lead)


def _elbow_path(
    start: QtCore.QPointF,
    end: QtCore.QPointF,
    lead_out: float | None = None,
    lead_in: float | None = None,
) -> QtGui.QPainterPath:
    sx = float(start.x())
    sy = float(start.y())
    dx = float(end.x())
    dy = float(end.y())
    total_dx = dx - sx
    total_dy = dy - sy

    abs_dx = abs(total_dx)
    abs_dy = abs(total_dy)
    if lead_out is None:
        lead_out = _lead_for_dx(total_dx)
    if lead_in is None:
        lead_in = _lead_for_dx(total_dx)
    lead_base = max(12.0, float(lead_out), float(lead_in))

    # Always step out to the right from the source pin, and approach the
    # destination pin from the left so the wire never bends back into a node.
    out_x = sx + lead_out
    in_x = dx - lead_in
    mid_y = (sy + dy) * 0.5
    mid_run = abs(in_x - out_x)
    tight_thresh = max(10.0, lead_base * 0.6)

    if mid_run < tight_thresh or abs_dy < tight_thresh:
        points = [
            QtCore.QPointF(sx, sy),
            QtCore.QPointF(out_x, sy),
            QtCore.QPointF(out_x, dy),
            QtCore.QPointF(dx, dy),
        ]
    else:
        points = [
            QtCore.QPointF(sx, sy),
            QtCore.QPointF(out_x, sy),
            QtCore.QPointF(out_x, mid_y),
            QtCore.QPointF(in_x, mid_y),
            QtCore.QPointF(in_x, dy),
            QtCore.QPointF(dx, dy),
        ]
    filtered = []
    for p in points:
        if filtered and abs(filtered[-1].x() - p.x()) <= 1e-6 and abs(filtered[-1].y() - p.y()) <= 1e-6:
            continue
        filtered.append(p)

    span = min(abs_dx, abs_dy, lead_out, lead_in)
    corner = max(6.0, min(24.0, span * 0.4))
    return _rounded_polyline_path(filtered, corner)


class EdgePin(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, edge, scene_pos: QtCore.QPointF, radius: float = 5.0):
        super().__init__(-radius, -radius, radius * 2.0, radius * 2.0)
        self._edge = edge
        self.setBrush(QtGui.QColor("#60a5fa"))
        pen = QtGui.QPen(QtGui.QColor("#1f2937"), 1.0)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(0.6)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        try:
            self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)
        except Exception:
            pass
        self.setPos(scene_pos)

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            edge = getattr(self, "_edge", None)
            if edge is not None:
                try:
                    edge.updatePath()
                except Exception:
                    pass
        return super().itemChange(change, value)


def _gi_flag(enum_name, fallback_enum):
    if hasattr(QtWidgets.QGraphicsItem, enum_name):
        return getattr(QtWidgets.QGraphicsItem, enum_name)
    if hasattr(QtWidgets.QGraphicsItem, "GraphicsItemFlag"):
        return getattr(QtWidgets.QGraphicsItem.GraphicsItemFlag, enum_name, fallback_enum)
    return fallback_enum


class EdgeItem(QtWidgets.QGraphicsPathItem):
    """
    Bezier edge between two NodeItem endpoints.
    - Keeps a normal vs highlighted pen.
    - Optionally stores logical port names:
        .src_port_name, .dst_port_name
      If the NodeItem implements port anchors (port_anchor(name, side)),
      we attach to those; otherwise we fall back to midpoints.
    """
    def __init__(self, src, dst, src_port_name: str | None = None, dst_port_name: str | None = None):
        super().__init__()
        self.setZValue(0)
        self.src = src
        self.dst = dst

        # Optional metadata for named inputs/outputs
        self.src_port_name = src_port_name
        self.dst_port_name = dst_port_name

        self.pen_normal = QtGui.QPen(QtGui.QColor("#586473"), 2)
        self.pen_path   = QtGui.QPen(QtGui.QColor("#22c55e"), 3)
        self.pen_click  = QtGui.QPen(QtGui.QColor("#60a5fa"), 3.5)
        self._highlight = False
        self._click_highlight = False
        self._pins = []
        self._apply_pen_state()
        self.setBrush(QtCore.Qt.NoBrush)
        self.updatePath()
        self.setAcceptHoverEvents(True)
        try:
            self.setFlag(_gi_flag("ItemIsSelectable", QtWidgets.QGraphicsItem.ItemIsSelectable), False)
        except Exception:
            pass

    # Small helpers so Scene can tag ports after creation
    def setPortNames(self, src_name: str | None, dst_name: str | None):
        self.src_port_name = src_name
        self.dst_port_name = dst_name
        self.updatePath()
        self.update()

    def setHighlighted(self, h: bool):
        self._highlight = h
        self._apply_pen_state()
        self.update()

    def setClickHighlighted(self, h: bool):
        self._click_highlight = bool(h)
        self._apply_pen_state()
        self.update()

    def _apply_pen_state(self):
        if self._click_highlight:
            self.setPen(self.pen_click)
        else:
            self.setPen(self.pen_path if self._highlight else self.pen_normal)

    def _pin_sort_key(self, s: QtCore.QPointF, d: QtCore.QPointF, p: QtCore.QPointF) -> float:
        vx = float(d.x() - s.x())
        vy = float(d.y() - s.y())
        denom = vx * vx + vy * vy
        if denom <= 1e-6:
            return float(p.y())
        return ((p.x() - s.x()) * vx + (p.y() - s.y()) * vy) / denom

    def _sorted_pin_points(self, s: QtCore.QPointF, d: QtCore.QPointF) -> list[QtCore.QPointF]:
        pins = [p for p in self._pins if p is not None]
        if not pins:
            return []
        points = []
        for pin in pins:
            try:
                points.append(pin.scenePos())
            except Exception:
                pass
        if not points:
            return []
        return sorted(points, key=lambda pt: self._pin_sort_key(s, d, pt))

    def add_pin(self, scene_pos: QtCore.QPointF) -> None:
        sc = self.scene()
        if sc is None:
            return
        pin = EdgePin(self, scene_pos)
        sc.addItem(pin)
        self._pins.append(pin)
        self.updatePath()

    def pin_positions(self) -> list[list[float]]:
        points = []
        for pin in list(self._pins):
            if pin is None:
                continue
            try:
                pos = pin.scenePos()
            except Exception:
                continue
            points.append([float(pos.x()), float(pos.y())])
        return points

    def add_pins_from_positions(self, positions) -> None:
        sc = self.scene()
        if sc is None:
            return
        for pos in positions or []:
            try:
                x = float(pos[0])
                y = float(pos[1])
            except Exception:
                continue
            self.add_pin(QtCore.QPointF(x, y))

    def clear_pins(self) -> None:
        sc = self.scene()
        for pin in list(self._pins):
            if pin is None:
                continue
            try:
                if sc is not None:
                    sc.removeItem(pin)
            except Exception:
                pass
        self._pins = []

    def _attach_points(self):
        """
        Compute start/end points for the cubic.
        If nodes expose port anchors, use them; else fall back to midpoints.
        """
        # --- Source anchor (right side) ---
        s_name = getattr(self, "src_port_name", None)
        if s_name and hasattr(self.src, "port_anchor"):
            try:
                s = self.src.port_anchor(s_name, side="out")
            except Exception:
                try:
                    s = self.src.mapToScene(QtCore.QPointF(getattr(self.src, "width", 0), getattr(self.src, "_BASE_H", 0) / 2.0))
                except Exception:
                    s = self.src.scenePos() + QtCore.QPointF(getattr(self.src, "width", 0), getattr(self.src, "_BASE_H", 0) / 2.0)
        else:
            try:
                s = self.src.mapToScene(QtCore.QPointF(self.src.width, self.src._BASE_H / 2.0))
            except Exception:
                s = QtCore.QPointF(0, 0)

        # --- Dest anchor (left side) ---
        d_name = getattr(self, "dst_port_name", None)
        if d_name and hasattr(self.dst, "port_anchor"):
            try:
                d = self.dst.port_anchor(d_name, side="in")
            except Exception:
                try:
                    d = self.dst.mapToScene(QtCore.QPointF(0, getattr(self.dst, "_BASE_H", 0) / 2.0))
                except Exception:
                    d = self.dst.scenePos() + QtCore.QPointF(0, getattr(self.dst, "_BASE_H", 0) / 2.0)
        else:
            try:
                d = self.dst.mapToScene(QtCore.QPointF(0, self.dst._BASE_H / 2.0))
            except Exception:
                d = QtCore.QPointF(0, 0)
        return s, d

    def updatePath(self):
        s, d = self._attach_points()
        pins = self._sorted_pin_points(s, d)
        if not pins:
            self.setPath(_elbow_path(s, d))
            return

        def _append_path(base: QtGui.QPainterPath, extra: QtGui.QPainterPath) -> QtGui.QPainterPath:
            if hasattr(base, "connectPath"):
                base.connectPath(extra)
            else:
                base.addPath(extra)
            return base

        first = pins[0]
        path = _elbow_path(s, first, lead_out=_lead_for_dx(first.x() - s.x()), lead_in=0.0)
        for idx in range(len(pins) - 1):
            seg = _elbow_path(pins[idx], pins[idx + 1], lead_out=0.0, lead_in=0.0)
            path = _append_path(path, seg)
        last = pins[-1]
        tail = _elbow_path(last, d, lead_out=0.0, lead_in=_lead_for_dx(d.x() - last.x()))
        path = _append_path(path, tail)
        self.setPath(path)

    def hoverEnterEvent(self, e):
        # Thicken a bit + show tooltip with port names if available
        pen = QtGui.QPen(self.pen().color(), self.pen().widthF() + 0.5)
        self.setPen(pen)

        src_label = f"{getattr(self.src, 'model', None).name if getattr(self.src, 'model', None) else 'src'}"
        dst_label = f"{getattr(self.dst, 'model', None).name if getattr(self.dst, 'model', None) else 'dst'}"

        sp = f"{self.src_port_name}" if self.src_port_name else ""
        dp = f"{self.dst_port_name}" if self.dst_port_name else ""
        port_bits = []
        if sp: port_bits.append(f"out:{sp}")
        if dp: port_bits.append(f"in:{dp}")
        hint = f"{src_label} → {dst_label}"
        if port_bits:
            hint += "  (" + ", ".join(port_bits) + ")"

        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), hint)
        super().hoverEnterEvent(e)

    def hoverLeaveEvent(self, e):
        self._apply_pen_state()
        super().hoverLeaveEvent(e)

    def mousePressEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        # Alt+LMB deletes the edge (with bookkeeping in Scene)
        if e.button() == QtCore.Qt.LeftButton and (e.modifiers() & QtCore.Qt.AltModifier):
            sc = self.scene()
            if sc and hasattr(sc, "_edges"):
                try:
                    self.clear_pins()
                except Exception:
                    pass
                try:
                    sc.removeItem(self)
                except Exception:
                    pass
                try:
                    sc._edges.remove(self)
                except Exception:
                    pass
                try:
                    if hasattr(sc, "_on_edge_removed"):
                        sc._on_edge_removed(self)
                except Exception:
                    pass
                e.accept()
                return
        if e.button() == QtCore.Qt.LeftButton and (e.modifiers() & QtCore.Qt.ControlModifier):
            try:
                self.add_pin(QtCore.QPointF(e.scenePos()))
            except Exception:
                self.add_pin(e.scenePos())
            self.setClickHighlighted(True)
            e.accept()
            return
        if e.button() == QtCore.Qt.LeftButton:
            sc = self.scene()
            if sc and hasattr(sc, "_edges"):
                for edge in list(getattr(sc, "_edges", [])):
                    if edge is self:
                        continue
                    try:
                        edge.setClickHighlighted(False)
                    except Exception:
                        pass
            self.setClickHighlighted(True)
            e.accept()
            return
        super().mousePressEvent(e)

    def itemChange(self, change, value):
        # Keep the curve tidy if either end moves
        if change == QtWidgets.QGraphicsItem.ItemScenePositionHasChanged:
            try:
                self.updatePath()
            except Exception:
                pass
        return super().itemChange(change, value)


class TempWire(QtWidgets.QGraphicsPathItem):
    """
    Temporary dashed wire while dragging from a node socket.
    """
    def __init__(self, start_pos: QtCore.QPointF):
        super().__init__()
        self.start = start_pos
        pen = QtGui.QPen(QtGui.QColor("#a1a1aa"), 2, QtCore.Qt.DashLine)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(5)

    def updateTo(self, end_pos: QtCore.QPointF):
        s = self.start
        d = end_pos
        self.setPath(_elbow_path(s, d))
