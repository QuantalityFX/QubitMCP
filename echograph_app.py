# echograph_app.py
from pathlib import Path
import sys

# Ensure local imports (icons, echograph_shelf, nodes/*) resolve
sys.path.insert(0, str(Path(__file__).parent))

# --- EchoGraph: stdout/stderr -> per-day temp log (works with pythonw) ---
from echograph.services import runtime_logging

_LOG_PATH = runtime_logging.init_echo_log()

# Qt imports with fallback
try:
    from PySide6 import QtWidgets, QtGui, QtCore
except ImportError:
    from PySide2 import QtWidgets, QtGui, QtCore

# Windows taskbar identity (groups under the same pinned launcher/shortcut)
APP_USER_MODEL_ID = "QuantalityFX.QubitField"
if sys.platform.startswith("win"):
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass

# Create the app first
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
app.setApplicationName("QubitField")

# App/window icon
icons_dir = Path(__file__).parent / "icons"
icon_path = icons_dir / "QubitField_Icon.ico"
if not icon_path.exists():
    icon_path = icons_dir / "QubitField_Icon.png"

if icon_path.exists():
    app.setWindowIcon(QtGui.QIcon(str(icon_path)))
else:
    print(f"[QubitField] Icon not found: {icon_path}")

# Dark theme for standalone
def apply_dark(app_):
    app_.setStyle("Fusion")
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
    app_.setPalette(pal)

apply_dark(app)

# Import the shelf script (it builds and shows the window)
import echograph_shelf  # this runs _launch() and sets echograph_shelf._WINDOW

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
    print("[QubitField] Failed to show window:", e)

# PySide6 uses exec(); PySide2 uses exec_()
try:
    rc = app.exec()
except AttributeError:
    rc = app.exec_()
sys.exit(rc)
