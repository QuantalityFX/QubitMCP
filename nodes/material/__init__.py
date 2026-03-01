from .spec import MATERIAL_SPEC, MNATERIAL_SPEC, build_material_asset


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("material", MATERIAL_SPEC)
    _core.register_spec("mnaterial", MNATERIAL_SPEC)


__all__ = ["MATERIAL_SPEC", "MNATERIAL_SPEC", "build_material_asset", "register"]
