from __future__ import annotations

from typing import Any, Dict, List, Tuple

try:
    from PySide6 import QtWidgets, QtGui, QtCore
except Exception:
    from PySide2 import QtWidgets, QtGui, QtCore  # type: ignore

from nodes.core import Spec


DEFAULT_CANVAS_SIZE = QtCore.QSize(720, 420)


def _default_state() -> Dict[str, Any]:
    return {"paths": []}


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
        self.setFixedSize(DEFAULT_CANVAS_SIZE)
        self.pixmaps: List[QtGui.QPixmap] = []
        self.paths: List[str] = []
        self.offsets: List[QtCore.QPoint] = []

    def load_images(self, paths: List[str]):
        loaded = _load_pixmaps(paths)
        if not loaded:
            return
        self.paths = [p for p, _ in loaded]

        n = len(loaded)
        cols = min(3, max(1, n))
        rows = (n + cols - 1) // cols
        pad = 6
        cell_w = max(1, int((self.width() - pad * (cols - 1)) / cols))
        cell_h = max(1, int((self.height() - pad * (rows - 1)) / rows))

        scaled = []
        for _, pm in loaded:
            if pm.width() <= 0 or pm.height() <= 0:
                continue
            factor = min(1.0, cell_w / float(pm.width()), cell_h / float(pm.height()))
            if factor < 1.0:
                pm = pm.scaled(
                    max(1, int(pm.width() * factor)),
                    max(1, int(pm.height() * factor)),
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
            scaled.append(pm)
        self.pixmaps = scaled

        self.offsets = []
        x = 0
        y = 0
        max_h = 0
        col = 0
        for pm in self.pixmaps:
            self.offsets.append(QtCore.QPoint(x, y))
            max_h = max(max_h, pm.height())
            col += 1
            if col >= cols:
                col = 0
                x = 0
                y += max_h + pad
                max_h = 0
            else:
                x += cell_w + pad
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
        load_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:6px 10px;}"
            "QPushButton:hover{background:#273449;}"
        )

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        title = QtWidgets.QLabel("Image Collection")
        title.setStyleSheet("color:#e2e8f0;font-weight:bold;")
        header.addWidget(title, 0)
        header.addStretch(1)
        header.addWidget(load_btn, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(header, 0)
        layout.addWidget(self.canvas, 1)

        # Reload any existing state on the model
        state = getattr(node_item.model, "_image_collection_state", None) or _default_state()
        paths = state.get("paths", [])
        if paths:
            self.canvas.load_images(paths)
        try:
            if not getattr(node_item.model, "_image_collection_state", None):
                setattr(node_item.model, "_image_collection_state", dict(state))
        except Exception:
            pass

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
