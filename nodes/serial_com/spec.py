from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    from PySide2 import QtWidgets, QtCore  # type: ignore

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:
    serial = None  # type: ignore
    list_ports = None  # type: ignore

from nodes.core import Spec


SERIAL_COM_NODE_KINDS = {"serial_com", "serial com", "serial_port", "serial port"}

_PORT_PARAM = "serial_port"
_BAUD_PARAM = "serial_baud"
_TIMEOUT_PARAM = "serial_timeout_ms"
_HIDDEN_PARAM = "__ui_hidden_params"

_DEFAULT_BAUD = 115200
_DEFAULT_TIMEOUT_MS = 700
_MIN_TIMEOUT_MS = 100
_MAX_TIMEOUT_MS = 5000
_PYSERIAL_REQUIREMENT = "pyserial==3.5"

SERIAL_COM_BODY_W = 360
SERIAL_COM_BODY_H = 124


def _param_value(model, name: str, default: str = "") -> str:
    if model is None:
        return default
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value", "") or "")
    return default


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
    hidden_entry = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == _HIDDEN_PARAM:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": _HIDDEN_PARAM, "value": ""}
        params.append(hidden_entry)
    hidden = {part.strip().lower() for part in str(hidden_entry.get("value", "")).split(",") if part.strip()}
    for name in names or []:
        token = str(name or "").strip().lower()
        if token:
            hidden.add(token)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_param_value(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    text = str(value or "")
    changed = False
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            if str(entry.get("value", "") or "") != text:
                entry["value"] = text
                changed = True
            break
    else:
        params.append({"name": name, "value": text})
        changed = True
    if not changed:
        return
    model.params = params
    if not notify_scene:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None or not hasattr(scene, "paramChanged"):
        return
    try:
        scene.paramChanged.emit(model.name, list(params))
    except Exception:
        pass


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _try_load_serial_runtime() -> bool:
    global serial, list_ports
    if serial is not None and list_ports is not None:
        return True
    try:
        serial = importlib.import_module("serial")  # type: ignore[assignment]
        list_ports = importlib.import_module("serial.tools.list_ports")  # type: ignore[assignment]
    except Exception:
        serial = None  # type: ignore[assignment]
        list_ports = None  # type: ignore[assignment]
        return False
    return True


def serial_runtime_available() -> bool:
    return _try_load_serial_runtime()


def _pyserial_install_command() -> list[str]:
    return [sys.executable, "-m", "pip", "install", _PYSERIAL_REQUIREMENT]


def pyserial_install_command_text() -> str:
    exe = str(sys.executable or "python")
    return f'"{exe}" -m pip install {_PYSERIAL_REQUIREMENT}'


def _dialog_parent(parent=None):
    app = QtWidgets.QApplication.instance()
    if app is not None:
        try:
            active = app.activeWindow()
            if active is not None:
                return active
        except Exception:
            pass
    if parent is not None:
        try:
            win = parent.window()
            if win is not None:
                return win
        except Exception:
            pass
    if app is not None:
        try:
            for widget in list(app.topLevelWidgets() or []):
                if widget is not None and widget.isVisible():
                    return widget
        except Exception:
            pass
    return None


def _apply_dialog_style(dialog, parent=None) -> None:
    try:
        icon = None
        if parent is not None:
            icon = parent.window().windowIcon()
        app = QtWidgets.QApplication.instance()
        if (icon is None or icon.isNull()) and app is not None:
            icon = app.windowIcon()
        if icon is not None and not icon.isNull():
            dialog.setWindowIcon(icon)
    except Exception:
        pass
    dialog.setStyleSheet(
        "QDialog{background:#0f1216;color:#e6edf3;}"
        "QLabel{color:#e6edf3;}"
        "QLineEdit{background:#111827;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:5px 7px;}"
        "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:4px 12px;min-width:72px;}"
        "QPushButton:hover{background:#273449;}"
    )


class _PyserialInstallDialog(QtWidgets.QDialog):
    ACTION_INSTALL = "install"
    ACTION_COPY = "copy"
    ACTION_LATER = "later"

    def __init__(self, command: str, parent=None):
        super().__init__(_dialog_parent(parent))
        self._action = self.ACTION_LATER
        self.setWindowTitle("Serial COM")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.resize(500, 210)
        _apply_dialog_style(self, self.parentWidget() or parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("Serial COM requires pyserial.")
        title.setStyleSheet("QLabel{font-weight:600;font-size:13px;color:#f8fafc;}")
        layout.addWidget(title, 0)

        body = QtWidgets.QLabel(
            "It is not installed in this QubitMCP environment. Install it now, or run setup.bat to refresh the app dependencies."
        )
        body.setWordWrap(True)
        layout.addWidget(body, 0)

        self._command = QtWidgets.QLineEdit(str(command or ""))
        self._command.setReadOnly(True)
        layout.addWidget(self._command, 0)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)

        self._copy_btn = QtWidgets.QPushButton("Copy Command")
        self._copy_btn.clicked.connect(self._on_copy)
        buttons.addWidget(self._copy_btn, 0)

        self._later_btn = QtWidgets.QPushButton("Later")
        self._later_btn.clicked.connect(self.reject)
        buttons.addWidget(self._later_btn, 0)

        self._install_btn = QtWidgets.QPushButton("Install pyserial")
        self._install_btn.setDefault(True)
        self._install_btn.clicked.connect(self._on_install)
        buttons.addWidget(self._install_btn, 0)
        layout.addLayout(buttons, 0)

    def action(self) -> str:
        return self._action

    def _on_copy(self) -> None:
        self._action = self.ACTION_COPY
        try:
            QtWidgets.QApplication.clipboard().setText(self._command.text())
        except Exception:
            pass
        self.accept()

    def _on_install(self) -> None:
        self._action = self.ACTION_INSTALL
        self.accept()


def _install_pyserial(parent=None) -> tuple[bool, str]:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        try:
            app.setOverrideCursor(QtCore.Qt.WaitCursor)
            app.processEvents()
        except Exception:
            pass
    try:
        run_kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": 300,
            "check": False,
        }
        if os.name == "nt":
            # Keep pip installs initiated from the GUI from spawning a stray console window.
            run_kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        proc = subprocess.run(
            _pyserial_install_command(),
            **run_kwargs,
        )
    except Exception as exc:
        return False, f"Could not start pip: {exc}"
    finally:
        if app is not None:
            try:
                app.restoreOverrideCursor()
            except Exception:
                pass

    output = "\n".join(part for part in ((proc.stdout or "").strip(), (proc.stderr or "").strip()) if part).strip()
    if int(proc.returncode) != 0:
        return False, output or f"pip exited with code {proc.returncode}."
    if not _try_load_serial_runtime():
        return False, "pip finished, but pyserial could not be imported in the running app."
    return True, output


def prompt_install_pyserial(parent=None) -> tuple[bool, str]:
    if serial_runtime_available():
        return True, ""

    command = pyserial_install_command_text()
    dialog = _PyserialInstallDialog(command, parent)
    try:
        dialog.exec()
    except Exception:
        dialog.exec_()

    action = dialog.action()
    if action == _PyserialInstallDialog.ACTION_COPY:
        return False, "Install command copied to the clipboard."
    if action != _PyserialInstallDialog.ACTION_INSTALL:
        return False, "pyserial is required for Serial COM."

    ok, details = _install_pyserial(parent)
    if ok:
        return True, "pyserial installed."
    first_line = next((line.strip() for line in str(details or "").splitlines() if line.strip()), "")
    if first_line:
        return False, f"pyserial install failed: {first_line}"
    return False, f"pyserial install failed. Run setup.bat or use: {command}"


def _read_port(node_item) -> str:
    return _param_value(getattr(node_item, "model", None), _PORT_PARAM, "").strip()


def _read_baud(node_item) -> int:
    return max(1200, _coerce_int(_param_value(getattr(node_item, "model", None), _BAUD_PARAM), _DEFAULT_BAUD))


def _read_timeout_ms(node_item) -> int:
    raw = _coerce_int(_param_value(getattr(node_item, "model", None), _TIMEOUT_PARAM), _DEFAULT_TIMEOUT_MS)
    return max(_MIN_TIMEOUT_MS, min(_MAX_TIMEOUT_MS, raw))


def _available_ports() -> list[str]:
    if not serial_runtime_available():
        return []
    try:
        return sorted({str(port.device or "").strip() for port in list_ports.comports() if str(port.device or "").strip()})
    except Exception:
        return []


def _node_kind(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "kind", "") or "").strip().lower()


def _is_serial_com_node(node_item) -> bool:
    return _node_kind(node_item) in SERIAL_COM_NODE_KINDS


@dataclass(frozen=True)
class SerialTargetConfig:
    node_name: str
    port: str
    baud_rate: int
    timeout_ms: int


def connected_serial_target_configs(source_node_item) -> list[SerialTargetConfig]:
    scene = source_node_item.scene() if hasattr(source_node_item, "scene") else None
    if scene is None:
        return []
    configs: list[SerialTargetConfig] = []
    seen: set[str] = set()
    for edge in list(getattr(scene, "_edges", []) or []):
        if getattr(edge, "src", None) is not source_node_item:
            continue
        dst = getattr(edge, "dst", None)
        if dst is None or not _is_serial_com_node(dst):
            continue
        model = getattr(dst, "model", None)
        node_name = str(getattr(model, "name", "") or "").strip() or "Serial COM"
        if node_name in seen:
            continue
        seen.add(node_name)
        configs.append(
            SerialTargetConfig(
                node_name=node_name,
                port=_read_port(dst),
                baud_rate=_read_baud(dst),
                timeout_ms=_read_timeout_ms(dst),
            )
        )
    return configs


class PicoSerialSession:
    def __init__(self, config: SerialTargetConfig):
        self.config = config
        self._handle = None

    def open(self) -> tuple[bool, str]:
        if not serial_runtime_available():
            return False, "pyserial is not installed."
        if not self.config.port:
            return False, "Select a COM port."
        try:
            self._handle = serial.Serial(
                port=self.config.port,
                baudrate=int(self.config.baud_rate),
                timeout=float(self.config.timeout_ms) / 1000.0,
                write_timeout=float(self.config.timeout_ms) / 1000.0,
            )
            time.sleep(0.15)
            return True, ""
        except Exception as exc:
            return False, f"Could not open {self.config.port}: {exc}"

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.close()
        except Exception:
            pass

    def _request(self, line: str) -> tuple[bool, str]:
        handle = self._handle
        if handle is None:
            return False, "Serial session is not open."
        payload = (str(line or "").strip() + "\n").encode("utf-8")
        try:
            handle.write(payload)
            handle.flush()
            raw = handle.readline()
        except Exception as exc:
            return False, f"Serial write failed: {exc}"
        reply = raw.decode("utf-8", errors="replace").strip()
        if not reply:
            return False, f"No reply from {self.config.port}."
        if reply.upper().startswith("ERR"):
            return False, reply
        return True, reply

    def ping(self) -> tuple[bool, str]:
        ok, reply = self._request("PING")
        if not ok:
            return ok, reply
        if reply.upper() != "PONG":
            return False, f"Unexpected ping reply: {reply}"
        return True, reply

    def tap(self, key_text: str, hold_ms: int) -> tuple[bool, str]:
        clean_key = str(key_text or "").strip()
        if not clean_key:
            return False, "Key is empty."
        if "|" in clean_key:
            return False, "Key text cannot contain '|'."
        ok, reply = self._request(f"TAP|{max(0, int(hold_ms))}|{clean_key}")
        if not ok:
            return ok, reply
        if not reply.upper().startswith("OK"):
            return False, f"Unexpected tap reply: {reply}"
        return True, reply

    def release_all(self) -> tuple[bool, str]:
        ok, reply = self._request("RELEASE_ALL")
        if not ok:
            return ok, reply
        if not reply.upper().startswith("OK"):
            return False, f"Unexpected release reply: {reply}"
        return True, reply

def open_pico_sessions(configs: list[SerialTargetConfig]) -> tuple[list[PicoSerialSession], str]:
    sessions: list[PicoSerialSession] = []
    for config in list(configs or []):
        session = PicoSerialSession(config)
        ok, message = session.open()
        if not ok:
            close_pico_sessions(sessions)
            return [], f"{config.node_name}: {message}"
        ok, message = session.ping()
        if not ok:
            session.close()
            close_pico_sessions(sessions)
            return [], f"{config.node_name}: {message}"
        sessions.append(session)
    return sessions, ""


def close_pico_sessions(sessions: list[PicoSerialSession], *, release_all: bool = False) -> None:
    for session in list(sessions or []):
        if release_all:
            try:
                session.release_all()
            except Exception:
                pass
        session.close()


def build_ports(node_item) -> None:
    _ensure_param(node_item, _PORT_PARAM, "")
    _ensure_param(node_item, _BAUD_PARAM, str(_DEFAULT_BAUD))
    _ensure_param(node_item, _TIMEOUT_PARAM, str(_DEFAULT_TIMEOUT_MS))
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [_PORT_PARAM, _BAUD_PARAM, _TIMEOUT_PARAM],
    )


class SerialComWidget(QtWidgets.QFrame):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item

        self.setObjectName("SerialComWidget")
        self.setStyleSheet(
            "QFrame#SerialComWidget{background:#0f1216;border:1px solid #334155;border-radius:0px;}"
            "QLabel{color:#cbd5e1;}"
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:4px 8px;}"
            "QPushButton:hover{background:#334155;}"
            "QComboBox,QSpinBox{background:#111827;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:2px 6px;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e2e8f0;selection-background-color:#1d4ed8;}"
        )

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        port_row = QtWidgets.QHBoxLayout()
        port_row.setContentsMargins(0, 0, 0, 0)
        port_row.setSpacing(6)
        port_row.addWidget(QtWidgets.QLabel("Port"), 0)

        self._port_combo = QtWidgets.QComboBox()
        self._port_combo.setEditable(True)
        self._port_combo.currentTextChanged.connect(self._on_port_changed)
        port_row.addWidget(self._port_combo, 1)

        self._refresh_btn = QtWidgets.QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._refresh_ports)
        port_row.addWidget(self._refresh_btn, 0)
        root.addLayout(port_row, 0)

        control_row = QtWidgets.QHBoxLayout()
        control_row.setContentsMargins(0, 0, 0, 0)
        control_row.setSpacing(6)
        control_row.addWidget(QtWidgets.QLabel("Baud"), 0)

        self._baud_spin = QtWidgets.QSpinBox()
        self._baud_spin.setRange(1200, 2000000)
        self._baud_spin.setSingleStep(9600)
        self._baud_spin.setValue(_read_baud(node_item))
        self._baud_spin.valueChanged.connect(self._on_baud_changed)
        control_row.addWidget(self._baud_spin, 0)

        control_row.addStretch(1)

        self._ping_btn = QtWidgets.QPushButton("Ping")
        self._ping_btn.clicked.connect(self._on_ping_clicked)
        control_row.addWidget(self._ping_btn, 0)
        root.addLayout(control_row, 0)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        root.addWidget(self._status, 0)

        self._refresh_ports()
        if not serial_runtime_available():
            self._set_status("pyserial is not installed. Press Ping to install it.")

    def sizeHint(self):
        return QtCore.QSize(SERIAL_COM_BODY_W, SERIAL_COM_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(280, 96)

    def _set_status(self, text: str) -> None:
        self._status.setText(str(text or ""))

    def _refresh_ports(self) -> None:
        current = _read_port(self._node_item) or str(self._port_combo.currentText() or "").strip()
        ports = _available_ports()
        if current and current not in ports:
            ports.insert(0, current)
        self._port_combo.blockSignals(True)
        try:
            self._port_combo.clear()
            self._port_combo.addItems(ports)
            self._port_combo.setCurrentText(current)
        finally:
            self._port_combo.blockSignals(False)
        if not serial_runtime_available():
            self._set_status("pyserial is not installed. Press Ping to install it.")
        elif ports:
            self._set_status("")
        else:
            self._set_status("No serial ports found.")

    def _on_port_changed(self, text: str) -> None:
        _set_param_value(self._node_item, _PORT_PARAM, str(text or "").strip(), notify_scene=True)

    def _on_baud_changed(self, value: int) -> None:
        _set_param_value(self._node_item, _BAUD_PARAM, str(max(1200, int(value))), notify_scene=True)

    def _on_ping_clicked(self) -> None:
        if not self._ensure_serial_runtime():
            return
        config = SerialTargetConfig(
            node_name=str(getattr(getattr(self._node_item, "model", None), "name", "") or "Serial COM"),
            port=_read_port(self._node_item),
            baud_rate=_read_baud(self._node_item),
            timeout_ms=_read_timeout_ms(self._node_item),
        )
        sessions, message = open_pico_sessions([config])
        if not sessions:
            self._set_status(message)
            return
        close_pico_sessions(sessions)
        self._set_status(f"PONG from {config.port}.")

    def _ensure_serial_runtime(self) -> bool:
        ready, message = prompt_install_pyserial(self)
        if not ready:
            if message:
                self._set_status(message)
            if not serial_runtime_available():
                return False
        return True


def render_node_body(node_item, y_cursor: int) -> int:
    body = SerialComWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    min_h = max(
        int(body.sizeHint().height()),
        int(body.minimumSizeHint().height()),
        int(body.minimumHeight() or 0),
    )
    try:
        available_h = int(
            max(
                float(min_h),
                float(node_item.height) - float(y_cursor) - float(getattr(node_item, "_PADDING", 0.0)),
            )
        )
    except Exception:
        available_h = int(min_h)
    proxy.resize(node_item.width, available_h)
    try:
        proxy.setPreferredSize(node_item.width, available_h)
    except Exception:
        pass
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + available_h


SERIAL_COM_SPEC = Spec(
    stripe_color="#14b8a6",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
