from __future__ import annotations

import json

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

_DAY_COUNT = 31
_SCHEDULE_PARAM = "schedule_data"


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
    _ensure_param(node_item, _SCHEDULE_PARAM, "{}")
    _ensure_hidden_params(getattr(node_item, "model", None), [_SCHEDULE_PARAM])


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


def _read_assignments(node_item) -> dict[str, int]:
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
    clean: dict[str, int] = {}
    for name, value in data.items():
        task = str(name or "").strip()
        if not task:
            continue
        try:
            day = int(value)
        except Exception:
            continue
        if 1 <= day <= _DAY_COUNT:
            clean[task] = day
    return clean


def _write_assignments(node_item, mapping: dict[str, int], *, notify_scene: bool = True) -> None:
    clean = {}
    for name, value in (mapping or {}).items():
        task = str(name or "").strip()
        if not task:
            continue
        try:
            day = int(value)
        except Exception:
            continue
        if 1 <= day <= _DAY_COUNT:
            clean[task] = day
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
        self._assignments: dict[str, int] = {}
        self._selected_task: str | None = None
        self._ignore_header_move = False

        self.setObjectName("GanttChartWidget")
        self.setStyleSheet(
            "QFrame#GanttChartWidget{background:#0f1216;border:1px solid #334155;border-radius:8px;}"
            "QLabel{color:#cbd5e1;}"
            "QTableWidget{background:#0f1216;color:#e2e8f0;border:1px solid #273244;border-radius:6px;"
            "gridline-color:#223041;selection-background-color:#16212b;selection-color:#e2e8f0;}"
            "QTableWidget::item:selected{background:#16212b;color:#e2e8f0;}"
            "QHeaderView::section{background:#141c27;color:#dbe4ee;border:1px solid #223041;padding:4px;font-weight:600;}"
            "QHeaderView::section:checked{background:#1d4f74;color:#f8fafc;}"
            "QTableCornerButton::section{background:#141c27;border:1px solid #223041;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(10)

        self._source_label = QtWidgets.QLabel("Connect a Note node to this input.")
        self._source_label.setStyleSheet("QLabel{color:#93a4b8;font-weight:600;}")
        status_row.addWidget(self._source_label, 1)

        self._selection_label = QtWidgets.QLabel("Select a task, then click a day.")
        self._selection_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._selection_label.setStyleSheet("QLabel{color:#7dd3fc;}")
        status_row.addWidget(self._selection_label, 1)

        layout.addLayout(status_row)

        self._table = QtWidgets.QTableWidget(0, _DAY_COUNT, self)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setFocusPolicy(QtCore.Qt.NoFocus)
        self._table.setWordWrap(False)
        self._table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._table.verticalHeader().setVisible(True)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(True)
        self._table.setHorizontalHeaderLabels([str(i) for i in range(1, _DAY_COUNT + 1)])
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
        for day in range(_DAY_COUNT):
            _set_section_resize_mode(self._table.horizontalHeader(), day, fixed_mode)
            self._table.setColumnWidth(day, 24)

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

        self._ensure_scene()
        self._sync_from_source()
        QtCore.QTimer.singleShot(0, self._post_attach_sync)

    def sizeHint(self):
        return QtCore.QSize(1000, 320)

    def _post_attach_sync(self):
        self._ensure_scene()
        self._schedule_sync()

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
        src_item, src_model = _resolve_note_source(self._node_item)
        self._source_invalid = bool(src_item is not None and src_model is None)
        self._source_name = getattr(src_model, "name", "") if src_model is not None else ""
        task_names = _note_task_names(src_model)
        assignments = _read_assignments(self._node_item)
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

        if self._selected_task:
            day = self._assignments.get(self._selected_task)
            if day:
                self._selection_label.setText(f"Selected: {self._selected_task} -> day {day}")
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
        self._table.setRowCount(len(self._task_names))
        for row, task in enumerate(self._task_names):
            self._table.setRowHeight(row, 26)
            header_item = self._table.verticalHeaderItem(row)
            if header_item is None:
                header_item = QtWidgets.QTableWidgetItem(task)
                self._table.setVerticalHeaderItem(row, header_item)
            header_item.setText(task)
            header_item.setToolTip(task)
            for day in range(_DAY_COUNT):
                cell = self._ensure_item(row, day)
                cell.setText("")
                cell.setTextAlignment(int(QtCore.Qt.AlignCenter))
                cell.setToolTip(f"{task}: day {day + 1}")
        self._table.blockSignals(False)
        self._ignore_header_move = False
        self._apply_table_styles()

    def _apply_table_styles(self):
        label_bg = QtGui.QColor("#141b24")
        label_fg = QtGui.QColor("#dbe4ee")
        selected_label_bg = QtGui.QColor("#1d4f74")
        selected_label_fg = QtGui.QColor("#f8fafc")
        cell_bg = QtGui.QColor("#10161d")
        selected_row_bg = QtGui.QColor("#16212b")
        assigned_bg = QtGui.QColor("#0f766e")
        assigned_fg = QtGui.QColor("#ecfeff")

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

            assigned_day = self._assignments.get(task)
            for day in range(_DAY_COUNT):
                item = self._ensure_item(row, day)
                if assigned_day == (day + 1):
                    item.setBackground(assigned_bg)
                    item.setForeground(assigned_fg)
                else:
                    item.setBackground(selected_row_bg if is_selected else cell_bg)
                    item.setForeground(label_fg)
        try:
            self._table.blockSignals(True)
            if self._selected_task and self._selected_task in self._task_names:
                row = self._task_names.index(self._selected_task)
                self._table.selectRow(row)
                assigned_day = self._assignments.get(self._selected_task)
                if assigned_day:
                    assigned_item = self._table.item(row, int(assigned_day) - 1)
                    if assigned_item is not None:
                        try:
                            assigned_item.setSelected(False)
                        except Exception:
                            pass
            else:
                self._table.clearSelection()
        except Exception:
            pass
        finally:
            try:
                self._table.blockSignals(False)
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
        if section < 0 or section >= _DAY_COUNT:
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
        if not task or not (1 <= int(day) <= _DAY_COUNT):
            return
        mapping = dict(self._assignments)
        mapping[task] = int(day)
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
        day = col + 1
        if self._assignments.get(task) != day:
            return
        mapping = dict(self._assignments)
        mapping.pop(task, None)
        self._assignments = mapping
        self._selected_task = task
        _write_assignments(self._node_item, mapping, notify_scene=True)
        self._apply_table_styles()
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
