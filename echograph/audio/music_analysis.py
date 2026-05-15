from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

try:
    import numpy as np
except Exception:  # pragma: no cover - runtime dependency guard
    np = None


class MusicAnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class MusicAnalysisSettings:
    sample_rate: int = 44_100
    frame_size: int = 2_048
    hop_size: int = 512
    smoothing_ms: float = 80.0


@dataclass(frozen=True)
class MusicAnalysisResult:
    cache_path: Path
    duration_s: float
    sample_rate: int
    frame_count: int
    from_cache: bool = False


def _require_numpy():
    if np is None:
        raise MusicAnalysisError("Music analysis requires numpy.")


def _find_ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled:
            return str(bundled)
    except Exception:
        pass
    raise MusicAnalysisError("MP3 analysis requires ffmpeg.")


def _decode_wav(path: Path) -> tuple["np.ndarray", int]:
    _require_numpy()
    try:
        wf = wave.open(str(path), "rb")
    except Exception as exc:
        raise MusicAnalysisError(f"Failed to open WAV file: {exc}") from exc
    with wf:
        try:
            channels = max(1, int(wf.getnchannels()))
            sample_rate = max(1, int(wf.getframerate()))
            sample_width = max(1, int(wf.getsampwidth()))
            frame_count = max(0, int(wf.getnframes()))
            raw = wf.readframes(frame_count)
        except Exception as exc:
            raise MusicAnalysisError(f"Failed to read WAV file: {exc}") from exc
    if not raw or frame_count <= 0:
        raise MusicAnalysisError("Audio file contains no samples.")

    if sample_width == 1:
        data = np.frombuffer(raw, dtype=np.uint8).astype("f4")
        data = (data - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype("f4") / 32768.0
    elif sample_width == 3:
        bytes_arr = np.frombuffer(raw, dtype=np.uint8)
        usable = int(bytes_arr.size - (bytes_arr.size % 3))
        if usable <= 0:
            raise MusicAnalysisError("Audio file contains no valid 24-bit samples.")
        triplets = bytes_arr[:usable].reshape(-1, 3).astype(np.int32)
        values = triplets[:, 0] | (triplets[:, 1] << 8) | (triplets[:, 2] << 16)
        values = np.where(values & 0x800000, values - 0x1000000, values)
        data = values.astype("f4") / 8388608.0
    elif sample_width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype("f4") / 2147483648.0
    else:
        raise MusicAnalysisError(f"Unsupported WAV sample width: {sample_width} bytes.")

    usable = int(data.size - (data.size % channels))
    if usable <= 0:
        raise MusicAnalysisError("Audio file contains no usable samples.")
    data = data[:usable].reshape(-1, channels)
    mono = data.mean(axis=1).astype("f4", copy=False)
    return mono, sample_rate


def _decode_with_ffmpeg(path: Path, sample_rate: int) -> tuple["np.ndarray", int]:
    _require_numpy()
    ffmpeg = _find_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-v",
        "error",
        "-i",
        str(path),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        "1",
        "-ar",
        str(int(sample_rate)),
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except Exception as exc:
        raise MusicAnalysisError(f"Failed to launch ffmpeg: {exc}") from exc
    if int(proc.returncode or 0) != 0:
        detail = proc.stderr.decode("utf-8", errors="ignore").strip()
        raise MusicAnalysisError(detail or "ffmpeg failed to decode audio.")
    data = np.frombuffer(proc.stdout, dtype="<f4").astype("f4", copy=False)
    if data.size == 0:
        raise MusicAnalysisError("Decoded audio contains no samples.")
    return data, int(sample_rate)


def _resample_linear(samples: "np.ndarray", source_rate: int, target_rate: int) -> "np.ndarray":
    _require_numpy()
    if int(source_rate) == int(target_rate):
        return samples.astype("f4", copy=False)
    if samples.size <= 1:
        return samples.astype("f4", copy=False)
    duration = float(samples.size - 1) / float(max(1, source_rate))
    target_count = max(1, int(round(duration * float(target_rate))) + 1)
    source_times = np.linspace(0.0, duration, int(samples.size), dtype="f8")
    target_times = np.linspace(0.0, duration, int(target_count), dtype="f8")
    return np.interp(target_times, source_times, samples.astype("f8", copy=False)).astype("f4")


def _decode_audio(path: Path, settings: MusicAnalysisSettings) -> tuple["np.ndarray", int]:
    suffix = path.suffix.lower()
    if suffix == ".wav":
        samples, sample_rate = _decode_wav(path)
        return _resample_linear(samples, sample_rate, int(settings.sample_rate)), int(settings.sample_rate)
    if suffix == ".mp3":
        return _decode_with_ffmpeg(path, int(settings.sample_rate))
    raise MusicAnalysisError("Supported audio formats are .wav and .mp3.")


def _moving_average(values: "np.ndarray", width: int) -> "np.ndarray":
    _require_numpy()
    width = max(1, int(width))
    if width <= 1 or values.size <= 1:
        return values.astype("f4", copy=False)
    kernel = np.ones((width,), dtype="f4") / float(width)
    return np.convolve(values.astype("f4", copy=False), kernel, mode="same").astype("f4")


def _normalize(values: "np.ndarray") -> "np.ndarray":
    _require_numpy()
    if values.size == 0:
        return values.astype("f4", copy=False)
    hi = float(np.percentile(values, 99.0))
    if hi <= 1.0e-8:
        hi = float(values.max(initial=0.0))
    if hi <= 1.0e-8:
        return np.zeros_like(values, dtype="f4")
    return np.clip(values / hi, 0.0, 1.0).astype("f4")


def _analyze_samples(samples: "np.ndarray", sample_rate: int, settings: MusicAnalysisSettings) -> dict[str, "np.ndarray"]:
    _require_numpy()
    frame_size = max(256, int(settings.frame_size))
    hop_size = max(64, int(settings.hop_size))
    if samples.size < frame_size:
        samples = np.pad(samples, (0, frame_size - int(samples.size)))
    frame_count = 1 + int(max(0, samples.size - frame_size) // hop_size)
    if frame_count <= 0:
        frame_count = 1

    window = np.hanning(frame_size).astype("f4")
    amplitude_rows = []
    onset_rows = []
    prev_mag = None
    for idx in range(frame_count):
        start = int(idx * hop_size)
        frame = samples[start : start + frame_size]
        if frame.size < frame_size:
            frame = np.pad(frame, (0, frame_size - int(frame.size)))
        rms = float(np.sqrt(np.mean(np.square(frame.astype("f4", copy=False)))))
        amplitude_rows.append(rms)
        mag = np.abs(np.fft.rfft(frame * window)).astype("f4")
        if prev_mag is None:
            onset_rows.append(0.0)
        else:
            onset_rows.append(float(np.maximum(mag - prev_mag, 0.0).sum()))
        prev_mag = mag

    amplitude = np.asarray(amplitude_rows, dtype="f4")
    onset = np.asarray(onset_rows, dtype="f4")
    smooth_frames = max(
        1,
        int(round((float(settings.smoothing_ms) / 1000.0) * float(sample_rate) / float(hop_size))),
    )
    amplitude_norm = _normalize(_moving_average(amplitude, smooth_frames))
    onset_norm = _normalize(_moving_average(onset, smooth_frames))
    beat_strength = _normalize((0.35 * amplitude_norm) + (0.65 * onset_norm))
    times_s = (np.arange(frame_count, dtype="f4") * float(hop_size) / float(sample_rate)).astype("f4")
    return {
        "times_s": times_s,
        "amplitude_envelope": amplitude_norm,
        "onset_envelope": onset_norm,
        "beat_strength": beat_strength,
    }


def _analysis_key(path: Path, settings: MusicAnalysisSettings) -> str:
    try:
        stat = path.stat()
        stamp = f"{int(stat.st_size)}:{int(stat.st_mtime_ns)}"
    except Exception:
        stamp = "0:0"
    raw = "|".join(
        [
            str(path.resolve()).lower(),
            stamp,
            str(int(settings.sample_rate)),
            str(int(settings.frame_size)),
            str(int(settings.hop_size)),
            f"{float(settings.smoothing_ms):.3f}",
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _default_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "EchoGraph" / "music_effects_cache"


def analyze_audio_file(
    path: str | Path,
    *,
    settings: MusicAnalysisSettings | None = None,
    cache_dir: str | Path | None = None,
) -> MusicAnalysisResult:
    _require_numpy()
    source = Path(path)
    if not source.is_file():
        raise MusicAnalysisError("Audio file was not found.")
    settings = settings or MusicAnalysisSettings()
    cache_root = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise MusicAnalysisError(f"Could not create music analysis cache: {exc}") from exc
    cache_path = cache_root / f"{_analysis_key(source, settings)}.npz"
    if cache_path.exists():
        try:
            with np.load(str(cache_path), allow_pickle=False) as data:
                times = np.asarray(data["times_s"], dtype="f4")
                sample_rate = int(np.asarray(data["sample_rate"]).reshape(-1)[0])
                duration_s = float(np.asarray(data["duration_s"]).reshape(-1)[0])
            return MusicAnalysisResult(
                cache_path=cache_path,
                duration_s=duration_s,
                sample_rate=sample_rate,
                frame_count=int(times.size),
                from_cache=True,
            )
        except Exception:
            try:
                cache_path.unlink()
            except Exception:
                pass

    samples, sample_rate = _decode_audio(source, settings)
    arrays = _analyze_samples(samples, sample_rate, settings)
    duration_s = float(samples.size) / float(max(1, sample_rate))
    try:
        np.savez_compressed(
            str(cache_path),
            **arrays,
            sample_rate=np.asarray([int(sample_rate)], dtype="i4"),
            duration_s=np.asarray([duration_s], dtype="f4"),
        )
    except Exception as exc:
        raise MusicAnalysisError(f"Could not write music analysis cache: {exc}") from exc
    return MusicAnalysisResult(
        cache_path=cache_path,
        duration_s=duration_s,
        sample_rate=int(sample_rate),
        frame_count=int(arrays["times_s"].size),
        from_cache=False,
    )
