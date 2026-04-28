from .spec import POST_PROCESS_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    _core.register_spec("post_process", POST_PROCESS_SPEC)
    _core.register_spec("postprocess", POST_PROCESS_SPEC)
    _core.register_spec("post_processing", POST_PROCESS_SPEC)
    _core.register_spec("post_process_effect", POST_PROCESS_SPEC)
