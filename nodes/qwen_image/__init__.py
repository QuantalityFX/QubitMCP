def register(core=None):
    if core is None:
        from nodes import core
    from .spec import QWEN_IMAGE_SPEC
    core.register_spec("qwen_image", QWEN_IMAGE_SPEC)
