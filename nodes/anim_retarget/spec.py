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

VISIBLE_PORTS: Tuple[str, str] = ("source", "target")
HIDDEN_PARAMS: Tuple[str, ...] = (
    "joint_map",
    "joint_handle_scale",
    "joint_curve_thickness",
    "display_params_version",
    "root_source",
    "root_target",
    "scale_mode",
    "rotation_mode",
    "debug_log",
)

JOINT_HANDLE_SCALE_PARAM = "joint_handle_scale"
JOINT_CURVE_THICKNESS_PARAM = "joint_curve_thickness"
DISPLAY_PARAMS_VERSION_PARAM = "display_params_version"
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


def _context_for_source_item(node_item, kind: str, errors: List[str], warnings: List[str]) -> Dict[str, Any] | None:
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
    return None


def _context_for_target_item(node_item, kind: str, errors: List[str], warnings: List[str]) -> Dict[str, Any] | None:
    if kind in TARGET_KIND_ALIASES:
        return _fbx_context_from_item(node_item, "target", errors, warnings, require_clip=False)
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
        errors.append("source: connect a Mocap Import or FBX Import node.")
    elif source_kind not in SOURCE_KIND_ALIASES:
        errors.append(f"source: unsupported node kind '{source_kind or '<none>'}'.")

    if target_model is None:
        errors.append("target: connect an FBX Import node.")
    elif target_kind not in TARGET_KIND_ALIASES:
        errors.append(f"target: unsupported node kind '{target_kind or '<none>'}'; expected FBX Import.")

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
        if source_model is not None and source_kind in SOURCE_KIND_ALIASES:
            source_context = _context_for_source_item(source_item, source_kind, errors, warnings)
        if target_model is not None and target_kind in TARGET_KIND_ALIASES:
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
        parent_index = int(getattr(joint, "parent_index", -1) or -1)
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
        parent_index = int(getattr(joint, "parent_index", -1) or -1)
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
    rig["skeleton"] = _preview_skeleton(context, role)
    rig["clip"] = context.get("clip") if is_source else None
    rig["meshes"] = []
    rig["loop"] = True
    rig["mesh_skinning_enabled"] = False
    rig["skin_weight_debug"] = False
    rig["show_capture_joints"] = not is_source
    rig["show_animated_joints"] = bool(is_source)
    rig.pop("retarget_static_pose", None)
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
    return {
        "path": path_text,
        "texture": "",
        "node": owner,
        "ext": ".bvh",
        "visible": True,
        "xform": {
            "pos": [float(x_offset), 0.0, 0.0],
            "rot": [0.0, 0.0, 0.0],
            "scl": [1.0, 1.0, 1.0],
        },
        "fbx_rig_context": rig_context,
        "fbx_debug_log": bool(rig_context.get("fbx_debug_log", False)),
    }


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

    source_handle_radius = _handle_radius_for_context(source_context)
    target_handle_radius = _handle_radius_for_context(target_context)
    handle_radius = max(source_handle_radius, target_handle_radius)
    base_name = str(getattr(model, "name", "") or "").strip() or "Anim Retarget"
    source_owner = f"{base_name} Source"
    target_owner = f"{base_name} Target"
    source_asset = _preview_asset_for_context(
        source_context,
        owner=source_owner,
        role="source",
        x_offset=0.0,
        curve_thickness=curve_thickness,
    )
    target_asset = _preview_asset_for_context(
        target_context,
        owner=target_owner,
        role="target",
        x_offset=0.0,
        curve_thickness=curve_thickness,
    )

    def _handles(context: Dict[str, Any], role: str, radius: float) -> List[Dict[str, Any]]:
        skeleton = _preview_skeleton(context, role)
        role_key = str(role or "").strip().lower()
        positions = _skeleton_joint_positions(
            skeleton,
            clip=context.get("clip") if role_key == "source" else None,
            prefer_inverse_bind=bool(role_key != "source"),
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

    source_handles = _handles(source_context, "source", source_handle_radius)
    target_handles = _handles(target_context, "target", target_handle_radius)
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
        source_pose="animated_clip_frame0",
        target_pose="capture_inverse_bind",
        source_skeleton=_points_summary(
            _skeleton_joint_positions(source_context.get("skeleton"), clip=source_context.get("clip"))
        ),
        target_skeleton=_points_summary(
            _skeleton_joint_positions(target_context.get("skeleton"), prefer_inverse_bind=True)
        ),
        source_handles=_points_summary([row.get("position") for row in source_handles]),
        target_handles=_points_summary([row.get("position") for row in target_handles]),
    )
    return [asset for asset in (source_asset, target_asset, preview_asset) if isinstance(asset, dict)]


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
    settings_layout.addStretch(1)

    tab_widget.addTab(mapping_tab, "Mapping")
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
        settings_syncing["value"] = True
        try:
            joint_handle_slider.blockSignals(True)
            joint_curve_slider.blockSignals(True)
            joint_handle_slider.setValue(int(round(handle_value * 100.0)))
            joint_curve_slider.setValue(int(round(curve_value * 10.0)))
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
            settings_syncing["value"] = False

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
        mapping = _joint_map_payload(getattr(item, "model", None))
        try:
            map_table.setUpdatesEnabled(False)
            map_table.setRowCount(len(mapping))
            for row, source_name in enumerate(sorted(mapping.keys(), key=lambda value: value.lower())):
                target_name = mapping.get(source_name, "")
                src_item = QtWidgets.QTableWidgetItem(source_name)
                dst_item = QtWidgets.QTableWidgetItem(target_name)
                map_table.setItem(row, 0, src_item)
                map_table.setItem(row, 1, dst_item)
        finally:
            try:
                map_table.setUpdatesEnabled(True)
            except Exception:
                pass
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
    "set_joint_map_link",
    "augment_infocard_footer",
    "ANIM_RETARGET_SPEC",
]
