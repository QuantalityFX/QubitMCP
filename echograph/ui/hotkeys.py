# echograph/ui/hotkeys.py
from __future__ import annotations
from echograph.qt_compat import QtCore, QtGui, QtWidgets
from echograph.ui import hotkeys_config

def add_shortcut(widget, action_id: str, fallback_seq: str, callback, *, context=None):
    """
    Bind an action id to a shortcut using hotkeys.json.
    Works across PySide6/PySide2 differences (QShortcut location).
    """
    seq = hotkeys_config.keyseq(action_id, fallback_seq)
    print("[HOTKEYS] BIND:", action_id, "->", seq, "fallback=", fallback_seq, flush=True)
    QShortcut = getattr(QtGui, "QShortcut", None) or getattr(QtWidgets, "QShortcut", None)
    if QShortcut is None:
        raise RuntimeError("QShortcut not found in QtGui or QtWidgets")

    sc = QShortcut(QtGui.QKeySequence(seq), widget)
    sc.setContext(context if context is not None else QtCore.Qt.WidgetWithChildrenShortcut)
    sc.activated.connect(callback)
    return sc


def keep_ref(owner, obj):
    if owner is None or obj is None:
        return obj
    lst = getattr(owner, "_hotkey_refs", None)
    if lst is None:
        lst = []
        setattr(owner, "_hotkey_refs", lst)
    lst.append(obj)
    return obj


class _HotkeyFilter(QtCore.QObject):
    def __init__(self, key, mods, callback, parent=None):
        super().__init__(parent)
        self._key = key
        self._mods = mods
        self._cb = callback

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.KeyPress:
            if (ev.key() == self._key) and (ev.modifiers() & self._mods):
                try:
                    self._cb()
                except Exception:
                    pass
                return True
        return super().eventFilter(obj, ev)


def add_keypress_filter(widget, key, mods, callback):
    hf = _HotkeyFilter(key, mods, callback, widget)
    widget.installEventFilter(hf)
    return hf

def keyseq(action_id: str, fallback_seq: str) -> str:
    # Compatibility wrapper (old code calls hotkeys.keyseq)
    return hotkeys_config.keyseq(action_id, fallback_seq)