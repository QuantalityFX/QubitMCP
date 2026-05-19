from .spec import SERIAL_COM_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("serial_com", SERIAL_COM_SPEC)
    _core.register_spec("serial com", SERIAL_COM_SPEC)
    _core.register_spec("serial_port", SERIAL_COM_SPEC)
    _core.register_spec("serial port", SERIAL_COM_SPEC)
