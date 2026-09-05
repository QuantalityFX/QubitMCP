from .spec import MINIMAX_H3_API_VIDEO_NODE_ALIASES, MINIMAX_H3_API_VIDEO_NODE_KIND, MINIMAX_H3_API_VIDEO_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in (MINIMAX_H3_API_VIDEO_NODE_KIND, *MINIMAX_H3_API_VIDEO_NODE_ALIASES):
        _core.register_spec(kind, MINIMAX_H3_API_VIDEO_SPEC)
