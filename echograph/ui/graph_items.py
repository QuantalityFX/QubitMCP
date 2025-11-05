# echograph/ui/graph_items.py
from echograph.qt_compat import QtCore, QtGui, QtWidgets

def _gi_flag(enum_name, fallback_enum):
    if hasattr(QtWidgets.QGraphicsItem, enum_name):
        return getattr(QtWidgets.QGraphicsItem, enum_name)
    if hasattr(QtWidgets.QGraphicsItem, "GraphicsItemFlag"):
        return getattr(QtWidgets.QGraphicsItem.GraphicsItemFlag, enum_name, fallback_enum)
    return fallback_enum


class EdgeItem(QtWidgets.QGraphicsPathItem):
    def __init__(self, src, dst):
        super().__init__()
        self.setZValue(0)
        self.src = src
        self.dst = dst
        self.pen_normal = QtGui.QPen(QtGui.QColor("#586473"), 2)
        self.pen_path   = QtGui.QPen(QtGui.QColor("#22c55e"), 3)
        self._highlight = False
        self.setPen(self.pen_normal)
        self.setBrush(QtCore.Qt.NoBrush)
        self.updatePath()
        self.setAcceptHoverEvents(True)

    def setHighlighted(self, h: bool):
        self._highlight = h
        self.setPen(self.pen_path if h else self.pen_normal)
        self.update()

    def updatePath(self):
        s = self.src.scenePos() + QtCore.QPointF(self.src.width, self.src._BASE_H / 2)
        d = self.dst.scenePos() + QtCore.QPointF(0, self.dst._BASE_H / 2)
        dx = max(80, abs(d.x() - s.x()) * 0.5)
        c1 = QtCore.QPointF(s.x() + dx, s.y())
        c2 = QtCore.QPointF(d.x() - dx, d.y())
        path = QtGui.QPainterPath(s)
        path.cubicTo(c1, c2, d)
        self.setPath(path)

    def hoverEnterEvent(self, e):
        pen = QtGui.QPen(self.pen().color(), self.pen().widthF() + 0.5)
        self.setPen(pen)

    def hoverLeaveEvent(self, e):
        self.setHighlighted(self._highlight)

    def mousePressEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if e.button() == QtCore.Qt.LeftButton and (e.modifiers() & QtCore.Qt.AltModifier):
            sc = self.scene()
            if sc and hasattr(sc, "_edges"):
                try: sc._on_edge_removed(self)
                except Exception: pass
                try: sc.removeItem(self)
                except Exception: pass
                try: sc._edges.remove(self)
                except Exception: pass
                e.accept()
                return
        super().mousePressEvent(e)


class TempWire(QtWidgets.QGraphicsPathItem):
    def __init__(self, start_pos: QtCore.QPointF):
        super().__init__()
        self.start = start_pos
        pen = QtGui.QPen(QtGui.QColor("#a1a1aa"), 2, QtCore.Qt.DashLine)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(5)

    def updateTo(self, end_pos: QtCore.QPointF):
        s = self.start; d = end_pos
        dx = max(80, abs(d.x()-s.x())*0.5)
        c1 = QtCore.QPointF(s.x()+dx, s.y())
        c2 = QtCore.QPointF(d.x()-dx, d.y())
        path = QtGui.QPainterPath(s); path.cubicTo(c1, c2, d)
        self.setPath(path)
