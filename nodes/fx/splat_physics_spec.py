from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant


KIND_ALIASES = {
    "fx_splat_physics",
    "fx splat physics",
    "splat_physics",
    "splat physics",
    "splatphysics",
}

_SKINNED_SPLAT_PROXY_KIND_ALIASES = {
    "skinned_splat_proxy",
    "skinned splat proxy",
    "skinnedsplatproxy",
    "fbx_to_skinned_splat_proxy",
    "fbx skinned splat proxy",
}
_SPLAT_RENDER_PROXY_TYPES = {
    "skinned_splat",
    "skinned_gaussian_splat",
    "guide_tube_splat",
    "groom_guide_tube_splat",
}
_GUIDE_TUBE_SPLAT_PROXY_TYPES = {"guide_tube_splat", "groom_guide_tube_splat"}
_FX_SPLAT_FX_KIND_ALIASES = {
    "fx_splat_fx",
    "fx splat fx",
    "splat_fx",
    "splat fx",
    "fx_splat_glow",
    "fx splat glow",
    "splat_glow",
    "splat glow",
    "splatglow",
}
_SPLAT_COLORIZE_KIND_ALIASES = {
    "colorize",
    "splat_colorize",
    "splat colorize",
    "fx_splat_colorize",
    "fx splat colorize",
    "gaussian_colorize",
    "gaussian colorize",
}

SPLAT_PHYSICS_NODE_W = 288
SPLAT_PHYSICS_BODY_INSET_X = 8
SPLAT_PHYSICS_BODY_INSET_TOP = 2
SPLAT_PHYSICS_BODY_INSET_BOTTOM = 4
SPLAT_PHYSICS_WIDGET_HINT_H = 516
SPLAT_PHYSICS_NODE_BODY_H = (
    SPLAT_PHYSICS_BODY_INSET_TOP
    + SPLAT_PHYSICS_WIDGET_HINT_H
    + SPLAT_PHYSICS_BODY_INSET_BOTTOM
)

NOISE_MODE_OPTIONS = [
    ("none", "None"),
    ("curl_force", "Curl Force"),
    ("velocity_multiply", "Velocity Multiply"),
    ("follow_multiply", "Follow Multiply"),
    ("drag_multiply", "Drag Multiply"),
]
SETTING_TOOLTIPS = {
    "Enabled": "Turns the splat physics simulation on or off.",
    "Preset": "Loads a saved starting point from splat_physics_presets.json.",
    "Noise Type": "Chooses how noise is applied. None disables noise; curl adds force; multiply modes vary velocity, follow, or drag per splat.",
    "Follow": "How strongly each splat is pulled back toward its skinned target position.",
    "Drag": "Velocity damping. Higher values slow the splats down faster.",
    "Velocity": "How much animation velocity is injected into the splats when the skinned target moves.",
    "Scale": "Global multiplier for scale-sensitive physics settings. Higher values make lag, gravity, and curl force act larger while broadening the noise pattern.",
    "Gravity Y": "Vertical gravity force added to the simulated splats.",
    "Noise": "Noise strength. Higher values create more uneven per-splat motion.",
    "N Scale": "Spatial size of the noise pattern. Lower values make broader noise; higher values make tighter breakup.",
    "N Speed": "How quickly the noise field changes over timeline playback.",
    "Substeps": "Simulation steps per frame. Higher values are smoother but cost more.",
    "Max Lag": "Maximum distance a splat can drift away from its skinned target.",
    "Jump": "Timeline frame jump threshold before the simulation resets to the current pose.",
    "Reset on timeline jumps": "Resets cached positions when scrubbing or jumping far enough on the timeline.",
    "Trail": "Emits extra fading splats from the animated surface.",
    "T Rate": "Fraction of source splats emitted into the trail each frame.",
    "T Life": "Trail lifetime in frames.",
    "T Alpha": "Starting opacity for emitted trail splats.",
    "T Size": "Size multiplier for emitted trail splats.",
    "T Curl": "Curl-noise force applied to trail splats after they emit.",
    "Glow": "Adds a glow overlay driven by the physics lag and velocity this node creates.",
}
PRESET_PARAM_NAMES = {
    "enabled",
    "follow_strength",
    "drag",
    "velocity_scale",
    "physics_scale",
    "noise_mode",
    "noise_strength",
    "noise_scale",
    "noise_speed",
    "gravity_y",
    "substeps",
    "max_lag",
    "reset_on_jump",
    "reset_frame_jump",
    "trail_enabled",
    "trail_spawn_rate",
    "trail_lifetime",
    "trail_alpha",
    "trail_radius_scale",
    "trail_curl",
    "glow_enabled",
    "glow_intensity",
    "glow_radius_boost",
}
DEFAULT_PRESETS = [
    {
        "id": "default",
        "label": "Default",
        "params": {
            "follow_strength": 12.0,
            "drag": 0.35,
            "velocity_scale": 0.15,
            "noise_mode": "none",
            "noise_strength": 0.0,
            "noise_scale": 1.5,
            "noise_speed": 0.75,
            "gravity_y": -0.25,
            "substeps": 2,
            "max_lag": 2.5,
            "reset_frame_jump": 12,
        },
    },
    {
        "id": "none_noise",
        "label": "None Noise",
        "params": {
            "follow_strength": 12.0,
            "drag": 0.35,
            "velocity_scale": 0.15,
            "noise_mode": "none",
            "noise_strength": 0.0,
            "noise_scale": 1.5,
            "noise_speed": 0.75,
            "gravity_y": -0.25,
            "substeps": 2,
            "max_lag": 2.5,
            "reset_frame_jump": 12,
        },
    },
    {
        "id": "soft_lag",
        "label": "Soft Lag",
        "params": {
            "follow_strength": 7.0,
            "drag": 0.8,
            "velocity_scale": 0.22,
            "noise_mode": "curl_force",
            "noise_strength": 0.28,
            "noise_scale": 1.2,
            "noise_speed": 0.55,
            "gravity_y": -0.12,
            "substeps": 2,
            "max_lag": 2.0,
            "reset_frame_jump": 12,
        },
    },
    {
        "id": "wispy_trails",
        "label": "Wispy Trails",
        "params": {
            "follow_strength": 4.5,
            "drag": 0.28,
            "velocity_scale": 0.45,
            "noise_mode": "curl_force",
            "noise_strength": 0.9,
            "noise_scale": 2.2,
            "noise_speed": 1.1,
            "gravity_y": -0.2,
            "substeps": 3,
            "max_lag": 4.0,
            "reset_frame_jump": 12,
        },
    },
    {
        "id": "sticky_skin",
        "label": "Sticky Skin",
        "params": {
            "follow_strength": 28.0,
            "drag": 5.0,
            "velocity_scale": 0.08,
            "noise_mode": "follow_multiply",
            "noise_strength": 0.35,
            "noise_scale": 1.8,
            "noise_speed": 0.35,
            "gravity_y": 0.0,
            "substeps": 2,
            "max_lag": 0.8,
            "reset_frame_jump": 12,
        },
    },
    {
        "id": "turbulent_drag",
        "label": "Turbulent Drag",
        "params": {
            "follow_strength": 12.0,
            "drag": 1.25,
            "velocity_scale": 0.35,
            "noise_mode": "drag_multiply",
            "noise_strength": 0.75,
            "noise_scale": 3.5,
            "noise_speed": 1.4,
            "gravity_y": -0.35,
            "substeps": 4,
            "max_lag": 3.0,
            "reset_frame_jump": 12,
        },
    },
    {
        "id": "velocity_shred",
        "label": "Velocity Shred",
        "params": {
            "follow_strength": 9.0,
            "drag": 0.55,
            "velocity_scale": 0.75,
            "noise_mode": "velocity_multiply",
            "noise_strength": 0.85,
            "noise_scale": 4.0,
            "noise_speed": 1.8,
            "gravity_y": -0.1,
            "substeps": 4,
            "max_lag": 5.0,
            "reset_frame_jump": 12,
        },
    },
    {
        "id": "comet_sparks_large",
        "label": "Comet Sparks Large",
        "params": {
            "enabled": 1,
            "follow_strength": 32.0,
            "drag": 0.12,
            "velocity_scale": 0.1,
            "physics_scale": 1.0,
            "noise_mode": "none",
            "noise_strength": 0.0,
            "noise_scale": 8.0,
            "noise_speed": 2.2,
            "gravity_y": -0.75,
            "substeps": 2,
            "max_lag": 0.25,
            "reset_on_jump": 1,
            "reset_frame_jump": 12,
            "trail_enabled": 1,
            "trail_spawn_rate": 0.025,
            "trail_lifetime": 34,
            "trail_alpha": 0.45,
            "trail_radius_scale": 0.9,
            "trail_curl": 5.0,
        },
    },
]


def _presets_path() -> Path:
    return Path(__file__).resolve().parent / "splat_physics_presets.json"


def _load_presets() -> list[dict]:
    payload = None
    try:
        path = _presets_path()
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = None
    if not isinstance(payload, list):
        payload = list(DEFAULT_PRESETS)
    out: list[dict] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        params = row.get("params")
        if not isinstance(params, dict):
            continue
        label = str(row.get("label") or row.get("id") or "").strip()
        if not label:
            continue
        cleaned = {}
        for key, value in params.items():
            key_text = str(key or "").strip()
            if key_text in PRESET_PARAM_NAMES:
                cleaned[key_text] = value
        if cleaned:
            out.append({"id": str(row.get("id") or label).strip(), "label": label, "params": cleaned})
    return out or list(DEFAULT_PRESETS)


@dataclass(frozen=True)
class SplatPhysicsBuildOutcome:
    asset: Optional[Dict[str, Any]]
    status: str
    detail: str


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value") or "")
    return ""


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = _param_value(model, name).strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _param_int(model, name: str, default: int, *, min_value: int, max_value: int) -> int:
    try:
        value = int(round(float(_param_value(model, name).strip())))
    except Exception:
        value = int(default)
    return max(int(min_value), min(int(max_value), int(value)))


def _param_float(
    model,
    name: str,
    default: float,
    *,
    min_value: float,
    max_value: float,
) -> float:
    try:
        value = float(_param_value(model, name).strip())
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
    key = name.strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _set_param(node_item, name: str, value: str, *, notify_scene: bool = True) -> bool:
    try:
        setter = getattr(node_item, "_set_param_value", None)
        if callable(setter):
            setter(name, str(value), rebuild=False, notify_scene=bool(notify_scene))
            return True
    except Exception:
        pass
    model = getattr(node_item, "model", None)
    if model is None:
        return False
    _ensure_param(node_item, name, str(value))
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
    store_key = "__ui_hidden_params"
    entry = None
    for param in params:
        if isinstance(param, dict) and (param.get("name") or "").strip().lower() == store_key:
            entry = param
            break
    if entry is None:
        entry = {"name": store_key, "value": ""}
        params.append(entry)
    hidden = {
        part.strip().lower()
        for part in str(entry.get("value") or "").split(",")
        if part.strip()
    }
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    try:
        model.params = params
    except Exception:
        pass


def _repo_logs_dir() -> Path:
    try:
        root = Path(__file__).resolve().parents[2]
    except Exception:
        root = Path.cwd()
    out = root / "logs"
    try:
        out.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return out


def _debug_enabled(model) -> bool:
    return _param_bool(model, "debug_log", False)


def _debug_log(model, event: str, **fields) -> None:
    if not _debug_enabled(model):
        return
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": str(event or ""),
    }
    record.update(fields or {})
    try:
        with (_repo_logs_dir() / "fx_splat_physics_debug.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")
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


def _connected_source_item(node_item):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    edges = _ordered_in_edges(scene, node_item)
    chosen = None
    for edge in edges:
        if _edge_dst_name(edge).lower() in {"splats", "splat", "source", "mesh", "path"}:
            chosen = edge
            break
    if chosen is None and edges:
        chosen = edges[0]
    return getattr(chosen, "src", None) if chosen is not None else None


def _node_kind(item) -> str:
    return str(getattr(getattr(item, "model", None), "kind", "") or "").strip().lower()


def _node_name(item) -> str:
    return str(getattr(getattr(item, "model", None), "name", "") or "").strip()


def splat_physics_config_from_model(model) -> Dict[str, Any]:
    noise_mode = _param_value(model, "noise_mode").strip().lower() or "none"
    valid_noise_modes = {key for key, _label in NOISE_MODE_OPTIONS}
    if noise_mode not in valid_noise_modes:
        noise_mode = "none"
    return {
        "enabled": _param_bool(model, "enabled", True),
        "debug_log": _debug_enabled(model),
        "follow_strength": _param_float(model, "follow_strength", 12.0, min_value=0.0, max_value=100.0),
        "drag": _param_float(model, "drag", 0.35, min_value=0.0, max_value=100.0),
        "velocity_scale": _param_float(model, "velocity_scale", 0.15, min_value=0.0, max_value=4.0),
        "physics_scale": _param_float(model, "physics_scale", 1.0, min_value=0.01, max_value=100.0),
        "noise_mode": noise_mode,
        "noise_strength": _param_float(model, "noise_strength", 0.0, min_value=0.0, max_value=20.0),
        "noise_scale": _param_float(model, "noise_scale", 1.5, min_value=0.01, max_value=100.0),
        "noise_speed": _param_float(model, "noise_speed", 0.75, min_value=0.0, max_value=20.0),
        "gravity": [
            _param_float(model, "gravity_x", 0.0, min_value=-50.0, max_value=50.0),
            _param_float(model, "gravity_y", -0.25, min_value=-50.0, max_value=50.0),
            _param_float(model, "gravity_z", 0.0, min_value=-50.0, max_value=50.0),
        ],
        "substeps": _param_int(model, "substeps", 2, min_value=1, max_value=32),
        "max_lag": _param_float(model, "max_lag", 2.5, min_value=0.0, max_value=1000.0),
        "reset_on_jump": _param_bool(model, "reset_on_jump", True),
        "reset_frame_jump": _param_int(model, "reset_frame_jump", 12, min_value=1, max_value=240),
        "trail_enabled": _param_bool(model, "trail_enabled", False),
        "trail_spawn_rate": _param_float(model, "trail_spawn_rate", 0.0, min_value=0.0, max_value=1.0),
        "trail_lifetime": _param_int(model, "trail_lifetime", 24, min_value=1, max_value=240),
        "trail_alpha": _param_float(model, "trail_alpha", 0.35, min_value=0.0, max_value=1.0),
        "trail_radius_scale": _param_float(model, "trail_radius_scale", 0.75, min_value=0.01, max_value=4.0),
        "trail_curl": _param_float(model, "trail_curl", 0.0, min_value=0.0, max_value=20.0),
        "glow_enabled": _param_bool(model, "glow_enabled", False),
        "glow_intensity": _param_float(model, "glow_intensity", 1.0, min_value=0.0, max_value=8.0),
        "glow_radius_boost": _param_float(model, "glow_radius_boost", 0.25, min_value=0.0, max_value=4.0),
    }


def _source_asset_from_item(source_item) -> tuple[Optional[Dict[str, Any]], str]:
    if source_item is None:
        return None, "Connect this node after a Skinned Splat Proxy."
    kind = _node_kind(source_item)
    if kind in KIND_ALIASES:
        return build_splat_physics_scene_asset(source_item).asset, ""
    if kind in _FX_SPLAT_FX_KIND_ALIASES:
        try:
            from nodes.fx import splat_fx_spec as splat_fx_spec  # type: ignore

            outcome = splat_fx_spec.build_splat_fx_scene_asset(source_item)
            return getattr(outcome, "asset", None), str(getattr(outcome, "detail", "") or "")
        except Exception as exc:
            return None, f"FX Splat FX asset build failed: {exc}"
    if kind in _SPLAT_COLORIZE_KIND_ALIASES:
        try:
            from nodes.fx import splat_colorize_spec as splat_colorize_spec  # type: ignore

            outcome = splat_colorize_spec.build_splat_colorize_scene_asset(source_item)
            return getattr(outcome, "asset", None), str(getattr(outcome, "detail", "") or "")
        except Exception as exc:
            return None, f"Colorize asset build failed: {exc}"
    if kind not in _SKINNED_SPLAT_PROXY_KIND_ALIASES:
        return None, f"Unsupported input node kind: {kind or '<none>'}."
    try:
        from nodes.skinned_splat_proxy import spec as proxy_spec  # type: ignore

        build_asset = getattr(proxy_spec, "build_skinned_splat_proxy_scene_asset", None)
        outcome = build_asset(source_item, generate=False) if callable(build_asset) else None
        asset = getattr(outcome, "asset", None)
        detail = str(getattr(outcome, "detail", "") or "")
    except Exception as exc:
        return None, f"Skinned Splat Proxy asset build failed: {exc}"
    if not isinstance(asset, dict):
        return None, detail or "Skinned Splat Proxy did not produce a valid asset."
    render_proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else None
    if not isinstance(render_proxy, dict):
        return None, "Input asset does not contain a splat render proxy."
    if str(render_proxy.get("type") or "").strip().lower() not in _SPLAT_RENDER_PROXY_TYPES:
        return None, "Input render proxy is not a supported splat proxy."
    return dict(asset), detail


def _is_guide_tube_splat_proxy(render_proxy: Dict[str, Any]) -> bool:
    return str((render_proxy or {}).get("type") or "").strip().lower() in _GUIDE_TUBE_SPLAT_PROXY_TYPES


def build_splat_physics_scene_asset(node_item) -> SplatPhysicsBuildOutcome:
    model = getattr(node_item, "model", None)
    source_item = _connected_source_item(node_item)
    source_asset, source_detail = _source_asset_from_item(source_item)
    if not isinstance(source_asset, dict):
        _debug_log(model, "resolve_source_failed", source=_node_name(source_item), error=source_detail)
        return SplatPhysicsBuildOutcome(None, "error", source_detail or "No supported splat proxy input.")

    asset = dict(source_asset)
    render_proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else {}
    render_proxy = dict(render_proxy)
    render_proxy["splat_physics"] = splat_physics_config_from_model(model)
    asset["render_proxy"] = render_proxy
    asset["splat_physics_node"] = str(getattr(model, "name", "") or "")
    if _is_guide_tube_splat_proxy(render_proxy):
        asset["kind"] = "groom_guides"
        asset["source_kind"] = str(asset.get("source_kind") or "groom_guide_tube")
    else:
        asset["kind"] = "fx_splat_physics"
    if _debug_enabled(model):
        asset["debug_log"] = True
    status = "ok"
    detail = "Splat physics ready."
    if str(render_proxy.get("status") or "").strip().lower() == "not_generated":
        status = "warning"
        detail = source_detail or "Proxy files are not generated yet."
    _debug_log(
        model,
        "asset_ready",
        source=_node_name(source_item),
        node=asset.get("node", ""),
        status=status,
        physics=render_proxy.get("splat_physics", {}),
    )
    return SplatPhysicsBuildOutcome(asset, status, detail)


def build_ports(node_item) -> None:
    _ensure_param(node_item, "splats", "")
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "enabled", "1")
    _ensure_param(node_item, "follow_strength", "12.0")
    _ensure_param(node_item, "drag", "0.35")
    _ensure_param(node_item, "velocity_scale", "0.15")
    _ensure_param(node_item, "physics_scale", "1.0")
    _ensure_param(node_item, "noise_mode", "none")
    _ensure_param(node_item, "noise_strength", "0.0")
    _ensure_param(node_item, "noise_scale", "1.5")
    _ensure_param(node_item, "noise_speed", "0.75")
    _ensure_param(node_item, "gravity_x", "0.0")
    _ensure_param(node_item, "gravity_y", "-0.25")
    _ensure_param(node_item, "gravity_z", "0.0")
    _ensure_param(node_item, "substeps", "2")
    _ensure_param(node_item, "max_lag", "2.5")
    _ensure_param(node_item, "reset_on_jump", "1")
    _ensure_param(node_item, "reset_frame_jump", "12")
    _ensure_param(node_item, "trail_enabled", "0")
    _ensure_param(node_item, "trail_spawn_rate", "0.0")
    _ensure_param(node_item, "trail_lifetime", "24")
    _ensure_param(node_item, "trail_alpha", "0.35")
    _ensure_param(node_item, "trail_radius_scale", "0.75")
    _ensure_param(node_item, "trail_curl", "0.0")
    _ensure_param(node_item, "glow_enabled", "0")
    _ensure_param(node_item, "glow_intensity", "1.0")
    _ensure_param(node_item, "glow_radius_boost", "0.25")
    _ensure_param(node_item, "debug_log", "0")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [
            "splats",
            "source",
            "path",
            "enabled",
            "follow_strength",
            "drag",
            "velocity_scale",
            "physics_scale",
            "noise_mode",
            "noise_strength",
            "noise_scale",
            "noise_speed",
            "gravity_x",
            "gravity_y",
            "gravity_z",
            "substeps",
            "max_lag",
            "reset_on_jump",
            "reset_frame_jump",
            "trail_enabled",
            "trail_spawn_rate",
            "trail_lifetime",
            "trail_alpha",
            "trail_radius_scale",
            "trail_curl",
            "glow_enabled",
            "glow_intensity",
            "glow_radius_boost",
            "debug_log",
        ],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("splats")


class SplatPhysicsWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._pending = False
        self._updating = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("Connect Skinned Splat Proxy")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status, 0)

        self._enabled = QtWidgets.QCheckBox("Enabled")
        self._enabled.setToolTip(SETTING_TOOLTIPS["Enabled"])
        self._enabled.stateChanged.connect(self._on_enabled_changed)
        layout.addWidget(self._enabled, 0)

        self._presets = _load_presets()
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(6)
        preset_label = QtWidgets.QLabel("Preset")
        preset_label.setToolTip(SETTING_TOOLTIPS["Preset"])
        preset_row.addWidget(preset_label, 0)
        self._preset_combo = QtWidgets.QComboBox()
        self._preset_combo.setToolTip(SETTING_TOOLTIPS["Preset"])
        for preset in self._presets:
            self._preset_combo.addItem(str(preset.get("label") or preset.get("id") or "Preset"), preset)
        preset_row.addWidget(self._preset_combo, 1)
        preset_btn = QtWidgets.QPushButton("Apply")
        preset_btn.setToolTip("Apply the selected preset to the node values.")
        preset_btn.setFixedHeight(22)
        preset_btn.setStyleSheet(
            "QPushButton{background:#1e293b;color:#e2e8f0;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#334155;}"
        )
        preset_btn.clicked.connect(self._apply_selected_preset)
        preset_row.addWidget(preset_btn, 0)
        layout.addLayout(preset_row, 0)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        noise_mode_label = QtWidgets.QLabel("Noise Type")
        noise_mode_label.setToolTip(SETTING_TOOLTIPS["Noise Type"])
        mode_row.addWidget(noise_mode_label, 0)
        self._noise_mode = QtWidgets.QComboBox()
        self._noise_mode.setToolTip(SETTING_TOOLTIPS["Noise Type"])
        for key, label in NOISE_MODE_OPTIONS:
            self._noise_mode.addItem(label, key)
        self._noise_mode.currentIndexChanged.connect(self._on_noise_mode_changed)
        mode_row.addWidget(self._noise_mode, 1)
        layout.addLayout(mode_row, 0)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        def _label(text: str):
            label = QtWidgets.QLabel(text)
            label.setMinimumWidth(64)
            tip = SETTING_TOOLTIPS.get(text)
            if tip:
                label.setToolTip(tip)
            return label

        def _float_box(minimum: float, maximum: float, step: float, decimals: int = 3):
            box = QtWidgets.QDoubleSpinBox()
            box.setDecimals(int(decimals))
            box.setRange(float(minimum), float(maximum))
            box.setSingleStep(float(step))
            box.setKeyboardTracking(False)
            box.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            box.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            return box

        def _int_box(minimum: int, maximum: int):
            box = QtWidgets.QSpinBox()
            box.setRange(int(minimum), int(maximum))
            box.setKeyboardTracking(False)
            box.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            box.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            return box

        self._follow = _float_box(0.0, 100.0, 1.0, decimals=2)
        self._drag = _float_box(0.0, 100.0, 1.0, decimals=2)
        self._velocity = _float_box(0.0, 4.0, 0.05)
        self._physics_scale = _float_box(0.01, 100.0, 0.05)
        self._noise_strength = _float_box(0.0, 20.0, 0.05)
        self._noise_scale = _float_box(0.01, 100.0, 0.05)
        self._noise_speed = _float_box(0.0, 20.0, 0.05)
        self._gravity_y = _float_box(-50.0, 50.0, 0.05)
        self._substeps = _int_box(1, 32)
        self._max_lag = _float_box(0.0, 1000.0, 0.05)
        self._reset_jump = _int_box(1, 240)
        self._trail_spawn_rate = _float_box(0.0, 1.0, 0.01)
        self._trail_lifetime = _int_box(1, 240)
        self._trail_alpha = _float_box(0.0, 1.0, 0.05, decimals=2)
        self._trail_radius_scale = _float_box(0.01, 4.0, 0.05)
        self._trail_curl = _float_box(0.0, 20.0, 0.05)
        self._glow_intensity = _float_box(0.0, 8.0, 0.05)
        self._glow_radius_boost = _float_box(0.0, 4.0, 0.05)
        for widget, tip_key in (
            (self._follow, "Follow"),
            (self._drag, "Drag"),
            (self._velocity, "Velocity"),
            (self._physics_scale, "Scale"),
            (self._gravity_y, "Gravity Y"),
            (self._noise_strength, "Noise"),
            (self._noise_scale, "N Scale"),
            (self._noise_speed, "N Speed"),
            (self._substeps, "Substeps"),
            (self._max_lag, "Max Lag"),
            (self._reset_jump, "Jump"),
            (self._trail_spawn_rate, "T Rate"),
            (self._trail_lifetime, "T Life"),
            (self._trail_alpha, "T Alpha"),
            (self._trail_radius_scale, "T Size"),
            (self._trail_curl, "T Curl"),
            (self._glow_intensity, "Glow"),
            (self._glow_radius_boost, "Glow"),
        ):
            widget.setToolTip(SETTING_TOOLTIPS[tip_key])

        grid.addWidget(_label("Follow"), 0, 0)
        grid.addWidget(self._follow, 0, 1)
        grid.addWidget(_label("Drag"), 0, 2)
        grid.addWidget(self._drag, 0, 3)
        grid.addWidget(_label("Velocity"), 1, 0)
        grid.addWidget(self._velocity, 1, 1)
        grid.addWidget(_label("Gravity Y"), 1, 2)
        grid.addWidget(self._gravity_y, 1, 3)
        grid.addWidget(_label("Scale"), 2, 0)
        grid.addWidget(self._physics_scale, 2, 1)
        grid.addWidget(_label("Noise"), 2, 2)
        grid.addWidget(self._noise_strength, 2, 3)
        grid.addWidget(_label("N Scale"), 3, 0)
        grid.addWidget(self._noise_scale, 3, 1)
        grid.addWidget(_label("N Speed"), 3, 2)
        grid.addWidget(self._noise_speed, 3, 3)
        grid.addWidget(_label("Substeps"), 4, 0)
        grid.addWidget(self._substeps, 4, 1)
        grid.addWidget(_label("Max Lag"), 4, 2)
        grid.addWidget(self._max_lag, 4, 3)
        grid.addWidget(_label("Jump"), 5, 0)
        grid.addWidget(self._reset_jump, 5, 1)
        grid.addWidget(_label("T Rate"), 5, 2)
        grid.addWidget(self._trail_spawn_rate, 5, 3)
        grid.addWidget(_label("T Life"), 6, 0)
        grid.addWidget(self._trail_lifetime, 6, 1)
        grid.addWidget(_label("T Alpha"), 6, 2)
        grid.addWidget(self._trail_alpha, 6, 3)
        grid.addWidget(_label("T Size"), 7, 0)
        grid.addWidget(self._trail_radius_scale, 7, 1)
        grid.addWidget(_label("T Curl"), 7, 2)
        grid.addWidget(self._trail_curl, 7, 3)
        grid.addWidget(_label("Glow"), 8, 0)
        grid.addWidget(self._glow_intensity, 8, 1)
        grid.addWidget(_label("G Size"), 8, 2)
        grid.addWidget(self._glow_radius_boost, 8, 3)
        layout.addLayout(grid, 0)

        self._reset_on_jump = QtWidgets.QCheckBox("Reset on timeline jumps")
        self._reset_on_jump.setToolTip(SETTING_TOOLTIPS["Reset on timeline jumps"])
        self._reset_on_jump.stateChanged.connect(self._on_reset_on_jump_changed)
        layout.addWidget(self._reset_on_jump, 0)

        self._trail_enabled = QtWidgets.QCheckBox("Trail")
        self._trail_enabled.setToolTip(SETTING_TOOLTIPS["Trail"])
        self._trail_enabled.stateChanged.connect(self._on_trail_enabled_changed)
        layout.addWidget(self._trail_enabled, 0)

        self._glow_enabled = QtWidgets.QCheckBox("Glow from physics")
        self._glow_enabled.setToolTip(SETTING_TOOLTIPS["Glow"])
        self._glow_enabled.stateChanged.connect(self._on_glow_enabled_changed)
        layout.addWidget(self._glow_enabled, 0)

        hint = QtWidgets.QLabel("Noise can add curl motion or multiply velocity, follow, or drag per splat so the surface no longer trails as one sheet.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#64748b;font-size:10px;")
        layout.addWidget(hint, 0)

        for widget, key in (
            (self._follow, "follow_strength"),
            (self._drag, "drag"),
            (self._velocity, "velocity_scale"),
            (self._physics_scale, "physics_scale"),
            (self._noise_strength, "noise_strength"),
            (self._noise_scale, "noise_scale"),
            (self._noise_speed, "noise_speed"),
            (self._gravity_y, "gravity_y"),
            (self._max_lag, "max_lag"),
            (self._trail_spawn_rate, "trail_spawn_rate"),
            (self._trail_alpha, "trail_alpha"),
            (self._trail_radius_scale, "trail_radius_scale"),
            (self._trail_curl, "trail_curl"),
            (self._glow_intensity, "glow_intensity"),
            (self._glow_radius_boost, "glow_radius_boost"),
        ):
            widget.valueChanged.connect(lambda value, name=key: self._set_param(name, f"{float(value):.3f}"))
        self._substeps.valueChanged.connect(lambda value: self._set_param("substeps", str(int(value))))
        self._reset_jump.valueChanged.connect(lambda value: self._set_param("reset_frame_jump", str(int(value))))
        self._trail_lifetime.valueChanged.connect(lambda value: self._set_param("trail_lifetime", str(int(value))))

        self._sync_from_params()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    def sizeHint(self):
        return QtCore.QSize(236, SPLAT_PHYSICS_WIDGET_HINT_H)

    def _ensure_scene(self) -> None:
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
        if _param_change_relevant(self._node_item, name):
            self._schedule_refresh()

    def _schedule_refresh(self, *_args):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(80, self._refresh_status)

    def _set_param(self, name: str, value: str, *, notify_scene: bool = True) -> None:
        if self._updating:
            return
        try:
            current = _param_value(getattr(self._node_item, "model", None), name)
            if current == value:
                return
            _set_param(self._node_item, name, value, notify_scene=notify_scene)
        except Exception:
            pass

    def _sync_from_params(self) -> None:
        model = getattr(self._node_item, "model", None)
        widgets = (
            self._enabled,
            self._noise_mode,
            self._follow,
            self._drag,
            self._velocity,
            self._physics_scale,
            self._noise_strength,
            self._noise_scale,
            self._noise_speed,
            self._gravity_y,
            self._substeps,
            self._max_lag,
            self._reset_on_jump,
            self._reset_jump,
            self._trail_enabled,
            self._trail_spawn_rate,
            self._trail_lifetime,
            self._trail_alpha,
            self._trail_radius_scale,
            self._trail_curl,
            self._glow_enabled,
            self._glow_intensity,
            self._glow_radius_boost,
        )
        for widget in widgets:
            try:
                widget.blockSignals(True)
            except Exception:
                pass
        try:
            self._updating = True
            self._enabled.setChecked(_param_bool(model, "enabled", True))
            noise_mode = _param_value(model, "noise_mode").strip().lower() or "none"
            idx = self._noise_mode.findData(noise_mode)
            if idx < 0:
                idx = 0
            self._noise_mode.setCurrentIndex(idx)
            self._follow.setValue(_param_float(model, "follow_strength", 12.0, min_value=0.0, max_value=100.0))
            self._drag.setValue(_param_float(model, "drag", 0.35, min_value=0.0, max_value=100.0))
            self._velocity.setValue(_param_float(model, "velocity_scale", 0.15, min_value=0.0, max_value=4.0))
            self._physics_scale.setValue(_param_float(model, "physics_scale", 1.0, min_value=0.01, max_value=100.0))
            self._noise_strength.setValue(_param_float(model, "noise_strength", 0.0, min_value=0.0, max_value=20.0))
            self._noise_scale.setValue(_param_float(model, "noise_scale", 1.5, min_value=0.01, max_value=100.0))
            self._noise_speed.setValue(_param_float(model, "noise_speed", 0.75, min_value=0.0, max_value=20.0))
            self._gravity_y.setValue(_param_float(model, "gravity_y", -0.25, min_value=-50.0, max_value=50.0))
            self._substeps.setValue(_param_int(model, "substeps", 2, min_value=1, max_value=32))
            self._max_lag.setValue(_param_float(model, "max_lag", 2.5, min_value=0.0, max_value=1000.0))
            self._reset_on_jump.setChecked(_param_bool(model, "reset_on_jump", True))
            self._reset_jump.setValue(_param_int(model, "reset_frame_jump", 12, min_value=1, max_value=240))
            self._trail_enabled.setChecked(_param_bool(model, "trail_enabled", False))
            self._trail_spawn_rate.setValue(_param_float(model, "trail_spawn_rate", 0.0, min_value=0.0, max_value=1.0))
            self._trail_lifetime.setValue(_param_int(model, "trail_lifetime", 24, min_value=1, max_value=240))
            self._trail_alpha.setValue(_param_float(model, "trail_alpha", 0.35, min_value=0.0, max_value=1.0))
            self._trail_radius_scale.setValue(_param_float(model, "trail_radius_scale", 0.75, min_value=0.01, max_value=4.0))
            self._trail_curl.setValue(_param_float(model, "trail_curl", 0.0, min_value=0.0, max_value=20.0))
            self._glow_enabled.setChecked(_param_bool(model, "glow_enabled", False))
            self._glow_intensity.setValue(_param_float(model, "glow_intensity", 1.0, min_value=0.0, max_value=8.0))
            self._glow_radius_boost.setValue(_param_float(model, "glow_radius_boost", 0.25, min_value=0.0, max_value=4.0))
        finally:
            self._updating = False
            for widget in widgets:
                try:
                    widget.blockSignals(False)
                except Exception:
                    pass

    def _refresh_status(self):
        self._pending = False
        self._sync_from_params()
        outcome = build_splat_physics_scene_asset(self._node_item)
        if outcome.status == "ok":
            self._status.setStyleSheet("color:#22c55e;font-size:11px;")
        elif outcome.status == "warning":
            self._status.setStyleSheet("color:#f59e0b;font-size:11px;")
        else:
            self._status.setStyleSheet("color:#ef4444;font-size:11px;")
        self._status.setText(outcome.detail)

    def _on_enabled_changed(self, _state: int):
        self._set_param("enabled", "1" if self._enabled.isChecked() else "0")

    def _apply_selected_preset(self):
        preset = self._preset_combo.currentData()
        self._apply_preset(preset if isinstance(preset, dict) else {})

    def _apply_preset(self, preset: dict):
        params = preset.get("params") if isinstance(preset, dict) else None
        if not isinstance(params, dict):
            return
        filtered = [(str(k), v) for k, v in params.items() if str(k) in PRESET_PARAM_NAMES]
        if not filtered:
            return
        for idx, (name, value) in enumerate(filtered):
            notify = idx == len(filtered) - 1
            if name == "noise_mode":
                raw = str(value or "none").strip().lower()
                valid = {key for key, _label in NOISE_MODE_OPTIONS}
                text = raw if raw in valid else "none"
            elif name in {"enabled", "reset_on_jump", "trail_enabled", "glow_enabled"}:
                if isinstance(value, str):
                    raw = value.strip().lower()
                    text = "1" if raw in {"1", "true", "yes", "on", "y"} else "0"
                else:
                    text = "1" if bool(value) else "0"
            elif name in {"substeps", "reset_frame_jump", "trail_lifetime"}:
                try:
                    text = str(int(round(float(value))))
                except Exception:
                    continue
            else:
                try:
                    text = f"{float(value):.3f}"
                except Exception:
                    continue
            _set_param(self._node_item, name, text, notify_scene=notify)
        self._sync_from_params()
        self._schedule_refresh()

    def _on_noise_mode_changed(self, _index: int):
        mode = str(self._noise_mode.currentData() or "none")
        self._set_param("noise_mode", mode)

    def _on_reset_on_jump_changed(self, _state: int):
        self._set_param("reset_on_jump", "1" if self._reset_on_jump.isChecked() else "0")

    def _on_trail_enabled_changed(self, _state: int):
        self._set_param("trail_enabled", "1" if self._trail_enabled.isChecked() else "0")

    def _on_glow_enabled_changed(self, _state: int):
        self._set_param("glow_enabled", "1" if self._glow_enabled.isChecked() else "0")


def render_node_body(node_item, y_cursor: int) -> int:
    inset_x = SPLAT_PHYSICS_BODY_INSET_X
    inset_top = SPLAT_PHYSICS_BODY_INSET_TOP
    inset_bottom = SPLAT_PHYSICS_BODY_INSET_BOTTOM
    try:
        before = {
            (entry.get("name") or "").strip().lower()
            for entry in (getattr(getattr(node_item, "model", None), "params", None) or [])
            if isinstance(entry, dict)
        }
        build_ports(node_item)
        after = {
            (entry.get("name") or "").strip().lower()
            for entry in (getattr(getattr(node_item, "model", None), "params", None) or [])
            if isinstance(entry, dict)
        }
        if after != before and hasattr(node_item, "_rebuild_deferred"):
            node_item._rebuild_deferred()
    except Exception:
        pass
    try:
        setattr(node_item, "_show_default_input_with_named", True)
    except Exception:
        pass
    body = SplatPhysicsWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(inset_x, y_cursor + inset_top)
    height = max(36, int(body.sizeHint().height()))
    proxy.resize(max(40, int(node_item.width) - (inset_x * 2)), height)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + inset_top + height + inset_bottom


SPLAT_PHYSICS_SPEC = Spec(
    stripe_color="#84cc16",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "KIND_ALIASES",
    "SPLAT_PHYSICS_NODE_BODY_H",
    "SPLAT_PHYSICS_NODE_W",
    "SPLAT_PHYSICS_SPEC",
    "SplatPhysicsBuildOutcome",
    "build_ports",
    "build_splat_physics_scene_asset",
    "render_node_body",
    "splat_physics_config_from_model",
]
