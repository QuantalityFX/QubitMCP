"""Qt lifetime and installation controls for the independently managed worker."""
from PySide6 import QtCore

from .config import PROJECT_ROOT
from .native_backend import NativeService
from .native_config import saved_native_config
from .runtime import RuntimeManager, setup_python


class NativeRuntimeManager(RuntimeManager):
    worker_output = QtCore.Signal(str)
    worker_stopped = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.config = saved_native_config()
        self.service = NativeService(log=self.worker_output.emit, stopped=self.worker_stopped.emit)
        self.worker_output.connect(self.append)
        self.worker_stopped.connect(self._native_stopped)

    @property
    def server_running(self):
        return hasattr(self, "service") and self.service.running

    def install(self, variant, license_use):
        if variant != "bf16":
            raise ValueError("Only the official BF16 checkpoint is enabled for native setup.")
        if self.installing or self.server_running or self.generating:
            raise RuntimeError("Stop the native backend and finish active jobs before setup.")
        command = setup_python() + ["-u", "-X", "utf8", "-m", "echograph.image_generation.native_installer",
            "--runtime", str(self.config.runtime), "--model-directory", str(self.config.model),
            "--accept-model-license", "--license-use", license_use]
        self._prepare("setup", self.setup)
        self.setup.setWorkingDirectory(str(PROJECT_ROOT))
        self.setup.start(command[0], command[1:])
        self.changed.emit()

    def start_server(self, low_memory=False):
        if self.installing:
            raise RuntimeError("Wait for native setup to finish.")
        if self.server_running:
            return
        # Release locks belonging to an already exited worker before restarting.
        self._native_stopped()
        self._prepare("server", self.server)
        try:
            self.service.start(self.config)
        except Exception:
            if "server" in self._logs:
                self._logs.pop("server").close()
            if "server" in self._locks:
                self._locks.pop("server").unlock()
            raise
        self.append("Native worker launched. Waiting for its connection and backend check...\n")
        self.changed.emit()

    def append(self, text):
        if "server" in self._logs:
            self._logs["server"].write(text)
            self._logs["server"].flush()
        super().append(text)

    def stop_server(self):
        if not self.server_running or self.stopping:
            return
        if self.generating:
            raise RuntimeError("Finish or cancel active Qwen Image jobs before stopping the shared native backend.")
        self.stopping = True
        self.changed.emit()
        self.append("Stopping native worker and releasing its memory...\n")
        self.service.stop()

    def _native_stopped(self):
        # A queued notification from the previous process cannot stop a replacement.
        if self.server_running:
            return
        if "server" in self._logs:
            self._logs.pop("server").close()
        if "server" in self._locks:
            self._locks.pop("server").unlock()
        self.stopping = False
        self.server_stopped.emit()
        self.changed.emit()

    def shutdown(self):
        # The parent owns the installer QProcess. The worker is owned by Popen.
        if hasattr(self, "service"):
            self.service.stop()
        super().shutdown()


_MANAGER = None


def native_runtime_manager():
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = NativeRuntimeManager()
    return _MANAGER
