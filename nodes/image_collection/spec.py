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

    def load_images(self, paths: List[str], append: bool = True):
        base = list(self.paths) if append else []
        combined = base + [p for p in paths if p]
        seen = set()
        dedup_paths = []
        for p in combined:
            if p in seen:
                continue
            seen.add(p)
            dedup_paths.append(p)

        loaded = _load_pixmaps(dedup_paths)
        if not loaded:
            return
        self.paths = [p for p, _ in loaded]

        edge_pad = 4
        gap = 4
        avail_w = max(1, self.width() - 2 * edge_pad)
        avail_h = max(1, self.height() - 2 * edge_pad)

        base_sizes = []
        base_pixmaps = []
        max_img_w = 1
        max_img_h = 1
        for _, pm in loaded:
            if pm.width() <= 0 or pm.height() <= 0:
                continue
            max_img_w = max(max_img_w, pm.width())
            max_img_h = max(max_img_h, pm.height())
            base_sizes.append((pm.width(), pm.height()))
            base_pixmaps.append(pm)

        # Single global pre-scale so largest image fits.
        pre_scale = min(1.0, avail_w / float(max_img_w), avail_h / float(max_img_h))
        if pre_scale < 1.0:
            scaled_sizes = []
            scaled_pixmaps = []
            for (w, h), pm in zip(base_sizes, base_pixmaps):
                nw = max(1, int(w * pre_scale))
                nh = max(1, int(h * pre_scale))
                scaled_sizes.append((nw, nh))
                scaled_pixmaps.append(pm.scaled(nw, nh, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            base_sizes = scaled_sizes
            base_pixmaps = scaled_pixmaps

        def _layout(sizes, width_limit):
            offs = []
            x = edge_pad
            y = edge_pad
            row_h = 0
            max_row_w = 0
            for w, h in sizes:
                if x + w > width_limit - edge_pad and x > edge_pad:
                    x = edge_pad
                    y += row_h + gap
                    row_h = 0
                offs.append(QtCore.QPoint(x, y))
                row_h = max(row_h, h)
                x += w + gap
                max_row_w = max(max_row_w, x - edge_pad - gap)
            total_h = y + row_h + edge_pad
            return offs, total_h, max_row_w

        offsets, total_h, max_row_w = _layout(base_sizes, self.width())
        scale_all = min(
            1.0,
            float(avail_w) / float(max(1, max_row_w)),
            float(avail_h) / float(max(1, total_h)),
        )
        if scale_all < 1.0:
            resized_sizes = []
            resized_pixmaps = []
            for (w, h), pm in zip(base_sizes, base_pixmaps):
                nw = max(1, int(w * scale_all))
                nh = max(1, int(h * scale_all))
                resized_sizes.append((nw, nh))
                resized_pixmaps.append(pm.scaled(nw, nh, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            base_sizes = resized_sizes
            base_pixmaps = resized_pixmaps
            offsets, total_h, max_row_w = _layout(base_sizes, self.width())

        self.pixmaps = base_pixmaps
        self.offsets = offsets
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
        header.addWidget(load_btn, 0)
        header.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(header, 0)
        layout.addWidget(self.canvas, 1)

        # Reload any existing state on the model
        state = getattr(node_item.model, "_image_collection_state", None) or _default_state()
        paths = state.get("paths", [])
        if paths:
            self.canvas.load_images(paths, append=False)
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
        self.canvas.load_images(paths, append=True)
        try:
            existing = getattr(self._node_item.model, "_image_collection_state", {}) or {}
            base = list(existing.get("paths", []))
            combined = base + list(paths)
            seen = set()
            dedup = []
            for p in combined:
                if p in seen:
                    continue
                seen.add(p)
                dedup.append(p)
            setattr(self._node_item.model, "_image_collection_state", {"paths": dedup})
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
