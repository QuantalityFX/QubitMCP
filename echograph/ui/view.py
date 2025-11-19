# echograph/ui/view.py
# Test: pan (MMB), zoom (wheel), right-click short-drag zoom still good; right-click tap opens Create Node;

import math
from echograph.qt_compat import QtCore, QtGui, QtWidgets

class GraphView(QtWidgets.QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QtGui.QPainter.Antialiasing, True)

        try:
            self.setViewportUpdateMode(QtWidgets.QGraphicsView.BoundingRectViewportUpdate)
        except AttributeError:
            self.setViewportUpdateMode(QtWidgets.QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)

        try:
            self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        except AttributeError:
            self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)

        self.setCursor(QtCore.Qt.ArrowCursor)

        try:
            self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        except AttributeError:
            self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.NoAnchor)

        self.setBackgroundBrush(QtGui.QColor("#1a1f24"))
        try:
            self.viewport().setStyleSheet("background:#1a1f24;")
        except Exception:
            pass
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        try:
            self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        except Exception:
            pass

        self._mm_dragging = False
        self._mm_last_pos = None

        self._rc_dragging = False
        self._rc_press_pos = None
        self._rc_started_over_llm = False
        self._rc_start_transform = QtGui.QTransform()
        self._rc_press_scene_pt = QtCore.QPointF()

        self._context_click_thresh = 4.0

        self._drag_divisor = 13.0
        self._zoom_multiplier = 1.1
        self._min_scale = 0.02
        self._max_scale = 50.0

    def _zoom_at(self, viewport_pos: QtCore.QPoint, factor: float):
        before = self.mapToScene(viewport_pos)
        self.scale(factor, factor)
        after = self.mapToScene(viewport_pos)
        delta = after - before
        self.translate(delta.x(), delta.y())

    def _current_scale_x(self) -> float:
        t = self.transform()
        try:
            return float(t.m11())
        except Exception:
            return 1.0

    def _clamp_factor_from(self, start_scale: float, factor: float) -> float:
        target = start_scale * factor
        if target < self._min_scale:
            return self._min_scale / max(start_scale, 1e-12)
        if target > self._max_scale:
            return self._max_scale / max(start_scale, 1e-12)
        return factor

    def _is_over_llm_view(self, viewport_pos: QtCore.QPoint) -> bool:
        sp = self.mapToScene(viewport_pos)
        for it in self.scene().items(sp):
            # If it's a proxy widget, check for the embedded LLM view by objectName
            if isinstance(it, QtWidgets.QGraphicsProxyWidget):
                w = it.widget()
                if w is not None and w.findChild(QtWidgets.QWidget, "LLMWebView") is not None:
                    return True

            # If it's a custom node item, it may expose _llm_proxy; avoid importing its class
            pr = getattr(it, "_llm_proxy", None)
            if isinstance(pr, QtWidgets.QGraphicsProxyWidget):
                try:
                    if pr.mapRectToScene(pr.boundingRect()).contains(sp):
                        return True
                except Exception:
                    pass
        return False

    def drawBackground(self, p: QtGui.QPainter, rect: QtCore.QRectF):
        p.fillRect(rect, QtGui.QColor("#1a1f24"))

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        def _unwrap_proxy(obj):
            if isinstance(obj, QtWidgets.QGraphicsProxyWidget):
                child = obj.widget()
                if child is not None:
                    return child
            return obj

        def _is_text_widget(widget) -> bool:
            if widget is None:
                return False
            text_widgets = (
                QtWidgets.QLineEdit,
                QtWidgets.QTextEdit,
                QtWidgets.QPlainTextEdit,
                QtWidgets.QTextBrowser,
                QtWidgets.QSpinBox,
                QtWidgets.QDoubleSpinBox,
                QtWidgets.QComboBox,
            )
            if isinstance(widget, text_widgets):
                if isinstance(widget, QtWidgets.QComboBox):
                    return bool(widget.isEditable())
                return True
            return False

        def _resolved_text_widget():
            fw = _unwrap_proxy(QtWidgets.QApplication.focusWidget())
            if _is_text_widget(fw):
                return fw
            scene = self.scene()
            if scene:
                item = scene.focusItem()
                fw = _unwrap_proxy(item)
                if _is_text_widget(fw):
                    return fw
            return None

        def _has_selected_nodes() -> bool:
            sc = self.scene()
            if not sc:
                return False
            try:
                for item in sc.selectedItems():
                    if getattr(item, "model", None) is not None:
                        return True
            except Exception:
                pass
            return False

        def _text_widget_wants_copy() -> bool:
            fw = _resolved_text_widget()
            if not fw:
                return False
            if isinstance(fw, QtWidgets.QComboBox):
                return bool(fw.isEditable())
            if isinstance(fw, (QtWidgets.QLineEdit, QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                return True
            if isinstance(fw, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit, QtWidgets.QTextBrowser)):
                return True
            return False

        def _text_widget_wants_paste() -> bool:
            fw = _resolved_text_widget()
            if not fw:
                return False
            if isinstance(fw, QtWidgets.QComboBox):
                return bool(fw.isEditable())
            if isinstance(fw, QtWidgets.QLineEdit):
                return not fw.isReadOnly()
            if isinstance(fw, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit, QtWidgets.QTextBrowser)):
                try:
                    return not fw.isReadOnly()
                except Exception:
                    return True
            if isinstance(fw, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                return True
            return False

        active_text_widget = _resolved_text_widget()
        if active_text_widget is not None:
            super().keyPressEvent(e)
            return

        if e.key() == QtCore.Qt.Key_Delete:
            sc = self.scene()
            if hasattr(sc, "delete_selected_nodes"):
                sc.delete_selected_nodes()
                e.accept()
                return
        if (
            e.key() == QtCore.Qt.Key_C
            and not (e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier | QtCore.Qt.AltModifier))
            and _has_selected_nodes()
        ):
            sc = self.scene()
            if sc and hasattr(sc, "create_comment_group_from_selection"):
                sc.create_comment_group_from_selection()
                e.accept()
                return
        if e.matches(QtGui.QKeySequence.Copy):
            sc = self.scene()
            if (
                sc
                and hasattr(sc, "copy_selection_to_clipboard")
                and not _text_widget_wants_copy()
                and sc.copy_selection_to_clipboard()
            ):
                e.accept()
                return
        if e.matches(QtGui.QKeySequence.Paste):
            sc = self.scene()
            if (
                sc
                and hasattr(sc, "paste_from_clipboard")
                and not _text_widget_wants_paste()
                and sc.paste_from_clipboard()
            ):
                e.accept()
                return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.MiddleButton:
            self._mm_dragging = True
            self._mm_last_pos = e.pos()
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept(); return

        if e.button() == QtCore.Qt.RightButton:
            self._rc_started_over_llm = self._is_over_llm_view(e.pos())
            if self._rc_started_over_llm:
                super().mousePressEvent(e)
                return
            self._rc_dragging = True
            self._rc_press_pos = e.pos()
            self._rc_start_transform = QtGui.QTransform(self.transform())
            self._rc_press_scene_pt = self.mapToScene(self._rc_press_pos)
            self._rc_did_zoom = False
            e.accept(); return

        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._mm_dragging and self._mm_last_pos is not None:
            # Cancel if MMB is no longer held (prevents "stuck pan")
            if not (e.buttons() & QtCore.Qt.MiddleButton):
                self._mm_dragging = False
                self._mm_last_pos = None
                self.viewport().setCursor(QtCore.Qt.ArrowCursor)
                # fall through to default handling
            else:
                delta = e.pos() - self._mm_last_pos
                self._mm_last_pos = e.pos()
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
                e.accept()
                return

        if self._rc_dragging and self._rc_press_pos is not None:
            # *** New: only zoom while RMB is actually pressed ***
            if not (e.buttons() & QtCore.Qt.RightButton):
                self._rc_dragging = False
                self._rc_press_pos = None
                # don’t treat as context tap; this was a drag that ended elsewhere
                e.accept()
                return

            dx = e.pos().x() - self._rc_press_pos.x()
            dy = e.pos().y() - self._rc_press_pos.y()
            distance = dy - dx
            exponent = abs(distance) / self._drag_divisor
            base = self._zoom_multiplier
            factor = base ** (-exponent) if distance > 0 else base ** (exponent)
            start_sx = float(self._rc_start_transform.m11()) or 1.0
            factor = self._clamp_factor_from(start_sx, factor)
            p = self._rc_press_scene_pt
            T = QtGui.QTransform(self._rc_start_transform)
            T.translate(p.x(), p.y())
            T.scale(factor, factor)
            T.translate(-p.x(), -p.y())
            self.setTransform(T)
            if abs(factor - 1.0) > 1e-3:
                self._rc_did_zoom = True
            e.accept()
            return

        super().mouseMoveEvent(e)


    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.MiddleButton:
            self._mm_dragging = False
            self._mm_last_pos = None
            self.viewport().setCursor(QtCore.Qt.ArrowCursor)
            e.accept(); return

        if e.button() == QtCore.Qt.RightButton:
            if self._rc_started_over_llm:
                self._rc_started_over_llm = False
                super().mouseReleaseEvent(e); return

            # If a zoom occurred, swallow the release (no context)
            if self._rc_did_zoom:
                self._rc_dragging = False
                self._rc_press_pos = None
                self._rc_did_zoom = False
                e.accept(); return

            # Otherwise, it might be a context click if we didn’t move much
            is_context = False
            if self._rc_press_pos is not None:
                dx = e.pos().x() - self._rc_press_pos.x()
                dy = e.pos().y() - self._rc_press_pos.y()
                if math.hypot(dx, dy) <= self._context_click_thresh:
                    is_context = True

            self._rc_dragging = False
            self._rc_press_pos = None
            self._rc_did_zoom = False

            if is_context and not self._is_over_llm_view(e.pos()):
                sp = self.mapToScene(e.pos())
                sc = self.scene()
                if hasattr(sc, "show_create_dialog_at"):
                    sc.show_create_dialog_at(sp)
                e.accept(); return

            e.accept(); return

        super().mouseReleaseEvent(e)


    def wheelEvent(self, e: QtGui.QWheelEvent):
        factor = 1.15 if e.angleDelta().y() > 0 else 1/1.15
        try:
            vp = e.position()
            vp = QtCore.QPoint(int(vp.x()), int(vp.y()))
        except AttributeError:
            vp = e.pos()
        start_sx = self._current_scale_x()
        factor = self._clamp_factor_from(start_sx, factor)
        self._zoom_at(vp, factor)
