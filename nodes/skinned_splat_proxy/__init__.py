from __future__ import annotations

from .spec import KIND_ALIASES, SKINNED_SPLAT_PROXY_SPEC

_DIALOG_PATCH_KEY = "_skinned_splat_proxy_create_dialog_patch_v1"
_HEADER_PATCH_KEY = "_skinned_splat_proxy_header_debug_patch_v1"


def _patch_create_node_dialog() -> None:
    try:
        from echograph.qt_compat import QtWidgets

        if QtWidgets.QApplication.instance() is None:
            return
    except Exception:
        return
    try:
        from echograph.ui import dialogs
    except Exception:
        return

    cls = getattr(dialogs, "CreateNodeDialog", None)
    if cls is None or getattr(cls, _DIALOG_PATCH_KEY, False):
        return

    original_init = getattr(cls, "__init__", None)
    if not callable(original_init):
        return

    def wrapped_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            combo = getattr(self, "kind_edit", None)
            if combo is not None and combo.findText("skinned_splat_proxy") < 0:
                combo.addItem("skinned_splat_proxy")
        except Exception:
            pass

    try:
        cls.__init__ = wrapped_init
        setattr(cls, _DIALOG_PATCH_KEY, True)
    except Exception:
        return


def _patch_header_debug_button() -> None:
    try:
        from echograph.qt_compat import QtWidgets

        if QtWidgets.QApplication.instance() is None:
            return
    except Exception:
        return
    try:
        from echograph.ui import node_item as node_item_mod
    except Exception:
        return

    cls = getattr(node_item_mod, "NodeItem", None)
    if cls is None or getattr(cls, _HEADER_PATCH_KEY, False):
        return

    original = getattr(cls, "_header_debug_button_kind", None)
    if not callable(original):
        return

    def wrapped(self, *args, **kwargs):
        try:
            kind = str(getattr(getattr(self, "model", None), "kind", "") or "").strip().lower()
            if kind in KIND_ALIASES:
                return "skinned_splat_proxy"
        except Exception:
            pass
        try:
            return original(self, *args, **kwargs)
        except Exception:
            return None

    try:
        cls._header_debug_button_kind = wrapped
        setattr(cls, _HEADER_PATCH_KEY, True)
    except Exception:
        return


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in KIND_ALIASES:
        _core.register_spec(kind, SKINNED_SPLAT_PROXY_SPEC)
    _patch_create_node_dialog()
    _patch_header_debug_button()


__all__ = ["register"]
