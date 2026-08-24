from __future__ import annotations

import glob
import html
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec


YOUTUBE_DOWNLOADER_NODE_KIND = "youtube_downloader"
YOUTUBE_DOWNLOADER_NODE_ALIASES = [
    "youtube downloader",
    "youtube",
    "yt_downloader",
    "yt downloader",
    "youtube_to_mp4",
    "youtube to mp4",
]
YOUTUBE_DOWNLOADER_NODE_KINDS = {YOUTUBE_DOWNLOADER_NODE_KIND, *YOUTUBE_DOWNLOADER_NODE_ALIASES}

YOUTUBE_DOWNLOADER_BODY_W = 440
YOUTUBE_DOWNLOADER_BODY_H = 262

MP4_OUTPUT_PARAM = "mp4_path"
MP3_OUTPUT_PARAM = "mp3_path"
TRANSCRIPT_OUTPUT_PARAM = "transcript_md"
OUTPUT_PARAMS = (MP4_OUTPUT_PARAM, MP3_OUTPUT_PARAM, TRANSCRIPT_OUTPUT_PARAM)

_PARAM_URL = "__youtube_url"
_PARAM_OUTPUT_DIR = "__youtube_output_dir"
_PARAM_EXTRACT_MP3 = "__youtube_extract_mp3"
_PARAM_WRITE_TRANSCRIPT = "__youtube_write_transcript"
_PARAM_LANGUAGE = "__youtube_language"
_PARAM_TITLE = "__youtube_title"
_PARAM_STATUS = "__youtube_status"
_HIDDEN_PARAM = "__ui_hidden_params"

_INTERNAL_DEFAULTS = {
    _PARAM_URL: "",
    _PARAM_OUTPUT_DIR: "",
    _PARAM_EXTRACT_MP3: "false",
    _PARAM_WRITE_TRANSCRIPT: "true",
    _PARAM_LANGUAGE: "en",
    _PARAM_TITLE: "",
    _PARAM_STATUS: "",
}

_VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class YouTubeJobSettings:
    url: str
    output_dir: Path
    extract_mp3: bool = False
    write_transcript: bool = True
    language: str = "en"


@dataclass
class YouTubeJobResult:
    ok: bool
    mp4_path: str = ""
    mp3_path: str = ""
    transcript_md: str = ""
    title: str = ""
    video_id: str = ""
    status: str = ""
    error: str = ""
    details: str = ""


def _param_value(model, name: str, default: str = "") -> str:
    if model is None:
        return default
    key = str(name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "") or "").strip().lower() == key:
            return str(entry.get("value", "") or "")
    return default


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        setattr(model, "params", params)
    if not isinstance(params, list):
        params = list(params)
        setattr(model, "params", params)
    key = str(name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    hidden_entry = None
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "") or "").strip().lower() == _HIDDEN_PARAM:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": _HIDDEN_PARAM, "value": ""}
        params.append(hidden_entry)
    hidden = {
        part.strip().lower()
        for part in str(hidden_entry.get("value", "") or "").split(",")
        if part.strip()
    }
    for name in names or []:
        key = str(name or "").strip().lower()
        if key:
            hidden.add(key)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_param_value(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = str(name or "").strip().lower()
    text = str(value or "")
    found = False
    changed = False
    for entry in params:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "") or "").strip().lower() != key:
            continue
        found = True
        if str(entry.get("value", "") or "") != text:
            entry["value"] = text
            changed = True
        break
    if not found:
        params.append({"name": name, "value": text})
        changed = True
    model.params = params
    _ensure_hidden_params(model, [*_INTERNAL_DEFAULTS.keys(), *OUTPUT_PARAMS])
    if not changed or not notify_scene:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is not None and hasattr(scene, "set_node_params"):
        try:
            scene.set_node_params(model.name, list(getattr(model, "params", None) or []), rebuild=False, emit=True)
            return
        except TypeError:
            try:
                scene.set_node_params(model.name, list(getattr(model, "params", None) or []))
                return
            except Exception:
                pass
        except Exception:
            pass
    if scene is not None and hasattr(scene, "paramChanged"):
        try:
            scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
        except Exception:
            pass


def _workflow_dir_from_ref(raw) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        path = Path(text).expanduser()
        return path.parent if path.suffix else path
    except Exception:
        return None


def _workflow_refs_from_widget(widget):
    seen = set()
    current = widget
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        try:
            path = getattr(current, "_current_path", None)
        except Exception:
            path = None
        if path:
            yield path
        try:
            current = current.parentWidget()
        except Exception:
            break


def _workflow_dir_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    candidates = []
    if scene is not None:
        try:
            for view in scene.views() or []:
                candidates.extend(_workflow_refs_from_widget(view))
                try:
                    candidates.extend(_workflow_refs_from_widget(view.window()))
                except Exception:
                    pass
        except Exception:
            pass
        try:
            candidates.append(getattr(scene, "_filename", None))
        except Exception:
            pass

    try:
        candidates.extend(_workflow_refs_from_widget(node_item.window()))
    except Exception:
        pass
    try:
        candidates.extend(_workflow_refs_from_widget(QtWidgets.QApplication.activeWindow()))
    except Exception:
        pass

    for raw in candidates:
        workflow_dir = _workflow_dir_from_ref(raw)
        if workflow_dir is not None:
            return workflow_dir
    return None


def _default_output_dir(node_item) -> Path:
    workflow_dir = _workflow_dir_for_node(node_item)
    if workflow_dir is not None:
        return workflow_dir / "Output"
    return Path(tempfile.gettempdir()) / "EchoGraph" / "youtube_downloads"


def _resolve_output_dir(node_item, raw: str) -> Path:
    text = (raw or "").strip().strip('"').strip("'").strip()
    if not text:
        return _default_output_dir(node_item)
    path = Path(text).expanduser()
    if not path.is_absolute():
        base = _workflow_dir_for_node(node_item)
        if base is None:
            base = Path.cwd()
        path = base / path
    return path


def _dialog_parent(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                if win is not None:
                    return win
        except Exception:
            pass
    try:
        win = node_item.window()
        if win is not None:
            return win
    except Exception:
        pass
    try:
        aw = QtWidgets.QApplication.activeWindow()
        if aw is not None and aw.isWindow():
            return aw
    except Exception:
        pass
    return None


def _coerce_bool(value: str, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _extract_youtube_url(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if _video_id_from_url(raw):
        return raw
    hit = re.search(r"https?://[^\s<>\"]*(?:youtube\.com|youtu\.be)[^\s<>\"]*", raw, re.IGNORECASE)
    if hit:
        return hit.group(0).rstrip(").,;]")
    hit = re.search(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", raw)
    return hit.group(1) if hit else raw


def _video_id_from_url(url: str) -> str:
    text = str(url or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    try:
        parsed = urlparse(text)
    except Exception:
        return ""
    host = (parsed.netloc or "").lower()
    path_parts = [part for part in (parsed.path or "").split("/") if part]
    if host.endswith("youtu.be") and path_parts:
        candidate = path_parts[0]
        return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else ""
    if host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        query_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", query_id):
            return query_id
        for part in path_parts:
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", part):
                return part
    return ""


def _find_ffmpeg_exe() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg  # type: ignore

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).exists():
            return str(bundled)
    except Exception:
        pass
    return "ffmpeg"


def _run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        creationflags=_CREATE_NO_WINDOW,
    )


def _existing_video_candidates(paths: list[Path]) -> list[Path]:
    out = []
    seen = set()
    for path in paths:
        try:
            p = Path(path)
        except Exception:
            continue
        key = str(p).lower()
        if key in seen or not p.exists() or not p.is_file():
            continue
        if p.suffix.lower() not in _VIDEO_EXTS:
            continue
        seen.add(key)
        out.append(p)
    return out


def _find_downloaded_video_path(info: dict, prepared_path: str, output_dir: Path, video_id: str) -> Path:
    candidates: list[Path] = []
    for item in info.get("requested_downloads") or []:
        if not isinstance(item, dict):
            continue
        for key in ("filepath", "_filename", "filename"):
            raw = item.get(key)
            if raw:
                candidates.append(Path(str(raw)))
    for key in ("filepath", "_filename", "filename"):
        raw = info.get(key)
        if raw:
            candidates.append(Path(str(raw)))
    if prepared_path:
        candidates.append(Path(str(prepared_path)))
        prepared = Path(str(prepared_path))
        for ext in _VIDEO_EXTS:
            candidates.append(prepared.with_suffix(ext))
    if video_id:
        candidates.extend(Path(p) for p in glob.glob(str(output_dir / f"*[{video_id}].*")))
    found = _existing_video_candidates(candidates)
    if not found:
        all_files = [p for p in output_dir.glob("*") if p.is_file() and p.suffix.lower() in _VIDEO_EXTS]
        found = sorted(all_files, key=lambda p: p.stat().st_mtime, reverse=True)
    if not found:
        raise RuntimeError("yt-dlp completed, but no downloaded video file was found.")
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _ensure_mp4(video_path: Path, ffmpeg: str, progress: Callable[[str], None]) -> Path:
    if video_path.suffix.lower() == ".mp4":
        return video_path
    target = video_path.with_suffix(".mp4")
    progress("Converting video to MP4...")
    remux_cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(target),
    ]
    remux_result = _run_subprocess(remux_cmd)
    if remux_result.returncode == 0 and target.exists():
        return target
    transcode_cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(target),
    ]
    transcode_result = _run_subprocess(transcode_cmd)
    if transcode_result.returncode != 0 or not target.exists():
        details = "\n\n".join(
            [
                "MP4 remux stderr:",
                remux_result.stderr or "",
                "MP4 transcode stderr:",
                transcode_result.stderr or "",
            ]
        )
        raise RuntimeError(f"ffmpeg failed to create MP4.\n{details}")
    return target


def _extract_mp3(mp4_path: Path, output_dir: Path, ffmpeg: str, progress: Callable[[str], None]) -> Path:
    mp3_path = output_dir / f"{mp4_path.stem}.mp3"
    progress("Extracting MP3...")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(mp4_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-b:a",
        "192k",
        "-c:a",
        "libmp3lame",
        str(mp3_path),
    ]
    result = _run_subprocess(cmd)
    if result.returncode != 0 or not mp3_path.exists():
        raise RuntimeError(result.stderr.strip() or "ffmpeg failed to extract MP3.")
    return mp3_path


def _caption_languages(language: str) -> list[str]:
    lang = str(language or "en").strip() or "en"
    out = [lang]
    if lang.lower() == "en":
        out.extend(["en-US", "en-GB"])
    return list(dict.fromkeys(out))


def _clean_caption_text(text: str) -> str:
    clean = html.unescape(str(text or ""))
    clean = re.sub(r"<[^>]+>", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _try_youtube_transcript_api(video_id: str, language: str) -> tuple[list[str] | None, str]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except Exception:
        return None, "youtube-transcript-api is not installed."
    langs = _caption_languages(language)
    try:
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            data = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
        else:
            data = YouTubeTranscriptApi().fetch(video_id, languages=langs)
    except Exception as exc:
        return None, str(exc)
    lines = []
    prev = None
    for item in data or []:
        if isinstance(item, dict):
            raw = item.get("text", "")
        else:
            raw = getattr(item, "text", "")
        line = _clean_caption_text(raw)
        if line and line != prev:
            lines.append(line)
            prev = line
    return (lines or None), ""


def _clean_vtt_lines(vtt_path: Path) -> list[str]:
    out = []
    timestamp = re.compile(r"-->\s")
    with vtt_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
                continue
            if timestamp.search(line) or "align:" in line or "position:" in line:
                continue
            line = _clean_caption_text(line)
            if line:
                out.append(line)
    dedup = []
    prev = None
    for line in out:
        if line != prev:
            dedup.append(line)
        prev = line
    return dedup


def _try_yt_dlp_subtitles(url: str, video_id: str, language: str) -> tuple[list[str] | None, str]:
    try:
        import yt_dlp  # type: ignore
    except Exception:
        return None, "yt-dlp is not installed."
    langs = _caption_languages(language)
    with tempfile.TemporaryDirectory(prefix="qubit_youtube_subs_") as tmp:
        tmp_dir = Path(tmp)
        ydl_opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": langs,
            "subtitlesformat": "vtt",
            "outtmpl": str(tmp_dir / f"{video_id}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extractor_args": {"youtube": {"player_client": ["web", "ios", "android"]}},
        }
        try:
            yt_dlp.YoutubeDL(ydl_opts).download([url])
        except Exception as exc:
            last_error = str(exc)
        else:
            last_error = ""
        hits = sorted(tmp_dir.glob(f"{video_id}*.vtt"))
        for vtt_path in hits:
            lines = _clean_vtt_lines(vtt_path)
            if lines:
                return lines, ""
        return None, last_error or "No subtitles were available."


def _write_transcript_md(
    output_dir: Path,
    mp4_path: Path,
    title: str,
    url: str,
    video_id: str,
    lines: list[str],
    source_label: str,
) -> Path:
    stem = mp4_path.stem if mp4_path else (video_id or "youtube_transcript")
    out_path = output_dir / f"{stem}.md"
    heading = (title or video_id or "YouTube Transcript").strip()
    parts = [
        f"# {heading}",
        "",
        f"Source: {url}",
        f"Video ID: {video_id or '(unknown)'}",
        f"Transcript source: {source_label}",
        "",
    ]
    for line in lines:
        clean = _clean_caption_text(line)
        if clean:
            parts.extend([clean, ""])
    out_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return out_path


def _download_youtube_mp4(settings: YouTubeJobSettings, progress: Callable[[str], None]) -> YouTubeJobResult:
    url = _extract_youtube_url(settings.url)
    if not url:
        return YouTubeJobResult(ok=False, status="Enter a YouTube URL.", error="Enter a YouTube URL.")
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        return YouTubeJobResult(
            ok=False,
            status="yt-dlp is not installed. Run setup.bat to update the app environment.",
            error=str(exc),
        )

    output_dir = Path(settings.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = _find_ffmpeg_exe()
    warnings = []
    last_progress = {"text": ""}

    def _progress_hook(data):
        try:
            status = str(data.get("status") or "")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                downloaded = data.get("downloaded_bytes") or 0
                if total:
                    pct = (float(downloaded) / float(total)) * 100.0
                    text = f"Downloading {pct:0.0f}%..."
                else:
                    text = "Downloading..."
            elif status == "finished":
                text = "Download finished; preparing MP4..."
            else:
                text = status or "Working..."
            if text and text != last_progress.get("text"):
                last_progress["text"] = text
                progress(text)
        except Exception:
            pass

    def _post_hook(data):
        try:
            status = str(data.get("status") or "")
            pp = str(data.get("postprocessor") or "ffmpeg")
            if status:
                progress(f"{pp}: {status}...")
        except Exception:
            pass

    progress("Reading video metadata...")
    ydl_opts = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "outtmpl": str(output_dir / "%(title).180B [%(id)s].%(ext)s"),
        "noplaylist": True,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "windowsfilenames": True,
        "progress_hooks": [_progress_hook],
        "postprocessor_hooks": [_post_hook],
        "extractor_args": {"youtube": {"player_client": ["web", "ios", "android"]}},
    }
    try:
        ffmpeg_path = Path(ffmpeg)
        if ffmpeg_path.exists():
            ydl_opts["ffmpeg_location"] = str(ffmpeg_path.parent)
    except Exception:
        pass

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not isinstance(info, dict):
                raise RuntimeError("yt-dlp did not return video metadata.")
            if "entries" in info and info.get("entries"):
                info = next((entry for entry in info.get("entries") or [] if isinstance(entry, dict)), info)
            prepared_path = ydl.prepare_filename(info)
    except Exception as exc:
        return YouTubeJobResult(ok=False, status="YouTube download failed.", error=str(exc))

    title = str(info.get("title") or "").strip()
    video_id = str(info.get("id") or _video_id_from_url(url) or "").strip()
    try:
        video_path = _find_downloaded_video_path(info, prepared_path, output_dir, video_id)
        mp4_path = _ensure_mp4(video_path, ffmpeg, progress)
    except Exception as exc:
        return YouTubeJobResult(
            ok=False,
            title=title,
            video_id=video_id,
            status="MP4 creation failed.",
            error=str(exc),
        )

    mp3_path = Path()
    if settings.extract_mp3:
        try:
            mp3_path = _extract_mp3(mp4_path, output_dir, ffmpeg, progress)
        except Exception as exc:
            warnings.append(f"MP3 failed: {exc}")

    transcript_path = Path()
    if settings.write_transcript:
        progress("Writing transcript...")
        lines, transcript_error = _try_youtube_transcript_api(video_id, settings.language)
        source_label = "youtube-transcript-api"
        if not lines:
            lines, fallback_error = _try_yt_dlp_subtitles(url, video_id, settings.language)
            source_label = "yt-dlp subtitles"
            transcript_error = fallback_error or transcript_error
        if lines:
            try:
                transcript_path = _write_transcript_md(
                    output_dir,
                    mp4_path,
                    title,
                    url,
                    video_id,
                    lines,
                    source_label,
                )
            except Exception as exc:
                warnings.append(f"Transcript write failed: {exc}")
        else:
            warnings.append(f"Transcript failed: {transcript_error or 'no captions/subtitles available'}")

    if warnings:
        status = "MP4 downloaded with warnings: " + " | ".join(warnings)
    else:
        status = "MP4 downloaded."
    return YouTubeJobResult(
        ok=True,
        mp4_path=str(mp4_path),
        mp3_path=str(mp3_path) if mp3_path else "",
        transcript_md=str(transcript_path) if transcript_path else "",
        title=title,
        video_id=video_id,
        status=status,
        error=" | ".join(warnings),
    )


class YouTubeDownloadThread(QtCore.QThread):
    progressChanged = QtCore.Signal(str)
    resultReady = QtCore.Signal(object)

    def __init__(self, settings: YouTubeJobSettings, parent=None):
        super().__init__(parent)
        self._settings = settings

    def run(self):
        try:
            result = _download_youtube_mp4(self._settings, self.progressChanged.emit)
        except Exception as exc:
            result = YouTubeJobResult(ok=False, status="YouTube job failed.", error=str(exc))
        self.resultReady.emit(result)


def _connected_url_from_graph(node_item) -> str:
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return ""
    try:
        in_edges = scene._ordered_in_edges(node_item)
    except Exception:
        try:
            in_edges = scene._in_edges(node_item)
        except Exception:
            in_edges = []
    for edge in in_edges or []:
        dst_port = str(getattr(edge, "dst_port_name", "") or "").strip().lower()
        if dst_port and dst_port != "url":
            continue
        try:
            text = scene.resolve_text_value(edge.src)
        except Exception:
            text = ""
        url = _extract_youtube_url(text)
        if url:
            return url
    return ""


class YouTubeDownloaderWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._thread: YouTubeDownloadThread | None = None
        self._syncing = False
        self._scene_connected = False
        self._connected_url = ""

        build_ports(node_item)
        self.setObjectName("YouTubeDownloaderWidget")
        self.setMinimumSize(YOUTUBE_DOWNLOADER_BODY_W, YOUTUBE_DOWNLOADER_BODY_H)
        self.setStyleSheet(
            "QWidget#YouTubeDownloaderWidget{background:#0f1216;border:1px solid #334155;border-radius:0px;}"
            "QLabel{color:#cbd5e1;}"
            "QLineEdit{background:#111827;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:3px 6px;}"
            "QLineEdit:disabled{background:#1f2937;color:#94a3b8;border:1px dashed #475569;}"
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#334155;}"
            "QPushButton:disabled{background:#263142;color:#94a3b8;}"
            "QPushButton#YouTubeDownloadButton{background:#38bdf8;color:#062033;border:1px solid #7dd3fc;font-weight:600;}"
            "QPushButton#YouTubeDownloadButton:hover{background:#7dd3fc;}"
            "QPushButton#YouTubeDownloadButton:pressed{background:#0ea5e9;}"
            "QPushButton#YouTubeDownloadButton:disabled{background:#334155;color:#94a3b8;border:1px solid #475569;}"
            "QCheckBox{color:#cbd5e1;}"
        )

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._url_edit = QtWidgets.QLineEdit()
        self._url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self._url_edit.textEdited.connect(self._commit_controls)
        root.addLayout(self._row("URL", self._url_edit), 0)

        self._output_dir_edit = QtWidgets.QLineEdit()
        self._output_dir_edit.editingFinished.connect(self._commit_controls)
        browse_btn = QtWidgets.QToolButton()
        browse_btn.setToolTip("Choose output folder")
        browse_btn.setFixedSize(26, 24)
        try:
            style = QtWidgets.QApplication.style()
            icon = style.standardIcon(QtWidgets.QStyle.SP_DirOpenIcon) if style is not None else QtGui.QIcon()
            if icon.isNull() and style is not None:
                icon = style.standardIcon(QtWidgets.QStyle.SP_DialogOpenButton)
            browse_btn.setIcon(icon)
            browse_btn.setIconSize(QtCore.QSize(16, 16))
        except Exception:
            browse_btn.setText("...")
        browse_btn.clicked.connect(self._browse_output_dir)
        folder_row = self._row("Output", self._output_dir_edit)
        folder_row.addWidget(browse_btn, 0)
        root.addLayout(folder_row, 0)

        option_row = QtWidgets.QHBoxLayout()
        option_row.setContentsMargins(0, 0, 0, 0)
        option_row.setSpacing(10)
        self._mp3_check = QtWidgets.QCheckBox("MP3")
        self._transcript_check = QtWidgets.QCheckBox("Transcript")
        self._mp3_check.toggled.connect(self._commit_controls)
        self._transcript_check.toggled.connect(self._commit_controls)
        option_row.addWidget(self._mp3_check, 0)
        option_row.addWidget(self._transcript_check, 0)
        option_row.addWidget(QtWidgets.QLabel("Lang"), 0)
        self._language_edit = QtWidgets.QLineEdit()
        self._language_edit.setMaximumWidth(52)
        self._language_edit.editingFinished.connect(self._commit_controls)
        option_row.addWidget(self._language_edit, 0)
        option_row.addStretch(1)
        root.addLayout(option_row, 0)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)
        self._download_btn = QtWidgets.QPushButton("Download")
        self._download_btn.setObjectName("YouTubeDownloadButton")
        self._download_btn.clicked.connect(self._on_download)
        self._open_btn = QtWidgets.QPushButton("Open")
        self._open_btn.clicked.connect(self._open_output_dir)
        button_row.addWidget(self._download_btn, 1)
        button_row.addWidget(self._open_btn, 0)
        root.addLayout(button_row, 0)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(34)
        self._status.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        root.addWidget(self._status, 0)

        self._mp4_out = self._output_line()
        self._mp3_out = self._output_line()
        self._transcript_out = self._output_line()
        root.addLayout(self._row("MP4", self._mp4_out), 0)
        root.addLayout(self._row("MP3", self._mp3_out), 0)
        root.addLayout(self._row("MD", self._transcript_out), 0)

        self._refresh_from_params()
        self._schedule_scene_sync()

    def sizeHint(self):
        return QtCore.QSize(YOUTUBE_DOWNLOADER_BODY_W, YOUTUBE_DOWNLOADER_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(YOUTUBE_DOWNLOADER_BODY_W, YOUTUBE_DOWNLOADER_BODY_H)

    def _row(self, label: str, widget: QtWidgets.QWidget) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        lab = QtWidgets.QLabel(label)
        lab.setMinimumWidth(44)
        row.addWidget(lab, 0)
        row.addWidget(widget, 1)
        return row

    def _output_line(self) -> QtWidgets.QLineEdit:
        edit = QtWidgets.QLineEdit()
        edit.setReadOnly(True)
        edit.setToolTip("Generated output path.")
        return edit

    def _model(self):
        return getattr(self._node_item, "model", None)

    def _schedule_scene_sync(self) -> None:
        QtCore.QTimer.singleShot(0, self._sync_scene)
        QtCore.QTimer.singleShot(120, self._sync_scene)

    def _sync_scene(self) -> None:
        scene = self._node_item.scene() if hasattr(self._node_item, "scene") else None
        if scene is not None and not self._scene_connected:
            if hasattr(scene, "linksChanged"):
                try:
                    scene.linksChanged.connect(self._on_graph_changed)
                except Exception:
                    pass
            if hasattr(scene, "paramChanged"):
                try:
                    scene.paramChanged.connect(self._on_graph_changed)
                except Exception:
                    pass
            self._scene_connected = True
        self._refresh_connected_url()

    def _on_graph_changed(self, *_args) -> None:
        QtCore.QTimer.singleShot(0, self._refresh_connected_url)

    def _refresh_connected_url(self) -> None:
        connected = _connected_url_from_graph(self._node_item)
        if connected == self._connected_url:
            return
        self._connected_url = connected
        self._syncing = True
        try:
            self._url_edit.blockSignals(True)
            if connected:
                self._url_edit.setText(connected)
                self._url_edit.setEnabled(False)
                self._url_edit.setToolTip("Driven by connected URL input.")
            else:
                self._url_edit.setEnabled(True)
                self._url_edit.setToolTip("")
                self._url_edit.setText(_param_value(self._model(), _PARAM_URL, ""))
        finally:
            self._url_edit.blockSignals(False)
            self._syncing = False

    def _refresh_from_params(self) -> None:
        model = self._model()
        self._syncing = True
        try:
            self._url_edit.setText(_param_value(model, _PARAM_URL, ""))
            output_dir = _param_value(model, _PARAM_OUTPUT_DIR, "")
            self._output_dir_edit.setText(output_dir or str(_default_output_dir(self._node_item)))
            self._mp3_check.setChecked(_coerce_bool(_param_value(model, _PARAM_EXTRACT_MP3, "false"), False))
            self._transcript_check.setChecked(
                _coerce_bool(_param_value(model, _PARAM_WRITE_TRANSCRIPT, "true"), True)
            )
            self._language_edit.setText(_param_value(model, _PARAM_LANGUAGE, "en") or "en")
            self._status.setText(_param_value(model, _PARAM_STATUS, "") or "Ready.")
            self._mp4_out.setText(_param_value(model, MP4_OUTPUT_PARAM, ""))
            self._mp3_out.setText(_param_value(model, MP3_OUTPUT_PARAM, ""))
            self._transcript_out.setText(_param_value(model, TRANSCRIPT_OUTPUT_PARAM, ""))
        finally:
            self._syncing = False
        self._refresh_connected_url()

    def _commit_controls(self) -> None:
        if self._syncing:
            return
        if not self._connected_url:
            _set_param_value(self._node_item, _PARAM_URL, self._url_edit.text().strip(), notify_scene=False)
        default_dir = str(_default_output_dir(self._node_item))
        output_dir = self._output_dir_edit.text().strip()
        stored_dir = "" if output_dir == default_dir else output_dir
        _set_param_value(self._node_item, _PARAM_OUTPUT_DIR, stored_dir, notify_scene=False)
        _set_param_value(
            self._node_item,
            _PARAM_EXTRACT_MP3,
            "true" if self._mp3_check.isChecked() else "false",
            notify_scene=False,
        )
        _set_param_value(
            self._node_item,
            _PARAM_WRITE_TRANSCRIPT,
            "true" if self._transcript_check.isChecked() else "false",
            notify_scene=False,
        )
        _set_param_value(self._node_item, _PARAM_LANGUAGE, self._language_edit.text().strip() or "en", notify_scene=False)

    def _browse_output_dir(self) -> None:
        parent = _dialog_parent(self._node_item) or self
        start = self._output_dir_edit.text().strip() or str(_default_output_dir(self._node_item))
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Choose YouTube Output Folder", start)
        if not path:
            return
        self._output_dir_edit.setText(path)
        self._commit_controls()

    def _open_output_dir(self) -> None:
        try:
            output_dir = _resolve_output_dir(self._node_item, self._output_dir_edit.text().strip())
            output_dir.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(output_dir)))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(_dialog_parent(self._node_item) or self, "YouTube Downloader", str(exc))

    def _set_busy(self, busy: bool) -> None:
        busy = bool(busy)
        self._download_btn.setEnabled(not busy)
        self._open_btn.setEnabled(not busy)
        self._output_dir_edit.setEnabled(not busy)
        self._mp3_check.setEnabled(not busy)
        self._transcript_check.setEnabled(not busy)
        self._language_edit.setEnabled(not busy)
        if not self._connected_url:
            self._url_edit.setEnabled(not busy)
        self._download_btn.setText("Working..." if busy else "Download")
        try:
            self._node_item.setBusyState(busy, "youtube" if busy else "")
        except Exception:
            pass

    def _set_status(self, text: str) -> None:
        clean = str(text or "").strip()
        self._status.setText(clean)
        _set_param_value(self._node_item, _PARAM_STATUS, clean, notify_scene=False)

    def _on_download(self) -> None:
        if self._thread is not None:
            return
        self._commit_controls()
        url = self._connected_url or self._url_edit.text().strip()
        url = _extract_youtube_url(url)
        if not url:
            self._set_status("Enter a YouTube URL.")
            return
        try:
            output_dir = _resolve_output_dir(self._node_item, self._output_dir_edit.text().strip())
        except Exception as exc:
            self._set_status(str(exc))
            return
        settings = YouTubeJobSettings(
            url=url,
            output_dir=output_dir,
            extract_mp3=self._mp3_check.isChecked(),
            write_transcript=self._transcript_check.isChecked(),
            language=self._language_edit.text().strip() or "en",
        )
        self._set_busy(True)
        self._set_status("Starting YouTube download...")
        self._thread = YouTubeDownloadThread(settings, self)
        self._thread.progressChanged.connect(self._set_status)
        self._thread.resultReady.connect(self._on_result)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_thread_finished(self) -> None:
        if self._thread is not None:
            try:
                self._thread.deleteLater()
            except Exception:
                pass
        self._thread = None
        self._set_busy(False)

    def _on_result(self, result_obj) -> None:
        result = result_obj if isinstance(result_obj, YouTubeJobResult) else YouTubeJobResult(False, error=str(result_obj))
        _set_param_value(self._node_item, MP4_OUTPUT_PARAM, result.mp4_path, notify_scene=False)
        _set_param_value(self._node_item, MP3_OUTPUT_PARAM, result.mp3_path, notify_scene=False)
        _set_param_value(self._node_item, TRANSCRIPT_OUTPUT_PARAM, result.transcript_md, notify_scene=False)
        _set_param_value(self._node_item, _PARAM_TITLE, result.title, notify_scene=False)
        final_status = result.status or ("Done." if result.ok else "YouTube job failed.")
        if result.error and not result.ok:
            final_status = f"{final_status} {result.error}".strip()
        _set_param_value(self._node_item, _PARAM_STATUS, final_status, notify_scene=True)
        try:
            model = self._model()
            if model is not None:
                model.info = final_status
        except Exception:
            pass
        self._mp4_out.setText(result.mp4_path)
        self._mp3_out.setText(result.mp3_path)
        self._transcript_out.setText(result.transcript_md)
        self._status.setText(final_status)
        if not result.ok:
            QtWidgets.QMessageBox.critical(
                _dialog_parent(self._node_item) or self,
                "YouTube Downloader",
                final_status,
            )


def build_ports(node_item) -> None:
    for name, default in _INTERNAL_DEFAULTS.items():
        _ensure_param(node_item, name, default)
    for name in OUTPUT_PARAMS:
        _ensure_param(node_item, name, "")
    model = getattr(node_item, "model", None)
    _ensure_hidden_params(model, [*_INTERNAL_DEFAULTS.keys(), *OUTPUT_PARAMS])
    try:
        setattr(node_item, "_default_named_input", "url")
        setattr(node_item, "_show_default_input_with_named", True)
        node_item.ensure_input("url")
    except Exception:
        pass
    try:
        for name in OUTPUT_PARAMS:
            node_item.ensure_output(name)
    except Exception:
        pass


def _ensure_body_space(node_item, bottom_y: int) -> None:
    try:
        pad = int(getattr(node_item, "_PADDING", 8))
        min_h = float(bottom_y + pad)
        if float(getattr(node_item, "height", 0.0) or 0.0) < min_h:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = min_h
    except Exception:
        pass


def _set_manual_ports(node_item, y_cursor: int) -> None:
    try:
        node_item._input_port_pos["url"] = (QtCore.QPointF(0.0, float(y_cursor + 18)), "url")
    except Exception:
        pass
    output_offsets = {
        MP4_OUTPUT_PARAM: 190,
        MP3_OUTPUT_PARAM: 218,
        TRANSCRIPT_OUTPUT_PARAM: 246,
    }
    for name, offset in output_offsets.items():
        try:
            node_item._output_port_pos[name.lower()] = (
                QtCore.QPointF(float(node_item.width), float(y_cursor + offset)),
                name,
            )
        except Exception:
            pass


def render_node_body(node_item, y_cursor: int) -> int:
    build_ports(node_item)
    body = YouTubeDownloaderWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    w = max(int(hint.width()), int(getattr(node_item, "width", YOUTUBE_DOWNLOADER_BODY_W) or YOUTUBE_DOWNLOADER_BODY_W))
    h = max(int(hint.height()), int(body.minimumSizeHint().height()))
    try:
        body.setMinimumWidth(w)
        body.setMaximumWidth(w)
        proxy.setMinimumWidth(w)
        proxy.setMaximumWidth(w)
        proxy.setPreferredSize(w, h)
    except Exception:
        pass
    proxy.resize(w, h)
    try:
        if int(getattr(node_item, "width", 0) or 0) < w:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.width = w
    except Exception:
        pass
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    _set_manual_ports(node_item, int(y_cursor))
    bottom_y = int(y_cursor) + h
    _ensure_body_space(node_item, bottom_y)
    return bottom_y


YOUTUBE_DOWNLOADER_SPEC = Spec(
    stripe_color="#dc2626",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
