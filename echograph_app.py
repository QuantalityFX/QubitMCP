# echograph_app.py
from pathlib import Path
import sys

# Ensure local imports (icons, echograph_shelf, nodes/*) resolve
sys.path.insert(0, str(Path(__file__).parent))

# --- EchoGraph: stdout/stderr -> per-day temp log (works with pythonw) ---
import os, time, tempfile, atexit, traceback

def _init_logging():
    log_dir = os.path.join(tempfile.gettempdir(), "EchoGraph")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"echograph_{time.strftime('%Y%m%d')}.log")

    # Line-buffered so writes appear quickly
    f = open(log_path, mode="a", encoding="utf-8", buffering=1)
    sys.stdout = f
    sys.stderr = f

    print(f"[{time.strftime('%H:%M:%S')}] --- EchoGraph started ---")

    # Log any uncaught exceptions too
    def _excepthook(exc_type, exc, tb):
        print("\n[EchoGraph] Uncaught exception:")
        traceback.print_exception(exc_type, exc, tb)
    sys.excepthook = _excepthook

    @atexit.register
    def _close_log():
        try:
            print(f"[{time.strftime('%H:%M:%S')}] --- EchoGraph exit ---")
        except Exception:
            pass
        try:
            f.flush(); f.close()
        except Exception:
            pass

    return log_path

_LOG_PATH = _init_logging()

# Qt imports with fallback
try:
    from PySide6 import QtWidgets, QtGui, QtCore
except ImportError:
    from PySide2 import QtWidgets, QtGui, QtCore

# Create the app first
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

# App/window icon
icon_path = Path(__file__).parent / "icons" / "QubitMCP_Icon_s.png"
if icon_path.exists():
    app.setWindowIcon(QtGui.QIcon(str(icon_path)))
else:
    print(f"[EchoGraph] Icon not found: {icon_path}")

# Windows taskbar identity (groups as its own app)
if sys.platform.startswith("win"):
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("QuantalityFX.EchoGraph")
    except Exception:
        pass

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
    print("[EchoGraph] Failed to show window:", e)

# PySide6 uses exec(); PySide2 uses exec_()
try:
    rc = app.exec()
except AttributeError:
    rc = app.exec_()
sys.exit(rc)
