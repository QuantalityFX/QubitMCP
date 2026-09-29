from .spec import START_SPEC, END_SPEC


def register(core=None):
    if core is None:
        from nodes import core
    core.register_spec("for_each", START_SPEC)
    core.register_spec("for_each_end", END_SPEC)
