from pathlib import Path
from .qt_compat import QtGui

APP_TITLE = "EchoGraph"
KEY_BIGEDIT = "Ctrl+B"

LLM_URL = "http://127.0.0.1:7860"
LLM_SCALE_DEFAULT = 0.5
LLM_NODE_W_BASE = 1920
LLM_NODE_H_BASE = 1170
DEFAULT_STRIPE_HEX = "#475569"

def script_dir() -> Path:
    import os
    if "__file__" in globals():
        try:
            return Path(__file__).resolve().parent.parent  # /echograph/ -> project root-ish
        except Exception:
            pass
    try:
        return Path.cwd()
    except Exception:
        return Path.home()

ICON_PATH = script_dir() / "icons" / "EchoMatrixMCP_Icon_s.png"
APP_ICON = QtGui.QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QtGui.QIcon()
