from .spec import MINIMAX_TTS_API_NODE_ALIASES, MINIMAX_TTS_API_NODE_KIND, MINIMAX_TTS_API_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in (MINIMAX_TTS_API_NODE_KIND, *MINIMAX_TTS_API_NODE_ALIASES):
        _core.register_spec(kind, MINIMAX_TTS_API_SPEC)
