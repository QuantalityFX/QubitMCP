# edugraph_app.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# Qt imports with fallback
try:
    from PySide6 import QtWidgets, QtGui, QtCore
except ImportError:
    from PySide2 import QtWidgets, QtGui, QtCore

# Create the app first
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

# App/window icon
icon_path = Path(__file__).parent / "icons" / "EchoMatrixMCP_Icon_s.png"
if icon_path.exists():
    app.setWindowIcon(QtGui.QIcon(str(icon_path)))
else:
    # Non-fatal, but useful in console if you launched with python.exe
    print(f"[EduGraph] Icon not found: {icon_path}")

# Windows taskbar identity (groups as its own app)
if sys.platform.startswith("win"):
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("QuantalityFX.EchoGraph")
    except Exception:
        pass

# Dark theme for standalone
def apply_dark(app):
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window,        QtGui.QColor("#1a1f24"))
    pal.setColor(QtGui.QPalette.WindowText,    QtGui.QColor("#e6edf3"))
    pal.setColor(QtGui.QPalette.Base,          QtGui.QColor("#0f1216"))
    pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#141820"))
    pal.setColor(QtGui.QPalette.ToolTipBase,   QtGui.QColor("#0f1216"))
    pal.setColor(QtGui.QPalette.ToolTipText,   QtGui.QColor("#e6edf3"))
    pal.setColor(QtGui.QPalette.Text,          QtGui.QColor("#e6edf3"))
    pal.setColor(QtGui.QPalette.Button,        QtGui.QColor("#20242b"))
    pal.setColor(QtGui.QPalette.ButtonText,    QtGui.QColor("#e6edf3"))
    pal.setColor(QtGui.QPalette.BrightText,    QtGui.QColor("#ffffff"))
    pal.setColor(QtGui.QPalette.Link,          QtGui.QColor("#60a5fa"))
    pal.setColor(QtGui.QPalette.Highlight,     QtGui.QColor("#22c55e"))
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#0a0f0a"))
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text,       QtGui.QColor("#808891"))
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor("#808891"))
    app.setPalette(pal)

apply_dark(app)

# Import the shelf script (it builds and shows the window)
import echograph_shelf

# Ensure taskbar-visible (force Qt.Window in case shelf used a tool flag)
try:
    win = getattr(echograph_shelf, "_WINDOW", None)
    if win is not None:
        win.setParent(None)
        try:
            win.setWindowFlag(QtCore.Qt.Window, True)
        except Exception:
            win.setWindowFlags(QtCore.Qt.Window)
        app.setQuitOnLastWindowClosed(True)
        win.show(); win.raise_(); win.activateWindow()
except Exception as e:
    # If you run with python.exe, you'll see this
    print("[EchoGraph] Failed to show window:", e)

sys.exit(app.exec())
