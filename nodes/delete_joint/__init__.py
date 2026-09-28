from .spec import DELETE_JOINT_SPEC


def register(core=None):
    if core is None:
        from nodes import core
    core.register_spec("delete_joint", DELETE_JOINT_SPEC)
