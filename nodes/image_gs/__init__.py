from __future__ import annotations

from .spec import IMAGE_GS_SPLAT_SPEC, KIND_ALIASES

_DIALOG_PATCH_KEY = "_image_gs_splat_create_dialog_patch_v1"
_HEADER_PATCH_KEY = "_image_gs_splat_header_debug_patch_v1"


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
            if combo is not None and combo.findText("image_gs_splat") < 0:
                combo.addItem("image_gs_splat")
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

    original_kind = getattr(cls, "_header_debug_button_kind", None)
    original_toggle = getattr(cls, "_toggle_header_debug_button", None)
    if not callable(original_kind) or not callable(original_toggle):
        return

    def wrapped_kind(self, *args, **kwargs):
        try:
            kind = str(getattr(getattr(self, "model", None), "kind", "") or "").strip().lower()
            if kind in KIND_ALIASES:
                return "image_gs_splat"
        except Exception:
            pass
        try:
            return original_kind(self, *args, **kwargs)
        except Exception:
            return None

    def wrapped_toggle(self, *args, **kwargs):
        try:
            kind = str(getattr(getattr(self, "model", None), "kind", "") or "").strip().lower()
            if kind in KIND_ALIASES:
                from nodes.image_gs import spec as _image_gs_spec  # type: ignore

                show_report = getattr(_image_gs_spec, "show_image_gs_debug_report", None)
                if callable(show_report):
                    return bool(show_report(self))
        except Exception:
            pass
        try:
            return original_toggle(self, *args, **kwargs)
        except Exception:
            return False

    try:
        cls._header_debug_button_kind = wrapped_kind
        cls._toggle_header_debug_button = wrapped_toggle
        setattr(cls, _HEADER_PATCH_KEY, True)
    except Exception:
        return


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in KIND_ALIASES:
        _core.register_spec(kind, IMAGE_GS_SPLAT_SPEC)
    _patch_create_node_dialog()
    _patch_header_debug_button()


__all__ = ["register"]
