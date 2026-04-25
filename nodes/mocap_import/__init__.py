from .spec import MOCAP_IMPORT_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in (
        "mocap_import",
        "mocap import",
        "mocapimport",
        "bvh_import",
        "bvh import",
        "bvhimport",
    ):
        _core.register_spec(kind, MOCAP_IMPORT_SPEC)
