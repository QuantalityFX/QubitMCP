from __future__ import annotations

import datetime
import subprocess
import threading
from pathlib import Path

from nodes.core import Spec
from echograph.qt_compat import QtWidgets, QtCore


MEDIGATOR_NODE_KIND = "medigator_agent"
MEDIGATOR_NODE_ALIASES = [
    "mediator_agent",
    "medigator",
    "mediator",
]
MEDIGATOR_NODE_KINDS = {MEDIGATOR_NODE_KIND, *MEDIGATOR_NODE_ALIASES}

MEDIGATOR_BODY_W = 560
MEDIGATOR_BODY_H = 340
MEDIGATOR_MEMORY_ROOT = "medigator_agents"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sanitize_folder_name(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        text = "medigator"
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    cleaned = "".join(out).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "medigator"


def _workspace_dir_for_node(node_item) -> Path:
    model = getattr(node_item, "model", None)
    node_name = str(getattr(model, "name", "") or "").strip()
    safe_name = _sanitize_folder_name(node_name)
    workspace = _repo_root() / "logs" / MEDIGATOR_MEMORY_ROOT / safe_name
    workspace.mkdir(parents=True, exist_ok=True)
    memory_file = workspace / "memory.md"
    if not memory_file.exists():
        memory_file.write_text(
            "# Medigator Memory\n\n"
            "This folder is dedicated to this node.\n"
            "Store notes, summaries, and agent context here.\n",
            encoding="utf-8",
        )
    return workspace


def _format_ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_ports(node_item) -> None:
    if not hasattr(node_item, "ensure_input"):
        return
    for port_name in ("voice_input", "chatbot_history", "system_prompt"):
        node_item.ensure_input(port_name)


class MedigatorConsoleWidget(QtWidgets.QWidget):
    _console_append = QtCore.Signal(str)
    _command_done = QtCore.Signal(int, str)

    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._workspace_dir = _workspace_dir_for_node(node_item)
        self._process = None
        self._process_lock = threading.Lock()
        self._running = False

        self.setMinimumSize(MEDIGATOR_BODY_W, MEDIGATOR_BODY_H)
        try:
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass

        self._workspace_label = QtWidgets.QLabel(f"Workspace: {self._workspace_dir}")
        self._workspace_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._workspace_label.setStyleSheet("QLabel{color:#93c5fd;}")

        self._command_edit = QtWidgets.QLineEdit()
        self._command_edit.setPlaceholderText("Enter a command (for example: & \".../tools/codex.ps1\")")
        self._command_edit.returnPressed.connect(self._run_command)
        self._command_edit.setStyleSheet(
            "QLineEdit{background:#0f1216;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:4px 6px;}"
        )

        codex_script = _repo_root() / "tools" / "codex.ps1"
        if codex_script.exists():
            self._command_edit.setText(f'& "{codex_script}"')

        self._console = QtWidgets.QPlainTextEdit()
        self._console.setReadOnly(True)
        self._console.setStyleSheet(
            "QPlainTextEdit{background:#0b1220;color:#e5e7eb;border:1px solid #334155;border-radius:6px;padding:6px;}"
        )

        self._status = QtWidgets.QLabel("Ready.")
        self._status.setStyleSheet("QLabel{color:#94a3b8;}")

        self._run_btn = QtWidgets.QPushButton("Run")
        self._stop_btn = QtWidgets.QPushButton("Stop")
        self._clear_btn = QtWidgets.QPushButton("Clear")
        self._open_btn = QtWidgets.QPushButton("Open Folder")

        self._run_btn.clicked.connect(self._run_command)
        self._stop_btn.clicked.connect(self._stop_command)
        self._clear_btn.clicked.connect(self._clear_console)
        self._open_btn.clicked.connect(self._open_workspace_folder)

        self._run_btn.setStyleSheet(
            "QPushButton{background:#1d4ed8;color:#e2e8f0;border:1px solid #1e3a8a;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#1e40af;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#334155;}"
        )
        self._stop_btn.setStyleSheet(
            "QPushButton{background:#991b1b;color:#fee2e2;border:1px solid #7f1d1d;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#b91c1c;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#334155;}"
        )
        self._clear_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e5e7eb;border:1px solid #475569;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#273449;}"
        )
        self._open_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e5e7eb;border:1px solid #475569;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#273449;}"
        )

        command_row = QtWidgets.QHBoxLayout()
        command_row.setContentsMargins(0, 0, 0, 0)
        command_row.setSpacing(6)
        command_row.addWidget(self._command_edit, 1)
        command_row.addWidget(self._run_btn, 0)
        command_row.addWidget(self._stop_btn, 0)
        command_row.addWidget(self._clear_btn, 0)
        command_row.addWidget(self._open_btn, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._workspace_label, 0)
        layout.addLayout(command_row, 0)
        layout.addWidget(self._console, 1)
        layout.addWidget(self._status, 0)

        self._console_append.connect(self._append_console_line)
        self._command_done.connect(self._on_command_done)
        self._update_controls()

    def sizeHint(self):
        return QtCore.QSize(MEDIGATOR_BODY_W, MEDIGATOR_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(MEDIGATOR_BODY_W, MEDIGATOR_BODY_H)

    def _console_log_path(self) -> Path:
        return self._workspace_dir / "console.log"

    def _history_path(self) -> Path:
        return self._workspace_dir / "command_history.log"

    def _write_console_log(self, line: str) -> None:
        payload = f"[{_format_ts()}] {line.rstrip()}\n"
        try:
            with self._console_log_path().open("a", encoding="utf-8") as handle:
                handle.write(payload)
        except Exception:
            pass

    def _write_history(self, command: str) -> None:
        payload = f"[{_format_ts()}] {command.strip()}\n"
        try:
            with self._history_path().open("a", encoding="utf-8") as handle:
                handle.write(payload)
        except Exception:
            pass

    @QtCore.Slot(str)
    def _append_console_line(self, line: str) -> None:
        text = str(line or "").rstrip("\r\n")
        if not text:
            return
        self._console.appendPlainText(text)
        self._write_console_log(text)
        try:
            bar = self._console.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status.setText(message or "")
        if error:
            self._status.setStyleSheet("QLabel{color:#fca5a5;}")
        else:
            self._status.setStyleSheet("QLabel{color:#94a3b8;}")

    def _set_running(self, running: bool) -> None:
        self._running = bool(running)
        self._update_controls()
        try:
            self._node_item.setBusyState(self._running, "medigator-running" if self._running else "")
        except Exception:
            pass

    def _update_controls(self) -> None:
        self._run_btn.setEnabled(not self._running)
        self._stop_btn.setEnabled(self._running)
        self._command_edit.setEnabled(not self._running)

    def _run_command(self) -> None:
        if self._running:
            self._set_status("A command is already running.", error=True)
            return
        command = str(self._command_edit.text() or "").strip()
        if not command:
            self._set_status("Enter a command first.", error=True)
            return

        self._set_running(True)
        self._set_status("Running command...")
        self._write_history(command)
        self._console_append.emit(f"$ {command}")
        threading.Thread(target=self._command_worker, args=(command,), daemon=True).start()

    def _command_worker(self, command: str) -> None:
        exit_code = -1
        error_text = ""
        process = None
        try:
            process = subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                cwd=str(self._workspace_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            with self._process_lock:
                self._process = process
            if process.stdout is not None:
                for raw in process.stdout:
                    self._console_append.emit(raw.rstrip("\n"))
            exit_code = int(process.wait())
        except Exception as exc:
            error_text = f"Failed to run command: {exc}"
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None
            self._command_done.emit(exit_code, error_text)

    def _stop_command(self) -> None:
        proc = None
        with self._process_lock:
            proc = self._process
        if proc is None:
            self._set_status("No running command.")
            return
        try:
            proc.terminate()
            self._console_append.emit("Process termination requested.")
            self._set_status("Stopping command...")
        except Exception as exc:
            self._set_status(f"Stop failed: {exc}", error=True)

    @QtCore.Slot(int, str)
    def _on_command_done(self, exit_code: int, error_text: str) -> None:
        self._set_running(False)
        if error_text:
            self._console_append.emit(error_text)
            self._set_status(error_text, error=True)
            return
        if int(exit_code) == 0:
            self._set_status("Command finished.")
        else:
            self._set_status(f"Command exited with code {exit_code}.", error=True)

    def _clear_console(self) -> None:
        self._console.clear()
        self._set_status("Console cleared.")

    def _open_workspace_folder(self) -> None:
        try:
            subprocess.Popen(["explorer", str(self._workspace_dir)])
        except Exception as exc:
            self._set_status(f"Failed to open folder: {exc}", error=True)


def render_node_body(node_item, y_cursor: int) -> int:
    try:
        body = MedigatorConsoleWidget(node_item, None)
    except Exception as exc:
        print("[EchoGraph] Medigator UI init failed:", exc)
        body = QtWidgets.QFrame()
        body.setStyleSheet(
            "QFrame{background:#0f1216;color:#e5e7eb;border:1px solid #334;border-radius:6px;}"
        )
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        msg = QtWidgets.QLabel("Medigator UI failed to load. Check console output for details.")
        msg.setWordWrap(True)
        msg.setStyleSheet("QLabel{color:#e5e7eb;}")
        lay.addWidget(msg, 1)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    h = body.sizeHint().height()
    try:
        pad = float(getattr(node_item, "_PADDING", 0))
        available = float(node_item.height) - float(y_cursor) - pad
        if available > h:
            h = int(available)
    except Exception:
        pass
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


MEDIGATOR_SPEC = Spec(
    stripe_color="#0f766e",
    render_node_body=render_node_body,
    build_ports=build_ports,
)

