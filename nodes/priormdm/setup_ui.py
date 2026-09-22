"""Node-owned onboarding. Checking never installs; the Install button opts in."""
from __future__ import annotations

import codecs
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import weakref

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from echograph.motion.backends.priormdm.backend import PROJECT_ROOT, PriorMDMConfig

_INSTALLING = {}
_SESSIONS = {}


def repository_key(repository: Path) -> str:
    return os.path.normcase(str(repository.resolve()))


def active_installation(repository: Path):
    return _INSTALLING.get(repository_key(repository))


def conflicting_installation(config: PriorMDMConfig):
    for session in _INSTALLING.values():
        if any(repository_key(left) == repository_key(right) for left, right in (
                (config.repository, session.config.repository),
                (config.cache_directory, session.config.cache_directory),
                (config.dataset, session.config.dataset))):
            return session
    return None


def bootstrap_python() -> Path:
    candidate = PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if candidate.is_file():
        return candidate
    current = Path(sys.executable)
    if current.name.lower() == "pythonw.exe":
        return current.with_name("python.exe")
    if current.name.lower().startswith("python"):
        return current
    raise ValueError("The setup Python could not be found. Restore Qubit Field's .venv first.")


class SetupSession(QtCore.QObject):
    log_directory = PROJECT_ROOT / "logs/motion/priormdm/setup"
    changed = QtCore.Signal()
    output = QtCore.Signal(str)
    finished = QtCore.Signal(object)

    def __init__(self, config: PriorMDMConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.report = {}
        self.running = False
        self.installing = False
        self.cancelled = False
        self.log_text = ""
        self.log_path = None
        self.dialog = None
        self._log = None
        self._settled = True
        self.process = QtCore.QProcess(self)
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self._timeout = QtCore.QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self.cancel)
        application = QtCore.QCoreApplication.instance()
        if application:
            application.aboutToQuit.connect(self.shutdown)

    def command(self, report_path: Path, install: bool) -> list[str]:
        cfg = self.config
        command = [str(bootstrap_python()), "-u", "-X", "utf8", "-m", "echograph.motion.backends.priormdm.installer",
                   "--repository", str(cfg.repository), "--python", str(cfg.python),
                   "--dataset", str(cfg.dataset), "--download-cache", str(cfg.cache_directory),
                   "--check-runtime", "--report", str(report_path)]
        if cfg.checkpoint:
            command += ["--checkpoint", str(cfg.checkpoint)]
        if install:
            command += ["--clone", "--install", "--download-assets"]
        return command

    def start(self, *, install=False) -> None:
        if self.running:
            return
        other = conflicting_installation(self.config)
        if other is not None and other is not self:
            raise ValueError("PriorMDM setup is already running for these folders. Open the active Setup window.")
        run_dir = self.log_directory / uuid.uuid4().hex
        self.report_path = run_dir / "report.json"
        command = self.command(self.report_path, install)
        run_dir.mkdir(parents=True, exist_ok=False)
        self.log_path = run_dir / "setup.log"
        self._log = self.log_path.open("x", encoding="utf-8")
        self.log_text = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.running, self.installing, self.cancelled, self._settled = True, install, False, False
        if install:
            _INSTALLING[repository_key(self.config.repository)] = self
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        environment.remove("PYTHONHOME")
        environment.remove("PYTHONPATH")
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONNOUSERSITE", "1")
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(PROJECT_ROOT))
        self.changed.emit()
        self.process.start(command[0], command[1:])
        if not install:
            self._timeout.start(120000)

    def _read(self):
        text = self._decoder.decode(bytes(self.process.readAllStandardOutput()))
        if text:
            self.log_text = (self.log_text + text)[-30000:]
            if self._log:
                self._log.write(text)
                self._log.flush()
            self.output.emit(text)

    def _error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self._settle({"ready": False, "failed": True, "error": "Could not start setup: " + self.process.errorString()})

    def _finished(self, code, status):
        if self._settled:
            return
        self._read()
        if self.cancelled:
            report = {"ready": False, "cancelled": True, "error": "Setup cancelled. You can retry from this window."}
        else:
            try:
                report = json.loads(self.report_path.read_text(encoding="utf-8"))
                if not isinstance(report, dict) or report.get("schema_version") != 1:
                    raise ValueError("Invalid setup report")
            except (OSError, ValueError):
                report = {"ready": False, "failed": True, "error": f"Setup stopped (exit {code}). See the log below."}
            if status != QtCore.QProcess.NormalExit or code not in (0, 1):
                report.update(ready=False, failed=True)
                report.setdefault("error", f"Setup stopped (exit {code}). See the log below.")
        self._settle(report)

    def _settle(self, report):
        if self._settled:
            return
        self._settled = True
        self._timeout.stop()
        self.running = False
        key = repository_key(self.config.repository)
        if _INSTALLING.get(key) is self:
            del _INSTALLING[key]
        if self._log:
            self._log.close()
            self._log = None
        self.report = report
        if not report.get("failed") and not report.get("cancelled"):
            paths = {key: Path(report[key]) for key in ("python", "dataset", "checkpoint", "download_cache") if report.get(key)}
            self.config = replace(self.config, **paths)
        self.finished.emit(report)
        self.changed.emit()

    def cancel(self):
        if not self.running:
            return
        self.cancelled = True
        # pip/gdown/git are children of the setup script: stopping only the parent
        # would leave them installing after the UI reported cancellation.
        pid = int(self.process.processId())
        if os.name == "nt" and pid:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                pass
        self.process.kill()

    def shutdown(self):
        if self.running:
            self.cancel()
            self.process.waitForFinished(1000)


class SetupDialog(QtWidgets.QDialog):
    def __init__(self, session: SetupSession, node, scene=None):
        super().__init__()
        self.session, self.node, self.graph_scene = session, node, scene
        self.setWindowTitle("Set up PriorMDM")
        self.resize(760, 580)
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "PriorMDM needs its AI dependencies and model files before it can generate animations. "
            "Click Install PriorMDM to set everything up. Downloads can be several gigabytes.\n\n"
            "Setup installs the dependencies, model and the two small HumanML3D normalization files "
            "in shared app folders. Downloads are cached and reused across projects. "
            "Default folders are ready to use; Change folders lets you choose another location. "
            "The full training dataset is not needed.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.status = QtWidgets.QLabel()
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.requirements = QtWidgets.QPlainTextEdit()
        self.requirements.setReadOnly(True)
        self.requirements.setMaximumHeight(115)
        layout.addWidget(self.requirements)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        self.install_button = QtWidgets.QPushButton("Install PriorMDM")
        self.install_button.clicked.connect(lambda: self._start(True))
        layout.addWidget(self.install_button)
        self.storage = QtWidgets.QLabel()
        self.storage.setWordWrap(True)
        self.storage.setTextFormat(QtCore.Qt.PlainText)
        self.storage.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.storage)
        self.locations_button = QtWidgets.QPushButton("Change folders… (optional)")
        self.locations_button.clicked.connect(self._change_locations)
        layout.addWidget(self.locations_button)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(700)
        self.log.setPlainText(session.log_text)
        layout.addWidget(self.log, 1)
        buttons = QtWidgets.QHBoxLayout()
        self.recheck = QtWidgets.QPushButton("Recheck")
        self.recheck.clicked.connect(lambda: self._start(False))
        self.cancel_button = QtWidgets.QPushButton("Cancel setup")
        self.cancel_button.clicked.connect(session.cancel)
        self.close_button = QtWidgets.QPushButton("Later")
        self.close_button.clicked.connect(self.hide)
        for button in (self.recheck, self.cancel_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        session.output.connect(self._append)
        session.changed.connect(self.refresh)
        self.refresh()

    def _change_locations(self):
        from .locations_ui import LocationsDialog
        self.locations_dialog = LocationsDialog(self)
        self.locations_dialog.show()

    def apply_locations(self, config, *, remember):
        from echograph.motion.backends.priormdm.preferences import save_locations
        from .spec import set_value
        job = getattr(self.node, "_motion_job", None)
        if self.session.running or (job and job.running) or conflicting_installation(config):
            raise ValueError("Wait for setup or generation to finish before changing folders.")
        if remember:
            save_locations(config)
        for key in ("repository", "python", "dataset", "download_cache", "checkpoint", "output_root"):
            path = config.cache_directory if key == "download_cache" else getattr(config, key)
            set_value(self.node, self.graph_scene, key, str(path) if path is not None else "")
        old_session = self.session
        session = session_for_node(self.node, self.graph_scene)
        if session is not old_session:
            old_session.output.disconnect(self._append)
            old_session.changed.disconnect(self.refresh)
            if old_session.dialog is self:
                old_session.dialog = None
            if session.dialog is not None and session.dialog is not self:
                session.dialog.hide()
            self.session = session
            session.dialog = self
            session.output.connect(self._append)
            session.changed.connect(self.refresh)
            self.cancel_button.clicked.disconnect(old_session.cancel)
            self.cancel_button.clicked.connect(session.cancel)
        # Open controls reconnect to this node's new session and refresh their paths.
        old_session.changed.emit()
        self.refresh()
        self._start(False)

    def _start(self, install):
        job = getattr(self.node, "_motion_job", None)
        if job and job.running:
            self.status.setText("Wait for generation to finish before changing the environment.")
            return
        try:
            self.session.start(install=install)
            self.log.clear()
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    @QtCore.Slot(str)
    def _append(self, text):
        cursor = self.log.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(text)
        self.log.setTextCursor(cursor)

    @QtCore.Slot()
    def refresh(self):
        session = self.session
        report = session.report
        if session.running:
            message = "Installing dependencies and downloading models…" if session.installing else "Checking PriorMDM setup…"
        elif report.get("error"):
            message = report["error"]
        elif report.get("ready"):
            message = "PriorMDM is ready. Close this window to generate an animation."
        elif report.get("dependencies_ready"):
            message = "Some required files are missing. Click Install PriorMDM to complete setup using cached downloads where available."
        else:
            message = "PriorMDM needs setup. Click Install PriorMDM to begin."
        self.status.setText(message)
        self.requirements.setPlainText("\n".join(report.get("missing", [])))
        self.progress.setVisible(session.running)
        self.install_button.setEnabled(not session.running)
        self.storage.setText(f"PriorMDM installation: {session.config.repository}\n"
                             f"Shared download cache: {session.config.cache_directory}\n"
                             f"Normalization files: {session.config.dataset}")
        job = getattr(self.node, "_motion_job", None)
        self.locations_button.setEnabled(not session.running and not (job and job.running))
        self.recheck.setEnabled(not session.running)
        self.cancel_button.setEnabled(session.running)
        self.close_button.setText("Close (setup continues)" if session.running else "Close" if report.get("ready") else "Later")


def session_for_node(node, scene=None) -> SetupSession:
    from .spec import config_for_node, set_value
    config = config_for_node(node)
    active = active_installation(config.repository)
    session = active if active is not None and active.config == config else getattr(node, "_prior_setup_session", None)
    if session is not None and not session.running and session.config != config:
        session = None
    if session is None:
        cached = _SESSIONS.get(config)
        if cached is not None and cached.config == config:
            session = cached
    if session is None or repository_key(session.config.repository) != repository_key(config.repository):
        session = SetupSession(config, QtWidgets.QApplication.instance())
        _SESSIONS[config] = session
    elif not session.running:
        session.config = config
    if getattr(node, "_prior_setup_session", None) is not session:
        node._prior_setup_session = session

        def apply_report(report):
            if getattr(node, "_prior_setup_session", None) is not session:
                return  # A previous shared session must not overwrite this node's new paths.
            if report.get("failed") or report.get("cancelled"):
                return
            for key in ("checkpoint", "python", "dataset", "download_cache"):
                if report.get(key):
                    set_value(node, scene, key, report[key])
            if report.get("dependencies_ready") and report.get("runtime", {}).get("cuda_available") is False:
                set_value(node, scene, "device", "cpu")
            if report.get("ready"):
                set_value(node, scene, "last_status", "PriorMDM setup complete")

        session.finished.connect(apply_report)
    return session


def show_setup(node, scene=None, *, session=None):
    session = session or session_for_node(node, scene)
    if session.dialog is None:
        session.dialog = SetupDialog(session, node, scene)
    session.dialog.node, session.dialog.graph_scene = node, scene
    session.dialog.show()
    session.dialog.raise_()
    session.dialog.activateWindow()
    if not session.running:
        session.dialog._start(False)
    return session.dialog


def schedule_setup_offer(item):
    """Defer until the new NodeItem has been attached to its graph scene."""
    node = item.model
    if getattr(node, "_prior_setup_offer_scheduled", False):
        return
    node._prior_setup_offer_scheduled = True
    reference = weakref.ref(item)

    def check_after_creation():
        current = reference()
        if current is None or not isValid(current):
            return
        scene = current.scene()
        if scene is None:
            node._prior_setup_offer_scheduled = False
            return
        try:
            session = session_for_node(node, scene)

            def offer(report):
                session.finished.disconnect(offer)
                if not report.get("ready") and isValid(current) and current.scene() is not None:
                    # The check already completed: show it without starting another.
                    if session.dialog is None:
                        session.dialog = SetupDialog(session, node, scene)
                    session.dialog.node, session.dialog.graph_scene = node, scene
                    session.dialog.show()
                    session.dialog.raise_()
            if session.running and session.installing:
                show_setup(node, scene, session=session)
            else:
                session.finished.connect(offer)
                if not session.running:
                    session.start()
        except (OSError, ValueError) as exc:
            from .spec import set_value
            set_value(node, scene, "last_status", f"Setup check failed: {exc}. Use Setup / dependencies to retry.")

    QtCore.QTimer.singleShot(0, check_after_creation)
