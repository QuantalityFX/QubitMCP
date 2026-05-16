from __future__ import annotations

from echograph.qt_compat import QtCore, QtWidgets
from echograph.services.profiler import (
    ProfilerSession,
    ProfilerSnapshot,
    export_snapshot_markdown,
    get_profiler,
)


class _ProfilerTableItem(QtWidgets.QTableWidgetItem):
    def __init__(self, text: str, *, sort_value=None):
        super().__init__(text)
        self._sort_value = sort_value

    def __lt__(self, other) -> bool:
        if isinstance(other, _ProfilerTableItem):
            left = self._sort_value
            right = other._sort_value
            if left is not None and right is not None:
                return left < right
        return super().__lt__(other)


class _ProfilerResizeHandle(QtWidgets.QFrame):
    def __init__(self, owner):
        super().__init__(owner)
        self._owner = owner
        self._drag_origin = None
        self._start_height = 0
        self.setObjectName("ProfilerResizeHandle")
        self.setFixedHeight(8)
        self.setCursor(QtCore.Qt.SizeVerCursor)

    @staticmethod
    def _global_y(event) -> int:
        try:
            return int(event.globalPosition().y())
        except Exception:
            try:
                return int(event.globalPos().y())
            except Exception:
                return 0

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_origin = self._global_y(event)
            self._start_height = int(self._owner.height())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and bool(event.buttons() & QtCore.Qt.LeftButton):
            delta = self._global_y(event) - int(self._drag_origin)
            self._owner.heightResizeRequested.emit(int(self._start_height + delta))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_origin is not None and event.button() == QtCore.Qt.LeftButton:
            delta = self._global_y(event) - int(self._drag_origin)
            self._owner.heightResizeRequested.emit(int(self._start_height + delta))
            self._drag_origin = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ProfilerPanel(QtWidgets.QFrame):
    heightResizeRequested = QtCore.Signal(int)

    def __init__(self, parent=None, *, session: ProfilerSession | None = None):
        super().__init__(parent)
        self._session = session or get_profiler()
        self._last_export_path = None
        self.setObjectName("ProfilerPanel")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setMinimumHeight(240)
        self.setStyleSheet(
            "#ProfilerPanel{background:rgba(15,23,42,232);border-bottom:1px solid #334155;}"
            "#ProfilerPanel QLabel{color:#e5e7eb;}"
            "#ProfilerPanel QPushButton{color:#e5e7eb;background:#1f2937;border:1px solid #475569;"
            "border-radius:4px;padding:2px 8px;}"
            "#ProfilerPanel QPushButton:hover{background:#334155;}"
            "#ProfilerPanel QPushButton#ProfilerRecordButton[recording=\"true\"]{"
            "background:#7f1d1d;border-color:#ef4444;}"
            "#ProfilerPanel QTabWidget::pane{border:1px solid #334155;}"
            "#ProfilerPanel QTabBar::tab{color:#cbd5e1;background:#111827;padding:4px 10px;"
            "border:1px solid #334155;border-bottom:0px;}"
            "#ProfilerPanel QTabBar::tab:selected{background:#1f2937;color:#f8fafc;}"
            "#ProfilerPanel QTableWidget{background:#0f172a;color:#e5e7eb;gridline-color:#334155;"
            "selection-background-color:#1f7a45;}"
            "#ProfilerPanel QHeaderView::section{background:#111827;color:#cbd5e1;"
            "border:0px;border-bottom:1px solid #334155;padding:3px 6px;}"
            "#ProfilerResizeHandle{background:#334155;}"
            "#ProfilerResizeHandle:hover{background:#64748b;}"
        )

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        title = QtWidgets.QLabel("Profiler", self)
        title.setStyleSheet("font-weight:600;")
        header.addWidget(title, 0)

        self._record_btn = QtWidgets.QPushButton("Start", self)
        self._record_btn.setObjectName("ProfilerRecordButton")
        self._record_btn.setToolTip("Start or stop a profiler capture")
        self._record_btn.clicked.connect(self._toggle_recording)
        header.addWidget(self._record_btn, 0)

        self._clear_btn = QtWidgets.QPushButton("Clear", self)
        self._clear_btn.setToolTip("Clear the last profiler capture")
        self._clear_btn.clicked.connect(self._clear_snapshot)
        header.addWidget(self._clear_btn, 0)

        self._export_btn = QtWidgets.QPushButton("Export", self)
        self._export_btn.setToolTip("Save the latest profiler capture under logs/Profiler")
        self._export_btn.clicked.connect(self._export_snapshot)
        header.addWidget(self._export_btn, 0)

        self._status = QtWidgets.QLabel("Ready", self)
        header.addWidget(self._status, 1)
        root.addLayout(header, 0)

        self._tabs = QtWidgets.QTabWidget(self)
        self._span_table = self._build_table(["Process", "Calls", "Total ms", "Avg ms", "Max ms"])
        self._function_table = self._build_table(["Python function", "Calls", "Cumulative ms", "Self ms"])
        self._tabs.addTab(self._span_table, "Processes")
        self._tabs.addTab(self._function_table, "Python")
        root.addWidget(self._tabs, 1)

        self._resize_handle = _ProfilerResizeHandle(self)
        root.addWidget(self._resize_handle, 0)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._refresh_status)
        self._show_snapshot(self._session.last_snapshot)
        self._refresh_status()

    @staticmethod
    def _build_table(headers: list[str]) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        table.setShowGrid(True)
        table.setSortingEnabled(True)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        for idx in range(len(headers)):
            mode = QtWidgets.QHeaderView.ResizeToContents
            if idx == 0:
                mode = QtWidgets.QHeaderView.Stretch
            header.setSectionResizeMode(idx, mode)
        return table

    def _toggle_recording(self) -> None:
        if self._session.recording:
            snapshot = self._session.stop()
            self._timer.stop()
            self._show_snapshot(snapshot)
            self._export_snapshot(snapshot=snapshot, automatic=True)
        else:
            self._session.start()
            self._clear_tables()
            self._timer.start()
        self._refresh_status()

    def _clear_snapshot(self) -> None:
        self._session.clear()
        self._last_export_path = None
        self._clear_tables()
        self._refresh_status()

    def _clear_tables(self) -> None:
        self._span_table.setRowCount(0)
        self._function_table.setRowCount(0)

    def _refresh_status(self) -> None:
        recording = self._session.recording
        self._record_btn.setText("Stop" if recording else "Start")
        self._record_btn.setProperty("recording", "true" if recording else "false")
        self._record_btn.style().unpolish(self._record_btn)
        self._record_btn.style().polish(self._record_btn)
        if recording:
            self._status.setText(f"Recording {self._session.elapsed_s():.2f} s")
            return
        snapshot = self._session.last_snapshot
        if snapshot.duration_s > 0.0:
            suffix = ""
            if self._last_export_path is not None:
                suffix = f" | saved {self._last_export_path.name}"
            self._status.setText(
                f"Last capture {snapshot.duration_s:.2f} s, "
                f"{len(snapshot.spans)} process groups, {len(snapshot.functions)} Python rows"
                f"{suffix}"
            )
        else:
            self._status.setText("Ready")

    def _export_snapshot(
        self,
        _checked: bool = False,
        *,
        snapshot: ProfilerSnapshot | None = None,
        automatic: bool = False,
    ) -> None:
        snap = snapshot or self._session.last_snapshot
        if snap.duration_s <= 0.0 and not snap.spans and not snap.functions:
            self._status.setText("No profiler capture to export")
            return
        try:
            self._last_export_path = export_snapshot_markdown(snap, max_logs=5)
        except Exception as exc:
            self._status.setText(f"Export failed: {exc}")
            return
        if not automatic:
            self._refresh_status()

    def _show_snapshot(self, snapshot: ProfilerSnapshot) -> None:
        self._populate_span_table(snapshot)
        self._populate_function_table(snapshot)

    def _populate_span_table(self, snapshot: ProfilerSnapshot) -> None:
        rows = list(snapshot.spans)
        self._span_table.setSortingEnabled(False)
        self._span_table.setRowCount(len(rows))
        for row_idx, stat in enumerate(rows):
            values = [
                (stat.name, stat.name),
                (str(stat.calls), stat.calls),
                (f"{stat.total_s * 1000.0:.3f}", stat.total_s * 1000.0),
                (f"{stat.avg_s * 1000.0:.3f}", stat.avg_s * 1000.0),
                (f"{stat.max_s * 1000.0:.3f}", stat.max_s * 1000.0),
            ]
            for col_idx, (text, sort_value) in enumerate(values):
                self._span_table.setItem(
                    row_idx,
                    col_idx,
                    _ProfilerTableItem(text, sort_value=sort_value),
                )
        self._span_table.setSortingEnabled(True)
        self._span_table.sortItems(2, QtCore.Qt.DescendingOrder)

    def _populate_function_table(self, snapshot: ProfilerSnapshot) -> None:
        rows = list(snapshot.functions)
        self._function_table.setSortingEnabled(False)
        self._function_table.setRowCount(len(rows))
        for row_idx, stat in enumerate(rows):
            values = [
                (stat.name, stat.name),
                (str(stat.calls), stat.calls),
                (f"{stat.total_s * 1000.0:.3f}", stat.total_s * 1000.0),
                (f"{stat.self_s * 1000.0:.3f}", stat.self_s * 1000.0),
            ]
            for col_idx, (text, sort_value) in enumerate(values):
                self._function_table.setItem(
                    row_idx,
                    col_idx,
                    _ProfilerTableItem(text, sort_value=sort_value),
                )
        self._function_table.setSortingEnabled(True)
        self._function_table.sortItems(2, QtCore.Qt.DescendingOrder)
