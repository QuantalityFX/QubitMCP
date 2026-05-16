from __future__ import annotations

import cProfile
import contextlib
import functools
import pstats
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List


@dataclass(frozen=True)
class SpanStat:
    name: str
    calls: int
    total_s: float
    avg_s: float
    max_s: float


@dataclass(frozen=True)
class FunctionStat:
    name: str
    calls: int
    total_s: float
    self_s: float


@dataclass(frozen=True)
class ProfilerSnapshot:
    duration_s: float
    spans: List[SpanStat]
    functions: List[FunctionStat]


class ProfilerSession:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._profile: cProfile.Profile | None = None
        self._recording = False
        self._started_at = 0.0
        self._span_totals: Dict[str, list[float]] = {}
        self._last_snapshot = ProfilerSnapshot(0.0, [], [])

    @property
    def recording(self) -> bool:
        with self._lock:
            return bool(self._recording)

    @property
    def last_snapshot(self) -> ProfilerSnapshot:
        with self._lock:
            return self._last_snapshot

    def elapsed_s(self) -> float:
        with self._lock:
            if not self._recording:
                return float(self._last_snapshot.duration_s)
            return max(0.0, time.perf_counter() - self._started_at)

    def start(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._profile = cProfile.Profile()
            self._span_totals = {}
            self._started_at = time.perf_counter()
            self._recording = True
            self._profile.enable()

    def stop(self, *, function_limit: int = 80) -> ProfilerSnapshot:
        with self._lock:
            if not self._recording:
                return self._last_snapshot
            profile = self._profile
            duration_s = max(0.0, time.perf_counter() - self._started_at)
            self._recording = False
            self._profile = None
        if profile is not None:
            profile.disable()
        snapshot = ProfilerSnapshot(
            duration_s=duration_s,
            spans=self._build_span_stats(),
            functions=self._build_function_stats(profile, function_limit=function_limit),
        )
        with self._lock:
            self._last_snapshot = snapshot
        return snapshot

    def clear(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._span_totals = {}
            self._last_snapshot = ProfilerSnapshot(0.0, [], [])

    @contextlib.contextmanager
    def span(self, name: str) -> Iterator[None]:
        label = str(name or "").strip()
        with self._lock:
            enabled = bool(self._recording and label)
        if not enabled:
            yield
            return
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = max(0.0, time.perf_counter() - started)
            with self._lock:
                if not self._recording:
                    return
                bucket = self._span_totals.setdefault(label, [0.0, 0.0, 0.0])
                bucket[0] += 1.0
                bucket[1] += elapsed
                bucket[2] = max(bucket[2], elapsed)

    def _build_span_stats(self) -> List[SpanStat]:
        with self._lock:
            rows = list(self._span_totals.items())
        out: List[SpanStat] = []
        for name, values in rows:
            calls = max(0, int(values[0]))
            total_s = max(0.0, float(values[1]))
            max_s = max(0.0, float(values[2]))
            avg_s = (total_s / float(calls)) if calls else 0.0
            out.append(
                SpanStat(
                    name=name,
                    calls=calls,
                    total_s=total_s,
                    avg_s=avg_s,
                    max_s=max_s,
                )
            )
        out.sort(key=lambda stat: (-stat.total_s, stat.name))
        return out

    @staticmethod
    def _build_function_stats(
        profile: cProfile.Profile | None,
        *,
        function_limit: int,
    ) -> List[FunctionStat]:
        if profile is None:
            return []
        try:
            raw_stats = pstats.Stats(profile).stats
        except Exception:
            return []
        rows: List[FunctionStat] = []
        for func_key, values in raw_stats.items():
            try:
                filename, line_no, func_name = func_key
                primitive_calls, total_calls, self_s, total_s, _callers = values
            except Exception:
                continue
            call_count = int(total_calls or primitive_calls or 0)
            rows.append(
                FunctionStat(
                    name=f"{filename}:{int(line_no)}({func_name})",
                    calls=max(0, call_count),
                    total_s=max(0.0, float(total_s)),
                    self_s=max(0.0, float(self_s)),
                )
            )
        rows.sort(key=lambda stat: (-stat.total_s, -stat.self_s, stat.name))
        return rows[: max(1, int(function_limit))]


def profiler_log_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "logs" / "Profiler"


def export_snapshot_markdown(
    snapshot: ProfilerSnapshot,
    *,
    directory: str | Path | None = None,
    max_logs: int = 5,
) -> Path:
    out_dir = Path(directory) if directory is not None else profiler_log_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d_%H%M%S_%f")
    out_path = out_dir / f"profiler_capture_{stamp}.md"
    out_path.write_text(_snapshot_to_markdown(snapshot, generated_at=now), encoding="utf-8")
    _prune_profiler_logs(out_dir, max_logs=max_logs)
    return out_path


def _prune_profiler_logs(directory: Path, *, max_logs: int) -> None:
    keep = max(1, int(max_logs))
    try:
        files = sorted(
            directory.glob("profiler_capture_*.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return
    for stale in files[keep:]:
        try:
            stale.unlink()
        except Exception:
            pass


def _snapshot_to_markdown(snapshot: ProfilerSnapshot, *, generated_at: datetime) -> str:
    lines = [
        "# Profiler Capture",
        "",
        f"- Generated UTC: `{generated_at.isoformat()}`",
        f"- Duration: `{snapshot.duration_s:.6f} s`",
        f"- Process groups: `{len(snapshot.spans)}`",
        f"- Python rows: `{len(snapshot.functions)}`",
        "",
        "## Processes",
        "",
        "| Process | Calls | Total ms | Avg ms | Max ms |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    if snapshot.spans:
        for stat in snapshot.spans:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape_markdown_cell(stat.name),
                        str(stat.calls),
                        f"{stat.total_s * 1000.0:.3f}",
                        f"{stat.avg_s * 1000.0:.3f}",
                        f"{stat.max_s * 1000.0:.3f}",
                    ]
                )
                + " |"
            )
    else:
        lines.append("| No process spans captured | 0 | 0.000 | 0.000 | 0.000 |")

    lines.extend(
        [
            "",
            "## Python Functions",
            "",
            "| Python function | Calls | Cumulative ms | Self ms |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    if snapshot.functions:
        for stat in snapshot.functions:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape_markdown_cell(stat.name),
                        str(stat.calls),
                        f"{stat.total_s * 1000.0:.3f}",
                        f"{stat.self_s * 1000.0:.3f}",
                    ]
                )
                + " |"
            )
    else:
        lines.append("| No Python rows captured | 0 | 0.000 | 0.000 |")
    lines.append("")
    return "\n".join(lines)


def _escape_markdown_cell(value: str) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


_GLOBAL_PROFILER = ProfilerSession()


def get_profiler() -> ProfilerSession:
    return _GLOBAL_PROFILER


@contextlib.contextmanager
def profile_scope(name: str) -> Iterator[None]:
    with _GLOBAL_PROFILER.span(name):
        yield


def profiled(name: str):
    def decorator(func):
        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            with profile_scope(name):
                return func(*args, **kwargs)

        return wrapped

    return decorator
