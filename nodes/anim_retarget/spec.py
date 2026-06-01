from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from PySide6 import QtWidgets, QtGui, QtCore
except Exception:
    try:
        from PySide2 import QtWidgets, QtGui, QtCore  # type: ignore
    except Exception:
        QtWidgets = None  # type: ignore[assignment]
        QtGui = None  # type: ignore[assignment]
        QtCore = None  # type: ignore[assignment]

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant
from echograph.services.profiler import profiled

ANIM_RETARGET_KIND_ALIASES: Tuple[str, ...] = (
    "anim_retarget",
    "anim retarget",
    "animretarget",
    "retarget",
)

SOURCE_KIND_ALIASES: Tuple[str, ...] = (
    "mocap_import",
    "mocap import",
    "mocapimport",
    "bvh_import",
    "bvh import",
    "bvhimport",
    "fbx_import",
    "fbx import",
    "fbximport",
)

TARGET_KIND_ALIASES: Tuple[str, ...] = (
    "fbx_import",
    "fbx import",
    "fbximport",
)

TRANSFORM_KIND_ALIASES: Tuple[str, ...] = (
    "transforms",
    "transform",
    "transform_node",
    "transform node",
)

VISIBLE_PORTS: Tuple[str, str] = ("source", "target")
HIDDEN_PARAMS: Tuple[str, ...] = (
    "joint_map",
    "joint_handle_scale",
    "joint_curve_thickness",
    "display_params_version",
    "preview_target_mesh",
    "source_zero_rotations",
    "pelvis_constraint_source",
    "pelvis_constraint_target",
    "pelvis_constraint_mode",
    "target_pose_offsets",
    "target_pose_selected_joint",
    "root_source",
    "root_target",
    "scale_mode",
    "rotation_mode",
    "debug_log",
)

JOINT_HANDLE_SCALE_PARAM = "joint_handle_scale"
JOINT_CURVE_THICKNESS_PARAM = "joint_curve_thickness"
DISPLAY_PARAMS_VERSION_PARAM = "display_params_version"
PREVIEW_TARGET_MESH_PARAM = "preview_target_mesh"
SOURCE_REST_POSE_PARAM = "source_zero_rotations"
PELVIS_CONSTRAINT_SOURCE_PARAM = "pelvis_constraint_source"
PELVIS_CONSTRAINT_TARGET_PARAM = "pelvis_constraint_target"
PELVIS_CONSTRAINT_MODE_PARAM = "pelvis_constraint_mode"
TARGET_POSE_OFFSETS_PARAM = "target_pose_offsets"
TARGET_POSE_SELECTED_JOINT_PARAM = "target_pose_selected_joint"
JOINT_HANDLE_SCALE_DEFAULT = 1.0
JOINT_CURVE_THICKNESS_DEFAULT = 2.4
JOINT_HANDLE_SCALE_MIN = 0.05
JOINT_HANDLE_SCALE_MAX = 25.0
JOINT_CURVE_THICKNESS_MIN = 0.5
JOINT_CURVE_THICKNESS_MAX = 10.0


def _repo_logs_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    out = root / "logs"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _retarget_debug_log(event: str, **fields) -> None:
    try:
        payload = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": str(event)}
        payload.update(fields)
        with (_repo_logs_dir() / "anim_retarget_debug.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    except Exception:
        pass


def _points_summary(points, *, limit: int = 5) -> Dict[str, Any]:
    rows: List[Tuple[float, float, float]] = []
    for point in list(points or []):
        try:
            if len(point) < 3:
                continue
            rows.append((float(point[0]), float(point[1]), float(point[2])))
        except Exception:
            continue
    if not rows:
        return {"count": 0, "bounds_min": None, "bounds_max": None, "diag": 0.0, "first": []}
    xs = [row[0] for row in rows]
    ys = [row[1] for row in rows]
    zs = [row[2] for row in rows]
    bmin = (min(xs), min(ys), min(zs))
    bmax = (max(xs), max(ys), max(zs))
    diag = math.sqrt(
        ((bmax[0] - bmin[0]) ** 2)
        + ((bmax[1] - bmin[1]) ** 2)
        + ((bmax[2] - bmin[2]) ** 2)
    )
    return {
        "count": len(rows),
        "bounds_min": [round(v, 6) for v in bmin],
        "bounds_max": [round(v, 6) for v in bmax],
        "diag": round(float(diag), 6),
        "first": [[round(v, 6) for v in row] for row in rows[: max(0, int(limit))]],
    }


@dataclass
class RetargetSourceTargetResult:
    status: str
    source_name: str = ""
    source_kind: str = ""
    target_name: str = ""
    target_kind: str = ""
    source_joint_count: int = 0
    source_clip_count: int = 0
    target_joint_count: int = 0
    target_mesh_count: int = 0
    joint_map_count: int = 0
    source_context: Dict[str, Any] | None = None
    target_context: Dict[str, Any] | None = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def message_lines(self) -> List[str]:
        lines = [f"Status: {self.status.upper()}"]
        lines.append(
            f"source: {self.source_name or '<none>'}"
            + (f" ({self.source_kind})" if self.source_kind else "")
        )
        lines.append(
            f"target: {self.target_name or '<none>'}"
            + (f" ({self.target_kind})" if self.target_kind else "")
        )
        if self.source_name:
            lines.append(
                f"source_context: joints={int(self.source_joint_count)} clips={int(self.source_clip_count)}"
            )
        if self.target_name:
            lines.append(
                f"target_context: joints={int(self.target_joint_count)} meshes={int(self.target_mesh_count)}"
            )
        lines.append(f"joint_map: {int(self.joint_map_count)} links")
        if self.errors:
            lines.append("Errors:")
            lines.extend(f"- {msg}" for msg in self.errors)
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {msg}" for msg in self.warnings)
        return lines


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value") or "").strip()
    return ""


def _param_float(
    model,
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = _param_value(model, name)
    try:
        value = float(raw)
    except Exception:
        value = float(default)
    if not math.isfinite(value):
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return float(value)


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = _param_value(model, name)
    if raw == "":
        return bool(default)
    token = str(raw or "").strip().lower()
    if token in {"1", "true", "yes", "on", "checked"}:
        return True
    if token in {"0", "false", "no", "off", "unchecked"}:
        return False
    return bool(default)


def _retarget_debug_enabled(model) -> bool:
    return _param_bool(model, "debug_log", False)


def _param_vec3(model, name: str, default: Tuple[float, float, float]) -> Tuple[float, float, float]:
    raw = _param_value(model, name)
    try:
        parts = [part.strip() for part in str(raw or "").split(",")]
        if len(parts) >= 3:
            return (float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:
        pass
    return (float(default[0]), float(default[1]), float(default[2]))


def _clean_xform(raw: Any | None = None) -> Dict[str, Tuple[float, float, float]]:
    data = raw if isinstance(raw, dict) else {}

    def _vec(name: str, default: Tuple[float, float, float]) -> Tuple[float, float, float]:
        value = data.get(name, default) if isinstance(data, dict) else default
        try:
            if isinstance(value, str):
                parts = [part.strip() for part in value.split(",")]
                if len(parts) >= 3:
                    return (float(parts[0]), float(parts[1]), float(parts[2]))
                return (float(default[0]), float(default[1]), float(default[2]))
            if len(value) >= 3:
                return (float(value[0]), float(value[1]), float(value[2]))
        except Exception:
            pass
        return (float(default[0]), float(default[1]), float(default[2]))

    return {
        "pos": _vec("pos", (0.0, 0.0, 0.0)),
        "rot": _vec("rot", (0.0, 0.0, 0.0)),
        "scl": _vec("scl", (1.0, 1.0, 1.0)),
    }


def _xform_from_transform_node(node_item) -> Dict[str, Tuple[float, float, float]]:
    model = getattr(node_item, "model", None)
    return {
        "pos": _param_vec3(model, "pos", (0.0, 0.0, 0.0)),
        "rot": _param_vec3(model, "rot", (0.0, 0.0, 0.0)),
        "scl": _param_vec3(model, "scl", (1.0, 1.0, 1.0)),
    }


def _combine_xforms(
    base: Dict[str, Any] | None,
    extra: Dict[str, Any] | None,
) -> Dict[str, Tuple[float, float, float]]:
    a = _clean_xform(base)
    b = _clean_xform(extra)
    return {
        "pos": (
            float(a["pos"][0]) + float(b["pos"][0]),
            float(a["pos"][1]) + float(b["pos"][1]),
            float(a["pos"][2]) + float(b["pos"][2]),
        ),
        "rot": (
            float(a["rot"][0]) + float(b["rot"][0]),
            float(a["rot"][1]) + float(b["rot"][1]),
            float(a["rot"][2]) + float(b["rot"][2]),
        ),
        "scl": (
            float(a["scl"][0]) * float(b["scl"][0]),
            float(a["scl"][1]) * float(b["scl"][1]),
            float(a["scl"][2]) * float(b["scl"][2]),
        ),
    }


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
        except Exception:
            params = []
        setattr(model, "params", params)

    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _set_param_value(model, name: str, value: str) -> None:
    if model is None:
        return
    key = (name or "").strip().lower()
    params = list(getattr(model, "params", None) or [])
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value or "")
            model.params = params
            return
    params.append({"name": name, "value": str(value or "")})
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
    raw = hidden_entry.get("value", "")
    hidden = {tok.strip().lower() for tok in str(raw).split(",") if tok.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _ensure_display_params(node_item) -> None:
    _ensure_param(node_item, JOINT_HANDLE_SCALE_PARAM, f"{JOINT_HANDLE_SCALE_DEFAULT:.2f}")
    _ensure_param(node_item, JOINT_CURVE_THICKNESS_PARAM, f"{JOINT_CURVE_THICKNESS_DEFAULT:.1f}")
    _ensure_param(node_item, PREVIEW_TARGET_MESH_PARAM, "0")
    _ensure_param(node_item, SOURCE_REST_POSE_PARAM, "0")
    _ensure_param(node_item, PELVIS_CONSTRAINT_SOURCE_PARAM, "")
    _ensure_param(node_item, PELVIS_CONSTRAINT_TARGET_PARAM, "")
    _ensure_param(node_item, PELVIS_CONSTRAINT_MODE_PARAM, "none")
    _ensure_param(node_item, TARGET_POSE_OFFSETS_PARAM, "{}")
    _ensure_param(node_item, TARGET_POSE_SELECTED_JOINT_PARAM, "")
    _ensure_param(node_item, DISPLAY_PARAMS_VERSION_PARAM, "")
    model = getattr(node_item, "model", None)
    if model is None:
        return
    version = _param_value(model, DISPLAY_PARAMS_VERSION_PARAM)
    if version == "2":
        return
    raw_scale = _param_value(model, JOINT_HANDLE_SCALE_PARAM)
    if raw_scale.strip() in {"", "0.45", "0.450", "0.4500"}:
        _set_param_value(model, JOINT_HANDLE_SCALE_PARAM, f"{JOINT_HANDLE_SCALE_DEFAULT:.2f}")
    _set_param_value(model, DISPLAY_PARAMS_VERSION_PARAM, "2")


def _ensure_input(node_item, name: str) -> None:
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input(name)
    elif hasattr(node_item, "add_input_port"):
        node_item.add_input_port(name)
    elif hasattr(node_item, "add_input"):
        node_item.add_input(name)


def _ordered_in_edges(scene, item):
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _edge_dst_name(edge) -> str:
    return (
        (getattr(edge, "dst_port_name", None) or "")
        or (getattr(edge, "dst_label", None) or "")
        or (getattr(edge, "dst_name", None) or "")
    ).strip()


def _connected_item_for_port(scene, node_item, port_name: str):
    if scene is None or node_item is None:
        return None, ""
    port_key = (port_name or "").strip().lower()
    matches = []
    for edge in _ordered_in_edges(scene, node_item):
        dst = _edge_dst_name(edge).lower()
        if dst == port_key:
            matches.append(edge)
    if not matches:
        return None, ""
    issue = ""
    if len(matches) > 1:
        issue = f"{port_name}: multiple inputs connected; using first edge."
    return getattr(matches[0], "src", None), issue


def _connected_item_for_ports(scene, node_item, port_names) -> tuple[Any | None, str]:
    if scene is None or node_item is None:
        return None, ""
    wanted = {str(name or "").strip().lower() for name in (port_names or []) if str(name or "").strip()}
    edges = _ordered_in_edges(scene, node_item)
    matches = []
    for edge in edges:
        dst = _edge_dst_name(edge).lower()
        if dst in wanted:
            matches.append(edge)
    if not matches and edges:
        matches = [edges[0]]
    if not matches:
        return None, ""
    issue = ""
    if len(matches) > 1:
        issue = "transform: multiple inputs connected; using first edge."
    return getattr(matches[0], "src", None), issue


def _joint_map_count(model) -> int:
    raw = _param_value(model, "joint_map")
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    return sum(1 for key, value in payload.items() if str(key or "").strip() and str(value or "").strip())


def _joint_map_payload(model) -> Dict[str, str]:
    raw = _param_value(model, "joint_map")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    clean: Dict[str, str] = {}
    for key, value in payload.items():
        source = str(key or "").strip()
        target = str(value or "").strip()
        if source and target:
            clean[source] = target
    return clean


def set_joint_map_link(node_item, source_joint: str, target_joint: str, *, notify_scene: bool = True) -> Dict[str, str]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {}
    source = str(source_joint or "").strip()
    target = str(target_joint or "").strip()
    if not source or not target:
        return _joint_map_payload(model)

    mapping = _joint_map_payload(model)
    mapping[source] = target
    encoded = json.dumps(mapping, sort_keys=True)

    setter = getattr(node_item, "_set_param_value", None)
    if callable(setter):
        try:
            setter("joint_map", encoded, rebuild=False, notify_scene=bool(notify_scene))
        except TypeError:
            try:
                setter("joint_map", encoded)
            except Exception:
                _set_param_value(model, "joint_map", encoded)
    else:
        _set_param_value(model, "joint_map", encoded)
        if notify_scene:
            try:
                scene = node_item.scene()
            except Exception:
                scene = None
            if scene is not None and hasattr(scene, "paramChanged"):
                try:
                    scene.paramChanged.emit(getattr(model, "name", ""), list(getattr(model, "params", []) or []))
                except Exception:
                    pass

    try:
        setattr(model, "_retarget_joint_map", encoded)
    except Exception:
        pass
    return mapping


def remove_joint_map_link(node_item, source_joint: str, target_joint: str = "", *, notify_scene: bool = True) -> Dict[str, str]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {}
    source = str(source_joint or "").strip()
    target = str(target_joint or "").strip()
    if not source:
        return _joint_map_payload(model)

    mapping = _joint_map_payload(model)
    current_target = str(mapping.get(source) or "").strip()
    if source not in mapping or (target and current_target != target):
        return mapping
    removed_target = mapping.pop(source, "")
    encoded = json.dumps(mapping, sort_keys=True)
    try:
        if (
            _param_value(model, PELVIS_CONSTRAINT_SOURCE_PARAM).strip() == source
            and _param_value(model, PELVIS_CONSTRAINT_TARGET_PARAM).strip() == str(removed_target or "").strip()
        ):
            _set_param_value(model, PELVIS_CONSTRAINT_SOURCE_PARAM, "")
            _set_param_value(model, PELVIS_CONSTRAINT_TARGET_PARAM, "")
            _set_param_value(model, PELVIS_CONSTRAINT_MODE_PARAM, "none")
    except Exception:
        pass

    setter = getattr(node_item, "_set_param_value", None)
    if callable(setter):
        try:
            setter("joint_map", encoded, rebuild=False, notify_scene=bool(notify_scene))
        except TypeError:
            try:
                setter("joint_map", encoded)
            except Exception:
                _set_param_value(model, "joint_map", encoded)
    else:
        _set_param_value(model, "joint_map", encoded)
        if notify_scene:
            try:
                scene = node_item.scene()
            except Exception:
                scene = None
            if scene is not None and hasattr(scene, "paramChanged"):
                try:
                    scene.paramChanged.emit(getattr(model, "name", ""), list(getattr(model, "params", []) or []))
                except Exception:
                    pass

    try:
        setattr(model, "_retarget_joint_map", encoded)
    except Exception:
        pass
    return mapping


def set_pelvis_constraint_link(
    node_item,
    source_joint: str,
    target_joint: str,
    *,
    mode: str | None = None,
    notify_scene: bool = True,
) -> Dict[str, str]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {"source": "", "target": "", "mode": "none"}
    source = str(source_joint or "").strip()
    target = str(target_joint or "").strip()
    if not source or not target:
        return _pelvis_constraint_payload(model)

    current_mode = _normalize_pelvis_constraint_mode(_param_value(model, PELVIS_CONSTRAINT_MODE_PARAM))
    next_mode = _normalize_pelvis_constraint_mode(mode if mode is not None else current_mode)
    if next_mode == "none":
        next_mode = "position_offset"

    values = (
        (PELVIS_CONSTRAINT_SOURCE_PARAM, source),
        (PELVIS_CONSTRAINT_TARGET_PARAM, target),
        (PELVIS_CONSTRAINT_MODE_PARAM, next_mode),
    )
    setter = getattr(node_item, "_set_param_value", None)
    notified = False
    if callable(setter):
        for idx, (name, value) in enumerate(values):
            should_notify = bool(notify_scene and idx == len(values) - 1)
            try:
                setter(name, value, rebuild=False, notify_scene=should_notify)
                notified = notified or should_notify
            except TypeError:
                try:
                    setter(name, value)
                    notified = notified or should_notify
                except Exception:
                    _set_param_value(model, name, value)
            except Exception:
                _set_param_value(model, name, value)
    else:
        for name, value in values:
            _set_param_value(model, name, value)

    if notify_scene and not notified:
        try:
            scene = node_item.scene()
        except Exception:
            scene = None
        if scene is not None and hasattr(scene, "paramChanged"):
            try:
                scene.paramChanged.emit(getattr(model, "name", ""), list(getattr(model, "params", []) or []))
            except Exception:
                pass

    return {"source": source, "target": target, "mode": next_mode}


def _clean_vec3(raw, default=(0.0, 0.0, 0.0)) -> Tuple[float, float, float]:
    try:
        if raw is None or len(raw) < 3:
            return (float(default[0]), float(default[1]), float(default[2]))
        return (float(raw[0]), float(raw[1]), float(raw[2]))
    except Exception:
        return (float(default[0]), float(default[1]), float(default[2]))


def _target_pose_offsets_payload(model) -> Dict[str, Dict[str, List[float]]]:
    raw = _param_value(model, TARGET_POSE_OFFSETS_PARAM)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    clean: Dict[str, Dict[str, List[float]]] = {}
    for key, value in payload.items():
        name = str(key or "").strip()
        if not name or not isinstance(value, dict):
            continue
        row: Dict[str, List[float]] = {}
        if "position" in value:
            px, py, pz = _clean_vec3(value.get("position"))
            row["position"] = [float(px), float(py), float(pz)]
        if "rotation" in value:
            rx, ry, rz = _clean_vec3(value.get("rotation"))
            row["rotation"] = [float(rx), float(ry), float(rz)]
        if row:
            clean[name] = row
    return clean


def _set_target_pose_payload(node_item, payload: Dict[str, Dict[str, List[float]]], *, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    encoded = json.dumps(payload if isinstance(payload, dict) else {}, sort_keys=True)
    setter = getattr(node_item, "_set_param_value", None)
    notified = False
    if callable(setter):
        try:
            setter(TARGET_POSE_OFFSETS_PARAM, encoded, rebuild=False, notify_scene=bool(notify_scene))
            notified = bool(notify_scene)
        except TypeError:
            try:
                setter(TARGET_POSE_OFFSETS_PARAM, encoded)
                notified = bool(notify_scene)
            except Exception:
                _set_param_value(model, TARGET_POSE_OFFSETS_PARAM, encoded)
        except Exception:
            _set_param_value(model, TARGET_POSE_OFFSETS_PARAM, encoded)
    else:
        _set_param_value(model, TARGET_POSE_OFFSETS_PARAM, encoded)
    try:
        setattr(model, "_retarget_target_pose_offsets", encoded)
    except Exception:
        pass
    if bool(notify_scene) and not notified:
        try:
            scene = node_item.scene()
        except Exception:
            scene = None
        if scene is not None and hasattr(scene, "paramChanged"):
            try:
                scene.paramChanged.emit(getattr(model, "name", ""), list(getattr(model, "params", []) or []))
            except Exception:
                pass


def set_target_pose_selected_joint(node_item, joint_name: str, *, notify_scene: bool = False) -> str:
    model = getattr(node_item, "model", None)
    if model is None:
        return ""
    name = str(joint_name or "").strip()
    setter = getattr(node_item, "_set_param_value", None)
    if callable(setter):
        try:
            setter(TARGET_POSE_SELECTED_JOINT_PARAM, name, rebuild=False, notify_scene=bool(notify_scene))
        except TypeError:
            try:
                setter(TARGET_POSE_SELECTED_JOINT_PARAM, name)
            except Exception:
                _set_param_value(model, TARGET_POSE_SELECTED_JOINT_PARAM, name)
        except Exception:
            _set_param_value(model, TARGET_POSE_SELECTED_JOINT_PARAM, name)
    else:
        _set_param_value(model, TARGET_POSE_SELECTED_JOINT_PARAM, name)
    return name


def set_target_pose_joint_position(
    node_item,
    joint_name: str,
    position,
    *,
    notify_scene: bool = True,
) -> Dict[str, List[float]]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {}
    name = str(joint_name or "").strip()
    if not name:
        return {}
    px, py, pz = _clean_vec3(position)
    payload = _target_pose_offsets_payload(model)
    row = dict(payload.get(name) or {})
    row["position"] = [float(px), float(py), float(pz)]
    payload[name] = row
    set_target_pose_selected_joint(node_item, name, notify_scene=False)
    _set_target_pose_payload(node_item, payload, notify_scene=bool(notify_scene))
    return row


def set_target_pose_joint_rotation(
    node_item,
    joint_name: str,
    rotation_degrees,
    *,
    notify_scene: bool = True,
) -> Dict[str, List[float]]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {}
    name = str(joint_name or "").strip()
    if not name:
        return {}
    rx, ry, rz = _clean_vec3(rotation_degrees)
    payload = _target_pose_offsets_payload(model)
    row = dict(payload.get(name) or {})
    row["rotation"] = [float(rx), float(ry), float(rz)]
    payload[name] = row
    set_target_pose_selected_joint(node_item, name, notify_scene=False)
    _set_target_pose_payload(node_item, payload, notify_scene=bool(notify_scene))
    return row


def reset_target_pose_joint(node_item, joint_name: str, *, notify_scene: bool = True) -> Dict[str, Dict[str, List[float]]]:
    model = getattr(node_item, "model", None)
    if model is None:
        return {}
    name = str(joint_name or "").strip()
    payload = _target_pose_offsets_payload(model)
    if name:
        payload.pop(name, None)
    _set_target_pose_payload(node_item, payload, notify_scene=bool(notify_scene))
    return payload


def reset_target_pose_all(node_item, *, notify_scene: bool = True) -> Dict[str, Dict[str, List[float]]]:
    _set_target_pose_payload(node_item, {}, notify_scene=bool(notify_scene))
    return {}


def _list_clips(animation_result) -> List[Any]:
    if animation_result is None:
        return []
    try:
        return list(getattr(animation_result, "clips", []) or [])
    except Exception:
        return []


def _joint_count(skeleton) -> int:
    if skeleton is None:
        return 0
    try:
        return int(len(getattr(skeleton, "joints", []) or []))
    except Exception:
        return 0


def _joint_parent_index(joint) -> int:
    try:
        value = getattr(joint, "parent_index", -1)
        if value is None:
            return -1
        return int(value)
    except Exception:
        return -1


def _mesh_count(meshes) -> int:
    try:
        return int(len(list(meshes or [])))
    except Exception:
        return 0


def _extend_prefixed(target: List[str], prefix: str, messages) -> None:
    for msg in list(messages or []):
        text = str(msg or "").strip()
        if text:
            target.append(f"{prefix}: {text}")


def _mocap_context_from_item(node_item, role: str, errors: List[str], warnings: List[str]) -> Dict[str, Any] | None:
    try:
        from nodes.mocap_import import spec as mocap_spec  # type: ignore
    except Exception as exc:
        errors.append(f"{role}: Mocap Import support is unavailable: {exc}")
        return None

    model = getattr(node_item, "model", None)
    try:
        result = mocap_spec.resolve_mocap_import_source(
            node_item,
            persist=True,
            validate_animation=True,
        )
    except Exception as exc:
        errors.append(f"{role}: mocap validation failed: {exc}")
        return None

    _extend_prefixed(warnings, role, getattr(result, "warnings", []))
    _extend_prefixed(errors, role, getattr(result, "errors", []))

    animation_result = getattr(model, "_mocap_animation_result", None)
    skeleton = getattr(animation_result, "skeleton", None) if animation_result is not None else None
    clips = _list_clips(animation_result)
    clip = clips[0] if clips else None
    if skeleton is None:
        errors.append(f"{role}: source skeleton is unavailable.")
    if clip is None:
        errors.append(f"{role}: source animation clip is unavailable.")

    return {
        "node_item": node_item,
        "model": model,
        "name": str(getattr(model, "name", "") or "").strip(),
        "kind": str(getattr(model, "kind", "") or "").strip().lower(),
        "source_format": "bvh",
        "path": str(getattr(result, "resolved_path", "") or ""),
        "skeleton": skeleton,
        "animation_result": animation_result,
        "clip": clip,
        "clips": clips,
        "meshes": [],
        "joint_count": _joint_count(skeleton),
        "clip_count": int(len(clips)),
        "mesh_count": 0,
    }


def _fbx_context_from_item(
    node_item,
    role: str,
    errors: List[str],
    warnings: List[str],
    *,
    require_clip: bool,
) -> Dict[str, Any] | None:
    try:
        from nodes.fbx_import import spec as fbx_spec  # type: ignore
    except Exception as exc:
        errors.append(f"{role}: FBX Import support is unavailable: {exc}")
        return None

    model = getattr(node_item, "model", None)
    try:
        result = fbx_spec.resolve_fbx_import_sources(
            node_item,
            persist=True,
            validate_bind_data=True,
            validate_animation_data=bool(require_clip),
        )
    except Exception as exc:
        errors.append(f"{role}: FBX validation failed: {exc}")
        return None

    _extend_prefixed(warnings, role, getattr(result, "warnings", []))
    _extend_prefixed(errors, role, getattr(result, "errors", []))

    asset = None
    try:
        build_preview = getattr(fbx_spec, "_build_preview_asset", None)
        if callable(build_preview):
            asset = build_preview(model, result)
    except Exception:
        asset = None

    rig_context = asset.get("fbx_rig_context") if isinstance(asset, dict) else None
    if not isinstance(rig_context, dict):
        rig_context = {}

    bind_result = (
        getattr(model, "_fbx_bind_capture_result", None)
        or getattr(model, "_fbx_bind_rest_result", None)
    )
    animation_result = (
        getattr(model, "_fbx_anim_animated_result", None)
        or getattr(model, "_fbx_anim_rest_result", None)
    )
    skeleton = rig_context.get("skeleton") or (
        getattr(bind_result, "skeleton", None) if bind_result is not None else None
    )
    meshes = rig_context.get("meshes")
    if meshes is None and bind_result is not None:
        try:
            meshes = list(getattr(bind_result, "meshes", []) or [])
        except Exception:
            meshes = []
    if meshes is None:
        meshes = []
    clips = _list_clips(animation_result)
    clip = rig_context.get("clip") or (clips[0] if clips else None)

    if skeleton is None:
        errors.append(f"{role}: FBX skeleton is unavailable.")
    if require_clip and clip is None:
        errors.append(f"{role}: FBX animation clip is unavailable.")

    path = ""
    try:
        effective = getattr(result, "effective_sources", {}) or {}
        if isinstance(effective, dict):
            path = str(effective.get("rest_geometry") or "")
    except Exception:
        path = ""

    return {
        "node_item": node_item,
        "model": model,
        "name": str(getattr(model, "name", "") or "").strip(),
        "kind": str(getattr(model, "kind", "") or "").strip().lower(),
        "source_format": "fbx",
        "path": path,
        "skeleton": skeleton,
        "bind_result": bind_result,
        "animation_result": animation_result,
        "clip": clip,
        "clips": clips,
        "meshes": list(meshes or []),
        "rig_context": rig_context,
        "preview_asset": asset if isinstance(asset, dict) else None,
        "joint_count": _joint_count(skeleton),
        "clip_count": int(len(clips)),
        "mesh_count": _mesh_count(meshes),
    }


def _transform_context_from_item(
    node_item,
    role: str,
    errors: List[str],
    warnings: List[str],
    *,
    target: bool,
    _depth: int = 0,
    _visited: set[int] | None = None,
) -> Dict[str, Any] | None:
    if _visited is None:
        _visited = set()
    node_id = id(node_item)
    if node_id in _visited or _depth > 8:
        errors.append(f"{role}: transform input chain contains a cycle.")
        return None
    _visited.add(node_id)

    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    source_item, issue = _connected_item_for_ports(scene, node_item, ("mesh", "path", "source"))
    if issue:
        warnings.append(f"{role}: {issue}")
    if source_item is None:
        errors.append(f"{role}: Transform node must be connected to an FBX Import or Mocap Import node.")
        return None

    source_model = getattr(source_item, "model", None)
    source_kind = str(getattr(source_model, "kind", "") or "").strip().lower()
    if target:
        context = _context_for_target_item(
            source_item,
            source_kind,
            errors,
            warnings,
            _depth=_depth + 1,
            _visited=_visited,
        )
    else:
        context = _context_for_source_item(
            source_item,
            source_kind,
            errors,
            warnings,
            _depth=_depth + 1,
            _visited=_visited,
        )
    if not isinstance(context, dict):
        if source_model is not None:
            expected = "FBX Import" if target else "Mocap Import or FBX Import"
            errors.append(
                f"{role}: Transform input is connected to unsupported node kind "
                f"'{source_kind or '<none>'}'; expected {expected}."
            )
        return None

    transform_xform = _xform_from_transform_node(node_item)
    out = dict(context)
    out["transform_xform"] = _combine_xforms(out.get("transform_xform"), transform_xform)
    chain = list(out.get("transform_chain") or [])
    model = getattr(node_item, "model", None)
    chain.append(
        {
            "name": str(getattr(model, "name", "") or "").strip(),
            "kind": str(getattr(model, "kind", "") or "").strip().lower(),
            "xform": transform_xform,
        }
    )
    out["transform_chain"] = chain
    out["transform_node_item"] = node_item
    out["transform_node_model"] = model
    return out


def _context_for_source_item(
    node_item,
    kind: str,
    errors: List[str],
    warnings: List[str],
    *,
    _depth: int = 0,
    _visited: set[int] | None = None,
) -> Dict[str, Any] | None:
    if kind in (
        "mocap_import",
        "mocap import",
        "mocapimport",
        "bvh_import",
        "bvh import",
        "bvhimport",
    ):
        return _mocap_context_from_item(node_item, "source", errors, warnings)
    if kind in TARGET_KIND_ALIASES:
        return _fbx_context_from_item(node_item, "source", errors, warnings, require_clip=True)
    if kind in TRANSFORM_KIND_ALIASES:
        return _transform_context_from_item(
            node_item,
            "source",
            errors,
            warnings,
            target=False,
            _depth=_depth,
            _visited=_visited,
        )
    return None


def _context_for_target_item(
    node_item,
    kind: str,
    errors: List[str],
    warnings: List[str],
    *,
    _depth: int = 0,
    _visited: set[int] | None = None,
) -> Dict[str, Any] | None:
    if kind in TARGET_KIND_ALIASES:
        return _fbx_context_from_item(node_item, "target", errors, warnings, require_clip=False)
    if kind in TRANSFORM_KIND_ALIASES:
        return _transform_context_from_item(
            node_item,
            "target",
            errors,
            warnings,
            target=True,
            _depth=_depth,
            _visited=_visited,
        )
    return None


def build_ports(node_item) -> None:
    for port in VISIBLE_PORTS:
        _ensure_param(node_item, port, "")
        _ensure_input(node_item, port)
    _ensure_param(node_item, "joint_map", "{}")
    _ensure_display_params(node_item)
    _ensure_param(node_item, "root_source", "")
    _ensure_param(node_item, "root_target", "")
    _ensure_param(node_item, "scale_mode", "auto")
    _ensure_param(node_item, "rotation_mode", "copy_global_delta")
    _ensure_param(node_item, PREVIEW_TARGET_MESH_PARAM, "0")
    _ensure_param(node_item, SOURCE_REST_POSE_PARAM, "0")
    _ensure_param(node_item, PELVIS_CONSTRAINT_SOURCE_PARAM, "")
    _ensure_param(node_item, PELVIS_CONSTRAINT_TARGET_PARAM, "")
    _ensure_param(node_item, PELVIS_CONSTRAINT_MODE_PARAM, "none")
    _ensure_param(node_item, TARGET_POSE_OFFSETS_PARAM, "{}")
    _ensure_param(node_item, TARGET_POSE_SELECTED_JOINT_PARAM, "")
    _ensure_param(node_item, "debug_log", "0")
    _ensure_hidden_params(getattr(node_item, "model", None), HIDDEN_PARAMS)


def resolve_anim_retarget_inputs(node_item, *, persist: bool = False) -> RetargetSourceTargetResult:
    model = getattr(node_item, "model", None)
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    errors: List[str] = []
    warnings: List[str] = []

    source_item, source_issue = _connected_item_for_port(scene, node_item, "source")
    if source_issue:
        warnings.append(source_issue)
    target_item, target_issue = _connected_item_for_port(scene, node_item, "target")
    if target_issue:
        warnings.append(target_issue)

    source_model = getattr(source_item, "model", None) if source_item is not None else None
    target_model = getattr(target_item, "model", None) if target_item is not None else None

    source_name = str(getattr(source_model, "name", "") or "").strip()
    source_kind = str(getattr(source_model, "kind", "") or "").strip().lower()
    target_name = str(getattr(target_model, "name", "") or "").strip()
    target_kind = str(getattr(target_model, "kind", "") or "").strip().lower()

    if source_model is None:
        errors.append("source: connect a Mocap Import, FBX Import, or Transform node.")
    elif source_kind not in SOURCE_KIND_ALIASES and source_kind not in TRANSFORM_KIND_ALIASES:
        errors.append(f"source: unsupported node kind '{source_kind or '<none>'}'.")

    if target_model is None:
        errors.append("target: connect an FBX Import or Transform node.")
    elif target_kind not in TARGET_KIND_ALIASES and target_kind not in TRANSFORM_KIND_ALIASES:
        errors.append(f"target: unsupported node kind '{target_kind or '<none>'}'; expected FBX Import or Transform.")

    source_context = None
    target_context = None
    if not persist and model is not None:
        cached_source = getattr(model, "_retarget_source_context", None)
        cached_target = getattr(model, "_retarget_target_context", None)
        if isinstance(cached_source, dict):
            source_context = cached_source
        if isinstance(cached_target, dict):
            target_context = cached_target

    if persist:
        if (
            source_model is not None
            and (source_kind in SOURCE_KIND_ALIASES or source_kind in TRANSFORM_KIND_ALIASES)
        ):
            source_context = _context_for_source_item(source_item, source_kind, errors, warnings)
        if (
            target_model is not None
            and (target_kind in TARGET_KIND_ALIASES or target_kind in TRANSFORM_KIND_ALIASES)
        ):
            target_context = _context_for_target_item(target_item, target_kind, errors, warnings)

    source_joint_count = int((source_context or {}).get("joint_count", 0) or 0)
    source_clip_count = int((source_context or {}).get("clip_count", 0) or 0)
    target_joint_count = int((target_context or {}).get("joint_count", 0) or 0)
    target_mesh_count = int((target_context or {}).get("mesh_count", 0) or 0)
    joint_map_count = _joint_map_count(model)
    status = "error" if errors else ("warning" if warnings else "ok")
    result = RetargetSourceTargetResult(
        status=status,
        source_name=source_name,
        source_kind=source_kind,
        target_name=target_name,
        target_kind=target_kind,
        source_joint_count=source_joint_count,
        source_clip_count=source_clip_count,
        target_joint_count=target_joint_count,
        target_mesh_count=target_mesh_count,
        joint_map_count=joint_map_count,
        source_context=source_context,
        target_context=target_context,
        errors=errors,
        warnings=warnings,
    )

    if persist and model is not None:
        try:
            setattr(model, "_retarget_source_item", source_item)
            setattr(model, "_retarget_target_item", target_item)
            setattr(model, "_retarget_source_model", source_model)
            setattr(model, "_retarget_target_model", target_model)
            setattr(model, "_retarget_source_context", source_context)
            setattr(model, "_retarget_target_context", target_context)
            setattr(model, "_retarget_joint_map", _param_value(model, "joint_map") or "{}")
            setattr(model, "_retarget_validation_state", status)
            setattr(model, "_retarget_validation_messages", result.message_lines())
        except Exception:
            pass
    return result


def _skeleton_bind_line_points(skeleton) -> List[Tuple[float, float, float]]:
    try:
        from echograph.rigging.fbx_stage6_debug import evaluate_skeleton_line_points

        sample = evaluate_skeleton_line_points(skeleton, None, 0.0, loop=True)
        points = list(getattr(sample, "line_points", []) or [])
        if points:
            return [
                (float(point[0]), float(point[1]), float(point[2]))
                for point in points
                if len(point) >= 3
            ]
    except Exception:
        pass

    points: List[Tuple[float, float, float]] = []
    positions: List[Tuple[float, float, float]] = []
    try:
        joints = list(getattr(skeleton, "joints", []) or [])
    except Exception:
        joints = []
    for joint in joints:
        try:
            tx, ty, tz = getattr(getattr(joint, "local_bind", None), "translation", (0.0, 0.0, 0.0))
            local = (float(tx), float(ty), float(tz))
        except Exception:
            local = (0.0, 0.0, 0.0)
        parent_index = _joint_parent_index(joint)
        if 0 <= parent_index < len(positions):
            parent = positions[parent_index]
            pos = (parent[0] + local[0], parent[1] + local[1], parent[2] + local[2])
        else:
            pos = local
        positions.append(pos)
        if 0 <= parent_index < len(positions):
            points.append(positions[parent_index])
            points.append(pos)
    return points


def _skeleton_joint_positions(
    skeleton,
    *,
    clip=None,
    prefer_inverse_bind: bool = False,
) -> List[Tuple[float, float, float]]:
    def _diag(rows: List[Tuple[float, float, float]]) -> float:
        if not rows:
            return 0.0
        try:
            summary = _points_summary(rows)
            return float(summary.get("diag", 0.0) or 0.0)
        except Exception:
            return 0.0

    def _inverse_bind_positions() -> List[Tuple[float, float, float]]:
        rows_tcol: List[Tuple[float, float, float]] = []
        rows_trow: List[Tuple[float, float, float]] = []
        try:
            import numpy as _np
        except Exception:
            _np = None
        if _np is None:
            return []
        try:
            joints = list(getattr(skeleton, "joints", []) or [])
        except Exception:
            joints = []
        for joint in joints:
            raw_inv = tuple(getattr(joint, "inverse_bind_matrix", ()) or ())
            if len(raw_inv) != 16:
                continue
            try:
                inv_bind = _np.asarray(raw_inv, dtype="f4").reshape(4, 4)
                bind_global = _np.linalg.inv(inv_bind)
                flat = bind_global.reshape(-1)
                rows_tcol.append((float(flat[3]), float(flat[7]), float(flat[11])))
                rows_trow.append((float(flat[12]), float(flat[13]), float(flat[14])))
            except Exception:
                continue
        if not rows_tcol and not rows_trow:
            return []
        return rows_trow if _diag(rows_trow) > _diag(rows_tcol) else rows_tcol

    if bool(prefer_inverse_bind):
        rows = _inverse_bind_positions()
        if rows and _diag(rows) > 1.0e-7:
            return rows

    try:
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time

        evaluation = evaluate_rig_at_time(
            skeleton=skeleton,
            clip=clip,
            time_seconds=0.0,
            loop=True,
        )
        rows: List[Tuple[float, float, float]] = []
        for matrix in list(getattr(evaluation, "global_matrices", []) or []):
            values = tuple(matrix or ())
            if len(values) != 16:
                continue
            rows.append((float(values[3]), float(values[7]), float(values[11])))
        if rows:
            return rows
    except Exception:
        pass

    positions: List[Tuple[float, float, float]] = []
    try:
        joints = list(getattr(skeleton, "joints", []) or [])
    except Exception:
        joints = []
    for joint in joints:
        try:
            tx, ty, tz = getattr(getattr(joint, "local_bind", None), "translation", (0.0, 0.0, 0.0))
            local = (float(tx), float(ty), float(tz))
        except Exception:
            local = (0.0, 0.0, 0.0)
        parent_index = _joint_parent_index(joint)
        if 0 <= parent_index < len(positions):
            parent = positions[parent_index]
            positions.append((parent[0] + local[0], parent[1] + local[1], parent[2] + local[2]))
        else:
            positions.append(local)
    return positions


def _preview_skeleton(context: Dict[str, Any], role: str):
    return context.get("skeleton")


def _skeleton_extent(context: Dict[str, Any] | None) -> float:
    skeleton = (context or {}).get("skeleton")
    if skeleton is None:
        return 1.0
    points = _skeleton_bind_line_points(skeleton)
    if not points:
        return 1.0
    try:
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
        zs = [float(p[2]) for p in points]
        return max(
            1.0,
            max(xs) - min(xs),
            max(ys) - min(ys),
            max(zs) - min(zs),
        )
    except Exception:
        return 1.0


def _preview_rig_context(context: Dict[str, Any], role: str, *, curve_thickness: float) -> Dict[str, Any]:
    base = context.get("rig_context")
    rig = dict(base or {}) if isinstance(base, dict) else {}
    is_source = str(role or "").strip().lower() == "source"
    source_rest_pose = bool(is_source and context.get("retarget_source_rest_pose"))
    rig["skeleton"] = _preview_skeleton(context, role)
    rig["clip"] = None if source_rest_pose else (context.get("clip") if is_source else None)
    if source_rest_pose:
        rig["clips"] = []
    rig["meshes"] = []
    rig["loop"] = True
    rig["mesh_skinning_enabled"] = False
    rig["skin_weight_debug"] = False
    rig["show_capture_joints"] = not is_source
    rig["show_animated_joints"] = bool(is_source)
    if source_rest_pose:
        rig["retarget_static_pose"] = True
        rig["retarget_source_rest_pose"] = True
    else:
        rig.pop("retarget_static_pose", None)
        rig.pop("retarget_source_rest_pose", None)
    rig["joint_line_width"] = float(curve_thickness)
    rig["joint_xray"] = False
    rig["retarget_preview_role"] = "source" if is_source else "target"
    rig["source_format"] = str(context.get("source_format") or "")
    if is_source:
        rig["joint_color"] = (0.10, 0.75, 1.00, 1.0)
    else:
        rig["joint_color"] = (1.00, 0.45, 0.15, 1.0)
    return rig


def _preview_asset_for_context(
    context: Dict[str, Any] | None,
    *,
    owner: str,
    role: str,
    x_offset: float,
    curve_thickness: float,
) -> Dict[str, Any] | None:
    if not isinstance(context, dict):
        return None
    path_text = str(context.get("path") or "").strip()
    preview_asset = context.get("preview_asset")
    if not path_text and isinstance(preview_asset, dict):
        path_text = str(preview_asset.get("path") or "").strip()
    if not path_text or context.get("skeleton") is None:
        return None
    try:
        if not Path(path_text).exists():
            return None
    except Exception:
        return None
    rig_context = _preview_rig_context(context, role, curve_thickness=curve_thickness)
    xform = _clean_xform(context.get("transform_xform") if isinstance(context, dict) else None)
    pos = (
        float(xform["pos"][0]) + float(x_offset),
        float(xform["pos"][1]),
        float(xform["pos"][2]),
    )
    return {
        "path": path_text,
        "texture": "",
        "node": owner,
        "ext": ".bvh",
        "visible": True,
        "xform": {
            "pos": [float(pos[0]), float(pos[1]), float(pos[2])],
            "rot": [float(v) for v in xform["rot"]],
            "scl": [float(v) for v in xform["scl"]],
        },
        "fbx_rig_context": rig_context,
        "fbx_debug_log": bool(rig_context.get("fbx_debug_log", False)),
    }


def _preview_target_animation_asset(
    node_item,
    result: RetargetSourceTargetResult,
    *,
    owner: str,
    curve_thickness: float,
    retarget_clip=None,
) -> Dict[str, Any] | None:
    model = getattr(node_item, "model", None)
    target_context = result.target_context
    source_context = result.source_context
    if not isinstance(source_context, dict) or not isinstance(target_context, dict):
        return None
    if retarget_clip is None:
        retarget_clip = build_anim_retarget_clip(node_item, result)
        if retarget_clip is None:
            return None
    preview_asset = target_context.get("preview_asset")
    asset = dict(preview_asset or {}) if isinstance(preview_asset, dict) else {}
    path_text = str(target_context.get("path") or asset.get("path") or "").strip()
    if not path_text:
        return None
    try:
        if not Path(path_text).exists():
            return None
    except Exception:
        return None

    xform = _clean_xform(target_context.get("transform_xform"))
    rig_context = dict(target_context.get("rig_context") or {})
    original_clip = rig_context.get("clip")
    target_skeleton = target_pose_skeleton_for_model(
        model,
        _retarget_eval_target_skeleton(target_context.get("skeleton")),
    )
    rig_context["skeleton"] = target_skeleton
    rig_context["clip"] = retarget_clip
    rig_context["clips"] = [retarget_clip]
    rig_context["meshes"] = []
    rig_context["loop"] = True
    rig_context["mesh_skinning_enabled"] = False
    rig_context["skin_weight_debug"] = False
    rig_context["show_capture_joints"] = False
    rig_context["show_animated_joints"] = True
    rig_context["joint_line_width"] = float(curve_thickness)
    rig_context["joint_xray"] = False
    rig_context["joint_color"] = (1.00, 0.72, 0.18, 1.0)
    rig_context["retarget_result"] = True
    rig_context["retarget_preview_role"] = "target_animation"
    rig_context["retarget_preview_target_animated"] = True
    rig_context["retarget_source_name"] = str(source_context.get("name") or "")
    rig_context["retarget_target_name"] = str(target_context.get("name") or "")
    rig_context["retarget_joint_map"] = dict(_joint_map_payload(model))
    rig_context["retarget_clip_name"] = str(getattr(retarget_clip, "name", "") or "")
    rig_context["retarget_track_count"] = int(len(getattr(retarget_clip, "tracks", []) or []))
    rig_context["retarget_original_clip_name"] = str(getattr(original_clip, "name", "") or "")
    rig_context["fbx_debug_log"] = bool(rig_context.get("fbx_debug_log", False)) or _param_bool(model, "debug_log", False)

    asset.update(
        {
            "path": path_text,
            "texture": "",
            "node": owner,
            "ext": ".bvh",
            "visible": True,
            "xform": {
                "pos": [float(v) for v in xform["pos"]],
                "rot": [float(v) for v in xform["rot"]],
                "scl": [float(v) for v in xform["scl"]],
            },
            "fbx_rig_context": rig_context,
            "fbx_debug_log": bool(rig_context.get("fbx_debug_log", False)),
        }
    )
    _retarget_debug_log(
        "build_preview_target_animation",
        node=str(getattr(model, "name", "") or ""),
        owner=owner,
        path=path_text,
        clip=str(getattr(retarget_clip, "name", "") or ""),
        original_clip=str(getattr(original_clip, "name", "") or ""),
        tracks=int(len(getattr(retarget_clip, "tracks", []) or [])),
        skeleton_only=True,
        source_transform=dict(source_context.get("transform_xform") or {}),
        target_transform=dict(target_context.get("transform_xform") or {}),
    )
    return asset


def build_anim_retarget_preview_assets(
    node_item,
    result: RetargetSourceTargetResult | None = None,
) -> List[Dict[str, Any]]:
    if result is None:
        result = resolve_anim_retarget_inputs(node_item, persist=True)
    source_context = result.source_context
    target_context = result.target_context
    if not isinstance(source_context, dict) or not isinstance(target_context, dict):
        return []

    model = getattr(node_item, "model", None)
    handle_scale = _param_float(
        model,
        JOINT_HANDLE_SCALE_PARAM,
        JOINT_HANDLE_SCALE_DEFAULT,
        minimum=JOINT_HANDLE_SCALE_MIN,
        maximum=JOINT_HANDLE_SCALE_MAX,
    )
    curve_thickness = _param_float(
        model,
        JOINT_CURVE_THICKNESS_PARAM,
        JOINT_CURVE_THICKNESS_DEFAULT,
        minimum=JOINT_CURVE_THICKNESS_MIN,
        maximum=JOINT_CURVE_THICKNESS_MAX,
    )
    def _handle_radius_for_context(context: Dict[str, Any]) -> float:
        extent = max(1.0, float(_skeleton_extent(context)))
        base_radius = max(0.05, extent * 0.012)
        max_radius = max(base_radius, min(120.0, extent * 0.10))
        return max(0.02, min(max_radius, base_radius * handle_scale))

    target_pose_context = dict(target_context)
    target_pose_context["skeleton"] = target_pose_skeleton_for_model(
        model,
        _retarget_eval_target_skeleton(target_context.get("skeleton")),
    )
    source_handle_radius = _handle_radius_for_context(source_context)
    target_handle_radius = _handle_radius_for_context(target_pose_context)
    handle_radius = max(source_handle_radius, target_handle_radius)
    base_name = str(getattr(model, "name", "") or "").strip() or "Anim Retarget"
    source_owner = f"{base_name} Source"
    target_owner = f"{base_name} Target"
    source_rest_pose = _param_bool(model, SOURCE_REST_POSE_PARAM, False)
    preview_target_animation = _param_bool(model, PREVIEW_TARGET_MESH_PARAM, False)
    retarget_preview_clip = build_anim_retarget_clip(node_item, result) if preview_target_animation else None
    source_preview_context = _source_preview_context(
        source_context,
        rest_pose=bool(source_rest_pose),
    )
    source_asset = _preview_asset_for_context(
        source_preview_context,
        owner=source_owner,
        role="source",
        x_offset=0.0,
        curve_thickness=curve_thickness,
    )
    if preview_target_animation:
        target_asset = _preview_target_animation_asset(
            node_item,
            result,
            owner=target_owner,
            curve_thickness=curve_thickness,
            retarget_clip=retarget_preview_clip,
        )
        if target_asset is None:
            target_asset = _preview_asset_for_context(
                target_pose_context,
                owner=target_owner,
                role="target",
                x_offset=0.0,
                curve_thickness=curve_thickness,
            )
    else:
        target_asset = _preview_asset_for_context(
            target_pose_context,
            owner=target_owner,
            role="target",
            x_offset=0.0,
            curve_thickness=curve_thickness,
        )

    def _handles(context: Dict[str, Any], role: str, radius: float) -> List[Dict[str, Any]]:
        skeleton = _preview_skeleton(context, role)
        role_key = str(role or "").strip().lower()
        animate_target = bool(
            role_key == "target"
            and context.get("retarget_preview_target_animated")
            and context.get("clip") is not None
        )
        clip_for_positions = context.get("clip") if (role_key == "source" or animate_target) else None
        positions = _skeleton_joint_positions(
            skeleton,
            clip=clip_for_positions,
            prefer_inverse_bind=bool(role_key == "target" and not animate_target),
        )
        try:
            joints = list(getattr(skeleton, "joints", []) or [])
        except Exception:
            joints = []
        rows: List[Dict[str, Any]] = []
        for idx, joint in enumerate(joints):
            if idx >= len(positions):
                break
            name = str(getattr(joint, "name", "") or "").strip()
            if not name:
                continue
            px, py, pz = positions[idx]
            rows.append(
                {
                    "role": role,
                    "name": name,
                    "index": int(idx),
                    "position": [float(px), float(py), float(pz)],
                    "radius": float(radius),
                }
            )
        return rows

    source_handles = _handles(source_preview_context, "source", source_handle_radius)
    target_handle_context = target_pose_context
    if retarget_preview_clip is not None:
        target_handle_context = dict(target_pose_context)
        target_handle_context["clip"] = retarget_preview_clip
        target_handle_context["retarget_preview_target_animated"] = True
    target_handles = _handles(target_handle_context, "target", target_handle_radius)
    preview_asset = {
        "kind": "anim_retarget_preview",
        "node": f"{base_name} Mapping",
        "visible": True,
        "source_owner": source_owner,
        "target_owner": target_owner,
        "retarget_node_item": node_item,
        "retarget_node_model": model,
        "joint_map": _param_value(model, "joint_map") or "{}",
        "source_handles": source_handles,
        "target_handles": target_handles,
        "handle_radius": handle_radius,
        "curve_thickness": curve_thickness,
    }
    target_log_skeleton = target_pose_context.get("skeleton")
    _retarget_debug_log(
        "build_preview_assets",
        node=base_name,
        source_name=result.source_name,
        source_kind=result.source_kind,
        target_name=result.target_name,
        target_kind=result.target_kind,
        source_joint_count=result.source_joint_count,
        target_joint_count=result.target_joint_count,
        source_clip_count=result.source_clip_count,
        handle_scale=round(handle_scale, 6),
        handle_radius=round(handle_radius, 6),
        source_handle_radius=round(source_handle_radius, 6),
        target_handle_radius=round(target_handle_radius, 6),
        curve_thickness=round(curve_thickness, 6),
        source_pose=(
            str(source_preview_context.get("retarget_source_reference_pose_kind") or "rest_bind")
            if source_rest_pose
            else "animated_clip"
        ),
        target_pose="retarget_animation" if preview_target_animation else "capture_inverse_bind",
        source_rest_pose=bool(source_rest_pose),
        pelvis_constraint=_pelvis_constraint_payload(model),
        preview_target_animation=bool(preview_target_animation),
        source_transform=dict(source_context.get("transform_xform") or {}),
        target_transform=dict(target_context.get("transform_xform") or {}),
        source_skeleton=_points_summary(
            _skeleton_joint_positions(
                source_preview_context.get("skeleton"),
                clip=source_preview_context.get("clip"),
            )
        ),
        target_skeleton=_points_summary(
            _skeleton_joint_positions(
                target_log_skeleton,
                clip=retarget_preview_clip if preview_target_animation else None,
                prefer_inverse_bind=not bool(preview_target_animation),
            )
        ),
        source_handles=_points_summary([row.get("position") for row in source_handles]),
        target_handles=_points_summary([row.get("position") for row in target_handles]),
    )
    _maybe_write_anim_retarget_joint_debug_snapshot(node_item, result, reason="preview")
    return [asset for asset in (source_asset, target_asset, preview_asset) if isinstance(asset, dict)]


def _quat_normalize(q) -> Tuple[float, float, float, float]:
    try:
        x, y, z, w = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
    except Exception:
        return (0.0, 0.0, 0.0, 1.0)
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= 1.0e-12:
        return (0.0, 0.0, 0.0, 1.0)
    inv = 1.0 / length
    return (x * inv, y * inv, z * inv, w * inv)


def _quat_dot(a, b) -> float:
    ax, ay, az, aw = _quat_normalize(a)
    bx, by, bz, bw = _quat_normalize(b)
    return (ax * bx) + (ay * by) + (az * bz) + (aw * bw)


def _quat_mul(a, b) -> Tuple[float, float, float, float]:
    ax, ay, az, aw = _quat_normalize(a)
    bx, by, bz, bw = _quat_normalize(b)
    return _quat_normalize(
        (
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        )
    )


def _quat_inverse(q) -> Tuple[float, float, float, float]:
    x, y, z, w = _quat_normalize(q)
    return (-x, -y, -z, w)


def _quat_from_axis_angle(axis: Tuple[float, float, float], degrees: float) -> Tuple[float, float, float, float]:
    try:
        ax, ay, az = (float(axis[0]), float(axis[1]), float(axis[2]))
        radians = math.radians(float(degrees))
    except Exception:
        return (0.0, 0.0, 0.0, 1.0)
    length = math.sqrt(ax * ax + ay * ay + az * az)
    if length <= 1.0e-12:
        return (0.0, 0.0, 0.0, 1.0)
    half = radians * 0.5
    s = math.sin(half) / length
    return _quat_normalize((ax * s, ay * s, az * s, math.cos(half)))


def _quat_from_euler_degrees(rot: Tuple[float, float, float]) -> Tuple[float, float, float, float]:
    try:
        rx, ry, rz = (float(rot[0]), float(rot[1]), float(rot[2]))
    except Exception:
        return (0.0, 0.0, 0.0, 1.0)
    qx = _quat_from_axis_angle((1.0, 0.0, 0.0), rx)
    qy = _quat_from_axis_angle((0.0, 1.0, 0.0), ry)
    qz = _quat_from_axis_angle((0.0, 0.0, 1.0), rz)
    return _quat_mul(_quat_mul(qx, qy), qz)


_IDENTITY_MATRIX_4X4: Tuple[float, ...] = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)


def _matrix4_mul_row_major(a: Tuple[float, ...], b: Tuple[float, ...]) -> Tuple[float, ...]:
    a00, a01, a02, a03, a10, a11, a12, a13, a20, a21, a22, a23, a30, a31, a32, a33 = a
    b00, b01, b02, b03, b10, b11, b12, b13, b20, b21, b22, b23, b30, b31, b32, b33 = b
    return (
        (a00 * b00) + (a01 * b10) + (a02 * b20) + (a03 * b30),
        (a00 * b01) + (a01 * b11) + (a02 * b21) + (a03 * b31),
        (a00 * b02) + (a01 * b12) + (a02 * b22) + (a03 * b32),
        (a00 * b03) + (a01 * b13) + (a02 * b23) + (a03 * b33),
        (a10 * b00) + (a11 * b10) + (a12 * b20) + (a13 * b30),
        (a10 * b01) + (a11 * b11) + (a12 * b21) + (a13 * b31),
        (a10 * b02) + (a11 * b12) + (a12 * b22) + (a13 * b32),
        (a10 * b03) + (a11 * b13) + (a12 * b23) + (a13 * b33),
        (a20 * b00) + (a21 * b10) + (a22 * b20) + (a23 * b30),
        (a20 * b01) + (a21 * b11) + (a22 * b21) + (a23 * b31),
        (a20 * b02) + (a21 * b12) + (a22 * b22) + (a23 * b32),
        (a20 * b03) + (a21 * b13) + (a22 * b23) + (a23 * b33),
        (a30 * b00) + (a31 * b10) + (a32 * b20) + (a33 * b30),
        (a30 * b01) + (a31 * b11) + (a32 * b21) + (a33 * b31),
        (a30 * b02) + (a31 * b12) + (a32 * b22) + (a33 * b32),
        (a30 * b03) + (a31 * b13) + (a32 * b23) + (a33 * b33),
    )


def _matrix4_inverse_row_major(matrix: Tuple[float, ...]) -> Tuple[float, ...] | None:
    if len(tuple(matrix or ())) != 16:
        return None
    rows = [
        [float(matrix[(r * 4) + c]) for c in range(4)]
        + [1.0 if r == c else 0.0 for c in range(4)]
        for r in range(4)
    ]
    for col in range(4):
        pivot_row = max(range(col, 4), key=lambda r: abs(rows[r][col]))
        pivot = rows[pivot_row][col]
        if abs(pivot) <= 1.0e-12:
            return None
        if pivot_row != col:
            rows[col], rows[pivot_row] = rows[pivot_row], rows[col]

        inv_pivot = 1.0 / pivot
        rows[col] = [value * inv_pivot for value in rows[col]]

        for r in range(4):
            if r == col:
                continue
            factor = rows[r][col]
            if abs(factor) <= 1.0e-16:
                continue
            rows[r] = [
                rows[r][idx] - (factor * rows[col][idx])
                for idx in range(8)
            ]
    return tuple(float(rows[r][4 + c]) for r in range(4) for c in range(4))


def _matrix4_from_joint_transform(xf) -> Tuple[float, ...]:
    try:
        tx, ty, tz = _clean_vec3(getattr(xf, "translation", (0.0, 0.0, 0.0)))
        x, y, z, w = _quat_normalize(getattr(xf, "rotation", (0.0, 0.0, 0.0, 1.0)))
        sx, sy, sz = _clean_vec3(getattr(xf, "scale", (1.0, 1.0, 1.0)), default=(1.0, 1.0, 1.0))
    except Exception:
        tx = ty = tz = 0.0
        x = y = z = 0.0
        w = 1.0
        sx = sy = sz = 1.0

    xx = x * x
    yy = y * y
    zz = z * z
    xy = x * y
    xz = x * z
    yz = y * z
    wx = w * x
    wy = w * y
    wz = w * z

    r00 = 1.0 - (2.0 * (yy + zz))
    r01 = 2.0 * (xy - wz)
    r02 = 2.0 * (xz + wy)
    r10 = 2.0 * (xy + wz)
    r11 = 1.0 - (2.0 * (xx + zz))
    r12 = 2.0 * (yz - wx)
    r20 = 2.0 * (xz - wy)
    r21 = 2.0 * (yz + wx)
    r22 = 1.0 - (2.0 * (xx + yy))
    return (
        r00 * sx, r01 * sy, r02 * sz, tx,
        r10 * sx, r11 * sy, r12 * sz, ty,
        r20 * sx, r21 * sy, r22 * sz, tz,
        0.0, 0.0, 0.0, 1.0,
    )


def _quat_from_rotation_matrix(
    m00: float,
    m01: float,
    m02: float,
    m10: float,
    m11: float,
    m12: float,
    m20: float,
    m21: float,
    m22: float,
) -> Tuple[float, float, float, float]:
    trace = float(m00) + float(m11) + float(m22)
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(max(0.0, 1.0 + m00 - m11 - m22)) * 2.0
        w = (m21 - m12) / s if s else 1.0
        x = 0.25 * s
        y = (m01 + m10) / s if s else 0.0
        z = (m02 + m20) / s if s else 0.0
    elif m11 > m22:
        s = math.sqrt(max(0.0, 1.0 + m11 - m00 - m22)) * 2.0
        w = (m02 - m20) / s if s else 1.0
        x = (m01 + m10) / s if s else 0.0
        y = 0.25 * s
        z = (m12 + m21) / s if s else 0.0
    else:
        s = math.sqrt(max(0.0, 1.0 + m22 - m00 - m11)) * 2.0
        w = (m10 - m01) / s if s else 1.0
        x = (m02 + m20) / s if s else 0.0
        y = (m12 + m21) / s if s else 0.0
        z = 0.25 * s
    return _quat_normalize((x, y, z, w))


def _joint_transform_from_matrix4(matrix: Tuple[float, ...]):
    from echograph.rigging.fbx_canonical import JointTransform

    values = tuple(float(v) for v in tuple(matrix or _IDENTITY_MATRIX_4X4))
    if len(values) != 16:
        return JointTransform()
    m00, m01, m02, m03 = values[0], values[1], values[2], values[3]
    m10, m11, m12, m13 = values[4], values[5], values[6], values[7]
    m20, m21, m22, m23 = values[8], values[9], values[10], values[11]

    sx = math.sqrt((m00 * m00) + (m10 * m10) + (m20 * m20))
    sy = math.sqrt((m01 * m01) + (m11 * m11) + (m21 * m21))
    sz = math.sqrt((m02 * m02) + (m12 * m12) + (m22 * m22))
    if sx <= 1.0e-12:
        sx = 1.0
    if sy <= 1.0e-12:
        sy = 1.0
    if sz <= 1.0e-12:
        sz = 1.0

    q = _quat_from_rotation_matrix(
        m00 / sx,
        m01 / sy,
        m02 / sz,
        m10 / sx,
        m11 / sy,
        m12 / sz,
        m20 / sx,
        m21 / sy,
        m22 / sz,
    )
    xf = JointTransform(
        translation=(float(m03), float(m13), float(m23)),
        rotation=q,
        scale=(float(sx), float(sy), float(sz)),
    )
    xf.validate()
    return xf


def _clone_joint_transform(xf):
    from echograph.rigging.fbx_canonical import JointTransform

    if xf is None:
        return JointTransform()
    try:
        out = JointTransform(
            translation=tuple(getattr(xf, "translation", (0.0, 0.0, 0.0))),
            rotation=tuple(getattr(xf, "rotation", (0.0, 0.0, 0.0, 1.0))),
            scale=tuple(getattr(xf, "scale", (1.0, 1.0, 1.0))),
        )
        out.validate()
        return out
    except Exception:
        return JointTransform()


def target_pose_skeleton_for_model(model, skeleton, *, rebind_inverse_bind: bool = True):
    if skeleton is None:
        return skeleton
    try:
        base_skeleton = getattr(skeleton, "_anim_retarget_target_pose_source_skeleton", None)
        if base_skeleton is not None:
            skeleton = base_skeleton
    except Exception:
        pass
    payload = _target_pose_offsets_payload(model)
    if not payload:
        return skeleton
    rebind_inverse = bool(rebind_inverse_bind)
    try:
        cache_key = json.dumps(
            {
                "offsets": payload,
                "rebind_inverse_bind": bool(rebind_inverse),
            },
            sort_keys=True,
        )
        cached_key = getattr(skeleton, "_anim_retarget_target_pose_cache_key", None)
        cached = getattr(skeleton, "_anim_retarget_target_pose_cache", None)
        if (
            cached_key == cache_key
            and cached is not None
            and getattr(cached, "_anim_retarget_target_pose_version", 0) == 3
        ):
            return cached
    except Exception:
        cache_key = ""
    try:
        from echograph.rigging.fbx_canonical import Joint, SkeletonAsset

        source_joints = list(getattr(skeleton, "joints", []) or [])
        if not source_joints:
            return skeleton
        joints = []
        for idx, joint in enumerate(source_joints):
            name = str(getattr(joint, "name", "") or f"joint_{idx}")
            parent_index = _joint_parent_index(joint)
            local_bind = _clone_joint_transform(getattr(joint, "local_bind", None))
            row = payload.get(name) or {}
            if "rotation" in row:
                rot_offset = _quat_from_euler_degrees(tuple(row.get("rotation") or (0.0, 0.0, 0.0)))
                local_bind.rotation = _quat_mul(local_bind.rotation, rot_offset)
            raw_inv = tuple(getattr(joint, "inverse_bind_matrix", ()) or ())
            if len(raw_inv) != 16:
                raw_inv = _IDENTITY_MATRIX_4X4
            joints.append(
                Joint(
                    name=name,
                    parent_index=int(parent_index),
                    local_bind=local_bind,
                    inverse_bind_matrix=tuple(float(v) for v in raw_inv),
                )
            )

        global_mats: List[Tuple[float, ...]] = []
        for idx, joint in enumerate(joints):
            parent_index = _joint_parent_index(joint)
            local_matrix = _matrix4_from_joint_transform(getattr(joint, "local_bind", None))
            if 0 <= parent_index < idx:
                global_mats.append(_matrix4_mul_row_major(global_mats[parent_index], local_matrix))
            else:
                global_mats.append(local_matrix)

            row = payload.get(str(getattr(joint, "name", "") or "")) or {}
            if "position" not in row:
                continue
            px, py, pz = _clean_vec3(row.get("position"))
            current_global = list(global_mats[idx])
            current_global[3] = float(px)
            current_global[7] = float(py)
            current_global[11] = float(pz)
            desired_global = tuple(float(v) for v in current_global)
            local_matrix = desired_global
            if 0 <= parent_index < idx:
                parent_inv = _matrix4_inverse_row_major(global_mats[parent_index])
                if parent_inv is not None:
                    local_matrix = _matrix4_mul_row_major(parent_inv, desired_global)
            local_bind = _joint_transform_from_matrix4(local_matrix)
            joint.local_bind = local_bind
            if 0 <= parent_index < idx:
                global_mats[idx] = _matrix4_mul_row_major(global_mats[parent_index], _matrix4_from_joint_transform(local_bind))
            else:
                global_mats[idx] = _matrix4_from_joint_transform(local_bind)

        if rebind_inverse:
            for idx, global_matrix in enumerate(global_mats):
                inverse_bind = _matrix4_inverse_row_major(global_matrix)
                if inverse_bind is not None and idx < len(joints):
                    joints[idx].inverse_bind_matrix = tuple(float(v) for v in inverse_bind)

        metadata = dict(getattr(skeleton, "metadata", None) or {})
        metadata["retarget_target_pose_offsets"] = int(len(payload))
        metadata["retarget_target_pose_rebind_inverse_bind"] = bool(rebind_inverse)
        out = SkeletonAsset(
            name=f"{str(getattr(skeleton, 'name', '') or 'TargetSkeleton')}_retarget_pose",
            joints=joints,
            metadata=metadata,
        )
        out.validate()
        try:
            setattr(out, "_anim_retarget_target_pose_source_skeleton", skeleton)
            setattr(out, "_anim_retarget_target_pose_version", 3)
            setattr(out, "_anim_retarget_target_pose_rebind_inverse_bind", bool(rebind_inverse))
        except Exception:
            pass
        try:
            if cache_key:
                setattr(skeleton, "_anim_retarget_target_pose_cache_key", cache_key)
                setattr(skeleton, "_anim_retarget_target_pose_cache", out)
        except Exception:
            pass
        _retarget_debug_log(
            "target_pose_skeleton",
            skeleton=str(getattr(skeleton, "name", "") or ""),
            joint_count=int(len(joints)),
            offset_count=int(len(payload)),
            rebind_inverse_bind=bool(rebind_inverse),
            pose=_points_summary(_skeleton_joint_positions(out, clip=None)),
        )
        return out
    except Exception as exc:
        _retarget_debug_log(
            "target_pose_skeleton_failed",
            skeleton=str(getattr(skeleton, "name", "") or ""),
            error=repr(exc),
        )
        return skeleton


def _source_reference_pose_skeleton(skeleton, clip):
    if skeleton is None or clip is None:
        return skeleton
    try:
        start_time = float(getattr(clip, "start_time", 0.0) or 0.0)
    except Exception:
        start_time = 0.0
    try:
        cache = getattr(skeleton, "_anim_retarget_source_reference_pose_cache", None)
        cache_key = (id(clip), round(float(start_time), 8))
        if isinstance(cache, dict):
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
    except Exception:
        cache = None
        cache_key = None
    try:
        from echograph.rigging.fbx_canonical import Joint, SkeletonAsset
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time

        joints = list(getattr(skeleton, "joints", []) or [])
        if not joints:
            return skeleton
        evaluation = evaluate_rig_at_time(
            skeleton=skeleton,
            clip=clip,
            time_seconds=float(start_time),
            loop=False,
        )
        local_transforms = list(getattr(evaluation, "local_transforms", []) or [])
        if len(local_transforms) != len(joints):
            return skeleton

        new_joints = []
        for idx, joint in enumerate(joints):
            try:
                parent_index = int(getattr(joint, "parent_index", -1))
            except Exception:
                parent_index = -1
            raw_inv = tuple(getattr(joint, "inverse_bind_matrix", ()) or ())
            if len(raw_inv) != 16:
                raw_inv = _IDENTITY_MATRIX_4X4
            new_joints.append(
                Joint(
                    name=str(getattr(joint, "name", "") or f"joint_{idx}"),
                    parent_index=int(parent_index),
                    local_bind=_clone_joint_transform(local_transforms[idx]),
                    inverse_bind_matrix=tuple(float(v) for v in raw_inv),
                )
            )

        metadata = dict(getattr(skeleton, "metadata", None) or {})
        metadata["retarget_source_reference_pose"] = "clip_start"
        metadata["retarget_source_reference_time"] = float(start_time)
        out = SkeletonAsset(
            name=f"{str(getattr(skeleton, 'name', '') or 'SourceSkeleton')}_reference_pose",
            joints=new_joints,
            metadata=metadata,
        )
        out.validate()
        try:
            if not isinstance(cache, dict):
                cache = {}
            if cache_key is not None:
                cache[cache_key] = out
            setattr(skeleton, "_anim_retarget_source_reference_pose_cache", cache)
        except Exception:
            pass
        _retarget_debug_log(
            "source_reference_pose_skeleton",
            skeleton=str(getattr(skeleton, "name", "") or ""),
            clip=str(getattr(clip, "name", "") or ""),
            reference_time=round(float(start_time), 6),
            reference_pose=_points_summary(_skeleton_joint_positions(out, clip=None)),
        )
        return out
    except Exception as exc:
        _retarget_debug_log(
            "source_reference_pose_skeleton_failed",
            skeleton=str(getattr(skeleton, "name", "") or ""),
            clip=str(getattr(clip, "name", "") or ""),
            error=repr(exc),
        )
        return skeleton


def _source_preview_context(context: Dict[str, Any], *, rest_pose: bool) -> Dict[str, Any]:
    if not bool(rest_pose):
        return context
    source_format = str(context.get("source_format") or "").strip().lower()
    if source_format == "bvh" and context.get("clip") is not None:
        reference_skeleton = _source_reference_pose_skeleton(context.get("skeleton"), context.get("clip"))
        pose_kind = "reference_clip_start"
    else:
        reference_skeleton = context.get("skeleton")
        pose_kind = "rest_bind"
    out = dict(context)
    out["skeleton"] = reference_skeleton
    out["clip"] = None
    out["clips"] = []
    out["retarget_source_rest_pose"] = True
    out["retarget_source_reference_pose_kind"] = pose_kind
    rig_context = dict(out.get("rig_context") or {})
    rig_context["skeleton"] = reference_skeleton
    rig_context["clip"] = None
    rig_context["clips"] = []
    rig_context["retarget_static_pose"] = True
    rig_context["retarget_source_rest_pose"] = True
    rig_context["retarget_source_reference_pose_kind"] = pose_kind
    out["rig_context"] = rig_context
    _retarget_debug_log(
        "source_reference_pose_preview",
        skeleton=str(getattr(reference_skeleton, "name", "") or ""),
        source_format=source_format,
        pose_kind=pose_kind,
        reference_pose=_points_summary(_skeleton_joint_positions(reference_skeleton, clip=None)),
    )
    return out


def _retarget_eval_target_skeleton(skeleton):
    if skeleton is None:
        return None
    cached = getattr(skeleton, "_anim_retarget_inverse_bind_eval_skeleton", None)
    if cached is not None and getattr(cached, "_anim_retarget_inverse_bind_eval_version", 0) == 2:
        return cached
    try:
        from echograph.rigging.fbx_canonical import Joint, SkeletonAsset

        joints = list(getattr(skeleton, "joints", []) or [])
        if not joints:
            return skeleton

        bind_globals: List[Tuple[float, ...] | None] = []
        positions: List[Tuple[float, float, float]] = []
        for joint in joints:
            raw_inv = tuple(getattr(joint, "inverse_bind_matrix", ()) or ())
            bind_global = _matrix4_inverse_row_major(raw_inv) if len(raw_inv) == 16 else None
            bind_globals.append(bind_global)
            if bind_global is not None:
                positions.append((float(bind_global[3]), float(bind_global[7]), float(bind_global[11])))

        if float(_points_summary(positions).get("diag", 0.0) or 0.0) <= 1.0e-6:
            return skeleton

        new_joints = []
        for idx, joint in enumerate(joints):
            parent_index = _joint_parent_index(joint)
            bind_global = bind_globals[idx] if idx < len(bind_globals) else None
            local_bind = None
            if bind_global is not None:
                local_matrix = bind_global
                if 0 <= parent_index < idx:
                    parent_global = bind_globals[parent_index] if parent_index < len(bind_globals) else None
                    parent_inv = _matrix4_inverse_row_major(parent_global) if parent_global is not None else None
                    if parent_inv is not None:
                        local_matrix = _matrix4_mul_row_major(parent_inv, bind_global)
                local_bind = _joint_transform_from_matrix4(local_matrix)
            if local_bind is None:
                local_bind = _clone_joint_transform(getattr(joint, "local_bind", None))
            raw_inv = tuple(getattr(joint, "inverse_bind_matrix", ()) or ())
            if len(raw_inv) != 16:
                raw_inv = _IDENTITY_MATRIX_4X4
            new_joints.append(
                Joint(
                    name=str(getattr(joint, "name", "") or f"joint_{idx}"),
                    parent_index=int(parent_index),
                    local_bind=local_bind,
                    inverse_bind_matrix=tuple(float(v) for v in raw_inv),
                )
            )

        metadata = dict(getattr(skeleton, "metadata", None) or {})
        metadata["retarget_eval_bind_source"] = "inverse_bind"
        out = SkeletonAsset(
            name=str(getattr(skeleton, "name", "") or "TargetSkeleton"),
            joints=new_joints,
            metadata=metadata,
        )
        out.validate()
        try:
            setattr(out, "_anim_retarget_inverse_bind_eval_version", 2)
        except Exception:
            pass
        setattr(skeleton, "_anim_retarget_inverse_bind_eval_skeleton", out)
        _retarget_debug_log(
            "target_eval_skeleton_from_inverse_bind",
            skeleton=str(getattr(skeleton, "name", "") or ""),
            joint_count=int(len(new_joints)),
            inverse_bind=_points_summary(positions),
            bind_eval=_points_summary(_skeleton_joint_positions(out)),
        )
        return out
    except Exception as exc:
        _retarget_debug_log(
            "target_eval_skeleton_from_inverse_bind_failed",
            skeleton=str(getattr(skeleton, "name", "") or ""),
            error=repr(exc),
        )
        return skeleton


def _rotation_basis_from_xform(raw: Dict[str, Any] | None) -> Tuple[float, float, float, float]:
    xform = _clean_xform(raw)
    return _quat_from_euler_degrees(xform["rot"])


def _quat_rotate_vec3(q, value: Tuple[float, float, float]) -> Tuple[float, float, float]:
    try:
        vx, vy, vz = (float(value[0]), float(value[1]), float(value[2]))
    except Exception:
        return (0.0, 0.0, 0.0)
    qx, qy, qz, qw = _quat_normalize(q)
    uvx = (qy * vz) - (qz * vy)
    uvy = (qz * vx) - (qx * vz)
    uvz = (qx * vy) - (qy * vx)
    uuvx = (qy * uvz) - (qz * uvy)
    uuvy = (qz * uvx) - (qx * uvz)
    uuvz = (qx * uvy) - (qy * uvx)
    return (
        vx + 2.0 * ((qw * uvx) + uuvx),
        vy + 2.0 * ((qw * uvy) + uuvy),
        vz + 2.0 * ((qw * uvz) + uuvz),
    )


def _quat_slerp(a, b, alpha: float) -> Tuple[float, float, float, float]:
    ax, ay, az, aw = _quat_normalize(a)
    bx, by, bz, bw = _quat_normalize(b)
    dot = (ax * bx) + (ay * by) + (az * bz) + (aw * bw)
    if dot < 0.0:
        bx, by, bz, bw = (-bx, -by, -bz, -bw)
        dot = -dot
    dot = max(-1.0, min(1.0, float(dot)))
    if dot > 0.9995:
        return _quat_normalize(
            (
                ax + (bx - ax) * float(alpha),
                ay + (by - ay) * float(alpha),
                az + (bz - az) * float(alpha),
                aw + (bw - aw) * float(alpha),
            )
        )
    theta0 = math.acos(dot)
    sin0 = math.sin(theta0)
    if abs(sin0) <= 1.0e-12:
        return (ax, ay, az, aw)
    theta = theta0 * max(0.0, min(1.0, float(alpha)))
    s0 = math.cos(theta) - dot * math.sin(theta) / sin0
    s1 = math.sin(theta) / sin0
    return _quat_normalize(
        (
            (ax * s0) + (bx * s1),
            (ay * s0) + (by * s1),
            (az * s0) + (bz * s1),
            (aw * s0) + (bw * s1),
        )
    )


def _sample_quat_keys(keys: List[Any], time_seconds: float, default_value) -> Tuple[float, float, float, float]:
    ordered = list(keys or [])
    if not ordered:
        return _quat_normalize(default_value)
    if len(ordered) == 1:
        return _quat_normalize(getattr(ordered[0], "value", default_value))
    t = float(time_seconds)
    first = ordered[0]
    last = ordered[-1]
    if t <= float(getattr(first, "time", 0.0) or 0.0) + 1.0e-8:
        return _quat_normalize(getattr(first, "value", default_value))
    if t >= float(getattr(last, "time", 0.0) or 0.0) - 1.0e-8:
        return _quat_normalize(getattr(last, "value", default_value))

    lo = 1
    hi = len(ordered) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if t <= float(getattr(ordered[mid], "time", 0.0) or 0.0):
            hi = mid
        else:
            lo = mid + 1
    right = ordered[lo]
    left = ordered[lo - 1]
    if str(getattr(left, "interpolation", "linear") or "linear").strip().lower() == "step":
        return _quat_normalize(getattr(left, "value", default_value))
    lt = float(getattr(left, "time", 0.0) or 0.0)
    rt = float(getattr(right, "time", lt) or lt)
    span = max(1.0e-8, rt - lt)
    alpha = max(0.0, min(1.0, (t - lt) / span))
    return _quat_slerp(
        getattr(left, "value", default_value),
        getattr(right, "value", default_value),
        alpha,
    )


def _global_bind_rotations(skeleton) -> List[Tuple[float, float, float, float]]:
    joints = list(getattr(skeleton, "joints", []) or [])
    out: List[Tuple[float, float, float, float]] = []
    for idx, joint in enumerate(joints):
        local = _quat_normalize(
            getattr(getattr(joint, "local_bind", None), "rotation", (0.0, 0.0, 0.0, 1.0))
        )
        try:
            parent_idx = int(getattr(joint, "parent_index", -1))
        except Exception:
            parent_idx = -1
        if 0 <= parent_idx < idx:
            out.append(_quat_mul(out[parent_idx], local))
        else:
            out.append(local)
    return out


def _global_sample_rotations(skeleton, tracks_by_name: Dict[str, Any], time_seconds: float) -> List[Tuple[float, float, float, float]]:
    joints = list(getattr(skeleton, "joints", []) or [])
    out: List[Tuple[float, float, float, float]] = []
    for idx, joint in enumerate(joints):
        bind_rot = getattr(getattr(joint, "local_bind", None), "rotation", (0.0, 0.0, 0.0, 1.0))
        track = tracks_by_name.get(str(getattr(joint, "name", "") or "").strip())
        local = _sample_quat_keys(
            list(getattr(track, "rotation_keys", []) or []) if track is not None else [],
            float(time_seconds),
            bind_rot,
        )
        try:
            parent_idx = int(getattr(joint, "parent_index", -1))
        except Exception:
            parent_idx = -1
        if 0 <= parent_idx < idx:
            out.append(_quat_mul(out[parent_idx], local))
        else:
            out.append(local)
    return out


def _build_global_rotation_key_sets(
    source_skeleton,
    target_skeleton,
    source_clip,
    mapping: Dict[str, str],
    source_tracks: Dict[str, Any],
    source_index: Dict[str, int],
    target_index: Dict[str, int],
    source_basis: Tuple[float, float, float, float] | None = None,
    target_basis: Tuple[float, float, float, float] | None = None,
) -> Dict[str, List[Any]]:
    try:
        from echograph.rigging.fbx_canonical import QuatKeyframe
    except Exception:
        return {}
    source_joints = list(getattr(source_skeleton, "joints", []) or [])
    target_joints = list(getattr(target_skeleton, "joints", []) or [])
    if not source_joints or not target_joints:
        return {}

    source_bind_global = _global_bind_rotations(source_skeleton)
    target_bind_global = _global_bind_rotations(target_skeleton)
    try:
        source_reference_time = float(getattr(source_clip, "start_time", 0.0) or 0.0)
    except Exception:
        source_reference_time = 0.0
    source_reference_global = _global_sample_rotations(
        source_skeleton,
        source_tracks,
        source_reference_time,
    )
    source_basis_q = _quat_normalize(source_basis or (0.0, 0.0, 0.0, 1.0))
    target_basis_q = _quat_normalize(target_basis or (0.0, 0.0, 0.0, 1.0))
    source_basis_inv = _quat_inverse(source_basis_q)
    target_basis_inv = _quat_inverse(target_basis_q)
    target_to_source: Dict[str, str] = {}
    for source_name, target_name in mapping.items():
        s_name = str(source_name or "").strip()
        t_name = str(target_name or "").strip()
        if s_name and t_name and t_name not in target_to_source:
            target_to_source[t_name] = s_name

    sample_cache: Dict[float, Dict[str, Tuple[float, float, float, float]]] = {}

    def _target_locals_at(time_seconds: float) -> Dict[str, Tuple[float, float, float, float]]:
        key = round(float(time_seconds), 8)
        cached = sample_cache.get(key)
        if cached is not None:
            return cached
        source_global = _global_sample_rotations(source_skeleton, source_tracks, float(time_seconds))
        target_global_current: List[Tuple[float, float, float, float]] = []
        target_local_current: Dict[str, Tuple[float, float, float, float]] = {}
        for idx, joint in enumerate(target_joints):
            name = str(getattr(joint, "name", "") or "").strip()
            bind_local = _quat_normalize(
                getattr(getattr(joint, "local_bind", None), "rotation", (0.0, 0.0, 0.0, 1.0))
            )
            try:
                parent_idx = int(getattr(joint, "parent_index", -1))
            except Exception:
                parent_idx = -1
            parent_global = (
                target_global_current[parent_idx]
                if 0 <= parent_idx < len(target_global_current)
                else (0.0, 0.0, 0.0, 1.0)
            )
            source_name = target_to_source.get(name)
            local = bind_local
            if source_name:
                si = source_index.get(source_name)
                if si is not None and 0 <= int(si) < len(source_global) and int(si) < len(source_bind_global):
                    reference_global = (
                        source_reference_global[int(si)]
                        if int(si) < len(source_reference_global)
                        else source_bind_global[int(si)]
                    )
                    # Apply source motion as a world-space delta. BVH hips often carry a
                    # first-frame basis rotation; post-multiplying that local delta can
                    # turn target pelvis children upward even when the source leg stays down.
                    source_delta = _quat_mul(source_global[int(si)], _quat_inverse(reference_global))
                    source_delta = _quat_mul(
                        target_basis_inv,
                        _quat_mul(source_basis_q, _quat_mul(source_delta, _quat_mul(source_basis_inv, target_basis_q))),
                    )
                    desired_global = _quat_mul(source_delta, target_bind_global[idx])
                    local = _quat_mul(_quat_inverse(parent_global), desired_global)
                    target_local_current[name] = local
            target_global_current.append(_quat_mul(parent_global, local))
        sample_cache[key] = target_local_current
        return target_local_current

    out: Dict[str, List[Any]] = {}
    for source_name, target_name in mapping.items():
        s_name = str(source_name or "").strip()
        t_name = str(target_name or "").strip()
        if not s_name or not t_name or t_name in out:
            continue
        source_track = source_tracks.get(s_name)
        source_rotation_keys = list(getattr(source_track, "rotation_keys", []) or []) if source_track is not None else []
        if not source_rotation_keys:
            continue
        keyframes = []
        for key in source_rotation_keys:
            t = float(getattr(key, "time", 0.0) or 0.0)
            local = _target_locals_at(t).get(t_name)
            if local is None:
                continue
            local = _quat_normalize(local)
            if (
                keyframes
                and _quat_dot(getattr(keyframes[-1], "value", (0.0, 0.0, 0.0, 1.0)), local) < 0.0
            ):
                local = (-local[0], -local[1], -local[2], -local[3])
            keyframes.append(
                QuatKeyframe(
                    time=t,
                    value=local,
                    interpolation=str(getattr(key, "interpolation", "linear") or "linear"),
                )
            )
        if keyframes:
            out[t_name] = keyframes
    return out


def _uniform_scale_from_xform(raw: Dict[str, Any] | None) -> float:
    xform = _clean_xform(raw)
    values = [abs(float(v)) for v in xform["scl"]]
    values = [v for v in values if math.isfinite(v) and v > 1.0e-8]
    if not values:
        return 1.0
    return sum(values) / float(len(values))


def _retarget_translation_scale(
    model,
    source_context: Dict[str, Any],
    target_context: Dict[str, Any],
) -> float:
    mode = (_param_value(model, "scale_mode") or "auto").strip().lower()
    if mode in {"none", "off", "identity", "1"}:
        return 1.0
    source_scale = _uniform_scale_from_xform(source_context.get("transform_xform"))
    target_scale = _uniform_scale_from_xform(target_context.get("transform_xform"))
    transform_scale = source_scale / target_scale if target_scale > 1.0e-8 else source_scale
    if mode in {"transform", "source_transform", "manual"}:
        return float(transform_scale)
    if abs(source_scale - 1.0) > 1.0e-6 or abs(target_scale - 1.0) > 1.0e-6:
        return float(transform_scale)
    source_extent = max(1.0e-8, float(_skeleton_extent(source_context)))
    target_extent = max(1.0e-8, float(_skeleton_extent(target_context)))
    return float(target_extent / source_extent)


def _joint_lookup(skeleton) -> Dict[str, int]:
    out: Dict[str, int] = {}
    try:
        joints = list(getattr(skeleton, "joints", []) or [])
    except Exception:
        joints = []
    for idx, joint in enumerate(joints):
        name = str(getattr(joint, "name", "") or "").strip()
        if name and name not in out:
            out[name] = int(idx)
    return out


def _track_lookup(clip) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for track in list(getattr(clip, "tracks", []) or []):
        name = str(getattr(track, "joint_name", "") or "").strip()
        if name and name not in out:
            out[name] = track
    return out


def _copy_rotation_keys(source_track, source_bind, target_bind):
    try:
        from echograph.rigging.fbx_canonical import QuatKeyframe
    except Exception:
        return []
    source_bind_q = getattr(source_bind, "rotation", (0.0, 0.0, 0.0, 1.0))
    target_bind_q = getattr(target_bind, "rotation", (0.0, 0.0, 0.0, 1.0))
    source_bind_inv = _quat_inverse(source_bind_q)
    out = []
    for key in list(getattr(source_track, "rotation_keys", []) or []):
        value = _quat_mul(
            target_bind_q,
            _quat_mul(source_bind_inv, getattr(key, "value", (0.0, 0.0, 0.0, 1.0))),
        )
        if (
            out
            and _quat_dot(getattr(out[-1], "value", (0.0, 0.0, 0.0, 1.0)), value) < 0.0
        ):
            value = (-value[0], -value[1], -value[2], -value[3])
        out.append(
            QuatKeyframe(
                time=float(getattr(key, "time", 0.0) or 0.0),
                value=value,
                interpolation=str(getattr(key, "interpolation", "linear") or "linear"),
            )
        )
    return out


def _copy_translation_keys(
    source_track,
    source_bind,
    target_bind,
    scale: float,
    source_basis: Tuple[float, float, float, float] | None = None,
    target_basis: Tuple[float, float, float, float] | None = None,
):
    try:
        from echograph.rigging.fbx_canonical import Vec3Keyframe
    except Exception:
        return []
    source_t = getattr(source_bind, "translation", (0.0, 0.0, 0.0))
    source_keys = list(getattr(source_track, "translation_keys", []) or [])
    if source_keys:
        try:
            source_t = tuple(getattr(source_keys[0], "value", source_t))
        except Exception:
            pass
    target_t = getattr(target_bind, "translation", (0.0, 0.0, 0.0))
    try:
        sx, sy, sz = (float(source_t[0]), float(source_t[1]), float(source_t[2]))
    except Exception:
        sx, sy, sz = (0.0, 0.0, 0.0)
    try:
        tx, ty, tz = (float(target_t[0]), float(target_t[1]), float(target_t[2]))
    except Exception:
        tx, ty, tz = (0.0, 0.0, 0.0)
    s = float(scale) if math.isfinite(float(scale)) else 1.0
    source_basis_q = _quat_normalize(source_basis or (0.0, 0.0, 0.0, 1.0))
    target_basis_q = _quat_normalize(target_basis or (0.0, 0.0, 0.0, 1.0))
    basis = _quat_mul(_quat_inverse(target_basis_q), source_basis_q)
    out = []
    for key in source_keys:
        try:
            vx, vy, vz = getattr(key, "value", (sx, sy, sz))
            dx, dy, dz = _quat_rotate_vec3(
                basis,
                (
                    (float(vx) - sx) * s,
                    (float(vy) - sy) * s,
                    (float(vz) - sz) * s,
                ),
            )
            value = (
                tx + dx,
                ty + dy,
                tz + dz,
            )
        except Exception:
            value = (tx, ty, tz)
        out.append(
            Vec3Keyframe(
                time=float(getattr(key, "time", 0.0) or 0.0),
                value=value,
                interpolation=str(getattr(key, "interpolation", "linear") or "linear"),
            )
        )
    return out


def _snap_translation_keys(
    source_track,
    scale: float,
    source_basis: Tuple[float, float, float, float] | None = None,
    target_basis: Tuple[float, float, float, float] | None = None,
):
    try:
        from echograph.rigging.fbx_canonical import Vec3Keyframe
    except Exception:
        return []
    source_keys = list(getattr(source_track, "translation_keys", []) or [])
    if not source_keys:
        return []
    try:
        s = float(scale)
    except Exception:
        s = 1.0
    if not math.isfinite(s):
        s = 1.0
    source_basis_q = _quat_normalize(source_basis or (0.0, 0.0, 0.0, 1.0))
    target_basis_q = _quat_normalize(target_basis or (0.0, 0.0, 0.0, 1.0))
    basis = _quat_mul(_quat_inverse(target_basis_q), source_basis_q)
    out = []
    for key in source_keys:
        try:
            vx, vy, vz = getattr(key, "value", (0.0, 0.0, 0.0))
            value = _quat_rotate_vec3(
                basis,
                (float(vx) * s, float(vy) * s, float(vz) * s),
            )
        except Exception:
            value = (0.0, 0.0, 0.0)
        out.append(
            Vec3Keyframe(
                time=float(getattr(key, "time", 0.0) or 0.0),
                value=value,
                interpolation=str(getattr(key, "interpolation", "linear") or "linear"),
            )
        )
    return out


def _normalize_pelvis_constraint_mode(raw: str) -> str:
    token = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"snap", "match", "match_source"}:
        return "snap"
    if token in {"position_offset", "offset", "keep_offset", "position"}:
        return "position_offset"
    return "none"


def _pelvis_constraint_payload(model) -> Dict[str, str]:
    mode = _normalize_pelvis_constraint_mode(_param_value(model, PELVIS_CONSTRAINT_MODE_PARAM))
    source = (_param_value(model, PELVIS_CONSTRAINT_SOURCE_PARAM) or "").strip()
    target = (_param_value(model, PELVIS_CONSTRAINT_TARGET_PARAM) or "").strip()
    if not source or not target:
        mode = "none"
    return {"mode": mode, "source": source, "target": target}


def _is_pelvis_constraint_mapping(
    constraint: Dict[str, str],
    source_name: str,
    target_name: str,
) -> bool:
    if _normalize_pelvis_constraint_mode(constraint.get("mode", "none")) == "none":
        return False
    source = str(constraint.get("source") or "").strip().lower()
    target = str(constraint.get("target") or "").strip().lower()
    return bool(
        source
        and target
        and source_name.strip().lower() == source
        and target_name.strip().lower() == target
    )


def _is_root_motion_mapping(
    model,
    source_name: str,
    target_name: str,
    target_joint,
) -> bool:
    root_source = (_param_value(model, "root_source") or "").strip()
    root_target = (_param_value(model, "root_target") or "").strip()
    if root_source or root_target:
        source_ok = (not root_source) or source_name.strip().lower() == root_source.lower()
        target_ok = (not root_target) or target_name.strip().lower() == root_target.lower()
        return bool(source_ok and target_ok)
    try:
        return int(getattr(target_joint, "parent_index", -1)) < 0
    except Exception:
        return False


def _freeze_cache_value(value: Any):
    if isinstance(value, dict):
        return tuple(
            (str(key), _freeze_cache_value(val))
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_cache_value(val) for val in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze_cache_value(val) for val in value), key=repr))
    if isinstance(value, float):
        return round(float(value), 8)
    if isinstance(value, (str, int, bool, type(None))):
        return value
    return repr(value)


def _retarget_model_clip_signature(model) -> Tuple[Tuple[str, str], ...]:
    rows = []
    for entry in list(getattr(model, "params", None) or []):
        rows.append(
            (
                str(entry.get("name") or ""),
                str(entry.get("value") or ""),
            )
        )
    return tuple(rows)


def _retarget_clip_cache_key(
    model,
    source_context: Dict[str, Any],
    target_context: Dict[str, Any],
    source_skeleton,
    target_skeleton,
    source_clip,
):
    return (
        id(source_skeleton),
        id(target_skeleton),
        id(source_clip),
        _freeze_cache_value(source_context.get("transform_xform")),
        _freeze_cache_value(target_context.get("transform_xform")),
        _retarget_model_clip_signature(model),
    )


@profiled("rigging.retarget_clip_build")
def build_anim_retarget_clip(
    node_item,
    result: RetargetSourceTargetResult | None = None,
):
    if result is None:
        result = resolve_anim_retarget_inputs(node_item, persist=True)
    model = getattr(node_item, "model", None)
    source_context = result.source_context if isinstance(result, RetargetSourceTargetResult) else None
    target_context = result.target_context if isinstance(result, RetargetSourceTargetResult) else None
    if not isinstance(source_context, dict) or not isinstance(target_context, dict):
        return None
    source_skeleton = source_context.get("skeleton")
    target_skeleton = target_pose_skeleton_for_model(
        model,
        _retarget_eval_target_skeleton(target_context.get("skeleton")),
    )
    source_clip = source_context.get("clip")
    if source_skeleton is None or target_skeleton is None or source_clip is None:
        return None
    mapping = _joint_map_payload(model)
    if not mapping:
        return None
    clip_cache_key = _retarget_clip_cache_key(
        model,
        source_context,
        target_context,
        source_skeleton,
        target_skeleton,
        source_clip,
    )
    if model is not None:
        cached_key = getattr(model, "_retarget_animation_clip_cache_key", None)
        cached_clip = getattr(model, "_retarget_animation_clip", None)
        if cached_key == clip_cache_key and cached_clip is not None:
            return cached_clip

    try:
        from echograph.rigging.fbx_canonical import AnimationClip, JointAnimationTrack
    except Exception as exc:
        _retarget_debug_log("build_retarget_clip_import_failed", error=repr(exc))
        return None

    source_joints = list(getattr(source_skeleton, "joints", []) or [])
    target_joints = list(getattr(target_skeleton, "joints", []) or [])
    source_index = _joint_lookup(source_skeleton)
    target_index = _joint_lookup(target_skeleton)
    source_tracks = _track_lookup(source_clip)
    target_eval_context = dict(target_context)
    target_eval_context["skeleton"] = target_skeleton
    translation_scale = _retarget_translation_scale(model, source_context, target_eval_context)
    source_basis = _rotation_basis_from_xform(source_context.get("transform_xform"))
    target_basis = _rotation_basis_from_xform(target_context.get("transform_xform"))
    rotation_key_sets = _build_global_rotation_key_sets(
        source_skeleton,
        target_skeleton,
        source_clip,
        mapping,
        source_tracks,
        source_index,
        target_index,
        source_basis,
        target_basis,
    )
    pelvis_constraint = _pelvis_constraint_payload(model)
    tracks = []
    used_targets = set()
    skipped: List[str] = []

    for source_joint, target_joint in mapping.items():
        source_name = str(source_joint or "").strip()
        target_name = str(target_joint or "").strip()
        if not source_name or not target_name:
            continue
        if target_name in used_targets:
            skipped.append(f"{source_name}->{target_name}: target already mapped")
            continue
        source_track = source_tracks.get(source_name)
        if source_track is None:
            skipped.append(f"{source_name}->{target_name}: source track missing")
            continue
        si = source_index.get(source_name)
        ti = target_index.get(target_name)
        if si is None or si < 0 or si >= len(source_joints):
            skipped.append(f"{source_name}->{target_name}: source joint missing")
            continue
        if ti is None or ti < 0 or ti >= len(target_joints):
            skipped.append(f"{source_name}->{target_name}: target joint missing")
            continue
        source_joint_obj = source_joints[si]
        target_joint_obj = target_joints[ti]
        source_bind = getattr(source_joint_obj, "local_bind", None)
        target_bind = getattr(target_joint_obj, "local_bind", None)
        rotation_keys = list(rotation_key_sets.get(target_name) or [])
        if not rotation_keys:
            rotation_keys = _copy_rotation_keys(source_track, source_bind, target_bind)
        translation_keys = []
        source_translation_keys = list(getattr(source_track, "translation_keys", []) or [])
        if source_translation_keys:
            if _is_pelvis_constraint_mapping(pelvis_constraint, source_name, target_name):
                pelvis_mode = _normalize_pelvis_constraint_mode(pelvis_constraint.get("mode", "none"))
                if pelvis_mode == "snap":
                    translation_keys = _snap_translation_keys(
                        source_track,
                        translation_scale,
                        source_basis,
                        target_basis,
                    )
                elif pelvis_mode == "position_offset":
                    translation_keys = _copy_translation_keys(
                        source_track,
                        source_bind,
                        target_bind,
                        translation_scale,
                        source_basis,
                        target_basis,
                    )
            elif _is_root_motion_mapping(model, source_name, target_name, target_joint_obj):
                translation_keys = _copy_translation_keys(
                    source_track,
                    source_bind,
                    target_bind,
                    translation_scale,
                    source_basis,
                    target_basis,
                )
            else:
                skipped.append(f"{source_name}->{target_name}: translation keys ignored to preserve target bone lengths")
        if not rotation_keys and not translation_keys:
            skipped.append(f"{source_name}->{target_name}: no animation keys")
            continue
        tracks.append(
            JointAnimationTrack(
                joint_name=target_name,
                translation_keys=translation_keys,
                rotation_keys=rotation_keys,
                scale_keys=[],
            )
        )
        used_targets.add(target_name)

    if not tracks:
        _retarget_debug_log("build_retarget_clip_empty", skipped=skipped, mapping=mapping)
        return None

    clip_name = str(getattr(source_clip, "name", "") or "source").strip() or "source"
    source_reference_time = float(getattr(source_clip, "start_time", 0.0) or 0.0)
    retarget_clip = AnimationClip(
        name=f"{clip_name}_retarget",
        start_time=source_reference_time,
        end_time=float(getattr(source_clip, "end_time", 0.0) or 0.0),
        sample_rate_hz=float(getattr(source_clip, "sample_rate_hz", 30.0) or 30.0),
        tracks=tracks,
        metadata={
            "source": str(source_context.get("name") or ""),
            "target": str(target_context.get("name") or ""),
            "joint_map_count": int(len(mapping)),
            "retarget_track_count": int(len(tracks)),
            "translation_scale": float(translation_scale),
            "rotation_space": "clip_start_world_delta",
            "source_reference_time": float(source_reference_time),
            "pelvis_constraint": dict(pelvis_constraint),
        },
    )
    try:
        retarget_clip.validate(skeleton=target_skeleton)
    except Exception as exc:
        _retarget_debug_log("build_retarget_clip_validation_failed", error=repr(exc), skipped=skipped)
        return None
    try:
        if model is not None:
            setattr(model, "_retarget_animation_clip", retarget_clip)
            setattr(model, "_retarget_animation_clip_cache_key", clip_cache_key)
            setattr(model, "_retarget_animation_track_count", int(len(tracks)))
            setattr(model, "_retarget_animation_skipped", list(skipped))
    except Exception:
        pass
    _retarget_debug_log(
        "build_retarget_clip",
        node=str(getattr(model, "name", "") or ""),
        source_clip=clip_name,
        tracks=int(len(tracks)),
        skipped=skipped,
        translation_scale=round(float(translation_scale), 6),
        translation_policy="root_motion_only",
        rotation_space="clip_start_world_delta",
        source_reference_time=round(float(source_reference_time), 6),
        source_transform=dict(source_context.get("transform_xform") or {}),
        target_transform=dict(target_context.get("transform_xform") or {}),
        source_basis=_round_quat(source_basis),
        target_basis=_round_quat(target_basis),
        pelvis_constraint=dict(pelvis_constraint),
    )
    return retarget_clip


@profiled("rigging.retarget_scene_asset")
def build_anim_retarget_scene_asset(
    node_item,
    result: RetargetSourceTargetResult | None = None,
) -> Dict[str, Any] | None:
    if result is None:
        result = resolve_anim_retarget_inputs(node_item, persist=True)
    if result.status == "error":
        return None
    model = getattr(node_item, "model", None)
    source_context = result.source_context
    target_context = result.target_context
    if not isinstance(source_context, dict) or not isinstance(target_context, dict):
        return None
    retarget_clip = build_anim_retarget_clip(node_item, result)
    if retarget_clip is None:
        return None

    preview_asset = target_context.get("preview_asset")
    asset = dict(preview_asset or {}) if isinstance(preview_asset, dict) else {}
    path_text = str(target_context.get("path") or asset.get("path") or "").strip()
    if not path_text:
        return None
    base_name = str(getattr(model, "name", "") or "").strip() or "Anim Retarget"
    xform = _clean_xform(target_context.get("transform_xform"))
    rig_context = dict(target_context.get("rig_context") or {})
    target_skeleton = target_pose_skeleton_for_model(
        model,
        _retarget_eval_target_skeleton(target_context.get("skeleton")),
        rebind_inverse_bind=False,
    )
    original_clip = rig_context.get("clip")
    rig_context["skeleton"] = target_skeleton
    rig_context["clip"] = retarget_clip
    rig_context["clips"] = [retarget_clip]
    rig_context["meshes"] = list(target_context.get("meshes") or rig_context.get("meshes") or [])
    rig_context["loop"] = True
    rig_context["mesh_skinning_enabled"] = True
    rig_context["skin_weight_debug"] = False
    rig_context["show_skin_weights"] = False
    rig_context["show_capture_joints"] = False
    rig_context["show_animated_joints"] = bool(rig_context.get("show_animated_joints", False))
    rig_context["retarget_result"] = True
    rig_context["retarget_source_name"] = str(source_context.get("name") or "")
    rig_context["retarget_target_name"] = str(target_context.get("name") or "")
    rig_context["retarget_joint_map"] = dict(_joint_map_payload(model))
    rig_context["retarget_clip_name"] = str(getattr(retarget_clip, "name", "") or "")
    rig_context["retarget_track_count"] = int(len(getattr(retarget_clip, "tracks", []) or []))
    rig_context["retarget_original_clip_name"] = str(getattr(original_clip, "name", "") or "")
    rig_context["retarget_target_pose_rebind_inverse_bind"] = False
    rig_context["retarget_target_pose_offset_count"] = int(len(_target_pose_offsets_payload(model)))

    asset.update(
        {
            "path": path_text,
            "texture": str(asset.get("texture") or ""),
            "node": base_name,
            "kind": "anim_retarget",
            "ext": Path(path_text).suffix.lower() or ".fbx",
            "visible": True,
            "xform": {
                "pos": [float(v) for v in xform["pos"]],
                "rot": [float(v) for v in xform["rot"]],
                "scl": [float(v) for v in xform["scl"]],
            },
            "fbx_rig_context": rig_context,
            "fbx_debug_log": bool(rig_context.get("fbx_debug_log", False)),
        }
    )
    try:
        if model is not None:
            setattr(model, "_retarget_scene_asset", asset)
    except Exception:
        pass
    _retarget_debug_log(
        "build_scene_asset",
        node=base_name,
        path=path_text,
        target=str(target_context.get("name") or ""),
        tracks=int(len(getattr(retarget_clip, "tracks", []) or [])),
        clip=str(getattr(retarget_clip, "name", "") or ""),
        original_clip=str(getattr(original_clip, "name", "") or ""),
        target_pose_offset_count=int(len(_target_pose_offsets_payload(model))),
        rebind_inverse_bind=False,
        source_transform=dict(source_context.get("transform_xform") or {}),
        target_transform=dict(target_context.get("transform_xform") or {}),
    )
    _maybe_write_anim_retarget_joint_debug_snapshot(node_item, result, reason="scene_asset")
    return asset


def _round_float(value: Any, digits: int = 6) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    if not math.isfinite(number):
        number = 0.0
    return round(number, int(digits))


def _round_vec3(value: Any) -> List[float]:
    try:
        return [_round_float(value[0]), _round_float(value[1]), _round_float(value[2])]
    except Exception:
        return [0.0, 0.0, 0.0]


def _round_quat(value: Any) -> List[float]:
    try:
        return [
            _round_float(value[0]),
            _round_float(value[1]),
            _round_float(value[2]),
            _round_float(value[3]),
        ]
    except Exception:
        return [0.0, 0.0, 0.0, 1.0]


def _position_bounds_summary(rows: List[Tuple[float, float, float]]) -> Dict[str, Any]:
    if not rows:
        return {
            "count": 0,
            "bounds_min": None,
            "bounds_max": None,
            "axis_lengths": [0.0, 0.0, 0.0],
            "diag": 0.0,
            "min_axis_ratio": 0.0,
            "flattened_axis": "",
        }
    xs = [float(row[0]) for row in rows]
    ys = [float(row[1]) for row in rows]
    zs = [float(row[2]) for row in rows]
    axis_lengths = [
        max(xs) - min(xs),
        max(ys) - min(ys),
        max(zs) - min(zs),
    ]
    diag = math.sqrt(sum(length * length for length in axis_lengths))
    max_axis = max(axis_lengths) if axis_lengths else 0.0
    min_axis = min(axis_lengths) if axis_lengths else 0.0
    ratio = (min_axis / max_axis) if max_axis > 1.0e-8 else 0.0
    axis_names = ("x", "y", "z")
    flat_axis = ""
    if max_axis > 1.0e-8 and ratio < 0.02:
        try:
            flat_axis = axis_names[axis_lengths.index(min_axis)]
        except Exception:
            flat_axis = ""
    return {
        "count": int(len(rows)),
        "bounds_min": [_round_float(min(xs)), _round_float(min(ys)), _round_float(min(zs))],
        "bounds_max": [_round_float(max(xs)), _round_float(max(ys)), _round_float(max(zs))],
        "axis_lengths": [_round_float(v) for v in axis_lengths],
        "diag": _round_float(diag),
        "min_axis_ratio": _round_float(ratio),
        "flattened_axis": flat_axis,
    }


def _clip_debug_summary(clip) -> Dict[str, Any]:
    tracks = list(getattr(clip, "tracks", []) or []) if clip is not None else []
    return {
        "name": str(getattr(clip, "name", "") or ""),
        "start_time": _round_float(getattr(clip, "start_time", 0.0) if clip is not None else 0.0),
        "end_time": _round_float(getattr(clip, "end_time", 0.0) if clip is not None else 0.0),
        "sample_rate_hz": _round_float(getattr(clip, "sample_rate_hz", 0.0) if clip is not None else 0.0),
        "track_count": int(len(tracks)),
        "metadata": dict(getattr(clip, "metadata", {}) or {}) if clip is not None else {},
        "tracks": [
            {
                "joint": str(getattr(track, "joint_name", "") or ""),
                "translation_keys": int(len(getattr(track, "translation_keys", []) or [])),
                "rotation_keys": int(len(getattr(track, "rotation_keys", []) or [])),
                "scale_keys": int(len(getattr(track, "scale_keys", []) or [])),
            }
            for track in tracks
        ],
    }


def _debug_sample_times(clip) -> List[float]:
    start = float(getattr(clip, "start_time", 0.0) or 0.0) if clip is not None else 0.0
    end = float(getattr(clip, "end_time", start) or start) if clip is not None else start
    if end <= start + 1.0e-8:
        return [round(start, 6)]
    span = end - start
    values = [start, start + (span * 0.25), start + (span * 0.5), start + (span * 0.75), end]
    out: List[float] = []
    for value in values:
        rounded = round(float(value), 6)
        if rounded not in out:
            out.append(rounded)
    return out


def _matrix_position_sets(skeleton, clip, time_seconds: float) -> Dict[str, List[Tuple[float, float, float]]]:
    rows_tcol: List[Tuple[float, float, float]] = []
    rows_trow: List[Tuple[float, float, float]] = []
    try:
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time

        evaluation = evaluate_rig_at_time(
            skeleton=skeleton,
            clip=clip,
            time_seconds=float(time_seconds),
            loop=True,
        )
        for matrix in list(getattr(evaluation, "global_matrices", []) or []):
            values = tuple(matrix or ())
            if len(values) != 16:
                continue
            rows_tcol.append((float(values[3]), float(values[7]), float(values[11])))
            rows_trow.append((float(values[12]), float(values[13]), float(values[14])))
    except Exception:
        pass
    return {"tcol": rows_tcol, "trow": rows_trow}


def _inverse_bind_position_sets(skeleton) -> Dict[str, List[Tuple[float, float, float]]]:
    rows_tcol: List[Tuple[float, float, float]] = []
    rows_trow: List[Tuple[float, float, float]] = []
    try:
        import numpy as _np
    except Exception:
        _np = None
    if _np is None:
        return {"tcol": rows_tcol, "trow": rows_trow}
    for joint in list(getattr(skeleton, "joints", []) or []):
        raw_inv = tuple(getattr(joint, "inverse_bind_matrix", ()) or ())
        if len(raw_inv) != 16:
            rows_tcol.append((0.0, 0.0, 0.0))
            rows_trow.append((0.0, 0.0, 0.0))
            continue
        try:
            inv_bind = _np.asarray(raw_inv, dtype="f4").reshape(4, 4)
            bind_global = _np.linalg.inv(inv_bind)
            flat = bind_global.reshape(-1)
            rows_tcol.append((float(flat[3]), float(flat[7]), float(flat[11])))
            rows_trow.append((float(flat[12]), float(flat[13]), float(flat[14])))
        except Exception:
            rows_tcol.append((0.0, 0.0, 0.0))
            rows_trow.append((0.0, 0.0, 0.0))
    return {"tcol": rows_tcol, "trow": rows_trow}


def _named_positions(skeleton, rows: List[Tuple[float, float, float]]) -> List[Dict[str, Any]]:
    joints = list(getattr(skeleton, "joints", []) or [])
    out: List[Dict[str, Any]] = []
    count = min(len(joints), len(rows))
    for idx in range(count):
        joint = joints[idx]
        out.append(
            {
                "index": int(idx),
                "name": str(getattr(joint, "name", "") or ""),
                "parent": _joint_parent_index(joint),
                "position": _round_vec3(rows[idx]),
            }
        )
    return out


def _track_debug(track) -> Dict[str, Any]:
    if track is None:
        return {"exists": False}
    translation_keys = list(getattr(track, "translation_keys", []) or [])
    rotation_keys = list(getattr(track, "rotation_keys", []) or [])
    return {
        "exists": True,
        "translation_key_count": int(len(translation_keys)),
        "rotation_key_count": int(len(rotation_keys)),
        "first_translation": (
            {
                "time": _round_float(getattr(translation_keys[0], "time", 0.0)),
                "value": _round_vec3(getattr(translation_keys[0], "value", (0.0, 0.0, 0.0))),
            }
            if translation_keys
            else None
        ),
        "last_translation": (
            {
                "time": _round_float(getattr(translation_keys[-1], "time", 0.0)),
                "value": _round_vec3(getattr(translation_keys[-1], "value", (0.0, 0.0, 0.0))),
            }
            if translation_keys
            else None
        ),
        "first_rotation": (
            {
                "time": _round_float(getattr(rotation_keys[0], "time", 0.0)),
                "value": _round_quat(getattr(rotation_keys[0], "value", (0.0, 0.0, 0.0, 1.0))),
            }
            if rotation_keys
            else None
        ),
        "last_rotation": (
            {
                "time": _round_float(getattr(rotation_keys[-1], "time", 0.0)),
                "value": _round_quat(getattr(rotation_keys[-1], "value", (0.0, 0.0, 0.0, 1.0))),
            }
            if rotation_keys
            else None
        ),
    }


def write_anim_retarget_joint_debug_snapshot(
    node_item,
    result: RetargetSourceTargetResult | None = None,
) -> Path:
    if result is None:
        result = resolve_anim_retarget_inputs(node_item, persist=True)
    if result.status == "error":
        raise RuntimeError("; ".join(result.errors or ["Anim Retarget inputs are invalid."]))
    model = getattr(node_item, "model", None)
    source_context = result.source_context
    target_context = result.target_context
    if not isinstance(source_context, dict) or not isinstance(target_context, dict):
        raise RuntimeError("Anim Retarget source or target context is unavailable.")
    source_skeleton = source_context.get("skeleton")
    target_skeleton_original = target_context.get("skeleton")
    target_skeleton = target_pose_skeleton_for_model(
        model,
        _retarget_eval_target_skeleton(target_skeleton_original),
    )
    source_clip = source_context.get("clip")
    if source_skeleton is None or target_skeleton is None or source_clip is None:
        raise RuntimeError("Anim Retarget source skeleton, target skeleton, or source clip is unavailable.")
    retarget_clip = build_anim_retarget_clip(node_item, result)
    if retarget_clip is None:
        raise RuntimeError("Retarget clip could not be built.")

    source_tracks = _track_lookup(source_clip)
    retarget_tracks = _track_lookup(retarget_clip)
    source_index = _joint_lookup(source_skeleton)
    target_index = _joint_lookup(target_skeleton)
    mapping = _joint_map_payload(model)
    sample_times = _debug_sample_times(retarget_clip)
    target_inverse = _inverse_bind_position_sets(target_skeleton)
    target_bind_eval = _matrix_position_sets(target_skeleton, None, 0.0)
    target_original_bind_eval = (
        _matrix_position_sets(target_skeleton_original, None, 0.0)
        if target_skeleton_original is not None and target_skeleton_original is not target_skeleton
        else target_bind_eval
    )
    source_bind_eval = _matrix_position_sets(source_skeleton, None, 0.0)
    target_anim_sets = {
        str(t): _matrix_position_sets(target_skeleton, retarget_clip, float(t))
        for t in sample_times
    }
    source_anim_sets = {
        str(t): _matrix_position_sets(source_skeleton, source_clip, float(t))
        for t in sample_times
    }

    mapped_rows: List[Dict[str, Any]] = []
    for source_name, target_name in mapping.items():
        source_name = str(source_name or "").strip()
        target_name = str(target_name or "").strip()
        si = source_index.get(source_name, -1)
        ti = target_index.get(target_name, -1)
        source_joint = list(getattr(source_skeleton, "joints", []) or [])[si] if 0 <= si < len(list(getattr(source_skeleton, "joints", []) or [])) else None
        target_joint = list(getattr(target_skeleton, "joints", []) or [])[ti] if 0 <= ti < len(list(getattr(target_skeleton, "joints", []) or [])) else None
        row: Dict[str, Any] = {
            "source": source_name,
            "target": target_name,
            "source_index": int(si) if isinstance(si, int) else -1,
            "target_index": int(ti) if isinstance(ti, int) else -1,
            "source_parent": _joint_parent_index(source_joint) if source_joint is not None else -1,
            "target_parent": _joint_parent_index(target_joint) if target_joint is not None else -1,
            "source_local_bind_translation": _round_vec3(
                getattr(getattr(source_joint, "local_bind", None), "translation", (0.0, 0.0, 0.0))
                if source_joint is not None
                else (0.0, 0.0, 0.0)
            ),
            "target_local_bind_translation": _round_vec3(
                getattr(getattr(target_joint, "local_bind", None), "translation", (0.0, 0.0, 0.0))
                if target_joint is not None
                else (0.0, 0.0, 0.0)
            ),
            "source_track": _track_debug(source_tracks.get(source_name)),
            "target_retarget_track": _track_debug(retarget_tracks.get(target_name)),
            "samples": {},
        }
        for time_key in [str(t) for t in sample_times]:
            source_tcol = source_anim_sets.get(time_key, {}).get("tcol", [])
            target_tcol = target_anim_sets.get(time_key, {}).get("tcol", [])
            target_trow = target_anim_sets.get(time_key, {}).get("trow", [])
            row["samples"][time_key] = {
                "source_tcol": _round_vec3(source_tcol[si]) if 0 <= si < len(source_tcol) else None,
                "target_tcol": _round_vec3(target_tcol[ti]) if 0 <= ti < len(target_tcol) else None,
                "target_trow": _round_vec3(target_trow[ti]) if 0 <= ti < len(target_trow) else None,
            }
        mapped_rows.append(row)

    target_anim_summary = {}
    for time_key, sets in target_anim_sets.items():
        target_anim_summary[time_key] = {
            "tcol": _position_bounds_summary(sets.get("tcol", [])),
            "trow": _position_bounds_summary(sets.get("trow", [])),
        }
    source_anim_summary = {}
    for time_key, sets in source_anim_sets.items():
        source_anim_summary[time_key] = {
            "tcol": _position_bounds_summary(sets.get("tcol", [])),
            "trow": _position_bounds_summary(sets.get("trow", [])),
        }

    first_time = str(sample_times[0]) if sample_times else "0.0"
    mid_time = str(sample_times[len(sample_times) // 2]) if sample_times else first_time
    last_time = str(sample_times[-1]) if sample_times else first_time
    payload = {
        "event": "anim_retarget_joint_debug_snapshot",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "node": str(getattr(model, "name", "") or ""),
        "status": result.status,
        "source": {
            "name": result.source_name,
            "kind": result.source_kind,
            "path": str(source_context.get("path") or ""),
            "transform": dict(source_context.get("transform_xform") or {}),
            "joint_count": int(result.source_joint_count),
            "clip": _clip_debug_summary(source_clip),
            "bind_eval": {
                "tcol": _position_bounds_summary(source_bind_eval.get("tcol", [])),
                "trow": _position_bounds_summary(source_bind_eval.get("trow", [])),
            },
            "animated_summary": source_anim_summary,
        },
        "target": {
            "name": result.target_name,
            "kind": result.target_kind,
            "path": str(target_context.get("path") or ""),
            "transform": dict(target_context.get("transform_xform") or {}),
            "joint_count": int(result.target_joint_count),
            "mesh_count": int(result.target_mesh_count),
            "target_pose_offsets": _target_pose_offsets_payload(model),
            "eval_bind_source": str(
                (getattr(target_skeleton, "metadata", None) or {}).get("retarget_eval_bind_source", "local_bind")
            ),
            "original_bind_eval": {
                "tcol": _position_bounds_summary(target_original_bind_eval.get("tcol", [])),
                "trow": _position_bounds_summary(target_original_bind_eval.get("trow", [])),
            },
            "bind_eval": {
                "tcol": _position_bounds_summary(target_bind_eval.get("tcol", [])),
                "trow": _position_bounds_summary(target_bind_eval.get("trow", [])),
            },
            "inverse_bind": {
                "tcol": _position_bounds_summary(target_inverse.get("tcol", [])),
                "trow": _position_bounds_summary(target_inverse.get("trow", [])),
            },
            "retarget_summary": target_anim_summary,
            "retarget_positions_first_sample_tcol": _named_positions(
                target_skeleton,
                target_anim_sets.get(first_time, {}).get("tcol", []),
            ),
            "retarget_positions_mid_sample_tcol": _named_positions(
                target_skeleton,
                target_anim_sets.get(mid_time, {}).get("tcol", []),
            ),
            "retarget_positions_last_sample_tcol": _named_positions(
                target_skeleton,
                target_anim_sets.get(last_time, {}).get("tcol", []),
            ),
            "bind_positions_tcol": _named_positions(target_skeleton, target_bind_eval.get("tcol", [])),
            "inverse_bind_positions_tcol": _named_positions(target_skeleton, target_inverse.get("tcol", [])),
            "inverse_bind_positions_trow": _named_positions(target_skeleton, target_inverse.get("trow", [])),
        },
        "retarget": {
            "clip": _clip_debug_summary(retarget_clip),
            "sample_times": sample_times,
            "joint_map_count": int(len(mapping)),
            "joint_map": mapping,
            "mapped_joints": mapped_rows,
            "warnings": list(result.warnings or []),
        },
    }
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (payload["node"] or "anim_retarget"))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = _repo_logs_dir() / f"anim_retarget_joint_debug_{safe_name}_{stamp}.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    _retarget_debug_log(
        "joint_debug_snapshot_written",
        node=payload["node"],
        path=str(out_path),
        source=result.source_name,
        target=result.target_name,
        joint_map_count=int(len(mapping)),
        target_bind=_position_bounds_summary(target_bind_eval.get("tcol", [])),
        target_inverse_tcol=_position_bounds_summary(target_inverse.get("tcol", [])),
        target_inverse_trow=_position_bounds_summary(target_inverse.get("trow", [])),
        target_retarget_first=target_anim_summary.get(first_time, {}),
        retarget_clip=str(getattr(retarget_clip, "name", "") or ""),
    )
    return out_path


def _maybe_write_anim_retarget_joint_debug_snapshot(
    node_item,
    result: RetargetSourceTargetResult | None,
    *,
    reason: str,
) -> Path | None:
    model = getattr(node_item, "model", None) if node_item is not None else None
    if not _retarget_debug_enabled(model):
        return None
    try:
        signature = json.dumps(
            {
                "reason": str(reason or ""),
                "source": str(getattr(result, "source_name", "") or ""),
                "target": str(getattr(result, "target_name", "") or ""),
                "map": _param_value(model, "joint_map"),
                "preview_target": _param_value(model, PREVIEW_TARGET_MESH_PARAM),
                "source_reference_pose": _param_value(model, SOURCE_REST_POSE_PARAM),
                "pelvis_constraint_source": _param_value(model, PELVIS_CONSTRAINT_SOURCE_PARAM),
                "pelvis_constraint_target": _param_value(model, PELVIS_CONSTRAINT_TARGET_PARAM),
                "pelvis_constraint_mode": _param_value(model, PELVIS_CONSTRAINT_MODE_PARAM),
                "target_pose_offsets": _target_pose_offsets_payload(model),
                "source_transform": dict((getattr(result, "source_context", None) or {}).get("transform_xform") or {}),
                "target_transform": dict((getattr(result, "target_context", None) or {}).get("transform_xform") or {}),
            },
            sort_keys=True,
            default=str,
        )
    except Exception:
        signature = str(time.time())
    now = float(time.time())
    try:
        last = getattr(model, "_retarget_joint_debug_snapshot_state", None)
    except Exception:
        last = None
    if isinstance(last, dict):
        try:
            if str(last.get("signature") or "") == signature and (now - float(last.get("time", 0.0) or 0.0)) < 2.0:
                return None
        except Exception:
            pass
    try:
        if model is not None:
            setattr(model, "_retarget_joint_debug_snapshot_state", {"signature": signature, "time": now})
    except Exception:
        pass
    try:
        path = write_anim_retarget_joint_debug_snapshot(node_item, result)
        _retarget_debug_log(
            "joint_debug_snapshot_auto",
            node=str(getattr(model, "name", "") or ""),
            reason=str(reason or ""),
            path=str(path),
        )
        return path
    except Exception as exc:
        _retarget_debug_log(
            "joint_debug_snapshot_auto_failed",
            node=str(getattr(model, "name", "") or ""),
            reason=str(reason or ""),
            error=repr(exc),
        )
        return None


def augment_infocard_footer(card, footer_layout) -> bool:
    if QtWidgets is None or QtGui is None or QtCore is None:
        return False

    node = getattr(card, "_node_ref", None)
    scene = getattr(card, "_graph_scene", None)
    if node is None or scene is None:
        return False
    kind = (getattr(node, "kind", "") or "").strip().lower()
    if kind not in ANIM_RETARGET_KIND_ALIASES:
        return False

    _ensure_hidden_params(node, HIDDEN_PARAMS)

    container = QtWidgets.QWidget(card)
    try:
        container.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Preferred,
        )
    except Exception:
        pass
    stack = QtWidgets.QVBoxLayout(container)
    stack.setContentsMargins(0, 0, 0, 0)
    stack.setSpacing(6)

    status_label = QtWidgets.QLabel("Status: unresolved")
    status_label.setStyleSheet("color:#94a3b8;")
    detail_box = QtWidgets.QPlainTextEdit("")
    detail_box.setReadOnly(True)
    detail_box.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
    detail_box.setMinimumHeight(96)
    try:
        detail_box.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
    except Exception:
        pass
    detail_box.setStyleSheet(
        "QPlainTextEdit {"
        "color:#cbd5e1;"
        "background:#0f172a;"
        "border:1px solid #1e293b;"
        "border-radius:4px;"
        "padding:6px;"
        "}"
    )
    map_table = QtWidgets.QTableWidget(0, 2)
    map_table.setHorizontalHeaderLabels(["Source Joint", "Target Joint"])
    map_table.setMinimumHeight(120)
    map_table.setAlternatingRowColors(True)
    try:
        try:
            no_edit = QtWidgets.QAbstractItemView.NoEditTriggers
        except Exception:
            no_edit = QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        map_table.setEditTriggers(no_edit)
        map_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        map_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        header = map_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
    except Exception:
        pass
    map_table.setStyleSheet(
        "QTableWidget {"
        "color:#cbd5e1;"
        "background:#0f172a;"
        "border:1px solid #1e293b;"
        "border-radius:4px;"
        "gridline-color:#1e293b;"
        "}"
        "QHeaderView::section {"
        "color:#e2e8f0;"
        "background:#111827;"
        "border:0;"
        "padding:4px;"
        "}"
    )

    tab_widget = QtWidgets.QTabWidget()
    try:
        tab_widget.setDocumentMode(True)
        tab_widget.setMinimumHeight(170)
    except Exception:
        pass
    tab_widget.setStyleSheet(
        "QTabWidget::pane{border:1px solid #1e293b;border-radius:4px;background:#0f172a;}"
        "QTabBar::tab{color:#cbd5e1;background:#111827;border:1px solid #1e293b;"
        "padding:5px 10px;margin-right:2px;border-top-left-radius:4px;border-top-right-radius:4px;}"
        "QTabBar::tab:selected{background:#1f2937;color:#f8fafc;}"
    )

    mapping_tab = QtWidgets.QWidget(tab_widget)
    mapping_layout = QtWidgets.QVBoxLayout(mapping_tab)
    mapping_layout.setContentsMargins(6, 6, 6, 6)
    mapping_layout.setSpacing(6)
    mapping_layout.addWidget(map_table, 1)

    settings_tab = QtWidgets.QWidget(tab_widget)
    settings_layout = QtWidgets.QVBoxLayout(settings_tab)
    settings_layout.setContentsMargins(8, 8, 8, 8)
    settings_layout.setSpacing(8)

    constraints_tab = QtWidgets.QWidget(tab_widget)
    constraints_layout = QtWidgets.QVBoxLayout(constraints_tab)
    constraints_layout.setContentsMargins(8, 8, 8, 8)
    constraints_layout.setSpacing(8)

    pelvis_link_label = QtWidgets.QLabel("Selected Pelvis Link")
    pelvis_link_label.setStyleSheet("color:#cbd5e1;")
    pelvis_link_label.setWordWrap(True)
    pelvis_link_table = QtWidgets.QTableWidget(0, 3)
    pelvis_link_table.setHorizontalHeaderLabels(["Source Pelvis", "Target Pelvis", "Constraint"])
    pelvis_link_table.setMinimumHeight(76)
    try:
        try:
            no_edit = QtWidgets.QAbstractItemView.NoEditTriggers
        except Exception:
            no_edit = QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        pelvis_link_table.setEditTriggers(no_edit)
        pelvis_link_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        pelvis_link_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        header = pelvis_link_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
    except Exception:
        pass
    pelvis_link_table.setStyleSheet(
        "QTableWidget {"
        "color:#cbd5e1;"
        "background:#0f172a;"
        "border:1px solid #1e293b;"
        "border-radius:4px;"
        "gridline-color:#1e293b;"
        "}"
        "QHeaderView::section {"
        "color:#e2e8f0;"
        "background:#111827;"
        "border:0;"
        "padding:4px;"
        "}"
    )
    pelvis_select_button = QtWidgets.QPushButton("Use Mapping Row")
    pelvis_select_button.setToolTip("Set the pelvis constraint from the selected Mapping tab row.")
    pelvis_mode_combo = QtWidgets.QComboBox()
    pelvis_mode_combo.addItem("None", "none")
    pelvis_mode_combo.addItem("Position Offset", "position_offset")
    pelvis_mode_combo.addItem("Snap", "snap")
    pelvis_mode_combo.setToolTip("Choose how the target pelvis translation follows the source pelvis.")
    try:
        pelvis_mode_combo.setMinimumWidth(150)
    except Exception:
        pass
    pelvis_mode_label = QtWidgets.QLabel("Motion")
    pelvis_mode_label.setStyleSheet("color:#cbd5e1;")
    pelvis_mode_row = QtWidgets.QHBoxLayout()
    pelvis_mode_row.setContentsMargins(0, 0, 0, 0)
    pelvis_mode_row.setSpacing(8)
    pelvis_mode_row.addWidget(pelvis_mode_label, 0)
    pelvis_mode_row.addWidget(pelvis_mode_combo, 1)
    constraints_layout.addWidget(pelvis_link_label, 0)
    constraints_layout.addWidget(pelvis_link_table, 0)
    constraints_layout.addWidget(pelvis_select_button, 0)
    constraints_layout.addLayout(pelvis_mode_row, 0)
    constraints_layout.addStretch(1)

    target_pose_tab = QtWidgets.QWidget(tab_widget)
    target_pose_layout = QtWidgets.QVBoxLayout(target_pose_tab)
    target_pose_layout.setContentsMargins(8, 8, 8, 8)
    target_pose_layout.setSpacing(8)
    target_pose_label = QtWidgets.QLabel("Selected Target Joint")
    target_pose_label.setStyleSheet("color:#cbd5e1;")
    target_pose_label.setWordWrap(True)
    target_pose_table = QtWidgets.QTableWidget(0, 3)
    target_pose_table.setHorizontalHeaderLabels(["Joint", "Position", "Rotation"])
    target_pose_table.setMinimumHeight(76)
    try:
        try:
            no_edit = QtWidgets.QAbstractItemView.NoEditTriggers
        except Exception:
            no_edit = QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        target_pose_table.setEditTriggers(no_edit)
        target_pose_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        target_pose_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        header = target_pose_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
    except Exception:
        pass
    target_pose_table.setStyleSheet(
        "QTableWidget {"
        "color:#cbd5e1;"
        "background:#0f172a;"
        "border:1px solid #1e293b;"
        "border-radius:4px;"
        "gridline-color:#1e293b;"
        "}"
        "QHeaderView::section {"
        "color:#e2e8f0;"
        "background:#111827;"
        "border:0;"
        "padding:4px;"
        "}"
    )
    target_pose_reset_button = QtWidgets.QPushButton("Reset Joint")
    target_pose_reset_all_button = QtWidgets.QPushButton("Reset All")
    target_pose_button_row = QtWidgets.QHBoxLayout()
    target_pose_button_row.setContentsMargins(0, 0, 0, 0)
    target_pose_button_row.setSpacing(8)
    target_pose_button_row.addWidget(target_pose_reset_button, 0)
    target_pose_button_row.addWidget(target_pose_reset_all_button, 0)
    target_pose_button_row.addStretch(1)
    target_pose_layout.addWidget(target_pose_label, 0)
    target_pose_layout.addWidget(target_pose_table, 0)
    target_pose_layout.addLayout(target_pose_button_row, 0)
    target_pose_layout.addStretch(1)

    def _slider_row(label_text: str, value_text: str, minimum: int, maximum: int, initial: int):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        label = QtWidgets.QLabel(label_text)
        label.setMinimumWidth(118)
        label.setStyleSheet("color:#cbd5e1;")
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(int(minimum), int(maximum))
        slider.setValue(max(int(minimum), min(int(maximum), int(initial))))
        slider.setToolTip(label_text)
        value_label = QtWidgets.QLabel(value_text)
        value_label.setMinimumWidth(48)
        value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        value_label.setStyleSheet("color:#94a3b8;")
        row.addWidget(label, 0)
        row.addWidget(slider, 1)
        row.addWidget(value_label, 0)
        settings_layout.addLayout(row, 0)
        return slider, value_label

    initial_handle_scale = _param_float(
        node,
        JOINT_HANDLE_SCALE_PARAM,
        JOINT_HANDLE_SCALE_DEFAULT,
        minimum=JOINT_HANDLE_SCALE_MIN,
        maximum=JOINT_HANDLE_SCALE_MAX,
    )
    initial_curve_thickness = _param_float(
        node,
        JOINT_CURVE_THICKNESS_PARAM,
        JOINT_CURVE_THICKNESS_DEFAULT,
        minimum=JOINT_CURVE_THICKNESS_MIN,
        maximum=JOINT_CURVE_THICKNESS_MAX,
    )
    initial_source_rest_pose = _param_bool(node, SOURCE_REST_POSE_PARAM, False)
    initial_preview_target_animation = _param_bool(node, PREVIEW_TARGET_MESH_PARAM, False)
    joint_handle_slider, joint_handle_value = _slider_row(
        "Joint Points",
        f"{initial_handle_scale:.2f}x",
        int(JOINT_HANDLE_SCALE_MIN * 100.0),
        int(JOINT_HANDLE_SCALE_MAX * 100.0),
        int(round(initial_handle_scale * 100.0)),
    )
    joint_curve_slider, joint_curve_value = _slider_row(
        "Curve Thickness",
        f"{initial_curve_thickness:.1f}px",
        int(JOINT_CURVE_THICKNESS_MIN * 10.0),
        int(JOINT_CURVE_THICKNESS_MAX * 10.0),
        int(round(initial_curve_thickness * 10.0)),
    )
    source_rest_pose_checkbox = QtWidgets.QCheckBox("Source Reference Pose")
    source_rest_pose_checkbox.setChecked(bool(initial_source_rest_pose))
    source_rest_pose_checkbox.setToolTip(
        "Use the source clip start pose as a static reference pose for the mapping preview."
    )
    source_rest_pose_checkbox.setStyleSheet("color:#cbd5e1;")
    settings_layout.addWidget(source_rest_pose_checkbox, 0)
    preview_target_animation_checkbox = QtWidgets.QCheckBox("Animate Target Skeleton")
    preview_target_animation_checkbox.setChecked(bool(initial_preview_target_animation))
    preview_target_animation_checkbox.setToolTip("Use the generated retarget clip on the target skeleton in this preview.")
    preview_target_animation_checkbox.setStyleSheet("color:#cbd5e1;")
    settings_layout.addWidget(preview_target_animation_checkbox, 0)
    settings_layout.addStretch(1)

    tab_widget.addTab(mapping_tab, "Mapping")
    tab_widget.addTab(constraints_tab, "Constraints")
    tab_widget.addTab(target_pose_tab, "Target Pose")
    tab_widget.addTab(settings_tab, "Settings")

    validate_button = QtWidgets.QPushButton("Validate")
    validate_button.setToolTip("Check that source and target inputs are connected.")
    view_button = QtWidgets.QPushButton("View")
    view_button.setToolTip("Open the source and target skeleton preview in the 3D viewport.")
    copy_button = QtWidgets.QPushButton("Copy Report")
    copy_button.setToolTip("Copy the retarget validation report to clipboard.")

    button_row = QtWidgets.QHBoxLayout()
    button_row.setContentsMargins(0, 0, 0, 0)
    button_row.setSpacing(8)
    button_row.addWidget(validate_button)
    button_row.addWidget(view_button)
    button_row.addWidget(copy_button)
    button_row.addStretch(1)

    stack.addLayout(button_row)
    stack.addWidget(status_label)
    stack.addWidget(detail_box, 1)
    stack.addWidget(tab_widget, 1)

    insert_idx = footer_layout.count()
    footer_layout.addWidget(container, 100)
    try:
        footer_layout.setStretch(insert_idx, 100)
    except Exception:
        pass

    report_holder: Dict[str, str] = {"value": ""}
    settings_syncing: Dict[str, bool] = {"value": False}
    view_state: Dict[str, bool] = {"opened": False}

    def _node_item():
        try:
            return scene._node_items.get(node.name)
        except Exception:
            return None

    def _set_retarget_pick_mode(mode: str = "") -> None:
        token = str(mode or "").strip().lower()
        normalized = token if token in {"pelvis_constraint", "target_pose"} else ""
        item = _node_item()
        model_obj = getattr(item, "model", None) if item is not None else node
        if model_obj is None:
            return
        try:
            setattr(model_obj, "_retarget_pick_mode", normalized)
        except Exception:
            pass

    def _on_retarget_tab_changed(index: int) -> None:
        try:
            active_widget = tab_widget.widget(int(index))
        except Exception:
            active_widget = None
        if active_widget is constraints_tab:
            _set_retarget_pick_mode("pelvis_constraint")
        elif active_widget is target_pose_tab:
            _set_retarget_pick_mode("target_pose")
        else:
            _set_retarget_pick_mode("")

    def _set_node_param(name: str, value: str, *, notify_scene: bool = True) -> None:
        item = _node_item()
        if item is None:
            return
        model_obj = getattr(item, "model", None)
        setter = getattr(item, "_set_param_value", None)
        setter_ok = False
        if callable(setter):
            try:
                setter(name, value, rebuild=False, notify_scene=bool(notify_scene))
                setter_ok = True
            except TypeError:
                try:
                    setter(name, value)
                    setter_ok = True
                except Exception:
                    pass
            except Exception:
                pass
        _set_param_value(model_obj, name, value)
        try:
            if bool(notify_scene) and model_obj is not None and hasattr(scene, "paramChanged") and not setter_ok:
                scene.paramChanged.emit(
                    getattr(model_obj, "name", "") or "",
                    list(getattr(model_obj, "params", None) or []),
                )
        except Exception:
            pass

    def _set_pelvis_mode_combo(mode: str) -> None:
        normalized = _normalize_pelvis_constraint_mode(mode)
        index = 0
        try:
            for row in range(pelvis_mode_combo.count()):
                data = pelvis_mode_combo.itemData(row)
                if _normalize_pelvis_constraint_mode(str(data or "")) == normalized:
                    index = row
                    break
        except Exception:
            index = 0
        pelvis_mode_combo.setCurrentIndex(index)

    def _pelvis_mode_combo_value() -> str:
        try:
            return _normalize_pelvis_constraint_mode(str(pelvis_mode_combo.currentData() or "none"))
        except Exception:
            return "none"

    def _display_pelvis_mode(mode: str) -> str:
        normalized = _normalize_pelvis_constraint_mode(mode)
        if normalized == "snap":
            return "Snap"
        if normalized == "position_offset":
            return "Position Offset"
        return "None"

    def _set_pelvis_link_label(source_name: str, target_name: str, mode: str | None = None) -> None:
        source_text = str(source_name or "").strip()
        target_text = str(target_name or "").strip()
        mode_text = _display_pelvis_mode(mode if mode is not None else _pelvis_mode_combo_value())
        if source_text and target_text:
            pelvis_link_label.setText("Selected Pelvis Link")
            try:
                pelvis_link_table.setRowCount(1)
                pelvis_link_table.setItem(0, 0, QtWidgets.QTableWidgetItem(source_text))
                pelvis_link_table.setItem(0, 1, QtWidgets.QTableWidgetItem(target_text))
                pelvis_link_table.setItem(0, 2, QtWidgets.QTableWidgetItem(mode_text))
            except Exception:
                pass
        else:
            pelvis_link_label.setText("Selected Pelvis Link")
            try:
                pelvis_link_table.setRowCount(0)
            except Exception:
                pass

    def _refresh_mapping_table_only(model_obj=None) -> None:
        if model_obj is None:
            item = _node_item()
            model_obj = getattr(item, "model", None) if item is not None else node
        mapping = _joint_map_payload(model_obj)
        try:
            map_table.setUpdatesEnabled(False)
            map_table.setRowCount(len(mapping))
            for row, source_name in enumerate(sorted(mapping.keys(), key=lambda value: value.lower())):
                target_name = mapping.get(source_name, "")
                map_table.setItem(row, 0, QtWidgets.QTableWidgetItem(source_name))
                map_table.setItem(row, 1, QtWidgets.QTableWidgetItem(target_name))
        finally:
            try:
                map_table.setUpdatesEnabled(True)
            except Exception:
                pass

    def _register_mapping_refresh_callback() -> None:
        item = _node_item()
        model_obj = getattr(item, "model", None) if item is not None else node
        if model_obj is None:
            return
        try:
            setattr(model_obj, "_retarget_mapping_table_refresh", _refresh_mapping_table_only)
        except Exception:
            pass

    def _clear_mapping_refresh_callback() -> None:
        item = _node_item()
        model_obj = getattr(item, "model", None) if item is not None else node
        callback = getattr(model_obj, "_retarget_mapping_table_refresh", None) if model_obj is not None else None
        if callback is not _refresh_mapping_table_only:
            return
        try:
            delattr(model_obj, "_retarget_mapping_table_refresh")
        except Exception:
            try:
                setattr(model_obj, "_retarget_mapping_table_refresh", None)
            except Exception:
                pass

    def _format_target_pose_vec(values, *, default=(0.0, 0.0, 0.0)) -> str:
        x, y, z = _clean_vec3(values, default=default)
        return f"{x:.3f}, {y:.3f}, {z:.3f}"

    def _refresh_target_pose_table_only(model_obj=None) -> None:
        if model_obj is None:
            item = _node_item()
            model_obj = getattr(item, "model", None) if item is not None else node
        selected_name = _param_value(model_obj, TARGET_POSE_SELECTED_JOINT_PARAM)
        payload = _target_pose_offsets_payload(model_obj)
        selected_name = str(selected_name or "").strip()
        try:
            target_pose_table.setUpdatesEnabled(False)
            if selected_name:
                row = dict(payload.get(selected_name) or {})
                target_pose_table.setRowCount(1)
                target_pose_table.setItem(0, 0, QtWidgets.QTableWidgetItem(selected_name))
                target_pose_table.setItem(
                    0,
                    1,
                    QtWidgets.QTableWidgetItem(
                        _format_target_pose_vec(row.get("position"))
                        if "position" in row
                        else "bind"
                    ),
                )
                target_pose_table.setItem(
                    0,
                    2,
                    QtWidgets.QTableWidgetItem(
                        _format_target_pose_vec(row.get("rotation"))
                        if "rotation" in row
                        else "0.000, 0.000, 0.000"
                    ),
                )
            else:
                target_pose_table.setRowCount(0)
        finally:
            try:
                target_pose_table.setUpdatesEnabled(True)
            except Exception:
                pass
        try:
            target_pose_reset_button.setEnabled(bool(selected_name and selected_name in payload))
            target_pose_reset_all_button.setEnabled(bool(payload))
        except Exception:
            pass

    def _register_target_pose_refresh_callback() -> None:
        item = _node_item()
        model_obj = getattr(item, "model", None) if item is not None else node
        if model_obj is None:
            return
        try:
            setattr(model_obj, "_retarget_target_pose_table_refresh", _refresh_target_pose_table_only)
        except Exception:
            pass

    def _clear_target_pose_refresh_callback() -> None:
        item = _node_item()
        model_obj = getattr(item, "model", None) if item is not None else node
        callback = getattr(model_obj, "_retarget_target_pose_table_refresh", None) if model_obj is not None else None
        if callback is not _refresh_target_pose_table_only:
            return
        try:
            delattr(model_obj, "_retarget_target_pose_table_refresh")
        except Exception:
            try:
                setattr(model_obj, "_retarget_target_pose_table_refresh", None)
            except Exception:
                pass

    def _sync_settings_sliders(model_obj) -> None:
        handle_value = _param_float(
            model_obj,
            JOINT_HANDLE_SCALE_PARAM,
            JOINT_HANDLE_SCALE_DEFAULT,
            minimum=JOINT_HANDLE_SCALE_MIN,
            maximum=JOINT_HANDLE_SCALE_MAX,
        )
        curve_value = _param_float(
            model_obj,
            JOINT_CURVE_THICKNESS_PARAM,
            JOINT_CURVE_THICKNESS_DEFAULT,
            minimum=JOINT_CURVE_THICKNESS_MIN,
            maximum=JOINT_CURVE_THICKNESS_MAX,
        )
        source_rest_pose = _param_bool(model_obj, SOURCE_REST_POSE_PARAM, False)
        preview_target_animation = _param_bool(model_obj, PREVIEW_TARGET_MESH_PARAM, False)
        pelvis_source = _param_value(model_obj, PELVIS_CONSTRAINT_SOURCE_PARAM)
        pelvis_target = _param_value(model_obj, PELVIS_CONSTRAINT_TARGET_PARAM)
        pelvis_mode = _param_value(model_obj, PELVIS_CONSTRAINT_MODE_PARAM) or "none"
        settings_syncing["value"] = True
        try:
            joint_handle_slider.blockSignals(True)
            joint_curve_slider.blockSignals(True)
            source_rest_pose_checkbox.blockSignals(True)
            preview_target_animation_checkbox.blockSignals(True)
            pelvis_mode_combo.blockSignals(True)
            joint_handle_slider.setValue(int(round(handle_value * 100.0)))
            joint_curve_slider.setValue(int(round(curve_value * 10.0)))
            source_rest_pose_checkbox.setChecked(bool(source_rest_pose))
            preview_target_animation_checkbox.setChecked(bool(preview_target_animation))
            _set_pelvis_mode_combo(pelvis_mode)
            _set_pelvis_link_label(pelvis_source, pelvis_target, pelvis_mode)
            joint_handle_value.setText(f"{handle_value:.2f}x")
            joint_curve_value.setText(f"{curve_value:.1f}px")
        finally:
            try:
                joint_handle_slider.blockSignals(False)
            except Exception:
                pass
            try:
                joint_curve_slider.blockSignals(False)
            except Exception:
                pass
            try:
                source_rest_pose_checkbox.blockSignals(False)
            except Exception:
                pass
            try:
                preview_target_animation_checkbox.blockSignals(False)
            except Exception:
                pass
            try:
                pelvis_mode_combo.blockSignals(False)
            except Exception:
                pass
            settings_syncing["value"] = False
        _refresh_target_pose_table_only(model_obj)

    def _refresh_retarget_view_from_settings() -> None:
        if not bool(view_state.get("opened", False)):
            return
        try:
            _on_view_clicked(frame=False, quiet=True)
        except Exception:
            pass

    def _on_joint_handle_changed(raw_value: int) -> None:
        value = max(JOINT_HANDLE_SCALE_MIN, min(JOINT_HANDLE_SCALE_MAX, float(raw_value) / 100.0))
        joint_handle_value.setText(f"{value:.2f}x")
        if bool(settings_syncing.get("value", False)):
            return
        _set_node_param(JOINT_HANDLE_SCALE_PARAM, f"{value:.2f}", notify_scene=False)

    def _on_joint_curve_changed(raw_value: int) -> None:
        value = max(JOINT_CURVE_THICKNESS_MIN, min(JOINT_CURVE_THICKNESS_MAX, float(raw_value) / 10.0))
        joint_curve_value.setText(f"{value:.1f}px")
        if bool(settings_syncing.get("value", False)):
            return
        _set_node_param(JOINT_CURVE_THICKNESS_PARAM, f"{value:.1f}", notify_scene=False)

    def _on_joint_handle_released() -> None:
        value = max(JOINT_HANDLE_SCALE_MIN, min(JOINT_HANDLE_SCALE_MAX, float(joint_handle_slider.value()) / 100.0))
        _set_node_param(JOINT_HANDLE_SCALE_PARAM, f"{value:.2f}", notify_scene=True)
        _refresh_retarget_view_from_settings()

    def _on_joint_curve_released() -> None:
        value = max(
            JOINT_CURVE_THICKNESS_MIN,
            min(JOINT_CURVE_THICKNESS_MAX, float(joint_curve_slider.value()) / 10.0),
        )
        _set_node_param(JOINT_CURVE_THICKNESS_PARAM, f"{value:.1f}", notify_scene=True)
        _refresh_retarget_view_from_settings()

    def _on_preview_target_animation_changed(raw_state: int) -> None:
        if bool(settings_syncing.get("value", False)):
            return
        checked = bool(raw_state)
        try:
            checked = bool(preview_target_animation_checkbox.isChecked())
        except Exception:
            pass
        _set_node_param(PREVIEW_TARGET_MESH_PARAM, "1" if checked else "0", notify_scene=True)
        _refresh_retarget_view_from_settings()

    def _on_source_rest_pose_changed(raw_state: int) -> None:
        if bool(settings_syncing.get("value", False)):
            return
        checked = bool(raw_state)
        try:
            checked = bool(source_rest_pose_checkbox.isChecked())
        except Exception:
            pass
        _set_node_param(SOURCE_REST_POSE_PARAM, "1" if checked else "0", notify_scene=True)
        _refresh_retarget_view_from_settings()

    def _selected_mapping_link() -> Tuple[str, str]:
        row = -1
        try:
            row = int(map_table.currentRow())
        except Exception:
            row = -1
        if row < 0:
            try:
                indexes = list(map_table.selectedIndexes() or [])
                if indexes:
                    row = int(indexes[0].row())
            except Exception:
                row = -1
        if row < 0:
            return "", ""
        try:
            source_item = map_table.item(row, 0)
            target_item = map_table.item(row, 1)
            source_name = str(source_item.text() if source_item is not None else "").strip()
            target_name = str(target_item.text() if target_item is not None else "").strip()
            return source_name, target_name
        except Exception:
            return "", ""

    def _on_pelvis_select_clicked() -> None:
        source_name, target_name = _selected_mapping_link()
        if not source_name or not target_name:
            try:
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Select a mapping row first.", card)
            except Exception:
                pass
            return
        item = _node_item()
        if item is None:
            return
        payload = set_pelvis_constraint_link(
            item,
            source_name,
            target_name,
            mode=_pelvis_mode_combo_value(),
            notify_scene=True,
        )
        settings_syncing["value"] = True
        try:
            pelvis_mode_combo.blockSignals(True)
            _set_pelvis_mode_combo(str(payload.get("mode") or "none"))
        finally:
            try:
                pelvis_mode_combo.blockSignals(False)
            except Exception:
                pass
            settings_syncing["value"] = False
        _set_pelvis_link_label(
            str(payload.get("source") or source_name),
            str(payload.get("target") or target_name),
            str(payload.get("mode") or _pelvis_mode_combo_value()),
        )
        _refresh_retarget_view_from_settings()

    def _on_pelvis_mode_changed(*_args) -> None:
        if bool(settings_syncing.get("value", False)):
            return
        mode = _pelvis_mode_combo_value()
        _set_node_param(PELVIS_CONSTRAINT_MODE_PARAM, mode, notify_scene=True)
        item = _node_item()
        model_obj = getattr(item, "model", None) if item is not None else None
        _set_pelvis_link_label(
            _param_value(model_obj, PELVIS_CONSTRAINT_SOURCE_PARAM),
            _param_value(model_obj, PELVIS_CONSTRAINT_TARGET_PARAM),
            mode,
        )
        _refresh_retarget_view_from_settings()

    def _on_target_pose_reset_joint_clicked() -> None:
        item = _node_item()
        if item is None:
            return
        model_obj = getattr(item, "model", None)
        selected_name = _param_value(model_obj, TARGET_POSE_SELECTED_JOINT_PARAM)
        selected_name = str(selected_name or "").strip()
        if not selected_name:
            try:
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Select a target joint first.", card)
            except Exception:
                pass
            return
        reset_target_pose_joint(item, selected_name, notify_scene=True)
        _refresh_target_pose_table_only(model_obj)
        _refresh_retarget_view_from_settings()

    def _on_target_pose_reset_all_clicked() -> None:
        item = _node_item()
        if item is None:
            return
        reset_target_pose_all(item, notify_scene=True)
        _refresh_target_pose_table_only(getattr(item, "model", None))
        _refresh_retarget_view_from_settings()

    def _refresh(*_args, persist: bool = False, toast: bool = False):
        item = _node_item()
        if item is None:
            status_label.setText("Status: no node item")
            status_label.setStyleSheet("color:#f59e0b;")
            detail_box.setPlainText("Connect this node to the graph canvas.")
            return None
        _ensure_display_params(item)
        _sync_settings_sliders(getattr(item, "model", None))
        result = resolve_anim_retarget_inputs(item, persist=bool(persist))
        if result.status == "error":
            label, color = "ERROR", "#ef4444"
        elif result.status == "warning":
            label, color = "WARNING", "#f59e0b"
        else:
            label, color = "OK", "#22c55e"
        status_label.setText(f"Status: {label}")
        status_label.setStyleSheet(f"color:{color};")
        source_line = f"source: {result.source_name or '<none>'}"
        if result.source_name:
            source_line += f" | joints={int(result.source_joint_count)} clips={int(result.source_clip_count)}"
        target_line = f"target: {result.target_name or '<none>'}"
        if result.target_name:
            target_line += f" | joints={int(result.target_joint_count)} meshes={int(result.target_mesh_count)}"
        lines = [source_line, target_line, f"joint links: {int(result.joint_map_count)}"]
        if result.errors:
            lines.append(f"errors: {len(result.errors)}")
        if result.warnings:
            lines.append(f"warnings: {len(result.warnings)}")
        detail_box.setPlainText("\n".join(lines))
        _register_mapping_refresh_callback()
        _register_target_pose_refresh_callback()
        _refresh_mapping_table_only(getattr(item, "model", None))
        _refresh_target_pose_table_only(getattr(item, "model", None))
        report = "\n".join(result.message_lines())
        report_holder["value"] = report
        detail_box.setToolTip(report)
        if toast:
            if result.status in ("error", "warning"):
                QtWidgets.QMessageBox.warning(card, "Anim Retarget Validation", report)
            else:
                try:
                    QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), report, card)
                except Exception:
                    pass
        return result

    def _copy_report() -> None:
        text = str(report_holder.get("value") or "").strip()
        if not text:
            text = str(detail_box.toPlainText() or "").strip()
        if not text:
            return
        try:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
        except Exception:
            pass
        try:
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Retarget report copied.", card)
        except Exception:
            pass

    validate_button.clicked.connect(lambda: _refresh(persist=True, toast=True))
    copy_button.clicked.connect(_copy_report)

    def _on_view_clicked(*, frame: bool = True, quiet: bool = False) -> None:
        item = _node_item()
        if item is None:
            if quiet:
                return
            QtWidgets.QMessageBox.warning(card, "Anim Retarget View", "Node item is not available.")
            return
        result = _refresh(persist=True, toast=False)
        if result is None:
            return
        if result.status == "error":
            if quiet:
                return
            QtWidgets.QMessageBox.warning(
                card,
                "Anim Retarget View",
                "\n".join(result.message_lines()),
            )
            return
        assets = build_anim_retarget_preview_assets(item, result)
        if len(assets) < 2:
            if quiet:
                return
            QtWidgets.QMessageBox.warning(
                card,
                "Anim Retarget View",
                "Source and target skeleton preview assets could not be built.",
            )
            return

        win = card.window()
        scene_handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(scene_handler):
            if quiet:
                return
            QtWidgets.QMessageBox.warning(card, "Anim Retarget View", "3D view is not available.")
            return
        try:
            scene_handler(assets, frame=bool(frame))
        except TypeError:
            try:
                scene_handler(assets)
            except Exception as exc:
                if quiet:
                    return
                QtWidgets.QMessageBox.warning(card, "Anim Retarget View", f"3D view failed: {exc}")
                return
        except Exception as exc:
            if quiet:
                return
            QtWidgets.QMessageBox.warning(card, "Anim Retarget View", f"3D view failed: {exc}")
            return
        view_state["opened"] = True

    view_button.clicked.connect(lambda: _on_view_clicked(frame=True, quiet=False))
    joint_handle_slider.valueChanged.connect(_on_joint_handle_changed)
    joint_curve_slider.valueChanged.connect(_on_joint_curve_changed)
    joint_handle_slider.sliderReleased.connect(_on_joint_handle_released)
    joint_curve_slider.sliderReleased.connect(_on_joint_curve_released)
    source_rest_pose_checkbox.stateChanged.connect(_on_source_rest_pose_changed)
    preview_target_animation_checkbox.stateChanged.connect(_on_preview_target_animation_changed)
    pelvis_select_button.clicked.connect(_on_pelvis_select_clicked)
    pelvis_mode_combo.currentIndexChanged.connect(_on_pelvis_mode_changed)
    target_pose_reset_button.clicked.connect(_on_target_pose_reset_joint_clicked)
    target_pose_reset_all_button.clicked.connect(_on_target_pose_reset_all_clicked)
    tab_widget.currentChanged.connect(_on_retarget_tab_changed)
    try:
        container.destroyed.connect(
            lambda *_args: (
                _set_retarget_pick_mode(""),
                _clear_mapping_refresh_callback(),
                _clear_target_pose_refresh_callback(),
            )
        )
    except Exception:
        pass

    def _on_links_changed(*_args):
        _refresh(persist=False, toast=False)

    def _on_param_changed(changed_name, *_args):
        item = _node_item()
        if item is None:
            return
        if _param_change_relevant(item, changed_name):
            _refresh(persist=False, toast=False)

    try:
        scene.linksChanged.connect(_on_links_changed)
    except Exception:
        pass
    try:
        scene.paramChanged.connect(_on_param_changed)
    except Exception:
        pass

    _on_retarget_tab_changed(tab_widget.currentIndex())
    _refresh(persist=False, toast=False)
    return True


ANIM_RETARGET_SPEC = Spec(
    stripe_color="#ec4899",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)


__all__ = [
    "ANIM_RETARGET_KIND_ALIASES",
    "SOURCE_KIND_ALIASES",
    "TARGET_KIND_ALIASES",
    "RetargetSourceTargetResult",
    "build_ports",
    "resolve_anim_retarget_inputs",
    "build_anim_retarget_preview_assets",
    "build_anim_retarget_clip",
    "build_anim_retarget_scene_asset",
    "write_anim_retarget_joint_debug_snapshot",
    "set_joint_map_link",
    "remove_joint_map_link",
    "set_pelvis_constraint_link",
    "target_pose_skeleton_for_model",
    "set_target_pose_selected_joint",
    "set_target_pose_joint_position",
    "set_target_pose_joint_rotation",
    "reset_target_pose_joint",
    "reset_target_pose_all",
    "augment_infocard_footer",
    "ANIM_RETARGET_SPEC",
]
