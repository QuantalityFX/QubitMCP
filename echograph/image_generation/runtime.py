"""One shared, loopback-only backend process for all Qwen Image nodes."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import uuid

from PySide6 import QtCore
from shiboken6 import isValid

from .config import PROJECT_ROOT, saved_config, storage_root


def setup_python():
    if os.name == "nt" and shutil.which("py"):
        return [shutil.which("py"), "-3.12"]
    if not getattr(sys, "frozen", False):
        return [str(Path(sys.executable).with_name("python.exe")) if os.name == "nt" else sys.executable]
    python = shutil.which("python")
    if python:
        return [python]
    raise RuntimeError("Install Python 3.12 and Git to set up the local Qwen runtime.")


class RuntimeManager(QtCore.QObject):
    changed = QtCore.Signal()
    output = QtCore.Signal(str)
    setup_finished = QtCore.Signal(bool)
    server_stopped = QtCore.Signal()

    def __init__(self):
        super().__init__(QtCore.QCoreApplication.instance())
        self.config = saved_config()
        self.setup = QtCore.QProcess(self)
        self.server = QtCore.QProcess(self)
        self.log_text = ""
        self.log_path = None
        self._logs = {}
        self._locks = {}
        self._jobs = set()
        self.stopping = False
        for name, process in (("setup", self.setup), ("server", self.server)):
            process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            process.readyReadStandardOutput.connect(lambda n=name, p=process: self._read(n, p))
            process.finished.connect(lambda code, status, n=name: self._finished(n, code, status))
            process.errorOccurred.connect(lambda error, n=name, p=process: self._error(n, p, error))
        self.server.started.connect(self._server_started)
        QtCore.QCoreApplication.instance().aboutToQuit.connect(self.shutdown)

    @property
    def installing(self):
        return self.setup.state() != QtCore.QProcess.NotRunning

    @property
    def server_running(self):
        return self.server.state() != QtCore.QProcess.NotRunning

    @property
    def generating(self):
        # A queued finished notification can be delayed by widget rebuilds or
        # sender deletion. Completed workers must not leave Stop/Setup blocked.
        self._jobs.difference_update(task for task in tuple(self._jobs)
            if not isValid(task) or (isinstance(task, QtCore.QThread) and task.isFinished()))
        return bool(self._jobs)

    def track_job(self, task):
        self._jobs.add(task)
        task.finished.connect(self._job_finished)
        self.changed.emit()

    @QtCore.Slot()
    def _job_finished(self):
        self._jobs.discard(self.sender())
        self.changed.emit()

    def _server_started(self):
        if self.stopping:
            self._terminate_server()
        self.changed.emit()

    def append(self, text):
        self.log_text = (self.log_text + text)[-60000:]
        self.output.emit(text)

    def _read(self, name, process):
        text = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            if name in self._logs:
                self._logs[name].write(text)
                self._logs[name].flush()
            self.append(text)

    def _finished(self, name, code, status):
        self._read(name, self.setup if name == "setup" else self.server)
        if name in self._logs:
            self._logs.pop(name).close()
        if name in self._locks:
            self._locks.pop(name).unlock()
        self.append(f"{name.capitalize()} exited ({code}).\n")
        if name == "server":
            self.stopping = False
            self.server_stopped.emit()
        self.changed.emit()
        if name == "setup":
            self.setup_finished.emit(code == 0 and status == QtCore.QProcess.NormalExit)

    def _error(self, name, process, error):
        self.append(f"{name}: {process.errorString()}\n")
        if error == QtCore.QProcess.FailedToStart:
            if name in self._logs:
                self._logs.pop(name).close()
            if name in self._locks:
                self._locks.pop(name).unlock()
            if name == "setup":
                self.setup_finished.emit(False)
            else:
                self.stopping = False
                self.server_stopped.emit()
        self.changed.emit()

    def _prepare(self, name, process):
        # Prevent two app windows from changing the same runtime simultaneously.
        directory = storage_root() / "logs/qwen_image"
        directory.mkdir(parents=True, exist_ok=True)
        import hashlib
        key = hashlib.sha256(str(self.config.runtime.resolve()).lower().encode()).hexdigest()[:16]
        lock = QtCore.QLockFile(str(directory / f"{key}-runtime.lock"))
        if not lock.tryLock(0):
            raise RuntimeError(f"Qwen {name} is already active in another application window.")
        self._locks[name] = lock
        self.log_path = directory / f"{name}-{uuid.uuid4().hex}.log"
        self._logs[name] = self.log_path.open("w", encoding="utf-8")
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            environment.remove(key)
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONNOUSERSITE", "1")
        process.setProcessEnvironment(environment)

    def install(self, variant, license_use):
        if self.installing or self.server_running:
            raise RuntimeError("Stop this app's local backend before installing model resources.")
        command = setup_python() + ["-u", "-X", "utf8", "-m", "echograph.image_generation.installer",
            "--repository", str(self.config.repository), "--runtime", str(self.config.runtime),
            "--variant", variant, "--accept-model-license", "--license-use", license_use]
        self._prepare("setup", self.setup)
        self.setup.setWorkingDirectory(str(PROJECT_ROOT))
        self.setup.start(command[0], command[1:])
        self.changed.emit()

    def start_server(self, low_memory=False):
        if self.installing:
            raise RuntimeError("Wait for setup to finish.")
        if self.server_running:
            return
        if not self.config.python.is_file() or not (self.config.runtime / "main.py").is_file():
            raise RuntimeError("Install the local runtime using Setup first.")
        # Socket binding is only a probe; ComfyUI remains responsible for its bind.
        import socket
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", self.config.port))
            except OSError as exc:
                raise RuntimeError("Port 8188 is in use. Use Check Backend to connect to the existing service.") from exc
        self._prepare("server", self.server)
        command = [str(self.config.python), "-u", "main.py", "--listen", "127.0.0.1",
                   "--port", str(self.config.port), "--disable-auto-launch", "--disable-dynamic-vram", "--reserve-vram", "1"]
        if low_memory:
            command.append("--lowvram")
        self.server.setWorkingDirectory(str(self.config.runtime))
        self.server.start(command[0], command[1:])
        self.append("Starting local backend. First startup can take several minutes; use Check Backend when ready.\n")
        self.changed.emit()

    def stop_server(self):
        # This QProcess belongs to this app. Existing external services are never killed.
        if not self.server_running or self.stopping:
            return
        if self.generating:
            raise RuntimeError("Finish or cancel active Qwen Image jobs before stopping the shared backend.")
        self.stopping = True
        self.append("Stopping local backend and releasing its memory...\n")
        if self.server.processId():
            self._terminate_server()
        self.changed.emit()

    def _terminate_server(self):
        stopping_pid = self.server.processId()
        self.server.terminate()
        QtCore.QTimer.singleShot(5000, lambda: self.server.kill()
            if self.server_running and self.server.processId() == stopping_pid else None)

    def shutdown(self):
        if self.installing:
            if os.name == "nt":
                # Terminate only our installer tree (pip/git descendants included).
                import subprocess
                subprocess.run(["taskkill", "/PID", str(self.setup.processId()), "/T", "/F"],
                               creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True)
            else:
                self.setup.kill()
            self.setup.waitForFinished(5000)
        if self.server_running:
            self.server.kill()
            self.server.waitForFinished(5000)


_MANAGER = None


def runtime_manager():
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = RuntimeManager()
    return _MANAGER
