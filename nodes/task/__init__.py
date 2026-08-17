from __future__ import annotations

from .spec import TASK_NODE_ALIASES, TASK_NODE_KIND, TASK_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in {TASK_NODE_KIND, *TASK_NODE_ALIASES}:
        _core.register_spec(kind, TASK_SPEC)
