"""Thread lifetime is independent of node widgets, which are often rebuilt."""
import threading

from PySide6 import QtCore


_ACTIVE = set()


class ImageTask(QtCore.QThread):
    progress = QtCore.Signal(object)
    result = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, operation):
        super().__init__(QtCore.QCoreApplication.instance())
        self.operation = operation
        self.cancel_event = threading.Event()
        self.finished.connect(self._release)
        QtCore.QCoreApplication.instance().aboutToQuit.connect(self._shutdown)

    def start(self):
        _ACTIVE.add(self)
        super().start()

    def run(self):
        try:
            self.result.emit(self.operation(self.cancel_event, self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))

    def cancel(self):
        self.cancel_event.set()

    def _shutdown(self):
        self.cancel()
        self.wait(40000)

    def _release(self):
        _ACTIVE.discard(self)
        self.deleteLater()
