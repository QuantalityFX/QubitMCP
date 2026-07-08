from .spec import FBX_ANIMATION_EXPORT_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    _core.register_spec("export_fbx_animation", FBX_ANIMATION_EXPORT_SPEC)
    _core.register_spec("export fbx animation", FBX_ANIMATION_EXPORT_SPEC)
    _core.register_spec("exportfbxanimation", FBX_ANIMATION_EXPORT_SPEC)
    # Backward-compatible aliases from the first implementation.
    _core.register_spec("fbx_animation_export", FBX_ANIMATION_EXPORT_SPEC)
    _core.register_spec("fbxanimationexport", FBX_ANIMATION_EXPORT_SPEC)
    _core.register_spec("fbx animation export", FBX_ANIMATION_EXPORT_SPEC)
