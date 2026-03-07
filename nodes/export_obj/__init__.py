from __future__ import annotations

from .spec import EXPORT_OBJ_SPEC

_PATCH_KEY = "_export_obj_create_dialog_patch_v1"


def _patch_create_node_dialog() -> None:
    try:
        from echograph.ui import dialogs
    except Exception:
        return

    cls = getattr(dialogs, "CreateNodeDialog", None)
    if cls is None or getattr(cls, _PATCH_KEY, False):
        return

    original_init = getattr(cls, "__init__", None)
    if not callable(original_init):
        return

    def wrapped_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            combo = getattr(self, "kind_edit", None)
            if combo is not None and combo.findText("export_obj") < 0:
                combo.addItem("export_obj")
        except Exception:
            pass

    try:
        cls.__init__ = wrapped_init
        setattr(cls, _PATCH_KEY, True)
    except Exception:
        return


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    _core.register_spec("export_obj", EXPORT_OBJ_SPEC)
    _core.register_spec("exportobj", EXPORT_OBJ_SPEC)
    _core.register_spec("export obj", EXPORT_OBJ_SPEC)
    _patch_create_node_dialog()
