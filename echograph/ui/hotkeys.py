# echograph/ui/hotkeys.py
from __future__ import annotations

from echograph.qt_compat import QtCore, QtGui, QtWidgets
from echograph.ui.hotkeys_config import load_keymap


_KEYMAP = None


def _keyseq_for(action_id: str, fallback: str) -> str:
    global _KEYMAP
    if _KEYMAP is None:
        _KEYMAP = load_keymap()
    return str(_KEYMAP.get(action_id, fallback) or fallback)


def add_shortcut(widget, action_id: str, fallback_seq: str, callback, *, context=None):
    """
    Bind an action id to a shortcut using hotkeys.json (created on first run).
    """
    seq = _keyseq_for(action_id, fallback_seq)
    sc = QtGui.QShortcut(QtGui.QKeySequence(seq), widget)
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
    """
    Returns the configured key sequence string for an action id.
    """
    return _keyseq_for(action_id, fallback_seq)
