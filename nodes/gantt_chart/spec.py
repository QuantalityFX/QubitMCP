from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from pathlib import Path

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

_SCHEDULE_PARAM = "schedule_data"
_VIEW_MONTH_PARAM = "view_month"
_VIEW_START_PARAM = "view_start_date"
_VISIBLE_DAY_COUNT = 31
_DAY_SCROLL_UNITS_PER_DAY = 12
_DAY_SCROLL_CENTER = 24000
_DAY_SCROLL_RANGE = 48000
_TODAY_MARKER_Y_OFFSET = -8


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return entry.get("value", "") or ""
    return ""


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            model.params = params
            return
    params.append({"name": name, "value": default})
    model.params = params


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    hidden_entry = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": store_key, "value": ""}
        params.append(hidden_entry)
    hidden = set()
    for part in str(hidden_entry.get("value", "")).split(","):
        token = part.strip().lower()
        if token:
            hidden.add(token)
    for name in names or []:
        token = (name or "").strip().lower()
        if token:
            hidden.add(token)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def build_ports(node_item) -> None:
    today = date.today()
    model = getattr(node_item, "model", None)
    year, month = _parse_view_month(
        _param_value(model, _VIEW_MONTH_PARAM),
        fallback_year=today.year,
        fallback_month=today.month,
    )
    view_start = _parse_view_start(
        _param_value(model, _VIEW_START_PARAM),
        fallback=date(year, month, 1),
    )
    _ensure_param(node_item, _SCHEDULE_PARAM, "{}")
    _ensure_param(node_item, _VIEW_MONTH_PARAM, f"{view_start.year:04d}-{view_start.month:02d}")
    _ensure_param(node_item, _VIEW_START_PARAM, view_start.isoformat())
    _ensure_hidden_params(model, [_SCHEDULE_PARAM, _VIEW_MONTH_PARAM, _VIEW_START_PARAM])


def _set_param_value(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    found = False
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            entry["value"] = value
            found = True
            break
    if not found:
        params.append({"name": name, "value": value})
    model.params = params
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None and hasattr(scene, "set_node_params"):
        try:
            scene.set_node_params(model.name, params, rebuild=False, emit=notify_scene)
        except Exception:
            pass


def _hidden_params(model) -> set[str]:
    raw = _param_value(model, "__ui_hidden_params")
    hidden = set()
    for part in str(raw).split(","):
        token = part.strip().lower()
        if token:
            hidden.add(token)
    return hidden


def _parse_view_month(raw: str, *, fallback_year: int, fallback_month: int) -> tuple[int, int]:
    text = str(raw or "").strip()
    if text:
        parts = text.split("-", 1)
        if len(parts) == 2:
            try:
                year = int(parts[0])
                month = int(parts[1])
                if 1 <= month <= 12:
                    return year, month
            except Exception:
                pass
    return fallback_year, fallback_month


def _parse_view_start(raw: str, *, fallback: date) -> date:
    text = str(raw or "").strip()
    if text:
        try:
            return date.fromisoformat(text)
        except Exception:
            pass
    return fallback


def _scroll_value_for_day_offset(day_offset: int) -> int:
    value = _DAY_SCROLL_CENTER + (int(day_offset) * _DAY_SCROLL_UNITS_PER_DAY)
    return max(0, min(_DAY_SCROLL_RANGE, value))


def _day_offset_for_scroll_value(value: int) -> int:
    delta = int(value) - _DAY_SCROLL_CENTER
    if delta >= 0:
        return int((delta + (_DAY_SCROLL_UNITS_PER_DAY // 2)) // _DAY_SCROLL_UNITS_PER_DAY)
    return -int(((-delta) + (_DAY_SCROLL_UNITS_PER_DAY // 2)) // _DAY_SCROLL_UNITS_PER_DAY)


def _days_in_month(year: int, month: int) -> int:
    return int(calendar.monthrange(int(year), int(month))[1])


def _coerce_iso_date(raw, *, default_year: int, default_month: int) -> str | None:
    if isinstance(raw, int):
        day = int(raw)
        if 1 <= day <= _days_in_month(default_year, default_month):
            return date(default_year, default_month, day).isoformat()
        return None
    text = str(raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        day = int(text)
        if 1 <= day <= _days_in_month(default_year, default_month):
            return date(default_year, default_month, day).isoformat()
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except Exception:
        return None


def _read_assignments(node_item, *, default_year: int, default_month: int) -> dict[str, str]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {}
    raw = _param_value(model, _SCHEDULE_PARAM).strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    clean: dict[str, str] = {}
    for name, value in data.items():
        task = str(name or "").strip()
        if not task:
            continue
        iso = _coerce_iso_date(value, default_year=default_year, default_month=default_month)
        if iso:
            clean[task] = iso
    return clean


def _write_assignments(node_item, mapping: dict[str, str], *, notify_scene: bool = True) -> None:
    clean = {}
    for name, value in (mapping or {}).items():
        task = str(name or "").strip()
        if not task:
            continue
        iso = _coerce_iso_date(value, default_year=date.today().year, default_month=date.today().month)
        if iso:
            clean[task] = iso
    _set_param_value(
        node_item,
        _SCHEDULE_PARAM,
        json.dumps(clean, sort_keys=True, separators=(",", ":")),
        notify_scene=notify_scene,
    )


def _resolve_note_source(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return None, None
    try:
        in_edges = list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            in_edges = list(scene._in_edges(node_item))
        except Exception:
            in_edges = []
    if not in_edges:
        return None, None
    chosen = None
    for edge in in_edges:
        port_name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
        if (port_name or "").strip().lower() in {"note", "source"}:
            chosen = edge
            break
    if chosen is None:
        chosen = in_edges[0]
    src_item = getattr(chosen, "src", None)
    src_model = getattr(src_item, "model", None)
    if src_model is None:
        return None, None
    if (getattr(src_model, "kind", "") or "").strip().lower() != "note":
        return src_item, None
    return src_item, src_model


def _note_task_names(model) -> list[str]:
    if model is None:
        return []
    hidden = _hidden_params(model)
    names = []
    seen = set()
    for entry in (getattr(model, "params", None) or []):
        name = (entry.get("name") or "").strip()
        key = name.lower()
        if not name or key == "__ui_hidden_params" or key in hidden or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _set_section_resize_mode(header, section: int, mode) -> None:
    try:
        header.setSectionResizeMode(section, mode)
    except AttributeError:
        header.setResizeMode(section, mode)


_TODAY_ICON_CACHE = None


def _today_icon() -> QtGui.QIcon | None:
    global _TODAY_ICON_CACHE
    if _TODAY_ICON_CACHE is not None:
        return _TODAY_ICON_CACHE
    try:
        icon_path = Path(__file__).resolve().parents[2] / "icons" / "KeyframeHandle_Icon.png"
        pm = QtGui.QPixmap(str(icon_path))
        if pm.isNull():
            _TODAY_ICON_CACHE = None
            return None
        tinted = QtGui.QPixmap(pm.size())
        tinted.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(tinted)
        painter.drawPixmap(0, 0, pm)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QtGui.QColor("#22c55e"))
        painter.end()
        _TODAY_ICON_CACHE = QtGui.QIcon(tinted)
        return _TODAY_ICON_CACHE
    except Exception:
        _TODAY_ICON_CACHE = None
        return None


class _GanttCalendarTable(QtWidgets.QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._today_column: int | None = None

    def set_today_column(self, column: int | None) -> None:
        self._today_column = None if column is None else int(column)
        try:
            self.viewport().update()
        except Exception:
            pass

    def paintEvent(self, event):
        super().paintEvent(event)
        col = self._today_column
        if col is None or col < 0 or col >= self.columnCount():
            return
        try:
            x = int(self.columnViewportPosition(col))
        except Exception:
            return
        if x >= self.viewport().width():
            return
        painter = QtGui.QPainter(self.viewport())
        pen = QtGui.QPen(QtGui.QColor("#22c55e"), 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(x, 0, x, self.viewport().height())
        painter.end()


class _GanttTaskHeader(QtWidgets.QHeaderView):
    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Vertical, parent)
        self._selected_section: int | None = None
        self._dragging_section: int | None = None
        self._drop_indicator_y: int | None = None
        self.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

    def set_selected_section(self, section: int | None) -> None:
        new_value = None if section is None or int(section) < 0 else int(section)
        if new_value == self._selected_section:
            return
        self._selected_section = new_value
        try:
            self.viewport().update()
        except Exception:
            self.update()

    def _set_drop_indicator_y(self, y: int | None) -> None:
        new_value = None if y is None else int(y)
        if new_value == self._drop_indicator_y:
            return
        self._drop_indicator_y = new_value
        try:
            self.viewport().update()
        except Exception:
            self.update()

    def _indicator_y_for_pos(self, pos_y: int) -> int | None:
        count = int(self.count())
        if count <= 0:
            return None
        for visual_index in range(count):
            logical_index = int(self.logicalIndex(visual_index))
            if logical_index < 0:
                continue
            top = int(self.sectionPosition(logical_index))
            size = int(self.sectionSize(logical_index))
            if pos_y < (top + (size / 2.0)):
                return top
        last_logical = int(self.logicalIndex(count - 1))
        if last_logical < 0:
            return None
        return int(self.sectionPosition(last_logical) + self.sectionSize(last_logical))

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        painter.save()
        is_selected = int(logical_index) == self._selected_section
        painter.fillRect(rect, QtGui.QColor("#1d4f74") if is_selected else QtGui.QColor("#141c27"))
        pen = QtGui.QPen(QtGui.QColor("#223041"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        text = ""
        try:
            model = self.model()
            if model is not None:
                text = str(model.headerData(int(logical_index), self.orientation(), QtCore.Qt.DisplayRole) or "")
        except Exception:
            text = ""
        font = painter.font()
        font.setBold(bool(is_selected))
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#f8fafc") if is_selected else QtGui.QColor("#dbe4ee"))
        painter.drawText(rect.adjusted(8, 0, -6, 0), int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter), text)
        painter.restore()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_indicator_y is None:
            return
        painter = QtGui.QPainter(self.viewport())
        pen = QtGui.QPen(QtGui.QColor("#ffffff"), 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        y = max(0, min(int(self._drop_indicator_y), max(0, self.viewport().height() - 1)))
        painter.drawLine(0, y, self.viewport().width(), y)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            logical_index = int(self.logicalIndexAt(event.pos()))
            self._dragging_section = logical_index if logical_index >= 0 else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if bool(event.buttons() & QtCore.Qt.LeftButton) and self._dragging_section is not None:
            self._set_drop_indicator_y(self._indicator_y_for_pos(int(event.pos().y())))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._dragging_section = None
        self._set_drop_indicator_y(None)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._dragging_section is None:
            self._set_drop_indicator_y(None)


class _GanttMonthStrip(QtWidgets.QWidget):
    def __init__(self, table=None, parent=None):
        super().__init__(parent)
        self._table = table
        self._visible_dates: list[date] = []
        self._today = date.today()
        self.setFixedHeight(16)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background:transparent;")

    def set_dates(self, visible_dates: list[date], today_value: date) -> None:
        self._visible_dates = list(visible_dates or [])
        self._today = today_value
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._visible_dates or self._table is None:
            return
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        except Exception:
            pass
        font = painter.font()
        font.setBold(True)
        try:
            font.setPointSize(max(8, int(font.pointSize()) - 1))
        except Exception:
            pass
        painter.setFont(font)
        header_width = int(self._table.verticalHeader().width())
        for column, visible_date in enumerate(self._visible_dates):
            if visible_date.day != 1:
                continue
            try:
                x = int(header_width + self._table.columnViewportPosition(column) + 3)
            except Exception:
                continue
            if x >= self.width():
                continue
            painter.setPen(
                QtGui.QColor("#22c55e")
                if visible_date.year == self._today.year and visible_date.month == self._today.month
                else QtGui.QColor("#e5eef8")
            )
            rect = QtCore.QRect(x, 0, max(1, self.width() - x), self.height())
            painter.drawText(
                rect,
                int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                f"{calendar.month_name[visible_date.month]} {visible_date.year}",
            )
        painter.end()


def _reorder_note_params(node_item, ordered_names: list[str]) -> bool:
    src_item, src_model = _resolve_note_source(node_item)
    if src_model is None:
        return False
    params = list(getattr(src_model, "params", None) or [])
    hidden = _hidden_params(src_model)
    visible_entries = []
    other_entries = []
    for entry in params:
        name = (entry.get("name") or "").strip()
        key = name.lower()
        if name and key != "__ui_hidden_params" and key not in hidden:
            visible_entries.append(entry)
        else:
            other_entries.append(entry)
    if not visible_entries:
        return False

    by_name = {(entry.get("name") or "").strip(): entry for entry in visible_entries}
    reordered_visible = [by_name[name] for name in ordered_names if name in by_name]
    seen_ids = {id(entry) for entry in reordered_visible}
    for entry in visible_entries:
        if id(entry) not in seen_ids:
            reordered_visible.append(entry)

    new_params = reordered_visible + other_entries
    if new_params == params:
        return False
    src_model.params = new_params
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None and hasattr(scene, "set_node_params"):
        try:
            scene.set_node_params(src_model.name, new_params, rebuild=True, emit=True)
        except Exception:
            pass
    return True


class GanttChartWidget(QtWidgets.QFrame):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._sync_pending = False
        self._source_name = ""
        self._source_invalid = False
        self._task_names: list[str] = []
        self._assignments: dict[str, str] = {}
        self._selected_task: str | None = None
        self._ignore_header_move = False
        self._today = date.today()
        self._base_start_date = date(self._today.year, self._today.month, 1)
        self._visible_start_date = self._base_start_date
        self._visible_dates: list[date] = []
        self._day_scroll_sync = False

        self.setObjectName("GanttChartWidget")
        self.setStyleSheet(
            "QFrame#GanttChartWidget{background:#0f1216;border:1px solid #334155;border-radius:8px;}"
            "QLabel{color:#cbd5e1;}"
            "QTableWidget{background:#0f1216;color:#e2e8f0;border:1px solid #273244;border-radius:6px;"
            "gridline-color:#223041;selection-background-color:#16212b;selection-color:#e2e8f0;}"
            "QTableWidget::item:selected{background:#16212b;color:#e2e8f0;}"
            "QHeaderView::section{background:#141c27;color:#dbe4ee;border:1px solid #223041;padding:4px;font-weight:600;}"
            "QTableCornerButton::section{background:#141c27;border:1px solid #223041;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 6)
        status_row.setSpacing(10)

        self._source_label = QtWidgets.QLabel("Connect a Note node to this input.")
        self._source_label.setStyleSheet("QLabel{color:#93a4b8;font-weight:600;}")
        status_row.addWidget(self._source_label, 1)

        self._selection_label = QtWidgets.QLabel("Select a task, then click a day.")
        self._selection_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._selection_label.setStyleSheet("QLabel{color:#7dd3fc;}")
        status_row.addWidget(self._selection_label, 1)

        layout.addLayout(status_row)

        self._month_strip = _GanttMonthStrip(None, self)
        layout.addWidget(self._month_strip, 0)

        self._today_marker_strip = QtWidgets.QWidget(self)
        self._today_marker_strip.setFixedHeight(16)
        self._today_marker_strip.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._today_marker_strip.setStyleSheet("background:transparent;")
        self._today_marker_icon = QtWidgets.QLabel(self._today_marker_strip)
        self._today_marker_icon.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._today_marker_icon.setStyleSheet("background:transparent;")
        self._today_marker_icon.hide()
        self._today_marker_strip.setFixedHeight(0)
        self._today_marker_strip.hide()
        layout.addWidget(self._today_marker_strip, 0)

        self._table = _GanttCalendarTable(self)
        self._table.setVerticalHeader(_GanttTaskHeader(self._table))
        self._month_strip._table = self._table
        self._table.setColumnCount(0)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setFocusPolicy(QtCore.Qt.NoFocus)
        self._table.setWordWrap(False)
        self._table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._table.verticalHeader().setVisible(True)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(True)
        self._table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignCenter)
        self._table.horizontalHeader().setSectionsClickable(True)
        try:
            self._table.horizontalHeader().setHighlightSections(False)
        except Exception:
            pass
        self._table.verticalHeader().setSectionsClickable(True)
        try:
            self._table.verticalHeader().setHighlightSections(True)
        except Exception:
            pass
        try:
            self._table.verticalHeader().setSectionsMovable(True)
        except Exception:
            pass
        try:
            self._table.verticalHeader().sectionMoved.connect(self._on_task_section_moved)
        except Exception:
            pass

        try:
            fixed_mode = QtWidgets.QHeaderView.ResizeMode.Fixed
        except AttributeError:
            fixed_mode = QtWidgets.QHeaderView.Fixed
        try:
            _set_section_resize_mode(self._table.verticalHeader(), 0, fixed_mode)
        except Exception:
            pass
        try:
            self._table.verticalHeader().setDefaultSectionSize(26)
            self._table.verticalHeader().setMinimumWidth(180)
        except Exception:
            pass

        self._table.cellClicked.connect(self._on_cell_clicked)
        try:
            self._table.verticalHeader().sectionClicked.connect(self._on_task_header_clicked)
        except Exception:
            pass
        try:
            self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        except Exception:
            pass
        self._table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        layout.addWidget(self._table, 1)

        self._day_scroll = QtWidgets.QScrollBar(QtCore.Qt.Horizontal, self)
        self._day_scroll.setRange(0, _DAY_SCROLL_RANGE)
        self._day_scroll.setSingleStep(_DAY_SCROLL_UNITS_PER_DAY)
        self._day_scroll.setPageStep(7 * _DAY_SCROLL_UNITS_PER_DAY)
        self._day_scroll.setValue(_DAY_SCROLL_CENTER)
        self._day_scroll.setToolTip("Scroll through days")
        self._day_scroll.valueChanged.connect(self._on_day_scroll_changed)
        layout.addWidget(self._day_scroll, 0)

        self._ensure_scene()
        self._sync_from_source()
        QtCore.QTimer.singleShot(0, self._post_attach_sync)

    def sizeHint(self):
        return QtCore.QSize(1000, 320)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_today_marker()

    def _post_attach_sync(self):
        self._ensure_scene()
        self._schedule_sync()

    def _visible_day_count(self) -> int:
        return int(len(self._visible_dates) or _VISIBLE_DAY_COUNT)

    def _visible_date_for_day(self, day: int) -> date:
        return self._visible_start_date + timedelta(days=max(0, int(day) - 1))

    def _visible_column_for_assignment(self, task: str) -> int | None:
        raw = self._assignments.get(task, "")
        try:
            assigned = date.fromisoformat(str(raw))
        except Exception:
            return None
        delta = (assigned - self._visible_start_date).days
        if 0 <= delta < self._visible_day_count():
            return int(delta)
        return None

    def _sync_view_start_from_params(self, *, force: bool = False):
        model = getattr(self._node_item, "model", None)
        fallback = self._base_start_date
        raw_start = _param_value(model, _VIEW_START_PARAM)
        if raw_start.strip():
            visible_start = _parse_view_start(raw_start, fallback=fallback)
        else:
            year, month = _parse_view_month(
                _param_value(model, _VIEW_MONTH_PARAM),
                fallback_year=fallback.year,
                fallback_month=fallback.month,
            )
            visible_start = date(year, month, 1)
        if (not force) and visible_start == self._visible_start_date:
            return
        self._visible_start_date = visible_start
        self._visible_dates = [
            self._visible_start_date + timedelta(days=offset)
            for offset in range(_VISIBLE_DAY_COUNT)
        ]
        offset = int((self._visible_start_date - self._base_start_date).days)
        new_value = _scroll_value_for_day_offset(offset)
        try:
            self._day_scroll_sync = True
            self._day_scroll.setValue(int(new_value))
        finally:
            self._day_scroll_sync = False

    def _update_today_marker(self):
        icon = _today_icon()
        if icon is None:
            self._today_marker_icon.hide()
            return
        header = self._table.horizontalHeader()
        try:
            header_viewport = header.viewport()
        except Exception:
            header_viewport = None
        if header_viewport is None:
            self._today_marker_icon.hide()
            return
        if self._today_marker_icon.parent() is not header_viewport:
            self._today_marker_icon.setParent(header_viewport)
            self._today_marker_icon.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self._today_marker_icon.setStyleSheet("background:transparent;")
        today_col = int((self._today - self._visible_start_date).days)
        if today_col < 0 or today_col >= self._table.columnCount():
            self._today_marker_icon.hide()
            return
        pm = icon.pixmap(12, 12)
        self._today_marker_icon.setPixmap(pm)
        try:
            x = int(header.sectionPosition(today_col) - (pm.width() / 2.0))
        except Exception:
            self._today_marker_icon.hide()
            return
        x = max(0, min(x, max(0, header_viewport.width() - pm.width())))
        y = max(0, int((header_viewport.height() - pm.height()) / 2.0) + _TODAY_MARKER_Y_OFFSET)
        self._today_marker_icon.move(x, y)
        self._today_marker_icon.resize(pm.size())
        self._today_marker_icon.show()
        self._month_strip.update()

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._schedule_sync)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(self._on_scene_param_changed)
            except Exception:
                pass
        self._scene_connected = True

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._schedule_sync()

    def _schedule_sync(self):
        self._ensure_scene()
        if self._sync_pending:
            return
        self._sync_pending = True
        QtCore.QTimer.singleShot(0, self._sync_from_source)

    def _sync_from_source(self):
        self._ensure_scene()
        self._sync_pending = False
        self._sync_view_start_from_params(force=True)
        src_item, src_model = _resolve_note_source(self._node_item)
        self._source_invalid = bool(src_item is not None and src_model is None)
        self._source_name = getattr(src_model, "name", "") if src_model is not None else ""
        task_names = _note_task_names(src_model)
        assignments = _read_assignments(
            self._node_item,
            default_year=self._visible_start_date.year,
            default_month=self._visible_start_date.month,
        )
        pruned = {name: day for name, day in assignments.items() if name in task_names}
        if pruned != assignments:
            _write_assignments(self._node_item, pruned, notify_scene=False)
        self._task_names = task_names
        self._assignments = pruned
        if self._selected_task not in self._task_names:
            self._selected_task = None
        self._rebuild_table()
        self._update_labels()

    def _update_labels(self):
        if self._source_invalid:
            self._source_label.setText("Connect a Note node to this input.")
            self._source_label.setStyleSheet("QLabel{color:#fca5a5;font-weight:600;}")
        elif self._source_name:
            self._source_label.setText(f"Source: {self._source_name}")
            self._source_label.setStyleSheet("QLabel{color:#93c5fd;font-weight:600;}")
        else:
            self._source_label.setText("Connect a Note node to this input.")
            self._source_label.setStyleSheet("QLabel{color:#93a4b8;font-weight:600;}")

        self._month_strip.set_dates(self._visible_dates, self._today)
        self._update_today_marker()

        if self._selected_task:
            raw = self._assignments.get(self._selected_task)
            if raw:
                try:
                    assigned = date.fromisoformat(str(raw))
                    self._selection_label.setText(
                        f"Selected: {self._selected_task} -> {calendar.month_abbr[assigned.month]} {assigned.day}, {assigned.year}"
                    )
                except Exception:
                    self._selection_label.setText(f"Selected: {self._selected_task} -> {raw}")
            else:
                self._selection_label.setText(f"Selected: {self._selected_task} -> unscheduled")
        elif self._task_names:
            self._selection_label.setText("Select a task, then click a day.")
        elif self._source_name:
            self._selection_label.setText("The connected note has no visible parameters.")
        else:
            self._selection_label.setText("Select a note task to place it on the chart.")

    def _ensure_item(self, row: int, col: int) -> QtWidgets.QTableWidgetItem:
        item = self._table.item(row, col)
        if item is None:
            item = QtWidgets.QTableWidgetItem("")
            try:
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            except Exception:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            self._table.setItem(row, col, item)
        return item

    def _rebuild_table(self):
        self._table.blockSignals(True)
        self._ignore_header_move = True
        day_count = self._visible_day_count()
        self._table.setColumnCount(day_count)
        if not self._visible_dates:
            self._visible_dates = [
                self._visible_start_date + timedelta(days=offset)
                for offset in range(day_count)
            ]
        self._table.setHorizontalHeaderLabels([str(visible_date.day) for visible_date in self._visible_dates])
        try:
            fixed_mode = QtWidgets.QHeaderView.ResizeMode.Fixed
        except AttributeError:
            fixed_mode = QtWidgets.QHeaderView.Fixed
        today_column = None
        today_offset = int((self._today - self._visible_start_date).days)
        if 0 <= today_offset < day_count:
            today_column = today_offset
        for day in range(day_count):
            _set_section_resize_mode(self._table.horizontalHeader(), day, fixed_mode)
            self._table.setColumnWidth(day, 24)
            header_item = self._table.horizontalHeaderItem(day)
            if header_item is None:
                header_item = QtWidgets.QTableWidgetItem(str(self._visible_dates[day].day))
                self._table.setHorizontalHeaderItem(day, header_item)
            header_item.setText(str(self._visible_dates[day].day))
            if today_column is not None and day == today_column:
                header_item.setForeground(QtGui.QBrush(QtGui.QColor("#22c55e")))
                header_item.setToolTip(f"Today: {self._today.isoformat()}")
            else:
                header_item.setIcon(QtGui.QIcon())
                header_item.setForeground(QtGui.QBrush(QtGui.QColor("#dbe4ee")))
                visible_date = self._visible_dates[day]
                header_item.setToolTip(f"{calendar.month_name[visible_date.month]} {visible_date.day}, {visible_date.year}")

        self._table.setRowCount(len(self._task_names))
        for row, task in enumerate(self._task_names):
            self._table.setRowHeight(row, 26)
            header_item = self._table.verticalHeaderItem(row)
            if header_item is None:
                header_item = QtWidgets.QTableWidgetItem(task)
                self._table.setVerticalHeaderItem(row, header_item)
            header_item.setText(task)
            header_item.setToolTip(task)
            for day in range(day_count):
                cell = self._ensure_item(row, day)
                cell.setText("")
                cell.setTextAlignment(int(QtCore.Qt.AlignCenter))
                visible_date = self._visible_dates[day]
                cell.setToolTip(f"{task}: {visible_date.isoformat()}")
        self._table.blockSignals(False)
        self._ignore_header_move = False
        self._table.set_today_column(today_column)
        self._month_strip.set_dates(self._visible_dates, self._today)
        self._apply_table_styles()
        self._update_today_marker()

    def _apply_table_styles(self):
        label_bg = QtGui.QColor("#141b24")
        label_fg = QtGui.QColor("#dbe4ee")
        selected_label_bg = QtGui.QColor("#1d4f74")
        selected_label_fg = QtGui.QColor("#f8fafc")
        cell_bg = QtGui.QColor("#10161d")
        selected_row_bg = QtGui.QColor("#16212b")
        assigned_bg = QtGui.QColor("#0f766e")
        assigned_fg = QtGui.QColor("#ecfeff")
        selected_row = None if self._selected_task not in self._task_names else self._task_names.index(self._selected_task)

        for row, task in enumerate(self._task_names):
            is_selected = task == self._selected_task
            header_item = self._table.verticalHeaderItem(row)
            if header_item is None:
                header_item = QtWidgets.QTableWidgetItem(task)
                self._table.setVerticalHeaderItem(row, header_item)
            header_item.setBackground(selected_label_bg if is_selected else label_bg)
            header_item.setForeground(selected_label_fg if is_selected else label_fg)
            font = header_item.font()
            font.setBold(bool(is_selected))
            header_item.setFont(font)

            assigned_column = self._visible_column_for_assignment(task)
            for day in range(self._visible_day_count()):
                item = self._ensure_item(row, day)
                if assigned_column == day:
                    item.setBackground(assigned_bg)
                    item.setForeground(assigned_fg)
                else:
                    item.setBackground(selected_row_bg if is_selected else cell_bg)
                    item.setForeground(label_fg)
        header = self._table.verticalHeader()
        if hasattr(header, "set_selected_section"):
            try:
                header.set_selected_section(selected_row)
            except Exception:
                pass

    def _on_cell_clicked(self, row: int, col: int):
        if row < 0 or row >= len(self._task_names):
            return
        task = self._task_names[row]
        if not self._selected_task:
            self._selected_task = task
            self._apply_table_styles()
            self._update_labels()
            return
        self._set_task_day(self._selected_task, col + 1)

    def _on_header_clicked(self, section: int):
        if section < 0 or section >= self._visible_day_count():
            return
        if not self._selected_task:
            self._selection_label.setText("Select a task first, then choose a day.")
            return
        self._set_task_day(self._selected_task, section + 1)

    def _on_task_header_clicked(self, section: int):
        if section < 0 or section >= len(self._task_names):
            return
        self._selected_task = self._task_names[section]
        self._apply_table_styles()
        self._update_labels()

    def _set_task_day(self, task: str, day: int):
        if not task or not (1 <= int(day) <= self._visible_day_count()):
            return
        mapping = dict(self._assignments)
        mapping[task] = self._visible_date_for_day(day).isoformat()
        self._assignments = mapping
        _write_assignments(self._node_item, mapping, notify_scene=True)
        self._apply_table_styles()
        self._update_labels()

    def _on_table_context_menu(self, pos):
        item = self._table.itemAt(pos)
        if item is None:
            return
        row = int(item.row())
        col = int(item.column())
        if row < 0 or row >= len(self._task_names):
            return
        task = self._task_names[row]
        if self._visible_column_for_assignment(task) != col:
            return
        mapping = dict(self._assignments)
        mapping.pop(task, None)
        self._assignments = mapping
        self._selected_task = task
        _write_assignments(self._node_item, mapping, notify_scene=True)
        self._apply_table_styles()
        self._update_labels()

    def _on_day_scroll_changed(self, value: int):
        if self._day_scroll_sync:
            return
        visible_start = self._base_start_date + timedelta(days=_day_offset_for_scroll_value(value))
        if visible_start == self._visible_start_date:
            return
        self._visible_start_date = visible_start
        self._visible_dates = [
            self._visible_start_date + timedelta(days=offset)
            for offset in range(_VISIBLE_DAY_COUNT)
        ]
        _set_param_value(
            self._node_item,
            _VIEW_START_PARAM,
            self._visible_start_date.isoformat(),
            notify_scene=False,
        )
        _set_param_value(
            self._node_item,
            _VIEW_MONTH_PARAM,
            f"{self._visible_start_date.year:04d}-{self._visible_start_date.month:02d}",
            notify_scene=False,
        )
        self._rebuild_table()
        self._update_labels()

    def _on_task_section_moved(self, logical_index: int, _old_visual_index: int, _new_visual_index: int):
        if self._ignore_header_move:
            return
        if logical_index < 0 or logical_index >= len(self._task_names):
            return
        header = self._table.verticalHeader()
        ordered = [
            self._task_names[row]
            for row in sorted(range(len(self._task_names)), key=lambda r: int(header.visualIndex(r)))
        ]
        if _reorder_note_params(self._node_item, ordered):
            self._task_names = ordered
            self._rebuild_table()
            self._update_labels()


def render_node_body(node_item, y_cursor: int) -> int:
    body = GanttChartWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    h = max(
        int(body.sizeHint().height()),
        int(body.minimumSizeHint().height()),
        int(body.minimumHeight() or 0),
    )
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


GANTT_CHART_SPEC = Spec(
    stripe_color="#22c55e",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
