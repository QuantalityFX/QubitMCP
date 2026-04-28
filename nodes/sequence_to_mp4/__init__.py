from .spec import SEQUENCE_TO_MP4_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    _core.register_spec("sequence_to_mp4", SEQUENCE_TO_MP4_SPEC)
    _core.register_spec("sequence mp4", SEQUENCE_TO_MP4_SPEC)
    _core.register_spec("sequence_to_video", SEQUENCE_TO_MP4_SPEC)
    _core.register_spec("image_sequence_to_mp4", SEQUENCE_TO_MP4_SPEC)
