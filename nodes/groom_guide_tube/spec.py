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
from echograph.rigging.groom_guide_tube import (
    build_tube_topology,
    normalize_radius_profile,
    radius_profile_to_json,
)
from echograph.rigging.xpbd_strand import curves_to_line_points, root_indices_for_curves

try:
    from nodes.fx.spec import FxRampWidget
except Exception:
    FxRampWidget = None  # type: ignore


KIND_ALIASES = {
    "groom_guide_tube",
    "groom guide tube",
    "groom_guides_tube",
    "groom guides tube",
    "hair_guide_tube",
    "hair guide tube",
    "hair_guides_tube",
    "hair guides tube",
    "guide_tube",
    "guide tube",
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
GROOM_GUIDE_SIM_KIND_ALIASES = {
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

GROOM_GUIDE_TUBE_NODE_W = 300
GROOM_GUIDE_TUBE_BODY_H = 286
DEFAULT_PROFILE_JSON = radius_profile_to_json([(0.0, 1.0), (1.0, 1.0)])

HIDDEN_PARAMS = {
    "source",
    "path",
    "guides_path",
    "tube_cache_path",
    "enabled",
    "root_radius",
    "tip_radius",
    "radius_profile",
    "sides",
    "segment_subdivisions",
    "cap_root",
    "cap_tip",
    "smooth_normals",
    "show_source_guides",
    "debug_log",
}


@dataclass(frozen=True)
class GroomGuideTubeBuildOutcome:
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


def _ensure_visible_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    for param in params:
        if not isinstance(param, dict) or (param.get("name") or "").strip().lower() != "__ui_hidden_params":
            continue
        hidden = {part.strip().lower() for part in str(param.get("value") or "").split(",") if part.strip()}
        for name in names or []:
            hidden.discard(str(name or "").strip().lower())
        param["value"] = ",".join(sorted(hidden))
        break
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


def _guides_asset_from_item(item) -> tuple[Optional[dict[str, Any]], str]:
    if item is None:
        return None, "Connect guide curves to the guides input."
    kind = _node_kind(item)
    if kind in KIND_ALIASES:
        return None, "Do not connect Groom Guide Tube to itself."
    if kind in GROOM_GUIDE_SIM_KIND_ALIASES:
        try:
            from nodes.groom_guide_sim import spec as sim_spec  # type: ignore

            build = getattr(sim_spec, "build_groom_guide_sim_scene_asset", None)
            outcome = build(item) if callable(build) else None
        except Exception as exc:
            return None, f"Groom Guide Sim build failed: {exc}"
        asset = getattr(outcome, "asset", None)
        detail = str(getattr(outcome, "detail", "") or "")
        return (dict(asset), "") if isinstance(asset, dict) else (None, detail or "Groom Guide Sim did not produce curves.")
    if kind in GROOM_GUIDE_POSE_KIND_ALIASES:
        try:
            from nodes.groom_guide_pose import spec as pose_spec  # type: ignore

            build = getattr(pose_spec, "build_groom_guide_pose_scene_asset", None)
            outcome = build(item) if callable(build) else None
        except Exception as exc:
            return None, f"Groom Guide Pose build failed: {exc}"
        asset = getattr(outcome, "asset", None)
        detail = str(getattr(outcome, "detail", "") or "")
        return (dict(asset), "") if isinstance(asset, dict) else (None, detail or "Groom Guide Pose did not produce curves.")
    if kind in GROOM_DEFORM_KIND_ALIASES:
        try:
            from nodes.groom_deform import spec as deform_spec  # type: ignore

            build = getattr(deform_spec, "build_groom_deform_scene_asset", None)
            outcome = build(item) if callable(build) else None
        except Exception as exc:
            return None, f"Groom Deform build failed: {exc}"
        asset = getattr(outcome, "asset", None)
        detail = str(getattr(outcome, "detail", "") or "")
        return (dict(asset), "") if isinstance(asset, dict) else (None, detail or "Groom Deform did not produce curves.")
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
    return safe.strip("._") or "groom_guide_tube"


def _logs_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    out = root / "logs" / "groom_guide_tube"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _settings_from_model(model) -> dict[str, Any]:
    profile = normalize_radius_profile(_param_value(model, "radius_profile", DEFAULT_PROFILE_JSON))
    return {
        "enabled": _param_bool(model, "enabled", True),
        "root_radius": _param_float(model, "root_radius", 0.006, min_value=0.0, max_value=100000.0),
        "tip_radius": _param_float(model, "tip_radius", 0.0015, min_value=0.0, max_value=100000.0),
        "radius_profile": [[float(x), float(y)] for x, y in profile],
        "sides": _param_int(model, "sides", 8, min_value=3, max_value=64),
        "segment_subdivisions": _param_int(model, "segment_subdivisions", 1, min_value=1, max_value=16),
        "cap_root": _param_bool(model, "cap_root", True),
        "cap_tip": _param_bool(model, "cap_tip", True),
        "smooth_normals": _param_bool(model, "smooth_normals", True),
        "show_source_guides": _param_bool(model, "show_source_guides", False),
        "debug_log": _param_bool(model, "debug_log", False),
    }


def _output_path(node_item, source_asset: dict[str, Any], settings: dict[str, Any]) -> Path:
    model = getattr(node_item, "model", None)
    node_name = _sanitize_name(str(getattr(model, "name", "") or "groom_guide_tube"))
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


def preview_groom_guide_tube_status(node_item) -> GroomGuideTubeBuildOutcome:
    try:
        build_ports(node_item)
    except Exception:
        pass
    settings = _settings_from_model(getattr(node_item, "model", None))
    source_item = _connected_input_item(
        node_item,
        {"guides", "guide", "source"},
        GUIDE_KIND_ALIASES | GROOM_DEFORM_KIND_ALIASES | GROOM_GUIDE_POSE_KIND_ALIASES | GROOM_GUIDE_SIM_KIND_ALIASES,
    )
    if source_item is None:
        return GroomGuideTubeBuildOutcome(None, "error", "Connect a guide source.", {"settings": dict(settings)})
    source_kind = _node_kind(source_item)
    if source_kind in KIND_ALIASES:
        return GroomGuideTubeBuildOutcome(None, "error", "Do not connect Groom Guide Tube to itself.", {"settings": dict(settings)})
    allowed = GUIDE_KIND_ALIASES | GROOM_DEFORM_KIND_ALIASES | GROOM_GUIDE_POSE_KIND_ALIASES | GROOM_GUIDE_SIM_KIND_ALIASES
    if source_kind not in allowed:
        return GroomGuideTubeBuildOutcome(None, "error", f"Unsupported guides input: {source_kind or '<none>'}.", {"settings": dict(settings)})
    label = _node_name(source_item) or source_kind.replace("_", " ").title()
    if not bool(settings.get("enabled", True)):
        detail = f"Tube disabled; passing {label} through."
    else:
        detail = f"Guide source ready: {label}."
    return GroomGuideTubeBuildOutcome({"source": label, "settings": dict(settings)}, "ok", detail, {"settings": dict(settings)})


def build_groom_guide_tube_scene_asset(
    node_item,
    *,
    compute_topology: bool = True,
    write_cache: bool = True,
    update_params: bool = True,
) -> GroomGuideTubeBuildOutcome:
    try:
        build_ports(node_item)
    except Exception:
        pass
    model = getattr(node_item, "model", None)
    settings = _settings_from_model(model)
    source_item = _connected_input_item(
        node_item,
        {"guides", "guide", "source"},
        GUIDE_KIND_ALIASES | GROOM_DEFORM_KIND_ALIASES | GROOM_GUIDE_POSE_KIND_ALIASES | GROOM_GUIDE_SIM_KIND_ALIASES,
    )
    guides_asset, error = _guides_asset_from_item(source_item)
    if not isinstance(guides_asset, dict):
        return GroomGuideTubeBuildOutcome(None, "error", error or "Connect a guide source.", {"settings": dict(settings)})
    curves = _copy_curves(guides_asset.get("curves") or guides_asset.get("bind_curves") or [])
    if not curves:
        return GroomGuideTubeBuildOutcome(None, "error", "Guides input has no curves.", {"settings": dict(settings)})
    line_points = guides_asset.get("line_points")
    line_points_empty = line_points is None
    if not line_points_empty:
        try:
            line_points_empty = len(line_points) <= 0
        except Exception:
            line_points_empty = False
    if line_points_empty:
        line_points = curves_to_line_points(curves)
    root_indices = list(guides_asset.get("root_indices") or [])
    if len(root_indices) != len(curves):
        root_indices = root_indices_for_curves(curves)
    point_groups = dict(guides_asset.get("point_groups") or {})
    point_groups["root"] = list(root_indices)

    topology_debug = {}
    if bool(settings.get("enabled", True)) and bool(compute_topology):
        try:
            topo = build_tube_topology(
                curves,
                root_radius=float(settings.get("root_radius", 0.006) or 0.006),
                tip_radius=float(settings.get("tip_radius", 0.0015) or 0.0015),
                radius_profile=settings.get("radius_profile"),
                sides=int(settings.get("sides", 8) or 8),
                segment_subdivisions=int(settings.get("segment_subdivisions", 1) or 1),
                cap_root=bool(settings.get("cap_root", True)),
                cap_tip=bool(settings.get("cap_tip", True)),
            )
            topology_debug = {
                "tube_vertex_count": int(topo.get("vertex_count", 0) or 0) if isinstance(topo, dict) else 0,
                "tube_triangle_count": int(topo.get("triangle_count", 0) or 0) if isinstance(topo, dict) else 0,
                "tube_error": str(topo.get("error") or "") if isinstance(topo, dict) else "",
            }
        except Exception as exc:
            topology_debug = {"tube_vertex_count": 0, "tube_triangle_count": 0, "tube_error": repr(exc)}

    node_name = str(getattr(model, "name", "") or "Groom Guide Tube").strip() or "Groom Guide Tube"
    source_owner = str(guides_asset.get("source_owner") or guides_asset.get("node") or _node_name(source_item) or "").strip()
    source_node = str(guides_asset.get("node") or _node_name(source_item) or "").strip()
    input_source_kind = str(guides_asset.get("source_kind") or guides_asset.get("kind") or "").strip()
    output_path = _output_path(node_item, guides_asset, settings)
    tube_config = {
        "schema": "qubit.groom_guide_tube.v1",
        "enabled": bool(settings.get("enabled", True)),
        "settings": dict(settings),
        "source_node": source_node,
        "source_kind": input_source_kind,
        "source_owner": source_owner,
        "cache_path": str(output_path),
        "show_source_guides": bool(settings.get("show_source_guides", False)),
    }
    debug = {
        "source_node": source_node,
        "source_kind": _node_kind(source_item) or input_source_kind,
        "source_owner": source_owner,
        "guide_count": int(len(curves)),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "settings": dict(settings),
        "cache_path": str(output_path),
    }
    debug.update(topology_debug)

    payload = {
        "schema": "qubit.groom_guide_tube.v1",
        "kind": "groom_guides",
        "asset_source_kind": "groom_guide_tube",
        "node": node_name,
        "visible": True,
        "tube_cache_path": str(output_path),
        "source_guides_path": str(guides_asset.get("guides_path") or guides_asset.get("path") or ""),
        "source_kind": input_source_kind,
        "source_node": source_node,
        "source_owner": source_owner,
        "settings": dict(settings),
        "guide_count": int(len(curves)),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "root_indices": list(root_indices),
        "guide_bindings": list(guides_asset.get("guide_bindings") or []),
        "curves": curves,
        "line_points": line_points,
        "debug": dict(debug),
        "groom_guide_tube": dict(tube_config),
    }
    if bool(write_cache):
        _write_cache(output_path, payload)
    if bool(update_params):
        _set_param(node_item, "source", str(guides_asset.get("guides_path") or guides_asset.get("source_path") or ""), notify_scene=False)
        _set_param(node_item, "path", str(output_path), notify_scene=False)
        _set_param(node_item, "guides_path", str(output_path), notify_scene=False)
        _set_param(node_item, "tube_cache_path", str(output_path), notify_scene=False)
        _set_param(node_item, "radius_profile", radius_profile_to_json(settings.get("radius_profile")), notify_scene=False)

    asset = {
        "kind": "groom_guides",
        "source_kind": "groom_guide_tube",
        "node": node_name,
        "visible": True,
        "debug_log": bool(settings.get("debug_log", False)),
        "guides_path": str(output_path),
        "tube_cache_path": str(output_path),
        "source_guides_path": str(guides_asset.get("guides_path") or guides_asset.get("path") or ""),
        "source_path": str(guides_asset.get("source_path") or ""),
        "source_owner": source_owner,
        "guide_count": int(len(curves)),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "length": float(guides_asset.get("length", 0.0) or 0.0),
        "hidden_submeshes": list(guides_asset.get("hidden_submeshes") or []),
        "root_indices": list(root_indices),
        "point_groups": dict(point_groups),
        "guide_bindings": list(guides_asset.get("guide_bindings") or []),
        "start_curves": _copy_curves(guides_asset.get("start_curves") or guides_asset.get("bind_curves") or curves),
        "curves": curves,
        "bind_curves": _copy_curves(guides_asset.get("bind_curves") or curves),
        "line_points": line_points,
        "debug": debug,
        "groom_guide_tube": dict(tube_config),
    }
    for key in ("groom_deform", "groom_guide_pose", "groom_guide_sim", "groom_collider", "groom_guide_sim_settings"):
        if isinstance(guides_asset.get(key), dict):
            asset[key] = dict(guides_asset.get(key) or {})
    detail = (
        f"Tube ready: {len(curves)} guide curve(s), "
        f"{int(topology_debug.get('tube_vertex_count', 0) or 0)} vertices."
    )
    if not bool(settings.get("enabled", True)):
        detail = f"Tube disabled; passing {len(curves)} guide curve(s) through."
    elif not bool(compute_topology):
        detail = f"Tube ready: {len(curves)} guide curve(s)."
    elif topology_debug.get("tube_error"):
        detail = f"Guide tube warning: {topology_debug.get('tube_error')}"
    return GroomGuideTubeBuildOutcome(asset, "ok", detail, debug)


def build_ports(node_item) -> None:
    for name, default in (
        ("guides", ""),
        ("source", ""),
        ("path", ""),
        ("guides_path", ""),
        ("tube_cache_path", ""),
        ("enabled", "1"),
        ("root_radius", "0.006"),
        ("tip_radius", "0.0015"),
        ("radius_profile", DEFAULT_PROFILE_JSON),
        ("sides", "8"),
        ("segment_subdivisions", "1"),
        ("cap_root", "1"),
        ("cap_tip", "1"),
        ("smooth_normals", "1"),
        ("show_source_guides", "0"),
        ("debug_log", "0"),
    ):
        _ensure_param(node_item, name, default)
    try:
        setattr(getattr(node_item, "model", None), "_named_inputs", ["guides"])
        setattr(node_item, "_default_named_input", "guides")
        setattr(node_item, "_show_default_input_with_named", False)
    except Exception:
        pass
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("guides")
    _ensure_hidden_params(getattr(node_item, "model", None), HIDDEN_PARAMS)
    _ensure_visible_params(getattr(node_item, "model", None), ["guides"])


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


class GroomGuideTubeWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending = False
        self._float_text_dirty: set[str] = set()
        self._ensure_defaults()
        self._build_ui()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    def sizeHint(self):
        return QtCore.QSize(GROOM_GUIDE_TUBE_NODE_W, GROOM_GUIDE_TUBE_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(GROOM_GUIDE_TUBE_NODE_W, GROOM_GUIDE_TUBE_BODY_H)

    def _ensure_defaults(self) -> None:
        build_ports(self._node_item)

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(5)

        self._enabled = QtWidgets.QCheckBox("Enabled")
        self._enabled.setStyleSheet("color:#e2e8f0;")
        layout.addWidget(self._enabled, 0)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        self._root_radius = self._double_box(0.0, 100000.0, 4, 0.001)
        self._tip_radius = self._double_box(0.0, 100000.0, 4, 0.001)
        self._sides = self._int_box(3, 64)
        self._subdiv = self._int_box(1, 16)
        grid.addWidget(self._label("Root"), 0, 0)
        grid.addWidget(self._root_radius, 0, 1)
        grid.addWidget(self._label("Tip"), 0, 2)
        grid.addWidget(self._tip_radius, 0, 3)
        grid.addWidget(self._label("Sides"), 1, 0)
        grid.addWidget(self._sides, 1, 1)
        grid.addWidget(self._label("Subdiv"), 1, 2)
        grid.addWidget(self._subdiv, 1, 3)
        layout.addLayout(grid)

        ramp_row = QtWidgets.QHBoxLayout()
        ramp_row.setContentsMargins(0, 0, 0, 0)
        ramp_row.setSpacing(4)
        ramp_label = QtWidgets.QLabel("Thickness")
        ramp_label.setStyleSheet("color:#cbd5e1;")
        ramp_row.addWidget(ramp_label, 0)
        ramp_row.addStretch(1)
        self._reset_ramp = QtWidgets.QPushButton("Flat")
        self._reset_ramp.setFixedHeight(18)
        self._reset_ramp.setStyleSheet(
            "QPushButton{background:#1e293b;color:#e2e8f0;border-radius:4px;padding:1px 8px;}"
            "QPushButton:hover{background:#334155;}"
        )
        ramp_row.addWidget(self._reset_ramp, 0)
        layout.addLayout(ramp_row)

        if FxRampWidget is not None:
            self._ramp = FxRampWidget(normalize_radius_profile(_param_value(getattr(self._node_item, "model", None), "radius_profile", DEFAULT_PROFILE_JSON)))
            self._ramp.setStyleSheet("background:#0f1216;border:1px solid #334155;border-radius:6px;")
            self._ramp.pointsChanged.connect(self._on_ramp_changed)
            layout.addWidget(self._ramp, 0)
        else:
            self._ramp = None

        options_row = QtWidgets.QHBoxLayout()
        options_row.setContentsMargins(0, 0, 0, 0)
        options_row.setSpacing(6)
        self._caps = QtWidgets.QCheckBox("Caps")
        self._smooth = QtWidgets.QCheckBox("Smooth")
        self._show_guides = QtWidgets.QCheckBox("Guides")
        for widget in (self._caps, self._smooth, self._show_guides):
            widget.setStyleSheet("color:#cbd5e1;")
            options_row.addWidget(widget, 0)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        self._status = QtWidgets.QLabel("Connect guide curves")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(32)
        self._status.setStyleSheet("color:#cbd5e1;font-size:10px;")
        layout.addWidget(self._status, 0)

        view_row = QtWidgets.QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(6)
        view_row.addStretch(1)
        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(72)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#ffffff;border:1px solid #1d4ed8;"
            "border-radius:4px;padding:3px 10px;font-weight:600;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#475569;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        view_row.addWidget(self._view_btn, 0)
        layout.addLayout(view_row)

        self._enabled.toggled.connect(lambda value: self._set_bool("enabled", bool(value)))
        try:
            self._root_radius.lineEdit().textEdited.connect(lambda _text: self._mark_float_text_dirty("root_radius"))
            self._tip_radius.lineEdit().textEdited.connect(lambda _text: self._mark_float_text_dirty("tip_radius"))
        except Exception:
            pass
        self._root_radius.valueChanged.connect(lambda value: self._on_float_value_changed("root_radius", self._root_radius, float(value)))
        self._tip_radius.valueChanged.connect(lambda value: self._on_float_value_changed("tip_radius", self._tip_radius, float(value)))
        self._root_radius.editingFinished.connect(lambda: self._commit_float_box("root_radius", self._root_radius))
        self._tip_radius.editingFinished.connect(lambda: self._commit_float_box("tip_radius", self._tip_radius))
        self._sides.valueChanged.connect(lambda value: self._set_int("sides", int(value)))
        self._subdiv.valueChanged.connect(lambda value: self._set_int("segment_subdivisions", int(value)))
        self._caps.toggled.connect(self._on_caps_changed)
        self._smooth.toggled.connect(lambda value: self._set_bool("smooth_normals", bool(value)))
        self._show_guides.toggled.connect(lambda value: self._set_bool("show_source_guides", bool(value)))
        self._reset_ramp.clicked.connect(self._reset_profile_flat)
        self._sync_from_params()

    @staticmethod
    def _label(text: str):
        label = QtWidgets.QLabel(text)
        label.setStyleSheet("color:#cbd5e1;")
        label.setFixedWidth(44)
        return label

    @staticmethod
    def _double_box(minimum: float, maximum: float, decimals: int, step: float):
        box = QtWidgets.QDoubleSpinBox()
        box.setRange(float(minimum), float(maximum))
        box.setDecimals(int(decimals))
        box.setSingleStep(float(step))
        box.setKeyboardTracking(False)
        box.setFixedWidth(78)
        return box

    @staticmethod
    def _int_box(minimum: int, maximum: int):
        box = QtWidgets.QSpinBox()
        box.setRange(int(minimum), int(maximum))
        box.setKeyboardTracking(False)
        box.setFixedWidth(58)
        return box

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
        for signal_name in ("linksChanged", "paramChanged"):
            signal = getattr(self._scene, signal_name, None)
            if signal is not None:
                try:
                    signal.connect(self._schedule_refresh)
                except Exception:
                    pass
        self._scene_connected = True

    def _schedule_refresh(self, *_args):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(120, self._refresh_status)

    def _sync_from_params(self):
        model = getattr(self._node_item, "model", None)
        widgets = [self._enabled, self._root_radius, self._tip_radius, self._sides, self._subdiv, self._caps, self._smooth, self._show_guides]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self._enabled.setChecked(_param_bool(model, "enabled", True))
            if not self._float_box_is_active(self._root_radius):
                self._root_radius.setValue(_param_float(model, "root_radius", 0.006, min_value=0.0, max_value=100000.0))
            if not self._float_box_is_active(self._tip_radius):
                self._tip_radius.setValue(_param_float(model, "tip_radius", 0.0015, min_value=0.0, max_value=100000.0))
            self._sides.setValue(_param_int(model, "sides", 8, min_value=3, max_value=64))
            self._subdiv.setValue(_param_int(model, "segment_subdivisions", 1, min_value=1, max_value=16))
            self._caps.setChecked(_param_bool(model, "cap_root", True) and _param_bool(model, "cap_tip", True))
            self._smooth.setChecked(_param_bool(model, "smooth_normals", True))
            self._show_guides.setChecked(_param_bool(model, "show_source_guides", False))
            if self._ramp is not None:
                self._ramp.set_points(normalize_radius_profile(_param_value(model, "radius_profile", DEFAULT_PROFILE_JSON)))
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

    def _mark_float_text_dirty(self, name: str) -> None:
        try:
            self._float_text_dirty.add(str(name))
        except Exception:
            pass

    def _on_float_value_changed(self, name: str, box, value: float) -> None:
        try:
            if str(name) in self._float_text_dirty and self._float_box_is_active(box):
                return
        except Exception:
            pass
        self._commit_float_value(name, float(value))

    @staticmethod
    def _float_box_is_active(box) -> bool:
        try:
            if bool(box.hasFocus()):
                return True
            editor = box.lineEdit()
            return bool(editor is not None and editor.hasFocus())
        except Exception:
            return False

    def _commit_float_value(self, name: str, value: float) -> None:
        try:
            value = float(value)
        except Exception:
            return
        current = _param_float(
            getattr(self._node_item, "model", None),
            name,
            value,
            min_value=0.0,
            max_value=100000.0,
        )
        if abs(float(current) - value) <= 1.0e-9:
            return
        self._set_float(name, value)

    def _commit_float_box(self, name: str, box) -> None:
        try:
            box.interpretText()
        except Exception:
            pass
        try:
            self._float_text_dirty.discard(str(name))
        except Exception:
            pass
        try:
            value = float(box.value())
        except Exception:
            return
        self._commit_float_value(name, value)

    def _on_caps_changed(self, value: bool):
        _set_param(self._node_item, "cap_root", "1" if value else "0", notify_scene=True)
        _set_param(self._node_item, "cap_tip", "1" if value else "0", notify_scene=True)
        self._schedule_refresh()

    def _on_ramp_changed(self, points):
        _set_param(self._node_item, "radius_profile", radius_profile_to_json(points), notify_scene=True)
        self._schedule_refresh()

    def _reset_profile_flat(self):
        points = [(0.0, 1.0), (1.0, 1.0)]
        if self._ramp is not None:
            self._ramp.set_points(points)
        self._on_ramp_changed(points)

    def _refresh_status(self):
        self._pending = False
        self._sync_from_params()
        outcome = preview_groom_guide_tube_status(self._node_item)
        self._view_btn.setEnabled(bool(outcome.asset))
        if outcome.status == "ok":
            self._status.setStyleSheet("color:#22c55e;font-size:10px;")
        elif outcome.status == "warning":
            self._status.setStyleSheet("color:#f59e0b;font-size:10px;")
        else:
            self._status.setStyleSheet("color:#ef4444;font-size:10px;")
        self._status.setText(outcome.detail)

    def _on_view_clicked(self):
        outcome = build_groom_guide_tube_scene_asset(self._node_item)
        if not outcome.asset:
            QtWidgets.QMessageBox.warning(_resolve_window(self._node_item) or self, "Groom Guide Tube", outcome.detail)
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
    body = GroomGuideTubeWidget(node_item)
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


GROOM_GUIDE_TUBE_SPEC = Spec(
    stripe_color="#7a5842",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "GROOM_GUIDE_TUBE_BODY_H",
    "GROOM_GUIDE_TUBE_NODE_W",
    "GROOM_GUIDE_TUBE_SPEC",
    "GroomGuideTubeBuildOutcome",
    "KIND_ALIASES",
    "build_groom_guide_tube_scene_asset",
    "build_ports",
    "preview_groom_guide_tube_status",
    "render_node_body",
]
