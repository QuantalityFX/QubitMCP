from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant
from echograph.rigging.groom_deform import (
    GroomDeformError,
    deform_groom_curves,
    transfer_groom_root_skin_weights,
)


KIND_ALIASES = {
    "groom_deform",
    "groom deform",
    "groomdeform",
    "hair_deform",
    "hair deform",
}
GUIDE_KIND_ALIASES = {"groom_guides", "groom guides", "hair_guides", "hair guides"}
RIG_KIND_ALIASES = {"anim_retarget", "anim retarget", "animretarget", "retarget", "fbx_import", "fbx import", "fbximport"}
HIDDEN_PARAMS = [
    "source",
    "path",
    "hide_deformer_geo",
    "mode",
    "debug_log",
]
GROOM_DEFORM_DEBUG_KEYS = (
    "guides_node",
    "guides_kind",
    "rig_node",
    "rig_kind",
    "diagnosis",
    "renderer_debug_log",
    "renderer_expected_tag",
    "asset_kind",
    "asset_source_kind",
    "has_groom_deform_payload",
    "has_deform_rig_context",
    "guide_count",
    "points_per_curve",
    "curve_count",
    "bind_curve_count",
    "guide_binding_count",
    "line_point_count",
    "line_segment_count",
    "non_degenerate_segments",
    "root_point_count",
    "line_bounds_min",
    "line_bounds_max",
    "source_owner",
    "rig_owner",
    "deformer_owner",
    "sample_owner",
    "source_path",
    "guides_path",
    "source_assets_count",
    "deformer_assets_count",
    "hide_deformer_geo",
    "skin_influence_bound_guides",
    "skin_influence_missing_guides",
    "triangle_bound_guides",
    "scalp_attached_guides",
    "bound_guides",
    "missing_guides",
    "joint_count",
    "mesh_count",
    "clip_name",
    "clip_start",
    "clip_end",
    "mode",
)


@dataclass(frozen=True)
class GroomDeformBuildOutcome:
    asset: Optional[dict[str, Any]]
    source_assets: tuple[dict[str, Any], ...]
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
    text = _param_value(model, name, "1" if default else "0").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


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


def _set_param(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    setter = getattr(node_item, "_set_param_value", None)
    if callable(setter):
        try:
            setter(name, str(value), rebuild=False, notify_scene=bool(notify_scene))
            return
        except Exception:
            pass
    _ensure_param(node_item, name, str(value))
    model = getattr(node_item, "model", None)
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value)
            return


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
    entry = None
    for param in params:
        if isinstance(param, dict) and (param.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = param
            break
    if entry is None:
        return
    hidden = {part.strip().lower() for part in str(entry.get("value") or "").split(",") if part.strip()}
    for name in names or []:
        if name:
            hidden.discard(str(name).strip().lower())
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
        dst = _edge_dst_name(edge).lower()
        if dst in names:
            return getattr(edge, "src", None)
    if kind_fallbacks:
        allowed = {str(kind).strip().lower() for kind in kind_fallbacks if str(kind).strip()}
        for edge in edges:
            src = getattr(edge, "src", None)
            if _node_kind(src) in allowed:
                return src
    return None


def _node_kind(item) -> str:
    return str(getattr(getattr(item, "model", None), "kind", "") or "").strip().lower()


def _node_name(item) -> str:
    return str(getattr(getattr(item, "model", None), "name", "") or "").strip()


def _guides_asset_from_item(item) -> tuple[Optional[dict[str, Any]], tuple[dict[str, Any], ...], str]:
    if item is None:
        return None, tuple(), "Connect Groom Guides to the guides input."
    kind = _node_kind(item)
    if kind not in GUIDE_KIND_ALIASES:
        return None, tuple(), f"Unsupported guides input: {kind or '<none>'}."
    try:
        from nodes.groom_guides import spec as guides_spec  # type: ignore

        build = getattr(guides_spec, "build_groom_guides_scene_asset", None)
        outcome = build(item) if callable(build) else None
    except Exception as exc:
        return None, tuple(), f"Groom Guides build failed: {exc}"
    asset = getattr(outcome, "asset", None)
    source_assets = tuple(dict(entry) for entry in (getattr(outcome, "source_assets", None) or tuple()) if isinstance(entry, dict))
    if not isinstance(asset, dict):
        detail = str(getattr(outcome, "detail", "") or "Groom Guides did not produce a valid asset.")
        return None, source_assets, detail
    return dict(asset), source_assets, ""


def _rig_asset_from_item(item) -> tuple[Optional[dict[str, Any]], str]:
    if item is None:
        return None, "Connect Anim Retarget to the rig input."
    kind = _node_kind(item)
    if kind in {"anim_retarget", "anim retarget", "animretarget", "retarget"}:
        try:
            from nodes.anim_retarget import spec as anim_spec  # type: ignore

            build = getattr(anim_spec, "build_anim_retarget_scene_asset", None)
            asset = build(item) if callable(build) else None
        except Exception as exc:
            return None, f"Anim Retarget asset build failed: {exc}"
        if not isinstance(asset, dict):
            return None, "Anim Retarget did not produce a valid rig asset."
        return dict(asset), ""
    if kind in {"fbx_import", "fbx import", "fbximport"}:
        try:
            from nodes.scene import spec as scene_spec  # type: ignore

            rig_fn = getattr(scene_spec, "_fbx_import_rig_context", None)
            rig_context = rig_fn(getattr(item, "model", None)) if callable(rig_fn) else None
        except Exception as exc:
            return None, f"FBX rig context failed: {exc}"
        if not isinstance(rig_context, dict):
            return None, "FBX Import did not expose a rig context."
        path = ""
        try:
            path = str(getattr(getattr(item, "model", None), "_fbx_resolved_rest_geometry", "") or "")
        except Exception:
            path = ""
        if not path:
            path = _param_value(getattr(item, "model", None), "path") or _param_value(getattr(item, "model", None), "rest_geometry")
        return {
            "kind": "fbx_import",
            "node": _node_name(item) or (Path(path).stem if path else "FBX Rig"),
            "path": path,
            "ext": Path(path).suffix.lower() if path else ".fbx",
            "fbx_rig_context": rig_context,
        }, ""
    return None, f"Unsupported rig input: {kind or '<none>'}."


def build_groom_deform_scene_asset(node_item) -> GroomDeformBuildOutcome:
    model = getattr(node_item, "model", None)
    guides_item = _connected_input_item(node_item, {"guides", "guide", "source"}, GUIDE_KIND_ALIASES)
    rig_item = _connected_input_item(node_item, {"rig", "skeleton", "animation"}, RIG_KIND_ALIASES)
    guides_asset, source_assets, guides_error = _guides_asset_from_item(guides_item)
    if not isinstance(guides_asset, dict):
        return GroomDeformBuildOutcome(None, source_assets, "error", guides_error, {})
    rig_asset, rig_error = _rig_asset_from_item(rig_item)
    if not isinstance(rig_asset, dict):
        return GroomDeformBuildOutcome(None, source_assets, "error", rig_error, {})
    rig_context = rig_asset.get("fbx_rig_context")
    if not isinstance(rig_context, dict):
        return GroomDeformBuildOutcome(None, source_assets, "error", "Rig input has no fbx_rig_context.", {})

    curves = list(guides_asset.get("curves") or [])
    guide_bindings = list(guides_asset.get("guide_bindings") or [])
    if not curves:
        return GroomDeformBuildOutcome(None, source_assets, "error", "Guides input has no curves.", {})
    if not guide_bindings:
        return GroomDeformBuildOutcome(None, source_assets, "error", "Guides input has no scalp bind data. Regenerate Groom Guides.", {})
    guide_bindings = [dict(entry) for entry in guide_bindings if isinstance(entry, dict)]
    if guide_bindings and any(not list(entry.get("skin_influences") or []) for entry in guide_bindings):
        try:
            root_positions = []
            for idx, binding in enumerate(guide_bindings):
                root = binding.get("bind_position")
                if not root and idx < len(curves) and isinstance(curves[idx], list) and curves[idx]:
                    root = curves[idx][0]
                root_positions.append(list(root or (0.0, 0.0, 0.0)))
            skin_bindings = transfer_groom_root_skin_weights(
                root_positions,
                rig_context,
                hidden_submeshes=list(guides_asset.get("hidden_submeshes") or []),
                preferred_bindings=guide_bindings,
            )
        except Exception:
            skin_bindings = []
        for idx, binding in enumerate(guide_bindings):
            if list(binding.get("skin_influences") or []):
                continue
            skin = skin_bindings[idx] if idx < len(skin_bindings) and isinstance(skin_bindings[idx], dict) else {}
            binding["skin_mesh_name"] = str(
                skin.get("mesh_name")
                or binding.get("skin_mesh_name")
                or binding.get("source_mesh_name")
                or ""
            )
            binding["skin_triangle_index"] = int(skin.get("triangle_index", binding.get("skin_triangle_index", -1)) or -1)
            binding["skin_triangle_vertices"] = list(skin.get("triangle_vertices") or binding.get("skin_triangle_vertices") or [])
            binding["skin_barycentric"] = list(skin.get("barycentric") or binding.get("skin_barycentric") or [])
            binding["skin_influences"] = list(skin.get("influences") or [])
            try:
                binding["skin_distance"] = float(skin.get("distance", binding.get("skin_distance", 0.0)) or 0.0)
            except Exception:
                binding["skin_distance"] = 0.0

    clip = rig_context.get("clip")
    try:
        sample_seconds = float(getattr(clip, "start_time", 0.0) or 0.0)
    except Exception:
        sample_seconds = 0.0
    mode = (_param_value(model, "mode", "skinned_cv").strip() or "skinned_cv").lower()
    try:
        deformed_curves, line_points, root_points, deform_debug = deform_groom_curves(
            curves,
            guide_bindings,
            rig_context,
            sample_seconds=sample_seconds,
            mode=mode,
        )
    except GroomDeformError as exc:
        return GroomDeformBuildOutcome(None, source_assets, "error", str(exc), {})
    except Exception as exc:
        return GroomDeformBuildOutcome(None, source_assets, "error", f"Groom deform failed: {exc}", {})

    node_name = str(getattr(model, "name", "") or "Groom Deform").strip()
    rig_owner = str(rig_asset.get("node") or _node_name(rig_item) or "").strip()
    hide_deformer = _param_bool(model, "hide_deformer_geo", True)
    deformer_assets: list[dict[str, Any]] = []
    for entry in source_assets:
        if not isinstance(entry, dict):
            continue
        deformer = dict(entry)
        deformer_context = dict(rig_context)
        deformer_context["mesh_skinning_enabled"] = True
        deformer_context["skin_weight_debug"] = False
        deformer_context["show_skin_weights"] = False
        deformer_context["show_capture_joints"] = False
        deformer["fbx_rig_context"] = deformer_context
        deformer["groom_deform_deformer"] = True
        deformer["selection_disabled"] = bool(hide_deformer)
        deformer_assets.append(deformer)
    source_path = str(guides_asset.get("source_path") or "").strip()
    source_owner = str(guides_asset.get("source_owner") or "").strip()
    if not source_owner:
        for entry in source_assets:
            if not isinstance(entry, dict):
                continue
            source_owner = str(entry.get("node") or entry.get("owner") or "").strip()
            if source_owner:
                break
    deformer_owner = ""
    for entry in deformer_assets:
        deformer_owner = str(entry.get("node") or entry.get("owner") or "").strip()
        if deformer_owner:
            break
    sample_owner_candidates: list[str] = []
    for candidate in (deformer_owner, source_owner, rig_owner, node_name):
        candidate = str(candidate or "").strip()
        if candidate and candidate.lower() not in {value.lower() for value in sample_owner_candidates}:
            sample_owner_candidates.append(candidate)
    sample_owner = sample_owner_candidates[0] if sample_owner_candidates else node_name
    guides_path = str(guides_asset.get("guides_path") or _param_value(getattr(guides_item, "model", None), "guides_path")).strip()
    _set_param(node_item, "source", guides_path or source_path, notify_scene=False)
    _set_param(node_item, "path", guides_path or source_path, notify_scene=False)
    debug = {
        "guides_node": _node_name(guides_item),
        "rig_node": _node_name(rig_item),
        "guide_count": int(len(deformed_curves)),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "hide_deformer_geo": bool(hide_deformer),
        "source_owner": source_owner,
        "rig_owner": rig_owner,
        "deformer_owner": deformer_owner,
        "sample_owner": sample_owner,
    }
    debug.update(dict(deform_debug or {}))
    groom_deform_payload = {
        "mode": mode,
        "rig_context": rig_context,
        "guide_bindings": list(guide_bindings),
        "bind_curves": curves,
        "hide_deformer_geo": bool(hide_deformer),
        "source_owner": source_owner,
        "rig_owner": rig_owner,
        "deformer_owner": deformer_owner,
        "sample_owner": sample_owner,
        "sample_owner_candidates": list(sample_owner_candidates),
    }
    asset = {
        "kind": "groom_guides",
        "source_kind": "groom_deform",
        "node": node_name,
        "visible": True,
        "guides_path": guides_path,
        "source_path": source_path,
        "source_owner": source_owner,
        "rig_owner": rig_owner,
        "deformer_owner": deformer_owner,
        "sample_owner": sample_owner,
        "sample_owner_candidates": list(sample_owner_candidates),
        "guide_count": int(len(deformed_curves)),
        "points_per_curve": int(guides_asset.get("points_per_curve", 0) or 0),
        "length": float(guides_asset.get("length", 0.0) or 0.0),
        "root_indices": list(guides_asset.get("root_indices") or []),
        "point_groups": dict(guides_asset.get("point_groups") or {}),
        "guide_bindings": list(guide_bindings),
        "curves": deformed_curves,
        "bind_curves": curves,
        "deform_rig_context": rig_context,
        "groom_deform_mode": mode,
        "line_points": line_points,
        "root_points": root_points,
        "debug": debug,
        "groom_deform": dict(groom_deform_payload),
    }
    return GroomDeformBuildOutcome(
        asset,
        tuple(deformer_assets),
        "ok",
        (
            f"Deforming {len(deformed_curves)} guide curve(s); "
            f"scalp attached {int(debug.get('scalp_attached_guides', 0) or 0)}."
        ),
        debug,
    )


def _logs_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    out = root / "logs"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _line_stats(line_points) -> dict[str, Any]:
    rows: list[list[float]] = []
    for raw in list(line_points or []):
        if not isinstance(raw, (list, tuple)) or len(raw) < 3:
            continue
        try:
            rows.append([float(raw[0]), float(raw[1]), float(raw[2])])
        except Exception:
            continue
    segment_count = int(len(rows) // 2)
    non_degenerate = 0
    for idx in range(segment_count):
        a = rows[idx * 2]
        b = rows[idx * 2 + 1]
        if abs(a[0] - b[0]) > 1.0e-8 or abs(a[1] - b[1]) > 1.0e-8 or abs(a[2] - b[2]) > 1.0e-8:
            non_degenerate += 1
    if rows:
        mins = [min(row[axis] for row in rows) for axis in range(3)]
        maxs = [max(row[axis] for row in rows) for axis in range(3)]
    else:
        mins = []
        maxs = []
    return {
        "line_point_count": int(len(rows)),
        "line_segment_count": int(segment_count),
        "non_degenerate_segments": int(non_degenerate),
        "line_bounds_min": mins,
        "line_bounds_max": maxs,
    }


def _groom_deform_debug_dict(node_item, outcome: GroomDeformBuildOutcome) -> dict[str, Any]:
    asset = outcome.asset if isinstance(outcome.asset, dict) else {}
    debug = dict(getattr(outcome, "debug", None) or {})
    guides_item = _connected_input_item(node_item, {"guides", "guide", "source"}, GUIDE_KIND_ALIASES)
    rig_item = _connected_input_item(node_item, {"rig", "skeleton", "animation"}, RIG_KIND_ALIASES)
    cfg = asset.get("groom_deform") if isinstance(asset.get("groom_deform"), dict) else {}
    rig_context = cfg.get("rig_context") if isinstance(cfg, dict) else None
    if not isinstance(rig_context, dict):
        rig_context = {}
    clip = rig_context.get("clip")
    skeleton = rig_context.get("skeleton")
    guide_bindings = list(asset.get("guide_bindings") or [])
    curves = list(asset.get("curves") or [])
    bind_curves = list(asset.get("bind_curves") or [])
    roots = list(asset.get("root_points") or [])
    line_stats = _line_stats(asset.get("line_points") or [])
    skin_bound = sum(1 for entry in guide_bindings if isinstance(entry, dict) and list(entry.get("skin_influences") or []))
    triangle_bound = sum(
        1
        for entry in guide_bindings
        if isinstance(entry, dict)
        and list(entry.get("skin_triangle_vertices") or entry.get("triangle_vertices") or [])
        and list(entry.get("skin_barycentric") or entry.get("barycentric") or [])
    )
    data: dict[str, Any] = {
        "status": str(getattr(outcome, "status", "") or ""),
        "detail": str(getattr(outcome, "detail", "") or ""),
        "renderer_debug_log": str(_logs_dir() / "groom_guides_renderer_debug.log"),
        "renderer_expected_tag": "scene-groom-guides",
        "asset_kind": str(asset.get("kind") or ""),
        "asset_source_kind": str(asset.get("source_kind") or ""),
        "has_groom_deform_payload": bool(isinstance(asset.get("groom_deform"), dict)),
        "has_deform_rig_context": bool(isinstance(asset.get("deform_rig_context"), dict)),
        "guides_node": _node_name(guides_item),
        "guides_kind": _node_kind(guides_item),
        "rig_node": _node_name(rig_item),
        "rig_kind": _node_kind(rig_item),
        "guide_count": int(asset.get("guide_count", 0) or 0),
        "points_per_curve": int(asset.get("points_per_curve", 0) or 0),
        "curve_count": int(len(curves)),
        "bind_curve_count": int(len(bind_curves)),
        "guide_binding_count": int(len(guide_bindings)),
        "root_point_count": int(len(roots)),
        "source_owner": str(asset.get("source_owner") or ""),
        "source_path": str(asset.get("source_path") or ""),
        "guides_path": str(asset.get("guides_path") or ""),
        "source_assets_count": int(len(getattr(outcome, "source_assets", None) or [])),
        "deformer_assets_count": int(len(getattr(outcome, "source_assets", None) or [])),
        "hide_deformer_geo": bool((cfg or {}).get("hide_deformer_geo", True)),
        "skin_influence_bound_guides": int(skin_bound),
        "skin_influence_missing_guides": int(max(0, len(guide_bindings) - skin_bound)),
        "triangle_bound_guides": int(triangle_bound),
        "mesh_count": int(len(rig_context.get("meshes") or [])),
        "joint_count": int(len(getattr(skeleton, "joints", []) or [])),
        "clip_name": str(getattr(clip, "name", "") or ""),
        "clip_start": float(getattr(clip, "start_time", 0.0) or 0.0) if clip is not None else 0.0,
        "clip_end": float(getattr(clip, "end_time", 0.0) or 0.0) if clip is not None else 0.0,
        "mode": str((cfg or {}).get("mode") or ""),
    }
    data.update(line_stats)
    data.update(debug)
    if data["status"] != "ok" or not asset:
        diagnosis = "Groom Deform did not build an asset. Check the detail/error first."
    elif int(data.get("line_segment_count", 0) or 0) <= 0:
        diagnosis = "The deform asset has no renderable line segments. The guide curves or deformation output are empty."
    elif int(data.get("guide_binding_count", 0) or 0) <= 0:
        diagnosis = "The asset has guide lines but no scalp binding data. Regenerate Groom Guides."
    elif int(data.get("scalp_attached_guides", 0) or 0) <= 0:
        diagnosis = "Guide lines exist, but no guides attached to scalp triangles. The skin/triangle binding did not resolve."
    else:
        diagnosis = "The asset has renderable deformed guide lines. If they are invisible, the issue is likely renderer load, scene visibility, or stale view state."
    data["diagnosis"] = diagnosis
    return data


def format_groom_deform_debug_report(outcome: GroomDeformBuildOutcome, node_item=None) -> str:
    if node_item is not None:
        debug = _groom_deform_debug_dict(node_item, outcome)
    else:
        debug = dict(getattr(outcome, "debug", None) or {})
        debug["status"] = str(getattr(outcome, "status", "") or "")
        debug["detail"] = str(getattr(outcome, "detail", "") or "")
    lines = [
        f"timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"status: {str(debug.get('status') or '')}",
        f"detail: {str(debug.get('detail') or '')}",
    ]
    for key in GROOM_DEFORM_DEBUG_KEYS:
        if key in debug:
            lines.append(f"{key}: {debug.get(key)}")
    lines.append("")
    lines.append("debug_json:")
    try:
        lines.append(json.dumps(debug, indent=2, sort_keys=True, default=str))
    except Exception:
        lines.append(str(debug))
    return "\n".join(lines)


def write_groom_deform_debug_report(report: str) -> Path:
    log_dir = _logs_dir()
    latest_path = log_dir / "groom_deform_debug_latest.txt"
    append_path = log_dir / "groom_deform_debug.log"
    latest_path.write_text(str(report or ""), encoding="utf-8")
    with append_path.open("a", encoding="utf-8") as handle:
        handle.write(str(report or ""))
        handle.write("\n\n")
    return latest_path


def show_groom_deform_debug_report(node_item, parent=None) -> bool:
    outcome = build_groom_deform_scene_asset(node_item)
    report = format_groom_deform_debug_report(outcome, node_item=node_item)
    try:
        log_path = write_groom_deform_debug_report(report)
    except Exception:
        log_path = None

    dialog = QtWidgets.QDialog(parent or _resolve_window(node_item))
    dialog.setWindowTitle("Groom Deform Debug")
    dialog.resize(760, 540)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.setSpacing(8)

    label = QtWidgets.QLabel(f"Saved to: {log_path}" if log_path is not None else "Could not write debug report to logs folder.")
    label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard)
    layout.addWidget(label)

    text = QtWidgets.QTextEdit()
    text.setReadOnly(True)
    text.setAcceptRichText(False)
    text.setPlainText(report)
    text.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
    layout.addWidget(text, 1)

    row = QtWidgets.QHBoxLayout()
    row.addStretch(1)
    copy_btn = QtWidgets.QPushButton("Copy")
    close_btn = QtWidgets.QPushButton("Close")

    def _copy_report():
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(report)
        copy_btn.setText("Copied")

    copy_btn.clicked.connect(_copy_report)
    close_btn.clicked.connect(dialog.accept)
    row.addWidget(copy_btn)
    row.addWidget(close_btn)
    layout.addLayout(row)
    dialog.exec()
    return True


def build_ports(node_item) -> None:
    for name, default in (
        ("guides", ""),
        ("rig", ""),
        ("source", ""),
        ("path", ""),
        ("hide_deformer_geo", "1"),
        ("mode", "skinned_cv"),
        ("debug_log", "0"),
    ):
        _ensure_param(node_item, name, default)
    try:
        setattr(getattr(node_item, "model", None), "_named_inputs", ["guides", "rig"])
        setattr(node_item, "_show_default_input_with_named", False)
    except Exception:
        pass
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("guides")
        node_item.ensure_input("rig")
    _ensure_hidden_params(getattr(node_item, "model", None), HIDDEN_PARAMS)
    _ensure_visible_params(getattr(node_item, "model", None), ["guides", "rig"])


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


class GroomDeformWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._pending = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(4)
        self._status = QtWidgets.QLabel("Connect guides and rig")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(28)
        layout.addWidget(self._status, 0)
        self._hide_check = QtWidgets.QCheckBox("Hide deformer geo")
        self._hide_check.stateChanged.connect(self._on_hide_changed)
        layout.addWidget(self._hide_check, 0)
        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setMinimumHeight(24)
        self._view_btn.clicked.connect(self._on_view_clicked)
        layout.addWidget(self._view_btn, 0)
        self._sync_from_params()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    def sizeHint(self):
        return QtCore.QSize(224, 104)

    def minimumSizeHint(self):
        return QtCore.QSize(224, 104)

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
        if _param_change_relevant(self._node_item, name):
            self._schedule_refresh()

    def _schedule_refresh(self, *_args):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(80, self._refresh_status)

    def _sync_from_params(self):
        model = getattr(self._node_item, "model", None)
        self._hide_check.blockSignals(True)
        try:
            self._hide_check.setChecked(_param_bool(model, "hide_deformer_geo", True))
        finally:
            self._hide_check.blockSignals(False)

    def _refresh_status(self):
        self._pending = False
        self._sync_from_params()
        outcome = build_groom_deform_scene_asset(self._node_item)
        self._view_btn.setEnabled(bool(outcome.asset))
        if outcome.status == "ok":
            self._status.setStyleSheet("color:#22c55e;font-size:11px;")
        else:
            self._status.setStyleSheet("color:#ef4444;font-size:11px;")
        self._status.setText(outcome.detail)

    def _on_hide_changed(self, _state: int):
        _set_param(self._node_item, "hide_deformer_geo", "1" if self._hide_check.isChecked() else "0", notify_scene=True)
        self._schedule_refresh()

    def _on_view_clicked(self):
        outcome = build_groom_deform_scene_asset(self._node_item)
        if not outcome.asset:
            QtWidgets.QMessageBox.warning(_resolve_window(self._node_item) or self, "Groom Deform", outcome.detail)
            return
        assets = []
        if not _param_bool(getattr(self._node_item, "model", None), "hide_deformer_geo", True):
            assets.extend(dict(entry) for entry in outcome.source_assets if isinstance(entry, dict))
        assets.append(dict(outcome.asset))
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if callable(handler):
            try:
                handler(assets, frame=True)
            except TypeError:
                handler(assets)


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
    body = GroomDeformWidget(node_item)
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


GROOM_DEFORM_SPEC = Spec(
    stripe_color="#fb7185",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "GROOM_DEFORM_SPEC",
    "GroomDeformBuildOutcome",
    "build_groom_deform_scene_asset",
    "build_ports",
    "format_groom_deform_debug_report",
    "show_groom_deform_debug_report",
    "write_groom_deform_debug_report",
]
