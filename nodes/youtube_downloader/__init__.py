from .spec import YOUTUBE_DOWNLOADER_NODE_ALIASES, YOUTUBE_DOWNLOADER_NODE_KIND, YOUTUBE_DOWNLOADER_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in (YOUTUBE_DOWNLOADER_NODE_KIND, *YOUTUBE_DOWNLOADER_NODE_ALIASES):
        _core.register_spec(kind, YOUTUBE_DOWNLOADER_SPEC)
