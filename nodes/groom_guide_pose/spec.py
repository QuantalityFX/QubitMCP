from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Optional

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec
from echograph.rigging.fbx_stage7_timeline import clip_sample_time_from_timeline_seconds
from echograph.rigging.groom_deform import deform_groom_curves
from echograph.rigging.xpbd_strand import (
    XPBDStrandConfig,
    XPBDStrandError,
    build_strand_runtime,
    curves_to_line_points,
    reset_strand_runtime,
    root_indices_for_curves,
    step_strand_runtime,
)

try:
    import moderngl
except Exception:
    moderngl = None

try:
    import numpy as np
except Exception:
    np = None

try:
    from echograph.rigging.xpbd_strand_gpu import XPBDStrandGPUBackend
except Exception:
    XPBDStrandGPUBackend = None  # type: ignore


_GUIDE_POSE_GPU_DISABLED = False


KIND_ALIASES = {
    "groom_guide_pose",
    "groom guide pose",
    "groom_guides_pose",
    "groom guides pose",
    "guide_pose",
    "guide pose",
    "hair_guide_pose",
    "hair guide pose",
    "hair_pose",
    "hair pose",
}

GUIDE_KIND_ALIASES = {"groom_guides", "groom guides", "hair_guides", "hair guides"}
GROOM_DEFORM_KIND_ALIASES = {
    "groom_deform",
    "groom deform",
    "groomdeform",
    "hair_deform",
    "hair deform",
}

GROOM_GUIDE_POSE_NODE_W = 292
GROOM_GUIDE_POSE_BODY_H = 264

HIDDEN_PARAMS = {
    "guides",
    "source",
    "path",
    "guides_path",
    "pose_cache_path",
    "enabled",
    "start_frame",
    "settle_seconds",
    "warmup_frames",
    "fps",
    "substeps",
    "iterations",
    "stretch",
    "bend",
    "damping",
    "gravity_y",
    "wind_x",
    "wind_y",
    "wind_z",
    "scene_units_per_meter",
    "root_pin",
    "max_velocity",
    "device",
    "debug_log",
}


@dataclass(frozen=True)
class GroomGuidePoseBuildOutcome:
    asset: Optional[dict[str, Any]]
    status: str
    detail: str
    debug: Optional[dict[str, Any]] = None


def _param_value(model, name: str, default: str = "") -> str:
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value", default) or default)
    return default


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = _param_value(model, name, "1" if default else "0").strip().lower()
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _param_int(model, name: str, default: int, *, min_value: int, max_value: int) -> int:
    try:
        value = int(round(float(_param_value(model, name, str(default)).strip())))
    except Exception:
        value = int(default)
    return max(int(min_value), min(int(max_value), int(value)))


def _param_float(model, name: str, default: float, *, min_value: float, max_value: float) -> float:
    try:
        value = float(_param_value(model, name, str(default)).strip())
    except Exception:
        value = float(default)
    return max(float(min_value), min(float(max_value), float(value)))


def _has_param(model, name: str) -> bool:
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return True
    return False


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        try:
            params = list(params)
            setattr(model, "params", params)
        except Exception:
            return
    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _set_param(node_item, name: str, value: str, *, notify_scene: bool = True) -> bool:
    setter = getattr(node_item, "_set_param_value", None)
    if callable(setter):
        try:
            setter(name, str(value), rebuild=False, notify_scene=bool(notify_scene))
            return True
        except Exception:
            pass
    _ensure_param(node_item, name, str(value))
    model = getattr(node_item, "model", None)
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value)
            return True
    return False


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    entry = None
    for param in params:
        if isinstance(param, dict) and (param.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = param
            break
    if entry is None:
        entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(entry)
    hidden = {part.strip().lower() for part in str(entry.get("value") or "").split(",") if part.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    try:
        setattr(model, "params", params)
    except Exception:
        pass


def _edge_dst_name(edge) -> str:
    return str(
        getattr(edge, "dst_port_name", None)
        or getattr(edge, "dst_label", None)
        or getattr(edge, "dst_name", None)
        or ""
    ).strip()


def _ordered_in_edges(scene, item) -> list:
    if scene is None or item is None:
        return []
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _connected_input_item(node_item, names: set[str], kind_fallbacks: Optional[set[str]] = None):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    edges = _ordered_in_edges(scene, node_item)
    for edge in edges:
        if _edge_dst_name(edge).lower() in names:
            return getattr(edge, "src", None)
    if kind_fallbacks:
        allowed = {str(kind).strip().lower() for kind in kind_fallbacks if str(kind).strip()}
        for edge in edges:
            src = getattr(edge, "src", None)
            if _node_kind(src) in allowed:
                return src
    return getattr(edges[0], "src", None) if edges else None


def _node_kind(item) -> str:
    return str(getattr(getattr(item, "model", None), "kind", "") or "").strip().lower()


def _node_name(item) -> str:
    return str(getattr(getattr(item, "model", None), "name", "") or "").strip()


def _guides_asset_from_item(item) -> tuple[Optional[dict[str, Any]], str]:
    if item is None:
        return None, "Connect Groom Deform to the guides input."
    kind = _node_kind(item)
    if kind in KIND_ALIASES:
        outcome = build_groom_guide_pose_scene_asset(item)
        return (dict(outcome.asset), "") if isinstance(outcome.asset, dict) else (None, outcome.detail)
    if kind in GROOM_DEFORM_KIND_ALIASES:
        try:
            from nodes.groom_deform import spec as deform_spec  # type: ignore

            build = getattr(deform_spec, "build_groom_deform_scene_asset", None)
            outcome = build(item) if callable(build) else None
        except Exception as exc:
            return None, f"Groom Deform build failed: {exc}"
        asset = getattr(outcome, "asset", None)
        detail = str(getattr(outcome, "detail", "") or "")
        return (dict(asset), "") if isinstance(asset, dict) else (None, detail or "Groom Deform did not produce guides.")
    if kind in GUIDE_KIND_ALIASES:
        try:
            from nodes.groom_guides import spec as guides_spec  # type: ignore

            build = getattr(guides_spec, "build_groom_guides_scene_asset", None)
            outcome = build(item) if callable(build) else None
        except Exception as exc:
            return None, f"Groom Guides build failed: {exc}"
        asset = getattr(outcome, "asset", None)
        detail = str(getattr(outcome, "detail", "") or "")
        return (dict(asset), "") if isinstance(asset, dict) else (None, detail or "Groom Guides did not produce curves.")
    return None, f"Unsupported guides input: {kind or '<none>'}."


def _sanitize_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    safe = re.sub(r"_+", "_", safe)
    return safe.strip("._") or "groom_guide_pose"


def _workflow_path_from_node(node_item) -> Path | None:
    def _coerce(raw) -> Path | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            return Path(text).expanduser().resolve()
        except Exception:
            return None

    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            for view in scene.views() or []:
                for owner in (view.window(), view):
                    path = _coerce(getattr(owner, "_current_path", None))
                    if path is not None:
                        return path
        except Exception:
            pass
    try:
        app = QtWidgets.QApplication.instance()
        windows = list(app.topLevelWidgets() or []) if app is not None else []
    except Exception:
        windows = []
    for win in windows:
        path = _coerce(getattr(win, "_current_path", None))
        if path is not None:
            return path
    return None


def _project_cache_dir(node_item) -> Path:
    workflow_path = _workflow_path_from_node(node_item)
    if workflow_path is not None:
        stem = _sanitize_name(workflow_path.stem or "workflow")
        out = workflow_path.parent / f"{stem}_groom_guide_pose_cache"
    else:
        out = Path.cwd() / "groom_guide_pose_cache"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _cache_param_value(node_item, path: Path) -> str:
    workflow_path = _workflow_path_from_node(node_item)
    if workflow_path is not None:
        try:
            rel = path.resolve().relative_to(workflow_path.parent.resolve())
            return rel.as_posix()
        except Exception:
            pass
    return str(path)


def _resolve_cache_path(node_item, raw_path: str) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    try:
        path = Path(text).expanduser()
    except Exception:
        return None
    if not path.is_absolute():
        workflow_path = _workflow_path_from_node(node_item)
        base = workflow_path.parent if workflow_path is not None else Path.cwd()
        path = base / path
    try:
        return path.resolve()
    except Exception:
        return path


def _cache_path_from_params(node_item) -> Path | None:
    model = getattr(node_item, "model", None)
    for name in ("pose_cache_path", "path", "guides_path"):
        path = _resolve_cache_path(node_item, _param_value(model, name, ""))
        if path is not None:
            return path
    return None


def _copy_curves(curves) -> list[list[list[float]]]:
    out: list[list[list[float]]] = []
    for curve in curves or []:
        rows: list[list[float]] = []
        if isinstance(curve, (list, tuple)):
            for point in curve:
                try:
                    seq = list(point)
                    if len(seq) >= 3:
                        rows.append([float(seq[0]), float(seq[1]), float(seq[2])])
                except Exception:
                    continue
        out.append(rows)
    return out


def _curves_from_points_like(points, template_curves) -> list[list[list[float]]]:
    if np is None:
        return _copy_curves(template_curves)
    try:
        arr = np.asarray(points, dtype="f4").reshape(-1, 3)
    except Exception:
        return _copy_curves(template_curves)
    out: list[list[list[float]]] = []
    cursor = 0
    for curve in template_curves or []:
        count = len(curve) if isinstance(curve, (list, tuple)) else 0
        if count <= 0:
            out.append([])
            continue
        rows = arr[cursor : cursor + count]
        out.append([[float(row[0]), float(row[1]), float(row[2])] for row in rows])
        cursor += count
    return out


def _points_list(points) -> list[list[float]]:
    rows: list[list[float]] = []
    try:
        seq = np.asarray(points, dtype="f4").reshape(-1, 3) if np is not None else list(points or [])
    except Exception:
        seq = []
    for row in seq:
        try:
            rows.append([float(row[0]), float(row[1]), float(row[2])])
        except Exception:
            continue
    return rows


def _settings_from_model(model) -> dict[str, Any]:
    fps = _param_float(model, "fps", 24.0, min_value=1.0, max_value=240.0)
    warmup_frames = _param_int(model, "warmup_frames", 36, min_value=0, max_value=10000)
    if _has_param(model, "settle_seconds"):
        settle_seconds = _param_float(model, "settle_seconds", warmup_frames / max(1.0e-6, fps), min_value=0.0, max_value=600.0)
        warmup_frames = max(0, min(10000, int(round(float(settle_seconds) * float(fps)))))
    else:
        settle_seconds = max(0.0, min(600.0, float(warmup_frames) / max(1.0e-6, float(fps))))
    device = (_param_value(model, "device", "auto").strip().lower() or "auto")
    if device in {"cpu", "python"}:
        device = "auto"
    return {
        "enabled": _param_bool(model, "enabled", True),
        "start_frame": _param_int(model, "start_frame", 0, min_value=0, max_value=100000),
        "settle_seconds": float(settle_seconds),
        "warmup_frames": int(warmup_frames),
        "fps": float(fps),
        "substeps": _param_int(model, "substeps", 4, min_value=1, max_value=64),
        "iterations": _param_int(model, "iterations", 8, min_value=0, max_value=128),
        "stretch": _param_float(model, "stretch", 1.0, min_value=0.0, max_value=1.0),
        "bend": _param_float(model, "bend", 0.35, min_value=0.0, max_value=1.0),
        "damping": _param_float(model, "damping", 0.04, min_value=0.0, max_value=0.999),
        "gravity_y": _param_float(model, "gravity_y", -9.81, min_value=-100.0, max_value=100.0),
        "wind_x": _param_float(model, "wind_x", 0.0, min_value=-100.0, max_value=100.0),
        "wind_y": _param_float(model, "wind_y", 0.0, min_value=-100.0, max_value=100.0),
        "wind_z": _param_float(model, "wind_z", 0.0, min_value=-100.0, max_value=100.0),
        "scene_units_per_meter": _param_float(model, "scene_units_per_meter", 100.0, min_value=0.001, max_value=100000.0),
        "root_pin": _param_float(model, "root_pin", 1.0, min_value=0.0, max_value=1.0),
        "max_velocity": _param_float(model, "max_velocity", 250.0, min_value=0.0, max_value=100000.0),
        "device": device,
        "debug_log": _param_bool(model, "debug_log", False),
    }


def _config_from_settings(settings: dict[str, Any]) -> XPBDStrandConfig:
    scene_units_per_meter = max(0.001, float(settings.get("scene_units_per_meter", 100.0) or 100.0))
    return XPBDStrandConfig(
        fps=float(settings.get("fps", 24.0) or 24.0),
        frame_count=int(settings.get("warmup_frames", 36) or 36),
        substeps=int(settings.get("substeps", 4) or 4),
        iterations=int(settings.get("iterations", 8) or 8),
        gravity=(0.0, float(settings.get("gravity_y", -9.81) or -9.81) * scene_units_per_meter, 0.0),
        wind=(
            float(settings.get("wind_x", 0.0) or 0.0) * scene_units_per_meter,
            float(settings.get("wind_y", 0.0) or 0.0) * scene_units_per_meter,
            float(settings.get("wind_z", 0.0) or 0.0) * scene_units_per_meter,
        ),
        damping=float(settings.get("damping", 0.04) or 0.04),
        stretch_stiffness=float(settings.get("stretch", 1.0) or 0.0),
        bend_stiffness=float(settings.get("bend", 0.35) or 0.0),
        root_pin_stiffness=float(settings.get("root_pin", 1.0) or 0.0),
        max_velocity=float(settings.get("max_velocity", 250.0) or 250.0) * scene_units_per_meter,
        store_frames=False,
    )


def _device_from_settings(settings: dict[str, Any]) -> str:
    raw = str(settings.get("device", "auto") or "auto").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    if raw in {"cpu", "python"}:
        return "cpu"
    if raw in {"gpu", "graphics", "graphics_card", "opengl", "compute"}:
        return "gpu"
    return "auto"


def _guide_pose_gpu_backend():
    global _GUIDE_POSE_GPU_DISABLED
    if _GUIDE_POSE_GPU_DISABLED:
        return None
    if moderngl is None or XPBDStrandGPUBackend is None or np is None:
        _GUIDE_POSE_GPU_DISABLED = True
        return None
    ctx = None
    try:
        try:
            ctx = moderngl.create_standalone_context(require=430)
        except TypeError:
            ctx = moderngl.create_standalone_context()
        backend = XPBDStrandGPUBackend(ctx)
    except Exception:
        try:
            if ctx is not None and hasattr(ctx, "release"):
                ctx.release()
        except Exception:
            pass
        _GUIDE_POSE_GPU_DISABLED = True
        return None
    return ctx, backend


def _current_qt_gl_context():
    try:
        context = QtGui.QOpenGLContext.currentContext()
        surface = context.surface() if context is not None and hasattr(context, "surface") else None
        return context, surface
    except Exception:
        return None, None


def _restore_qt_gl_context(context, surface) -> None:
    if context is None or surface is None:
        return
    try:
        context.makeCurrent(surface)
    except Exception:
        pass


def _resolve_gl_view(node_item):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return None
    try:
        views = scene.views()
        if not views:
            return None
        win = views[0].window()
    except Exception:
        return None
    return getattr(win, "gl_view", None) if win is not None else None


def _timeline_fps_from_node(node_item, settings: dict[str, Any], clip) -> float:
    glv = _resolve_gl_view(node_item)
    if glv is not None:
        fn = getattr(glv, "_mgl_timeline_fps_value", None)
        if callable(fn):
            try:
                value = float(fn())
                if math.isfinite(value) and value > 1.0e-6:
                    return value
            except Exception:
                pass
        try:
            value = float(getattr(glv, "_timeline_fps", 0.0) or 0.0)
            if math.isfinite(value) and value > 1.0e-6:
                return value
        except Exception:
            pass
    try:
        value = float(settings.get("fps", 0.0) or 0.0)
        if math.isfinite(value) and value > 1.0e-6:
            return value
    except Exception:
        pass
    try:
        value = float(getattr(clip, "sample_rate_hz", 0.0) or 0.0)
        if math.isfinite(value) and value > 1.0e-6:
            return value
    except Exception:
        pass
    return 24.0


def _append_unique_text(out: list[str], value) -> None:
    text = str(value or "").strip()
    if not text:
        return
    key = text.lower()
    if key in {entry.lower() for entry in out}:
        return
    out.append(text)


def _pose_sample_owner(node_item, guides_asset: dict[str, Any], deform_cfg: dict[str, Any], requested_frame: int) -> str:
    candidates: list[str] = []
    for source in (deform_cfg, guides_asset):
        if not isinstance(source, dict):
            continue
        for key in ("sample_owner", "deformer_owner", "source_owner"):
            _append_unique_text(candidates, source.get(key))
    for source in (deform_cfg, guides_asset):
        if not isinstance(source, dict):
            continue
        for value in list(source.get("sample_owner_candidates") or []):
            _append_unique_text(candidates, value)
    for source in (deform_cfg, guides_asset):
        if not isinstance(source, dict):
            continue
        for key in ("rig_owner", "animation_owner", "retarget_owner"):
            _append_unique_text(candidates, source.get(key))
    _append_unique_text(candidates, _node_name(node_item))
    if not candidates:
        return ""
    return candidates[0]


def _sample_seconds_from_settings(
    settings: dict[str, Any],
    rig_context: dict[str, Any] | None,
    *,
    node_item=None,
    sample_owner: str = "",
) -> tuple[float, dict[str, Any]]:
    clip = rig_context.get("clip") if isinstance(rig_context, dict) else None
    try:
        requested_frame = int(round(float(settings.get("start_frame", 0) or 0)))
    except Exception:
        requested_frame = 0
    requested_frame = max(0, int(requested_frame))
    fps = _timeline_fps_from_node(node_item, settings, clip)
    source_frame = float(requested_frame)
    owner = str(sample_owner or "").strip()
    timeline_seconds = float(source_frame) / max(1.0e-6, float(fps))
    return (
        float(clip_sample_time_from_timeline_seconds(clip, timeline_seconds)),
        {
            "pose_requested_frame": int(requested_frame),
            "pose_source_frame": float(source_frame),
            "pose_sample_owner": owner,
            "pose_sample_fps": float(fps),
            "pose_sample_method": "literal_pose_frame",
        },
    )


def _output_path(node_item, source_asset: dict[str, Any], settings: dict[str, Any]) -> Path:
    model = getattr(node_item, "model", None)
    node_name = _sanitize_name(str(getattr(model, "name", "") or "groom_guide_pose"))
    source_key = str(source_asset.get("guides_path") or source_asset.get("path") or source_asset.get("node") or "")
    signature = json.dumps(
        {
            "source": source_key,
            "guide_count": int(source_asset.get("guide_count", 0) or 0),
            "points_per_curve": int(source_asset.get("points_per_curve", 0) or 0),
            "settings": settings,
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha1(signature.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return _project_cache_dir(node_item) / f"{node_name}_{digest}.qgpose.json"


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    except Exception:
        pass


def _read_pose_cache(path: Path | None) -> tuple[Optional[dict[str, Any]], str]:
    if path is None:
        return None, "No pose cache path."
    try:
        if not path.exists() or not path.is_file():
            return None, f"Pose cache not found: {path}"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"Pose cache read failed: {exc}"
    if not isinstance(payload, dict):
        return None, "Pose cache is not a JSON object."
    schema = str(payload.get("schema") or "").strip()
    if schema and schema != "qubit.groom_guide_pose.v2":
        return None, f"Unsupported pose cache schema: {schema}"
    pose_curves = payload.get("pose_curves") or payload.get("curves")
    if not isinstance(pose_curves, list) or not pose_curves:
        return None, "Pose cache has no posed curves."
    return payload, ""


def _curves_from_source_asset(asset: dict[str, Any]) -> list[list[list[float]]]:
    for key in ("curves", "bind_curves"):
        value = asset.get(key)
        if isinstance(value, list) and value:
            return _copy_curves(value)
    pose_cfg = asset.get("groom_guide_pose") if isinstance(asset.get("groom_guide_pose"), dict) else {}
    for key in ("pose_curves", "bind_curves", "frozen_curves"):
        value = pose_cfg.get(key)
        if isinstance(value, list) and value:
            return _copy_curves(value)
    deform_cfg = asset.get("groom_deform") if isinstance(asset.get("groom_deform"), dict) else {}
    value = deform_cfg.get("bind_curves")
    if isinstance(value, list) and value:
        return _copy_curves(value)
    return []


def _root_points_from_curves(curves) -> list[list[float]]:
    rows: list[list[float]] = []
    for curve in curves or []:
        if isinstance(curve, (list, tuple)) and curve:
            try:
                point = list(curve[0])
                if len(point) >= 3:
                    rows.append([float(point[0]), float(point[1]), float(point[2])])
            except Exception:
                continue
    return rows


def _source_asset_from_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    src = payload if isinstance(payload, dict) else {}
    guides = src.get("groom_guides") if isinstance(src.get("groom_guides"), dict) else {}
    pose_cfg = src.get("groom_guide_pose") if isinstance(src.get("groom_guide_pose"), dict) else {}
    deform_cfg = src.get("groom_deform") if isinstance(src.get("groom_deform"), dict) else {}
    curves = _curves_from_source_asset(
        {
            "curves": src.get("curves"),
            "bind_curves": src.get("bind_curves"),
            "groom_guide_pose": pose_cfg,
            "groom_deform": deform_cfg,
        }
    )
    return {
        "kind": "groom_guides",
        "source_kind": str(src.get("source_kind") or "groom_deform"),
        "node": str(src.get("owner") or ""),
        "guides_path": str(src.get("path") or ""),
        "source_path": str(src.get("source_path") or ""),
        "source_owner": str(src.get("source_owner") or ""),
        "guide_count": int(guides.get("guide_count", len(curves)) or len(curves)),
        "points_per_curve": int(guides.get("points_per_curve", 0) or 0),
        "length": float(guides.get("length", 0.0) or 0.0),
        "root_indices": list(guides.get("root_indices") or root_indices_for_curves(curves)),
        "point_groups": {"root": list(guides.get("root_indices") or root_indices_for_curves(curves))},
        "guide_bindings": list(deform_cfg.get("guide_bindings") or []),
        "curves": _copy_curves(curves),
        "bind_curves": _copy_curves(curves),
        "line_points": _points_list(src.get("line_points")) if src.get("line_points") is not None else curves_to_line_points(curves),
        "root_points": _points_list(src.get("root_points")) if src.get("root_points") is not None else _root_points_from_curves(curves),
        "groom_deform": dict(deform_cfg) if isinstance(deform_cfg, dict) else {},
    }


def _make_pose_outcome(
    node_item,
    guides_asset: dict[str, Any],
    settings: dict[str, Any],
    pose_curves,
    *,
    source_item=None,
    cache_path: Path | None = None,
    line_points=None,
    root_points=None,
    initial_curves=None,
    deform_bind_curves=None,
    interactive: bool = False,
    debug_extra: dict[str, Any] | None = None,
    write_cache: bool = True,
) -> GroomGuidePoseBuildOutcome:
    pose_curves = _copy_curves(pose_curves)
    if not pose_curves:
        return GroomGuidePoseBuildOutcome(None, "error", "Guides input has no curves.", {})
    initial_curves = _copy_curves(initial_curves if initial_curves is not None else pose_curves)
    deform_bind_curves = _copy_curves(deform_bind_curves if deform_bind_curves is not None else pose_curves)
    if len(deform_bind_curves) != len(pose_curves):
        deform_bind_curves = _copy_curves(pose_curves)
    pose_line_points = _points_list(line_points) if line_points is not None else curves_to_line_points(pose_curves)
    pose_root_points = _points_list(root_points) if root_points is not None else _root_points_from_curves(pose_curves)
    root_indices = list(guides_asset.get("root_indices") or [])
    if len(root_indices) != len(pose_curves):
        root_indices = root_indices_for_curves(pose_curves)
    point_groups = dict(guides_asset.get("point_groups") or {})
    point_groups["root"] = list(root_indices)
    model = getattr(node_item, "model", None)
    node_name = str(getattr(model, "name", "") or "Groom Guide Pose").strip() or "Groom Guide Pose"
    source_owner = str(guides_asset.get("source_owner") or guides_asset.get("node") or _node_name(source_item) or "").strip()
    cache_text = str(cache_path or guides_asset.get("guides_path") or guides_asset.get("path") or "")
    debug = {
        "source_node": _node_name(source_item),
        "source_kind": _node_kind(source_item) or str(guides_asset.get("source_kind") or guides_asset.get("kind") or ""),
        "source_owner": source_owner,
        "guide_count": int(len(pose_curves)),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "settings": dict(settings),
        "cache_path": cache_text,
        "interactive_pose": bool(interactive),
    }
    if isinstance(debug_extra, dict):
        debug.update(dict(debug_extra))

    if cache_path is not None and bool(write_cache):
        pose_payload = {
            "schema": "qubit.groom_guide_pose.v2",
            "source_guides_path": str(guides_asset.get("guides_path") or guides_asset.get("path") or ""),
            "source_kind": str(guides_asset.get("source_kind") or guides_asset.get("kind") or ""),
            "settings": dict(settings),
            "guide_count": int(len(pose_curves)),
            "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
            "root_indices": list(root_indices),
            "guide_bindings": list(guides_asset.get("guide_bindings") or []),
            "frozen_curves": _copy_curves(initial_curves),
            "pose_curves": _copy_curves(pose_curves),
            "deform_bind_curves": _copy_curves(deform_bind_curves),
            "simulate_seconds": float(settings.get("settle_seconds", 0.0) or 0.0),
            "simulate_frames": int(settings.get("warmup_frames", 0) or 0),
            "line_points": pose_line_points,
            "root_points": pose_root_points,
            "debug": dict(debug),
        }
        _write_cache(cache_path, pose_payload)

    asset = {
        "kind": "groom_guides",
        "source_kind": "groom_guide_pose",
        "node": node_name,
        "visible": True,
        "debug_log": bool(settings.get("debug_log", False)),
        "guides_path": cache_text,
        "pose_cache_path": str(cache_path or ""),
        "source_path": str(guides_asset.get("source_path") or ""),
        "source_owner": source_owner,
        "guide_count": int(len(pose_curves)),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "length": float(guides_asset.get("length", 0.0) or 0.0),
        "hidden_submeshes": list(guides_asset.get("hidden_submeshes") or []),
        "root_indices": list(root_indices),
        "point_groups": dict(point_groups),
        "guide_bindings": list(guides_asset.get("guide_bindings") or []),
        "curves": _copy_curves(pose_curves),
        "bind_curves": _copy_curves(pose_curves),
        "deform_bind_curves": _copy_curves(deform_bind_curves),
        "line_points": pose_line_points,
        "root_points": pose_root_points,
        "debug": debug,
        "groom_guide_pose": {
            "settings": dict(settings),
            "source_node": _node_name(source_item),
            "source_kind": str(guides_asset.get("source_kind") or guides_asset.get("kind") or ""),
            "source_owner": source_owner,
            "cache_path": str(cache_path or ""),
            "start_frame": int(settings.get("start_frame", 0) or 0),
            "simulate_seconds": float(settings.get("settle_seconds", 0.0) or 0.0),
            "simulate_frames": int(settings.get("warmup_frames", 0) or 0),
            "pose_curves": _copy_curves(pose_curves),
            "bind_curves": _copy_curves(pose_curves),
            "deform_bind_curves": _copy_curves(deform_bind_curves),
            "initial_curves": _copy_curves(initial_curves),
            "pose_state": "captured" if interactive else "live_deform",
        },
    }
    deform_cfg = guides_asset.get("groom_deform") if isinstance(guides_asset.get("groom_deform"), dict) else None
    if isinstance(deform_cfg, dict):
        posed_deform = dict(deform_cfg)
        posed_deform["bind_curves"] = _copy_curves(deform_bind_curves)
        posed_deform["guide_pose"] = {
            "cache_path": str(cache_path or ""),
            "start_frame": int(settings.get("start_frame", 0) or 0),
            "settle_seconds": float(settings.get("settle_seconds", 0.0) or 0.0),
            "warmup_frames": int(settings.get("warmup_frames", 0) or 0),
            "interactive": bool(interactive),
        }
        asset["groom_deform"] = posed_deform
        for key in ("rig_owner", "deformer_owner", "sample_owner", "sample_owner_candidates", "groom_deform_mode"):
            if key in guides_asset:
                asset[key] = guides_asset.get(key)
    if isinstance(guides_asset.get("source_fbx_rig_context"), dict):
        asset["source_fbx_rig_context"] = guides_asset.get("source_fbx_rig_context")
    detail = (
        f"Captured pose at frame {int(settings.get('start_frame', 0) or 0)} for "
        f"{float(settings.get('settle_seconds', 0.0) or 0.0):.2f}s."
        if interactive
        else "Live deform pose ready. Scrub the timeline, then press Sim Pose."
    )
    return GroomGuidePoseBuildOutcome(asset, "ok", detail, debug)


def _outcome_from_pose_cache(
    node_item,
    source_item,
    guides_asset: dict[str, Any],
    settings: dict[str, Any],
    cache_path: Path,
) -> GroomGuidePoseBuildOutcome | None:
    payload, error = _read_pose_cache(cache_path)
    if not isinstance(payload, dict):
        return None
    pose_curves = _copy_curves(payload.get("pose_curves") or payload.get("curves"))
    if not pose_curves:
        return None
    initial_curves = _copy_curves(
        payload.get("frozen_curves")
        or payload.get("initial_curves")
        or payload.get("bind_curves")
        or pose_curves
    )
    deform_bind_curves = _copy_curves(payload.get("deform_bind_curves") or pose_curves)
    line_points = payload.get("line_points") or curves_to_line_points(pose_curves)
    root_points = payload.get("root_points") or _root_points_from_curves(pose_curves)
    debug_extra = dict(payload.get("debug") or {})
    debug_extra.update(
        {
            "pose_build_mode": "disk_cache",
            "pose_cache_path": str(cache_path),
            "pose_cache_error": str(error or ""),
        }
    )
    outcome = _make_pose_outcome(
        node_item,
        guides_asset,
        settings,
        pose_curves,
        source_item=source_item,
        cache_path=cache_path,
        line_points=line_points,
        root_points=root_points,
        initial_curves=initial_curves,
        deform_bind_curves=deform_bind_curves,
        interactive=True,
        debug_extra=debug_extra,
        write_cache=False,
    )
    if not isinstance(outcome.asset, dict):
        return None
    detail = (
        f"Loaded cached pose: frame {int(settings.get('start_frame', 0) or 0)}, "
        f"{float(settings.get('settle_seconds', 0.0) or 0.0):.2f}s."
    )
    return GroomGuidePoseBuildOutcome(
        _clone_value(outcome.asset),
        outcome.status,
        detail,
        _clone_value(outcome.debug) if isinstance(outcome.debug, dict) else outcome.debug,
    )


def _interactive_cached_outcome(
    node_item,
    settings: dict[str, Any],
    source_signature: str,
) -> GroomGuidePoseBuildOutcome | None:
    try:
        cached = getattr(node_item, "_groom_guide_pose_interactive_outcome", None)
        cached_sig = str(getattr(node_item, "_groom_guide_pose_interactive_source_sig", "") or "")
    except Exception:
        return None
    if not isinstance(cached, GroomGuidePoseBuildOutcome):
        return None
    if source_signature and cached_sig and cached_sig != str(source_signature):
        return None
    cached_settings = cached.debug.get("settings") if isinstance(cached.debug, dict) else {}
    if cached_settings != settings:
        return None
    return GroomGuidePoseBuildOutcome(
        _clone_value(cached.asset) if isinstance(cached.asset, dict) else None,
        cached.status,
        cached.detail,
        _clone_value(cached.debug) if isinstance(cached.debug, dict) else cached.debug,
    )


def store_interactive_pose_result(
    node_item,
    pose_curves,
    *,
    line_points=None,
    root_points=None,
    initial_curves=None,
    deform_bind_curves=None,
    settings: dict[str, Any] | None = None,
    source_payload: dict[str, Any] | None = None,
    frame: int | None = None,
    debug: dict[str, Any] | None = None,
) -> GroomGuidePoseBuildOutcome:
    try:
        build_ports(node_item)
    except Exception:
        pass
    model = getattr(node_item, "model", None)
    current_settings = dict(settings if isinstance(settings, dict) else _settings_from_model(model))
    if frame is not None:
        try:
            current_settings["start_frame"] = max(0, int(frame))
        except Exception:
            pass
    try:
        fps = max(1.0e-6, float(current_settings.get("fps", 24.0) or 24.0))
        seconds = max(0.0, float(current_settings.get("settle_seconds", 0.0) or 0.0))
        current_settings["warmup_frames"] = max(0, min(10000, int(round(seconds * fps))))
    except Exception:
        pass
    _set_param(node_item, "start_frame", str(int(current_settings.get("start_frame", 0) or 0)), notify_scene=False)
    _set_param(node_item, "settle_seconds", f"{float(current_settings.get('settle_seconds', 0.0) or 0.0):.6g}", notify_scene=False)
    _set_param(node_item, "warmup_frames", str(int(current_settings.get("warmup_frames", 0) or 0)), notify_scene=False)
    source_item = _connected_input_item(
        node_item,
        {"guides", "guide", "source"},
        GUIDE_KIND_ALIASES | GROOM_DEFORM_KIND_ALIASES | KIND_ALIASES,
    )
    guides_asset, _error = _guides_asset_from_item(source_item)
    if not isinstance(guides_asset, dict):
        guides_asset = _source_asset_from_payload(source_payload)
    output_path = _output_path(node_item, guides_asset, current_settings)
    outcome = _make_pose_outcome(
        node_item,
        guides_asset,
        current_settings,
        pose_curves,
        source_item=source_item,
        cache_path=output_path,
        line_points=line_points,
        root_points=root_points,
        initial_curves=initial_curves,
        deform_bind_curves=deform_bind_curves,
        interactive=True,
        debug_extra=debug,
    )
    if not isinstance(outcome.asset, dict):
        return outcome
    cache_param = _cache_param_value(node_item, output_path)
    _set_param(node_item, "source", str(guides_asset.get("guides_path") or guides_asset.get("source_path") or ""), notify_scene=False)
    _set_param(node_item, "path", cache_param, notify_scene=False)
    _set_param(node_item, "guides_path", cache_param, notify_scene=False)
    _set_param(node_item, "pose_cache_path", cache_param, notify_scene=False)
    source_sig = _build_early_cache_signature(source_item, current_settings) if source_item is not None else ""
    cache_sig = _build_cache_signature(source_item, guides_asset, current_settings, output_path) if source_item is not None else str(output_path)
    _store_cached_outcome(
        node_item,
        source_sig or cache_sig,
        outcome,
        alt_signature=cache_sig,
        extra_signatures=(source_sig, cache_sig),
    )
    try:
        setattr(node_item, "_groom_guide_pose_interactive_source_sig", source_sig)
        setattr(
            node_item,
            "_groom_guide_pose_interactive_outcome",
            GroomGuidePoseBuildOutcome(
                _clone_value(outcome.asset) if isinstance(outcome.asset, dict) else None,
                outcome.status,
                outcome.detail,
                _clone_value(outcome.debug) if isinstance(outcome.debug, dict) else outcome.debug,
            ),
        )
    except Exception:
        pass
    return GroomGuidePoseBuildOutcome(
        _clone_value(outcome.asset) if isinstance(outcome.asset, dict) else None,
        outcome.status,
        outcome.detail,
        _clone_value(outcome.debug) if isinstance(outcome.debug, dict) else outcome.debug,
    )


def clear_interactive_pose_result(node_item) -> GroomGuidePoseBuildOutcome:
    try:
        build_ports(node_item)
    except Exception:
        pass
    for name in (
        "_groom_guide_pose_interactive_outcome",
        "_groom_guide_pose_interactive_source_sig",
        "_groom_guide_pose_cache_outcome",
        "_groom_guide_pose_cache_sig",
        "_groom_guide_pose_cache_alt_sig",
        "_groom_guide_pose_cache_sigs",
    ):
        try:
            if hasattr(node_item, name):
                delattr(node_item, name)
        except Exception:
            try:
                setattr(node_item, name, "")
            except Exception:
                pass
    for name in ("path", "guides_path", "pose_cache_path"):
        _set_param(node_item, name, "", notify_scene=False)
    return build_groom_guide_pose_scene_asset(node_item)


def _clone_value(value):
    if isinstance(value, dict):
        return {key: _clone_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_clone_value(val) for val in value]
    if isinstance(value, tuple):
        return tuple(_clone_value(val) for val in value)
    return value


def _cached_outcome(node_item, signature: str) -> GroomGuidePoseBuildOutcome | None:
    if not signature:
        return None
    try:
        known = set()
        for value in (
            getattr(node_item, "_groom_guide_pose_cache_sig", None),
            getattr(node_item, "_groom_guide_pose_cache_alt_sig", None),
        ):
            if value:
                known.add(str(value))
        extra = getattr(node_item, "_groom_guide_pose_cache_sigs", None)
        if isinstance(extra, (set, list, tuple)):
            known.update(str(value) for value in extra if value)
        if str(signature) not in known:
            return None
        cached = getattr(node_item, "_groom_guide_pose_cache_outcome", None)
    except Exception:
        return None
    if not isinstance(cached, GroomGuidePoseBuildOutcome):
        return None
    return GroomGuidePoseBuildOutcome(
        _clone_value(cached.asset) if isinstance(cached.asset, dict) else None,
        cached.status,
        cached.detail,
        _clone_value(cached.debug) if isinstance(cached.debug, dict) else cached.debug,
    )


def _store_cached_outcome(
    node_item,
    signature: str,
    outcome: GroomGuidePoseBuildOutcome,
    *,
    alt_signature: str | None = None,
    extra_signatures=None,
) -> None:
    try:
        signatures: list[str] = []
        for value in (signature, alt_signature):
            if value and str(value) not in signatures:
                signatures.append(str(value))
        for value in list(extra_signatures or []):
            if value and str(value) not in signatures:
                signatures.append(str(value))
        setattr(node_item, "_groom_guide_pose_cache_sig", signature)
        if alt_signature:
            setattr(node_item, "_groom_guide_pose_cache_alt_sig", alt_signature)
        else:
            setattr(node_item, "_groom_guide_pose_cache_alt_sig", "")
        setattr(node_item, "_groom_guide_pose_cache_sigs", tuple(signatures))
        setattr(
            node_item,
            "_groom_guide_pose_cache_outcome",
            GroomGuidePoseBuildOutcome(
                _clone_value(outcome.asset) if isinstance(outcome.asset, dict) else None,
                outcome.status,
                outcome.detail,
                _clone_value(outcome.debug) if isinstance(outcome.debug, dict) else outcome.debug,
            ),
        )
    except Exception:
        pass


def _model_params_signature(item) -> list[tuple[str, str]]:
    model = getattr(item, "model", None)
    rows: list[tuple[str, str]] = []
    for entry in getattr(model, "params", None) or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip().lower()
        if not name:
            continue
        if name.startswith("_") or name == "__ui_hidden_params":
            continue
        rows.append((name, str(entry.get("value") or "")))
    rows.sort(key=lambda item: item[0])
    return rows


def _lightweight_graph_signature(item, *, depth: int = 0, seen: set[int] | None = None):
    if item is None:
        return None
    model = getattr(item, "model", None)
    ident = int(id(model) if model is not None else id(item))
    if seen is None:
        seen = set()
    if ident in seen:
        return {"cycle": ident, "kind": _node_kind(item), "name": _node_name(item)}
    if depth >= 8:
        return {
            "truncated": True,
            "model_id": ident,
            "kind": _node_kind(item),
            "name": _node_name(item),
            "params": _model_params_signature(item),
        }
    next_seen = set(seen)
    next_seen.add(ident)
    inputs = []
    try:
        scene = item.scene()
    except Exception:
        scene = None
    for index, edge in enumerate(_ordered_in_edges(scene, item)):
        src = getattr(edge, "src", None)
        inputs.append(
            {
                "index": int(index),
                "dst": _edge_dst_name(edge).lower(),
                "src": _lightweight_graph_signature(src, depth=depth + 1, seen=next_seen),
            }
        )
    return {
        "model_id": ident,
        "kind": _node_kind(item),
        "name": _node_name(item),
        "params": _model_params_signature(item),
        "inputs": inputs,
    }


def _build_early_cache_signature(source_item, settings: dict[str, Any]) -> str:
    signature = {
        "schema": "qubit.groom_guide_pose.early_cache.v3",
        "frame_semantics": "literal_pose_frame_zero_based_no_owner_remap",
        "settings": settings,
        "source_graph": _lightweight_graph_signature(source_item),
    }
    try:
        return json.dumps(signature, sort_keys=True, default=str)
    except Exception:
        return str(signature)


def _build_cache_signature(source_item, guides_asset: dict[str, Any], settings: dict[str, Any], output_path: Path) -> str:
    deform_cfg = guides_asset.get("groom_deform") if isinstance(guides_asset.get("groom_deform"), dict) else {}
    signature = {
        "schema": "qubit.groom_guide_pose.cache.v3",
        "frame_semantics": "literal_pose_frame_zero_based_no_owner_remap",
        "output_path": str(output_path),
        "settings": settings,
        "source_kind": _node_kind(source_item),
        "source_name": _node_name(source_item),
        "source_params": _model_params_signature(source_item),
        "guides_path": str(guides_asset.get("guides_path") or guides_asset.get("path") or ""),
        "source_path": str(guides_asset.get("source_path") or ""),
        "source_kind_asset": str(guides_asset.get("source_kind") or guides_asset.get("kind") or ""),
        "guide_count": int(guides_asset.get("guide_count", 0) or 0),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "length": float(guides_asset.get("length", 0.0) or 0.0),
        "deform_mode": str(deform_cfg.get("mode") or guides_asset.get("groom_deform_mode") or ""),
        "rig_context_id": int(id(deform_cfg.get("rig_context"))) if isinstance(deform_cfg, dict) else 0,
        "bind_curve_count": len(deform_cfg.get("bind_curves") or []) if isinstance(deform_cfg, dict) else 0,
        "binding_count": len(deform_cfg.get("guide_bindings") or []) if isinstance(deform_cfg, dict) else 0,
    }
    try:
        return json.dumps(signature, sort_keys=True, default=str)
    except Exception:
        return str(signature)


def _freeze_source_curves(node_item, guides_asset: dict[str, Any], settings: dict[str, Any]):
    deform_cfg = guides_asset.get("groom_deform") if isinstance(guides_asset.get("groom_deform"), dict) else None
    if not isinstance(deform_cfg, dict):
        curves = _copy_curves(guides_asset.get("curves") or [])
        return curves, list(guides_asset.get("line_points") or curves_to_line_points(curves)), list(guides_asset.get("root_points") or []), {}, None
    rig_context = deform_cfg.get("rig_context")
    bind_curves = deform_cfg.get("bind_curves")
    guide_bindings = deform_cfg.get("guide_bindings")
    if not isinstance(rig_context, dict) or not isinstance(bind_curves, list) or not isinstance(guide_bindings, list):
        curves = _copy_curves(guides_asset.get("curves") or [])
        return curves, list(guides_asset.get("line_points") or curves_to_line_points(curves)), list(guides_asset.get("root_points") or []), {}, deform_cfg
    requested_frame = int(settings.get("start_frame", 0) or 0)
    sample_owner = _pose_sample_owner(node_item, guides_asset, deform_cfg, requested_frame)
    sample_seconds, sample_debug = _sample_seconds_from_settings(
        settings,
        rig_context,
        node_item=node_item,
        sample_owner=sample_owner,
    )
    mode = str(deform_cfg.get("mode") or guides_asset.get("groom_deform_mode") or "skinned_cv")
    curves, line_points, root_points, debug = deform_groom_curves(
        bind_curves,
        guide_bindings,
        rig_context,
        sample_seconds=float(sample_seconds),
        mode=mode,
    )
    debug = dict(debug or {})
    debug.update(dict(sample_debug or {}))
    debug["pose_sample_seconds"] = float(sample_seconds)
    debug["pose_start_frame"] = int(settings.get("start_frame", 0) or 0)
    return curves, line_points, root_points, debug, deform_cfg


def _warm_pose_curves(frozen_curves, root_points, settings: dict[str, Any]):
    if not bool(settings.get("enabled", True)):
        return _copy_curves(frozen_curves), list(curves_to_line_points(frozen_curves)), dict(simulation_enabled=False)
    warmup_frames = int(settings.get("warmup_frames", 36) or 36)
    if warmup_frames <= 0:
        curves = _copy_curves(frozen_curves)
        return curves, list(curves_to_line_points(curves)), {"warmup_frames": 0, "simulation_enabled": True}
    config = _config_from_settings(settings)
    if _device_from_settings(settings) != "cpu":
        qt_context, qt_surface = _current_qt_gl_context()
        gpu_pair = _guide_pose_gpu_backend()
        if gpu_pair is not None:
            ctx, backend = gpu_pair
            runtime = None
            try:
                runtime = backend.build_runtime(frozen_curves, config, root_targets=root_points)
                result = backend.step_runtime(runtime, config, root_targets=root_points, steps=warmup_frames)
                point_arr = backend.read_points(runtime)
                line_arr = backend.read_line_points(runtime)
                pose_curves = _curves_from_points_like(point_arr[:, :3], frozen_curves)
                debug = dict(result.get("debug") or {})
                debug["warmup_device"] = "gpu"
                debug["warmup_backend"] = "standalone_moderngl_compute"
                return (
                    pose_curves,
                    [[float(v) for v in row[:3]] for row in list(line_arr)],
                    debug,
                )
            except Exception:
                pass
            finally:
                if runtime is not None:
                    try:
                        backend.release_runtime(runtime)
                    except Exception:
                        pass
                try:
                    if hasattr(ctx, "release"):
                        ctx.release()
                except Exception:
                    pass
                _restore_qt_gl_context(qt_context, qt_surface)
    runtime = build_strand_runtime(frozen_curves, config)
    reset_strand_runtime(runtime, root_targets=root_points)
    points, line_points, _roots, debug = step_strand_runtime(
        runtime,
        config,
        root_targets=root_points,
        steps=warmup_frames,
    )
    pose_curves = _curves_from_points_like(points, frozen_curves)
    debug = dict(debug or {})
    debug["warmup_device"] = "cpu"
    return pose_curves, [[float(v) for v in row[:3]] for row in list(line_points)], debug


def build_groom_guide_pose_scene_asset(node_item) -> GroomGuidePoseBuildOutcome:
    try:
        build_ports(node_item)
    except Exception:
        pass
    model = getattr(node_item, "model", None)
    settings = _settings_from_model(model)
    source_item = _connected_input_item(
        node_item,
        {"guides", "guide", "source"},
        GUIDE_KIND_ALIASES | GROOM_DEFORM_KIND_ALIASES | KIND_ALIASES,
    )
    early_cache_sig = _build_early_cache_signature(source_item, settings) if source_item is not None else ""
    cached = _interactive_cached_outcome(node_item, settings, early_cache_sig)
    if cached is not None:
        return cached
    guides_asset, error = _guides_asset_from_item(source_item)
    if not isinstance(guides_asset, dict):
        return GroomGuidePoseBuildOutcome(None, "error", error or "Connect Groom Deform.", {})
    post_early_cache_sig = _build_early_cache_signature(source_item, settings) if source_item is not None else early_cache_sig
    cached = _interactive_cached_outcome(node_item, settings, post_early_cache_sig or early_cache_sig)
    if cached is not None:
        return cached
    cache_path = _cache_path_from_params(node_item)
    if cache_path is not None:
        cache_outcome = _outcome_from_pose_cache(node_item, source_item, guides_asset, settings, cache_path)
        if cache_outcome is not None:
            cache_sig = _build_cache_signature(source_item, guides_asset, settings, cache_path) if source_item is not None else str(cache_path)
            _store_cached_outcome(
                node_item,
                post_early_cache_sig or cache_sig,
                cache_outcome,
                alt_signature=cache_sig,
                extra_signatures=(post_early_cache_sig, cache_sig),
            )
            try:
                setattr(node_item, "_groom_guide_pose_interactive_source_sig", post_early_cache_sig or cache_sig)
                setattr(
                    node_item,
                    "_groom_guide_pose_interactive_outcome",
                    GroomGuidePoseBuildOutcome(
                        _clone_value(cache_outcome.asset) if isinstance(cache_outcome.asset, dict) else None,
                        cache_outcome.status,
                        cache_outcome.detail,
                        _clone_value(cache_outcome.debug) if isinstance(cache_outcome.debug, dict) else cache_outcome.debug,
                    ),
                )
            except Exception:
                pass
            return cache_outcome
    source_curves = _curves_from_source_asset(guides_asset)
    if not source_curves:
        return GroomGuidePoseBuildOutcome(None, "error", "Guides input has no curves.", {})
    line_points = guides_asset.get("line_points")
    root_points = guides_asset.get("root_points")
    outcome = _make_pose_outcome(
        node_item,
        guides_asset,
        settings,
        source_curves,
        source_item=source_item,
        cache_path=None,
        line_points=line_points,
        root_points=root_points,
        initial_curves=source_curves,
        interactive=False,
        debug_extra={"pose_build_mode": "live_deform_passthrough"},
    )
    return GroomGuidePoseBuildOutcome(
        _clone_value(outcome.asset) if isinstance(outcome.asset, dict) else None,
        outcome.status,
        outcome.detail,
        _clone_value(outcome.debug) if isinstance(outcome.debug, dict) else outcome.debug,
    )


def build_ports(node_item) -> None:
    model = getattr(node_item, "model", None)
    fps_default = _param_float(model, "fps", 24.0, min_value=1.0, max_value=240.0)
    warmup_default = _param_int(model, "warmup_frames", 36, min_value=0, max_value=10000)
    settle_default = max(0.0, min(600.0, float(warmup_default) / max(1.0e-6, float(fps_default))))
    for name, default in (
        ("guides", ""),
        ("source", ""),
        ("path", ""),
        ("guides_path", ""),
        ("pose_cache_path", ""),
        ("enabled", "1"),
        ("start_frame", "0"),
        ("settle_seconds", f"{settle_default:.6g}"),
        ("warmup_frames", "36"),
        ("fps", "24.0"),
        ("substeps", "4"),
        ("iterations", "8"),
        ("stretch", "1.0"),
        ("bend", "0.35"),
        ("damping", "0.04"),
        ("gravity_y", "-9.81"),
        ("wind_x", "0.0"),
        ("wind_y", "0.0"),
        ("wind_z", "0.0"),
        ("scene_units_per_meter", "100.0"),
        ("root_pin", "1.0"),
        ("max_velocity", "250.0"),
        ("device", "auto"),
        ("debug_log", "0"),
    ):
        _ensure_param(node_item, name, default)
    try:
        setattr(getattr(node_item, "model", None), "_named_inputs", ["guides"])
        setattr(node_item, "_show_default_input_with_named", False)
    except Exception:
        pass
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("guides")
    _ensure_hidden_params(getattr(node_item, "model", None), HIDDEN_PARAMS)


def _quick_status(node_item) -> GroomGuidePoseBuildOutcome:
    try:
        build_ports(node_item)
    except Exception:
        pass
    model = getattr(node_item, "model", None)
    settings = _settings_from_model(model)
    source_item = _connected_input_item(
        node_item,
        {"guides", "guide", "source"},
        GUIDE_KIND_ALIASES | GROOM_DEFORM_KIND_ALIASES | KIND_ALIASES,
    )
    if source_item is None:
        return GroomGuidePoseBuildOutcome(None, "error", "Connect Groom Deform.", {})
    kind = _node_kind(source_item)
    allowed = GUIDE_KIND_ALIASES | GROOM_DEFORM_KIND_ALIASES | KIND_ALIASES
    if kind not in allowed:
        return GroomGuidePoseBuildOutcome(None, "error", f"Unsupported guides input: {kind or '<none>'}.", {})
    try:
        cached = getattr(node_item, "_groom_guide_pose_cache_outcome", None)
    except Exception:
        cached = None
    cached_settings = {}
    if isinstance(cached, GroomGuidePoseBuildOutcome) and isinstance(cached.debug, dict):
        cached_settings = cached.debug.get("settings") if isinstance(cached.debug.get("settings"), dict) else {}
    has_cache = bool(cached_settings == settings)
    cache_path = _cache_path_from_params(node_item)
    has_disk_cache = bool(cache_path is not None and cache_path.exists())
    detail = (
        f"Cached pose: pose frame {int(settings.get('start_frame', 0) or 0)}, "
        f"{float(settings.get('settle_seconds', 0.0) or 0.0):.2f}s."
        if has_cache or has_disk_cache
        else (
            f"Ready: pose frame {int(settings.get('start_frame', 0) or 0)}, "
            f"{float(settings.get('settle_seconds', 0.0) or 0.0):.2f}s."
        )
    )
    return GroomGuidePoseBuildOutcome(
        None,
        "ok",
        detail,
        {"settings": dict(settings), "cached": bool(has_cache or has_disk_cache), "cache_path": str(cache_path or "")},
    )


def _resolve_window(node_item):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                return views[0].window()
        except Exception:
            pass
    return None


class GroomGuidePoseWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._pending = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("Connect Groom Deform")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(28)
        layout.addWidget(self._status, 0)

        self._enabled = QtWidgets.QCheckBox("Enabled")
        layout.addWidget(self._enabled, 0)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)
        layout.addLayout(grid, 0)

        self._start_frame = self._spin(0, 100000)
        self._settle_seconds = self._double(0.0, 600.0, decimals=2, step=0.25)
        try:
            self._settle_seconds.setSuffix(" s")
        except Exception:
            pass
        self._fps = self._double(1.0, 240.0, decimals=1, step=1.0)
        self._substeps = self._spin(1, 64)
        self._iterations = self._spin(0, 128)
        self._stretch = self._double(0.0, 1.0, decimals=2, step=0.05)
        self._bend = self._double(0.0, 1.0, decimals=2, step=0.05)
        self._damping = self._double(0.0, 0.999, decimals=3, step=0.01)
        self._gravity_y = self._double(-100.0, 100.0, decimals=2, step=0.5)
        self._scene_units_per_meter = self._double(0.001, 100000.0, decimals=3, step=1.0)

        self._add_row(grid, 0, "Pose Frame", self._start_frame, "Sim Time", self._settle_seconds)
        self._add_row(grid, 1, "FPS", self._fps, "Sub", self._substeps)
        self._add_row(grid, 2, "Iter", self._iterations, "Stretch", self._stretch)
        self._add_row(grid, 3, "Bend", self._bend, "Damp", self._damping)
        self._add_row(grid, 4, "Gravity Y", self._gravity_y, "Units/M", self._scene_units_per_meter)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)
        self._sim_pose_btn = QtWidgets.QPushButton("Sim Pose")
        self._sim_pose_btn.setMinimumHeight(24)
        self._sim_pose_btn.setStyleSheet(
            "QPushButton{background:#a85f24;color:#ffffff;border:1px solid #814719;"
            "border-radius:4px;padding:3px 10px;font-weight:600;}"
            "QPushButton:hover{background:#8f4f1f;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#475569;}"
        )
        self._clear_sim_btn = QtWidgets.QPushButton("Clear Sim")
        self._clear_sim_btn.setMinimumHeight(24)
        self._clear_sim_btn.setStyleSheet(
            "QPushButton{background:#7f2f35;color:#ffffff;border:1px solid #612329;"
            "border-radius:4px;padding:3px 10px;font-weight:600;}"
            "QPushButton:hover{background:#6e282e;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#475569;}"
        )
        action_row.addWidget(self._sim_pose_btn, 1)
        action_row.addWidget(self._clear_sim_btn, 1)
        layout.addLayout(action_row, 0)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setMinimumHeight(24)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#ffffff;border:1px solid #1d4ed8;"
            "border-radius:4px;padding:3px 10px;font-weight:600;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#475569;}"
        )
        layout.addWidget(self._view_btn, 0)

        self._sim_active = False
        self._sim_status_timer = QtCore.QTimer(self)
        self._sim_status_timer.setInterval(150)
        self._sim_status_timer.timeout.connect(self._poll_sim_state)
        self._connect_controls()
        self._sync_from_params()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    def sizeHint(self):
        return QtCore.QSize(GROOM_GUIDE_POSE_NODE_W, GROOM_GUIDE_POSE_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(GROOM_GUIDE_POSE_NODE_W, GROOM_GUIDE_POSE_BODY_H)

    def _spin(self, minimum: int, maximum: int) -> QtWidgets.QSpinBox:
        spin = QtWidgets.QSpinBox(self)
        spin.setRange(int(minimum), int(maximum))
        try:
            spin.setKeyboardTracking(False)
        except Exception:
            pass
        spin.setFixedHeight(22)
        return spin

    def _double(self, minimum: float, maximum: float, *, decimals: int, step: float) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox(self)
        spin.setRange(float(minimum), float(maximum))
        spin.setDecimals(int(decimals))
        spin.setSingleStep(float(step))
        try:
            spin.setKeyboardTracking(False)
        except Exception:
            pass
        spin.setFixedHeight(22)
        return spin

    def _add_row(self, grid, row: int, left_label: str, left_widget, right_label: str, right_widget) -> None:
        label = QtWidgets.QLabel(left_label)
        label.setStyleSheet("color:#cbd5e1;font-size:11px;")
        grid.addWidget(label, row, 0)
        grid.addWidget(left_widget, row, 1)
        if right_widget is not None:
            label2 = QtWidgets.QLabel(right_label)
            label2.setStyleSheet("color:#cbd5e1;font-size:11px;")
            grid.addWidget(label2, row, 2)
            grid.addWidget(right_widget, row, 3)

    def _connect_controls(self):
        self._enabled.stateChanged.connect(lambda _state: self._set_bool("enabled", self._enabled.isChecked()))
        self._connect_int_control(self._start_frame, "start_frame")
        self._settle_seconds.valueChanged.connect(lambda value: self._set_settle_seconds(value, notify_scene=False))
        self._settle_seconds.editingFinished.connect(
            lambda control=self._settle_seconds: self._set_settle_seconds(control.value(), notify_scene=True)
        )
        self._connect_float_control(self._fps, "fps")
        self._connect_int_control(self._substeps, "substeps")
        self._connect_int_control(self._iterations, "iterations")
        self._connect_float_control(self._stretch, "stretch")
        self._connect_float_control(self._bend, "bend")
        self._connect_float_control(self._damping, "damping")
        self._connect_float_control(self._gravity_y, "gravity_y")
        self._connect_float_control(self._scene_units_per_meter, "scene_units_per_meter")
        self._view_btn.clicked.connect(self._on_view_clicked)
        self._sim_pose_btn.clicked.connect(self._on_sim_pose_clicked)
        self._clear_sim_btn.clicked.connect(self._on_clear_sim_clicked)

    def _connect_int_control(self, widget, name: str) -> None:
        widget.valueChanged.connect(lambda value, param=name: self._set_int(param, value, notify_scene=False))
        widget.editingFinished.connect(lambda param=name, control=widget: self._set_int(param, control.value(), notify_scene=True))

    def _connect_float_control(self, widget, name: str) -> None:
        widget.valueChanged.connect(lambda value, param=name: self._set_float(param, value, notify_scene=False))
        widget.editingFinished.connect(lambda param=name, control=widget: self._set_float(param, control.value(), notify_scene=True))

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None:
            return
        try:
            if hasattr(self._scene, "linksChanged"):
                self._scene.linksChanged.connect(self._schedule_refresh)
            if hasattr(self._scene, "paramChanged"):
                self._scene.paramChanged.connect(self._on_scene_param_changed)
        except Exception:
            pass

    def _on_scene_param_changed(self, name=None, _params=None):
        if not name or str(name).strip().lower() in HIDDEN_PARAMS:
            self._schedule_refresh()

    def _schedule_refresh(self, *_args):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(120, self._refresh_status)

    def _sync_from_params(self):
        model = getattr(self._node_item, "model", None)
        widgets = [
            self._enabled,
            self._start_frame,
            self._settle_seconds,
            self._fps,
            self._substeps,
            self._iterations,
            self._stretch,
            self._bend,
            self._damping,
            self._gravity_y,
            self._scene_units_per_meter,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self._enabled.setChecked(_param_bool(model, "enabled", True))
            self._start_frame.setValue(_param_int(model, "start_frame", 0, min_value=0, max_value=100000))
            settings = _settings_from_model(model)
            self._settle_seconds.setValue(float(settings.get("settle_seconds", 1.5) or 1.5))
            self._fps.setValue(_param_float(model, "fps", 24.0, min_value=1.0, max_value=240.0))
            self._substeps.setValue(_param_int(model, "substeps", 4, min_value=1, max_value=64))
            self._iterations.setValue(_param_int(model, "iterations", 8, min_value=0, max_value=128))
            self._stretch.setValue(_param_float(model, "stretch", 1.0, min_value=0.0, max_value=1.0))
            self._bend.setValue(_param_float(model, "bend", 0.35, min_value=0.0, max_value=1.0))
            self._damping.setValue(_param_float(model, "damping", 0.04, min_value=0.0, max_value=0.999))
            self._gravity_y.setValue(_param_float(model, "gravity_y", -9.81, min_value=-100.0, max_value=100.0))
            self._scene_units_per_meter.setValue(
                _param_float(model, "scene_units_per_meter", 100.0, min_value=0.001, max_value=100000.0)
            )
        finally:
            for widget in widgets:
                widget.blockSignals(False)

    def _set_bool(self, name: str, value: bool):
        _set_param(self._node_item, name, "1" if value else "0", notify_scene=True)
        self._schedule_refresh()

    def _set_int(self, name: str, value: int, *, notify_scene: bool = True):
        _set_param(self._node_item, name, str(int(value)), notify_scene=bool(notify_scene))
        self._schedule_refresh()

    def _set_float(self, name: str, value: float, *, notify_scene: bool = True):
        _set_param(self._node_item, name, f"{float(value):.6g}", notify_scene=bool(notify_scene))
        if str(name or "").strip().lower() == "fps":
            _set_param(self._node_item, "warmup_frames", str(self._settle_frame_count()), notify_scene=False)
        self._schedule_refresh()

    def _settle_frame_count(self) -> int:
        try:
            seconds = max(0.0, float(self._settle_seconds.value()))
        except Exception:
            seconds = 0.0
        try:
            fps = max(1.0e-6, float(self._fps.value()))
        except Exception:
            fps = 24.0
        return max(0, min(10000, int(round(seconds * fps))))

    def _set_settle_seconds(self, value: float, *, notify_scene: bool = True):
        seconds = max(0.0, min(600.0, float(value)))
        _set_param(self._node_item, "settle_seconds", f"{seconds:.6g}", notify_scene=bool(notify_scene))
        _set_param(self._node_item, "warmup_frames", str(self._settle_frame_count()), notify_scene=False)
        self._schedule_refresh()

    def _commit_current_controls(self, *, notify_scene: bool = False) -> None:
        for control in (
            self._start_frame,
            self._settle_seconds,
            self._fps,
            self._substeps,
            self._iterations,
            self._stretch,
            self._bend,
            self._damping,
            self._gravity_y,
            self._scene_units_per_meter,
        ):
            try:
                control.interpretText()
            except Exception:
                pass
        _set_param(self._node_item, "enabled", "1" if self._enabled.isChecked() else "0", notify_scene=bool(notify_scene))
        _set_param(self._node_item, "start_frame", str(int(self._start_frame.value())), notify_scene=bool(notify_scene))
        _set_param(self._node_item, "fps", f"{float(self._fps.value()):.6g}", notify_scene=bool(notify_scene))
        _set_param(self._node_item, "settle_seconds", f"{float(self._settle_seconds.value()):.6g}", notify_scene=bool(notify_scene))
        _set_param(self._node_item, "warmup_frames", str(self._settle_frame_count()), notify_scene=False)
        _set_param(self._node_item, "substeps", str(int(self._substeps.value())), notify_scene=bool(notify_scene))
        _set_param(self._node_item, "iterations", str(int(self._iterations.value())), notify_scene=bool(notify_scene))
        _set_param(self._node_item, "stretch", f"{float(self._stretch.value()):.6g}", notify_scene=bool(notify_scene))
        _set_param(self._node_item, "bend", f"{float(self._bend.value()):.6g}", notify_scene=bool(notify_scene))
        _set_param(self._node_item, "damping", f"{float(self._damping.value()):.6g}", notify_scene=bool(notify_scene))
        _set_param(self._node_item, "gravity_y", f"{float(self._gravity_y.value()):.6g}", notify_scene=bool(notify_scene))
        _set_param(
            self._node_item,
            "scene_units_per_meter",
            f"{float(self._scene_units_per_meter.value()):.6g}",
            notify_scene=bool(notify_scene),
        )

    def _control_has_focus(self) -> bool:
        for control in (
            self._start_frame,
            self._settle_seconds,
            self._fps,
            self._substeps,
            self._iterations,
            self._stretch,
            self._bend,
            self._damping,
            self._gravity_y,
            self._scene_units_per_meter,
        ):
            try:
                if control.hasFocus():
                    return True
            except Exception:
                pass
            try:
                editor = control.lineEdit()
                if editor is not None and editor.hasFocus():
                    return True
            except Exception:
                pass
        return False

    def _refresh_status(self):
        self._pending = False
        if not self._control_has_focus():
            self._sync_from_params()
        outcome = _quick_status(self._node_item)
        self._view_btn.setEnabled(outcome.status != "error")
        self._sim_pose_btn.setEnabled(outcome.status != "error")
        self._clear_sim_btn.setEnabled(outcome.status != "error")
        if outcome.status == "ok":
            self._status.setStyleSheet("color:#22c55e;font-size:11px;")
        elif outcome.status == "warning":
            self._status.setStyleSheet("color:#f59e0b;font-size:11px;")
        else:
            self._status.setStyleSheet("color:#f87171;font-size:11px;")
        self._status.setText(outcome.detail)
        self._update_sim_button_state()

    def _pose_sim_status(self) -> dict[str, Any]:
        win = _resolve_window(self._node_item)
        gl_view = getattr(win, "gl_view", None) if win is not None else None
        status_handler = getattr(gl_view, "groom_guide_pose_sim_status", None) if gl_view is not None else None
        if not callable(status_handler):
            return {"loaded": False, "active": False}
        try:
            owner = str(getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
            status = status_handler(owner)
            return dict(status) if isinstance(status, dict) else {"loaded": False, "active": False}
        except Exception:
            return {"loaded": False, "active": False}

    def _update_sim_button_state(self) -> bool:
        active = bool(self._pose_sim_status().get("active", False))
        self._sim_active = active
        self._sim_pose_btn.setText("Stop Sim" if active else "Sim Pose")
        if active:
            if not self._sim_status_timer.isActive():
                self._sim_status_timer.start()
        elif self._sim_status_timer.isActive():
            self._sim_status_timer.stop()
        return active

    def _poll_sim_state(self) -> None:
        was_active = bool(self._sim_active)
        active = self._update_sim_button_state()
        if was_active and not active:
            self._schedule_refresh()

    def _on_view_clicked(self):
        self._commit_current_controls(notify_scene=False)
        outcome = build_groom_guide_pose_scene_asset(self._node_item)
        if not isinstance(outcome.asset, dict):
            self._refresh_status()
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if callable(handler):
            try:
                handler([dict(outcome.asset)], frame=True)
            except TypeError:
                handler([dict(outcome.asset)])
        self._refresh_status()

    def _on_sim_pose_clicked(self):
        win = _resolve_window(self._node_item)
        if win is None:
            self._status.setStyleSheet("color:#f87171;font-size:11px;")
            self._status.setText("Open the node in the viewport before sim posing.")
            return
        gl_view = getattr(win, "gl_view", None)
        runtime_status = self._pose_sim_status()
        if bool(runtime_status.get("active", False)):
            stop_handler = getattr(gl_view, "stop_groom_guide_pose_sim", None) if gl_view is not None else None
            if not callable(stop_handler):
                self._status.setStyleSheet("color:#f87171;font-size:11px;")
                self._status.setText("Viewport pose simulation cannot be stopped.")
                return
            result = stop_handler(node_item=self._node_item)
            ok = bool(isinstance(result, dict) and result.get("ok", False))
            detail = str(result.get("detail") or "") if isinstance(result, dict) else ""
            if ok:
                self._sim_active = False
                self._sim_pose_btn.setText("Sim Pose")
                self._sim_status_timer.stop()
                self._status.setStyleSheet("color:#22c55e;font-size:11px;")
                self._status.setText(detail or "Sim Pose stopped. Current pose captured.")
            else:
                self._status.setStyleSheet("color:#f87171;font-size:11px;")
                self._status.setText(detail or "Could not stop Sim Pose.")
            return
        self._commit_current_controls(notify_scene=False)
        if not bool(runtime_status.get("loaded", False)):
            outcome = build_groom_guide_pose_scene_asset(self._node_item)
            if isinstance(outcome.asset, dict):
                handler = getattr(win, "open_scene_assets", None)
                if callable(handler):
                    try:
                        handler([dict(outcome.asset)], frame=False)
                    except TypeError:
                        handler([dict(outcome.asset)])
        start_handler = getattr(gl_view, "start_groom_guide_pose_sim", None) if gl_view is not None else None
        if not callable(start_handler):
            self._status.setStyleSheet("color:#f87171;font-size:11px;")
            self._status.setText("Viewport pose simulation is unavailable.")
            return
        settings = _settings_from_model(getattr(self._node_item, "model", None))
        result = start_handler(node_item=self._node_item, settings=settings)
        ok = bool(isinstance(result, dict) and result.get("ok", False))
        detail = str(result.get("detail") or "") if isinstance(result, dict) else ""
        if ok:
            self._sim_active = True
            self._sim_pose_btn.setText("Stop Sim")
            self._sim_status_timer.start()
            self._status.setStyleSheet("color:#22c55e;font-size:11px;")
            self._status.setText(detail or "Sim Pose running.")
        else:
            self._status.setStyleSheet("color:#f87171;font-size:11px;")
            self._status.setText(detail or "Sim Pose failed.")

    def _on_clear_sim_clicked(self):
        win = _resolve_window(self._node_item)
        gl_view = getattr(win, "gl_view", None) if win is not None else None
        clear_viewport = getattr(gl_view, "clear_groom_guide_pose_sim", None) if gl_view is not None else None
        if callable(clear_viewport):
            try:
                clear_viewport(node_item=self._node_item)
            except Exception:
                pass
        self._sim_active = False
        self._sim_pose_btn.setText("Sim Pose")
        self._sim_status_timer.stop()
        outcome = clear_interactive_pose_result(self._node_item)
        if not isinstance(outcome.asset, dict):
            self._refresh_status()
            return
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if callable(handler):
            try:
                handler([dict(outcome.asset)], frame=False)
            except TypeError:
                handler([dict(outcome.asset)])
        self._status.setStyleSheet("color:#22c55e;font-size:11px;")
        self._status.setText("Sim cleared. Live/rest guides restored.")


def _ensure_body_space(node_item, bottom_y: int) -> None:
    try:
        old_h = float(getattr(node_item, "height", 0.0) or 0.0)
        min_h = float(bottom_y) + 10.0
        if old_h < min_h:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = min_h
            try:
                node_item.update()
            except Exception:
                pass
    except Exception:
        pass


def render_node_body(node_item, y_cursor: int) -> int:
    try:
        build_ports(node_item)
    except Exception:
        pass
    body = GroomGuidePoseWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    h = max(int(body.sizeHint().height()), int(body.minimumSizeHint().height()))
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    bottom_y = int(y_cursor) + h
    _ensure_body_space(node_item, bottom_y)
    return bottom_y


GROOM_GUIDE_POSE_SPEC = Spec(
    stripe_color="#7a5842",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "GROOM_GUIDE_POSE_BODY_H",
    "GROOM_GUIDE_POSE_NODE_W",
    "GROOM_GUIDE_POSE_SPEC",
    "GroomGuidePoseBuildOutcome",
    "KIND_ALIASES",
    "build_groom_guide_pose_scene_asset",
    "build_ports",
    "clear_interactive_pose_result",
    "render_node_body",
    "store_interactive_pose_result",
]
