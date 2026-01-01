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
        self._mode_3d = False
        self._orbit_dragging = False
        self._pan_dragging = False
        self._dolly_dragging = False
        self._orbit_last_pos = None
        self._pan_last_pos = None
        self._dolly_last_pos = None
        self._cam_yaw = 0.45
        self._cam_pitch = -0.35
        self._cam_dist = 2400.0
        self._cam_focal = 1200.0
        self._cam_pan = QtCore.QPointF(0.0, 0.0)
        self._cam_target = QtCore.QPointF(0.0, 0.0)
        self._depth_scale = 160.0
        self._near_plane = 0.1
        self._min_cam_dist = 200.0
        self._max_cam_dist = 20000.0
        self._orbit_sensitivity = 0.005
        self._drag_mode_prev = None
        self._pre_3d_transform = None

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

    def drawForeground(self, p: QtGui.QPainter, rect: QtCore.QRectF):
        super().drawForeground(p, rect)
        if not self._mode_3d:
            return
        p.save()
        try:
            p.resetTransform()
        except Exception:
            p.setTransform(QtGui.QTransform())
        self._draw_axis_gizmo(p)
        p.restore()

    def _draw_axis_gizmo(self, p: QtGui.QPainter):
        vp = self.viewport().rect()
        if vp.isNull():
            return
        size = 58.0
        margin = 14.0
        origin = QtCore.QPointF(vp.right() - margin - size * 0.5, vp.top() + margin + size * 0.5)
        radius = size * 0.45

        axes = (
            ("X", QtGui.QColor("#ef4444"), (1.0, 0.0, 0.0)),
            ("Y", QtGui.QColor("#22c55e"), (0.0, 1.0, 0.0)),
            ("Z", QtGui.QColor("#3b82f6"), (0.0, 0.0, 1.0)),
        )
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        for label, color, vec in axes:
            x2, y2, z2 = self._rotate_vec(vec[0], vec[1], vec[2])
            v2 = QtCore.QPointF(x2, -y2)
            length = math.hypot(v2.x(), v2.y())
            if length <= 1e-6:
                continue
            v2 = QtCore.QPointF(v2.x() / length * radius, v2.y() / length * radius)
            pen = QtGui.QPen(color, 2.2)
            if z2 < 0.0:
                pen.setStyle(QtCore.Qt.DotLine)
                color = QtGui.QColor(color)
                color.setAlpha(130)
                pen.setColor(color)
            p.setPen(pen)
            p.drawLine(origin, origin + v2)
            p.setPen(QtGui.QPen(color))
            p.drawText(origin + v2 + QtCore.QPointF(4.0, -2.0), label)
        p.setPen(QtGui.QPen(QtGui.QColor("#e2e8f0")))
        p.setBrush(QtGui.QBrush(QtGui.QColor("#0f172a")))
        p.drawEllipse(origin, 3.2, 3.2)

    def set_3d_mode(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self._mode_3d:
            return
        self._mode_3d = enabled
        sc = self.scene()
        if enabled:
            self._orbit_dragging = False
            self._pan_dragging = False
            self._dolly_dragging = False
            self._mm_dragging = False
            self._rc_dragging = False
            self._rc_started_over_llm = False
            self._rc_press_pos = None
            self._rc_did_zoom = False
            self._drag_mode_prev = self.dragMode()
            self._pre_3d_transform = QtGui.QTransform(self.transform())
            self.resetTransform()
            try:
                self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
            except AttributeError:
                self.setDragMode(QtWidgets.QGraphicsView.DragMode.NoDrag)
            self.setInteractive(False)
            if sc is not None:
                sc._suppress_node_model_updates = True
                for cg in getattr(sc, "_comment_groups", []):
                    try:
                        cg.setVisible(False)
                    except Exception:
                        pass
            self._reset_3d_camera()
            self._apply_3d_projection()
        else:
            if sc is not None:
                for cg in getattr(sc, "_comment_groups", []):
                    try:
                        cg.setVisible(True)
                    except Exception:
                        pass
            if self._pre_3d_transform is not None:
                self.setTransform(self._pre_3d_transform)
                self._pre_3d_transform = None
            try:
                if self._drag_mode_prev is not None:
                    self.setDragMode(self._drag_mode_prev)
                else:
                    self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
            except AttributeError:
                if self._drag_mode_prev is not None:
                    self.setDragMode(self._drag_mode_prev)
                else:
                    self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
            self.setInteractive(True)
            if sc is not None:
                for item in getattr(sc, "_node_items", {}).values():
                    model = getattr(item, "model", None)
                    if model is None:
                        continue
                    try:
                        x, y = model.pos_xy
                        item.setPos(QtCore.QPointF(float(x), float(y)))
                    except Exception:
                        pass
                    try:
                        item.setTransform(QtGui.QTransform())
                    except Exception:
                        pass
                    try:
                        item.setZValue(1.0)
                    except Exception:
                        pass
                for edge in getattr(sc, "_edges", []):
                    try:
                        edge.updatePath()
                    except Exception:
                        pass
                if hasattr(sc, "_reframe_to_nodes"):
                    try:
                        sc._reframe_to_nodes(margin=8000.0)
                    except Exception:
                        pass
                sc._suppress_node_model_updates = False

    def _base_nodes_bbox(self):
        sc = self.scene()
        if sc is None:
            return None
        rect = None
        for item in getattr(sc, "_node_items", {}).values():
            model = getattr(item, "model", None)
            if model is None:
                continue
            try:
                x, y = model.pos_xy
                x = float(x)
                y = float(y)
            except Exception:
                try:
                    pos = item.pos()
                    x, y = float(pos.x()), float(pos.y())
                except Exception:
                    continue
            try:
                w = float(getattr(item, "width", 0.0))
                h = float(getattr(item, "height", 0.0))
            except Exception:
                w = h = 0.0
            r = QtCore.QRectF(x, y, w, h)
            rect = r if rect is None else rect.united(r)
        return rect

    def _reset_3d_camera(self):
        rect = self._base_nodes_bbox()
        if rect is None:
            self._cam_target = QtCore.QPointF(0.0, 0.0)
            extent = 1000.0
        else:
            self._cam_target = rect.center()
            extent = max(rect.width(), rect.height(), 1.0)
        self._cam_yaw = 0.45
        self._cam_pitch = -0.35
        self._cam_pan = QtCore.QPointF(0.0, 0.0)
        self._cam_dist = max(1200.0, extent * 1.6)
        self._cam_focal = max(800.0, extent * 0.9)
        self._depth_scale = max(60.0, extent * 0.08)

    def _depth_zvalue(self, z_cam: float) -> float:
        depth = max(self._near_plane, float(z_cam))
        return 1.0 + min(1.0, 1200.0 / depth)

    def _rotate_vec(self, x: float, y: float, z: float):
        cy = math.cos(self._cam_yaw)
        sy = math.sin(self._cam_yaw)
        cp = math.cos(self._cam_pitch)
        sp = math.sin(self._cam_pitch)
        x1 = x * cy + z * sy
        z1 = -x * sy + z * cy
        y1 = y
        y2 = y1 * cp - z1 * sp
        z2 = y1 * sp + z1 * cp
        x2 = x1
        return x2, y2, z2

    def _project_point(self, x: float, y: float, z: float):
        dx = x - self._cam_target.x()
        dy = y - self._cam_target.y()
        x0 = float(dx)
        y0 = float(-dy)
        z0 = float(z)
        x2, y2, z2 = self._rotate_vec(x0, y0, z0)
        z_cam = z2 + self._cam_dist
        if z_cam < self._near_plane:
            z_cam = self._near_plane
        scale = self._cam_focal / z_cam
        sx = x2 * scale
        sy = -y2 * scale
        return sx, sy, z_cam

    def _apply_3d_projection(self):
        if not self._mode_3d:
            return
        sc = self.scene()
        if sc is None:
            return
        try:
            center_vp = self.viewport().rect().center()
            anchor = self.mapToScene(center_vp)
        except Exception:
            anchor = QtCore.QPointF(0.0, 0.0)
        pan = self._cam_pan
        for item in getattr(sc, "_node_items", {}).values():
            model = getattr(item, "model", None)
            if model is None:
                continue
            try:
                base_x, base_y = model.pos_xy
                base_x = float(base_x)
                base_y = float(base_y)
            except Exception:
                try:
                    pos = item.pos()
                    base_x, base_y = float(pos.x()), float(pos.y())
                except Exception:
                    continue
            try:
                w = float(getattr(item, "width", 0.0))
                h = float(getattr(item, "height", 0.0))
            except Exception:
                w = h = 0.0
            try:
                depth = float(getattr(model, "pos_z", 0.0))
            except Exception:
                depth = 0.0
            depth_z = depth * self._depth_scale
            sx0, sy0, _ = self._project_point(base_x, base_y, depth_z)
            sx1, sy1, _ = self._project_point(base_x + 1.0, base_y, depth_z)
            sx2, sy2, _ = self._project_point(base_x, base_y + 1.0, depth_z)
            p0 = QtCore.QPointF(anchor.x() + sx0 + pan.x(), anchor.y() + sy0 + pan.y())
            p1 = QtCore.QPointF(anchor.x() + sx1 + pan.x(), anchor.y() + sy1 + pan.y())
            p2 = QtCore.QPointF(anchor.x() + sx2 + pan.x(), anchor.y() + sy2 + pan.y())
            vx = p1 - p0
            vy = p2 - p0
            transform = QtGui.QTransform(vx.x(), vx.y(), vy.x(), vy.y(), 0.0, 0.0)
            item.setTransform(transform)
            item.setPos(p0)
            cx = base_x + w / 2.0
            cy = base_y + h / 2.0
            _, _, z_cam = self._project_point(cx, cy, depth_z)
            try:
                item.setZValue(self._depth_zvalue(z_cam))
            except Exception:
                pass
        for edge in getattr(sc, "_edges", []):
            try:
                edge.updatePath()
            except Exception:
                pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._mode_3d:
            self._apply_3d_projection()

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        if self._mode_3d:
            if e.key() == QtCore.Qt.Key_R:
                self._reset_3d_camera()
                self._apply_3d_projection()
            e.accept()
            return
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

        if _resolved_text_widget() is not None:
            super().keyPressEvent(e)
            return

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
        if self._mode_3d:
            if e.button() == QtCore.Qt.LeftButton:
                self._orbit_dragging = True
                self._orbit_last_pos = e.pos()
                self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
                e.accept()
                return
            if e.button() == QtCore.Qt.MiddleButton:
                self._pan_dragging = True
                self._pan_last_pos = e.pos()
                self.viewport().setCursor(QtCore.Qt.OpenHandCursor)
                e.accept()
                return
            if e.button() == QtCore.Qt.RightButton:
                self._dolly_dragging = True
                self._dolly_last_pos = e.pos()
                self.viewport().setCursor(QtCore.Qt.SizeVerCursor)
                e.accept()
                return
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
        if self._mode_3d:
            if self._orbit_dragging and self._orbit_last_pos is not None:
                if not (e.buttons() & QtCore.Qt.LeftButton):
                    self._orbit_dragging = False
                    self._orbit_last_pos = None
                    self.viewport().setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return
                delta = e.pos() - self._orbit_last_pos
                self._orbit_last_pos = e.pos()
                self._cam_yaw -= float(delta.x()) * self._orbit_sensitivity
                self._cam_pitch -= float(delta.y()) * self._orbit_sensitivity
                self._cam_pitch = max(-1.45, min(1.45, self._cam_pitch))
                self._apply_3d_projection()
                e.accept()
                return
            if self._pan_dragging and self._pan_last_pos is not None:
                if not (e.buttons() & QtCore.Qt.MiddleButton):
                    self._pan_dragging = False
                    self._pan_last_pos = None
                    self.viewport().setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return
                delta = self.mapToScene(e.pos()) - self.mapToScene(self._pan_last_pos)
                self._pan_last_pos = e.pos()
                self._cam_pan = QtCore.QPointF(self._cam_pan.x() + delta.x(), self._cam_pan.y() + delta.y())
                self._apply_3d_projection()
                e.accept()
                return
            if self._dolly_dragging and self._dolly_last_pos is not None:
                if not (e.buttons() & QtCore.Qt.RightButton):
                    self._dolly_dragging = False
                    self._dolly_last_pos = None
                    self.viewport().setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return
                dy = float(self._dolly_last_pos.y() - e.pos().y())
                self._dolly_last_pos = e.pos()
                if dy != 0.0:
                    factor = 1.0 + (dy * 0.01)
                    if factor < 0.1:
                        factor = 0.1
                    self._cam_dist = max(self._min_cam_dist, min(self._max_cam_dist, self._cam_dist / factor))
                self._apply_3d_projection()
                e.accept()
                return
            e.accept()
            return
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
        if self._mode_3d:
            if e.button() == QtCore.Qt.LeftButton:
                self._orbit_dragging = False
                self._orbit_last_pos = None
            if e.button() == QtCore.Qt.MiddleButton:
                self._pan_dragging = False
                self._pan_last_pos = None
            if e.button() == QtCore.Qt.RightButton:
                self._dolly_dragging = False
                self._dolly_last_pos = None
            if not (self._orbit_dragging or self._pan_dragging or self._dolly_dragging):
                self.viewport().setCursor(QtCore.Qt.ArrowCursor)
            e.accept()
            return
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
        if self._mode_3d:
            delta = e.angleDelta().y()
            factor = 1.15 if delta > 0 else 1 / 1.15
            self._cam_dist = max(self._min_cam_dist, min(self._max_cam_dist, self._cam_dist / factor))
            self._apply_3d_projection()
            e.accept()
            return
        factor = 1.15 if e.angleDelta().y() > 0 else 1/1.15
        try:
            vp = e.position()
            vp = QtCore.QPoint(int(vp.x()), int(vp.y()))
        except AttributeError:
            vp = e.pos()
        start_sx = self._current_scale_x()
        factor = self._clamp_factor_from(start_sx, factor)
        self._zoom_at(vp, factor)
