from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Optional

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from nodes.core import Spec
from echograph.rigging.xpbd_strand import (
    curves_to_line_points,
    root_indices_for_curves,
)


KIND_ALIASES = {
    "groom_guide_sim",
    "groom guide sim",
    "groom_guides_sim",
    "groom guides sim",
    "hair_guide_sim",
    "hair guide sim",
    "hair_guides_sim",
    "hair guides sim",
    "hair_sim",
    "hair sim",
    "guide_sim",
    "guide sim",
}

GUIDE_KIND_ALIASES = {"groom_guides", "groom guides", "hair_guides", "hair guides"}
GROOM_DEFORM_KIND_ALIASES = {
    "groom_deform",
    "groom deform",
    "groomdeform",
    "hair_deform",
    "hair deform",
}
GROOM_GUIDE_POSE_KIND_ALIASES = {
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

GROOM_GUIDE_SIM_NODE_W = 292
GROOM_GUIDE_SIM_BODY_H = 242

HIDDEN_PARAMS = {
    "guides",
    "source",
    "path",
    "guides_path",
    "sim_cache_path",
    "enabled",
    "start_frame",
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
class GroomGuideSimBuildOutcome:
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


def _remove_param(node_item, name: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    key = str(name or "").strip().lower()
    params = []
    for entry in list(getattr(model, "params", None) or []):
        if not isinstance(entry, dict):
            params.append(entry)
            continue
        entry_name = str(entry.get("name") or "").strip().lower()
        if entry_name == key:
            continue
        if entry_name == "__ui_hidden_params":
            hidden = [
                part.strip().lower()
                for part in str(entry.get("value") or "").split(",")
                if part.strip() and part.strip().lower() != key
            ]
            entry["value"] = ",".join(sorted(set(hidden)))
        params.append(entry)
    try:
        setattr(model, "params", params)
    except Exception:
        pass


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
        return None, "Connect Groom Guides to the guides input."
    kind = _node_kind(item)
    if kind in KIND_ALIASES:
        outcome = build_groom_guide_sim_scene_asset(item)
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
    if kind in GROOM_GUIDE_POSE_KIND_ALIASES:
        try:
            from nodes.groom_guide_pose import spec as pose_spec  # type: ignore

            build = getattr(pose_spec, "build_groom_guide_pose_scene_asset", None)
            outcome = build(item) if callable(build) else None
        except Exception as exc:
            return None, f"Groom Guide Pose build failed: {exc}"
        asset = getattr(outcome, "asset", None)
        detail = str(getattr(outcome, "detail", "") or "")
        return (dict(asset), "") if isinstance(asset, dict) else (None, detail or "Groom Guide Pose did not produce guides.")
    if kind not in GUIDE_KIND_ALIASES:
        return None, f"Unsupported guides input: {kind or '<none>'}."
    try:
        from nodes.groom_guides import spec as guides_spec  # type: ignore

        build = getattr(guides_spec, "build_groom_guides_scene_asset", None)
        outcome = build(item) if callable(build) else None
    except Exception as exc:
        return None, f"Groom Guides build failed: {exc}"
    asset = getattr(outcome, "asset", None)
    detail = str(getattr(outcome, "detail", "") or "")
    return (dict(asset), "") if isinstance(asset, dict) else (None, detail or "Groom Guides did not produce curves.")


def _sanitize_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    safe = re.sub(r"_+", "_", safe)
    return safe.strip("._") or "groom_guide_sim"


def _logs_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    out = root / "logs" / "groom_guide_sim"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _sim_settings_from_model(model) -> dict[str, Any]:
    device = (_param_value(model, "device", "auto").strip().lower() or "auto")
    if device in {"cpu", "python"}:
        device = "auto"
    return {
        "enabled": _param_bool(model, "enabled", True),
        "start_frame": _param_int(model, "start_frame", 0, min_value=0, max_value=100000),
        "fps": _param_float(model, "fps", 24.0, min_value=1.0, max_value=240.0),
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


def _output_path(node_item, source_asset: dict[str, Any], settings: dict[str, Any]) -> Path:
    model = getattr(node_item, "model", None)
    node_name = _sanitize_name(str(getattr(model, "name", "") or "groom_guide_sim"))
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
    return _logs_dir() / f"{node_name}_{digest}.json"


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    except Exception:
        pass


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


def _start_curves_from_asset(guides_asset: dict[str, Any], fallback_curves) -> list[list[list[float]]]:
    pose_cfg = guides_asset.get("groom_guide_pose") if isinstance(guides_asset.get("groom_guide_pose"), dict) else {}
    for source in (
        pose_cfg.get("pose_curves") if isinstance(pose_cfg, dict) else None,
        pose_cfg.get("bind_curves") if isinstance(pose_cfg, dict) else None,
        guides_asset.get("start_curves"),
        guides_asset.get("bind_curves"),
        fallback_curves,
    ):
        curves = _copy_curves(source)
        if curves and any(len(curve) >= 2 for curve in curves):
            return curves
    return _copy_curves(fallback_curves)


def build_groom_guide_sim_scene_asset(node_item) -> GroomGuideSimBuildOutcome:
    try:
        build_ports(node_item)
    except Exception:
        pass
    model = getattr(node_item, "model", None)
    settings = _sim_settings_from_model(model)
    source_item = _connected_input_item(
        node_item,
        {"guides", "guide", "source"},
        GUIDE_KIND_ALIASES | GROOM_DEFORM_KIND_ALIASES | GROOM_GUIDE_POSE_KIND_ALIASES | KIND_ALIASES,
    )
    guides_asset, error = _guides_asset_from_item(source_item)
    if not isinstance(guides_asset, dict):
        return GroomGuideSimBuildOutcome(None, "error", error or "Connect a guide source.", {})
    curves = _copy_curves(guides_asset.get("curves") or [])
    if not curves:
        return GroomGuideSimBuildOutcome(None, "error", "Guides input has no curves.", {})

    bind_curves = _start_curves_from_asset(guides_asset, curves)
    if bool(settings.get("enabled", True)):
        sim_curves = bind_curves
        line_points = list(guides_asset.get("line_points") or curves_to_line_points(sim_curves))
        sim_debug = {
            "input_curve_count": int(len(curves)),
            "simulated_curve_count": int(len(sim_curves)),
            "line_point_count": int(len(line_points)),
            "simulation_mode": "viewport_realtime",
            "start_frame": int(settings.get("start_frame", 0) or 0),
            "first_simulated_frame": int(settings.get("start_frame", 0) or 0) + 1,
            "solver_device": str(settings.get("device", "auto") or "auto"),
        }
        start_frame = int(settings.get("start_frame", 0) or 0)
        detail = (
            f"Realtime sim ready: {len(sim_curves)} guide curve(s), "
            f"armed at frame {start_frame}; first physics frame {start_frame + 1}."
        )
        status = "ok"
    else:
        sim_curves = bind_curves
        line_points = list(guides_asset.get("line_points") or curves_to_line_points(sim_curves))
        sim_debug = {
            "input_curve_count": int(len(curves)),
            "simulated_curve_count": int(len(sim_curves)),
            "line_point_count": int(len(line_points)),
            "simulation_enabled": False,
        }
        detail = f"Simulation disabled; passing {len(sim_curves)} guide curve(s) through."
        status = "warning"

    root_indices = list(guides_asset.get("root_indices") or [])
    if len(root_indices) != len(sim_curves):
        root_indices = root_indices_for_curves(sim_curves)
    point_groups = dict(guides_asset.get("point_groups") or {})
    point_groups["root"] = list(root_indices)
    node_name = str(getattr(model, "name", "") or "Groom Guide Sim").strip() or "Groom Guide Sim"
    source_owner = str(guides_asset.get("source_owner") or guides_asset.get("node") or _node_name(source_item) or "").strip()
    output_path = _output_path(node_item, guides_asset, settings)
    debug = {
        "source_node": _node_name(source_item),
        "source_kind": _node_kind(source_item),
        "source_owner": source_owner,
        "guide_count": int(len(sim_curves)),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "settings": dict(settings),
        "cache_path": str(output_path),
    }
    debug.update(sim_debug)
    payload = {
        "schema": "qubit.groom_guide_sim.v1",
        "source_guides_path": str(guides_asset.get("guides_path") or guides_asset.get("path") or ""),
        "source_kind": str(guides_asset.get("source_kind") or guides_asset.get("kind") or ""),
        "settings": dict(settings),
        "guide_count": int(len(sim_curves)),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "root_indices": list(root_indices),
        "guide_bindings": list(guides_asset.get("guide_bindings") or []),
        "start_curves": _copy_curves(bind_curves),
        "bind_curves": bind_curves,
        "curves": sim_curves,
        "line_points": line_points,
        "debug": dict(debug),
    }
    if isinstance(guides_asset.get("groom_deform"), dict):
        payload["groom_deform"] = dict(guides_asset.get("groom_deform") or {})
    if isinstance(guides_asset.get("groom_guide_pose"), dict):
        payload["groom_guide_pose"] = dict(guides_asset.get("groom_guide_pose") or {})
    _write_cache(output_path, payload)
    _set_param(node_item, "source", str(guides_asset.get("guides_path") or guides_asset.get("source_path") or ""), notify_scene=False)
    _set_param(node_item, "path", str(output_path), notify_scene=False)
    _set_param(node_item, "guides_path", str(output_path), notify_scene=False)
    _set_param(node_item, "sim_cache_path", str(output_path), notify_scene=False)

    asset = {
        "kind": "groom_guides",
        "source_kind": "groom_guide_sim",
        "node": node_name,
        "visible": True,
        "debug_log": bool(settings.get("debug_log", False)),
        "guides_path": str(output_path),
        "sim_cache_path": str(output_path),
        "source_path": str(guides_asset.get("source_path") or ""),
        "source_owner": source_owner,
        "guide_count": int(len(sim_curves)),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "length": float(guides_asset.get("length", 0.0) or 0.0),
        "hidden_submeshes": list(guides_asset.get("hidden_submeshes") or []),
        "root_indices": list(root_indices),
        "point_groups": dict(point_groups),
        "guide_bindings": list(guides_asset.get("guide_bindings") or []),
        "start_curves": _copy_curves(bind_curves),
        "curves": sim_curves,
        "bind_curves": bind_curves,
        "line_points": line_points,
        "debug": debug,
        "groom_guide_sim_settings": dict(settings),
        "groom_guide_sim": {
            "settings": dict(settings),
            "source_node": _node_name(source_item),
            "source_kind": str(guides_asset.get("source_kind") or guides_asset.get("kind") or ""),
            "source_owner": source_owner,
            "cache_path": str(output_path),
            "start_curves": _copy_curves(bind_curves),
            "bind_curves": bind_curves,
        },
    }
    if isinstance(guides_asset.get("groom_deform"), dict):
        asset["groom_deform"] = dict(guides_asset.get("groom_deform") or {})
        for key in ("rig_owner", "deformer_owner", "sample_owner", "sample_owner_candidates", "groom_deform_mode"):
            if key in guides_asset:
                asset[key] = guides_asset.get(key)
    if isinstance(guides_asset.get("groom_guide_pose"), dict):
        asset["groom_guide_pose"] = dict(guides_asset.get("groom_guide_pose") or {})
    if isinstance(guides_asset.get("source_fbx_rig_context"), dict):
        asset["source_fbx_rig_context"] = guides_asset.get("source_fbx_rig_context")
    return GroomGuideSimBuildOutcome(asset, status, detail, debug)


def build_ports(node_item) -> None:
    _remove_param(node_item, "frames")
    for name, default in (
        ("guides", ""),
        ("source", ""),
        ("path", ""),
        ("guides_path", ""),
        ("sim_cache_path", ""),
        ("enabled", "1"),
        ("start_frame", "0"),
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
    try:
        return node_item.window()
    except Exception:
        return QtWidgets.QApplication.activeWindow()


def _quick_status(node_item) -> GroomGuideSimBuildOutcome:
    try:
        build_ports(node_item)
    except Exception:
        pass
    model = getattr(node_item, "model", None)
    settings = _sim_settings_from_model(model)
    source_item = _connected_input_item(
        node_item,
        {"guides", "guide", "source"},
        GUIDE_KIND_ALIASES | GROOM_DEFORM_KIND_ALIASES | GROOM_GUIDE_POSE_KIND_ALIASES | KIND_ALIASES,
    )
    if source_item is None:
        return GroomGuideSimBuildOutcome(None, "error", "Connect a guide source.", {"settings": dict(settings)})
    guides_asset, error = _guides_asset_from_item(source_item)
    if not isinstance(guides_asset, dict):
        return GroomGuideSimBuildOutcome(None, "error", error or "Connect a guide source.", {"settings": dict(settings)})
    curves = _copy_curves(guides_asset.get("curves") or [])
    if not curves:
        return GroomGuideSimBuildOutcome(None, "error", "Guides input has no curves.", {"settings": dict(settings)})
    start_frame = int(settings.get("start_frame", 0) or 0)
    detail = f"Ready: {len(curves)} guide curve(s), armed at frame {start_frame}; first physics frame {start_frame + 1}."
    return GroomGuideSimBuildOutcome({"guide_count": int(len(curves))}, "ok", detail, {"settings": dict(settings)})


class GroomGuideSimWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._pending = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("Connect guide curves")
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
        self._fps = self._double(1.0, 240.0, decimals=1, step=1.0)
        self._substeps = self._spin(1, 64)
        self._iterations = self._spin(0, 128)
        self._stretch = self._double(0.0, 1.0, decimals=2, step=0.05)
        self._bend = self._double(0.0, 1.0, decimals=2, step=0.05)
        self._damping = self._double(0.0, 0.999, decimals=3, step=0.01)
        self._gravity_y = self._double(-100.0, 100.0, decimals=2, step=0.5)
        self._wind_x = self._double(-100.0, 100.0, decimals=2, step=0.5)
        self._wind_y = self._double(-100.0, 100.0, decimals=2, step=0.5)
        self._wind_z = self._double(-100.0, 100.0, decimals=2, step=0.5)
        self._scene_units_per_meter = self._double(0.001, 100000.0, decimals=3, step=1.0)

        self._add_row(grid, 0, "Start Frame", self._start_frame, "FPS", self._fps)
        self._add_row(grid, 1, "Sub", self._substeps, "Iter", self._iterations)
        self._add_row(grid, 2, "Stretch", self._stretch, "Bend", self._bend)
        self._add_row(grid, 3, "Damp", self._damping, "Gravity Y", self._gravity_y)
        self._add_row(grid, 4, "Wind X", self._wind_x, "Wind Y", self._wind_y)
        self._add_row(grid, 5, "Wind Z", self._wind_z, "Units/M", self._scene_units_per_meter)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setMinimumHeight(24)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#ffffff;border:1px solid #1d4ed8;"
            "border-radius:4px;padding:3px 10px;font-weight:600;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#475569;}"
        )
        layout.addWidget(self._view_btn, 0)

        self._connect_controls()
        self._sync_from_params()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    def sizeHint(self):
        return QtCore.QSize(GROOM_GUIDE_SIM_NODE_W, GROOM_GUIDE_SIM_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(GROOM_GUIDE_SIM_NODE_W, GROOM_GUIDE_SIM_BODY_H)

    def _spin(self, minimum: int, maximum: int) -> QtWidgets.QSpinBox:
        spin = QtWidgets.QSpinBox(self)
        spin.setRange(int(minimum), int(maximum))
        spin.setFixedHeight(22)
        return spin

    def _double(self, minimum: float, maximum: float, *, decimals: int, step: float) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox(self)
        spin.setRange(float(minimum), float(maximum))
        spin.setDecimals(int(decimals))
        spin.setSingleStep(float(step))
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
        self._start_frame.valueChanged.connect(lambda value: self._set_int("start_frame", value))
        self._fps.valueChanged.connect(lambda value: self._set_float("fps", value))
        self._substeps.valueChanged.connect(lambda value: self._set_int("substeps", value))
        self._iterations.valueChanged.connect(lambda value: self._set_int("iterations", value))
        self._stretch.valueChanged.connect(lambda value: self._set_float("stretch", value))
        self._bend.valueChanged.connect(lambda value: self._set_float("bend", value))
        self._damping.valueChanged.connect(lambda value: self._set_float("damping", value))
        self._gravity_y.valueChanged.connect(lambda value: self._set_float("gravity_y", value))
        self._wind_x.valueChanged.connect(lambda value: self._set_float("wind_x", value))
        self._wind_y.valueChanged.connect(lambda value: self._set_float("wind_y", value))
        self._wind_z.valueChanged.connect(lambda value: self._set_float("wind_z", value))
        self._scene_units_per_meter.valueChanged.connect(lambda value: self._set_float("scene_units_per_meter", value))
        self._view_btn.clicked.connect(self._on_view_clicked)

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
            self._fps,
            self._substeps,
            self._iterations,
            self._stretch,
            self._bend,
            self._damping,
            self._gravity_y,
            self._wind_x,
            self._wind_y,
            self._wind_z,
            self._scene_units_per_meter,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self._enabled.setChecked(_param_bool(model, "enabled", True))
            self._start_frame.setValue(_param_int(model, "start_frame", 0, min_value=0, max_value=100000))
            self._fps.setValue(_param_float(model, "fps", 24.0, min_value=1.0, max_value=240.0))
            self._substeps.setValue(_param_int(model, "substeps", 4, min_value=1, max_value=64))
            self._iterations.setValue(_param_int(model, "iterations", 8, min_value=0, max_value=128))
            self._stretch.setValue(_param_float(model, "stretch", 1.0, min_value=0.0, max_value=1.0))
            self._bend.setValue(_param_float(model, "bend", 0.35, min_value=0.0, max_value=1.0))
            self._damping.setValue(_param_float(model, "damping", 0.04, min_value=0.0, max_value=0.999))
            self._gravity_y.setValue(_param_float(model, "gravity_y", -9.81, min_value=-100.0, max_value=100.0))
            self._wind_x.setValue(_param_float(model, "wind_x", 0.0, min_value=-100.0, max_value=100.0))
            self._wind_y.setValue(_param_float(model, "wind_y", 0.0, min_value=-100.0, max_value=100.0))
            self._wind_z.setValue(_param_float(model, "wind_z", 0.0, min_value=-100.0, max_value=100.0))
            self._scene_units_per_meter.setValue(
                _param_float(model, "scene_units_per_meter", 100.0, min_value=0.001, max_value=100000.0)
            )
        finally:
            for widget in widgets:
                widget.blockSignals(False)

    def _set_bool(self, name: str, value: bool):
        _set_param(self._node_item, name, "1" if value else "0", notify_scene=True)
        self._schedule_refresh()

    def _set_int(self, name: str, value: int):
        _set_param(self._node_item, name, str(int(value)), notify_scene=True)
        self._schedule_refresh()

    def _set_float(self, name: str, value: float):
        _set_param(self._node_item, name, f"{float(value):.6g}", notify_scene=True)
        self._schedule_refresh()

    def _refresh_status(self):
        self._pending = False
        self._sync_from_params()
        outcome = _quick_status(self._node_item)
        self._view_btn.setEnabled(bool(outcome.asset))
        if outcome.status == "ok":
            self._status.setStyleSheet("color:#22c55e;font-size:11px;")
        elif outcome.status == "warning":
            self._status.setStyleSheet("color:#f59e0b;font-size:11px;")
        else:
            self._status.setStyleSheet("color:#ef4444;font-size:11px;")
        detail = outcome.detail
        win = _resolve_window(self._node_item)
        gl_view = getattr(win, "gl_view", None) if win is not None else None
        status_fn = getattr(gl_view, "groom_guide_sim_runtime_status", None) if gl_view is not None else None
        if callable(status_fn):
            try:
                owner = str(getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
                runtime_status = status_fn(owner)
            except Exception:
                runtime_status = {}
            device = str((runtime_status or {}).get("device") or "").strip().lower()
            if device == "gpu":
                detail += " Viewport: GPU."
            elif device == "cpu":
                detail += " Viewport: CPU fallback."
            elif device == "waiting":
                mode = str(((runtime_status or {}).get("debug") or {}).get("simulation_mode") or "")
                if mode == "deformed_start_state_armed":
                    detail += " Viewport: start state armed; physics begins next frame."
                elif mode == "deform_only_start_frame_not_armed":
                    detail += " Viewport: deform only; play through Start Frame to arm physics."
                else:
                    detail += " Viewport: deform only before Start Frame."
            elif device:
                detail += f" Viewport: {device}."
        self._status.setText(detail)

    def _on_view_clicked(self):
        outcome = build_groom_guide_sim_scene_asset(self._node_item)
        if not outcome.asset:
            QtWidgets.QMessageBox.warning(_resolve_window(self._node_item) or self, "Groom Guide Sim", outcome.detail)
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if callable(handler):
            try:
                handler([dict(outcome.asset)], frame=True)
            except TypeError:
                handler([dict(outcome.asset)])
        QtCore.QTimer.singleShot(250, self._refresh_status)


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
    body = GroomGuideSimWidget(node_item)
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


GROOM_GUIDE_SIM_SPEC = Spec(
    stripe_color="#7a5842",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "GROOM_GUIDE_SIM_BODY_H",
    "GROOM_GUIDE_SIM_NODE_W",
    "GROOM_GUIDE_SIM_SPEC",
    "GroomGuideSimBuildOutcome",
    "KIND_ALIASES",
    "build_groom_guide_sim_scene_asset",
    "build_ports",
    "render_node_body",
]
