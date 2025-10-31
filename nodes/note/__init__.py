# nodes/note/__init__.py
from .spec import Spec

def register(core=None):
    """
    Register Note node spec with the central registry.
    The `core` is optional; we import lazily if not provided.
    """
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register("note", Spec)
