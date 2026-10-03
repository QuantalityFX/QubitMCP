"""Prepare captured system audio for speech recognition without changing playback."""
from __future__ import annotations

import audioop
from dataclasses import replace


def prepare_speech_audio(chunk):
    """Boost quiet PCM toward -20 dBFS, with bounded gain and peak headroom."""
    if not chunk.pcm:
        return None
    full_scale = float(1 << (chunk.sample_width * 8 - 1))
    rms = audioop.rms(chunk.pcm, chunk.sample_width)
    if rms < full_scale * 0.0001:  # Digital silence / below -80 dBFS.
        return None
    peak = audioop.max(chunk.pcm, chunk.sample_width)
    gain = min(12.0, full_scale * 0.1 / rms, full_scale * 0.95 / max(1, peak))
    if gain <= 1.0:
        return chunk
    return replace(chunk, pcm=audioop.mul(chunk.pcm, chunk.sample_width, gain))


def join_audio(previous, current):
    if previous is None:
        return current
    if (previous.sample_rate, previous.sample_width, previous.channels) != (current.sample_rate, current.sample_width, current.channels):
        raise ValueError("Audio format changed during capture. Restart listening.")
    return replace(current, pcm=previous.pcm + current.pcm)


def duration_seconds(chunk):
    return len(chunk.pcm) / (chunk.sample_rate * chunk.sample_width * chunk.channels)
