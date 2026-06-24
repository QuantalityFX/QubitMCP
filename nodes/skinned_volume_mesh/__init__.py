from __future__ import annotations

from .spec import KIND_ALIASES, SKINNED_VOLUME_MESH_SPEC


_DIALOG_PATCH_KEY = "_skinned_volume_mesh_create_dialog_patch_v1"


def _patch_create_node_dialog() -> None:
    """Keep the node visible when plugins register after dialogs were imported."""
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
            if combo is not None and combo.findText("skinned_volume_mesh") < 0:
                combo.addItem("skinned_volume_mesh")
        except Exception:
            pass

    try:
        cls.__init__ = wrapped_init
        setattr(cls, _DIALOG_PATCH_KEY, True)
    except Exception:
        pass


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    for kind in KIND_ALIASES:
        _core.register_spec(kind, SKINNED_VOLUME_MESH_SPEC)
    _patch_create_node_dialog()


__all__ = ["register"]
