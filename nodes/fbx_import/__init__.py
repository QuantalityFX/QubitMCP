from .spec import FBX_IMPORT_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    _core.register_spec("fbx_import", FBX_IMPORT_SPEC)
    _core.register_spec("fbximport", FBX_IMPORT_SPEC)
    _core.register_spec("fbx import", FBX_IMPORT_SPEC)
