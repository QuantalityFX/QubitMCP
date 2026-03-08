from .spec import SCENE_SPEC

_DEBUG_PATCH_KEY = "_scene_header_debug_patch_v1"


def _patch_header_debug_button() -> None:
    try:
        from echograph.ui import node_item as node_item_mod
    except Exception:
        return

    cls = getattr(node_item_mod, "NodeItem", None)
    if cls is None or getattr(cls, _DEBUG_PATCH_KEY, False):
        return

    original = getattr(cls, "_header_debug_button_kind", None)
    if not callable(original):
        return

    def wrapped(self, *args, **kwargs):
        try:
            kind = str(getattr(getattr(self, "model", None), "kind", "") or "").strip().lower()
            if kind in ("scene", "scene_assembly", "scene_outliner"):
                return "scene"
        except Exception:
            pass
        try:
            return original(self, *args, **kwargs)
        except Exception:
            return None

    try:
        cls._header_debug_button_kind = wrapped
        setattr(cls, _DEBUG_PATCH_KEY, True)
    except Exception:
        return


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("scene", SCENE_SPEC)
    _core.register_spec("scene_assembly", SCENE_SPEC)
    _core.register_spec("scene_outliner", SCENE_SPEC)
    _patch_header_debug_button()
