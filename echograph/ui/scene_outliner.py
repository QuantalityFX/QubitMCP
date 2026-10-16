"""Persistent display ordering for Scene outliner groups and panel siblings."""
import json

from PySide6 import QtCore, QtGui, QtWidgets


ROW_MIME = "application/x-qubit-scene-outliner-row"
PARENT_ROLE = QtCore.Qt.UserRole + 3


def read_order(raw):
    try:
        order = json.loads(raw)
        if not isinstance(order, dict):
            return {}
        return order
    except (ValueError, TypeError):
        return {}


def ordered_rows(rows, order):
    groups = {}
    for row in rows:
        root = str(row.get("parent") or row["name"])
        groups.setdefault(root, []).append(row)
    roots = order.get("roots", [])
    roots = roots if isinstance(roots, list) else []
    root_rank = {name: index for index, name in enumerate(roots) if isinstance(name, str)}
    children = order.get("children", {})
    children = children if isinstance(children, dict) else {}
    result = []
    for root in sorted(groups, key=lambda name: root_rank.get(name, len(root_rank))):
        group = groups[root]
        panels = [row for row in group if row.get("kind") == "pattern_piece"]
        panel_order = children.get(root, [])
        panel_order = panel_order if isinstance(panel_order, list) else []
        rank = {name: index for index, name in enumerate(panel_order) if isinstance(name, str)}
        panels.sort(key=lambda row: rank.get(row["name"], len(rank)))
        panels = iter(panels)
        result.extend(next(panels) if row.get("kind") == "pattern_piece" else row for row in group)
    return result


class SceneOutliner(QtWidgets.QListWidget):
    orderChanged = QtCore.Signal(object)

    def __init__(self, scene_name, parent=None):
        super().__init__(parent)
        self.setResizeMode(QtWidgets.QListView.Adjust)
        self.scene_name = scene_name
        self.rows = []
        self.order = {}
        self._drop_line = None
        self._drag_position = None
        self._drag_source = None
        self._scroll_timer = QtCore.QTimer(self)
        self._scroll_timer.setInterval(50)
        self._scroll_timer.timeout.connect(self._auto_scroll)
        self.setAcceptDrops(True)
        self.setAutoScroll(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDrop)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Embedded row widgets must follow splitter drags immediately, rather
        # than retaining the geometry from the previous item layout.
        self.doItemsLayout()

    def _source(self, event):
        mime = event.mimeData()
        if not mime.hasFormat(ROW_MIME):
            return None
        try:
            source = json.loads(bytes(mime.data(ROW_MIME)))
        except (ValueError, TypeError):
            return None
        if not isinstance(source, dict) or source.get("scene") != self.scene_name:
            return None
        if not any(row["name"] == source.get("name") and str(row.get("parent") or "") == source.get("parent", "")
                   and row.get("kind") == source.get("kind") for row in self.rows):
            return None
        return source

    def _destination(self, point, source):
        item = self.itemAt(point)
        if item is None and self.count():
            item = self.item(self.count() - 1)
        if item is None:
            return None
        kind = str(item.data(QtCore.Qt.UserRole + 1) or "")
        parent = str(item.data(PARENT_ROLE) or "")
        name = str(item.data(QtCore.Qt.UserRole + 2) or item.data(QtCore.Qt.UserRole) or "")
        rect = self.visualItemRect(item)
        after = point.y() >= rect.center().y()
        if source.get("parent"):
            if source.get("kind") != "pattern_piece" or kind != "pattern_piece" or parent != source["parent"]:
                return None
        else:
            name = parent or name
            indices = [i for i in range(self.count()) if
                       str(self.item(i).data(PARENT_ROLE) or self.item(i).data(QtCore.Qt.UserRole) or "") == name]
            if indices:
                rect = self.visualItemRect(self.item(indices[-1] if after else indices[0]))
        return name, after, rect.bottom() + 1 if after else rect.top()

    def dragEnterEvent(self, event):
        if self._source(event) is not None:
            event.setDropAction(QtCore.Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        source = self._source(event)
        point = event.position().toPoint()
        destination = self._destination(point, source) if source is not None else None
        self._drag_position, self._drag_source = point, source
        if source is not None:
            self._scroll_timer.start()
        self._drop_line = destination[2] if destination is not None else None
        self.viewport().update()
        if destination is not None:
            event.setDropAction(QtCore.Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._scroll_timer.stop()
        self._drop_line = None
        self.viewport().update()
        event.accept()

    def dropEvent(self, event):
        self._scroll_timer.stop()
        source = self._source(event)
        destination = self._destination(event.position().toPoint(), source) if source is not None else None
        self._drop_line = None
        self.viewport().update()
        if destination is None:
            event.ignore()
            return
        target, after, _ = destination
        parent = source.get("parent", "")
        names = ([row["name"] for row in self.rows if row.get("parent") == parent and row.get("kind") == "pattern_piece"]
                 if parent else [row["name"] for row in self.rows if not row.get("child")])
        if source["name"] != target and target in names:
            names.remove(source["name"])
            names.insert(names.index(target) + int(after), source["name"])
            order = dict(self.order)
            if parent:
                children = order.get("children", {})
                order["children"] = {**(children if isinstance(children, dict) else {}), parent: names}
            else:
                order["roots"] = names
            self.orderChanged.emit(order)
        event.setDropAction(QtCore.Qt.MoveAction)
        event.accept()

    def _auto_scroll(self):
        if self._drag_position is None or self._drag_source is None:
            self._scroll_timer.stop()
            return
        y = self._drag_position.y()
        direction = -1 if y < 24 else 1 if y > self.viewport().height() - 24 else 0
        if direction:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + direction * max(1, bar.singleStep()))
            destination = self._destination(self._drag_position, self._drag_source)
            self._drop_line = destination[2] if destination is not None else None
            self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_line is not None:
            painter = QtGui.QPainter(self.viewport())
            painter.setPen(QtGui.QPen(QtGui.QColor("#60a5fa"), 2))
            painter.drawLine(6, self._drop_line, self.viewport().width() - 6, self._drop_line)
            painter.end()
