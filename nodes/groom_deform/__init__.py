from __future__ import annotations

from .spec import GROOM_DEFORM_SPEC, KIND_ALIASES


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    for kind in KIND_ALIASES:
        _core.register_spec(kind, GROOM_DEFORM_SPEC)


__all__ = ["register"]
