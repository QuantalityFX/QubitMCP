from __future__ import annotations

import atexit
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()
_LOG_STREAM: "_EchoGraphLogStream | None" = None
_LOG_PATH: Path | None = None
_VIEWPORT_RENDER_DEBUG_ENABLED = False


def debug_log_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "EchoGraph"
    path.mkdir(parents=True, exist_ok=True)
    return path


def echo_log_path() -> Path:
    global _LOG_PATH
    if _LOG_PATH is None:
        _LOG_PATH = debug_log_dir() / f"echograph_{time.strftime('%Y%m%d')}.log"
    return _LOG_PATH


def viewport_render_log_path() -> Path:
    return debug_log_dir() / f"viewport_render_debug_{time.strftime('%Y%m%d')}.jsonl"


class _EchoGraphLogStream:
    def __init__(self, path: Path, enabled: bool = True):
        self.path = Path(path)
        self.enabled = bool(enabled)
        self._file = self.path.open("a", encoding="utf-8", buffering=1)

    def write(self, text: str) -> int:
        raw = str(text or "")
        if not raw:
            return 0
        with _LOCK:
            if self.enabled:
                self._file.write(raw)
        return len(raw)

    def flush(self) -> None:
        with _LOCK:
            try:
                self._file.flush()
            except Exception:
                pass

    def close(self) -> None:
        with _LOCK:
            try:
                self._file.flush()
            except Exception:
                pass
            try:
                self._file.close()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False


def init_echo_log() -> str:
    global _LOG_STREAM, _LOG_PATH
    with _LOCK:
        if _LOG_STREAM is not None:
            return str(echo_log_path())
        _LOG_PATH = echo_log_path()
        stream = _EchoGraphLogStream(_LOG_PATH, enabled=True)
        _LOG_STREAM = stream
        sys.stdout = stream
        sys.stderr = stream

    print(f"[{time.strftime('%H:%M:%S')}] --- EchoGraph started ---")

    def _excepthook(exc_type, exc, tb):
        print("\n[EchoGraph] Uncaught exception:")
        traceback.print_exception(exc_type, exc, tb)

    sys.excepthook = _excepthook

    @atexit.register
    def _close_log():
        try:
            print(f"[{time.strftime('%H:%M:%S')}] --- EchoGraph exit ---")
        except Exception:
            pass
        stream.close()

    return str(_LOG_PATH)


def echo_log_enabled() -> bool:
    stream = _LOG_STREAM
    return True if stream is None else bool(stream.enabled)


def set_echo_log_enabled(enabled: bool) -> None:
    stream = _LOG_STREAM
    if stream is None:
        return
    enabled = bool(enabled)
    if enabled:
        stream.enabled = True
        print(f"[{time.strftime('%H:%M:%S')}] --- EchoGraph log enabled ---")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] --- EchoGraph log disabled ---")
        stream.flush()
        stream.enabled = False


def viewport_render_debug_enabled() -> bool:
    return bool(_VIEWPORT_RENDER_DEBUG_ENABLED)


def set_viewport_render_debug_enabled(enabled: bool) -> None:
    global _VIEWPORT_RENDER_DEBUG_ENABLED
    enabled = bool(enabled)
    if _VIEWPORT_RENDER_DEBUG_ENABLED == enabled:
        return
    if enabled:
        _VIEWPORT_RENDER_DEBUG_ENABLED = True
        log_viewport_render("viewport_debug_enabled", path=str(viewport_render_log_path()))
    else:
        log_viewport_render("viewport_debug_disabled", path=str(viewport_render_log_path()), force=True)
        _VIEWPORT_RENDER_DEBUG_ENABLED = False


def log_viewport_render(event: str, *, force: bool = False, **fields: Any) -> None:
    if not force and not _VIEWPORT_RENDER_DEBUG_ENABLED:
        return
    try:
        payload = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": str(event)}
        payload.update(fields)
        with viewport_render_log_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    except Exception:
        pass


def open_debug_path() -> None:
    path = debug_log_dir()
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        pass

