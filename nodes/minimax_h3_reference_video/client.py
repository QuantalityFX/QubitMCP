"""UI-independent MiniMax V2 reference generation and persistent task retrieval.

Protocol: platform.minimax.io/docs/api-reference/video-generation-v2-create,
video-generation-v2-query and file-management-upload (video_generation_input).
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .media import inspect_references, remote_ref


RATIOS = ("adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")


@dataclass
class Settings:
    prompt: str = ""
    images: list[str] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)
    audio: list[str] = field(default_factory=list)
    model: str = "MiniMax-H3"
    resolution: str = "768P"
    duration: int = 5
    ratio: str = "adaptive"
    expansion: str = "balanced"
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.minimax.io"
    base_dir: Path = field(default_factory=Path.cwd)
    output_dir: Path = field(default_factory=lambda: Path.cwd() / "videos" / "minimax_h3_reference")
    poll_interval: float = 5
    timeout: float = 1800


@dataclass
class Result:
    ok: bool = False
    task_id: str = ""
    status: str = "failed"
    path: str = ""
    error: str = ""
    metadata_path: str = ""


class Stopped(Exception):
    pass


class APIError(RuntimeError):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def build_payload(settings: Settings, *, images=None, videos=None, audio=None) -> dict:
    prompt = settings.prompt.strip()
    if not prompt or len(prompt) > 7000:
        raise ValueError("Enter a prompt of 1-7000 characters.")
    limits = {"MiniMax-H3": (4, {"768P", "2K"}), "MiniMax-H3-Max": (5, {"480P", "768P"})}
    if settings.model not in limits:
        raise ValueError("Choose MiniMax-H3 or MiniMax-H3-Max.")
    minimum, resolutions = limits[settings.model]
    if type(settings.duration) is not int or not minimum <= settings.duration <= 15:
        raise ValueError(f"{settings.model} duration must be an integer from {minimum} to 15 seconds.")
    if settings.resolution not in resolutions:
        raise ValueError(f"{settings.model} supports {' / '.join(sorted(resolutions))}.")
    if settings.ratio not in RATIOS:
        raise ValueError("Unsupported aspect ratio.")
    groups = [
        ("video", settings.videos if videos is None else videos, 3),
        ("image", settings.images if images is None else images, 9),
        ("audio", settings.audio if audio is None else audio, 3),
    ]
    if not any(refs for _, refs, _ in groups):
        raise ValueError("Add a previs video, reference image, or reference audio.")
    if sum(len(refs) for _, refs, _ in groups) > 12:
        raise ValueError("Use at most 12 reference files in total.")
    content = [{"type": "text", "text": prompt}]
    for kind, refs, count in groups:
        if len(refs) > count:
            raise ValueError(f"Use at most {count} reference {kind} files.")
        for ref in refs:
            if not remote_ref(ref, kind) and not ref.startswith(f"data:{kind}/"):
                raise ValueError("Upload local media before building the API payload.")
            content.append({"type": f"{kind}_url", f"{kind}_url": {"url": ref}, "role": f"reference_{kind}"})
    payload = {"model": settings.model, "content": content, "resolution": settings.resolution,
               "duration": settings.duration, "ratio": settings.ratio}
    if settings.model == "MiniMax-H3-Max":
        if settings.expansion not in {"disabled", "balanced", "quality"}:
            raise ValueError("Invalid prompt expansion mode.")
        payload["extra"] = {"prompt_expansion_mode": settings.expansion}
    if len(json.dumps(payload, ensure_ascii=True).encode("utf-8")) > 64 * 1024**2:
        raise ValueError("Request exceeds 64 MB. Use HTTPS or mm_file:// media references.")
    return payload


def _task_id(value) -> str:
    task_id = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
        raise ValueError("MiniMax returned an invalid task ID.")
    return task_id


def _request(session, method, url, key, **kwargs):
    try:
        with session.request(method, url, headers={"Authorization": f"Bearer {key}"},
                             timeout=(15, 120), allow_redirects=False, **kwargs) as response:
            try:
                body = response.json()
            except ValueError:
                raise APIError(f"MiniMax returned HTTP {response.status_code} without valid JSON.", response.status_code)
            if not isinstance(body, dict):
                raise APIError("MiniMax returned an invalid response.")
            base_error = body.get("base_resp") or {}
            if response.status_code >= 300 or body.get("error") or base_error.get("status_code", 0):
                error = body.get("error") or {}
                message = error.get("message", "") if isinstance(error, dict) else str(error)
                message = str(message or base_error.get("status_msg") or "Request failed.")[:800]
                hint = {401: " Check your MiniMax API key.", 402: " Check pay-as-you-go balance/eligibility.",
                        429: " MiniMax rate limit reached."}.get(response.status_code, "")
                raise APIError(f"MiniMax HTTP {response.status_code}: {message}{hint}", response.status_code)
            return body
    except requests.RequestException as exc:
        # Avoid including signed URLs, credentials or request bodies in errors.
        raise APIError("MiniMax network request failed; check connectivity and resume the saved task if available.") from exc


def _write_metadata(path: Path, metadata: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _download(url: str, target: Path, stop: threading.Event):
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("MiniMax returned an invalid output video URL.")
    temporary = target.with_suffix(".mp4.part")
    try:
        # Deliberately separate from authenticated API requests: CDN receives no API key.
        with requests.get(url, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(256 * 1024):
                    if stop.is_set():
                        raise Stopped()
                    output.write(chunk)
        with temporary.open("rb") as video:
            header = video.read(12)
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise ValueError("MiniMax download was not an MP4 video. Resume to try downloading again.")
        temporary.replace(target)
    except requests.RequestException as exc:
        raise APIError("Video download failed. Resume the task to retry downloading without generating again.") from exc
    finally:
        temporary.unlink(missing_ok=True)


def run_job(settings: Settings, *, progress=lambda status: None, submitted=lambda task_id: None,
            stop=None, resume_id="", session=None) -> Result:
    """Validate everything before uploading; never retry a paid create request.

    Stop interrupts local waiting only. The provider cannot cancel a running task.
    Task JSON intentionally excludes credentials, source URLs and encoded media.
    """
    stop = stop if stop is not None else threading.Event()
    task_id = ""
    metadata_path = None
    metadata = {}
    own_session = session is None
    session = session if session is not None else requests.Session()

    def check_stop():
        if stop.is_set():
            raise Stopped()

    try:
        base = settings.base_url.rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Use an HTTPS MiniMax API base URL.")
        if not settings.api_key.strip():
            raise ValueError("Enter a MiniMax pay-as-you-go API key or set MINIMAX_API_KEY.")
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        if resume_id:
            task_id = _task_id(resume_id)
            metadata_path = settings.output_dir / f"h3_{task_id}.json"
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            # Resuming does not inspect/upload media or issue another create request.
        else:
            progress("Validating reference media...")
            # Check model/prompt/counts before expensive inspection or any upload.
            build_payload(settings, images=["mm_file://pending"] * len(settings.images),
                          videos=["mm_file://pending"] * len(settings.videos),
                          audio=["mm_file://pending"] * len(settings.audio))
            media = inspect_references(settings.images, settings.videos, settings.audio, settings.base_dir)
            refs = {"image": [], "video": [], "audio": []}
            for index, item in enumerate(media, 1):
                check_stop()
                if item.path is None and item.data is None:
                    refs[item.kind].append(item.source)
                    continue
                progress(f"Uploading reference {index}/{len(media)}: {item.filename}")
                with (item.path.open("rb") if item.path else io.BytesIO(item.data)) as stream:
                    body = _request(session, "POST", base + "/v1/files/upload", settings.api_key,
                                    data={"purpose": "video_generation_input"},
                                    files={"file": (item.filename, stream)})
                file_id = str((body.get("file") or {}).get("file_id") or "")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
                    raise APIError("MiniMax upload did not return a valid file ID.")
                refs[item.kind].append(f"mm_file://{file_id}")
            payload = build_payload(settings, images=refs["image"], videos=refs["video"], audio=refs["audio"])
            check_stop()
            progress("Submitting MiniMax H3 generation...")
            body = _request(session, "POST", base + "/v2/video_generation", settings.api_key, json=payload)
            task_id = _task_id(body.get("task_id"))
            metadata_path = settings.output_dir / f"h3_{task_id}.json"
            metadata = {"schema": 1, "task_id": task_id, "model": settings.model,
                        "prompt": settings.prompt.strip(), "resolution": settings.resolution,
                        "duration": settings.duration, "ratio": settings.ratio,
                        "reference_counts": {kind: len(values) for kind, values in refs.items()},
                        "status": "queued"}
            # Notify even if writing metadata fails, so the UI retains the paid task ID.
            submitted(task_id)
            _write_metadata(metadata_path, metadata)
        deadline = time.monotonic() + max(1, settings.timeout)
        retries = 0
        while time.monotonic() < deadline:
            check_stop()
            try:
                body = _request(session, "GET", base + f"/v2/query/video_generation/{task_id}", settings.api_key)
                retries = 0
            except APIError as exc:
                if exc.status and exc.status != 429 and exc.status < 500:
                    raise
                retries += 1
                if retries > 5:
                    raise
                progress("Query temporarily unavailable; retrying the saved task...")
                stop.wait(min(30, max(0.1, settings.poll_interval) * 2**retries))
                continue
            task = body.get("task") or {}
            status = str(task.get("status", "")).lower()
            if status not in {"queued", "running", "succeeded", "failed", "cancelled"}:
                raise APIError("MiniMax query returned an unknown task status. Resume the saved task later.")
            metadata.update(task_id=task_id, status=status)
            _write_metadata(metadata_path, metadata)
            progress(f"Task {task_id}: {status}")
            if status in {"failed", "cancelled"}:
                error = task.get("error") or body.get("error") or {}
                message = error.get("message", "") if isinstance(error, dict) else str(error)
                return Result(task_id=task_id, status=status, error=message or f"MiniMax task {status}.", metadata_path=str(metadata_path))
            if status == "succeeded":
                url = (task.get("content") or {}).get("url")
                if not url:
                    raise APIError("Task succeeded without a video URL. Resume to query again.")
                progress("Downloading generated video...")
                target = settings.output_dir / f"h3_{task_id}.mp4"
                _download(url, target, stop)
                metadata.update(output_path=str(target), usage=task.get("usage", {}),
                                resolution=task.get("resolution", metadata.get("resolution")),
                                ratio=task.get("ratio", metadata.get("ratio")))
                _write_metadata(metadata_path, metadata)
                return Result(True, task_id, "succeeded", str(target), metadata_path=str(metadata_path))
            stop.wait(max(0.1, settings.poll_interval))
        raise TimeoutError("Waiting timed out. Resume the saved task to continue without paying for a new generation.")
    except Stopped:
        return Result(task_id=task_id, status="stopped", error="Stopped waiting. Submitted tasks continue at MiniMax; use Resume to retrieve the result.",
                      metadata_path=str(metadata_path or ""))
    except Exception as exc:
        message = str(exc)
        if settings.api_key:
            message = message.replace(settings.api_key, "[redacted]")
        return Result(task_id=task_id, error=message, metadata_path=str(metadata_path or ""))
    finally:
        if own_session:
            session.close()
