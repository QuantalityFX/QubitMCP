from .spec import MOCAP_COLLECTION_SPEC


def register(core=None):
    if core is None:
        from nodes import core
    core.register_spec("mocap_collection", MOCAP_COLLECTION_SPEC)
