from .spec import WAN22_VIDEO_NODE_ALIASES, WAN22_VIDEO_NODE_KIND, WAN22_VIDEO_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in (WAN22_VIDEO_NODE_KIND, *WAN22_VIDEO_NODE_ALIASES):
        _core.register_spec(kind, WAN22_VIDEO_SPEC)
