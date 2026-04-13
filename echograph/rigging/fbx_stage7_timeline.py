from __future__ import annotations

from typing import List, Sequence, Set

from .fbx_canonical import AnimationClip, JointAnimationTrack, QuatKeyframe, Vec3Keyframe


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _resolve_fps_value(fps: float, clip: AnimationClip | None) -> float:
    value = _safe_float(fps, 0.0)
    if value > 1.0e-6:
        return value
    if clip is not None:
        clip_rate = _safe_float(getattr(clip, "sample_rate_hz", 0.0), 0.0)
        if clip_rate > 1.0e-6:
            return clip_rate
    return 24.0


def clip_sample_time_from_timeline_seconds(
    clip: AnimationClip | None,
    timeline_seconds: float,
) -> float:
    timeline_t = _safe_float(timeline_seconds, 0.0)
    if clip is None:
        return timeline_t
    return _safe_float(getattr(clip, "start_time", 0.0), 0.0) + timeline_t


def _append_key_frames(
    out_frames: Set[int],
    keys: Sequence[Vec3Keyframe] | Sequence[QuatKeyframe],
    *,
    start_time: float,
    fps: float,
) -> None:
    for key in list(keys or []):
        t = _safe_float(getattr(key, "time", 0.0), 0.0)
        frame = int(round((t - float(start_time)) * float(fps)))
        if frame >= 0:
            out_frames.add(int(frame))


def clip_marker_frames(
    clip: AnimationClip | None,
    *,
    fps: float,
) -> List[int]:
    if clip is None:
        return []
    resolved_fps = _resolve_fps_value(fps, clip)
    start_time = _safe_float(getattr(clip, "start_time", 0.0), 0.0)
    end_time = _safe_float(getattr(clip, "end_time", start_time), start_time)
    if end_time < start_time:
        end_time = start_time
    end_frame = int(round((end_time - start_time) * float(resolved_fps)))
    if end_frame < 0:
        end_frame = 0

    frames: Set[int] = {0, int(end_frame)}
    for track in list(getattr(clip, "tracks", []) or []):
        if not isinstance(track, JointAnimationTrack):
            continue
        _append_key_frames(
            frames,
            list(getattr(track, "translation_keys", []) or []),
            start_time=start_time,
            fps=resolved_fps,
        )
        _append_key_frames(
            frames,
            list(getattr(track, "rotation_keys", []) or []),
            start_time=start_time,
            fps=resolved_fps,
        )
        _append_key_frames(
            frames,
            list(getattr(track, "scale_keys", []) or []),
            start_time=start_time,
            fps=resolved_fps,
        )
    return sorted(frames)


__all__ = [
    "clip_sample_time_from_timeline_seconds",
    "clip_marker_frames",
]
