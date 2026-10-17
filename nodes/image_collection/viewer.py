from __future__ import annotations

from pathlib import Path
from typing import Sequence

from echograph.qt_compat import QKeySequence, QShortcut, QtCore, QtGui, QtWidgets


class _NavigationButton(QtWidgets.QToolButton):
    def __init__(self, forward, parent=None):
        super().__init__(parent)
        self._forward = forward

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#b0b8c4" if self.isEnabled() else "#64748b"))
        center = QtCore.QRectF(self.rect()).center()
        direction = 1 if self._forward else -1
        painter.drawPolygon(QtGui.QPolygonF([
            center + QtCore.QPointF(-7 * direction, -9),
            center + QtCore.QPointF(7 * direction, 0),
            center + QtCore.QPointF(-7 * direction, 9),
        ]))
        painter.end()


class _CenteredContentButton(QtWidgets.QToolButton):
    """Center visible text bounds rather than the font's baseline spacing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)

    def paintEvent(self, event):
        option = QtWidgets.QStyleOptionToolButton()
        self.initStyleOption(option)
        option.text = ""
        option.icon = QtGui.QIcon()
        painter = QtGui.QPainter(self)
        self.style().drawComplexControl(QtWidgets.QStyle.CC_ToolButton, option, painter, self)
        glyphs = QtGui.QPainterPath()
        glyphs.addText(0, 0, self.font(), self.text())
        bounds = glyphs.boundingRect()
        icon_width = self.iconSize().width() if not self.icon().isNull() else 0
        gap = 10 if icon_width else 0
        left = (self.width() - bounds.width() - icon_width - gap) / 2
        if icon_width:
            icon_rect = QtCore.QRect(int(left), (self.height() - self.iconSize().height()) // 2,
                                    icon_width, self.iconSize().height())
            mode = QtGui.QIcon.Normal if self.isEnabled() else QtGui.QIcon.Disabled
            self.icon().paint(painter, icon_rect, QtCore.Qt.AlignCenter, mode)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#e2e8f0" if self.isEnabled() else "#64748b"))
        painter.drawPath(glyphs.translated(left + icon_width + gap - bounds.left(),
                                          self.height() / 2 - bounds.center().y()))
        painter.end()


class _ImageView(QtWidgets.QGraphicsView):
    navigateRequested = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        scene = QtWidgets.QGraphicsScene(self)
        self.setScene(scene)
        self._image = scene.addPixmap(QtGui.QPixmap())
        self._actual_size = False
        self._swipe_start = None
        self._press_dragged = False
        self._wheel_delta = 0
        self.setBackgroundBrush(QtGui.QColor("#101318"))
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setToolTip("Click for next. Scroll up/down or drag left/right to browse. At 100%, drag to pan.")

    def set_pixmap(self, pixmap):
        self._swipe_start = None
        # Keep the original pixels; fitting only changes the view transform.
        self._image.setPixmap(pixmap)
        self.setSceneRect(self._image.boundingRect())
        self._update_scale()
        self.centerOn(self._image)

    def set_actual_size(self, enabled):
        self._swipe_start = None
        self._actual_size = bool(enabled)
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag if enabled else QtWidgets.QGraphicsView.NoDrag)
        if not enabled:
            self.viewport().setCursor(QtCore.Qt.ArrowCursor)
        policy = QtCore.Qt.ScrollBarAsNeeded if enabled else QtCore.Qt.ScrollBarAlwaysOff
        self.setHorizontalScrollBarPolicy(policy)
        self.setVerticalScrollBarPolicy(policy)
        self._update_scale()

    def _update_scale(self):
        self.resetTransform()
        if not self._actual_size and not self._image.pixmap().isNull():
            self.fitInView(self._image, QtCore.Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._actual_size:
            self._update_scale()

    def mousePressEvent(self, event):
        self._swipe_start = None
        self._press_dragged = False
        if event.button() in (QtCore.Qt.BackButton, QtCore.Qt.ForwardButton):
            self.navigateRequested.emit(-1 if event.button() == QtCore.Qt.BackButton else 1)
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton and not self._actual_size:
            self._swipe_start = event.position().toPoint() if hasattr(event, "position") else event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._swipe_start is not None:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if (pos - self._swipe_start).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
                self._press_dragged = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        start = self._swipe_start
        self._swipe_start = None
        if event.button() in (QtCore.Qt.BackButton, QtCore.Qt.ForwardButton):
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if start is not None and event.button() == QtCore.Qt.LeftButton and not self._actual_size:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            delta = pos - start
            threshold = max(40, QtWidgets.QApplication.startDragDistance() * 2)
            if abs(delta.x()) >= threshold and abs(delta.x()) > abs(delta.y()):
                self.navigateRequested.emit(1 if delta.x() < 0 else -1)
            elif not self._press_dragged and delta.manhattanLength() < QtWidgets.QApplication.startDragDistance():
                self.navigateRequested.emit(1)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        if delta * self._wheel_delta < 0:
            self._wheel_delta = 0
        self._wheel_delta += delta
        while abs(self._wheel_delta) >= 120:
            direction = 1 if self._wheel_delta > 0 else -1
            self._wheel_delta -= direction * 120
            self.navigateRequested.emit(direction)
        event.accept()

    def hideEvent(self, event):
        self._wheel_delta = 0
        self._swipe_start = None
        super().hideEvent(event)


class ImageCollectionViewer(QtWidgets.QDialog):
    """Browse original collection images in a separate, non-modal window."""

    def __init__(self, paths: Sequence[str], index: int = 0, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowFlag(QtCore.Qt.WindowMaximizeButtonHint, True)
        self.setMinimumSize(480, 320)
        self._paths = []
        self._index = 0
        self._browse_index = 0
        self._pinned_path = None

        self.image_view = _ImageView(self)
        self.image_view.navigateRequested.connect(self._step)
        self.image_view.set_actual_size(False)
        self.error_label = QtWidgets.QLabel()
        self.error_label.setTextFormat(QtCore.Qt.PlainText)
        self.error_label.setAlignment(QtCore.Qt.AlignCenter)
        self.error_label.setWordWrap(True)
        self.pages = QtWidgets.QStackedWidget()
        self.pages.addWidget(self.image_view)
        self.pages.addWidget(self.error_label)

        self.previous_button = _NavigationButton(forward=False)
        self.previous_button.setObjectName("previousImage")
        self.previous_button.setToolTip("Previous image (Left arrow)")
        self.previous_button.setAccessibleName("Previous image")
        self.previous_button.clicked.connect(lambda: self._step(-1))
        self.next_button = _NavigationButton(forward=True)
        self.next_button.setObjectName("nextImage")
        self.next_button.setToolTip("Next image (Right arrow)")
        self.next_button.setAccessibleName("Next image")
        self.next_button.clicked.connect(lambda: self._step(1))
        self.caption = QtWidgets.QLabel()
        self.caption.setTextFormat(QtCore.Qt.PlainText)
        self.caption.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)

        self.pin_button = QtWidgets.QToolButton()
        self.pin_button.setObjectName("comparisonPin")
        self.pin_button.setCheckable(True)
        self.pin_button.setIconSize(QtCore.QSize(24, 24))
        source = QtGui.QPixmap(str(Path(__file__).resolve().parents[2] / "icons" / "Pin_b_Icon.png"))
        self._blue_pin_icon = QtGui.QIcon(source)
        original = source.toImage()
        gray = original.convertToFormat(QtGui.QImage.Format_Grayscale8).convertToFormat(QtGui.QImage.Format_ARGB32)
        gray.setAlphaChannel(original.convertToFormat(QtGui.QImage.Format_Alpha8))
        self._gray_pin_icon = QtGui.QIcon(QtGui.QPixmap.fromImage(gray))
        self.pin_button.clicked.connect(self._toggle_pin)
        for button in (self.pin_button, self.previous_button, self.next_button):
            button.setFixedSize(40, 40)

        self.actual_size_button = _CenteredContentButton()
        self.actual_size_button.setText("100%")
        self.actual_size_button.setCheckable(True)
        self.actual_size_button.setToolTip("Show original pixels at 100%; drag to pan. Turn off to fit the window.")
        self.actual_size_button.toggled.connect(self.image_view.set_actual_size)

        self.slideshow_button = _CenteredContentButton()
        self.slideshow_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.slideshow_button.setCheckable(True)
        self.slideshow_button.setToolTip("Play or pause slideshow (Space); advances every 3 seconds")
        self.slideshow_button.toggled.connect(self._toggle_slideshow)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(lambda: self._step(1))
        self._toggle_slideshow(False)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self.pin_button)
        controls.addWidget(self.previous_button)
        controls.addWidget(self.next_button)
        controls.addWidget(self.caption, 1)
        controls.addWidget(self.actual_size_button)
        controls.addWidget(self.slideshow_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(self.pages, 1)
        layout.addLayout(controls)
        self.setStyleSheet(
            "QDialog { background: #101318; color: #e2e8f0; }"
            "QLabel { color: #e2e8f0; }"
            "QToolButton { background: #1f2937; color: #e2e8f0; border: 1px solid #475569;"
            " border-radius: 4px; padding: 7px 12px; }"
            "QToolButton:hover { background: #334155; }"
            "QToolButton:checked { background: #2563eb; }"
            "QToolButton#comparisonPin:checked { background: #1f2937; border-color: #60a5fa; }"
            "QToolButton#comparisonPin { padding: 6px; }"
            "QToolButton#previousImage, QToolButton#nextImage {"
            " padding: 0px; }"
            "QToolButton:disabled { color: #64748b; border-color: #293241; }"
        )

        for key, callback in ((QtCore.Qt.Key_Left, lambda: self._step(-1)),
                              (QtCore.Qt.Key_Right, lambda: self._step(1)),
                              (QtCore.Qt.Key_Space, self.slideshow_button.click)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(QtCore.Qt.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)

        screen = parent.screen() if parent is not None else QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(int(available.width() * 0.9), int(available.height() * 0.9))
            self.move(available.center() - self.rect().center())
        else:
            self.resize(1200, 800)
        self.set_collection(paths, index)

    def set_collection(self, paths: Sequence[str], index: int = 0):
        self.slideshow_button.setChecked(False)
        self._paths = list(paths)
        if self._pinned_path not in self._paths:
            self._pinned_path = None
        multiple = len(self._paths) > 1
        for button in (self.previous_button, self.next_button, self.slideshow_button):
            button.setEnabled(multiple)
        self._show_image(max(0, min(index, len(self._paths) - 1)))

    def _show_image(self, index, remember_browse=True):
        self._index = index
        if remember_browse:
            self._browse_index = index
        path = self._paths[index] if self._paths else ""
        reader = QtGui.QImageReader(path)
        reader.setAutoTransform(True)
        image = reader.read() if path else QtGui.QImage()
        self.image_view.set_pixmap(QtGui.QPixmap.fromImage(image))
        name = Path(path).name
        self.setWindowTitle(f"Image Collection — {name}" if path else "Image Collection")
        caption = f"{index + 1} / {len(self._paths)}  ·  {name}" if path else "No images"
        self.actual_size_button.setEnabled(not image.isNull())
        if image.isNull():
            self.error_label.setText(f"Unable to open this image.\n\n{path}\n\n{reader.errorString()}"
                                     if path else "No images in this collection.")
            self.pages.setCurrentWidget(self.error_label)
        else:
            caption += f"  ·  {image.width()} × {image.height()}"
            self.pages.setCurrentWidget(self.image_view)
        self.caption.setText(caption)
        self.caption.setToolTip(path)
        self._update_pin_button()
        if self._timer.isActive():
            self._timer.start()

    def _step(self, offset):
        if len(self._paths) > 1:
            if offset < 0 and self._pinned_path in self._paths:
                self._show_image(self._paths.index(self._pinned_path), remember_browse=False)
            elif offset > 0 and self._pinned_path in self._paths and self._index != self._browse_index:
                # Return to the comparison first; a second Next advances it.
                self._show_image(self._browse_index)
            else:
                self._show_image((self._browse_index + offset) % len(self._paths))

    def _toggle_pin(self):
        path = self._paths[self._index] if self._paths else None
        self._pinned_path = None if self._pinned_path == path else path
        # After unpinning, resume ordinary navigation from the displayed image.
        if self._pinned_path is None:
            self._browse_index = self._index
        self._update_pin_button()

    def _update_pin_button(self):
        path = self._paths[self._index] if self._paths else None
        pinned = path is not None and path == self._pinned_path
        self.pin_button.setChecked(pinned)
        self.pin_button.setIcon(self._blue_pin_icon if pinned else self._gray_pin_icon)
        self.pin_button.setEnabled(bool(path) and (pinned or self.pages.currentWidget() == self.image_view))
        self.pin_button.setAccessibleName("Unpin image" if pinned else "Pin image for comparison")
        if pinned:
            tooltip = "Unpin this image to restore normal navigation. Previous shows the reference; Next returns to the comparison. Press Next again to advance."
        elif self._pinned_path:
            tooltip = f"Pin this image instead. Current reference: {Path(self._pinned_path).name}"
        else:
            tooltip = "Pin this image for comparison. Previous shows it; Next returns to the comparison. Press Next again to advance."
        self.pin_button.setToolTip(tooltip)
        self.previous_button.setToolTip("Pinned image (Left arrow)" if self._pinned_path else "Previous image (Left arrow)")
        returning = self._pinned_path is not None and self._index != self._browse_index
        self.next_button.setToolTip("Return to comparison image (Right arrow); press again to advance" if returning else "Next image (Right arrow)")

    def _toggle_slideshow(self, enabled):
        playing = bool(enabled and len(self._paths) > 1)
        if playing:
            self._timer.start()
        else:
            self._timer.stop()
        icon = QtWidgets.QStyle.SP_MediaPause if playing else QtWidgets.QStyle.SP_MediaPlay
        self.slideshow_button.setIcon(self.style().standardIcon(icon))
        self.slideshow_button.setText("Pause" if playing else "Slideshow")

    def hideEvent(self, event):
        self.slideshow_button.setChecked(False)
        super().hideEvent(event)

    def done(self, result):
        self.slideshow_button.setChecked(False)
        super().done(result)
