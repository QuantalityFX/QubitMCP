"""Shared frame-range navigation for viewport timelines and the sequencer."""
from pathlib import Path

from echograph.qt_compat import QtCore, QtGui, QtWidgets


def timeline_spinbox_button_style():
    icons = Path(__file__).resolve().parents[2]/'icons'
    rules = []
    for widget in ('QSpinBox', 'QDoubleSpinBox'):
        for direction, position in (('up', 'top'), ('down', 'bottom')):
            button = f'#GLTimelinePanel {widget}::{direction}-button'
            rules.append(f'{button} {{ subcontrol-origin:border; subcontrol-position:{position} right; '
                         'width:13px; background:#465366; border:1px solid #526075; }')
            rules.append(f'{button}:hover {{ background:#59697f; }}')
            rules.append(f'{button}:pressed {{ background:#334155; }}')
            arrow = (icons/f'timeline_spin_{direction}.svg').as_posix().replace('"', '\\"')
            rules.append(f'#GLTimelinePanel {widget}::{direction}-arrow '
                         f'{{ image:url("{arrow}"); width:7px; height:4px; }}')
    return ''.join(rules)


class TimelineSpeedSpinBox(QtWidgets.QDoubleSpinBox):
    def textFromValue(self, value):
        text = super().textFromValue(value)
        decimal = self.locale().decimalPoint()
        return text.rstrip('0').rstrip(decimal) if decimal in text else text


class TimelineRangeBar(QtWidgets.QWidget):
    rangeChanged = QtCore.Signal(int, int)
    editingFinished = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bounds = (0, 240)
        self._range = (0, 240)
        self._drag = None
        self.setMinimumSize(80, 22)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setToolTip('Drag either triangle to change the displayed range. Drag the middle to scroll.')
        self.setAccessibleName('Displayed timeline range')

    def sync(self, minimum, maximum, start, end):
        state = ((minimum, maximum), (start, end))
        if state != (self._bounds, self._range):
            self._bounds, self._range = state
            self.update()

    def _frame_x(self, frame):
        minimum, maximum = self._bounds
        return 10. + (frame-minimum)*max(1., self.width()-20.)/max(1, maximum-minimum)

    def handle_positions(self):
        if self._bounds[0] == self._bounds[1]:
            return (10., self.width()-10.)
        left, right = (self._frame_x(frame) for frame in self._range)
        # Keep both handles usable even when only one or two frames are shown.
        if right-left < 24:
            left = max(10., min((left+right)/2-12, self.width()-34.))
            right = left+24
        return left, right

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        center_y = self.height()/2
        rect = QtCore.QRectF(5.5, center_y-5, self.width()-11, 10)
        painter.setPen(QtGui.QColor('#475569'))
        painter.setBrush(QtGui.QColor('#111827'))
        painter.drawRect(rect)
        left, right = self.handle_positions()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor('#475569'))
        painter.drawRect(QtCore.QRectF(left-5, center_y-5, right-left+10, 10))
        painter.setBrush(QtGui.QColor('#e2e8f0'))
        for x, direction in ((left, 1), (right, -1)):
            painter.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(x-5*direction, 6),
                QtCore.QPointF(x+5*direction, self.height()/2),
                QtCore.QPointF(x-5*direction, self.height()-6)]))
        painter.end()

    def _hit(self, x):
        left, right = self.handle_positions()
        if abs(x-left) <= 9 or abs(x-right) <= 9:
            return 'start' if abs(x-left) <= abs(x-right) else 'end'
        return 'pan'

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus(QtCore.Qt.MouseFocusReason)
        self._drag = (self._hit(event.position().x()), event.position().x(), self._range)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            self.setCursor(QtCore.Qt.SizeHorCursor if self._hit(event.position().x()) != 'pan'
                           else QtCore.Qt.ArrowCursor)
            return
        mode, press_x, (start, end) = self._drag
        minimum, maximum = self._bounds
        delta = round((event.position().x()-press_x)*(maximum-minimum)/max(1, self.width()-20))
        gap = min(1, maximum-minimum)
        if mode == 'start':
            start = max(minimum, min(end-gap, start+delta))
        elif mode == 'end':
            end = min(maximum, max(start+gap, end+delta))
        else:
            delta = max(minimum-start, min(maximum-end, delta))
            start, end = start+delta, end+delta
        if (start, end) != self._range:
            self.rangeChanged.emit(start, end)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._drag is not None:
            self.mouseMoveEvent(event)
            self._drag = None
            self.editingFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() not in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
            return super().keyPressEvent(event)
        start, end = self._range
        delta = -1 if event.key() == QtCore.Qt.Key_Left else 1
        delta = max(self._bounds[0]-start, min(self._bounds[1]-end, delta))
        self.rangeChanged.emit(start+delta, end+delta)
        self.editingFinished.emit()
        event.accept()


class TimelineRangeControls(QtWidgets.QWidget):
    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.start_spin = self._spin('Full timeline start frame', light=True)
        self.view_start_spin = self._spin('First displayed frame')
        self.bar = TimelineRangeBar(self)
        self.view_end_spin = self._spin('Last displayed frame')
        self.end_spin = self._spin('Full timeline end frame', light=True)
        for widget in (self.start_spin, self.view_start_spin, self.bar, self.view_end_spin, self.end_spin):
            row.addWidget(widget, 1 if widget is self.bar else 0)
        self.start_spin.valueChanged.connect(view._timeline_on_start_frame_spin_changed)
        self.end_spin.valueChanged.connect(view._timeline_on_end_frame_spin_changed)
        self.view_start_spin.valueChanged.connect(self._start_changed)
        self.view_end_spin.valueChanged.connect(self._end_changed)
        self.bar.rangeChanged.connect(view._timeline_on_display_range_changed)
        self.bar.editingFinished.connect(view._timeline_range_save_to_disk)

    def _spin(self, tooltip, light=False):
        spin = QtWidgets.QSpinBox(self)
        spin.setRange(0, 100000)
        spin.setFixedWidth(62)
        spin.setKeyboardTracking(False)
        spin.setToolTip(tooltip)
        spin.setAccessibleName(tooltip)
        spin.setStyleSheet('QSpinBox { background:%s; color:#e2e8f0; border:1px solid #475569; '
                           'border-radius:3px; padding:1px 4px; }' % ('#364152' if light else '#1f2937'))
        return spin

    def _start_changed(self, value):
        self.view._timeline_on_display_range_changed(value, self.view_end_spin.value())
        self.view._timeline_range_save_to_disk()

    def _end_changed(self, value):
        self.view._timeline_on_display_range_changed(self.view_start_spin.value(), value)
        self.view._timeline_range_save_to_disk()

    def sync(self, minimum, maximum, start, end):
        gap = min(1, maximum-minimum)
        for spin, bounds, value in (
                (self.start_spin, (0, maximum), minimum),
                (self.view_start_spin, (minimum, end-gap), start),
                (self.view_end_spin, (start+gap, maximum), end),
                (self.end_spin, (minimum, 100000), maximum)):
            blocked = spin.blockSignals(True)
            spin.setRange(*bounds)
            spin.setValue(value)
            spin.blockSignals(blocked)
        self.bar.sync(minimum, maximum, start, end)
