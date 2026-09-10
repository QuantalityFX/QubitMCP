# echograph_app.py
from pathlib import Path
import os
import sys

# Ensure local imports (icons, echograph_shelf, nodes/*) resolve
sys.path.insert(0, str(Path(__file__).parent))


def _append_qtwebengine_chromium_flags(*flags: str) -> None:
    existing = str(os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS") or "").strip()
    parts = [part for part in existing.split() if part]
    seen = set(parts)
    for flag in flags:
        flag = str(flag or "").strip()
        if flag and flag not in seen:
            parts.append(flag)
            seen.add(flag)
    if parts:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(parts)


_append_qtwebengine_chromium_flags(
    "--autoplay-policy=no-user-gesture-required",
    "--allow-file-access-from-files",
)

# --- EchoGraph: stdout/stderr -> per-day temp log (works with pythonw) ---
from echograph.services import runtime_logging

_LOG_PATH = runtime_logging.init_echo_log()

# Qt imports with fallback
try:
    from PySide6 import QtWidgets, QtGui, QtCore
except ImportError:
    from PySide2 import QtWidgets, QtGui, QtCore

try:
    share_contexts = getattr(QtCore.Qt, "AA_ShareOpenGLContexts", None)
    if share_contexts is not None:
        QtCore.QCoreApplication.setAttribute(share_contexts, True)
except Exception:
    pass

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


def _splash_window_flags():
    try:
        return QtCore.Qt.FramelessWindowHint
    except Exception:
        return QtCore.Qt.FramelessWindowHint


def _create_startup_splash(app_):
    splash_path = Path(__file__).parent / "Doc" / "assets" / "images" / "QubitField_SplashScreen_1.5_001.png"
    if not splash_path.exists():
        return None
    pixmap = QtGui.QPixmap(str(splash_path))
    if pixmap.isNull():
        return None
    try:
        screen = app_.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        max_width = 1040
        max_height = 585
        if available is not None:
            max_width = min(max_width, max(520, int(available.width() * 0.72)))
            max_height = min(max_height, max(300, int(available.height() * 0.72)))
        if pixmap.width() > max_width or pixmap.height() > max_height:
            pixmap = pixmap.scaled(max_width, max_height, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    except Exception:
        pass
    try:
        painted = QtGui.QPixmap(pixmap)
        painter = QtGui.QPainter(painted)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        font = QtGui.QFont("Segoe UI", max(9, int(painted.height() * 0.019)))
        font.setWeight(QtGui.QFont.DemiBold)
        painter.setFont(font)
        text = "Starting Qubit Field..."
        metrics = QtGui.QFontMetrics(font)
        margin_x = max(34, int(painted.width() * 0.045))
        margin_bottom = max(30, int(painted.height() * 0.06))
        text_rect = QtCore.QRect(
            margin_x,
            max(0, painted.height() - margin_bottom - metrics.height() - 8),
            max(1, painted.width() - (margin_x * 2)),
            metrics.height() + 8,
        )
        shadow_rect = text_rect.translated(1, 1)
        painter.setPen(QtGui.QColor(0, 0, 0, 190))
        painter.drawText(shadow_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text)
        painter.setPen(QtGui.QColor("#e6edf3"))
        painter.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text)
        painter.end()
        pixmap = painted
    except Exception:
        pass
    try:
        splash = QtWidgets.QSplashScreen(pixmap, _splash_window_flags())
    except TypeError:
        splash = QtWidgets.QSplashScreen(pixmap)
    splash.setWindowTitle("QubitField")
    splash.show()
    try:
        app_.processEvents()
    except Exception:
        pass
    return splash


startup_splash = _create_startup_splash(app)


def _close_startup_splash() -> None:
    global startup_splash
    splash = startup_splash
    startup_splash = None
    if splash is None:
        return
    try:
        splash.close()
    except Exception:
        pass
    try:
        splash.deleteLater()
    except Exception:
        pass
    try:
        app.processEvents()
    except Exception:
        pass


QtCore.QTimer.singleShot(7000, _close_startup_splash)

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
        if startup_splash is not None:
            _close_startup_splash()
    elif startup_splash is not None:
        _close_startup_splash()
except Exception as e:
    if startup_splash is not None:
        _close_startup_splash()
    print("[QubitField] Failed to show window:", e)

# PySide6 uses exec(); PySide2 uses exec_()
try:
    rc = app.exec()
except AttributeError:
    rc = app.exec_()
sys.exit(rc)
