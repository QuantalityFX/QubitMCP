"""View-only curve navigation and resizable timeline hosting."""
import math

from echograph.qt_compat import QtCore, QtGui, QtWidgets
from echograph.ui import hotkeys_config

CURVE_LABEL_GUTTER = 40


def curve_ticks(minimum, maximum, height):
    """Readable grid spacing, with bounded work even for very large ranges."""
    span = maximum-minimum
    if not math.isfinite(span) or span <= 0:
        return []
    raw = span/max(2, min(20, int(height)//28))
    power = 10.**math.floor(math.log10(raw))
    step = next(factor*power for factor in (1, 2, 5, 10) if factor*power >= raw)
    first = math.ceil(minimum/step)*step
    return [first+i*step for i in range(min(24, max(0, int((maximum-first)/step)+1)))]


class TimelineValueRangeBar(QtWidgets.QWidget):
    """Floating-point counterpart of the horizontal displayed-range bar."""
    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self._bounds = (-15., 15.)
        self._range = (-5., 5.)
        self._drag = None
        self.setFixedWidth(22)
        self.setMinimumHeight(90)
        self.setMouseTracking(True)
        self.setAccessibleName('Displayed curve value range')
        self.setToolTip('Drag either triangle to resize the value range. Drag the middle to pan vertically.')

    def sync(self, minimum, maximum):
        self._range = (minimum, maximum)
        if self._drag is None:
            span = maximum-minimum
            self._bounds = (minimum-span, maximum+span)
        self.update()

    def _value_y(self, value):
        low, high = self._bounds
        return 10.+(high-value)*max(1., self.height()-20.)/(high-low)

    def handle_positions(self):
        low, high = self._range
        return self._value_y(high), self._value_y(low)

    def _hit(self, y):
        top, bottom = self.handle_positions()
        if min(abs(y-top), abs(y-bottom)) <= 9:
            return 'maximum' if abs(y-top) <= abs(y-bottom) else 'minimum'
        return 'pan'

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QColor('#475569'))
        painter.setBrush(QtGui.QColor('#111827'))
        painter.drawRect(QtCore.QRectF(6, 5, 10, self.height()-10))
        top, bottom = self.handle_positions()
        painter.setBrush(QtGui.QColor('#475569'))
        painter.drawRect(QtCore.QRectF(6, top-5, 10, bottom-top+10))
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor('#e2e8f0'))
        for y, direction in ((top, 1), (bottom, -1)):
            painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(5, y-5*direction),
                QtCore.QPointF(11, y+5*direction), QtCore.QPointF(17, y-5*direction)]))
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        self._drag = (self._hit(event.position().y()), event.position().y(), self._range)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            self.setCursor(QtCore.Qt.SizeVerCursor if self._hit(event.position().y()) != 'pan'
                           else QtCore.Qt.OpenHandCursor)
            return
        mode, press, (low, high) = self._drag
        delta = -(event.position().y()-press)*(self._bounds[1]-self._bounds[0])/max(1., self.height()-20.)
        gap = max(1e-6, (high-low)*1e-5)
        if mode == 'minimum':
            low = min(high-gap, low+delta)
        elif mode == 'maximum':
            high = max(low+gap, high+delta)
        else:
            low, high = low+delta, high+delta
        self.view._timeline_set_curve_range(low, high)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._drag is not None:
            self.mouseMoveEvent(event)
            self._drag = None
            self.sync(*self._range)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TimelinePanel(QtWidgets.QFrame):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self._resize_drag = None
        self._resize_hover = False
        self.setMouseTracking(True)
        # Unhandled panel gestures must never reach the 3D viewport underneath.
        self.setAttribute(QtCore.Qt.WA_NoMousePropagation, True)

    def paintEvent(self, event):
        super().paintEvent(event)
        if getattr(self.view, '_timeline_detached', False):
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        active = self._resize_hover or self._resize_drag is not None
        painter.setBrush(QtGui.QColor('#3b495d' if active else '#293646'))
        painter.drawRect(QtCore.QRectF(0, 0, self.width(), 5))
        painter.setBrush(QtGui.QColor('#e2e8f0' if active else '#94a3b8'))
        for offset in (-12, -6, 0, 6, 12):
            painter.drawEllipse(QtCore.QPointF(self.width()*.5+offset, 2.5), 1., 1.)
        painter.end()

    def mousePressEvent(self, event):
        if (event.button() == QtCore.Qt.LeftButton and event.position().y() <= 8
                and not getattr(self.view, '_timeline_detached', False)):
            self._resize_drag = (event.globalPosition().y(), self.height())
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_drag is not None:
            press, height = self._resize_drag
            self.view._timeline_resize_panel(height+press-event.globalPosition().y())
            event.accept()
            return
        hover = event.position().y() <= 8 and not getattr(self.view, '_timeline_detached', False)
        if hover != self._resize_hover:
            self._resize_hover = hover
            self.update()
        self.setCursor(QtCore.Qt.SizeVerCursor if hover else QtCore.Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._resize_hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._resize_drag is not None:
            self.mouseMoveEvent(event)
            self._resize_drag = None
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            if self.view._timeline_delete_selected_keys():
                event.accept()
                return
        super().keyPressEvent(event)


class TimelineWindow(QtWidgets.QDialog):
    def __init__(self, view):
        super().__init__(view, QtCore.Qt.Window)
        self.view = view
        self.setWindowTitle('Animation Timeline')
        self.setSizeGripEnabled(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self.view, '_timeline_detached', False):
            self.view._layout_timeline_panel()

    def closeEvent(self, event):
        self.view._timeline_set_detached(False)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Escape, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            event.ignore()
            return
        super().keyPressEvent(event)


class TimelineGraphNavigationMixin:
    def _timeline_set_curve_range(self, minimum, maximum):
        minimum, maximum = float(minimum), float(maximum)
        if not all(math.isfinite(v) for v in (minimum, maximum, maximum-minimum)) or maximum-minimum < 1e-6:
            return
        self._timeline_curve_min, self._timeline_curve_max = minimum, maximum
        bar = getattr(self, '_timeline_value_range_bar', None)
        if bar is not None:
            bar.sync(minimum, maximum)
        canvas = getattr(self, '_timeline_curves_canvas', None)
        if canvas is not None:
            canvas.update()

    def _timeline_fit_curve_values(self):
        if self._timeline_is_composition_mode():
            return
        self._timeline_set_curves_mode(True)
        values = []
        with self._timeline_curve_read_scope():
            for frame, entry in (getattr(self, '_timeline_keys', {}) or {}).items():
                for axis in self._timeline_axes_for_entry(entry):
                    if not self._timeline_axis_is_visible(axis):
                        continue
                    value = self._timeline_axis_value_for_entry(entry, axis)
                    if value is None or not math.isfinite(value):
                        continue
                    values.append(value)
                    handles = self._timeline_axis_handles_for_key(axis, frame, create=False)
                    if handles and handles.get('mode') != 'straight':
                        for side in ('in', 'out'):
                            vector = handles.get(side)
                            if vector and math.isfinite(vector[1]):
                                values.append(value+vector[1])
        if values:
            low, high = min(values), max(values)
            pad = max(1., (high-low)*.1)
            self._timeline_set_curve_range(low-pad, high+pad)

    def _timeline_frame_selected_curve_points(self):
        keys = getattr(self, '_timeline_keys', {}) or {}
        points = []
        selected = getattr(self, '_timeline_curve_selected', set()) or set()
        candidates = selected if selected else (
            (axis,frame) for frame,entry in keys.items() for axis in self._timeline_axes_for_entry(entry))
        for axis, frame in candidates:
            entry = keys.get(frame)
            if entry is None or not self._timeline_axis_is_visible(axis):
                continue
            value = self._timeline_axis_value_for_entry(entry, axis)
            if value is not None and math.isfinite(value):
                points.append((frame, value))
        if not points:
            return
        low, high = min(value for _, value in points), max(value for _, value in points)
        pad = max(1., (high-low)*.1)
        self._timeline_set_curve_range(low-pad, high+pad)
        start, end = min(frame for frame, _ in points), max(frame for frame, _ in points)
        padding = max(5, math.ceil((end-start)*.1))
        self._timeline_on_display_range_changed(start-padding, end+padding)
        self._timeline_range_save_to_disk()

    def _handle_timeline_graph_key(self, event, target=None):
        if event.type() not in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride):
            return False
        canvas = getattr(self, '_timeline_curves_canvas', None)
        panel = getattr(self, '_timeline_panel', None)
        if canvas is None or not canvas.isVisible() or panel is None:
            return False
        target = target if isinstance(target, QtWidgets.QWidget) else QtWidgets.QApplication.focusWidget()
        if target is self:
            target = QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
        editable = (QtWidgets.QLineEdit, QtWidgets.QTextEdit,
                    QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox)
        focus = QtWidgets.QApplication.focusWidget()
        if (target is None or not (target is panel or panel.isAncestorOf(target))
                or isinstance(target, editable)
                or (isinstance(focus, editable) and target.isAncestorOf(focus))):
            return False
        for action,callback in (
                ('timeline_graph_frame_selected',self._timeline_frame_selected_curve_points),
                ('timeline_join_ik_ranges',self._timeline_join_selected_ik_ranges)):
            held, key, mods = hotkeys_config.chord_binding(action)
            if held or event.key() != key or (event.modifiers() & hotkeys_config._MOD_MASK) != mods:
                continue
            if event.type() == QtCore.QEvent.KeyPress and not event.isAutoRepeat():
                callback()
            event.accept()
            return True
        return False

    def _timeline_scale_graph_view(self, origin, dx, dy):
        if abs(dy) > 2 or origin.get('vertical_changed'):
            low, high = origin['values']
            center = (low+high)*.5
            span = max(1e-5, min(1e12, (high-low)*math.exp(max(-12., min(12., dy/180.)))))
            self._timeline_set_curve_range(center-span*.5, center+span*.5)
            origin['vertical_changed'] = True
        if abs(dx) > 2 or origin.get('horizontal_changed'):
            start, end = origin['frames']
            minimum = int(getattr(self, '_timeline_in_frame', 0) or 0)
            maximum = max(minimum, self._timeline_end_frame_value())
            span = min(maximum-minimum, max(1, round((end-start)*math.exp(max(-12., min(12., -dx/180.))))))
            first = max(minimum, min(maximum-span, round((start+end-span)*.5)))
            self._timeline_on_display_range_changed(first, first+span)
            origin['horizontal_changed'] = True

    def _timeline_pan_graph_view(self, origin, dx, dy):
        low, high = origin['values']
        offset = dy*(high-low)/origin['height']
        self._timeline_set_curve_range(low+offset, high+offset)
        start, end = origin['frames']
        minimum = int(getattr(self, '_timeline_in_frame', 0) or 0)
        maximum = max(minimum, self._timeline_end_frame_value())
        span = end-start
        first = max(minimum, min(maximum-span, start-round(dx/origin['pixels_per_frame'])))
        if first != self._timeline_view_start:
            self._timeline_on_display_range_changed(first, first+span)
            origin['horizontal_changed'] = True

    def _timeline_sync_graph_controls(self):
        curves = not self._timeline_is_composition_mode() and bool(getattr(self, '_timeline_curves_mode', False))
        slider_row = getattr(self, '_timeline_frame_slider_row', None)
        if slider_row is not None:
            left, right = (CURVE_LABEL_GUTTER, 8) if curves else (0, 0)
            margins = slider_row.contentsMargins()
            if (margins.left(), margins.right()) != (left, right):
                slider_row.setContentsMargins(left, 0, right, 0)
                slider_row.activate()
                self._timeline_update_tick_labels()
        bar = getattr(self, '_timeline_value_range_bar', None)
        if bar is not None:
            bar.setVisible(curves)
            bar.sync(self._timeline_curve_min, self._timeline_curve_max)
        button = getattr(self, '_timeline_curve_fit_btn', None)
        if button is not None:
            button.setEnabled(not self._timeline_is_composition_mode())
        canvas = getattr(self, '_timeline_curves_canvas', None)
        if canvas is not None:
            pan = hotkeys_config.keyseq('timeline_graph_pan', 'Alt+MiddleClick')
            scale = hotkeys_config.keyseq('timeline_graph_scale', 'RightClick')
            frame = hotkeys_config.keyseq('timeline_graph_frame_selected', 'F')
            join = hotkeys_config.keyseq('timeline_join_ik_ranges', 'Ctrl+J')
            canvas.setToolTip(f'{pan}: drag to pan. {scale}: drag down/up to expand/contract values; '
                              f'right/left to contract/expand frames (Alt may also be held). '
                              f'{frame}: frame selected points, or all curve keys when none are selected. '
                              f'{join}: join selected End/Start IK ranges, keeping both keys. '
                              'Right-click without dragging for key actions.')

    def _timeline_resize_panel(self, height):
        panel = self._timeline_panel
        controls = getattr(self, '_controls', None)
        controls_h = getattr(self, '_controls_h', 44) if controls is not None and controls.isVisible() else 0
        self._timeline_h = max(panel.minimumSizeHint().height(),
                               min(round(height), self.height()-controls_h))
        self._layout_timeline_panel()
        self.update()

    def _timeline_set_detached(self, detached):
        detached = bool(detached)
        if detached == bool(getattr(self, '_timeline_detached', False)):
            return
        panel = self._timeline_panel
        window = getattr(self, '_timeline_window', None)
        if detached:
            if window is None:
                window = self._timeline_window = TimelineWindow(self)
            self._timeline_detached = True
            size = panel.size()
            window.layout().addWidget(panel)
            window.resize(size.width(), max(420, size.height()))
            for button in panel.findChildren(QtWidgets.QPushButton):
                button.setAutoDefault(False)
            panel.show()
            window.show()
        else:
            self._timeline_detached = False
            window.layout().removeWidget(panel)
            panel.setParent(self)
            panel.setVisible(bool(getattr(self, '_timeline_enabled', False)))
            window.hide()
        button = getattr(self, '_timeline_detach_btn', None)
        if button is not None:
            blocker = QtCore.QSignalBlocker(button)
            button.setChecked(detached)
            del blocker
            button.setToolTip('Dock timeline' if detached else 'Detach timeline')
            button.setAccessibleName(button.toolTip())
        self._layout_timeline_panel()
        self.update()
