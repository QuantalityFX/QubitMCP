from .spec import IMAGE_COLLECTION_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("image_collection", IMAGE_COLLECTION_SPEC)
    _core.register_spec("imagecollection", IMAGE_COLLECTION_SPEC)
