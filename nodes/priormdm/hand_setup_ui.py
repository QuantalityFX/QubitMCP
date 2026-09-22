"""Explicit HandMDM setup on the node. Opening this dialog never starts setup."""
from __future__ import annotations

import weakref
from PySide6 import QtCore, QtGui, QtWidgets

from echograph.motion.backends.handmdm.backend import HandMDMConfig, ROOT
from echograph.motion.backends.priormdm import PriorMDMConfig
from .setup_ui import SetupSession, bootstrap_python

_SESSION = None
_NODES = []


class HandSetupSession(SetupSession):
    log_directory = ROOT / "logs/motion/handmdm/setup"

    def __init__(self, parent=None):
        hand = HandMDMConfig()
        # Reuse the established subprocess, log, cancellation and shared-install
        # lifecycle. The installer command below owns the HandMDM-specific recipe.
        super().__init__(PriorMDMConfig(repository=hand.repository, python=hand.python,
                                       checkpoint=hand.checkpoint, dataset=hand.repository / "motion_stats"), parent)

    def command(self, report_path, install):
        command = [str(bootstrap_python()), "-u", "-X", "utf8", str(ROOT / "scripts/setup_handmdm.py"),
                   "--application", "--report", str(report_path)]
        if install:
            command += ["--install", "--download-models"]
        return command

    def _finished(self, code, status):
        # The script uses 2 for an incomplete check; the shared session uses 1.
        super()._finished(1 if code == 2 else code, status)


def hand_setup_session():
    global _SESSION
    if _SESSION is None:
        _SESSION = HandSetupSession(QtWidgets.QApplication.instance())
    return _SESSION


class HandSetupDialog(QtWidgets.QDialog):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.setWindowTitle("HandMDM setup")
        self.resize(730, 560)
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("Install HandMDM when you are ready to test hand generation. "
                                "Installation uses its own Python environment and downloads the pretrained model. "
                                "Downloads can be several gigabytes. You can close this window and set it up later.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        location = QtWidgets.QLabel(f"Managed installation: {HandMDMConfig().repository}")
        location.setWordWrap(True)
        location.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(location)
        self.status = QtWidgets.QLabel("Setup deferred. Nothing has been installed by this window.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(self.status)
        self.requirements = QtWidgets.QPlainTextEdit()
        self.requirements.setReadOnly(True)
        self.requirements.setMaximumHeight(120)
        layout.addWidget(self.requirements)
        self.install = QtWidgets.QPushButton("Install HandMDM")
        self.check = QtWidgets.QPushButton("Check existing setup")
        self.cancel = QtWidgets.QPushButton("Cancel")
        self.close_button = QtWidgets.QPushButton("Later")
        row = QtWidgets.QHBoxLayout()
        for widget in (self.install, self.check, self.cancel, self.close_button):
            row.addWidget(widget)
        layout.addLayout(row)
        self.install.clicked.connect(lambda: self._start(True))
        self.check.clicked.connect(lambda: self._start(False))
        self.cancel.clicked.connect(session.cancel)
        self.close_button.clicked.connect(self.close)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(600)
        self.log.setPlainText(session.log_text)
        layout.addWidget(self.log)
        session.output.connect(self._append)
        session.changed.connect(self.refresh)
        self.refresh()

    def _start(self, install):
        if any((node := reference()) is not None and getattr(node, "_motion_job", None)
               and node._motion_job.running for reference in _NODES):
            self.status.setText("Wait for animation generation to finish before checking or installing HandMDM.")
            return
        try:
            self.log.clear()
            self.session.start(install=install)
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
            self.status.setText("Installing HandMDM…" if session.installing else "Checking existing HandMDM files and packages…")
        elif report.get("error"):
            self.status.setText(report["error"])
        elif report.get("ready"):
            self.status.setText("HandMDM resources are ready. Generate a short gesture to test the model.")
        elif report:
            self.status.setText("Setup is incomplete. Install when you are ready to test generation.")
        self.requirements.setPlainText("\n".join(report.get("missing", [])))
        self.install.setEnabled(not session.running)
        self.check.setEnabled(not session.running)
        self.cancel.setEnabled(session.running)
        self.close_button.setText("Close (setup continues)" if session.running else "Close")


def register_node(node):
    _NODES[:] = [reference for reference in _NODES if reference() is not None]
    if not any(reference() is node for reference in _NODES):
        _NODES.append(weakref.ref(node))


def show_hand_setup(node, scene=None):
    register_node(node)
    session = hand_setup_session()
    node._hand_setup_session = session
    if session.dialog is None:
        session.dialog = HandSetupDialog(session)
    session.dialog.show()
    session.dialog.raise_()
    session.dialog.activateWindow()
    return session.dialog
