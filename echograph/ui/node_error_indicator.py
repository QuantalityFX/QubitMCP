"""Compact node feedback with selectable details in a separate window."""
from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid


def short_message(message, limit=180):
    lines = str(message or "").splitlines()
    line = lines[0] if lines else ""
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


class ErrorIndicator(QtWidgets.QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NodeErrorIndicator")
        self.setText("!")
        self.setFixedSize(24, 24)
        self.setToolTip("Show error details")
        self.setAccessibleName("Show error details")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet("QToolButton { color:#ef4444; border:2px solid #ef4444;"
                           " border-radius:12px; font-weight:bold; font-size:16px; background:#1e1014; }"
                           "QToolButton:hover { background:#451a20; }")
        self.summary = self.details = ""
        self.dialog = None
        self.hide()
        self.clicked.connect(self.show_details)

    def set_error(self, summary="", details=""):
        self.summary = short_message(summary)
        self.details = str(details or summary or "")
        self.setVisible(bool(self.details))

    def show_details(self):
        if not self.details:
            return
        if self.dialog is not None and isValid(self.dialog):
            editor = self.dialog.findChild(QtWidgets.QPlainTextEdit, "NodeErrorDetails")
            summary = self.dialog.findChild(QtWidgets.QLabel, "NodeErrorSummary")
            if editor.toPlainText() != self.details:
                editor.setPlainText(self.details)
                summary.setText(self.summary)
            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        parent = self.window()
        proxy = parent.graphicsProxyWidget()
        if proxy is not None and proxy.scene() is not None and proxy.scene().views():
            parent = proxy.scene().views()[0].window()
        dialog = QtWidgets.QDialog(parent)
        dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dialog.setWindowTitle("Error details")
        dialog.resize(680, 420)
        layout = QtWidgets.QVBoxLayout(dialog)
        summary = QtWidgets.QLabel(self.summary)
        summary.setObjectName("NodeErrorSummary")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        text = QtWidgets.QPlainTextEdit(self.details)
        text.setObjectName("NodeErrorDetails")
        text.setReadOnly(True)
        layout.addWidget(text)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        self.dialog = dialog
        dialog.show()
