# echograph/ui/node_icons.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from echograph.qt_compat import QtGui

# Optional icons (pixmaps)
_DB_ICON = None
_LLM_ICON = None
_LLM_SERVER_ICON = None
_APPEND_ICON = None
_NOTE_ICON = None
_LIBRARIAN_ICON = None
_IMPORT_ICON = None
_OUTPUT_ICON = None
_PYTHON_ICON = None
_SCREENGRAB_ICON = None


def _load_pm(rel_icon_name: str):
    try:
        icon_path = Path(__file__).resolve().parents[2] / "icons" / rel_icon_name
        if icon_path.is_file():
            pm = QtGui.QPixmap(str(icon_path))
            if not pm.isNull():
                return pm
    except Exception:
        pass
    return None


def _db_icon():
    global _DB_ICON
    if _DB_ICON is not None:
        return _DB_ICON
    _DB_ICON = _load_pm("Database_Icon.png")
    return _DB_ICON


def _llm_icon():
    global _LLM_ICON
    if _LLM_ICON is not None:
        return _LLM_ICON
    _LLM_ICON = _load_pm("LLM_Icon.png")
    return _LLM_ICON


def _llm_server_icon():
    global _LLM_SERVER_ICON
    if _LLM_SERVER_ICON is not None:
        return _LLM_SERVER_ICON
    _LLM_SERVER_ICON = _load_pm("Server_Icon.png")
    return _LLM_SERVER_ICON


def _append_icon():
    global _APPEND_ICON
    if _APPEND_ICON is not None:
        return _APPEND_ICON
    _APPEND_ICON = _load_pm("append_icon.png")
    return _APPEND_ICON


def _note_icon():
    global _NOTE_ICON
    if _NOTE_ICON is not None:
        return _NOTE_ICON
    _NOTE_ICON = _load_pm("Electric_Pen_Icon.png")
    return _NOTE_ICON


def _librarian_icon():
    global _LIBRARIAN_ICON
    if _LIBRARIAN_ICON is not None:
        return _LIBRARIAN_ICON
    _LIBRARIAN_ICON = _load_pm("librarian_search_Icon.png")
    return _LIBRARIAN_ICON


def _import_icon():
    global _IMPORT_ICON
    if _IMPORT_ICON is not None:
        return _IMPORT_ICON
    _IMPORT_ICON = _load_pm("Import_File_Icon.png")
    return _IMPORT_ICON


def _output_icon():
    global _OUTPUT_ICON
    if _OUTPUT_ICON is not None:
        return _OUTPUT_ICON
    _OUTPUT_ICON = _load_pm("Out_Node_Icon.png")
    return _OUTPUT_ICON


def _python_icon():
    global _PYTHON_ICON
    if _PYTHON_ICON is not None:
        return _PYTHON_ICON
    _PYTHON_ICON = _load_pm("Python_Icon.png")
    return _PYTHON_ICON


def _screengrab_icon():
    global _SCREENGRAB_ICON
    if _SCREENGRAB_ICON is not None:
        return _SCREENGRAB_ICON
    _SCREENGRAB_ICON = _load_pm("screengrab _Icon_s_001.png")
    return _SCREENGRAB_ICON
