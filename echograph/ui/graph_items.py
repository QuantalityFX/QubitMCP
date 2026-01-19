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


def _elbow_path(start: QtCore.QPointF, end: QtCore.QPointF) -> QtGui.QPainterPath:
    sx = float(start.x())
    sy = float(start.y())
    dx = float(end.x())
    dy = float(end.y())
    total_dx = dx - sx
    total_dy = dy - sy

    lead = max(40.0, min(180.0, abs(total_dx) * 0.35))
    sign = 1.0 if total_dx >= 0.0 else -1.0
    out_x = sx + sign * lead
    in_x = dx - sign * lead
    if (sign > 0.0 and out_x > in_x) or (sign < 0.0 and out_x < in_x):
        mid_x = (sx + dx) * 0.5
    else:
        mid_x = (out_x + in_x) * 0.5

    points = [
        QtCore.QPointF(sx, sy),
        QtCore.QPointF(mid_x, sy),
        QtCore.QPointF(mid_x, dy),
        QtCore.QPointF(dx, dy),
    ]
    span = min(abs(total_dx), abs(total_dy))
    corner = max(6.0, min(28.0, span * 0.25))
    return _rounded_polyline_path(points, corner)


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
        self._highlight = False
        self.setPen(self.pen_normal)
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
        self.setPen(self.pen_path if h else self.pen_normal)
        self.update()

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
        self.setPath(_elbow_path(s, d))

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
        self.setHighlighted(self._highlight)
        super().hoverLeaveEvent(e)

    def mousePressEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        # Alt+LMB deletes the edge (with bookkeeping in Scene)
        if e.button() == QtCore.Qt.LeftButton and (e.modifiers() & QtCore.Qt.AltModifier):
            sc = self.scene()
            if sc and hasattr(sc, "_edges"):
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
