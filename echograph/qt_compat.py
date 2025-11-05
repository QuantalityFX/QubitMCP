# PySide6 preferred, fallback to PySide2
from typing import Optional
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    try:
        from shiboken6 import wrapInstance
    except Exception:
        wrapInstance = None
    QT_IS_6 = True
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    try:
        from shiboken2 import wrapInstance
    except Exception:
        wrapInstance = None
    QT_IS_6 = False

# QAction/QShortcut cross-compat
try:
    QAction = QtGui.QAction
except AttributeError:
    QAction = QtWidgets.QAction

try:
    QShortcut = QtGui.QShortcut
except AttributeError:
    QShortcut = QtWidgets.QShortcut

QKeySequence = QtGui.QKeySequence

def _qexec(dlg: QtWidgets.QDialog) -> int:
    try:
        return dlg.exec()
    except AttributeError:
        return dlg.exec_()
