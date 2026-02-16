from .spec import EXPORT_FBX_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    _core.register_spec("export_fbx", EXPORT_FBX_SPEC)
    _core.register_spec("exportfbx", EXPORT_FBX_SPEC)
    _core.register_spec("export fbx", EXPORT_FBX_SPEC)
