from __future__ import annotations

from .spec import DATA_NEXUS_ALIASES, DATA_NEXUS_NODE_KIND, DATA_NEXUS_SPEC

_PATCH_KEY = "_data_nexus_create_dialog_patch_v1"


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
            if combo is not None and combo.findText(DATA_NEXUS_NODE_KIND) < 0:
                combo.addItem(DATA_NEXUS_NODE_KIND)
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

    for kind in {DATA_NEXUS_NODE_KIND, *DATA_NEXUS_ALIASES}:
        _core.register(
            kind,
            stripe_color=DATA_NEXUS_SPEC.stripe_color,
            render_node_body=DATA_NEXUS_SPEC.render_node_body,
            build_ports=DATA_NEXUS_SPEC.build_ports,
        )
    _patch_create_node_dialog()
