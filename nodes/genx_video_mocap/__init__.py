from .spec import GENX_VIDEO_MOCAP_SPEC, GENX_NODE_KINDS


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in GENX_NODE_KINDS:
        _core.register_spec(kind, GENX_VIDEO_MOCAP_SPEC)
