from __future__ import annotations

from .spec import TRANSLATOR_NODE_ALIASES, TRANSLATOR_NODE_KIND, TRANSLATOR_SPEC

_PATCH_KEY = "_translator_create_dialog_patch_v1"


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
            if combo is not None and combo.findText(TRANSLATOR_NODE_KIND) < 0:
                combo.insertItem(2, TRANSLATOR_NODE_KIND)
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
    for kind in {TRANSLATOR_NODE_KIND, *TRANSLATOR_NODE_ALIASES}:
        _core.register(
            kind,
            stripe_color=TRANSLATOR_SPEC.stripe_color,
            render_node_body=TRANSLATOR_SPEC.render_node_body,
            build_ports=TRANSLATOR_SPEC.build_ports,
        )
    _patch_create_node_dialog()
