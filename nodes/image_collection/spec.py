from __future__ import annotations

from typing import Any, Dict, List, Tuple

try:
    from PySide6 import QtWidgets, QtGui, QtCore
except Exception:
    from PySide2 import QtWidgets, QtGui, QtCore  # type: ignore

from nodes.core import Spec


DEFAULT_CANVAS_SIZE = QtCore.QSize(720, 1420)


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

        cols_hint = 3
        rows_est = max(1, (len(loaded) + cols_hint - 1) // cols_hint)
        target_row_h = max(60, min(220, int(avail_h / rows_est)))

        def _build_layout(target_h: int):
            items = []
            for pth, pm in loaded:
                w, h = pm.width(), pm.height()
                if w <= 0 or h <= 0:
                    continue
                scaled_w = int(w * (target_h / float(h)))
                items.append((pth, pm, scaled_w, target_h))

            rows = []
            row = []
            row_w = 0
            for pth, pm, sw, th in items:
                est = row_w + sw if not row else row_w + gap + sw
                if row and est > avail_w:
                    rows.append((row, row_w))
                    row = []
                    row_w = 0
                row.append((pth, pm, sw, th))
                row_w += sw if not row[:-1] else sw
            if row:
                rows.append((row, row_w))

            row_heights = []
            row_scales = []
            for row, rw in rows:
                n = len(row)
                available_w = max(1, avail_w - gap * max(0, n - 1))
                scale = available_w / float(rw) if rw > 0 else 1.0
                row_scales.append(scale)
                row_heights.append(int(target_h * scale))

            total_h = sum(row_heights) + gap * max(0, len(row_heights) - 1)
            return rows, row_scales, row_heights, total_h

        rows, row_scales, row_heights, total_h = _build_layout(target_row_h)
        if total_h > avail_h:
            shrink = float(avail_h) / float(total_h)
            new_h = max(40, int(target_row_h * shrink))
            rows, row_scales, row_heights, total_h = _build_layout(new_h)

        pixmaps_out = []
        offsets_out = []
        y = edge_pad
        for (row, _rw), rscale, rheight in zip(rows, row_scales, row_heights):
            x = edge_pad
            for pth, pm, sw, th in row:
                final_w = max(1, int(sw * rscale))
                final_h = max(1, int(th * rscale))
                pixmaps_out.append(pm.scaled(final_w, final_h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
                offsets_out.append(QtCore.QPoint(x, y))
                x += final_w + gap
            y += int(rheight) + gap

        self.pixmaps = pixmaps_out
        self.offsets = offsets_out
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
        self._sync_canvas_size()

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

        edit_btn = QtWidgets.QPushButton("Edit")
        edit_btn.setEnabled(True)
        edit_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:6px 10px;}"
            "QPushButton:hover{background:#273449;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(header, 0)
        layout.addWidget(edit_btn, 0)
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_canvas_size()

    def _sync_canvas_size(self):
        try:
            w = max(DEFAULT_CANVAS_SIZE.width(), int(getattr(self._node_item, "width", DEFAULT_CANVAS_SIZE.width())) - 12)
        except Exception:
            w = DEFAULT_CANVAS_SIZE.width()
        h = DEFAULT_CANVAS_SIZE.height()
        self.canvas.setFixedSize(w, h)

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
