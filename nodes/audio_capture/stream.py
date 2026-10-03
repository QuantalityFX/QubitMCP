"""Continuous capture and a disk-backed FIFO for slower speech recognizers."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from pathlib import Path
import queue
import tempfile
import threading

from . import spec as capture


@contextmanager
def open_audio_reader(config):
    """Keep the selected device open for the entire recording session."""
    if capture._exclude_app_audio_enabled(config):
        from .windows_loopback import ProcessLoopbackReader
        rate = capture._coerce_int(config.get("sample_rate"), 16000, minimum=8000, maximum=96000)
        with ProcessLoopbackReader(rate) as recorder:
            yield (lambda: recorder.read(int(rate * capture._BLOCK_MS / 1000)),
                   rate, "Other apps (all outputs; QubitMCP excluded)")
        return
    mode = capture._normalize_source_mode(config.get("source_mode", capture.SOURCE_OUTPUT_DEVICE))
    if mode == capture.SOURCE_APPLICATION:
        raise RuntimeError("Application capture is unavailable. Select an input or output device.")
    if mode == capture.SOURCE_OUTPUT_DEVICE and capture.sc is not None and capture.np is not None:
        speaker = capture._soundcard_speaker_for_config(config)
        loopback = capture._soundcard_loopback_for_speaker(speaker)
        if loopback is None:
            raise RuntimeError("No loopback recorder found for the selected output device.")
        rate = capture._coerce_int(config.get("sample_rate"), 16000, minimum=8000, maximum=96000)
        block_frames = int(rate * capture._BLOCK_MS / 1000)
        with loopback.recorder(samplerate=rate, blocksize=block_frames) as recorder:
            yield (lambda: capture._float_frames_to_pcm_mono(recorder.record(numframes=block_frames)),
                   rate, str(getattr(speaker, "name", "Output device")))
        return
    if capture.pyaudio is None:
        raise RuntimeError("Audio Capture needs PyAudio. Run setup.bat.")
    entry = capture._pyaudio_input_for_config(config, allow_stereo_mix_fallback=mode == capture.SOURCE_OUTPUT_DEVICE)
    if not entry:
        raise RuntimeError("No recording device found. Select an input device or install soundcard for output capture.")
    pa, stream = capture.pyaudio.PyAudio(), None
    try:
        rate = int(entry.get("sample_rate") or config.get("sample_rate") or 16000)
        channels = min(2, max(1, int(entry.get("input_channels") or 1)))
        block_frames = int(rate * capture._BLOCK_MS / 1000)
        stream = pa.open(format=capture.pyaudio.paInt16, channels=channels, rate=rate, input=True,
                         input_device_index=int(entry.get("index", entry.get("id", 0))),
                         frames_per_buffer=block_frames)
        # Report device overruns instead of silently discarding input.
        yield (lambda: capture._pyaudio_to_mono(stream.read(block_frames, exception_on_overflow=True), channels),
               rate, str(entry.get("name") or "Input device"))
    finally:
        try:
            if stream is not None:
                try:
                    stream.stop_stream()
                finally:
                    stream.close()
        finally:
            pa.terminate()


def iter_audio_chunks(config, *, should_stop, is_paused=lambda: False):
    """Split continuous PCM on silence or five seconds, preserving a final partial phrase."""
    threshold = capture._coerce_float(config.get("threshold"), capture._DEFAULT_THRESHOLD, minimum=0.001, maximum=0.5)
    phrase_ms = min(5000, capture._coerce_int(config.get("phrase_ms"), 5000, minimum=1000, maximum=120000))
    silence_ms = capture._coerce_int(config.get("silence_ms"), 850, minimum=150, maximum=5000)
    with open_audio_reader(config) as (read, rate, label):
        frames, samples, silent_samples = [], 0, 0
        # Keep quiet consonants just before the first above-threshold block.
        preroll = deque(maxlen=max(1, 250 // capture._BLOCK_MS))

        def chunk():
            return capture.CapturedAudioChunk(b"".join(frames), rate, source_label=label)

        while not should_stop():
            try:
                block = read()
                if not block:
                    raise RuntimeError("The audio device returned no samples.")
            except Exception:
                if frames:
                    yield chunk()
                raise
            if is_paused():
                if frames:
                    yield chunk()
                frames, samples, silent_samples = [], 0, 0
                preroll.clear()
                # Continue draining the device so resume cannot replay paused audio.
                continue
            voiced = capture._pcm_level(block) >= threshold
            if not frames:
                if not voiced:
                    preroll.append(block)
                    continue
                frames.extend(preroll)
                samples = sum(len(part) // 2 for part in preroll)
                preroll.clear()
            frames.append(block)
            samples += len(block) // 2
            silent_samples = 0 if voiced else silent_samples + len(block) // 2
            if samples >= rate * phrase_ms / 1000 or silent_samples >= rate * silence_ms / 1000:
                yield chunk()
                frames, samples, silent_samples = [], 0, 0
        if frames:
            yield chunk()


def record_sample(config, *, seconds=3.0, on_level=lambda level: None, should_stop=lambda: False):
    """Record a fixed-duration test even when the device is completely silent."""
    with open_audio_reader(config) as (read, rate, label):
        remaining = max(1, int(rate * seconds))
        frames = []
        while remaining > 0 and not should_stop():
            block = read()[:remaining * 2]
            if not block:
                raise RuntimeError("The audio device returned no samples.")
            frames.append(block)
            remaining -= len(block) // 2
            on_level(capture._pcm_level(block))
        return capture.CapturedAudioChunk(b"".join(frames), rate, source_label=label)


class BufferedAudioStream:
    """Record independently of decoding; queued PCM lives on disk, not in growing RAM."""
    def __init__(self, config, *, should_stop, is_paused=lambda: False):
        self.config = dict(config)
        self.should_stop, self.is_paused = should_stop, is_paused
        self.error = ""
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._closing = threading.Event()
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._pending_seconds = 0.0
        self._spool = None
        self._thread = None

    @property
    def pending_seconds(self):
        with self._lock:
            return self._pending_seconds

    @property
    def finished(self):
        return self._done.is_set() and self._queue.empty()

    def __enter__(self):
        self._spool = tempfile.TemporaryDirectory(prefix="qubit_audio_stream_")
        self._thread = threading.Thread(target=self._record, daemon=True, name="AudioStreamCapture")
        self._thread.start()
        return self

    def _record(self):
        try:
            for index, chunk in enumerate(iter_audio_chunks(
                self.config, should_stop=lambda: self._stop.is_set() or self.should_stop(),
                is_paused=self.is_paused,
            )):
                path = Path(self._spool.name) / f"{index}.pcm"
                path.write_bytes(chunk.pcm)
                duration = len(chunk.pcm) / (chunk.sample_rate * chunk.sample_width * chunk.channels)
                metadata = (chunk.sample_rate, chunk.sample_width, chunk.channels, chunk.source_label)
                with self._lock:
                    self._pending_seconds += duration
                self._queue.put((path, metadata, duration))
        except Exception as exc:
            self.error = f"Audio stream capture stopped: {exc}"
        finally:
            self._done.set()
            if self._closing.is_set():
                self._spool.cleanup()

    def get(self, timeout=0.1):
        try:
            path, metadata, duration = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        pcm = path.read_bytes()
        path.unlink()
        with self._lock:
            self._pending_seconds = max(0.0, self._pending_seconds - duration)
        return capture.CapturedAudioChunk(pcm, *metadata)

    def __exit__(self, *_args):
        self._closing.set()
        self._stop.set()
        self._thread.join(timeout=2.0)
        if not self._thread.is_alive():
            self._spool.cleanup()
