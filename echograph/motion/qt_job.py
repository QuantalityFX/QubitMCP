"""Asynchronous execution shared by motion-model UIs; no model imports here."""
from __future__ import annotations

import codecs
import json

from PySide6 import QtCore

from .api import MotionGenerationService
from .types import GenerationPlan


class MotionJob(QtCore.QObject):
    output = QtCore.Signal(str)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, service: MotionGenerationService, plan: GenerationPlan, parent=None):
        super().__init__(parent)
        self.service, self.plan = service, plan
        self.running = False
        self.cancelled = False
        self.log_text = ""
        self._settled = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._log = None
        self._stages = plan.stages or (plan,)
        self._stage_index = 0
        self.process = QtCore.QProcess(self)
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.process.setWorkingDirectory(str(plan.cwd))
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        # Do not inherit another Python application's module paths.
        environment.remove("PYTHONPATH")
        environment.remove("PYTHONHOME")
        for key, value in plan.environment.items():
            environment.insert(key, value)
        self.process.setProcessEnvironment(environment)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        application = QtCore.QCoreApplication.instance()
        if application:
            application.aboutToQuit.connect(self.shutdown)

    def start(self) -> None:
        if self.running or self._settled:
            raise RuntimeError("A motion job can only be started once.")
        self._log = (self.plan.run_dir / "process.log").open("x", encoding="utf-8")
        self.running = True
        self._start_stage()

    def _start_stage(self):
        stage = self._stages[self._stage_index]
        self.process.setWorkingDirectory(str(stage.cwd))
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        environment.remove("PYTHONPATH")
        environment.remove("PYTHONHOME")
        for key, value in stage.environment.items():
            environment.insert(key, value)
        self.process.setProcessEnvironment(environment)
        self.process.start(stage.command[0], list(stage.command[1:]))

    def cancel(self) -> None:
        if self.running:
            self.cancelled = True
            # Worker does not spawn children. kill() also works for Windows console
            # Python processes that do not handle QProcess.terminate's WM_CLOSE.
            self.process.kill()

    def shutdown(self) -> None:
        if self.running:
            self.cancel()
            self.process.waitForFinished(1000)

    def _read(self) -> None:
        text = self._decoder.decode(bytes(self.process.readAllStandardOutput()))
        if text:
            self.log_text = (self.log_text + text)[-24000:]
            if self._log:
                self._log.write(text)
                self._log.flush()
            self.output.emit(text)

    def _error(self, error) -> None:
        if error == QtCore.QProcess.FailedToStart:
            self._fail(f"Could not start inference: {self.process.errorString()}")

    def _close(self, status: str, message: str = "") -> None:
        self._settled = True
        self.running = False
        if self._log:
            self._log.close()
            self._log = None
        try:
            (self.plan.run_dir / "status.json").write_text(
                json.dumps({"status": status, "message": message}, indent=2), encoding="utf-8")
        except OSError as exc:
            self.output.emit(f"Could not save job status: {exc}\n")

    def _fail(self, message: str) -> None:
        if self._settled:
            return
        self._close("cancelled" if self.cancelled else "failed", message)
        self.failed.emit(message)

    def _finished(self, code: int, status) -> None:
        if self._settled:
            return
        self._read()
        if self.cancelled:
            self._fail("Generation cancelled. Partial files are kept in the run folder.")
            return
        if code != 0 or status != QtCore.QProcess.NormalExit:
            self._fail(f"Inference failed (exit {code}). See the log: {self.plan.run_dir / 'process.log'}")
            return
        if self._stage_index + 1 < len(self._stages):
            self._stage_index += 1
            self.output.emit("Body motion complete; generating hand articulation…\n")
            self._start_stage()
            return
        try:
            result = self.service.collect(self.plan)
        except Exception as exc:
            self._fail(f"Motion conversion failed: {exc}")
            return
        self._close("complete")
        self.completed.emit(result)
