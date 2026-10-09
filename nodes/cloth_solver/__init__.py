from nodes.core import register_spec
from .spec import CLOTH_SOLVER_SPEC


def register():
    register_spec("cloth_solver", CLOTH_SOLVER_SPEC)


register()
