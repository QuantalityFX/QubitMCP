"""Pins above the embedded widget, with the node's existing wire anchors."""
from PySide6 import QtCore, QtGui, QtWidgets


class PortDot(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, node_item, proxy, name, side, position):
        radius = node_item._PORT_DOT_RADIUS
        super().__init__(-radius, -radius, radius * 2, radius * 2, proxy)
        self.node_item = node_item
        self.port_name = name
        self.side = side
        self.setPos(proxy.mapFromParent(position))
        # Parenting to the proxy keeps pins above it when the node is raised.
        self.setZValue(1)
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton if side == "out" else QtCore.Qt.NoButton)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(f"{'Output' if side == 'out' else 'Input'}: {name}")

    def paint(self, painter, option, widget=None):
        wired = self.side == "in" and self.port_name in self.node_item._wired_named_inputs()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#facc15" if wired else "#cbd5e1"))
        painter.drawEllipse(self.rect())

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.side == "out":
            node = self.node_item
            node._bring_to_front()
            node.setSelected(True)
            scene = node.scene()
            if scene is not None:
                scene._active_node_item = node
                scene._group_drag_active = False
            node._wire_drag_src_port_name = self.port_name
            node.startWireDrag.emit(node)
            event.accept()
            return
        event.ignore()

    def mouseReleaseEvent(self, event):
        event.accept()
