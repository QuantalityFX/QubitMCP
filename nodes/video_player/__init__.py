from .spec import VIDEO_PLAYER_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("video_player", VIDEO_PLAYER_SPEC)
    _core.register_spec("video player", VIDEO_PLAYER_SPEC)
    _core.register_spec("videoplayer", VIDEO_PLAYER_SPEC)

