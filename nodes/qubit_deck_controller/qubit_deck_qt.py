from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


DEFAULT_API_BASE = "http://127.0.0.1:8765"
APP_ICON_FILE = Path(__file__).with_name("QBeam__API_Icon.ico")
WINDOWS_APP_USER_MODEL_ID = "QubitDeckController.QtDebugger"
HIGHLIGHT_BG = QColor(92, 72, 18)
HIGHLIGHT_FG = QColor(245, 245, 245)


class ApiClient:
    def __init__(self, base_url: str, timeout_seconds: float = 3.0):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds

    def get_json(self, path: str) -> dict:
        return self._request_json("GET", path)

    def post_json(self, path: str, body: dict | None = None) -> dict:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        return self._request_json("POST", path, payload=payload)

    def _request_json(self, method: str, path: str, payload: bytes | None = None) -> dict:
        url = urljoin(self.base_url, path.lstrip("/"))
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url=url, data=payload, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace").strip()
                if not raw:
                    return {}
                return json.loads(raw)
        except HTTPError as ex:
            detail = ex.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"HTTP {ex.code}: {detail or ex.reason}") from ex
        except URLError as ex:
            raise RuntimeError(f"Connection failed: {ex.reason}") from ex
        except json.JSONDecodeError as ex:
            raise RuntimeError(f"Invalid JSON response: {ex}") from ex


class DebuggerWindow(QMainWindow):
    def __init__(self, api_base_url: str):
        super().__init__()
        self.setWindowTitle("Qubit Deck Controller Debugger")
        self.resize(980, 680)

        self.api_client = ApiClient(api_base_url)
        self.buttons_data: list[dict] = []
        self._last_buttons_signature: tuple | None = None
        self._refresh_in_flight = False

        self._build_ui()

        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.timeout.connect(self.refresh_buttons)
        self.auto_refresh_timer.start(self.refresh_interval_ms())

        self.ping_health()
        self.refresh_buttons()

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        top_grid = QGridLayout()
        main_layout.addLayout(top_grid)

        title = QLabel("Qubit Deck API Debugger")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        top_grid.addWidget(title, 0, 0, 1, 5)

        top_grid.addWidget(QLabel("API Base URL"), 1, 0)
        self.api_base_edit = QLineEdit(self.api_client.base_url.rstrip("/"))
        self.api_base_edit.setMinimumWidth(380)
        top_grid.addWidget(self.api_base_edit, 1, 1, 1, 3)

        self.apply_base_btn = QPushButton("Apply URL")
        self.apply_base_btn.clicked.connect(self.apply_api_base)
        top_grid.addWidget(self.apply_base_btn, 1, 4)

        self.health_label = QLabel("Health: Unknown")
        self.health_label.setStyleSheet("color: #d1a000; font-weight: bold;")
        top_grid.addWidget(self.health_label, 2, 0, 1, 2)

        self.health_btn = QPushButton("Ping Health")
        self.health_btn.clicked.connect(self.ping_health)
        top_grid.addWidget(self.health_btn, 2, 2)

        self.auto_refresh_check = QCheckBox("Auto Refresh")
        self.auto_refresh_check.setChecked(True)
        self.auto_refresh_check.toggled.connect(self.on_auto_refresh_toggled)
        top_grid.addWidget(self.auto_refresh_check, 2, 3)

        self.refresh_interval_spin = QSpinBox()
        self.refresh_interval_spin.setRange(1, 120)
        self.refresh_interval_spin.setValue(5)
        self.refresh_interval_spin.setSuffix(" s")
        self.refresh_interval_spin.valueChanged.connect(self.on_refresh_interval_changed)
        top_grid.addWidget(self.refresh_interval_spin, 2, 4)

        self.buttons_status_label = QLabel("Buttons: n/a")
        self.buttons_status_label.setStyleSheet("color: #aaaaaa;")
        top_grid.addWidget(self.buttons_status_label, 3, 0, 1, 5)

        actions = QHBoxLayout()
        main_layout.addLayout(actions)

        self.refresh_btn = QPushButton("Refresh Buttons")
        self.refresh_btn.clicked.connect(lambda: self.refresh_buttons(force=True))
        actions.addWidget(self.refresh_btn)

        self.invoke_btn = QPushButton("Invoke Selected")
        self.invoke_btn.clicked.connect(self.invoke_selected)
        actions.addWidget(self.invoke_btn)

        self.highlight_on_btn = QPushButton("Highlight On")
        self.highlight_on_btn.clicked.connect(lambda: self.set_selected_highlight(True))
        actions.addWidget(self.highlight_on_btn)

        self.highlight_off_btn = QPushButton("Highlight Off")
        self.highlight_off_btn.clicked.connect(lambda: self.set_selected_highlight(False))
        actions.addWidget(self.highlight_off_btn)

        actions.addStretch(1)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Display Name", "Type", "Slot #", "Highlighted", "Docker Running"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        main_layout.addWidget(self.table, 1)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(180)
        main_layout.addWidget(self.log_view)

    def apply_api_base(self) -> None:
        candidate = self.api_base_edit.text().strip()
        if not candidate:
            QMessageBox.warning(self, "Invalid URL", "API base URL cannot be empty.")
            return
        self.api_client = ApiClient(candidate)
        self.log(f"API base set to {self.api_client.base_url.rstrip('/')}")
        self.ping_health()
        self.refresh_buttons(force=True)

    def ping_health(self) -> None:
        try:
            payload = self.api_client.get_json("/api/health")
            status = payload.get("status") if isinstance(payload, dict) else None
            self.health_label.setText(f"Health: {status or 'OK'}")
            self.health_label.setStyleSheet("color: #3aa655; font-weight: bold;")
            self.log("Health check OK")
        except Exception as ex:
            self.health_label.setText("Health: OFFLINE")
            self.health_label.setStyleSheet("color: #d9534f; font-weight: bold;")
            self.log(f"Health check failed: {ex}")

    def refresh_buttons(self, force: bool = False) -> None:
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        try:
            payload = self.api_client.get_json("/api/buttons")
            if isinstance(payload, dict):
                buttons = payload.get("buttons", [])
            elif isinstance(payload, list):
                buttons = payload
            else:
                buttons = []

            if not isinstance(buttons, list):
                raise RuntimeError("Unexpected /api/buttons payload.")

            index_summary = self.build_index_summary(buttons)
            self.buttons_status_label.setText(f"Buttons: {len(buttons)} ({index_summary})")

            signature = self.compute_buttons_signature(buttons)
            if not force and self._last_buttons_signature == signature:
                return

            self.buttons_data = buttons
            self._last_buttons_signature = signature
            self.populate_table()
            self.log(f"Loaded {len(buttons)} button(s) | {index_summary}")
        except Exception as ex:
            self.log(f"Refresh failed: {ex}")
        finally:
            self._refresh_in_flight = False

    @staticmethod
    def compute_buttons_signature(buttons: list[dict]) -> tuple:
        rows: list[tuple] = []
        for button in buttons:
            rows.append(
                (
                    str(button.get("name", "")),
                    str(button.get("displayName", "")),
                    str(button.get("type", "")),
                    str(button.get("index", "")),
                    bool(button.get("isHighlighted", False)),
                    bool(button.get("isDockerRunning", False)),
                )
            )
        return tuple(rows)

    @staticmethod
    def parse_index(value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def build_index_summary(self, buttons: list[dict]) -> str:
        indices = [idx for idx in (self.parse_index(btn.get("index")) for btn in buttons) if idx is not None]
        if not indices:
            return "index n/a"
        return f"slot range {min(indices) + 1}-{max(indices) + 1}"

    def populate_table(self) -> None:
        selected_name = self.current_selected_name()
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self.buttons_data))
        try:
            for row, button in enumerate(self.buttons_data):
                name = str(button.get("name", ""))
                display_name = str(button.get("displayName", ""))
                button_type = str(button.get("type", ""))
                raw_index = self.parse_index(button.get("index"))
                slot_display = str(raw_index + 1) if raw_index is not None else str(button.get("index", ""))
                highlighted = bool(button.get("isHighlighted", False))
                docker_running = bool(button.get("isDockerRunning", False))

                values = [
                    name,
                    display_name,
                    button_type,
                    slot_display,
                    "Yes" if highlighted else "No",
                    "Yes" if docker_running else "No",
                ]

                for col, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(row, col, item)

                if highlighted:
                    for col in range(self.table.columnCount()):
                        cell = self.table.item(row, col)
                        cell.setBackground(HIGHLIGHT_BG)
                        cell.setForeground(HIGHLIGHT_FG)
        finally:
            self.table.setUpdatesEnabled(True)

        self.restore_selection(selected_name)

    def current_selected_name(self) -> str | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.buttons_data):
            return None
        return str(self.buttons_data[row].get("name", ""))

    def restore_selection(self, selected_name: str | None) -> None:
        if not selected_name:
            return
        for row, button in enumerate(self.buttons_data):
            if str(button.get("name", "")) == selected_name:
                self.table.selectRow(row)
                break

    def selected_button(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.buttons_data):
            QMessageBox.information(self, "Select Button", "Select a row first.")
            return None
        return self.buttons_data[row]

    def invoke_selected(self) -> None:
        button = self.selected_button()
        if button is None:
            return
        try:
            name = quote(str(button.get("name", "")), safe="")
            self.api_client.post_json(f"/api/buttons/{name}/invoke")
            self.log(f"Invoked: {button.get('name')}")
            self.refresh_buttons(force=True)
        except Exception as ex:
            self.log(f"Invoke failed: {ex}")

    def set_selected_highlight(self, enabled: bool) -> None:
        button = self.selected_button()
        if button is None:
            return
        try:
            name = quote(str(button.get("name", "")), safe="")
            query = urlencode({"enabled": "true" if enabled else "false"})
            self.api_client.post_json(f"/api/buttons/{name}/highlight?{query}")
            self.log(f"Highlight {'ON' if enabled else 'OFF'}: {button.get('name')}")
            self.refresh_buttons(force=True)
        except Exception as ex:
            self.log(f"Highlight update failed: {ex}")

    def refresh_interval_ms(self) -> int:
        return int(self.refresh_interval_spin.value()) * 1000

    def on_auto_refresh_toggled(self, enabled: bool) -> None:
        if enabled:
            self.auto_refresh_timer.start(self.refresh_interval_ms())
            self.log("Auto refresh enabled")
        else:
            self.auto_refresh_timer.stop()
            self.log("Auto refresh disabled")

    def on_refresh_interval_changed(self, _value: int) -> None:
        if self.auto_refresh_check.isChecked():
            self.auto_refresh_timer.start(self.refresh_interval_ms())
        self.log(f"Refresh interval set to {self.refresh_interval_spin.value()}s")

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{timestamp}] {message}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qubit Deck Qt API Debugger")
    parser.add_argument(
        "--api-base",
        default=DEFAULT_API_BASE,
        help=f"Qubit Deck API base URL (default: {DEFAULT_API_BASE})",
    )
    return parser.parse_args(argv)


def set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except Exception:
        pass


def load_app_icon() -> QIcon | None:
    if not APP_ICON_FILE.exists():
        return None
    icon = QIcon(str(APP_ICON_FILE))
    if icon.isNull():
        return None
    return icon


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    set_windows_app_user_model_id()

    app = QApplication(sys.argv)
    app_icon = load_app_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)

    window = DebuggerWindow(api_base_url=args.api_base)
    if app_icon is not None:
        window.setWindowIcon(app_icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
