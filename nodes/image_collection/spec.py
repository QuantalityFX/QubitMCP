from __future__ import annotations

from typing import Any, Dict, List, Tuple

try:
    from PySide6 import QtWidgets, QtGui, QtCore
except Exception:
    from PySide2 import QtWidgets, QtGui, QtCore  # type: ignore

from nodes.core import Spec


DEFAULT_CANVAS_SIZE = QtCore.QSize(720, 420)


def _load_pixmaps(paths: List[str]) -> List[Tuple[str, QtGui.QPixmap]]:
    out = []
    for p in paths:
        pm = QtGui.QPixmap(p)
        if not pm.isNull():
            out.append((p, pm))
    return out


def _mosaic_offsets(pixmaps: List[QtGui.QPixmap], cols: int = 3, pad: int = 10) -> List[QtCore.QPoint]:
    offsets = []
    col = 0
    x_accum = 0
    y_accum = 0
    max_h_in_row = 0
    for pm in pixmaps:
        w = pm.width()
        h = pm.height()
        offsets.append(QtCore.QPoint(x_accum, y_accum))
        if h > max_h_in_row:
            max_h_in_row = h
        col += 1
        if col >= cols:
            col = 0
            x_accum = 0
            y_accum += max_h_in_row + pad
            max_h_in_row = 0
        else:
            x_accum += w + pad
    return offsets


class _ImageCanvas(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.pixmaps: List[QtGui.QPixmap] = []
        self.paths: List[str] = []
        self.offsets: List[QtCore.QPoint] = []

    def load_images(self, paths: List[str]):
        loaded = _load_pixmaps(paths)
        if not loaded:
            return
        self.paths = [p for p, _ in loaded]
        self.pixmaps = [pm for _, pm in loaded]
        self.offsets = _mosaic_offsets(self.pixmaps)
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        for pm, off in zip(self.pixmaps, self.offsets):
            painter.drawPixmap(QtCore.QPoint(off.x(), off.y()), pm)
        painter.end()

    def sizeHint(self):
        return DEFAULT_CANVAS_SIZE


class ImageCollectionWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self.canvas = _ImageCanvas(self)

        load_btn = QtWidgets.QPushButton("Load Images")
        load_btn.clicked.connect(self._pick_images)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(load_btn, 0)
        layout.addWidget(self.canvas, 1)

        # Reload any existing state on the model
        state = getattr(node_item.model, "_image_collection_state", None) or {}
        paths = state.get("paths", [])
        if paths:
            self.canvas.load_images(paths)

    def _pick_images(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select Images",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)",
        )
        if not paths:
            return
        self.canvas.load_images(paths)
        try:
            setattr(self._node_item.model, "_image_collection_state", {"paths": paths})
        except Exception:
            pass

    def sizeHint(self):
        hint = self.canvas.sizeHint()
        return QtCore.QSize(hint.width(), hint.height() + 40)


def render_node_body(node_item, y_cursor: int) -> int:
    body = ImageCollectionWidget(node_item, node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    h = body.sizeHint().height()
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    return y_cursor + h


IMAGE_COLLECTION_SPEC = Spec(
    stripe_color="#22c55e",
    render_node_body=render_node_body,
)
