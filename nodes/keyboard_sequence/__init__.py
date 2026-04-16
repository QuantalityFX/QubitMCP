from .spec import KEYBOARD_SEQUENCE_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("keyboard_sequence", KEYBOARD_SEQUENCE_SPEC)
    _core.register_spec("keyboard sequence", KEYBOARD_SEQUENCE_SPEC)
    _core.register_spec("keyboard_scheduler", KEYBOARD_SEQUENCE_SPEC)
    _core.register_spec("keyboard scheduler", KEYBOARD_SEQUENCE_SPEC)
