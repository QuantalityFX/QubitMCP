"""Inspect H3 inputs using Pillow and the app's existing FFmpeg runtime."""
from __future__ import annotations

import base64
import binascii
import io
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image


FORMATS = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"},
    "video": {".mp4", ".mov"},
    "audio": {".wav", ".mp3"},
}
LIMITS = {"image": 30 * 1024**2, "video": 50 * 1024**2, "audio": 15 * 1024**2}


@dataclass
class Media:
    kind: str
    source: str
    path: Path | None = None
    data: bytes | None = None
    filename: str = ""
    duration: float = 0.0


def parse_refs(raw) -> list[str]:
    """Newline/JSON lists; preserve ordering and case-sensitive URL paths."""
    import json
    if isinstance(raw, (list, tuple)):
        return list(dict.fromkeys(ref for item in raw for ref in parse_refs(item)))
    if isinstance(raw, dict):
        for key in ("paths", "images", "videos", "audio", "reference_images", "reference_videos", "reference_audio"):
            if key in raw:
                return parse_refs(raw[key])
        return []
    text = str(raw or "").strip()
    if text.startswith(("[", "{")):
        try:
            return parse_refs(json.loads(text))
        except (ValueError, TypeError):
            pass
    return list(dict.fromkeys(line.strip().strip('\"').strip("'") for line in text.splitlines() if line.strip()))


def remote_ref(value: str, kind: str) -> bool:
    # mm_file is a provider locator, not an RFC URL scheme (it contains '_').
    if re.fullmatch(r"mm_file://[A-Za-z0-9_-]+", value):
        return True
    parsed = urlsplit(value)
    if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password:
        return True
    return False


def _dimensions(width: int, height: int):
    if not (256 <= width <= 5760 and 256 <= height <= 5760):
        raise ValueError("Media dimensions must be 256-5760 pixels on each side.")
    if not 0.4 <= width / height <= 2.5:
        raise ValueError("Media aspect ratio must be between 0.4 and 2.5.")


def probe_clip(path: Path, kind: str) -> float:
    import imageio_ffmpeg
    executable = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [executable, "-hide_banner", "-i", str(path)], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    info = result.stderr
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if not duration_match:
        raise ValueError(f"Cannot inspect {kind} duration: {path.name}")
    hours, minutes, seconds = map(float, duration_match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    if not 2 <= duration <= 15:
        raise ValueError(f"Each reference {kind} must be 2-15 seconds ({path.name}: {duration:g}s).")
    if kind == "video":
        line = next((line for line in info.splitlines() if "Video:" in line and "attached pic" not in line), "")
        if not re.search(r"Video:\s*(h264|hevc)\b", line):
            raise ValueError(f"Reference video must use H.264 or H.265/HEVC: {path.name}")
        dimensions = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
        fps = re.search(r"([\d.]+) fps\b", line)
        if not dimensions or not fps:
            raise ValueError(f"Cannot inspect video dimensions/frame rate: {path.name}")
        _dimensions(*map(int, dimensions.groups()))
        if not 23.976 - 0.001 <= float(fps[1]) <= 60:
            raise ValueError("Reference video frame rate must be 23.976-60 FPS.")
        audio = next((line for line in info.splitlines() if "Audio:" in line), "")
        if audio and not re.search(r"Audio:\s*(aac|mp3)\b", audio):
            raise ValueError("Reference video audio must use AAC or MP3 (or remove the audio track).")
    elif "Audio:" not in info:
        raise ValueError(f"No audio stream found: {path.name}")
    return duration


def inspect_media(source: str, kind: str, base_dir: Path) -> Media:
    if remote_ref(source, kind):
        # The provider checks dimensions, codecs, sizes and durations of remote inputs.
        return Media(kind, source)
    data = None
    if source.startswith("data:"):
        match = re.fullmatch(r"data:(image|video|audio)/([a-z0-9.+-]+);base64,(.+)", source, re.DOTALL)
        if not match or match[1] != kind:
            raise ValueError(f"Invalid {kind} data URI.")
        if len(match[3]) > (LIMITS[kind] + 2) // 3 * 4:
            raise ValueError(f"Reference {kind} exceeds the {LIMITS[kind] // 1024**2} MB limit.")
        try:
            data = base64.b64decode(match[3], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"Invalid {kind} Base64 data.") from exc
        extension = {"jpeg": "jpg", "x-wav": "wav", "mpeg": "mp3", "quicktime": "mov"}.get(match[2], match[2])
        path = Path(f"reference.{extension}")
    else:
        path = Path(source).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        if not path.is_file():
            raise ValueError(f"Reference {kind} file does not exist: {path}")
    if path.suffix.lower() not in FORMATS[kind]:
        raise ValueError(f"Unsupported {kind} format. Use: {', '.join(sorted(FORMATS[kind]))}.")
    size = len(data) if data is not None else path.stat().st_size
    if size <= 0 or size > LIMITS[kind]:
        raise ValueError(f"Reference {kind} must be nonempty and at most {LIMITS[kind] // 1024**2} MB: {path.name}")
    media = Media(kind, source, path if data is None else None, data, path.name)
    if kind == "image":
        if path.suffix.lower() not in {".heic", ".heif"}:
            with Image.open(io.BytesIO(data) if data is not None else path) as image:
                _dimensions(*image.size)
                image.verify()
        # HEIC/HEIF dimensions are checked by MiniMax during upload.
    elif data is not None:
        with tempfile.TemporaryDirectory(prefix="qubit_h3_probe_") as temporary:
            temp_path = Path(temporary) / path.name
            temp_path.write_bytes(data)
            media.duration = probe_clip(temp_path, kind)
    else:
        media.duration = probe_clip(path, kind)
    return media


def inspect_references(images, videos, audio, base_dir: Path) -> list[Media]:
    groups = [("video", videos, 3), ("image", images, 9), ("audio", audio, 3)]
    if not any(refs for _, refs, _ in groups):
        raise ValueError("Add a previs video, reference image, or reference audio.")
    if sum(len(refs) for _, refs, _ in groups) > 12:
        raise ValueError("Use at most 12 reference files in total.")
    media = []
    for kind, refs, limit in groups:
        if len(refs) > limit:
            raise ValueError(f"Use at most {limit} reference {kind} files.")
        group = [inspect_media(ref, kind, base_dir) for ref in refs]
        if kind != "image" and sum(item.duration for item in group) > 15.001:
            raise ValueError(f"Reference {kind} clips must total at most 15 seconds.")
        media.extend(group)
    return media
