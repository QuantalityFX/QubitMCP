from .spec import HTML_PREVIEW_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("html_preview", HTML_PREVIEW_SPEC)
