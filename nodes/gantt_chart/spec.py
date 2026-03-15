from __future__ import annotations

import calendar
import csv
import html
import json
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import winsound
except Exception:
    winsound = None  # type: ignore

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant
from echograph.ui.dialogs import BigTextEditDialog

_SCHEDULE_PARAM = "schedule_data"
_PROGRESS_PARAM = "progress_data"
_NOTIFICATION_PARAM = "notification_data"
_VIEW_MONTH_PARAM = "view_month"
_VIEW_START_PARAM = "view_start_date"
_TASK_PANEL_WIDTH_PARAM = "task_panel_width"
_GROUP_STATE_PARAM = "group_state_data"
_VISIBLE_DAY_COUNT = 31
_GANTT_DAY_COLUMN_WIDTH = 24
_GANTT_TASK_ROW_HEIGHT = 28
_GANTT_TASK_PANEL_MIN_WIDTH = 180
_GANTT_TASK_PANEL_DEFAULT_WIDTH = 180
_GANTT_TASK_PANEL_HANDLE_WIDTH = 4
_DAY_SCROLL_UNITS_PER_DAY = 128
_DAY_SCROLL_CENTER = 15360
_DAY_SCROLL_RANGE = 30720
_TODAY_MARKER_Y_OFFSET = -8
_GANTT_SIDECAR_SUFFIX = ".gantt_chart.json"

_ICON_PIXMAP_CACHE: dict[str, QtGui.QPixmap | None] = {}
_GANTT_TRANSFER_BUFFER: dict[str, object] | None = None


def _set_gantt_transfer_buffer(payload: dict[str, object] | None) -> None:
    global _GANTT_TRANSFER_BUFFER
    if not isinstance(payload, dict):
        _GANTT_TRANSFER_BUFFER = None
        return
    try:
        _GANTT_TRANSFER_BUFFER = json.loads(json.dumps(payload))
    except Exception:
        _GANTT_TRANSFER_BUFFER = None


def _get_gantt_transfer_buffer() -> dict[str, object] | None:
    if not isinstance(_GANTT_TRANSFER_BUFFER, dict):
        return None
    try:
        return json.loads(json.dumps(_GANTT_TRANSFER_BUFFER))
    except Exception:
        return None


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return entry.get("value", "") or ""
    return ""


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            model.params = params
            return
    params.append({"name": name, "value": default})
    model.params = params


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    hidden_entry = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": store_key, "value": ""}
        params.append(hidden_entry)
    hidden = set()
    for part in str(hidden_entry.get("value", "")).split(","):
        token = part.strip().lower()
        if token:
            hidden.add(token)
    for name in names or []:
        token = (name or "").strip().lower()
        if token:
            hidden.add(token)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def build_ports(node_item) -> None:
    today = date.today()
    model = getattr(node_item, "model", None)
    year, month = _parse_view_month(
        _param_value(model, _VIEW_MONTH_PARAM),
        fallback_year=today.year,
        fallback_month=today.month,
    )
    view_start = _parse_view_start(
        _param_value(model, _VIEW_START_PARAM),
        fallback=date(year, month, 1),
    )
    _ensure_param(node_item, _SCHEDULE_PARAM, "{}")
    _ensure_param(node_item, _PROGRESS_PARAM, "{}")
    _ensure_param(node_item, _NOTIFICATION_PARAM, "{}")
    _ensure_param(node_item, _VIEW_MONTH_PARAM, f"{view_start.year:04d}-{view_start.month:02d}")
    _ensure_param(node_item, _VIEW_START_PARAM, view_start.isoformat())
    _ensure_param(node_item, _TASK_PANEL_WIDTH_PARAM, str(_GANTT_TASK_PANEL_DEFAULT_WIDTH))
    _ensure_param(node_item, _GROUP_STATE_PARAM, "{}")
    _ensure_hidden_params(
        model,
        [
            _SCHEDULE_PARAM,
            _PROGRESS_PARAM,
            _NOTIFICATION_PARAM,
            _VIEW_MONTH_PARAM,
            _VIEW_START_PARAM,
            _TASK_PANEL_WIDTH_PARAM,
            _GROUP_STATE_PARAM,
        ],
    )


def _set_param_value(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    found = False
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            entry["value"] = value
            found = True
            break
    if not found:
        params.append({"name": name, "value": value})
    model.params = params
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None and hasattr(scene, "set_node_params"):
        try:
            scene.set_node_params(model.name, params, rebuild=False, emit=notify_scene)
        except Exception:
            pass


def _hidden_params(model) -> set[str]:
    raw = _param_value(model, "__ui_hidden_params")
    hidden = set()
    for part in str(raw).split(","):
        token = part.strip().lower()
        if token:
            hidden.add(token)
    return hidden


def _parse_view_month(raw: str, *, fallback_year: int, fallback_month: int) -> tuple[int, int]:
    text = str(raw or "").strip()
    if text:
        parts = text.split("-", 1)
        if len(parts) == 2:
            try:
                year = int(parts[0])
                month = int(parts[1])
                if 1 <= month <= 12:
                    return year, month
            except Exception:
                pass
    return fallback_year, fallback_month


def _parse_view_start(raw: str, *, fallback: date) -> date:
    text = str(raw or "").strip()
    if text:
        try:
            return date.fromisoformat(text)
        except Exception:
            pass
    return fallback


def _scroll_value_for_day_offset(day_offset: int) -> int:
    value = _DAY_SCROLL_CENTER + (int(day_offset) * _DAY_SCROLL_UNITS_PER_DAY)
    return max(0, min(_DAY_SCROLL_RANGE, value))


def _day_offset_for_scroll_value(value: int) -> int:
    delta = int(value) - _DAY_SCROLL_CENTER
    if delta >= 0:
        return int((delta + (_DAY_SCROLL_UNITS_PER_DAY // 2)) // _DAY_SCROLL_UNITS_PER_DAY)
    return -int(((-delta) + (_DAY_SCROLL_UNITS_PER_DAY // 2)) // _DAY_SCROLL_UNITS_PER_DAY)


def _load_icon_pixmap(name: str) -> QtGui.QPixmap | None:
    cached = _ICON_PIXMAP_CACHE.get(name)
    if name in _ICON_PIXMAP_CACHE:
        return cached
    try:
        icon_path = Path(__file__).resolve().parents[2] / "icons" / name
        pm = QtGui.QPixmap(str(icon_path))
        if pm.isNull():
            pm = None
    except Exception:
        pm = None
    _ICON_PIXMAP_CACHE[name] = pm
    return pm


def _play_notification_alert_sound() -> None:
    if winsound is None:
        return
    try:
        sound_path = Path(__file__).resolve().parents[2] / "sounds" / "notification_alert.wav"
    except Exception:
        return
    if not sound_path.is_file():
        return
    try:
        winsound.PlaySound(
            str(sound_path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception:
        pass


def _days_in_month(year: int, month: int) -> int:
    return int(calendar.monthrange(int(year), int(month))[1])


def _coerce_iso_date(raw, *, default_year: int, default_month: int) -> str | None:
    if isinstance(raw, int):
        day = int(raw)
        if 1 <= day <= _days_in_month(default_year, default_month):
            return date(default_year, default_month, day).isoformat()
        return None
    text = str(raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        day = int(text)
        if 1 <= day <= _days_in_month(default_year, default_month):
            return date(default_year, default_month, day).isoformat()
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except Exception:
        return None


def _normalize_assignment_entry(start_iso: str, end_iso: str) -> tuple[str, str]:
    try:
        start_date = date.fromisoformat(str(start_iso))
        end_date = date.fromisoformat(str(end_iso))
    except Exception:
        return str(start_iso), str(end_iso)
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    return start_date.isoformat(), end_date.isoformat()


def _coerce_assignment_entry(raw, *, default_year: int, default_month: int) -> tuple[str, str] | None:
    start_iso = None
    end_iso = None
    if isinstance(raw, dict):
        start_iso = _coerce_iso_date(
            raw.get("start", raw.get("date", raw.get("day"))),
            default_year=default_year,
            default_month=default_month,
        )
        end_iso = _coerce_iso_date(
            raw.get("end", raw.get("finish", raw.get("to"))),
            default_year=default_year,
            default_month=default_month,
        )
    elif isinstance(raw, (list, tuple)):
        if len(raw) >= 1:
            start_iso = _coerce_iso_date(raw[0], default_year=default_year, default_month=default_month)
        if len(raw) >= 2:
            end_iso = _coerce_iso_date(raw[1], default_year=default_year, default_month=default_month)
    else:
        start_iso = _coerce_iso_date(raw, default_year=default_year, default_month=default_month)
        end_iso = start_iso
    if start_iso is None and end_iso is None:
        return None
    if start_iso is None:
        start_iso = end_iso
    if end_iso is None:
        end_iso = start_iso
    if start_iso is None or end_iso is None:
        return None
    return _normalize_assignment_entry(start_iso, end_iso)


def _read_assignments(node_item, *, default_year: int, default_month: int) -> dict[str, tuple[str, str]]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {}
    raw = _param_value(model, _SCHEDULE_PARAM).strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    clean: dict[str, tuple[str, str]] = {}
    for name, value in data.items():
        task = str(name or "").strip()
        if not task:
            continue
        entry = _coerce_assignment_entry(value, default_year=default_year, default_month=default_month)
        if entry:
            clean[task] = entry
    return clean


def _coerce_progress_value(raw) -> int | None:
    try:
        value = int(float(raw))
    except Exception:
        return None
    return max(0, min(100, value))


def _coerce_bool_value(raw) -> bool | None:
    if isinstance(raw, bool):
        return bool(raw)
    if isinstance(raw, (int, float)):
        return bool(int(raw))
    text = str(raw or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _read_progress_map(node_item) -> dict[str, int]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {}
    raw = _param_value(model, _PROGRESS_PARAM).strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    clean: dict[str, int] = {}
    for name, value in data.items():
        task = str(name or "").strip()
        if not task:
            continue
        progress = _coerce_progress_value(value)
        if progress is not None:
            clean[task] = progress
    return clean


def _read_group_state(node_item) -> dict[str, bool]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {}
    raw = _param_value(model, _GROUP_STATE_PARAM).strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    clean: dict[str, bool] = {}
    for name, value in data.items():
        key = str(name or "").strip()
        if not key:
            continue
        parsed = _coerce_bool_value(value)
        if parsed is None:
            continue
        clean[key] = bool(parsed)
    return clean


def _write_assignments(node_item, mapping: dict[str, object], *, notify_scene: bool = True) -> None:
    clean = {}
    for name, value in (mapping or {}).items():
        task = str(name or "").strip()
        if not task:
            continue
        entry = _coerce_assignment_entry(value, default_year=date.today().year, default_month=date.today().month)
        if not entry:
            continue
        start_iso, end_iso = entry
        clean[task] = start_iso if start_iso == end_iso else {"start": start_iso, "end": end_iso}
    _set_param_value(
        node_item,
        _SCHEDULE_PARAM,
        json.dumps(clean, sort_keys=True, separators=(",", ":")),
        notify_scene=notify_scene,
    )
    _write_gantt_sidecar_snapshot(node_item)


def _write_progress_map(node_item, mapping: dict[str, int], *, notify_scene: bool = True) -> None:
    clean: dict[str, int] = {}
    for name, value in (mapping or {}).items():
        task = str(name or "").strip()
        if not task:
            continue
        progress = _coerce_progress_value(value)
        if progress is not None:
            clean[task] = progress
    _set_param_value(
        node_item,
        _PROGRESS_PARAM,
        json.dumps(clean, sort_keys=True, separators=(",", ":")),
        notify_scene=notify_scene,
    )
    _write_gantt_sidecar_snapshot(node_item)


def _parse_notification_datetime(raw) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if getattr(parsed, "tzinfo", None) is not None:
        try:
            parsed = parsed.astimezone().replace(tzinfo=None)
        except Exception:
            parsed = parsed.replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _serialize_notification_datetime(value: datetime) -> str:
    if getattr(value, "tzinfo", None) is not None:
        try:
            value = value.astimezone().replace(tzinfo=None)
        except Exception:
            value = value.replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat(timespec="seconds")


def _coerce_notification_entry(raw) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    notify_at = _parse_notification_datetime(raw.get("notify_at"))
    if notify_at is None:
        return None
    message = str(raw.get("message") or "").strip()
    return {
        "notify_at": _serialize_notification_datetime(notify_at),
        "message": message,
    }


def _read_notifications(node_item) -> dict[str, dict[str, str]]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {}
    raw = _param_value(model, _NOTIFICATION_PARAM).strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    clean: dict[str, dict[str, str]] = {}
    for name, value in data.items():
        task = str(name or "").strip()
        if not task:
            continue
        entry = _coerce_notification_entry(value)
        if entry is not None:
            clean[task] = entry
    return clean


def _write_notifications(
    node_item,
    mapping: dict[str, dict[str, str]],
    *,
    notify_scene: bool = True,
) -> None:
    clean: dict[str, dict[str, str]] = {}
    for name, value in (mapping or {}).items():
        task = str(name or "").strip()
        if not task:
            continue
        entry = _coerce_notification_entry(value)
        if entry is not None:
            clean[task] = entry
    _set_param_value(
        node_item,
        _NOTIFICATION_PARAM,
        json.dumps(clean, sort_keys=True, separators=(",", ":")),
        notify_scene=notify_scene,
    )
    _write_gantt_sidecar_snapshot(node_item)


def _read_task_panel_width(node_item) -> int:
    model = getattr(node_item, "model", None)
    raw = _param_value(model, _TASK_PANEL_WIDTH_PARAM).strip()
    try:
        value = int(float(raw))
    except Exception:
        value = _GANTT_TASK_PANEL_DEFAULT_WIDTH
    return max(_GANTT_TASK_PANEL_MIN_WIDTH, value)


def _write_task_panel_width(node_item, width: int) -> None:
    _set_param_value(
        node_item,
        _TASK_PANEL_WIDTH_PARAM,
        str(max(_GANTT_TASK_PANEL_MIN_WIDTH, int(width))),
        notify_scene=False,
    )
    _write_gantt_sidecar_snapshot(node_item)


def _write_group_state(node_item, mapping: dict[str, bool], *, notify_scene: bool = False) -> None:
    clean: dict[str, bool] = {}
    for name, value in (mapping or {}).items():
        key = str(name or "").strip()
        if not key:
            continue
        parsed = _coerce_bool_value(value)
        if parsed is None:
            continue
        clean[key] = bool(parsed)
    _set_param_value(
        node_item,
        _GROUP_STATE_PARAM,
        json.dumps(clean, sort_keys=True, separators=(",", ":")),
        notify_scene=notify_scene,
    )
    _write_gantt_sidecar_snapshot(node_item)


def _rename_task_key(mapping: dict, old_name: str, new_name: str):
    clean = dict(mapping or {})
    old_key = str(old_name or "").strip()
    new_key = str(new_name or "").strip()
    if not old_key or not new_key or old_key == new_key or old_key not in clean:
        return clean, False
    clean[new_key] = clean.pop(old_key)
    return clean, True


def _workflow_path_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    scene_path = getattr(scene, "_filename", None) if scene is not None else None
    workflow_path = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                workflow_path = getattr(win, "_current_path", None)
        except Exception:
            pass
    workflow_path = workflow_path or scene_path
    if not workflow_path:
        return None
    try:
        return Path(workflow_path)
    except Exception:
        return None


def _gantt_sidecar_path(node_item) -> Path | None:
    workflow_path = _workflow_path_for_node(node_item)
    if workflow_path is None:
        return None
    try:
        return workflow_path.parent / "gantt_chart" / f"{workflow_path.stem}{_GANTT_SIDECAR_SUFFIX}"
    except Exception:
        return None


def _gantt_transfer_log_dir(node_item) -> Path | None:
    candidates = []
    workflow_path = _workflow_path_for_node(node_item)
    if workflow_path is not None:
        try:
            candidates.append(workflow_path.parent / "gantt_chart" / "transfer_logs")
        except Exception:
            pass
    try:
        candidates.append(Path(__file__).resolve().parents[2] / "gantt_chart" / "transfer_logs")
    except Exception:
        pass
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            continue
    return None


def _gantt_transfer_log_path(node_item, chart_name: str) -> Path | None:
    directory = _gantt_transfer_log_dir(node_item)
    if directory is None:
        return None
    safe_name = "".join(ch if (str(ch).isalnum() or ch in ("-", "_")) else "_" for ch in str(chart_name or "gantt_chart"))
    safe_name = safe_name.strip("_") or "gantt_chart"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return directory / f"{safe_name}_copy_{stamp}.csv"


def _gantt_transfer_pointer_path(node_item) -> Path | None:
    directory = _gantt_transfer_log_dir(node_item)
    if directory is None:
        return None
    return directory / "latest_transfer_log.txt"


def _write_gantt_transfer_pointer(node_item, log_path: Path) -> None:
    pointer = _gantt_transfer_pointer_path(node_item)
    if pointer is None:
        return
    try:
        pointer.write_text(str(log_path), encoding="utf-8")
    except Exception:
        pass


def _read_gantt_transfer_pointer(node_item) -> Path | None:
    pointer = _gantt_transfer_pointer_path(node_item)
    if pointer is None or not pointer.is_file():
        return None
    try:
        path_text = pointer.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not path_text:
        return None
    try:
        candidate = Path(path_text)
    except Exception:
        return None
    return candidate if candidate.is_file() else None


def _latest_gantt_transfer_log(node_item) -> Path | None:
    directory = _gantt_transfer_log_dir(node_item)
    if directory is None or not directory.is_dir():
        return None
    try:
        matches = sorted(directory.glob("*_copy_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        matches = []
    for match in matches:
        if match.is_file():
            return match
    return None


def _resolve_gantt_transfer_log_path(node_item) -> Path | None:
    payload = _get_gantt_transfer_buffer()
    if isinstance(payload, dict):
        log_path_text = str(payload.get("log_path") or "").strip()
        if log_path_text:
            try:
                candidate = Path(log_path_text)
            except Exception:
                candidate = None
            if candidate is not None and candidate.is_file():
                return candidate
    pointed = _read_gantt_transfer_pointer(node_item)
    if pointed is not None:
        return pointed
    return _latest_gantt_transfer_log(node_item)


def _read_gantt_sidecar_nodes(node_item) -> dict[str, dict]:
    path = _gantt_sidecar_path(node_item)
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    nodes = payload.get("nodes") or {}
    return nodes if isinstance(nodes, dict) else {}


def _json_param_is_empty(raw: str) -> bool:
    text = str(raw or "").strip()
    return not text or text == "{}"


def _write_gantt_sidecar_snapshot(node_item) -> None:
    path = _gantt_sidecar_path(node_item)
    model = getattr(node_item, "model", None)
    node_name = str(getattr(model, "name", "") or "").strip()
    if path is None or not node_name:
        return
    nodes = _read_gantt_sidecar_nodes(node_item)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    nodes[node_name] = {
        "schedule_data": _param_value(model, _SCHEDULE_PARAM).strip() or "{}",
        "progress_data": _param_value(model, _PROGRESS_PARAM).strip() or "{}",
        "notification_data": _param_value(model, _NOTIFICATION_PARAM).strip() or "{}",
        "view_start_date": _param_value(model, _VIEW_START_PARAM).strip(),
        "task_panel_width": _param_value(model, _TASK_PANEL_WIDTH_PARAM).strip(),
        "group_state_data": _param_value(model, _GROUP_STATE_PARAM).strip() or "{}",
    }
    try:
        path.write_text(json.dumps({"nodes": nodes}, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_gantt_sidecar_snapshot(node_item) -> bool:
    model = getattr(node_item, "model", None)
    node_name = str(getattr(model, "name", "") or "").strip()
    if model is None or not node_name:
        return False
    entry = _read_gantt_sidecar_nodes(node_item).get(node_name)
    if not isinstance(entry, dict):
        return False

    changed = False
    current_schedule = _param_value(model, _SCHEDULE_PARAM)
    sidecar_schedule = entry.get("schedule_data")
    if _json_param_is_empty(current_schedule):
        if isinstance(sidecar_schedule, dict):
            sidecar_schedule = json.dumps(sidecar_schedule, sort_keys=True, separators=(",", ":"))
        sidecar_schedule = str(sidecar_schedule or "").strip() or "{}"
        try:
            parsed = json.loads(sidecar_schedule)
            if isinstance(parsed, dict):
                sidecar_schedule = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
            else:
                sidecar_schedule = "{}"
        except Exception:
            sidecar_schedule = "{}"
        if not _json_param_is_empty(sidecar_schedule):
            _set_param_value(node_item, _SCHEDULE_PARAM, sidecar_schedule, notify_scene=False)
            changed = True

    current_view_start = _param_value(model, _VIEW_START_PARAM).strip()
    sidecar_view_start = str(entry.get("view_start_date") or "").strip()
    if not current_view_start and sidecar_view_start:
        parsed_view_start = _parse_view_start(sidecar_view_start, fallback=date.today())
        _set_param_value(node_item, _VIEW_START_PARAM, parsed_view_start.isoformat(), notify_scene=False)
        _set_param_value(
            node_item,
            _VIEW_MONTH_PARAM,
            f"{parsed_view_start.year:04d}-{parsed_view_start.month:02d}",
            notify_scene=False,
        )
        changed = True
    current_progress = _param_value(model, _PROGRESS_PARAM)
    sidecar_progress = entry.get("progress_data")
    if _json_param_is_empty(current_progress):
        if isinstance(sidecar_progress, dict):
            sidecar_progress = json.dumps(sidecar_progress, sort_keys=True, separators=(",", ":"))
        sidecar_progress = str(sidecar_progress or "").strip() or "{}"
        try:
            parsed_progress = json.loads(sidecar_progress)
            if isinstance(parsed_progress, dict):
                sidecar_progress = json.dumps(parsed_progress, sort_keys=True, separators=(",", ":"))
            else:
                sidecar_progress = "{}"
        except Exception:
            sidecar_progress = "{}"
        if not _json_param_is_empty(sidecar_progress):
            _set_param_value(node_item, _PROGRESS_PARAM, sidecar_progress, notify_scene=False)
            changed = True
    current_notifications = _param_value(model, _NOTIFICATION_PARAM)
    sidecar_notifications = entry.get("notification_data")
    if _json_param_is_empty(current_notifications):
        if isinstance(sidecar_notifications, dict):
            sidecar_notifications = json.dumps(sidecar_notifications, sort_keys=True, separators=(",", ":"))
        sidecar_notifications = str(sidecar_notifications or "").strip() or "{}"
        try:
            parsed_notifications = json.loads(sidecar_notifications)
            if isinstance(parsed_notifications, dict):
                clean_notifications = {}
                for task_name, raw_entry in parsed_notifications.items():
                    task = str(task_name or "").strip()
                    if not task:
                        continue
                    entry_value = _coerce_notification_entry(raw_entry)
                    if entry_value is not None:
                        clean_notifications[task] = entry_value
                sidecar_notifications = json.dumps(clean_notifications, sort_keys=True, separators=(",", ":"))
            else:
                sidecar_notifications = "{}"
        except Exception:
            sidecar_notifications = "{}"
        if not _json_param_is_empty(sidecar_notifications):
            _set_param_value(node_item, _NOTIFICATION_PARAM, sidecar_notifications, notify_scene=False)
            changed = True
    current_width_raw = _param_value(model, _TASK_PANEL_WIDTH_PARAM).strip()
    current_width = _read_task_panel_width(node_item)
    sidecar_width = str(entry.get("task_panel_width") or "").strip()
    if sidecar_width:
        try:
            parsed_sidecar_width = max(_GANTT_TASK_PANEL_MIN_WIDTH, int(float(sidecar_width)))
        except Exception:
            parsed_sidecar_width = None
        if parsed_sidecar_width is not None:
            should_restore_width = (not current_width_raw) or (current_width == _GANTT_TASK_PANEL_DEFAULT_WIDTH)
            if should_restore_width and parsed_sidecar_width != current_width:
                _set_param_value(
                    node_item,
                    _TASK_PANEL_WIDTH_PARAM,
                    str(parsed_sidecar_width),
                    notify_scene=False,
                )
                changed = True
    current_group_state = _param_value(model, _GROUP_STATE_PARAM)
    sidecar_group_state = entry.get("group_state_data")
    if _json_param_is_empty(current_group_state):
        if isinstance(sidecar_group_state, dict):
            sidecar_group_state = json.dumps(sidecar_group_state, sort_keys=True, separators=(",", ":"))
        sidecar_group_state = str(sidecar_group_state or "").strip() or "{}"
        try:
            parsed_group_state = json.loads(sidecar_group_state)
            if isinstance(parsed_group_state, dict):
                clean_group_state = {}
                for group_name, raw_value in parsed_group_state.items():
                    key = str(group_name or "").strip()
                    if not key:
                        continue
                    parsed = _coerce_bool_value(raw_value)
                    if parsed is None:
                        continue
                    clean_group_state[key] = bool(parsed)
                sidecar_group_state = json.dumps(clean_group_state, sort_keys=True, separators=(",", ":"))
            else:
                sidecar_group_state = "{}"
        except Exception:
            sidecar_group_state = "{}"
        if not _json_param_is_empty(sidecar_group_state):
            _set_param_value(node_item, _GROUP_STATE_PARAM, sidecar_group_state, notify_scene=False)
            changed = True
    return changed


def _ordered_in_edges(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return []
    try:
        return list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            return list(scene._in_edges(node_item))
        except Exception:
            return []


def _collect_note_sources_from_item(
    source_item,
    *,
    seen_note_items: set[int],
    active_path: set[int],
) -> tuple[list[tuple[object, object]], bool, bool]:
    if source_item is None:
        return [], True, False
    marker = id(source_item)
    if marker in active_path:
        return [], True, False
    active_path.add(marker)
    try:
        source_model = getattr(source_item, "model", None)
        if source_model is None:
            return [], True, False
        kind = (getattr(source_model, "kind", "") or "").strip().lower()
        if kind == "note":
            if marker in seen_note_items:
                return [], False, True
            seen_note_items.add(marker)
            return [(source_item, source_model)], False, True
        if kind != "append":
            return [], True, False
        upstream_edges = _ordered_in_edges(source_item)
        if not upstream_edges:
            return [], True, False
        note_sources: list[tuple[object, object]] = []
        invalid_inputs = False
        had_note_candidate = False
        for upstream_edge in upstream_edges:
            upstream_item = getattr(upstream_edge, "src", None)
            child_sources, child_invalid, child_candidate = _collect_note_sources_from_item(
                upstream_item,
                seen_note_items=seen_note_items,
                active_path=active_path,
            )
            if child_sources:
                note_sources.extend(child_sources)
            if child_invalid:
                invalid_inputs = True
            if child_candidate:
                had_note_candidate = True
        if not note_sources and not had_note_candidate:
            invalid_inputs = True
        return note_sources, invalid_inputs, had_note_candidate
    finally:
        active_path.discard(marker)


def _resolve_note_sources(node_item):
    in_edges = _ordered_in_edges(node_item)
    if not in_edges:
        return [], False
    note_sources: list[tuple[object, object]] = []
    seen_note_items: set[int] = set()
    invalid_inputs = False
    for edge in in_edges:
        port_name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
        is_note_port = (port_name or "").strip().lower() in {"note", "source", ""}
        src_item = getattr(edge, "src", None)
        resolved_sources, has_invalid, _ = _collect_note_sources_from_item(
            src_item,
            seen_note_items=seen_note_items,
            active_path=set(),
        )
        if resolved_sources:
            note_sources.extend(resolved_sources)
        if is_note_port and has_invalid:
            invalid_inputs = True
    if in_edges and not note_sources:
        invalid_inputs = True
    return note_sources, invalid_inputs


def _resolve_note_source(node_item):
    note_sources, invalid_inputs = _resolve_note_sources(node_item)
    if note_sources:
        return note_sources[0]
    if invalid_inputs:
        for edge in _ordered_in_edges(node_item):
            src_item = getattr(edge, "src", None)
            src_model = getattr(src_item, "model", None)
            if src_item is not None and src_model is not None and (getattr(src_model, "kind", "") or "").strip().lower() != "note":
                return src_item, None
            if src_item is not None and src_model is None:
                return src_item, None
    return None, None


def _task_storage_key(source_name: str, task_name: str) -> str:
    return json.dumps([str(source_name or "").strip(), str(task_name or "").strip()], separators=(",", ":"))


def _parse_task_storage_key(raw_key: str) -> tuple[str | None, str]:
    text = str(raw_key or "").strip()
    if not text:
        return None, ""
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (list, tuple)) and len(parsed) >= 2:
                source_name = str(parsed[0] or "").strip()
                task_name = str(parsed[1] or "").strip()
                if source_name and task_name:
                    return source_name, task_name
        except Exception:
            pass
    return None, text


def _canonical_task_key(
    raw_key: str,
    *,
    valid_task_ids: set[str],
    by_source_task: dict[tuple[str, str], str],
    by_task_name: dict[str, list[str]],
) -> str | None:
    key = str(raw_key or "").strip()
    if not key:
        return None
    if key in valid_task_ids:
        return key
    source_name, task_name = _parse_task_storage_key(key)
    task_lookup = task_name.strip().lower()
    if not task_lookup:
        return None
    if source_name:
        candidate = by_source_task.get((source_name.strip().lower(), task_lookup))
        if candidate:
            return candidate
    candidates = by_task_name.get(task_lookup) or []
    if not candidates:
        return None
    return candidates[0]


def _normalized_source_key(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    if text.endswith(")") and "(" in text:
        base, _, tail = text.rpartition("(")
        token = tail[:-1].strip()
        if token.isdigit():
            text = base.strip()
    return text


def _dialog_parent_for_node(node_item):
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
        return QtWidgets.QApplication.activeWindow()
    except Exception:
        return None


def _read_source_task_value(model, task_name: str) -> str:
    if model is None:
        return ""
    key = (task_name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return entry.get("value", "") or ""
    return ""


def _write_source_task_value(
    node_item,
    src_item,
    src_model,
    task_name: str,
    value: str,
    *,
    notify_scene: bool = True,
) -> bool:
    if src_model is None:
        return False
    key = (task_name or "").strip().lower()
    params = list(getattr(src_model, "params", None) or [])
    updated = False
    for entry in params:
        if (entry.get("name") or "").strip().lower() != key:
            continue
        entry["value"] = value or ""
        updated = True
        break
    if not updated:
        return False
    try:
        src_model.params = params
    except Exception:
        return False
    try:
        if src_item is not None and hasattr(src_item, "_schedule_rebuild"):
            src_item._schedule_rebuild()
        elif src_item is not None and hasattr(src_item, "update"):
            src_item.update()
    except Exception:
        pass
    if notify_scene:
        scene = None
        try:
            scene = src_item.scene() if src_item is not None else node_item.scene()
        except Exception:
            scene = None
        if scene is not None and hasattr(scene, "paramChanged"):
            try:
                scene.paramChanged.emit(getattr(src_model, "name", ""), list(getattr(src_model, "params", []) or []))
            except Exception:
                pass
    return True


def _note_task_names(model) -> list[str]:
    if model is None:
        return []
    hidden = _hidden_params(model)
    names = []
    seen = set()
    for entry in (getattr(model, "params", None) or []):
        name = (entry.get("name") or "").strip()
        key = name.lower()
        if not name or key == "__ui_hidden_params" or key in hidden or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _completed_task_names(model) -> set[str]:
    if model is None:
        return set()
    raw = getattr(model, "_completed_params", None)
    out = set()
    if isinstance(raw, set):
        out.update(str(x) for x in raw if x)
    elif isinstance(raw, (list, tuple)):
        out.update(str(x) for x in raw if x)
    elif isinstance(raw, str) and raw:
        out.add(raw)
    return out


def _write_source_completed_tasks(node_item, src_item, src_model, names: set[str], *, notify_scene: bool = True) -> bool:
    if src_model is None:
        return False
    clean = {str(name) for name in (names or set()) if str(name).strip()}
    try:
        setattr(src_model, "_completed_params", clean)
    except Exception:
        return False
    try:
        if src_item is not None and hasattr(src_item, "_schedule_rebuild"):
            src_item._schedule_rebuild()
        elif src_item is not None and hasattr(src_item, "update"):
            src_item.update()
    except Exception:
        pass
    if notify_scene:
        scene = None
        try:
            scene = src_item.scene() if src_item is not None else node_item.scene()
        except Exception:
            scene = None
        if scene is not None and hasattr(scene, "paramChanged"):
            try:
                scene.paramChanged.emit(getattr(src_model, "name", ""), list(getattr(src_model, "params", []) or []))
            except Exception:
                pass
    return True


def _set_section_resize_mode(header, section: int, mode) -> None:
    try:
        header.setSectionResizeMode(section, mode)
    except AttributeError:
        header.setResizeMode(section, mode)


_TODAY_ICON_CACHE = None


def _today_icon() -> QtGui.QIcon | None:
    global _TODAY_ICON_CACHE
    if _TODAY_ICON_CACHE is not None:
        return _TODAY_ICON_CACHE
    try:
        pm = _load_icon_pixmap("KeyframeHandle_Icon.png")
        if pm is None:
            _TODAY_ICON_CACHE = None
            return None
        if pm.isNull():
            _TODAY_ICON_CACHE = None
            return None
        tinted = QtGui.QPixmap(pm.size())
        tinted.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(tinted)
        painter.drawPixmap(0, 0, pm)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QtGui.QColor("#f8fafc"))
        painter.end()
        _TODAY_ICON_CACHE = QtGui.QIcon(tinted)
        return _TODAY_ICON_CACHE
    except Exception:
        _TODAY_ICON_CACHE = None
        return None


def _paint_gantt_button_chrome(
    painter,
    rect: QtCore.QRect,
    *,
    enabled: bool,
    hovered: bool,
    pressed: bool,
) -> None:
    if not rect.isValid():
        return
    if pressed:
        fill = QtGui.QColor("#000000")
        border = QtGui.QColor("#e2e8f0")
    elif hovered and enabled:
        fill = QtGui.QColor(24, 34, 46, 220)
        border = QtGui.QColor("#93a4b8")
    elif enabled:
        fill = QtGui.QColor(18, 24, 32, 175)
        border = QtGui.QColor(71, 85, 105, 180)
    else:
        fill = QtGui.QColor(18, 24, 32, 115)
        border = QtGui.QColor(51, 65, 85, 120)
    painter.setPen(QtGui.QPen(border, 1))
    painter.setBrush(QtGui.QBrush(fill))
    painter.drawRoundedRect(QtCore.QRectF(rect.adjusted(1, 1, -1, -1)), 3.0, 3.0)


class _GanttNotificationButton(QtWidgets.QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._icon_pm = _load_icon_pixmap("notification_timer.png")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Select a task to schedule a reminder.")
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_Hover, True)
        self.setMouseTracking(True)

    def sizeHint(self):
        return QtCore.QSize(28, 28)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass
        hovered = bool(self.underMouse())
        pressed = bool(self.isDown())
        _paint_gantt_button_chrome(
            painter,
            self.rect(),
            enabled=bool(self.isEnabled()),
            hovered=hovered,
            pressed=pressed,
        )
        if self._icon_pm is not None and not self._icon_pm.isNull():
            target = QtCore.QRect(0, 0, min(self.width() - 7, 17), min(self.height() - 7, 17))
            target.moveCenter(self.rect().center())
            if pressed:
                target.translate(0, 1)
            try:
                if not self.isEnabled():
                    painter.setOpacity(0.45)
                elif pressed:
                    painter.setOpacity(1.0)
                elif hovered:
                    painter.setOpacity(0.95)
                else:
                    painter.setOpacity(0.82)
            except Exception:
                pass
            painter.drawPixmap(target, self._icon_pm, self._icon_pm.rect())
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class _GanttTodayButton(QtWidgets.QAbstractButton):
    def __init__(self, today_value: date, parent=None):
        super().__init__(parent)
        self._today = today_value
        self._icon_pm = _load_icon_pixmap("CalendarToday_Icon.png")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(f"Jump to today: {self._today.isoformat()}")
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_Hover, True)
        self.setMouseTracking(True)

    def sizeHint(self):
        return QtCore.QSize(28, 28)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass
        hovered = bool(self.underMouse())
        pressed = bool(self.isDown())
        _paint_gantt_button_chrome(
            painter,
            self.rect(),
            enabled=bool(self.isEnabled()),
            hovered=hovered,
            pressed=pressed,
        )
        if self._icon_pm is not None and not self._icon_pm.isNull():
            target = QtCore.QRect(0, 0, min(self.width() - 5, 21), min(self.height() - 5, 21))
            target.moveCenter(self.rect().center())
            target.moveTop(max(0, int((self.height() - target.height()) / 2.0)))
            if pressed:
                target.translate(0, 1)
            try:
                if not self.isEnabled():
                    painter.setOpacity(0.5)
                elif pressed:
                    painter.setOpacity(1.0)
                elif hovered:
                    painter.setOpacity(0.96)
                else:
                    painter.setOpacity(0.9)
            except Exception:
                pass
            painter.drawPixmap(target, self._icon_pm, self._icon_pm.rect())
        font = painter.font()
        font.setBold(True)
        try:
            font.setPointSize(max(8, int(font.pointSize()) - 1))
        except Exception:
            pass
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#f8fafc"))
        text_rect = QtCore.QRect(target) if self._icon_pm is not None and not self._icon_pm.isNull() else self.rect()
        painter.drawText(text_rect.adjusted(0, 1 if pressed else 0, 0, 0), int(QtCore.Qt.AlignCenter), str(self._today.day))
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class _GanttCopyButton(QtWidgets.QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._icon_pm = _load_icon_pixmap("Copy_Icon.png")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Copy this chart's assignments and progress.")
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_Hover, True)
        self.setMouseTracking(True)

    def sizeHint(self):
        return QtCore.QSize(28, 28)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass
        hovered = bool(self.underMouse())
        pressed = bool(self.isDown())
        _paint_gantt_button_chrome(
            painter,
            self.rect(),
            enabled=bool(self.isEnabled()),
            hovered=hovered,
            pressed=pressed,
        )
        if self._icon_pm is not None and not self._icon_pm.isNull():
            target = QtCore.QRect(0, 0, min(self.width() - 7, 17), min(self.height() - 7, 17))
            target.moveCenter(self.rect().center())
            if pressed:
                target.translate(0, 1)
            try:
                if not self.isEnabled():
                    painter.setOpacity(0.45)
                elif pressed:
                    painter.setOpacity(1.0)
                elif hovered:
                    painter.setOpacity(0.97)
                else:
                    painter.setOpacity(0.9)
            except Exception:
                pass
            painter.drawPixmap(target, self._icon_pm, self._icon_pm.rect())
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class _GanttPasteButton(QtWidgets.QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._icon_pm = _load_icon_pixmap("Paste_Icon.png")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Paste assignments and progress from copied chart data.")
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_Hover, True)
        self.setMouseTracking(True)

    def sizeHint(self):
        return QtCore.QSize(28, 28)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass
        hovered = bool(self.underMouse())
        pressed = bool(self.isDown())
        _paint_gantt_button_chrome(
            painter,
            self.rect(),
            enabled=bool(self.isEnabled()),
            hovered=hovered,
            pressed=pressed,
        )
        if self._icon_pm is not None and not self._icon_pm.isNull():
            target = QtCore.QRect(0, 0, min(self.width() - 7, 17), min(self.height() - 7, 17))
            target.moveCenter(self.rect().center())
            if pressed:
                target.translate(0, 1)
            try:
                if not self.isEnabled():
                    painter.setOpacity(0.45)
                elif pressed:
                    painter.setOpacity(1.0)
                elif hovered:
                    painter.setOpacity(0.97)
                else:
                    painter.setOpacity(0.9)
            except Exception:
                pass
            painter.drawPixmap(target, self._icon_pm, self._icon_pm.rect())
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class _GanttTaskDividerHandle(QtWidgets.QWidget):
    dragMoved = QtCore.Signal(int)
    dragFinished = QtCore.Signal(int)

    def __init__(self, table, parent=None):
        super().__init__(parent)
        self._table = table
        self._dragging = False
        self._start_global_x = 0
        self._start_width = 0
        self.setCursor(QtCore.Qt.SizeHorCursor)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            try:
                self._start_global_x = int(event.globalPosition().x())
            except Exception:
                self._start_global_x = int(event.globalX())
            try:
                self._start_width = int(self._table.verticalHeader().width()) if self._table is not None else 0
            except Exception:
                self._start_width = 0
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            try:
                global_x = int(event.globalPosition().x())
            except Exception:
                global_x = int(event.globalX())
            self.dragMoved.emit(int(self._start_width + (global_x - self._start_global_x)))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == QtCore.Qt.LeftButton:
            self._dragging = False
            try:
                global_x = int(event.globalPosition().x())
            except Exception:
                global_x = int(event.globalX())
            self.dragFinished.emit(int(self._start_width + (global_x - self._start_global_x)))
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _GanttCalendarTable(QtWidgets.QTableWidget):
    cellShortRightClicked = QtCore.Signal(int, int)
    cellLeftDoubleClicked = QtCore.Signal(int, int)
    assignmentResizeMoved = QtCore.Signal(int, int, int)
    assignmentResizeFinished = QtCore.Signal(int, int, int)
    assignmentMoveMoved = QtCore.Signal(int, int, int)
    assignmentMoveFinished = QtCore.Signal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._today_column: int | None = None
        self._assignment_overlays: dict[int, tuple[int, int, QtGui.QColor]] = {}
        self._progress_overlays: dict[int, tuple[int, int, int]] = {}
        self._right_press_pos: QtCore.QPoint | None = None
        self._right_press_index = QtCore.QModelIndex()
        self._right_drag_threshold = 0
        self._resize_drag_row: int | None = None
        self._resize_drag_edge: int | None = None
        self._move_drag_row: int | None = None
        self._move_drag_origin_start: int | None = None
        self._move_drag_origin_end: int | None = None
        self._move_drag_grab_column: int | None = None

    def set_today_column(self, column: int | None) -> None:
        self._today_column = None if column is None else int(column)
        try:
            self.viewport().update()
        except Exception:
            pass

    def set_progress_overlays(self, overlays: dict[int, tuple[int, int, int]]) -> None:
        self._progress_overlays = {
            int(row): (int(start_column), int(end_column), int(progress))
            for row, (start_column, end_column, progress) in (overlays or {}).items()
        }
        try:
            self.viewport().update()
        except Exception:
            pass

    def set_assignment_overlays(self, overlays: dict[int, tuple[int, int, QtGui.QColor]]) -> None:
        clean: dict[int, tuple[int, int, QtGui.QColor]] = {}
        for row, value in (overlays or {}).items():
            try:
                start_column, end_column, color = value
                clean[int(row)] = (int(start_column), int(end_column), QtGui.QColor(color))
            except Exception:
                continue
        self._assignment_overlays = clean
        try:
            self.viewport().update()
        except Exception:
            pass

    def _assignment_rect(self, row: int, start_column: int, end_column: int) -> QtCore.QRectF:
        try:
            start_rect = self.visualRect(self.model().index(int(row), int(start_column)))
            end_rect = self.visualRect(self.model().index(int(row), int(end_column)))
        except Exception:
            return QtCore.QRectF()
        if not start_rect.isValid() or not end_rect.isValid():
            return QtCore.QRectF()
        combined = start_rect.united(end_rect).adjusted(1, 1, -1, -1)
        if combined.width() <= 1 or combined.height() <= 1:
            return QtCore.QRectF()
        return QtCore.QRectF(combined)

    def _column_for_pos_x(self, x: int) -> int | None:
        count = int(self.columnCount())
        if count <= 0:
            return None
        for column in range(count):
            try:
                left = int(self.columnViewportPosition(column))
                width = int(self.columnWidth(column))
            except Exception:
                continue
            if x < (left + width):
                return int(column)
        return int(count - 1)

    def _resize_hit_for_pos(self, pos) -> tuple[int, int, int] | None:
        try:
            index = self.indexAt(pos)
        except Exception:
            index = QtCore.QModelIndex()
        if not index.isValid():
            return None
        row = int(index.row())
        overlay = self._assignment_overlays.get(row)
        if not overlay:
            return None
        start_column, end_column, _color = overlay
        rect = self._assignment_rect(row, start_column, end_column)
        if not rect.isValid():
            return None
        point = QtCore.QPointF(pos)
        if not rect.contains(point):
            return None
        left_distance = abs(float(point.x()) - float(rect.left()))
        right_distance = abs(float(point.x()) - float(rect.right()))
        if left_distance <= 6.0 and left_distance <= right_distance:
            return row, start_column, -1
        if right_distance <= 6.0:
            return row, end_column, 1
        return None

    def _move_hit_for_pos(self, pos) -> tuple[int, int, int, int] | None:
        try:
            index = self.indexAt(pos)
        except Exception:
            index = QtCore.QModelIndex()
        if not index.isValid():
            return None
        row = int(index.row())
        overlay = self._assignment_overlays.get(row)
        if not overlay:
            return None
        start_column, end_column, _color = overlay
        if end_column <= start_column:
            return None
        rect = self._assignment_rect(row, start_column, end_column)
        if not rect.isValid():
            return None
        point = QtCore.QPointF(pos)
        if not rect.contains(point):
            return None
        left_distance = abs(float(point.x()) - float(rect.left()))
        right_distance = abs(float(point.x()) - float(rect.right()))
        if left_distance <= 6.0 or right_distance <= 6.0:
            return None
        return row, start_column, end_column, int(index.column())

    def _update_resize_cursor(self, pos=None) -> None:
        if self._resize_drag_row is not None:
            self.viewport().setCursor(QtCore.Qt.SizeHorCursor)
            return
        if self._move_drag_row is not None:
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            return
        hit = self._resize_hit_for_pos(pos) if pos is not None else None
        if hit is not None:
            self.viewport().setCursor(QtCore.Qt.SizeHorCursor)
            return
        move_hit = self._move_hit_for_pos(pos) if pos is not None else None
        if move_hit is not None:
            self.viewport().setCursor(QtCore.Qt.OpenHandCursor)
            return
        self.viewport().unsetCursor()

    def viewportEvent(self, event):
        event_type = event.type()
        if event_type == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
            hit = self._resize_hit_for_pos(event.pos())
            if hit is not None:
                self._resize_drag_row = int(hit[0])
                self._resize_drag_edge = int(hit[2])
                self._update_resize_cursor(event.pos())
                event.accept()
                return True
            move_hit = self._move_hit_for_pos(event.pos())
            if move_hit is not None:
                self._move_drag_row = int(move_hit[0])
                self._move_drag_origin_start = int(move_hit[1])
                self._move_drag_origin_end = int(move_hit[2])
                self._move_drag_grab_column = int(move_hit[3])
                self._update_resize_cursor(event.pos())
                event.accept()
                return True
        elif event_type == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.RightButton:
            try:
                self._right_press_pos = QtCore.QPoint(event.pos())
            except Exception:
                self._right_press_pos = None
            try:
                self._right_press_index = self.indexAt(event.pos())
            except Exception:
                self._right_press_index = QtCore.QModelIndex()
            try:
                self._right_drag_threshold = int(QtWidgets.QApplication.startDragDistance())
            except Exception:
                self._right_drag_threshold = 8
        elif event_type == QtCore.QEvent.MouseMove:
            if self._resize_drag_row is not None and bool(event.buttons() & QtCore.Qt.LeftButton):
                target_column = self._column_for_pos_x(int(event.pos().x()))
                if target_column is not None:
                    self.assignmentResizeMoved.emit(
                        int(self._resize_drag_row),
                        int(target_column),
                        int(self._resize_drag_edge or 1),
                    )
                self._update_resize_cursor(event.pos())
                event.accept()
                return True
            if self._move_drag_row is not None and bool(event.buttons() & QtCore.Qt.LeftButton):
                target_column = self._column_for_pos_x(int(event.pos().x()))
                if (
                    target_column is not None
                    and self._move_drag_origin_start is not None
                    and self._move_drag_origin_end is not None
                    and self._move_drag_grab_column is not None
                ):
                    span = max(0, int(self._move_drag_origin_end) - int(self._move_drag_origin_start))
                    proposed_start = int(self._move_drag_origin_start) + (int(target_column) - int(self._move_drag_grab_column))
                    max_start = max(0, int(self.columnCount()) - span - 1)
                    start_column = max(0, min(max_start, proposed_start))
                    end_column = start_column + span
                    self.assignmentMoveMoved.emit(int(self._move_drag_row), int(start_column), int(end_column))
                self._update_resize_cursor(event.pos())
                event.accept()
                return True
            if self._right_press_pos is not None and bool(event.buttons() & QtCore.Qt.RightButton):
                try:
                    pos = QtCore.QPoint(event.pos())
                    if (pos - self._right_press_pos).manhattanLength() > max(1, int(self._right_drag_threshold)):
                        self._right_press_pos = None
                        self._right_press_index = QtCore.QModelIndex()
                except Exception:
                    pass
            self._update_resize_cursor(event.pos())
        elif event_type == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton:
            try:
                dbl_index = self.indexAt(event.pos())
            except Exception:
                dbl_index = QtCore.QModelIndex()
            if dbl_index.isValid():
                self.cellLeftDoubleClicked.emit(int(dbl_index.row()), int(dbl_index.column()))
        elif event_type == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.RightButton:
            self._right_press_pos = None
            self._right_press_index = QtCore.QModelIndex()
            event.accept()
            return True
        elif event_type == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.RightButton:
            try:
                release_pos = QtCore.QPoint(event.pos())
            except Exception:
                release_pos = None
            if self._right_press_pos is not None and release_pos is not None:
                try:
                    moved = (release_pos - self._right_press_pos).manhattanLength()
                except Exception:
                    moved = max(1, int(self._right_drag_threshold) + 1)
                if moved <= max(1, int(self._right_drag_threshold)):
                    try:
                        release_index = self.indexAt(release_pos)
                    except Exception:
                        release_index = QtCore.QModelIndex()
                    target_index = release_index if release_index.isValid() else self._right_press_index
                    if target_index.isValid():
                        self.cellShortRightClicked.emit(int(target_index.row()), int(target_index.column()))
            self._right_press_pos = None
            self._right_press_index = QtCore.QModelIndex()
        elif event_type == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
            if self._resize_drag_row is not None:
                target_column = self._column_for_pos_x(int(event.pos().x()))
                if target_column is not None:
                    self.assignmentResizeFinished.emit(
                        int(self._resize_drag_row),
                        int(target_column),
                        int(self._resize_drag_edge or 1),
                    )
                self._resize_drag_row = None
                self._resize_drag_edge = None
                self.viewport().unsetCursor()
                event.accept()
                return True
            if self._move_drag_row is not None:
                target_column = self._column_for_pos_x(int(event.pos().x()))
                if (
                    target_column is not None
                    and self._move_drag_origin_start is not None
                    and self._move_drag_origin_end is not None
                    and self._move_drag_grab_column is not None
                ):
                    span = max(0, int(self._move_drag_origin_end) - int(self._move_drag_origin_start))
                    proposed_start = int(self._move_drag_origin_start) + (int(target_column) - int(self._move_drag_grab_column))
                    max_start = max(0, int(self.columnCount()) - span - 1)
                    start_column = max(0, min(max_start, proposed_start))
                    end_column = start_column + span
                    self.assignmentMoveFinished.emit(int(self._move_drag_row), int(start_column), int(end_column))
                self._move_drag_row = None
                self._move_drag_origin_start = None
                self._move_drag_origin_end = None
                self._move_drag_grab_column = None
                self.viewport().unsetCursor()
                event.accept()
                return True
        elif event_type == QtCore.QEvent.Leave:
            if self._resize_drag_row is None and self._move_drag_row is None:
                self._update_resize_cursor(None)
        return super().viewportEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._assignment_overlays or self._progress_overlays:
            painter = QtGui.QPainter(self.viewport())
            try:
                try:
                    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
                except Exception:
                    pass
                painter.setPen(QtCore.Qt.NoPen)
                for row, (start_column, end_column, color) in self._assignment_overlays.items():
                    rounded_rect = self._assignment_rect(int(row), int(start_column), int(end_column))
                    if not rounded_rect.isValid():
                        continue
                    painter.setBrush(QtGui.QBrush(color))
                    painter.drawRoundedRect(rounded_rect, 4.0, 4.0)
                for row, (start_column, end_column, progress) in self._progress_overlays.items():
                    if progress <= 0 or progress >= 100:
                        continue
                    rounded_rect = self._assignment_rect(int(row), int(start_column), int(end_column))
                    if not rounded_rect.isValid():
                        continue
                    fill_height = max(1.0, (max(0, min(100, progress)) / 100.0) * float(rounded_rect.height()))
                    fill_rect = QtCore.QRectF(
                        rounded_rect.left(),
                        rounded_rect.bottom() - fill_height,
                        rounded_rect.width(),
                        fill_height,
                    )
                    clip = QtGui.QPainterPath()
                    clip.addRoundedRect(rounded_rect, 4.0, 4.0)
                    painter.save()
                    painter.setClipPath(clip)
                    painter.setBrush(QtGui.QColor(29, 78, 216, 170))
                    painter.drawRect(fill_rect)
                    painter.restore()
            finally:
                painter.end()
        col = self._today_column
        if col is None or col < 0 or col >= self.columnCount():
            return
        try:
            x = int(self.columnViewportPosition(col))
        except Exception:
            return
        if x >= self.viewport().width():
            return
        painter = QtGui.QPainter(self.viewport())
        pen = QtGui.QPen(QtGui.QColor("#f8fafc"), 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(x, 0, x, self.viewport().height())
        painter.end()


class _GanttTaskHeader(QtWidgets.QHeaderView):
    completionToggled = QtCore.Signal(int, bool)
    progressEditRequested = QtCore.Signal(int, object)
    renameRequested = QtCore.Signal(int)
    reorderRequested = QtCore.Signal(int, int)
    selectionRequested = QtCore.Signal(int)
    groupToggleRequested = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Vertical, parent)
        self._selected_section: int | None = None
        self._completed_sections: set[int] = set()
        self._progress_values: dict[int, int] = {}
        self._notification_sections: set[int] = set()
        self._group_sections: set[int] = set()
        self._expanded_group_sections: set[int] = set()
        self._child_sections: set[int] = set()
        self._notification_icon = _load_icon_pixmap("bell_icon.png")
        self._dragging_section: int | None = None
        self._drag_start_visual_index: int | None = None
        self._drag_grab_offset: int | None = None
        self._drag_pointer_y: int | None = None
        self._drop_indicator_y: int | None = None
        self._drop_indicator_index: int | None = None
        self._checkbox_pressed_section: int | None = None
        self._progress_pressed_section: int | None = None
        self._group_pressed_section: int | None = None
        self.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

    def set_selected_section(self, section: int | None) -> None:
        new_value = None if section is None or int(section) < 0 else int(section)
        if new_value == self._selected_section:
            return
        self._selected_section = new_value
        try:
            self.viewport().update()
        except Exception:
            self.update()

    def set_completed_sections(self, sections: set[int]) -> None:
        clean = {int(section) for section in (sections or set()) if int(section) >= 0}
        if clean == self._completed_sections:
            return
        self._completed_sections = clean
        try:
            self.viewport().update()
        except Exception:
            self.update()

    def set_progress_values(self, values: dict[int, int]) -> None:
        clean = {}
        for section, value in (values or {}).items():
            progress = _coerce_progress_value(value)
            if progress is not None and int(section) >= 0:
                clean[int(section)] = progress
        if clean == self._progress_values:
            return
        self._progress_values = clean
        try:
            self.viewport().update()
        except Exception:
            self.update()

    def set_notification_sections(self, sections: set[int]) -> None:
        clean = {int(section) for section in (sections or set()) if int(section) >= 0}
        if clean == self._notification_sections:
            return
        self._notification_sections = clean
        try:
            self.viewport().update()
        except Exception:
            self.update()

    def set_group_sections(self, expanded_by_section: dict[int, bool], child_sections: set[int] | None = None) -> None:
        group_map = {}
        for section, expanded in (expanded_by_section or {}).items():
            if int(section) < 0:
                continue
            group_map[int(section)] = bool(expanded)
        new_group_sections = set(group_map.keys())
        new_expanded = {section for section, expanded in group_map.items() if expanded}
        new_children = {int(section) for section in (child_sections or set()) if int(section) >= 0}
        if (
            new_group_sections == self._group_sections
            and new_expanded == self._expanded_group_sections
            and new_children == self._child_sections
        ):
            return
        self._group_sections = new_group_sections
        self._expanded_group_sections = new_expanded
        self._child_sections = new_children
        try:
            self.viewport().update()
        except Exception:
            self.update()

    def _is_group_section(self, logical_index: int) -> bool:
        return int(logical_index) in self._group_sections

    def _is_child_section(self, logical_index: int) -> bool:
        return int(logical_index) in self._child_sections

    def _group_toggle_rect(self, rect: QtCore.QRect) -> QtCore.QRect:
        size = max(9, min(12, int(rect.height()) - 10))
        x = int(rect.left() + 8)
        y = int(rect.top() + ((rect.height() - size) / 2.0))
        return QtCore.QRect(x, y, size, size)

    def _group_toggle_rect_for_section(self, logical_index: int) -> QtCore.QRect:
        top = int(self.sectionPosition(int(logical_index)))
        return self._group_toggle_rect(QtCore.QRect(0, top, self.viewport().width(), int(self.sectionSize(int(logical_index)))))

    def _paint_group_triangle(self, painter, rect: QtCore.QRect, expanded: bool, selected: bool) -> None:
        painter.save()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#f8fafc") if selected else QtGui.QColor("#dbe4ee"))
        cx = int(rect.center().x())
        left = int(rect.left() + 1)
        right = int(rect.right() - 1)
        top = int(rect.top() + 1)
        bottom = int(rect.bottom() - 1)
        if expanded:
            points = [
                QtCore.QPoint(cx, top),
                QtCore.QPoint(left, bottom),
                QtCore.QPoint(right, bottom),
            ]
        else:
            points = [
                QtCore.QPoint(left, top),
                QtCore.QPoint(right, top),
                QtCore.QPoint(cx, bottom),
            ]
        painter.drawPolygon(QtGui.QPolygon(points))
        painter.restore()

    def _progress_rect(self, rect: QtCore.QRect) -> QtCore.QRect:
        checkbox_rect = self._checkbox_rect(rect)
        width = 36
        x = int(checkbox_rect.left() - width - 8)
        y = int(rect.top() + 4)
        return QtCore.QRect(x, y, width, max(12, int(rect.height()) - 8))

    def _notification_rect(self, rect: QtCore.QRect) -> QtCore.QRect:
        progress_rect = self._progress_rect(rect)
        size = max(10, min(12, int(rect.height()) - 12))
        x = int(progress_rect.left() - size - 8)
        y = int(rect.top() + ((rect.height() - size) / 2.0))
        return QtCore.QRect(x, y, size, size)

    def _checkbox_rect(self, rect: QtCore.QRect) -> QtCore.QRect:
        size = max(12, min(14, int(rect.height()) - 10))
        x = int(rect.right() - size - 8)
        y = int(rect.top() + ((rect.height() - size) / 2.0))
        return QtCore.QRect(x, y, size, size)

    def _checkbox_rect_for_section(self, logical_index: int) -> QtCore.QRect:
        top = int(self.sectionPosition(int(logical_index)))
        return self._checkbox_rect(QtCore.QRect(0, top, self.viewport().width(), int(self.sectionSize(int(logical_index)))))

    def _progress_rect_for_section(self, logical_index: int) -> QtCore.QRect:
        top = int(self.sectionPosition(int(logical_index)))
        return self._progress_rect(QtCore.QRect(0, top, self.viewport().width(), int(self.sectionSize(int(logical_index)))))

    def _set_drop_indicator_y(self, y: int | None) -> None:
        new_value = None if y is None else int(y)
        if new_value == self._drop_indicator_y:
            return
        self._drop_indicator_y = new_value
        try:
            self.viewport().update()
        except Exception:
            self.update()

    def _set_drop_indicator_index(self, index: int | None) -> None:
        self._drop_indicator_index = None if index is None else int(index)

    def _indicator_target_for_pos(self, pos_y: int) -> tuple[int | None, int | None]:
        count = int(self.count())
        if count <= 0:
            return None, None
        for visual_index in range(count):
            logical_index = int(self.logicalIndex(visual_index))
            if logical_index < 0:
                continue
            top = int(self.sectionPosition(logical_index))
            size = int(self.sectionSize(logical_index))
            if pos_y < (top + (size / 2.0)):
                return top, visual_index
        last_logical = int(self.logicalIndex(count - 1))
        if last_logical < 0:
            return None, None
        return int(self.sectionPosition(last_logical) + self.sectionSize(last_logical)), count

    def _paint_task_section(self, painter, rect, logical_index, *, force_selected: bool | None = None, ghost: bool = False, placeholder: bool = False):
        if not rect.isValid():
            return
        logical = int(logical_index)
        is_group = self._is_group_section(logical)
        is_child = self._is_child_section(logical)
        painter.save()
        if ghost:
            try:
                painter.setOpacity(0.96)
            except Exception:
                pass
        is_selected = logical == self._selected_section if force_selected is None else bool(force_selected)
        if placeholder:
            painter.fillRect(rect, QtGui.QColor("#10151c"))
        elif is_group:
            painter.fillRect(rect, QtGui.QColor("#29405a") if is_selected else QtGui.QColor("#1a2735"))
        else:
            painter.fillRect(rect, QtGui.QColor("#1d4f74") if is_selected else QtGui.QColor("#141c27"))
        pen = QtGui.QPen(QtGui.QColor("#223041"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        text = ""
        try:
            model = self.model()
            if model is not None:
                text = str(model.headerData(int(logical_index), self.orientation(), QtCore.Qt.DisplayRole) or "")
        except Exception:
            text = ""
        font = painter.font()
        font.setBold(bool(is_selected) or is_group)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#f8fafc") if is_selected else QtGui.QColor("#dbe4ee"))
        if is_group:
            toggle_rect = self._group_toggle_rect(rect)
            self._paint_group_triangle(painter, toggle_rect, logical in self._expanded_group_sections, is_selected)
            text_left = int(toggle_rect.right() + 8)
            text_rect = QtCore.QRect(text_left, int(rect.top()) - 2, max(0, int(rect.right()) - text_left - 6), int(rect.height()) + 4)
            painter.drawText(text_rect, int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter), text)
            painter.restore()
            return
        checkbox_rect = self._checkbox_rect(rect)
        progress_rect = self._progress_rect(rect)
        notification_rect = self._notification_rect(rect)
        text_left = int(rect.left() + (24 if is_child else 8))
        text_right = int(notification_rect.left() - 4)
        text_rect = QtCore.QRect(text_left, int(rect.top()) - 2, max(0, text_right - text_left), int(rect.height()) + 4)
        painter.drawText(text_rect, int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter), text)
        if logical in self._notification_sections and self._notification_icon is not None and not self._notification_icon.isNull():
            icon_target = QtCore.QRect(notification_rect)
            source_rect = self._notification_icon.rect()
            if source_rect.width() > 0 and source_rect.height() > 0:
                try:
                    painter.setOpacity(1.0 if is_selected else 0.95)
                except Exception:
                    pass
                painter.drawPixmap(icon_target, self._notification_icon, source_rect)
                try:
                    painter.setOpacity(1.0)
                except Exception:
                    pass
        progress = self._progress_values.get(logical, 0)
        painter.setBrush(QtGui.QColor("#10161d"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#475569"), 1))
        painter.drawRoundedRect(QtCore.QRectF(progress_rect.adjusted(0, 0, -1, -1)), 3.0, 3.0)
        painter.setPen(QtGui.QColor("#cbd5e1"))
        painter.drawText(progress_rect.adjusted(3, -1, -5, -1), int(QtCore.Qt.AlignCenter), str(progress))
        checkbox_checked = logical in self._completed_sections
        painter.setBrush(QtGui.QColor("#12151a"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#475569"), 1))
        painter.drawRoundedRect(QtCore.QRectF(checkbox_rect.adjusted(0, 0, -1, -1)), 3.0, 3.0)
        if checkbox_checked:
            pen = QtGui.QPen(QtGui.QColor("#f8fafc"), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            p1 = QtCore.QPoint(int(checkbox_rect.left() + (checkbox_rect.width() * 0.22)), int(checkbox_rect.top() + (checkbox_rect.height() * 0.56)))
            p2 = QtCore.QPoint(int(checkbox_rect.left() + (checkbox_rect.width() * 0.44)), int(checkbox_rect.top() + (checkbox_rect.height() * 0.78)))
            p3 = QtCore.QPoint(int(checkbox_rect.left() + (checkbox_rect.width() * 0.80)), int(checkbox_rect.top() + (checkbox_rect.height() * 0.26)))
            painter.drawLine(p1, p2)
            painter.drawLine(p2, p3)
        painter.restore()

    def paintSection(self, painter, rect, logical_index):
        if int(logical_index) == self._dragging_section and self._drag_pointer_y is not None:
            self._paint_task_section(painter, rect, logical_index, placeholder=True)
            return
        self._paint_task_section(painter, rect, logical_index)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self.viewport())
        if self._drop_indicator_y is not None:
            pen = QtGui.QPen(QtGui.QColor("#ffffff"), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            y = max(0, min(int(self._drop_indicator_y), max(0, self.viewport().height() - 1)))
            painter.drawLine(0, y, self.viewport().width(), y)
        if self._dragging_section is not None and self._drag_pointer_y is not None:
            try:
                section_height = int(self.sectionSize(int(self._dragging_section)))
            except Exception:
                section_height = 0
            if section_height > 0:
                grab_offset = int(self._drag_grab_offset or (section_height // 2))
                ghost_top = int(self._drag_pointer_y) - grab_offset
                ghost_top = max(0, min(ghost_top, max(0, self.viewport().height() - section_height)))
                ghost_rect = QtCore.QRect(0, ghost_top, self.viewport().width(), section_height)
                self._paint_task_section(painter, ghost_rect, int(self._dragging_section), force_selected=True, ghost=True)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            logical_index = int(self.logicalIndexAt(event.pos()))
            if logical_index >= 0 and self._is_group_section(logical_index):
                if self._group_toggle_rect_for_section(logical_index).contains(event.pos()):
                    self._group_pressed_section = logical_index
                else:
                    self._group_pressed_section = None
                self._dragging_section = None
                self._drag_start_visual_index = None
                self._drag_grab_offset = None
                self._drag_pointer_y = None
                self._set_drop_indicator_y(None)
                self._set_drop_indicator_index(None)
                event.accept()
                return
            if logical_index >= 0 and self._progress_rect_for_section(logical_index).contains(event.pos()):
                self._progress_pressed_section = logical_index
                event.accept()
                return
            if logical_index >= 0 and self._checkbox_rect_for_section(logical_index).contains(event.pos()):
                self._checkbox_pressed_section = logical_index
                event.accept()
                return
            self._dragging_section = logical_index if logical_index >= 0 else None
            if self._dragging_section is not None:
                self.selectionRequested.emit(self._dragging_section)
                try:
                    self._drag_start_visual_index = int(self.visualIndex(self._dragging_section))
                except Exception:
                    self._drag_start_visual_index = None
                try:
                    section_top = int(self.sectionPosition(self._dragging_section))
                    self._drag_grab_offset = int(event.pos().y()) - section_top
                except Exception:
                    self._drag_grab_offset = None
                self._drag_pointer_y = int(event.pos().y())
                self._set_drop_indicator_index(self._drag_start_visual_index)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._group_pressed_section is not None:
            event.accept()
            return
        if self._progress_pressed_section is not None:
            event.accept()
            return
        if self._checkbox_pressed_section is not None:
            event.accept()
            return
        if bool(event.buttons() & QtCore.Qt.LeftButton) and self._dragging_section is not None:
            self._drag_pointer_y = int(event.pos().y())
            indicator_y, indicator_index = self._indicator_target_for_pos(int(event.pos().y()))
            self._set_drop_indicator_y(indicator_y)
            self._set_drop_indicator_index(indicator_index)
            try:
                self.viewport().update()
            except Exception:
                self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._group_pressed_section is not None:
            logical_index = int(self._group_pressed_section)
            pressed = (
                event.button() == QtCore.Qt.LeftButton
                and self._group_toggle_rect_for_section(logical_index).contains(event.pos())
            )
            self._group_pressed_section = None
            if pressed:
                self.groupToggleRequested.emit(logical_index)
            event.accept()
            return
        if self._progress_pressed_section is not None:
            logical_index = int(self._progress_pressed_section)
            pressed = self._progress_rect_for_section(logical_index).contains(event.pos())
            self._progress_pressed_section = None
            if pressed:
                self.progressEditRequested.emit(logical_index, self._progress_rect_for_section(logical_index))
            event.accept()
            return
        if self._checkbox_pressed_section is not None:
            logical_index = int(self._checkbox_pressed_section)
            pressed = self._checkbox_rect_for_section(logical_index).contains(event.pos())
            self._checkbox_pressed_section = None
            if pressed:
                checked = logical_index not in self._completed_sections
                self.completionToggled.emit(logical_index, checked)
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton and self._dragging_section is not None:
            try:
                start_visual = int(self._drag_start_visual_index) if self._drag_start_visual_index is not None else None
            except Exception:
                start_visual = None
            try:
                drop_index = int(self._drop_indicator_index) if self._drop_indicator_index is not None else None
            except Exception:
                drop_index = None
            if start_visual is not None and drop_index is not None:
                self.reorderRequested.emit(start_visual, drop_index)
            self._dragging_section = None
            self._drag_start_visual_index = None
            self._drag_grab_offset = None
            self._drag_pointer_y = None
            self._set_drop_indicator_y(None)
            self._set_drop_indicator_index(None)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._dragging_section = None
        self._drag_start_visual_index = None
        self._drag_grab_offset = None
        self._drag_pointer_y = None
        self._set_drop_indicator_y(None)
        self._set_drop_indicator_index(None)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            logical_index = int(self.logicalIndexAt(event.pos()))
            if logical_index >= 0:
                if self._is_group_section(logical_index):
                    event.accept()
                    return
                if self._progress_rect_for_section(logical_index).contains(event.pos()):
                    event.accept()
                    return
                if self._checkbox_rect_for_section(logical_index).contains(event.pos()):
                    event.accept()
                    return
                self.renameRequested.emit(logical_index)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._group_pressed_section = None
        self._progress_pressed_section = None
        self._checkbox_pressed_section = None
        if self._dragging_section is None:
            self._set_drop_indicator_y(None)
            self._set_drop_indicator_index(None)


class _GanttMonthStrip(QtWidgets.QWidget):
    def __init__(self, table=None, parent=None):
        super().__init__(parent)
        self._table = table
        self._visible_dates: list[date] = []
        self._today = date.today()
        self.setFixedHeight(16)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background:transparent;")

    def set_dates(self, visible_dates: list[date], today_value: date) -> None:
        self._visible_dates = list(visible_dates or [])
        self._today = today_value
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._visible_dates or self._table is None:
            return
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        except Exception:
            pass
        font = painter.font()
        font.setBold(True)
        try:
            font.setPointSize(max(8, int(font.pointSize()) - 1))
        except Exception:
            pass
        painter.setFont(font)
        header_width = int(self._table.verticalHeader().width())
        month_starts: list[tuple[int, date]] = []
        previous = None
        for column, visible_date in enumerate(self._visible_dates):
            key = (visible_date.year, visible_date.month)
            if key != previous:
                month_starts.append((column, visible_date))
                previous = key
        left_margin = int(header_width + 3)
        gap = 6
        for index, (column, visible_date) in enumerate(month_starts):
            if index == 0:
                x = left_margin
            else:
                try:
                    x = int(header_width + self._table.columnViewportPosition(column) + 3)
                except Exception:
                    continue
            if x >= self.width():
                continue
            next_x = self.width()
            if (index + 1) < len(month_starts):
                next_column = int(month_starts[index + 1][0])
                try:
                    next_x = int(header_width + self._table.columnViewportPosition(next_column) + 3)
                except Exception:
                    next_x = self.width()
            if (index + 1) < len(month_starts):
                segment_columns = max(1, int(month_starts[index + 1][0]) - int(column))
            else:
                segment_columns = max(1, len(self._visible_dates) - int(column))
            rect_width = min(max(1, self.width() - x), max(1, next_x - x - gap))
            if rect_width <= 0:
                continue
            year_short = int(visible_date.year) % 100
            if segment_columns <= 1:
                label = f"{int(visible_date.month):02d}"
            elif segment_columns == 2:
                label = f"{calendar.month_abbr[visible_date.month]}'{year_short:02d}"
            elif segment_columns == 3:
                label = f"{calendar.month_abbr[visible_date.month]} {visible_date.year}"
            else:
                label = f"{calendar.month_name[visible_date.month]} {visible_date.year}"
            painter.setPen(
                QtGui.QColor("#f97316")
                if visible_date.year == self._today.year and visible_date.month == self._today.month
                else QtGui.QColor("#e5eef8")
            )
            text_rect = QtCore.QRect(x, 0, rect_width, self.height())
            painter.save()
            painter.setClipRect(text_rect)
            painter.drawText(
                text_rect,
                int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                label,
            )
            painter.restore()
        painter.end()


def _reorder_note_params(node_item, src_item, src_model, ordered_names: list[str]) -> bool:
    if src_model is None:
        return False
    params = list(getattr(src_model, "params", None) or [])
    hidden = _hidden_params(src_model)
    visible_entries = []
    other_entries = []
    for entry in params:
        name = (entry.get("name") or "").strip()
        key = name.lower()
        if name and key != "__ui_hidden_params" and key not in hidden:
            visible_entries.append(entry)
        else:
            other_entries.append(entry)
    if not visible_entries:
        return False

    by_name = {(entry.get("name") or "").strip(): entry for entry in visible_entries}
    reordered_visible = [by_name[name] for name in ordered_names if name in by_name]
    seen_ids = {id(entry) for entry in reordered_visible}
    for entry in visible_entries:
        if id(entry) not in seen_ids:
            reordered_visible.append(entry)

    new_params = reordered_visible + other_entries
    if new_params == params:
        return False
    src_model.params = new_params
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None and hasattr(scene, "set_node_params"):
        try:
            scene.set_node_params(src_model.name, new_params, rebuild=True, emit=True)
        except Exception:
            pass
    return True


def _qdatetime_from_datetime(value: datetime) -> QtCore.QDateTime:
    try:
        return QtCore.QDateTime.fromSecsSinceEpoch(int(value.timestamp()))
    except Exception:
        qdt = QtCore.QDateTime.currentDateTime()
        try:
            qdt.setSecsSinceEpoch(int(value.timestamp()))
        except Exception:
            pass
        return qdt


class TaskNotificationDialog(QtWidgets.QDialog):
    ClearResult = 2

    def __init__(
        self,
        parent=None,
        *,
        task_name: str,
        initial_when: datetime | None = None,
        initial_message: str = "",
        has_existing: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("Task Notification")
        self.setModal(True)
        self.resize(420, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        task_label = QtWidgets.QLabel(f"Task: {task_name}", self)
        task_label.setStyleSheet("QLabel{color:#cbd5e1;font-weight:600;}")
        layout.addWidget(task_label)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)

        self._when_edit = QtWidgets.QDateTimeEdit(self)
        self._when_edit.setCalendarPopup(True)
        self._when_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._when_edit.setDateTime(
            _qdatetime_from_datetime(initial_when or (datetime.now() + timedelta(minutes=5)))
        )
        form.addRow("Notify at", self._when_edit)

        self._message_edit = QtWidgets.QLineEdit(self)
        self._message_edit.setPlaceholderText("Optional reminder message")
        self._message_edit.setText(str(initial_message or "").strip())
        form.addRow("Message", self._message_edit)
        layout.addLayout(form)

        self._button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            self,
        )
        self._save_button = self._button_box.button(QtWidgets.QDialogButtonBox.Ok)
        if self._save_button is not None:
            self._save_button.setText("Save")
        if has_existing:
            self._clear_button = self._button_box.addButton("Clear", QtWidgets.QDialogButtonBox.ResetRole)
            self._clear_button.clicked.connect(self._clear_notification)
        else:
            self._clear_button = None
        self._button_box.accepted.connect(self._validate_and_accept)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

    def _validate_and_accept(self) -> None:
        notify_at = self.notification_datetime()
        if notify_at <= datetime.now():
            QtWidgets.QMessageBox.warning(self, "Task Notification", "Choose a future date and time.")
            return
        self.accept()

    def _clear_notification(self) -> None:
        self.done(self.ClearResult)

    def notification_datetime(self) -> datetime:
        qdt = self._when_edit.dateTime()
        try:
            return datetime.fromtimestamp(int(qdt.toSecsSinceEpoch())).replace(microsecond=0)
        except Exception:
            parsed = _parse_notification_datetime(qdt.toString("yyyy-MM-ddTHH:mm:ss"))
            return parsed or datetime.now().replace(microsecond=0)

    def message_text(self) -> str:
        return str(self._message_edit.text() or "").strip()


class GanttChartWidget(QtWidgets.QFrame):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._sync_pending = False
        self._source_names: list[str] = []
        self._source_invalid = False
        self._task_names: list[str] = []
        self._task_display_names: dict[str, str] = {}
        self._task_source_names: dict[str, str] = {}
        self._task_source_refs: dict[str, tuple[object, object]] = {}
        self._source_info_by_name: dict[str, dict[str, object]] = {}
        self._group_order: list[str] = []
        self._group_tasks: dict[str, list[str]] = {}
        self._group_expanded: dict[str, bool] = {}
        self._row_entries: list[dict[str, str]] = []
        self._assignments: dict[str, tuple[str, str]] = {}
        self._completed_tasks: set[str] = set()
        self._task_progress: dict[str, int] = {}
        self._task_notifications: dict[str, dict[str, str]] = {}
        self._selected_task: str | None = None
        self._assignment_resize_preview: tuple[str, int, int] | None = None
        self._processing_due_notifications = False
        self._active_notification_popups: list[QtWidgets.QMessageBox] = []
        self._ignore_header_move = False
        self._today = date.today()
        self._base_start_date = date(self._today.year, self._today.month, 1)
        self._visible_start_date = self._base_start_date
        self._visible_dates: list[date] = []
        self._day_scroll_sync = False
        self._sidecar_loaded = False
        self._task_panel_width = _read_task_panel_width(node_item)

        self.setObjectName("GanttChartWidget")
        self.setStyleSheet(
            "QFrame#GanttChartWidget{background:#0f1216;border:1px solid #334155;border-radius:0px;}"
            "QLabel{color:#cbd5e1;}"
            "QTableWidget{background:#0f1216;color:#e2e8f0;border:1px solid #273244;border-radius:0px;"
            "gridline-color:#223041;selection-background-color:#16212b;selection-color:#e2e8f0;}"
            "QTableWidget::item:selected{background:#16212b;color:#e2e8f0;}"
            "QHeaderView::section{background:#141c27;color:#dbe4ee;border-top:0px;border-left:0px;"
            "border-right:1px solid #223041;border-bottom:1px solid #223041;padding:4px;font-weight:600;}"
            "QTableCornerButton::section{background:#000000;border-top:0px;border-left:0px;"
            "border-right:1px solid #223041;border-bottom:1px solid #223041;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 6)
        status_row.setSpacing(10)

        self._source_label = QtWidgets.QLabel("Connect one or more Note nodes to this input.")
        self._source_label.setStyleSheet("QLabel{color:#93a4b8;font-weight:600;}")
        status_row.addWidget(self._source_label, 1)

        self._selection_label = QtWidgets.QLabel("Click a task cell to place that task on a day.")
        self._selection_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._selection_label.setStyleSheet("QLabel{color:#93a4b8;}")
        status_row.addWidget(self._selection_label, 1)

        layout.addLayout(status_row)

        self._month_strip = _GanttMonthStrip(None, self)
        layout.addWidget(self._month_strip, 0)

        self._today_marker_strip = QtWidgets.QWidget(self)
        self._today_marker_strip.setFixedHeight(16)
        self._today_marker_strip.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._today_marker_strip.setStyleSheet("background:transparent;")
        self._today_marker_icon = QtWidgets.QLabel(self._today_marker_strip)
        self._today_marker_icon.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._today_marker_icon.setStyleSheet("background:transparent;")
        self._today_marker_icon.hide()
        self._today_marker_strip.setFixedHeight(0)
        self._today_marker_strip.hide()
        layout.addWidget(self._today_marker_strip, 0)

        self._table = _GanttCalendarTable(self)
        self._table.setVerticalHeader(_GanttTaskHeader(self._table))
        self._month_strip._table = self._table
        self._table.setColumnCount(0)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setFocusPolicy(QtCore.Qt.NoFocus)
        self._table.setWordWrap(False)
        self._table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._table.verticalHeader().setVisible(True)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(True)
        self._table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignCenter)
        self._table.horizontalHeader().setStyleSheet(
            "QHeaderView::section{background:#000000;color:#dbe4ee;border-top:0px;border-left:0px;"
            "border-right:1px solid #223041;border-bottom:1px solid #223041;"
            "padding-left:4px;padding-right:4px;padding-top:3px;padding-bottom:5px;font-weight:600;}"
        )
        self._table.horizontalHeader().setSectionsClickable(True)
        try:
            self._table.horizontalHeader().setHighlightSections(False)
        except Exception:
            pass
        try:
            self._table.horizontalHeader().setFixedHeight(_GANTT_TASK_ROW_HEIGHT)
        except Exception:
            try:
                self._table.horizontalHeader().setMinimumHeight(_GANTT_TASK_ROW_HEIGHT)
                self._table.horizontalHeader().setMaximumHeight(_GANTT_TASK_ROW_HEIGHT)
            except Exception:
                pass
        self._table.verticalHeader().setSectionsClickable(True)
        try:
            self._table.verticalHeader().setHighlightSections(True)
        except Exception:
            pass
        try:
            self._table.verticalHeader().completionToggled.connect(self._on_task_completion_toggled)
        except Exception:
            pass
        try:
            self._table.verticalHeader().progressEditRequested.connect(self._on_progress_edit_requested)
        except Exception:
            pass
        try:
            self._table.verticalHeader().renameRequested.connect(self._on_task_header_rename_requested)
        except Exception:
            pass
        try:
            self._table.verticalHeader().reorderRequested.connect(self._on_task_reorder_requested)
        except Exception:
            pass
        try:
            self._table.verticalHeader().selectionRequested.connect(self._on_task_header_clicked)
        except Exception:
            pass
        try:
            self._table.verticalHeader().groupToggleRequested.connect(self._on_group_toggle_requested)
        except Exception:
            pass

        try:
            fixed_mode = QtWidgets.QHeaderView.ResizeMode.Fixed
        except AttributeError:
            fixed_mode = QtWidgets.QHeaderView.Fixed
        try:
            _set_section_resize_mode(self._table.verticalHeader(), 0, fixed_mode)
        except Exception:
            pass
        try:
            self._table.verticalHeader().setDefaultSectionSize(_GANTT_TASK_ROW_HEIGHT)
            self._table.verticalHeader().setMinimumWidth(_GANTT_TASK_PANEL_MIN_WIDTH)
        except Exception:
            pass

        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.cellLeftDoubleClicked.connect(self._on_cell_double_clicked)
        try:
            self._table.verticalHeader().sectionClicked.connect(self._on_task_header_clicked)
        except Exception:
            pass
        try:
            self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        except Exception:
            pass
        self._table.cellShortRightClicked.connect(self._on_table_short_right_click)
        self._table.assignmentResizeMoved.connect(self._on_assignment_resize_moved)
        self._table.assignmentResizeFinished.connect(self._on_assignment_resize_finished)
        self._table.assignmentMoveMoved.connect(self._on_assignment_move_moved)
        self._table.assignmentMoveFinished.connect(self._on_assignment_move_finished)
        layout.addWidget(self._table, 1)

        self._task_divider_handle = _GanttTaskDividerHandle(self._table, self)
        self._task_divider_handle.dragMoved.connect(self._on_task_panel_width_dragged)
        self._task_divider_handle.dragFinished.connect(self._on_task_panel_width_drag_finished)
        self._task_divider_handle.raise_()

        self._today_jump_button = _GanttTodayButton(self._today, self._table)
        self._today_jump_button.clicked.connect(self._jump_to_today)
        self._today_jump_button.raise_()
        self._notification_button = _GanttNotificationButton(self._table)
        self._notification_button.setEnabled(False)
        self._notification_button.clicked.connect(self._open_notification_dialog)
        self._notification_button.raise_()
        self._copy_button = _GanttCopyButton(self._table)
        self._copy_button.setEnabled(False)
        self._copy_button.clicked.connect(self._copy_chart_data)
        self._copy_button.raise_()
        self._paste_button = _GanttPasteButton(self._table)
        self._paste_button.setEnabled(False)
        self._paste_button.clicked.connect(self._paste_chart_data)
        self._paste_button.raise_()

        self._progress_editor = QtWidgets.QSpinBox(self._table.verticalHeader().viewport())
        self._progress_editor.setRange(0, 100)
        self._progress_editor.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self._progress_editor.setFrame(True)
        self._progress_editor.setAlignment(QtCore.Qt.AlignCenter)
        self._progress_editor.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._progress_editor.setStyleSheet(
            "QSpinBox{background:#10161d;color:#e2e8f0;border:1px solid #475569;border-radius:3px;padding:0 4px;}"
        )
        self._progress_editor.hide()
        self._progress_edit_section: int | None = None
        self._progress_editor.editingFinished.connect(self._commit_progress_edit)
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
        except Exception:
            pass

        self._notification_timer = QtCore.QTimer(self)
        self._notification_timer.setInterval(1000)
        self._notification_timer.timeout.connect(self._process_due_notifications)
        self._notification_timer.start()

        self._apply_task_panel_width(self._task_panel_width, persist=False)

        self._day_scroll = QtWidgets.QScrollBar(QtCore.Qt.Horizontal, self)
        self._day_scroll.setRange(0, _DAY_SCROLL_RANGE)
        self._day_scroll.setSingleStep(_DAY_SCROLL_UNITS_PER_DAY)
        self._day_scroll.setPageStep(_VISIBLE_DAY_COUNT * _DAY_SCROLL_UNITS_PER_DAY)
        self._day_scroll.setValue(_DAY_SCROLL_CENTER)
        self._day_scroll.setToolTip("Scroll through days")
        self._day_scroll.valueChanged.connect(self._on_day_scroll_changed)
        layout.addWidget(self._day_scroll, 0)

        self._ensure_scene()
        self._sync_from_source()
        QtCore.QTimer.singleShot(0, self._post_attach_sync)

    def sizeHint(self):
        return QtCore.QSize(1000, 320)

    def eventFilter(self, obj, event):
        try:
            editor_visible = bool(self._progress_editor.isVisible())
        except Exception:
            editor_visible = False
        if editor_visible and event is not None:
            try:
                event_type = event.type()
            except Exception:
                event_type = None
            if event_type in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
                try:
                    global_pos = event.globalPosition().toPoint()
                except Exception:
                    try:
                        global_pos = event.globalPos()
                    except Exception:
                        global_pos = None
                if global_pos is not None:
                    try:
                        local_pos = self._progress_editor.mapFromGlobal(global_pos)
                        inside_editor = self._progress_editor.rect().contains(local_pos)
                    except Exception:
                        inside_editor = False
                    if not inside_editor:
                        QtCore.QTimer.singleShot(0, self._commit_progress_edit)
        return super().eventFilter(obj, event)

    def _clamp_task_panel_width(self, width: int) -> int:
        try:
            table_width = int(self._table.width())
        except Exception:
            table_width = 0
        min_width = int(_GANTT_TASK_PANEL_MIN_WIDTH)
        max_width = max(min_width, table_width - (_GANTT_DAY_COLUMN_WIDTH * 6)) if table_width > 0 else min_width
        return max(min_width, min(int(width), max_width))

    def _apply_task_panel_width(self, width: int, *, persist: bool) -> None:
        clamped = self._clamp_task_panel_width(width)
        self._task_panel_width = clamped
        try:
            self._table.verticalHeader().setFixedWidth(clamped)
        except Exception:
            try:
                self._table.verticalHeader().setMinimumWidth(clamped)
                self._table.verticalHeader().setMaximumWidth(clamped)
            except Exception:
                pass
        if persist:
            _write_task_panel_width(self._node_item, clamped)
        try:
            self._table.updateGeometries()
        except Exception:
            pass
        try:
            self._table.doItemsLayout()
        except Exception:
            pass
        self._update_task_divider_geometry()
        self._month_strip.update()
        self._update_corner_button_geometry()
        self._update_today_marker()
        try:
            self._table.viewport().update()
            self._table.verticalHeader().viewport().update()
            self._table.horizontalHeader().viewport().update()
        except Exception:
            pass

    def _on_task_panel_width_dragged(self, width: int) -> None:
        self._apply_task_panel_width(width, persist=False)

    def _on_task_panel_width_drag_finished(self, width: int) -> None:
        self._apply_task_panel_width(width, persist=True)

    def _update_task_divider_geometry(self) -> None:
        try:
            table_x = int(self._table.x())
            table_y = int(self._table.y())
            frame = int(self._table.frameWidth())
            header_width = int(self._table.verticalHeader().width())
            handle_width = int(_GANTT_TASK_PANEL_HANDLE_WIDTH)
            divider_x = table_x + frame + header_width
            x = max(0, divider_x - (handle_width // 2))
            y = max(0, table_y + frame)
            h = max(1, int(self._table.height()) - (frame * 2))
            self._task_divider_handle.setGeometry(x, y, handle_width, h)
            self._task_divider_handle.raise_()
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self._progress_editor.hide()
        except Exception:
            pass
        self._progress_edit_section = None
        self._apply_task_panel_width(self._task_panel_width, persist=False)
        self._update_corner_button_geometry()
        self._update_today_marker()

    def _post_attach_sync(self):
        self._ensure_scene()
        self._schedule_sync()

    def _visible_day_count(self) -> int:
        return int(len(self._visible_dates) or _VISIBLE_DAY_COUNT)

    def _visible_date_for_day(self, day: int) -> date:
        return self._visible_start_date + timedelta(days=max(0, int(day) - 1))

    def _assignment_dates_for_task(self, task: str) -> tuple[date, date] | None:
        raw = self._assignments.get(task)
        if not raw:
            return None
        try:
            start_iso, end_iso = raw
            start_date = date.fromisoformat(str(start_iso))
            end_date = date.fromisoformat(str(end_iso))
        except Exception:
            return None
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        return start_date, end_date

    def _visible_assignment_span(self, task: str) -> tuple[int, int] | None:
        dates = self._assignment_dates_for_task(task)
        if dates is None:
            return None
        start_date, end_date = dates
        start_delta = int((start_date - self._visible_start_date).days)
        end_delta = int((end_date - self._visible_start_date).days)
        day_count = self._visible_day_count()
        if end_delta < 0 or start_delta >= day_count:
            return None
        return max(0, start_delta), min(day_count - 1, end_delta)

    def _visible_column_for_assignment(self, task: str) -> int | None:
        span = self._visible_assignment_span(task)
        return None if span is None else int(span[0])

    def _rebuild_row_entries(self) -> None:
        rows: list[dict[str, str]] = []
        for group_name in self._group_order:
            rows.append({"kind": "group", "group": str(group_name)})
            if self._group_expanded.get(group_name, True):
                for task_id in (self._group_tasks.get(group_name) or []):
                    rows.append({"kind": "task", "group": str(group_name), "task": str(task_id)})
        self._row_entries = rows

    def _row_entry(self, row: int) -> dict[str, str] | None:
        if row < 0 or row >= len(self._row_entries):
            return None
        return self._row_entries[row]

    def _task_for_row(self, row: int) -> str | None:
        entry = self._row_entry(row)
        if not entry or entry.get("kind") != "task":
            return None
        task_id = str(entry.get("task") or "").strip()
        return task_id or None

    def _row_for_task(self, task_id: str | None) -> int | None:
        key = str(task_id or "").strip()
        if not key:
            return None
        for row, entry in enumerate(self._row_entries):
            if entry.get("kind") != "task":
                continue
            if str(entry.get("task") or "").strip() == key:
                return row
        return None

    def _task_name_for_id(self, task_id: str) -> str:
        return str(self._task_display_names.get(str(task_id or ""), "") or "")

    def _task_group_name(self, task_id: str) -> str:
        return str(self._task_source_names.get(str(task_id or ""), "") or "")

    def _task_display_label(self, task_id: str) -> str:
        task_name = self._task_name_for_id(task_id)
        group_name = self._task_group_name(task_id)
        if group_name and task_name:
            return f"{group_name} / {task_name}"
        return task_name or group_name or str(task_id or "").strip()

    def _source_ref_for_task(self, task_id: str):
        return self._task_source_refs.get(str(task_id or ""), (None, None))

    def _set_source_task_completed(self, task_id: str, checked: bool, *, notify_scene: bool) -> bool:
        source_name = self._task_group_name(task_id)
        source_info = self._source_info_by_name.get(source_name) or {}
        src_item = source_info.get("item")
        src_model = source_info.get("model")
        task_name = self._task_name_for_id(task_id)
        if not task_name or src_model is None:
            return False
        completed_names = set(_completed_task_names(src_model))
        if checked:
            completed_names.add(task_name)
        else:
            completed_names.discard(task_name)
        return _write_source_completed_tasks(
            self._node_item,
            src_item,
            src_model,
            completed_names,
            notify_scene=notify_scene,
        )

    def _toggle_group(self, group_name: str) -> None:
        name = str(group_name or "").strip()
        if not name or name not in self._group_expanded:
            return
        expanded = bool(self._group_expanded.get(name, True))
        self._group_expanded[name] = not expanded
        _write_group_state(self._node_item, self._group_expanded, notify_scene=False)
        if expanded and self._selected_task in set(self._group_tasks.get(name) or []):
            self._selected_task = None
        self._rebuild_row_entries()
        self._rebuild_table()
        self._update_labels()

    def _on_group_toggle_requested(self, section: int) -> None:
        entry = self._row_entry(int(section))
        if not entry or entry.get("kind") != "group":
            return
        self._toggle_group(str(entry.get("group") or ""))

    def _sync_view_start_from_params(self, *, force: bool = False):
        model = getattr(self._node_item, "model", None)
        fallback = self._base_start_date
        raw_start = _param_value(model, _VIEW_START_PARAM)
        if raw_start.strip():
            visible_start = _parse_view_start(raw_start, fallback=fallback)
        else:
            year, month = _parse_view_month(
                _param_value(model, _VIEW_MONTH_PARAM),
                fallback_year=fallback.year,
                fallback_month=fallback.month,
            )
            visible_start = date(year, month, 1)
        if (not force) and visible_start == self._visible_start_date and self._visible_dates:
            return
        self._set_visible_start_date(visible_start, sync_scroll=True, persist=False)

    def _update_today_marker(self):
        icon = _today_icon()
        if icon is None:
            self._today_marker_icon.hide()
            return
        header = self._table.horizontalHeader()
        try:
            header_rect = header.geometry()
        except Exception:
            header_rect = QtCore.QRect()
        if header_rect.isNull():
            self._today_marker_icon.hide()
            return
        if self._today_marker_icon.parent() is not self._table:
            self._today_marker_icon.setParent(self._table)
            self._today_marker_icon.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self._today_marker_icon.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            self._today_marker_icon.setStyleSheet("background:transparent;")
        today_col = int((self._today - self._visible_start_date).days)
        if today_col < 0 or today_col >= self._table.columnCount():
            self._today_marker_icon.hide()
            return
        pm = icon.pixmap(12, 12)
        self._today_marker_icon.setPixmap(pm)
        try:
            x = int(header_rect.left() + header.sectionPosition(today_col) - (pm.width() / 2.0))
        except Exception:
            self._today_marker_icon.hide()
            return
        x = max(0, min(x, max(0, self._table.width() - pm.width())))
        y = max(0, int(header_rect.top() + ((header_rect.height() - pm.height()) / 2.0) + _TODAY_MARKER_Y_OFFSET))
        self._today_marker_icon.move(x, y)
        self._today_marker_icon.resize(pm.size())
        self._today_marker_icon.show()
        self._today_marker_icon.raise_()
        self._month_strip.update()

    def _update_corner_button_geometry(self):
        try:
            header_height = int(self._table.horizontalHeader().height())
            corner_width = int(self._table.verticalHeader().width())
            frame = int(self._table.frameWidth())
        except Exception:
            return
        button_size = max(18, min(24, corner_width - 4, header_height - 4))
        gap = 6
        buttons = [
            self._notification_button,
            self._today_jump_button,
            self._copy_button,
            self._paste_button,
        ]
        group_width = (button_size * len(buttons)) + (gap * max(0, len(buttons) - 1))
        x = max(0, frame + int((corner_width - group_width) / 2.0))
        y = max(0, frame + int((header_height - button_size) / 2.0))
        for index, button in enumerate(buttons):
            button.setGeometry(x + (index * (button_size + gap)), y, button_size, button_size)
            button.raise_()

    def _on_progress_edit_requested(self, section: int, rect_obj):
        task = self._task_for_row(int(section))
        if not task:
            return
        try:
            rect = QtCore.QRect(rect_obj)
        except Exception:
            return
        self._progress_edit_section = int(section)
        self._progress_editor.setGeometry(rect.adjusted(0, 0, 0, 0))
        self._progress_editor.setValue(int(self._task_progress.get(task, 0)))
        self._progress_editor.show()
        self._progress_editor.raise_()
        self._progress_editor.setFocus(QtCore.Qt.MouseFocusReason)
        try:
            self._progress_editor.selectAll()
        except Exception:
            try:
                self._progress_editor.lineEdit().selectAll()
            except Exception:
                pass

    def _commit_progress_edit(self):
        section = self._progress_edit_section
        self._progress_edit_section = None
        try:
            self._progress_editor.hide()
        except Exception:
            pass
        if section is None:
            return
        task = self._task_for_row(int(section))
        if not task:
            return
        self._set_task_progress(task, int(self._progress_editor.value()))

    def _update_notification_button(self) -> None:
        selected_task = str(self._selected_task or "").strip()
        if not selected_task:
            self._notification_button.setToolTip("Select a task to schedule a reminder.")
            self._notification_button.setEnabled(False)
            return
        entry = self._task_notifications.get(selected_task) or {}
        notify_at = _parse_notification_datetime(entry.get("notify_at"))
        selected_label = self._task_display_label(selected_task)
        if notify_at is None:
            self._notification_button.setToolTip(f"Schedule a reminder for {selected_label}.")
        else:
            self._notification_button.setToolTip(
                f"Reminder for {selected_label}: {_serialize_notification_datetime(notify_at)}"
            )
        self._notification_button.setEnabled(True)

    def _update_transfer_buttons(self) -> None:
        has_tasks = bool(self._task_names)
        self._copy_button.setEnabled(has_tasks)
        if has_tasks:
            self._copy_button.setToolTip("Copy this chart's assignments and progress to a CSV log.")
        else:
            self._copy_button.setToolTip("No tasks available to copy.")
        log_path = _resolve_gantt_transfer_log_path(self._node_item)
        has_log = bool(log_path is not None and log_path.is_file())
        can_paste = bool(has_tasks and has_log)
        self._paste_button.setEnabled(can_paste)
        if not has_tasks:
            self._paste_button.setToolTip("No tasks available to paste into.")
        elif not has_log:
            self._paste_button.setToolTip("Copy chart data first.")
        else:
            self._paste_button.setToolTip(f"Paste from copied log: {log_path.name}")

    def _copy_chart_data(self) -> None:
        if not self._task_names:
            self._update_transfer_buttons()
            return
        chart_model = getattr(self._node_item, "model", None)
        chart_name = str(getattr(chart_model, "name", "") or "").strip() or "gantt_chart"
        log_path = _gantt_transfer_log_path(self._node_item, chart_name)
        if log_path is None:
            QtWidgets.QMessageBox.warning(
                _dialog_parent_for_node(self._node_item),
                "Copy Gantt Data",
                "Unable to prepare a transfer-log location for this workflow.",
            )
            self._update_transfer_buttons()
            return
        copied_at = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        rows = []
        scheduled_count = 0
        for task_id in self._task_names:
            task_name = self._task_name_for_id(task_id)
            if not task_name:
                continue
            assignment_raw = self._assignments.get(task_id)
            start_date = ""
            end_date = ""
            if assignment_raw:
                start_date = str(assignment_raw[0] or "")
                end_date = str(assignment_raw[1] or "")
                scheduled_count += 1
            progress = _coerce_progress_value(self._task_progress.get(task_id, 0))
            if progress is None:
                progress = 0
            rows.append(
                {
                    "source_chart": chart_name,
                    "source": self._task_group_name(task_id),
                    "task": task_name,
                    "progress": int(progress),
                    "start_date": start_date,
                    "end_date": end_date,
                    "copied_at": copied_at,
                }
            )
        if not rows:
            self._update_transfer_buttons()
            return
        try:
            with log_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "source_chart",
                        "source",
                        "task",
                        "progress",
                        "start_date",
                        "end_date",
                        "copied_at",
                    ],
                )
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                _dialog_parent_for_node(self._node_item),
                "Copy Gantt Data",
                f"Failed to write transfer log:\n{exc}",
            )
            self._update_transfer_buttons()
            return
        _set_gantt_transfer_buffer(
            {
                "version": 2,
                "log_path": str(log_path),
                "copied_at": copied_at,
                "row_count": len(rows),
            }
        )
        _write_gantt_transfer_pointer(self._node_item, log_path)
        self._selection_label.setText(
            f"Copied {len(rows)} tasks ({scheduled_count} scheduled) to {log_path.name}"
        )
        self._update_transfer_buttons()

    def _paste_chart_data(self) -> None:
        log_path = _resolve_gantt_transfer_log_path(self._node_item)
        if log_path is None or not log_path.is_file():
            QtWidgets.QMessageBox.information(
                _dialog_parent_for_node(self._node_item),
                "Paste Gantt Data",
                "No copied Gantt log is available yet. Use Copy first.",
            )
            self._update_transfer_buttons()
            return
        _set_gantt_transfer_buffer(
            {
                "version": 2,
                "log_path": str(log_path),
            }
        )
        task_rows = []
        try:
            with log_path.open("r", newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if isinstance(row, dict):
                        task_rows.append(row)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                _dialog_parent_for_node(self._node_item),
                "Paste Gantt Data",
                f"Failed to read copied log:\n{exc}",
            )
            self._update_transfer_buttons()
            return
        if not task_rows:
            QtWidgets.QMessageBox.information(
                _dialog_parent_for_node(self._node_item),
                "Paste Gantt Data",
                "The copied log file has no task rows.",
            )
            self._update_transfer_buttons()
            return
        if not self._task_names:
            self._update_transfer_buttons()
            return

        by_source_task: dict[tuple[str, str], str] = {}
        by_source_task_normalized: dict[tuple[str, str], str] = {}
        by_task_name: dict[str, list[str]] = {}
        for task_id in self._task_names:
            source_key = self._task_group_name(task_id).strip().lower()
            source_key_norm = _normalized_source_key(source_key)
            task_key = self._task_name_for_id(task_id).strip().lower()
            if not task_key:
                continue
            by_source_task.setdefault((source_key, task_key), task_id)
            if source_key_norm:
                by_source_task_normalized.setdefault((source_key_norm, task_key), task_id)
            by_task_name.setdefault(task_key, []).append(task_id)

        assignments = dict(self._assignments)
        progress_map = dict(self._task_progress)
        completed_by_source: dict[str, set[str]] = {}
        touched_sources: set[str] = set()
        used_task_ids: set[str] = set()
        matched_count = 0
        for raw_entry in task_rows:
            if not isinstance(raw_entry, dict):
                continue
            raw_task = str(raw_entry.get("task") or "").strip()
            if not raw_task:
                continue
            source_hint = str(raw_entry.get("source") or "").strip().lower()
            source_hint_norm = _normalized_source_key(source_hint)
            task_key = raw_task.lower()
            task_id = by_source_task.get((source_hint, task_key))
            if task_id is None and source_hint_norm:
                task_id = by_source_task_normalized.get((source_hint_norm, task_key))
            if task_id is None:
                candidates = by_task_name.get(task_key) or []
                if not candidates:
                    continue
                if source_hint_norm:
                    normalized_candidates = [
                        candidate
                        for candidate in candidates
                        if _normalized_source_key(self._task_group_name(candidate)) == source_hint_norm
                    ]
                    if len(normalized_candidates) == 1:
                        task_id = normalized_candidates[0]
                    elif normalized_candidates:
                        candidates = normalized_candidates
                task_id = None
                for candidate in candidates:
                    if candidate not in used_task_ids:
                        task_id = candidate
                        break
                if task_id is None:
                    task_id = candidates[0]
            used_task_ids.add(task_id)
            matched_count += 1

            start_text = str(raw_entry.get("start_date") or "").strip()
            end_text = str(raw_entry.get("end_date") or "").strip()
            if not start_text and not end_text:
                assignments.pop(task_id, None)
            else:
                normalized = _coerce_assignment_entry(
                    [start_text, end_text],
                    default_year=self._visible_start_date.year,
                    default_month=self._visible_start_date.month,
                )
                if normalized is not None:
                    assignments[task_id] = normalized
                else:
                    assignments.pop(task_id, None)

            progress = _coerce_progress_value(raw_entry.get("progress"))
            if progress is None:
                progress = 0
            progress_map[task_id] = progress

            source_name = self._task_group_name(task_id)
            task_name = self._task_name_for_id(task_id)
            if source_name and task_name:
                touched_sources.add(source_name)
                if source_name not in completed_by_source:
                    source_info = self._source_info_by_name.get(source_name) or {}
                    src_model = source_info.get("model")
                    completed_by_source[source_name] = set(_completed_task_names(src_model))
                if progress >= 100:
                    completed_by_source[source_name].add(task_name)
                else:
                    completed_by_source[source_name].discard(task_name)

        if matched_count <= 0:
            QtWidgets.QMessageBox.information(
                _dialog_parent_for_node(self._node_item),
                "Paste Gantt Data",
                "No matching tasks were found for the copied data.",
            )
            self._update_transfer_buttons()
            return

        self._assignments = assignments
        self._task_progress = progress_map
        _write_assignments(self._node_item, assignments, notify_scene=False)
        _write_progress_map(self._node_item, progress_map, notify_scene=False)
        for source_name in touched_sources:
            source_info = self._source_info_by_name.get(source_name) or {}
            src_item = source_info.get("item")
            src_model = source_info.get("model")
            if src_model is None:
                continue
            _write_source_completed_tasks(
                self._node_item,
                src_item,
                src_model,
                completed_by_source.get(source_name, set()),
                notify_scene=False,
            )
        self._selection_label.setText(f"Pasted data from {log_path.name} into {matched_count} task rows.")
        self._schedule_sync()
        self._update_transfer_buttons()

    def _open_notification_dialog(self) -> None:
        task = str(self._selected_task or "").strip()
        if not task:
            QtWidgets.QMessageBox.information(
                _dialog_parent_for_node(self._node_item),
                "Task Notification",
                "Select a task from the list before creating a notification.",
            )
            return
        current_entry = self._task_notifications.get(task) or {}
        dlg = TaskNotificationDialog(
            _dialog_parent_for_node(self._node_item),
            task_name=self._task_display_label(task),
            initial_when=_parse_notification_datetime(current_entry.get("notify_at")),
            initial_message=str(current_entry.get("message") or ""),
            has_existing=bool(current_entry),
        )
        try:
            result = dlg.exec()
        except Exception:
            result = dlg.exec_()
        if result == TaskNotificationDialog.ClearResult:
            notifications = dict(self._task_notifications)
            if task in notifications:
                notifications.pop(task, None)
                self._task_notifications = notifications
                _write_notifications(self._node_item, notifications, notify_scene=False)
                self._apply_table_styles()
                self._update_labels()
            return
        if result != QtWidgets.QDialog.Accepted:
            return
        notifications = dict(self._task_notifications)
        notifications[task] = {
            "notify_at": _serialize_notification_datetime(dlg.notification_datetime()),
            "message": dlg.message_text(),
        }
        self._task_notifications = notifications
        _write_notifications(self._node_item, notifications, notify_scene=False)
        self._apply_table_styles()
        self._update_labels()

    def _show_task_notification(self, task: str, message: str) -> None:
        _play_notification_alert_sound()
        body = str(message or "").strip()
        task_line = f"Task: {self._task_display_label(task)}"
        parent = _dialog_parent_for_node(self._node_item)
        box = QtWidgets.QMessageBox(parent)
        box.setWindowTitle("Task Notification")
        try:
            box.setIcon(QtWidgets.QMessageBox.NoIcon)
        except Exception:
            pass
        try:
            if body:
                box.setTextFormat(QtCore.Qt.RichText)
                box.setText(f"<b>{html.escape(body)}</b>")
                box.setInformativeText(task_line)
            else:
                box.setText(task_line)
                box.setInformativeText("")
        except Exception:
            box.setText(f"{body}\n\n{task_line}" if body else task_line)
        try:
            icon_pm = _load_icon_pixmap("notification_timer.png")
            if icon_pm is not None and not icon_pm.isNull():
                box.setIconPixmap(icon_pm.scaled(28, 28, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        except Exception:
            pass
        try:
            box.setStyleSheet(
                "QLabel#qt_msgbox_label,QLabel#qt_msgbox_informativelabel{min-width:420px;}"
            )
        except Exception:
            pass
        try:
            box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        except Exception:
            pass
        try:
            button_box = box.findChild(QtWidgets.QDialogButtonBox)
            if button_box is not None:
                button_box.setCenterButtons(True)
        except Exception:
            pass
        try:
            box.adjustSize()
            target_width = max(520, int(box.sizeHint().width()))
            box.setMinimumWidth(target_width)
            box.resize(target_width, max(int(box.height()), int(box.sizeHint().height())))
        except Exception:
            pass
        try:
            box.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        except Exception:
            pass
        self._active_notification_popups.append(box)

        def _cleanup_popup(_result=0, popup=box):
            try:
                self._active_notification_popups.remove(popup)
            except Exception:
                pass

        try:
            box.finished.connect(_cleanup_popup)
        except Exception:
            pass
        try:
            box.open()
        except Exception:
            try:
                box.show()
            except Exception:
                try:
                    box.exec()
                except Exception:
                    box.exec_()

    def _process_due_notifications(self) -> None:
        if self._processing_due_notifications or not self._task_notifications:
            return
        self._processing_due_notifications = True
        try:
            now = datetime.now().replace(microsecond=0)
            due_items = []
            remaining = dict(self._task_notifications)
            changed = False
            for task, entry in sorted(
                self._task_notifications.items(),
                key=lambda item: str((item[1] or {}).get("notify_at") or ""),
            ):
                notify_at = _parse_notification_datetime((entry or {}).get("notify_at"))
                if notify_at is None:
                    remaining.pop(task, None)
                    changed = True
                    continue
                if notify_at <= now:
                    due_items.append((task, str((entry or {}).get("message") or "").strip()))
                    remaining.pop(task, None)
                    changed = True
            if changed:
                self._task_notifications = remaining
                _write_notifications(self._node_item, remaining, notify_scene=False)
                self._apply_table_styles()
                self._update_labels()
            for task, message in due_items:
                self._show_task_notification(task, message)
        finally:
            self._processing_due_notifications = False

    def _set_visible_start_date(self, visible_start: date, *, sync_scroll: bool = True, persist: bool = True) -> None:
        if visible_start == self._visible_start_date and self._visible_dates:
            if sync_scroll:
                offset = int((self._visible_start_date - self._base_start_date).days)
                new_value = _scroll_value_for_day_offset(offset)
                try:
                    self._day_scroll_sync = True
                    self._day_scroll.setValue(int(new_value))
                finally:
                    self._day_scroll_sync = False
            return
        self._visible_start_date = visible_start
        self._visible_dates = [
            self._visible_start_date + timedelta(days=offset)
            for offset in range(_VISIBLE_DAY_COUNT)
        ]
        if persist:
            _set_param_value(
                self._node_item,
                _VIEW_START_PARAM,
                self._visible_start_date.isoformat(),
                notify_scene=False,
            )
            _set_param_value(
                self._node_item,
                _VIEW_MONTH_PARAM,
                f"{self._visible_start_date.year:04d}-{self._visible_start_date.month:02d}",
                notify_scene=False,
            )
            _write_gantt_sidecar_snapshot(self._node_item)
        if sync_scroll:
            offset = int((self._visible_start_date - self._base_start_date).days)
            new_value = _scroll_value_for_day_offset(offset)
            try:
                self._day_scroll_sync = True
                self._day_scroll.setValue(int(new_value))
            finally:
                self._day_scroll_sync = False
        self._rebuild_table()
        self._update_labels()

    def _jump_to_today(self):
        self._set_visible_start_date(self._today)

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._schedule_sync)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(self._on_scene_param_changed)
            except Exception:
                pass
        self._scene_connected = True

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._schedule_sync()

    def _schedule_sync(self):
        self._ensure_scene()
        if self._sync_pending:
            return
        self._sync_pending = True
        QtCore.QTimer.singleShot(0, self._sync_from_source)

    def _sync_from_source(self):
        self._ensure_scene()
        self._sync_pending = False
        try:
            self._progress_editor.hide()
        except Exception:
            pass
        self._progress_edit_section = None
        if not self._sidecar_loaded:
            loaded = _load_gantt_sidecar_snapshot(self._node_item)
            if loaded:
                self._sidecar_loaded = True
            else:
                path = _gantt_sidecar_path(self._node_item)
                if path is not None and path.is_file():
                    self._sidecar_loaded = True
        self._sync_view_start_from_params(force=True)
        panel_width = _read_task_panel_width(self._node_item)
        if panel_width != self._task_panel_width:
            self._apply_task_panel_width(panel_width, persist=False)
        note_sources, invalid_inputs = _resolve_note_sources(self._node_item)
        source_names: list[str] = []
        task_ids: list[str] = []
        task_display: dict[str, str] = {}
        task_sources: dict[str, str] = {}
        task_refs: dict[str, tuple[object, object]] = {}
        source_info: dict[str, dict[str, object]] = {}
        group_order: list[str] = []
        group_tasks: dict[str, list[str]] = {}
        used_group_names = set()
        by_source_task: dict[tuple[str, str], str] = {}
        by_task_name: dict[str, list[str]] = {}
        for source_index, (src_item, src_model) in enumerate(note_sources):
            base_name = str(getattr(src_model, "name", "") or "").strip() or f"Note {source_index + 1}"
            group_name = base_name
            suffix = 2
            while group_name.lower() in used_group_names:
                group_name = f"{base_name} ({suffix})"
                suffix += 1
            used_group_names.add(group_name.lower())
            source_names.append(group_name)
            group_order.append(group_name)
            visible_tasks = _note_task_names(src_model)
            group_task_ids: list[str] = []
            aliases = [group_name]
            model_name = str(getattr(src_model, "name", "") or "").strip()
            if model_name and model_name.lower() != group_name.lower():
                aliases.append(model_name)
            for task_name in visible_tasks:
                task_id = _task_storage_key(group_name, task_name)
                group_task_ids.append(task_id)
                task_ids.append(task_id)
                task_display[task_id] = task_name
                task_sources[task_id] = group_name
                task_refs[task_id] = (src_item, src_model)
                task_key = task_name.strip().lower()
                if task_key:
                    for alias in aliases:
                        by_source_task.setdefault((alias.strip().lower(), task_key), task_id)
                    by_task_name.setdefault(task_key, []).append(task_id)
            group_tasks[group_name] = group_task_ids
            source_info[group_name] = {
                "item": src_item,
                "model": src_model,
                "task_ids": list(group_task_ids),
                "task_names": list(visible_tasks),
            }

        self._source_names = source_names
        self._source_info_by_name = source_info
        self._task_names = task_ids
        self._task_display_names = task_display
        self._task_source_names = task_sources
        self._task_source_refs = task_refs
        self._group_order = group_order
        self._group_tasks = group_tasks
        self._source_invalid = bool(invalid_inputs and not source_names)
        persisted_group_state = _read_group_state(self._node_item)
        previous_group_expanded = dict(self._group_expanded)
        next_group_expanded: dict[str, bool] = {}
        for group_name in self._group_order:
            if group_name in previous_group_expanded:
                next_group_expanded[group_name] = bool(previous_group_expanded.get(group_name, True))
            elif group_name in persisted_group_state:
                next_group_expanded[group_name] = bool(persisted_group_state.get(group_name, True))
            else:
                next_group_expanded[group_name] = True
        self._group_expanded = next_group_expanded
        self._rebuild_row_entries()

        assignments = _read_assignments(
            self._node_item,
            default_year=self._visible_start_date.year,
            default_month=self._visible_start_date.month,
        )
        progress_map = _read_progress_map(self._node_item)
        notifications = _read_notifications(self._node_item)
        has_valid_note_source = bool(source_names)
        valid_task_ids = set(task_ids)
        desired_group_state = {group_name: bool(self._group_expanded.get(group_name, True)) for group_name in self._group_order}
        persisted_visible_group_state = {
            group_name: bool(value)
            for group_name, value in (persisted_group_state or {}).items()
            if group_name in self._group_order
        }
        if has_valid_note_source and desired_group_state != persisted_visible_group_state:
            _write_group_state(self._node_item, desired_group_state, notify_scene=False)

        def _canonicalize(raw_mapping: dict):
            clean = {}
            changed = False
            for raw_key, value in (raw_mapping or {}).items():
                original_key = str(raw_key or "").strip()
                canonical = _canonical_task_key(
                    original_key,
                    valid_task_ids=valid_task_ids,
                    by_source_task=by_source_task,
                    by_task_name=by_task_name,
                )
                if canonical is None:
                    changed = True
                    continue
                if canonical != original_key:
                    changed = True
                clean[canonical] = value
            if len(clean) != len(raw_mapping or {}):
                changed = True
            return clean, changed

        pruned_assignments, assignments_changed = _canonicalize(assignments)
        pruned_progress, progress_key_changed = _canonicalize(progress_map)
        pruned_notifications, notifications_changed = _canonicalize(notifications)
        if has_valid_note_source and assignments_changed:
            _write_assignments(self._node_item, pruned_assignments, notify_scene=False)
        if has_valid_note_source and notifications_changed:
            _write_notifications(self._node_item, pruned_notifications, notify_scene=False)

        completed_ids: set[str] = set()
        synced_progress = dict(pruned_progress)
        progress_changed = bool(progress_key_changed)
        if has_valid_note_source:
            for group_name in self._group_order:
                info = self._source_info_by_name.get(group_name) or {}
                src_item = info.get("item")
                src_model = info.get("model")
                if src_model is None:
                    continue
                source_completed = set(_completed_task_names(src_model))
                source_task_names = {self._task_name_for_id(task_id) for task_id in (info.get("task_ids") or [])}
                source_task_names.discard("")
                cleaned_completed = {name for name in source_completed if name in source_task_names}
                for task_id in (info.get("task_ids") or []):
                    task_name = self._task_name_for_id(task_id)
                    current_value = int(synced_progress.get(task_id, 0))
                    if task_name in cleaned_completed:
                        completed_ids.add(task_id)
                        if current_value != 100:
                            synced_progress[task_id] = 100
                            progress_changed = True
                    elif current_value >= 100:
                        synced_progress[task_id] = 0
                        progress_changed = True
                if cleaned_completed != source_completed:
                    _write_source_completed_tasks(
                        self._node_item,
                        src_item,
                        src_model,
                        cleaned_completed,
                        notify_scene=False,
                    )
            if progress_changed:
                _write_progress_map(self._node_item, synced_progress, notify_scene=False)

        self._assignments = pruned_assignments if has_valid_note_source else assignments
        self._task_progress = synced_progress if has_valid_note_source else progress_map
        self._task_notifications = pruned_notifications if has_valid_note_source else notifications
        self._completed_tasks = completed_ids if has_valid_note_source else set()
        self._assignment_resize_preview = None
        if self._selected_task not in self._task_names:
            self._selected_task = None
        self._rebuild_table()
        self._update_labels()

    def _update_labels(self):
        if self._source_invalid:
            self._source_label.setText("Connect one or more Note nodes to this input.")
            self._source_label.setStyleSheet("QLabel{color:#fca5a5;font-weight:600;}")
        elif self._source_names:
            if len(self._source_names) == 1:
                self._source_label.setText(f"Source: {self._source_names[0]}")
            else:
                self._source_label.setText(f"Sources: {', '.join(self._source_names)}")
            self._source_label.setStyleSheet("QLabel{color:#93a4b8;font-weight:600;}")
        else:
            self._source_label.setText("Connect one or more Note nodes to this input.")
            self._source_label.setStyleSheet("QLabel{color:#93a4b8;font-weight:600;}")

        self._month_strip.set_dates(self._visible_dates, self._today)
        self._update_today_marker()

        if self._selected_task:
            raw = self._assignments.get(self._selected_task)
            selected_label = self._task_display_label(self._selected_task)
            if raw:
                try:
                    start_iso, end_iso = raw
                    assigned_start = date.fromisoformat(str(start_iso))
                    assigned_end = date.fromisoformat(str(end_iso))
                    if assigned_start == assigned_end:
                        self._selection_label.setText(
                            f"Selected: {selected_label} -> {calendar.month_abbr[assigned_start.month]} {assigned_start.day}, {assigned_start.year}"
                        )
                    else:
                        self._selection_label.setText(
                            f"Selected: {selected_label} -> {assigned_start.isoformat()} to {assigned_end.isoformat()}"
                        )
                except Exception:
                    self._selection_label.setText(f"Selected: {selected_label} -> {raw}")
            else:
                self._selection_label.setText(f"Selected: {selected_label} -> unscheduled")
        elif self._task_names:
            self._selection_label.setText("Click a task cell to place that task on a day.")
        elif self._source_names:
            self._selection_label.setText("The connected note nodes have no visible parameters.")
        else:
            self._selection_label.setText("Connect note task lists to place tasks on the chart.")
        self._update_notification_button()
        self._update_transfer_buttons()

    def _ensure_item(self, row: int, col: int) -> QtWidgets.QTableWidgetItem:
        item = self._table.item(row, col)
        if item is None:
            item = QtWidgets.QTableWidgetItem("")
            try:
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            except Exception:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            self._table.setItem(row, col, item)
        return item

    def _rebuild_table(self):
        self._table.blockSignals(True)
        self._ignore_header_move = True
        day_count = self._visible_day_count()
        self._table.setColumnCount(day_count)
        if not self._visible_dates:
            self._visible_dates = [
                self._visible_start_date + timedelta(days=offset)
                for offset in range(day_count)
            ]
        self._table.setHorizontalHeaderLabels([str(visible_date.day) for visible_date in self._visible_dates])
        try:
            fixed_mode = QtWidgets.QHeaderView.ResizeMode.Fixed
        except AttributeError:
            fixed_mode = QtWidgets.QHeaderView.Fixed
        today_column = None
        today_offset = int((self._today - self._visible_start_date).days)
        if 0 <= today_offset < day_count:
            today_column = today_offset
        for day in range(day_count):
            _set_section_resize_mode(self._table.horizontalHeader(), day, fixed_mode)
            self._table.setColumnWidth(day, _GANTT_DAY_COLUMN_WIDTH)
            header_item = self._table.horizontalHeaderItem(day)
            if header_item is None:
                header_item = QtWidgets.QTableWidgetItem(str(self._visible_dates[day].day))
                self._table.setHorizontalHeaderItem(day, header_item)
            header_item.setText(str(self._visible_dates[day].day))
            if today_column is not None and day == today_column:
                header_item.setForeground(QtGui.QBrush(QtGui.QColor("#22c55e")))
                header_item.setToolTip(f"Today: {self._today.isoformat()}")
            else:
                header_item.setIcon(QtGui.QIcon())
                header_item.setForeground(QtGui.QBrush(QtGui.QColor("#dbe4ee")))
                visible_date = self._visible_dates[day]
                header_item.setToolTip(f"{calendar.month_name[visible_date.month]} {visible_date.day}, {visible_date.year}")

        self._table.setRowCount(len(self._row_entries))
        for row, entry in enumerate(self._row_entries):
            is_group = entry.get("kind") == "group"
            group_name = str(entry.get("group") or "")
            task_id = str(entry.get("task") or "")
            task_name = self._task_name_for_id(task_id)
            header_text = group_name if is_group else (task_name or task_id)
            header_tip = f"{group_name} group" if is_group else self._task_display_label(task_id)
            try:
                _set_section_resize_mode(self._table.verticalHeader(), row, fixed_mode)
            except Exception:
                pass
            self._table.setRowHeight(row, _GANTT_TASK_ROW_HEIGHT)
            header_item = self._table.verticalHeaderItem(row)
            if header_item is None:
                header_item = QtWidgets.QTableWidgetItem(header_text)
                self._table.setVerticalHeaderItem(row, header_item)
            header_item.setText(header_text)
            header_item.setToolTip(header_tip)
            for day in range(day_count):
                cell = self._ensure_item(row, day)
                cell.setText("")
                cell.setTextAlignment(int(QtCore.Qt.AlignCenter))
                visible_date = self._visible_dates[day]
                if is_group:
                    cell.setToolTip(f"{group_name} group")
                else:
                    cell.setToolTip(f"{self._task_display_label(task_id)}: {visible_date.isoformat()}")
        self._table.blockSignals(False)
        self._ignore_header_move = False
        self._table.set_today_column(today_column)
        self._month_strip.set_dates(self._visible_dates, self._today)
        self._apply_task_panel_width(self._task_panel_width, persist=False)
        self._update_corner_button_geometry()
        self._apply_table_styles()
        self._update_today_marker()

    def _apply_table_styles(self):
        label_bg = QtGui.QColor("#141b24")
        label_fg = QtGui.QColor("#dbe4ee")
        selected_label_bg = QtGui.QColor("#1d4f74")
        selected_label_fg = QtGui.QColor("#f8fafc")
        group_label_bg = QtGui.QColor("#1a2735")
        group_label_fg = QtGui.QColor("#e2e8f0")
        cell_bg = QtGui.QColor("#10161d")
        weekend_cell_bg = QtGui.QColor("#0c1015")
        selected_row_bg = QtGui.QColor("#16212b")
        weekend_selected_row_bg = QtGui.QColor("#121b23")
        group_cell_bg = QtGui.QColor("#131d28")
        assigned_bg = QtGui.QColor("#60a5fa")
        completed_assigned_bg = QtGui.QColor("#16a34a")
        assigned_fg = QtGui.QColor("#ecfeff")
        selected_row = self._row_for_task(self._selected_task)
        assignment_overlays: dict[int, tuple[int, int, QtGui.QColor]] = {}
        progress_overlays: dict[int, tuple[int, int, int]] = {}
        progress_sections: dict[int, int] = {}
        completed_sections: set[int] = set()
        notification_sections: set[int] = set()
        group_sections: dict[int, bool] = {}
        child_sections: set[int] = set()

        for row, entry in enumerate(self._row_entries):
            is_group = entry.get("kind") == "group"
            group_name = str(entry.get("group") or "")
            task = str(entry.get("task") or "")
            task_name = self._task_name_for_id(task)
            header_text = group_name if is_group else (task_name or task)
            is_selected = bool(task and task == self._selected_task)
            header_item = self._table.verticalHeaderItem(row)
            if header_item is None:
                header_item = QtWidgets.QTableWidgetItem(header_text)
                self._table.setVerticalHeaderItem(row, header_item)
            font = header_item.font()
            if is_group:
                group_sections[row] = bool(self._group_expanded.get(group_name, True))
                header_item.setBackground(group_label_bg)
                header_item.setForeground(group_label_fg)
                font.setBold(True)
                for day in range(self._visible_day_count()):
                    item = self._ensure_item(row, day)
                    item.setBackground(group_cell_bg)
                    item.setForeground(group_label_fg)
                header_item.setFont(font)
                continue

            child_sections.add(row)
            header_item.setBackground(selected_label_bg if is_selected else label_bg)
            header_item.setForeground(selected_label_fg if is_selected else label_fg)
            font.setBold(bool(is_selected))
            header_item.setFont(font)

            progress_value = int(self._task_progress.get(task, 0))
            progress_sections[row] = progress_value
            if task in self._completed_tasks:
                completed_sections.add(row)
            if task in self._task_notifications:
                notification_sections.add(row)

            assigned_span = self._visible_assignment_span(task)
            if self._assignment_resize_preview is not None and self._assignment_resize_preview[0] == task:
                assigned_span = (
                    int(self._assignment_resize_preview[1]),
                    int(self._assignment_resize_preview[2]),
                )
            for day in range(self._visible_day_count()):
                item = self._ensure_item(row, day)
                is_weekend = False
                try:
                    is_weekend = self._visible_dates[day].weekday() >= 5
                except Exception:
                    is_weekend = False
                base_bg = (
                    weekend_selected_row_bg if (is_selected and is_weekend)
                    else selected_row_bg if is_selected
                    else weekend_cell_bg if is_weekend
                    else cell_bg
                )
                item.setBackground(base_bg)
                item.setForeground(label_fg)
                if assigned_span is not None and assigned_span[0] <= day <= assigned_span[1]:
                    if task in self._completed_tasks or progress_value >= 100:
                        assignment_overlays[row] = (assigned_span[0], assigned_span[1], completed_assigned_bg)
                    else:
                        assignment_overlays[row] = (assigned_span[0], assigned_span[1], assigned_bg)
                        if progress_value > 0:
                            progress_overlays[row] = (assigned_span[0], assigned_span[1], progress_value)
                    item.setForeground(assigned_fg)
        header = self._table.verticalHeader()
        if hasattr(header, "set_selected_section"):
            try:
                header.set_selected_section(selected_row)
            except Exception:
                pass
        if hasattr(header, "set_progress_values"):
            try:
                header.set_progress_values(progress_sections)
            except Exception:
                pass
        if hasattr(header, "set_completed_sections"):
            try:
                header.set_completed_sections(completed_sections)
            except Exception:
                pass
        if hasattr(header, "set_notification_sections"):
            try:
                header.set_notification_sections(notification_sections)
            except Exception:
                pass
        if hasattr(header, "set_group_sections"):
            try:
                header.set_group_sections(group_sections, child_sections)
            except Exception:
                pass
        self._table.set_assignment_overlays(assignment_overlays)
        self._table.set_progress_overlays(progress_overlays)

    def _on_cell_clicked(self, row: int, col: int):
        task = self._task_for_row(row)
        if not task:
            return
        self._selected_task = task
        assigned_span = self._visible_assignment_span(task)
        if assigned_span is not None and assigned_span[0] <= col <= assigned_span[1]:
            self._apply_table_styles()
            self._update_labels()
            return
        assignment_dates = self._assignment_dates_for_task(task)
        if assignment_dates is not None:
            start_date, end_date = assignment_dates
            span_days = max(0, int((end_date - start_date).days))
            new_start = self._visible_date_for_day(col + 1)
            self._set_task_day_range(task, new_start, new_start + timedelta(days=span_days), notify_scene=True)
            return
        self._set_task_day(task, col + 1)

    def _on_cell_double_clicked(self, row: int, _col: int):
        task = self._task_for_row(row)
        if not task:
            return
        self._selected_task = task
        self._apply_table_styles()
        self._update_labels()
        task_name = self._task_name_for_id(task)
        src_item, src_model = self._source_ref_for_task(task)
        if src_model is None or not task_name:
            QtWidgets.QMessageBox.information(
                _dialog_parent_for_node(self._node_item),
                "Edit Task Value",
                "Connect a Note node to edit task values from the Gantt chart.",
            )
            return
        initial_text = _read_source_task_value(src_model, task_name)
        dlg = BigTextEditDialog(
            _dialog_parent_for_node(self._node_item),
            title=f"Edit Task Value: {self._task_display_label(task)}",
            initial=initial_text,
        )
        try:
            dlg.edit.setPlaceholderText("Enter the note value for this task.")
        except Exception:
            pass
        try:
            result = dlg.exec()
        except Exception:
            result = dlg.exec_()
        if result != QtWidgets.QDialog.Accepted:
            return
        new_text = dlg.text()
        if new_text == initial_text:
            return
        _write_source_task_value(
            self._node_item,
            src_item,
            src_model,
            task_name,
            new_text,
            notify_scene=True,
        )

    def _on_header_clicked(self, section: int):
        if section < 0 or section >= self._visible_day_count():
            return
        if self._selected_task:
            self._selection_label.setText("Click a day cell in the selected task row to place it.")
        else:
            self._selection_label.setText("Click a task cell to place that task on a day.")

    def _on_task_header_clicked(self, section: int):
        task = self._task_for_row(section)
        if not task:
            return
        self._selected_task = task
        self._apply_table_styles()
        self._update_labels()

    def _on_task_header_rename_requested(self, section: int):
        task_id = self._task_for_row(section)
        if not task_id:
            return
        old_name = self._task_name_for_id(task_id)
        source_name = self._task_group_name(task_id)
        src_item, src_model = self._source_ref_for_task(task_id)
        if src_item is None or src_model is None:
            QtWidgets.QMessageBox.information(
                _dialog_parent_for_node(self._node_item),
                "Rename Task",
                "Connect a Note node to rename tasks from the Gantt chart.",
            )
            return
        rename_fn = getattr(src_item, "_apply_param_rename", None)
        if not callable(rename_fn):
            return
        param_index = -1
        old_key = old_name.strip().lower()
        for idx, entry in enumerate(getattr(src_model, "params", None) or []):
            if (entry.get("name") or "").strip().lower() == old_key:
                param_index = idx
                break
        if param_index < 0:
            return
        parent = _dialog_parent_for_node(self._node_item)
        text, ok = QtWidgets.QInputDialog.getText(
            parent, "Rename Parameter", "New name:", QtWidgets.QLineEdit.Normal, old_name
        )
        if not ok:
            return
        desired_name = (text or "").strip()
        if not desired_name:
            return
        final_name = str(rename_fn(param_index, desired_name) or "").strip()
        if not final_name:
            return
        old_task_id = task_id
        new_task_id = _task_storage_key(source_name, final_name)
        assignments, assignments_changed = _rename_task_key(self._assignments, old_task_id, new_task_id)
        progress_map, progress_changed = _rename_task_key(self._task_progress, old_task_id, new_task_id)
        notifications, notifications_changed = _rename_task_key(self._task_notifications, old_task_id, new_task_id)
        completed = set(self._completed_tasks)
        completed_changed = False
        if old_task_id != new_task_id and old_task_id in completed:
            completed.discard(old_task_id)
            completed.add(new_task_id)
            completed_changed = True
        if assignments_changed:
            self._assignments = assignments
            _write_assignments(self._node_item, assignments, notify_scene=False)
        if progress_changed:
            self._task_progress = progress_map
            _write_progress_map(self._node_item, progress_map, notify_scene=False)
        if notifications_changed:
            self._task_notifications = notifications
            _write_notifications(self._node_item, notifications, notify_scene=False)
        if completed_changed:
            self._completed_tasks = completed
        if self._selected_task == old_task_id:
            self._selected_task = new_task_id
        self._schedule_sync()

    def _on_task_completion_toggled(self, section: int, checked: bool):
        task = self._task_for_row(section)
        if not task:
            return
        progress_map = dict(self._task_progress)
        progress_map[task] = 100 if checked else 0
        self._task_progress = progress_map
        _write_progress_map(self._node_item, progress_map, notify_scene=False)
        completed = set(self._completed_tasks)
        if checked:
            completed.add(task)
        else:
            completed.discard(task)
        if not self._set_source_task_completed(task, checked, notify_scene=True):
            return
        self._completed_tasks = completed
        self._apply_table_styles()
        self._update_labels()

    def _set_task_progress(self, task: str, progress: int):
        if not task:
            return
        progress_value = _coerce_progress_value(progress)
        if progress_value is None:
            return
        progress_map = dict(self._task_progress)
        progress_map[task] = progress_value
        self._task_progress = progress_map
        _write_progress_map(self._node_item, progress_map, notify_scene=True)

        has_source = self._task_group_name(task) in self._source_info_by_name
        if progress_value >= 100:
            completed = set(self._completed_tasks)
            completed.add(task)
            if has_source and not self._set_source_task_completed(task, True, notify_scene=True):
                return
            self._completed_tasks = completed
        else:
            completed = set(self._completed_tasks)
            completed.discard(task)
            if has_source and not self._set_source_task_completed(task, False, notify_scene=True):
                return
            self._completed_tasks = completed
        self._apply_table_styles()
        self._update_labels()

    def _set_task_day_range(self, task: str, start_date: date, end_date: date, *, notify_scene: bool = True):
        if not task:
            return
        self._assignment_resize_preview = None
        mapping = dict(self._assignments)
        mapping[task] = _normalize_assignment_entry(start_date.isoformat(), end_date.isoformat())
        self._assignments = mapping
        _write_assignments(self._node_item, mapping, notify_scene=notify_scene)
        self._apply_table_styles()
        self._update_labels()

    def _set_task_day(self, task: str, day: int):
        if not task or not (1 <= int(day) <= self._visible_day_count()):
            return
        assigned_date = self._visible_date_for_day(day)
        self._set_task_day_range(task, assigned_date, assigned_date, notify_scene=True)

    def _remove_task_day_at(self, row: int, col: int) -> bool:
        task = self._task_for_row(row)
        if not task:
            return False
        assigned_span = self._visible_assignment_span(task)
        if assigned_span is None or not (assigned_span[0] <= col <= assigned_span[1]):
            return False
        mapping = dict(self._assignments)
        mapping.pop(task, None)
        self._assignment_resize_preview = None
        self._assignments = mapping
        self._selected_task = task
        _write_assignments(self._node_item, mapping, notify_scene=True)
        self._apply_table_styles()
        self._update_labels()
        return True

    def _on_assignment_resize_moved(self, row: int, col: int, edge: int):
        task = self._task_for_row(row)
        if not task:
            return
        assigned_span = self._visible_assignment_span(task)
        if assigned_span is None:
            return
        self._selected_task = task
        start_col, end_col = int(assigned_span[0]), int(assigned_span[1])
        if int(edge) < 0:
            start_col = max(0, min(int(col), end_col))
        else:
            end_col = max(start_col, int(col))
        self._assignment_resize_preview = (task, start_col, end_col)
        self._apply_table_styles()
        self._update_labels()

    def _on_assignment_resize_finished(self, row: int, col: int, edge: int):
        task = self._task_for_row(row)
        if not task:
            self._assignment_resize_preview = None
            self._apply_table_styles()
            return
        dates = self._assignment_dates_for_task(task)
        assigned_span = self._visible_assignment_span(task)
        if dates is None or assigned_span is None:
            self._assignment_resize_preview = None
            self._apply_table_styles()
            return
        start_col, end_col = int(assigned_span[0]), int(assigned_span[1])
        target_col = max(0, min(int(col), max(0, len(self._visible_dates) - 1)))
        if int(edge) < 0:
            target_start_col = min(target_col, end_col)
            target_end_col = end_col
        else:
            target_start_col = start_col
            target_end_col = max(start_col, target_col)
        self._assignment_resize_preview = None
        self._selected_task = task
        target_start_date = self._visible_dates[target_start_col]
        target_end_date = self._visible_dates[target_end_col]
        self._set_task_day_range(task, target_start_date, target_end_date, notify_scene=True)

    def _on_assignment_move_moved(self, row: int, start_col: int, end_col: int):
        task = self._task_for_row(row)
        if not task:
            return
        self._selected_task = task
        self._assignment_resize_preview = (task, int(start_col), int(end_col))
        self._apply_table_styles()
        self._update_labels()

    def _on_assignment_move_finished(self, row: int, start_col: int, end_col: int):
        task = self._task_for_row(row)
        if not task:
            self._assignment_resize_preview = None
            self._apply_table_styles()
            return
        assigned_span = self._visible_assignment_span(task)
        target_start_col = max(0, min(int(start_col), max(0, len(self._visible_dates) - 1)))
        target_end_col = max(target_start_col, min(int(end_col), max(0, len(self._visible_dates) - 1)))
        self._assignment_resize_preview = None
        self._selected_task = task
        if assigned_span is not None and assigned_span == (target_start_col, target_end_col):
            self._apply_table_styles()
            self._update_labels()
            return
        self._set_task_day_range(
            task,
            self._visible_dates[target_start_col],
            self._visible_dates[target_end_col],
            notify_scene=True,
        )

    def _handle_graph_view_short_right_click(self, global_pos) -> bool:
        try:
            viewport_pos = self._table.viewport().mapFromGlobal(global_pos)
        except Exception:
            return False
        if not self._table.viewport().rect().contains(viewport_pos):
            return False
        try:
            index = self._table.indexAt(viewport_pos)
        except Exception:
            index = QtCore.QModelIndex()
        if not index.isValid():
            return False
        return self._remove_task_day_at(int(index.row()), int(index.column()))

    def _handle_graph_view_short_right_click_local(self, local_pos) -> bool:
        try:
            widget_pos = QtCore.QPoint(local_pos)
        except Exception:
            try:
                widget_pos = QtCore.QPoint(int(local_pos.x()), int(local_pos.y()))
            except Exception:
                return False
        try:
            viewport_pos = self._table.viewport().mapFrom(self, widget_pos)
        except Exception:
            return False
        if not self._table.viewport().rect().contains(viewport_pos):
            return False
        try:
            index = self._table.indexAt(viewport_pos)
        except Exception:
            index = QtCore.QModelIndex()
        if not index.isValid():
            return False
        return self._remove_task_day_at(int(index.row()), int(index.column()))

    def _on_table_short_right_click(self, row: int, col: int):
        self._remove_task_day_at(row, col)

    def _on_day_scroll_changed(self, value: int):
        if self._day_scroll_sync:
            return
        visible_start = self._base_start_date + timedelta(days=_day_offset_for_scroll_value(value))
        self._set_visible_start_date(visible_start, sync_scroll=False, persist=True)

    def _on_task_reorder_requested(self, start_visual_index: int, drop_indicator_index: int):
        if self._ignore_header_move:
            return
        if not self._task_names:
            return
        try:
            start_row = int(start_visual_index)
            drop_index = int(drop_indicator_index)
        except Exception:
            return
        task_id = self._task_for_row(start_row)
        if not task_id:
            return
        source_name = self._task_group_name(task_id)
        source_info = self._source_info_by_name.get(source_name) or {}
        src_item = source_info.get("item")
        src_model = source_info.get("model")
        if src_item is None or src_model is None:
            return
        source_task_ids = list(self._group_tasks.get(source_name) or [])
        if len(source_task_ids) <= 1:
            return
        visible_group_rows = [
            row
            for row, entry in enumerate(self._row_entries)
            if entry.get("kind") == "task" and str(entry.get("group") or "") == source_name
        ]
        if not visible_group_rows:
            return
        min_boundary = int(visible_group_rows[0])
        max_boundary = int(visible_group_rows[-1]) + 1
        if drop_index < min_boundary or drop_index > max_boundary:
            return
        try:
            start_index = source_task_ids.index(task_id)
        except ValueError:
            return
        insert_index = 0
        for row in visible_group_rows:
            if int(row) < drop_index:
                insert_index += 1
        ordered_ids = list(source_task_ids)
        moved_task = ordered_ids.pop(start_index)
        if insert_index > start_index:
            insert_index -= 1
        insert_index = max(0, min(len(ordered_ids), int(insert_index)))
        ordered_ids.insert(insert_index, moved_task)
        if ordered_ids == source_task_ids:
            return
        ordered_names = [self._task_name_for_id(task_key) for task_key in ordered_ids if self._task_name_for_id(task_key)]
        if not ordered_names:
            return
        if _reorder_note_params(self._node_item, src_item, src_model, ordered_names):
            self._schedule_sync()


def render_node_body(node_item, y_cursor: int) -> int:
    body = GanttChartWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    min_h = max(
        int(body.sizeHint().height()),
        int(body.minimumSizeHint().height()),
        int(body.minimumHeight() or 0),
    )
    try:
        available_h = int(
            max(
                float(min_h),
                float(node_item.height) - float(y_cursor) - float(getattr(node_item, "_PADDING", 0.0)),
            )
        )
    except Exception:
        available_h = int(min_h)
    proxy.resize(node_item.width, available_h)
    try:
        proxy.setPreferredSize(node_item.width, available_h)
    except Exception:
        pass
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + available_h


GANTT_CHART_SPEC = Spec(
    stripe_color="#22c55e",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
