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


def _lead_for_delta(total_delta: float) -> float:
    abs_d = abs(total_delta)
    lead_base = 24.0
    min_lead = 8.0
    lead = lead_base
    if abs_d < lead_base * 2.0:
        lead = max(min_lead, abs_d * 0.35)
    if total_delta > 0.0:
        min_mid = max(10.0, lead_base * 0.5)
        max_lead = (total_delta - min_mid) * 0.5
        if max_lead < lead:
            lead = max(min_lead, max_lead)
    return max(min_lead, lead)


def _elbow_points(
    start: QtCore.QPointF,
    end: QtCore.QPointF,
    lead_out: float | None = None,
    lead_in: float | None = None,
    axis_start: str = "x",
    axis_end: str = "x",
    dir_start: float = 1.0,
    dir_end: float = 1.0,
) -> tuple[list[QtCore.QPointF], float]:
    sx = float(start.x())
    sy = float(start.y())
    dx = float(end.x())
    dy = float(end.y())
    total_dx = dx - sx
    total_dy = dy - sy

    abs_dx = abs(total_dx)
    abs_dy = abs(total_dy)
    if lead_out is None:
        lead_out = _lead_for_delta(total_dx if axis_start == "x" else total_dy)
    if lead_in is None:
        lead_in = _lead_for_delta(total_dx if axis_end == "x" else total_dy)
    lead_base = max(12.0, float(lead_out), float(lead_in))

    if axis_start == "x":
        out_pt = QtCore.QPointF(sx + dir_start * lead_out, sy)
    else:
        out_pt = QtCore.QPointF(sx, sy + dir_start * lead_out)
    if axis_end == "x":
        in_pt = QtCore.QPointF(dx - dir_end * lead_in, dy)
    else:
        in_pt = QtCore.QPointF(dx, dy - dir_end * lead_in)

    tight_thresh = max(16.0, lead_base * 0.9)
    points = [QtCore.QPointF(sx, sy), out_pt]

    if axis_start == axis_end:
        if axis_start == "x":
            mid_y = (sy + dy) * 0.5
            mid_run = abs(in_pt.x() - out_pt.x())
            if abs_dy < tight_thresh:
                points.extend(
                    [
                        QtCore.QPointF(in_pt.x(), sy),
                        QtCore.QPointF(in_pt.x(), dy),
                        QtCore.QPointF(dx, dy),
                    ]
                )
            elif mid_run < tight_thresh:
                points.extend([QtCore.QPointF(out_pt.x(), dy), QtCore.QPointF(dx, dy)])
            else:
                points.extend(
                    [
                        QtCore.QPointF(out_pt.x(), mid_y),
                        QtCore.QPointF(in_pt.x(), mid_y),
                        QtCore.QPointF(in_pt.x(), dy),
                        QtCore.QPointF(dx, dy),
                    ]
                )
        else:
            mid_x = (sx + dx) * 0.5
            mid_run = abs(in_pt.y() - out_pt.y())
            if abs_dx < tight_thresh:
                points.extend(
                    [
                        QtCore.QPointF(sx, in_pt.y()),
                        QtCore.QPointF(dx, in_pt.y()),
                        QtCore.QPointF(dx, dy),
                    ]
                )
            elif mid_run < tight_thresh:
                points.extend([QtCore.QPointF(dx, out_pt.y()), QtCore.QPointF(dx, dy)])
            else:
                points.extend(
                    [
                        QtCore.QPointF(mid_x, out_pt.y()),
                        QtCore.QPointF(mid_x, in_pt.y()),
                        QtCore.QPointF(dx, in_pt.y()),
                        QtCore.QPointF(dx, dy),
                    ]
                )
    else:
        if abs_dx < tight_thresh or abs_dy < tight_thresh:
            if axis_start == "x":
                points.extend([QtCore.QPointF(out_pt.x(), dy), QtCore.QPointF(dx, dy)])
            else:
                points.extend([QtCore.QPointF(dx, out_pt.y()), QtCore.QPointF(dx, dy)])
        else:
            if axis_start == "x":
                mid = QtCore.QPointF(out_pt.x(), in_pt.y())
            else:
                mid = QtCore.QPointF(in_pt.x(), out_pt.y())
            points.extend([mid, in_pt, QtCore.QPointF(dx, dy)])
    filtered = []
    for p in points:
        if filtered and abs(filtered[-1].x() - p.x()) <= 1e-6 and abs(filtered[-1].y() - p.y()) <= 1e-6:
            continue
        filtered.append(p)

    span = min(abs_dx, abs_dy, lead_out, lead_in)
    corner = max(6.0, min(24.0, span * 0.4))
    return filtered, corner


def _elbow_path(
    start: QtCore.QPointF,
    end: QtCore.QPointF,
    lead_out: float | None = None,
    lead_in: float | None = None,
    axis_start: str = "x",
    axis_end: str = "x",
    dir_start: float = 1.0,
    dir_end: float = 1.0,
) -> QtGui.QPainterPath:
    points, corner = _elbow_points(
        start,
        end,
        lead_out=lead_out,
        lead_in=lead_in,
        axis_start=axis_start,
        axis_end=axis_end,
        dir_start=dir_start,
        dir_end=dir_end,
    )
    return _rounded_polyline_path(points, corner)


class EdgePin(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, edge, scene_pos: QtCore.QPointF, radius: float = 5.0, axis_hint: str | None = None):
        super().__init__(-radius, -radius, radius * 2.0, radius * 2.0)
        self._edge = edge
        self.axis_hint = axis_hint if axis_hint in ("x", "y") else None
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
        self._sync_color()

    def _sync_color(self):
        edge = getattr(self, "_edge", None)
        color = QtGui.QColor("#60a5fa")
        if edge is not None:
            try:
                color = edge.pen().color()
            except Exception:
                pass
        try:
            self.setBrush(QtGui.QBrush(color))
        except Exception:
            self.setBrush(color)

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            edge = getattr(self, "_edge", None)
            if edge is not None:
                try:
                    if hasattr(edge, "_axis_hint_for_point"):
                        self.axis_hint = edge._axis_hint_for_point(self.scenePos())
                except Exception:
                    pass
                try:
                    edge.updatePath()
                except Exception:
                    pass
        return super().itemChange(change, value)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            if not (e.modifiers() & QtCore.Qt.ShiftModifier):
                _deselect_node_items(self.scene())
        if e.button() == QtCore.Qt.LeftButton and (e.modifiers() & QtCore.Qt.ControlModifier):
            edge = getattr(self, "_edge", None)
            if edge is not None and hasattr(edge, "remove_pin"):
                try:
                    edge.remove_pin(self)
                except Exception:
                    pass
            sc = self.scene()
            if sc is not None:
                try:
                    sc.removeItem(self)
                except Exception:
                    pass
            e.accept()
            return
        if e.button() == QtCore.Qt.LeftButton:
            edge = getattr(self, "_edge", None)
            if edge is not None and hasattr(edge, "_select_clicked"):
                try:
                    edge._select_clicked()
                except Exception:
                    pass
            super().mousePressEvent(e)
            return
        super().mousePressEvent(e)


def _gi_flag(enum_name, fallback_enum):
    if hasattr(QtWidgets.QGraphicsItem, enum_name):
        return getattr(QtWidgets.QGraphicsItem, enum_name)
    if hasattr(QtWidgets.QGraphicsItem, "GraphicsItemFlag"):
        return getattr(QtWidgets.QGraphicsItem.GraphicsItemFlag, enum_name, fallback_enum)
    return fallback_enum


def _deselect_node_items(scene) -> None:
    if scene is None:
        return
    try:
        items = list(scene.selectedItems())
    except Exception:
        return
    for it in items:
        name = getattr(it, "__class__", type(it)).__name__
        if name in ("NodeItem", "CommentGroup"):
            try:
                it.setSelected(False)
            except Exception:
                pass
    try:
        scene._group_drag_active = False
    except Exception:
        pass


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
        self._polyline_points = []
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
        self._sync_pin_colors()

    def _sync_pin_colors(self):
        for pin in list(self._pins):
            if pin is None:
                continue
            try:
                pin._sync_color()
            except Exception:
                pass

    def _select_clicked(self):
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

    def _axis_hint_for_point(self, scene_pos: QtCore.QPointF) -> str | None:
        pts = list(getattr(self, "_polyline_points", []) or [])
        if len(pts) < 2:
            return None
        px = float(scene_pos.x())
        py = float(scene_pos.y())
        best_axis = None
        best_dist = None

        def _dist2_point_to_segment(p0, p1):
            x1 = float(p0.x())
            y1 = float(p0.y())
            x2 = float(p1.x())
            y2 = float(p1.y())
            vx = x2 - x1
            vy = y2 - y1
            denom = vx * vx + vy * vy
            if denom <= 1e-6:
                dx = px - x1
                dy = py - y1
                return dx * dx + dy * dy
            t = ((px - x1) * vx + (py - y1) * vy) / denom
            t = max(0.0, min(1.0, t))
            proj_x = x1 + t * vx
            proj_y = y1 + t * vy
            dx = px - proj_x
            dy = py - proj_y
            return dx * dx + dy * dy

        for i in range(len(pts) - 1):
            a = pts[i]
            b = pts[i + 1]
            dist2 = _dist2_point_to_segment(a, b)
            axis = "y" if abs(b.y() - a.y()) > abs(b.x() - a.x()) else "x"
            if best_dist is None or dist2 < best_dist:
                best_dist = dist2
                best_axis = axis
        return best_axis

    def add_pin(self, scene_pos: QtCore.QPointF) -> None:
        sc = self.scene()
        if sc is None:
            return
        axis_hint = self._axis_hint_for_point(scene_pos)
        pin = EdgePin(self, scene_pos, axis_hint=axis_hint)
        sc.addItem(pin)
        self._pins.append(pin)
        self._sync_pin_colors()
        self.updatePath()

    def remove_pin(self, pin) -> None:
        if pin in self._pins:
            try:
                self._pins.remove(pin)
            except Exception:
                pass
        sc = self.scene()
        if sc is not None:
            try:
                sc.removeItem(pin)
            except Exception:
                pass
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
            entry = [float(pos.x()), float(pos.y())]
            axis = getattr(pin, "axis_hint", None)
            if axis in ("x", "y"):
                points.append({"pos": entry, "axis": axis})
            else:
                points.append(entry)
        return points

    def add_pins_from_positions(self, positions) -> None:
        sc = self.scene()
        if sc is None:
            return
        for pos in positions or []:
            try:
                axis_hint = None
                if isinstance(pos, dict):
                    raw = pos.get("pos") or pos.get("point") or pos.get("p")
                    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                        x = float(raw[0])
                        y = float(raw[1])
                    else:
                        continue
                    axis_hint = pos.get("axis")
                else:
                    x = float(pos[0])
                    y = float(pos[1])
            except Exception:
                continue
            pin = EdgePin(self, QtCore.QPointF(x, y), axis_hint=axis_hint)
            sc.addItem(pin)
            self._pins.append(pin)
        self._sync_pin_colors()
        self.updatePath()

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
            points, corner = _elbow_points(s, d)
            self._polyline_points = list(points)
            self.setPath(_rounded_polyline_path(points, corner))
            return

        def _axis_for_segment(a: QtCore.QPointF, b: QtCore.QPointF) -> str:
            dx = float(b.x() - a.x())
            dy = float(b.y() - a.y())
            return "y" if abs(dy) > abs(dx) else "x"

        def _dir_for_axis(a: QtCore.QPointF, b: QtCore.QPointF, axis: str) -> float:
            delta = float((b.y() - a.y()) if axis == "y" else (b.x() - a.x()))
            return 1.0 if delta >= 0.0 else -1.0

        def _delta_for_axis(a: QtCore.QPointF, b: QtCore.QPointF, axis: str) -> float:
            return float((b.y() - a.y()) if axis == "y" else (b.x() - a.x()))

        def _append_path(base: QtGui.QPainterPath, extra: QtGui.QPainterPath) -> QtGui.QPainterPath:
            if hasattr(base, "connectPath"):
                base.connectPath(extra)
            else:
                base.addPath(extra)
            return base

        def _append_points(accum, more):
            if not more:
                return accum
            if not accum:
                return list(more)
            if abs(accum[-1].x() - more[0].x()) <= 1e-6 and abs(accum[-1].y() - more[0].y()) <= 1e-6:
                accum.extend(list(more)[1:])
            else:
                accum.extend(list(more))
            return accum

        pin_lead = 24.0
        first = pins[0]
        axis_end = getattr(first, "axis_hint", None) or _axis_for_segment(s, first)
        dir_end = _dir_for_axis(s, first, axis_end)
        lead_out = _lead_for_delta(first.x() - s.x())
        lead_in = pin_lead
        poly_points = []
        seg_points, _ = _elbow_points(
            s,
            first,
            lead_out=lead_out,
            lead_in=lead_in,
            axis_start="x",
            axis_end=axis_end,
            dir_start=1.0,
            dir_end=dir_end,
        )
        poly_points = _append_points(poly_points, seg_points)
        path = _elbow_path(
            s,
            first,
            lead_out=lead_out,
            lead_in=lead_in,
            axis_start="x",
            axis_end=axis_end,
            dir_start=1.0,
            dir_end=dir_end,
        )
        for idx in range(len(pins) - 1):
            p0 = pins[idx]
            p1 = pins[idx + 1]
            axis_start = getattr(p0, "axis_hint", None) or _axis_for_segment(p0, p1)
            axis_end = getattr(p1, "axis_hint", None) or _axis_for_segment(p0, p1)
            lead_out = pin_lead
            lead_in = pin_lead
            dir_start = _dir_for_axis(p0, p1, axis_start)
            dir_end = _dir_for_axis(p0, p1, axis_end)
            seg_points, _ = _elbow_points(
                p0,
                p1,
                lead_out=lead_out,
                lead_in=lead_in,
                axis_start=axis_start,
                axis_end=axis_end,
                dir_start=dir_start,
                dir_end=dir_end,
            )
            poly_points = _append_points(poly_points, seg_points)
            seg = _elbow_path(
                p0,
                p1,
                lead_out=lead_out,
                lead_in=lead_in,
                axis_start=axis_start,
                axis_end=axis_end,
                dir_start=dir_start,
                dir_end=dir_end,
            )
            path = _append_path(path, seg)
        last = pins[-1]
        axis_start = getattr(last, "axis_hint", None) or _axis_for_segment(last, d)
        dir_start = _dir_for_axis(last, d, axis_start)
        lead_out = pin_lead
        lead_in = _lead_for_delta(d.x() - last.x())
        seg_points, _ = _elbow_points(
            last,
            d,
            lead_out=lead_out,
            lead_in=lead_in,
            axis_start=axis_start,
            axis_end="x",
            dir_start=dir_start,
            dir_end=1.0,
        )
        poly_points = _append_points(poly_points, seg_points)
        tail = _elbow_path(
            last,
            d,
            lead_out=lead_out,
            lead_in=lead_in,
            axis_start=axis_start,
            axis_end="x",
            dir_start=dir_start,
            dir_end=1.0,
        )
        path = _append_path(path, tail)
        self._polyline_points = poly_points
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
        if e.button() == QtCore.Qt.LeftButton:
            if not (e.modifiers() & QtCore.Qt.ShiftModifier):
                _deselect_node_items(self.scene())
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
            self._select_clicked()
            e.accept()
            return
        if e.button() == QtCore.Qt.LeftButton:
            self._select_clicked()
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
