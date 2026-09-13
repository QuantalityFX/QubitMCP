from .spec import OPENAI_REALTIME_TRANSLATE_NODE_ALIASES, OPENAI_REALTIME_TRANSLATE_NODE_KIND, OPENAI_REALTIME_TRANSLATE_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    for kind in (OPENAI_REALTIME_TRANSLATE_NODE_KIND, *OPENAI_REALTIME_TRANSLATE_NODE_ALIASES):
        _core.register_spec(kind, OPENAI_REALTIME_TRANSLATE_SPEC)
